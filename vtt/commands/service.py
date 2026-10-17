"""The durable command pipeline (VTT-ADR-0002 sections 7 and 8).

Order, for one ``vtt.command`` from an authenticated GM connection:

1. Correlation. Without a well-formed ``commandId`` no acknowledgement can be
   addressed, so the transport closes the connection (fail closed).
2. Version (``unsupported-version``), registered type (``unknown-command``),
   schema plus command rules (``invalid-schema``), policy lookup.
3. Current campaign capability, rechecked from storage (``unauthorized``).
4. Origin binding: the connection's live GM subscription in that campaign
   (``resync-required`` when there is none or it is recovering).
5. The per-connection rate limit (``rate-limited``).
6. One transaction:
   a. take the campaign stream lock (PostgreSQL ``lock_timeout``; a timeout is
      ``temporarily-unavailable``). Every command in the campaign serializes
      here, so this also serializes concurrent use of one receipt key;
   b. look up the receipt. Same digest: return the recorded acknowledgement,
      apply nothing, emit no frame. Another digest: ``command-id-reused``;
   c. run the handler. It locks its rows (scene, then entities) and checks
      expected versions;
   d. the write-time size rule: the scene's GM snapshot must fit the 2 MiB
      frame limit and the schema's entity caps;
   e. applied: state, receipt, stream position and outbox row commit together.
      A deterministic refusal (stale, conflict, validation-failed,
      rule-violation) rolls the whole attempt back; a second short transaction
      (lock, receipt re-check) commits only its receipt.
7. Acknowledge only after that commit.

None of the failures in steps 1-5, nor a lock timeout or an internal error,
writes a receipt. An exception before commit is ``server-error``; one raised
while committing is ``temporarily-unavailable``, because the outcome is then
unknown and the client must retry the same ``commandId`` so the receipt
resolves it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, OperationalError

from core.persistence.database import Database
from vtt.projection import serializers as S
from vtt.protocol import ContractRegistry, ProtocolValidationError
from vtt.protocol.boundary import MAX_JSON_BYTES
from vtt.storage.models import VttCommandReceipt, VttEntity, VttScene
from vtt.storage.stream import advance, lock_stream

from .handlers import POLICIES, Context, Rejection

OPAQUE_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}")
RECEIPTED = ("stale", "conflict", "validation-failed", "rule-violation")

# Schema caps (gm.schema.json / table.schema.json) and the frame limit.
GM_ENTITY_CAP = 4096
TABLE_ENTITY_CAP = 2048
# Room for what surrounds the frame on the wire: the Socket.IO packet type and
# namespace prefix, and the longest subscription and principal ids.
FRAME_HEADROOM = 1024
LOCK_TIMEOUT_MS = 2000


class _SizingKey:
    """Version tokens have a fixed length, so a placeholder of that length sizes
    a frame exactly without computing an HMAC per entity."""

    def token(self, *_args, **_kwargs) -> str:
        return "v" + "0" * 24


@dataclass
class Result:
    ack: dict[str, Any] | None          # None: close the connection (no correlation)
    notify: str | None = None           # campaign to notify after a fresh commit


class RateLimiter:
    """Token bucket per connection: ``burst`` commands, refilled at ``rate`` per second."""

    def __init__(self, rate: float = 20.0, burst: float = 40.0, clock: Callable[[], float] = time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, sid: str) -> bool:
        now = self.clock()
        with self._lock:
            tokens, at = self._buckets.get(sid, (self.burst, now))
            tokens = min(self.burst, tokens + (now - at) * self.rate)
            if tokens < 1:
                self._buckets[sid] = (tokens, now)
                return False
            self._buckets[sid] = (tokens - 1, now)
            return True

    def forget(self, sid: str) -> None:
        with self._lock:
            self._buckets.pop(sid, None)


def _canonical(value):
    """Integral floats as ints, so 64 and 64.0 hash alike."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def command_digest(message: dict[str, Any]) -> str:
    body = {k: message[k] for k in ("schemaVersion", "campaignId", "sceneId", "type", "expected", "payload")}
    raw = json.dumps(_canonical(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def gm_snapshot_bytes(session, campaign_id: str, scene_id: str) -> tuple[int, int, int]:
    """(bytes, GM entity count, table entity count) of the scene's largest
    snapshot, as the transport would encode it."""
    scene = session.get(VttScene, (campaign_id, scene_id))
    rows = session.execute(
        select(VttEntity).where(VttEntity.campaign_id == campaign_id, VttEntity.scene_id == scene_id)
        .order_by(VttEntity.kind, VttEntity.id)
    ).scalars().all()
    from vtt import fog
    fog_view = fog.load(session, scene)[1]
    entities = S.gm_entities(_SizingKey(), scene, rows, fog_view)
    frame = S.snapshot_frame("gm", "s" * 128, campaign_id, scene_id,
                             {"kind": "member", "id": "u" * 128, "campaignRole": "gm"}, entities)
    # python-socketio encodes the packet's [event, payload] list through
    # StrictJSON.dumps with these separators; that string is what the limit counts.
    encoded = json.dumps(["vtt.snapshot", frame], separators=(",", ":"), allow_nan=False)
    table_count = (1 + (fog_view is not None)
                   + sum(1 for r in rows if not r.hidden and r.kind in ("token", "light")))
    return len(encoded.encode("utf-8")), len(entities), table_count


class CommandService:
    def __init__(self, db: Database, registry: ContractRegistry, key: S.TokenKey, hub,
                 is_campaign_gm: Callable[[Any, str, str], bool], rate_limiter: RateLimiter | None = None,
                 lock_timeout_ms: int = LOCK_TIMEOUT_MS):
        self.db = db
        self.registry = registry
        self.key = key
        self.hub = hub
        self.is_campaign_gm = is_campaign_gm
        self.rate = rate_limiter or RateLimiter()
        self.lock_timeout_ms = lock_timeout_ms
        variants = set(registry.manifest.get("commandVariants", []))
        if variants != set(POLICIES):
            # Fail closed at start-up: a registered type without a policy, or a
            # policy for a type the contract does not publish.
            raise RuntimeError("command policies do not match the contract's commandVariants")
        self.stats = {"applied": 0, "rejected": 0, "replayed": 0, "receipted_rejections": 0, "ack_errors": 0}

    # --- acks --------------------------------------------------------------------------------

    def _ack(self, command_id: str, code: str | None = None) -> dict[str, Any]:
        ack = ({"schemaVersion": 1, "commandId": command_id, "outcome": "applied"} if code is None
               else {"schemaVersion": 1, "commandId": command_id, "outcome": "rejected", "code": code})
        self.registry.validate("acknowledgement", ack)
        if code is not None:
            self.stats["rejected"] += 1
        return ack

    # --- the pipeline ------------------------------------------------------------------------

    def handle(self, sid: str, principal, message: Any) -> Result:
        command_id = message.get("commandId") if isinstance(message, dict) else None
        if not isinstance(command_id, str) or not OPAQUE_TOKEN.fullmatch(command_id):
            return Result(None)
        version = message.get("schemaVersion")
        if type(version) is int and version != 1:
            return Result(self._ack(command_id, "unsupported-version"))
        if isinstance(message.get("type"), str) and message["type"] not in POLICIES:
            return Result(self._ack(command_id, "unknown-command"))
        try:
            self.registry.validate("command", message)
        except ProtocolValidationError:
            return Result(self._ack(command_id, "invalid-schema"))
        policy = POLICIES[message["type"]]
        campaign_id = message["campaignId"]

        try:
            with self.db.session() as session:
                allowed = principal.kind == "member" and self.is_campaign_gm(session, principal.id, campaign_id)
        except (OperationalError, DBAPIError):
            return Result(self._ack(command_id, "temporarily-unavailable"))
        if not allowed or not policy.capability(message["payload"]) <= set(S.GM_CAPABILITIES):
            return Result(self._ack(command_id, "unauthorized"))

        sub = self.hub.subscription(sid)
        if sub is None or not sub.live or sub.audience != "gm" or sub.campaign_id != campaign_id:
            return Result(self._ack(command_id, "resync-required"))
        origin = {"subscriptionId": sub.subscription_id, "commandId": command_id}

        if not self.rate.allow(sid):
            return Result(self._ack(command_id, "rate-limited"))

        return self._transact(principal, message, policy, origin)

    def _transact(self, principal, message, policy, origin) -> Result:
        command_id = message["commandId"]
        campaign_id = message["campaignId"]
        digest = command_digest(message)

        def apply(session) -> str | None:
            ctx = Context(session=session, key=self.key, campaign_id=campaign_id, scene_id=message["sceneId"],
                          payload=message["payload"],
                          expected={(e["kind"], e["id"]): e["version"] for e in message["expected"]})
            event = policy.handler(ctx)
            if policy.sizes_scene:
                self._check_size(session, campaign_id, ctx.scene_id)
            advance(session, campaign_id, {**event, "origin": origin})
            return None

        status, value = self._in_transaction(principal, message, digest, apply)
        if status == "rejection":
            # The attempt was rolled back whole. Record the refusal in its own
            # short transaction (no savepoint: pysqlite releases a SAVEPOINT
            # opened before any write as a COMMIT). It re-takes the lock and
            # re-checks the receipt, so a racing identical command still wins
            # cleanly and this one answers with that recorded outcome.
            code = value
            status, value = self._in_transaction(principal, message, digest, lambda _session: code)
        if status == "ack":
            return Result(value)
        if status == "replayed":
            # A retry of an applied command. It applies nothing, but it wakes
            # delivery: if the first attempt's wake-up was lost, this repairs it.
            # Delivery only sends what is committed and not yet sent, so a
            # healthy subscription gets nothing (no second completion).
            return Result(value, notify=campaign_id)
        if status == "uncertain":
            # If it did commit, its frames must not wait for some later commit:
            # wake delivery anyway (it only ever sends what is committed).
            return Result(value, notify=campaign_id)
        code = value
        if code is None:
            self.stats["applied"] += 1
        else:
            self.stats["receipted_rejections"] += 1
        return Result(self._ack(command_id, code), notify=campaign_id if code is None else None)

    def _in_transaction(self, principal, message, digest, body):
        """One command transaction: stream lock, receipt check, ``body``, receipt,
        commit. Returns ("committed", code), ("rejection", code) after a full
        rollback, ("replayed", ack) for a retry of an applied receipt, or
        ("ack", ack) for any other answer that commits nothing."""
        command_id = message["commandId"]
        campaign_id = message["campaignId"]
        session = self.db.session_factory()
        try:
            try:
                session.begin()
                if session.get_bind().dialect.name == "postgresql":
                    session.execute(text(f"SET LOCAL lock_timeout = '{int(self.lock_timeout_ms)}ms'"))
                lock_stream(session, campaign_id)
                receipt = session.execute(
                    select(VttCommandReceipt).where(
                        VttCommandReceipt.campaign_id == campaign_id,
                        VttCommandReceipt.principal_kind == principal.kind,
                        VttCommandReceipt.principal_id == principal.id,
                        VttCommandReceipt.command_id == command_id,
                    )
                ).scalar_one_or_none()
                if receipt is not None:
                    session.rollback()
                    if receipt.digest != digest:
                        return "ack", self._ack(command_id, "command-id-reused")
                    self.stats["replayed"] += 1
                    return ("replayed" if receipt.code is None else "ack"), self._ack(command_id, receipt.code)
                try:
                    code = body(session)
                except Rejection as rejection:
                    session.rollback()
                    if rejection.code not in RECEIPTED:
                        raise RuntimeError("a handler raised an unreceipted rejection code") from rejection
                    return "rejection", rejection.code
                session.add(VttCommandReceipt(
                    campaign_id=campaign_id, principal_kind=principal.kind, principal_id=principal.id,
                    command_id=command_id, digest=digest,
                    outcome="applied" if code is None else "rejected", code=code))
                session.flush()
            except (OperationalError, DBAPIError):
                # Lock timeout, serialization failure, a receipt-key race or a lost
                # connection before commit: nothing committed, and a retry of the
                # same commandId resolves it.
                session.rollback()
                return "ack", self._ack(command_id, "temporarily-unavailable")
            except Exception:
                session.rollback()
                return "ack", self._ack(command_id, "server-error")
            try:
                session.commit()
            except Exception:
                # The commit may or may not have happened. Never claim either: the
                # client retries the same commandId and the receipt answers.
                session.rollback()
                return "uncertain", self._ack(command_id, "temporarily-unavailable")
            return "committed", code
        finally:
            session.close()

    @staticmethod
    def _check_size(session, campaign_id: str, scene_id: str) -> None:
        size, gm_count, table_count = gm_snapshot_bytes(session, campaign_id, scene_id)
        if size + FRAME_HEADROOM > MAX_JSON_BYTES or gm_count > GM_ENTITY_CAP or table_count > TABLE_ENTITY_CAP:
            raise Rejection("validation-failed")
