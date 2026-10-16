"""Socket.IO namespace ``/vtt`` (VTT-ADR-0002 sections 1, 2, 3, 7 and 10): M1 read path, M2 commands.

Handshake
---------
- The engine only accepts the app's own Origin (python-engineio's default
  same-origin check; ``allowed_origins`` widens it explicitly, never ``*``).
- A table presents its grant cookie. Only a verifier (HMAC under a server key) is
  stored, so the cookie value is looked up by its verifier. Unknown or
  malformed: ``session-ended``. A recognized credential that is revoked or
  expired gets ``grant-revoked`` / ``grant-expired``.
- A member needs the app's authenticated session plus CSRF proof in the
  Socket.IO ``auth`` payload (``{"csrf": <token>}``).
- Every refusal is a stable v1 ``BootstrapRejection``, with no campaign, scene
  or grant detail.

Events
------
- ``vtt.subscribe`` is validated with the principal's own audience schema; the
  audience comes from the authenticated principal, never from the request. A
  request for a version other than 1 gets the v1 ``unsupported-version``
  envelope. Any other invalid request disconnects (fail closed).
- ``vtt.command`` (M2) runs the durable pipeline in ``vtt.commands`` and answers
  with one ``vtt.ack`` event, emitted only after the outcome is committed; the
  projection frames follow through ``Hub.notify``. A command without a
  well-formed ``commandId`` cannot be acknowledged, so it disconnects.
- Every other event is refused. A table sending anything but subscribe is
  disconnected: it has no command, transient or whisper capability (criterion 14).

Table grants (M3)
-----------------
One rule (``vtt.storage.grants.grant_status``) decides a grant at connect, at
every snapshot, at every live delivery and on the heartbeat: revoked, issued
under an older campaign table epoch, or expired. Pairing and revocation are
HTTP routes (``vtt.transport.pairing``). A revocation commits a
``table-access`` outbox row, so the next delivery ends each matching live
table with ``revoked``; the service also sweeps that campaign's tables at once.
The heartbeat rechecks every connected table principal (subscribed or not)
every ``HEARTBEAT_SECONDS``, which bounds delayed fan-out under 30 s even when
nothing else commits (section 10).
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
import re
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from flask import request
from sqlalchemy import text
from flask_socketio import ConnectionRefusedError, SocketIO, disconnect

from core.persistence.database import Database
from vtt.commands import CommandService, RateLimiter
from vtt.storage import grants as G
from vtt.delivery.hub import AccessLost, Hub, Principal
from vtt.projection.serializers import TokenKey
from vtt.protocol import ContractRegistry, ProtocolValidationError, StrictJSON
from vtt.storage.models import VttTableGrant

NAMESPACE = "/vtt"
OPAQUE = re.compile(r"[A-Za-z0-9_-]{16,128}")
TABLE_COOKIE = "vtt_table"
# Section 10: no connected table may go more than AUTH_BOUND without its grant
# being confirmed in storage, whatever storage is doing. The numbers are chosen so
# the bound holds by construction (handoffs 035 and 037):
#
# - sweeps START on a fixed cadence of HEARTBEAT_SECONDS (a slow sweep does not
#   push the next one back);
# - the storage read is abandoned after VERIFY_TIMEOUT_SECONDS, however it is
#   stuck (slow query, lock, or waiting for a connection);
# - a successful read stamps verified_at with the time the read STARTED, and a
#   failed read measures age AFTER it failed;
# - after a failed read, a table unverified for UNVERIFIED_LIMIT is ended.
#
# Last good read starts at S. The sweep at S + H may fail as late as S + H + V
# (15 s, under the limit: one failed sweep is tolerated). The sweep at S + 2H
# fails no later than S + 2H + V = 25 s with age >= 20 s, so the table is ended
# inside AUTH_BOUND. A test pins these inequalities.
AUTH_BOUND = timedelta(seconds=30)
HEARTBEAT_SECONDS = 10
VERIFY_TIMEOUT_SECONDS = 5.0
UNVERIFIED_LIMIT = timedelta(seconds=20)
# The database's own waits stay inside the read timeout.
SWEEP_STATEMENT_TIMEOUT_MS = 4000


class VerifyTimeout(Exception):
    """The storage read did not finish within VERIFY_TIMEOUT_SECONDS."""


def run_bounded(fn, timeout: float):
    """Run ``fn`` on a worker and wait at most ``timeout`` seconds. Raises
    VerifyTimeout (the worker is left to finish on its own; its result is
    discarded) or whatever ``fn`` raised."""
    done = threading.Event()
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as error:   # handed back to the caller
            box["error"] = error
        finally:
            done.set()

    worker = threading.Thread(target=target, name="vtt-table-verify", daemon=True)
    worker.start()
    if not done.wait(timeout):
        raise VerifyTimeout(worker)
    if "error" in box:
        raise box["error"]
    return box["value"]


def next_start(scheduled: float, now: float, interval: float) -> float:
    """The next sweep start on a fixed cadence. A sweep that ran long does not
    push the cadence back; one that fell a whole interval behind runs at once."""
    upcoming = scheduled + interval
    return upcoming if upcoming > now else now


class VttHost(Protocol):
    """What the VTT needs from the host app (wired in app.py, faked in tests)."""

    def current_member_id(self) -> str | None: ...
    def csrf_valid(self, token: Any) -> bool: ...
    def csrf_token(self) -> str: ...
    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool: ...


def _rejection(reason: str) -> dict[str, Any]:
    return {"schemaVersion": 1, "reason": reason}


def _refused(reason: str) -> Exception:
    """A handshake refusal carrying the v1 BootstrapRejection as its data.

    python-socketio sends ``{"message": str(args[0])}`` for a one-argument
    ConnectionRefusedError, which would put a Python repr of the rejection on
    the wire. With two arguments it sends ``{"message": ..., "data": args[1]}``,
    and socket.io-client exposes that object as ``err.data``."""
    return ConnectionRefusedError("bootstrap-rejected", _rejection(reason))


@dataclass
class VttService:
    socketio: SocketIO
    hub: Hub
    principals: dict[str, Principal]
    commands: CommandService
    heartbeat: Callable[[], int]
    sweep: Callable[[str | None], int]
    sweep_stats: dict
    end_member: Callable[..., int]


def table_verifier(grant_key: bytes, credential: str) -> str:
    return hmac.new(grant_key, credential.encode("utf-8"), hashlib.sha256).hexdigest()


def init_vtt(app, *, db: Database, host: VttHost, grant_key: bytes, token_key: TokenKey,
             registry: ContractRegistry | None = None, allowed_origins: list[str] | None = None,
             async_mode: str | None = None, now: Callable[[], datetime] | None = None,
             rate_limiter: RateLimiter | None = None, start_heartbeat: bool = True,
             pairing_limits=None, dist_dir: Path | None = None, legacy_source=None) -> VttService:
    registry = registry or ContractRegistry()
    clock = now or (lambda: datetime.now(timezone.utc))
    socketio = SocketIO(app, json=StrictJSON, async_mode=async_mode, manage_session=False,
                        cors_allowed_origins=allowed_origins)
    principals: dict[str, Principal] = {}

    def emit(sid: str, event: str, payload: dict[str, Any]) -> None:
        socketio.emit(event, payload, to=sid, namespace=NAMESPACE)

    def access_check(session, principal: Principal, audience: str, campaign_id: str) -> bool:
        if audience == "gm":
            return principal.kind == "member" and host.is_campaign_gm(session, principal.id, campaign_id)
        grant = session.get(VttTableGrant, principal.id)
        return (grant is not None and grant.campaign_id == campaign_id
                and G.grant_status(session, grant, clock()) == "ok")

    def close(sid: str) -> None:
        principals.pop(sid, None)
        socketio.server.disconnect(sid, namespace=NAMESPACE)

    hub = Hub(db, registry, token_key, emit, access_check, disconnect=close)
    commands = CommandService(db, registry, token_key, hub, host.is_campaign_gm, rate_limiter=rate_limiter)

    def authenticate(auth: Any) -> Principal:
        # The connection says what it is. A member page sends its CSRF token in
        # the handshake auth; a table display sends none. A browser can hold both
        # credentials (a GM laptop that was once paired as a table), so the table
        # cookie is never consulted for a connection that presents a CSRF token,
        # and a member session is never used for one that does not.
        token = auth.get("csrf") if isinstance(auth, dict) else None
        if token is not None:
            user_id = host.current_member_id()
            if not user_id or not host.csrf_valid(token):
                raise _refused("session-ended")
            return Principal(kind="member", id=user_id)
        credential = request.cookies.get(TABLE_COOKIE)
        if credential:
            if not isinstance(credential, str) or not (16 <= len(credential) <= 256):
                raise _refused("session-ended")
            with db.session() as session:
                grant = session.query(VttTableGrant).filter_by(
                    verifier=table_verifier(grant_key, credential)).one_or_none()
                if grant is None:
                    raise _refused("session-ended")
                status = G.grant_status(session, grant, clock())
                if status == "revoked":
                    raise _refused("grant-revoked")
                if status == "expired":
                    raise _refused("grant-expired")
                return Principal(kind="table", id=grant.id, campaign_id=grant.campaign_id,
                                 verified_at=clock(), expires_at=G.utc(grant.expires_at))
        raise _refused("session-ended")

    sweep_stats = {"unverifiable": 0, "end_errors": 0, "timeouts": 0, "loop_errors": 0}
    verifying = {"worker": None}

    def sweep(campaign_id: str | None = None) -> int:
        """Recheck connected table principals (one campaign, or all) and end each
        whose grant no longer stands: ``revoked`` to a subscribed one, then close.
        Returns how many were ended."""
        tables = [(sid, p) for sid, p in list(principals.items())
                  if p.kind == "table" and (campaign_id is None or p.campaign_id == campaign_id)]
        if not tables:
            return 0
        started = clock()
        try:
            if verifying["worker"] is not None and verifying["worker"].is_alive():
                # The last read is still stuck: do not stack another on it.
                raise VerifyTimeout(verifying["worker"])
            failing, passing = run_bounded(lambda: verify(tables, started), VERIFY_TIMEOUT_SECONDS)
        except Exception as error:
            if isinstance(error, VerifyTimeout):
                verifying["worker"] = error.args[0]
                sweep_stats["timeouts"] += 1
            # Storage cannot be checked (handoffs 035, 037). Fail closed on what
            # is known locally, measured NOW, after the failed read: a grant past
            # its expiry, or one not verified within the limit.
            sweep_stats["unverifiable"] += 1
            now = clock()
            failing = [sid for sid, p in tables
                       if (p.expires_at is not None and p.expires_at <= now)
                       or p.verified_at is None or now - p.verified_at >= UNVERIFIED_LIMIT]
        else:
            # Only a read that finished in time renews anything, and it renews
            # to when it started.
            for p, expires_at in passing:
                p.verified_at, p.expires_at = started, expires_at
        for sid in failing:
            end_one(sid)
        return len(failing)

    def verify(tables, current):
        """Read every listed table's grant. Returns (failing sids, [(principal,
        expires_at)] that passed); stamps nothing itself, so a read abandoned
        by the timeout can never renew a table. Raises if storage fails."""
        with db.session() as session:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text(f"SET LOCAL statement_timeout = '{SWEEP_STATEMENT_TIMEOUT_MS}ms'"))
                session.execute(text(f"SET LOCAL lock_timeout = '{SWEEP_STATEMENT_TIMEOUT_MS}ms'"))
            failing, passing = [], []
            for sid, p in tables:
                grant = session.get(VttTableGrant, p.id)
                if G.grant_status(session, grant, current) == "ok":
                    passing.append((p, G.utc(grant.expires_at)))
                else:
                    failing.append(sid)
        return failing, passing

    def end_one(sid: str) -> None:
        """End one connection; a failure here never stops the rest of the sweep
        (handoff 035). The close is attempted whatever the control send did."""
        try:
            if hub.end(sid):
                return
        except Exception:
            sweep_stats["end_errors"] += 1
        try:
            close(sid)
        except Exception:
            sweep_stats["end_errors"] += 1

    def heartbeat_loop():
        scheduled = time.monotonic() + HEARTBEAT_SECONDS
        while True:
            socketio.sleep(max(0.0, scheduled - time.monotonic()))
            scheduled = next_start(scheduled, time.monotonic(), HEARTBEAT_SECONDS)
            try:
                sweep(None)
            except Exception:       # nothing may stop the heartbeat
                sweep_stats["loop_errors"] += 1

    if start_heartbeat:
        socketio.start_background_task(heartbeat_loop)

    def end_member(user_id: str | None, campaign_id: str | None = None) -> int:
        """The app revoked a member's authority (M4a). The app calls this from
        the same places it closes that user's SSE streams.

        - ``campaign_id`` given (membership or role changed in one campaign): each
          of the member's subscriptions in that campaign gets the terminal
          ``revoked`` and is closed. Other campaigns are untouched.
        - ``campaign_id`` None (the session itself ended: password change,
          logout everywhere, account store unavailable): every connection of the
          member is closed. Reconnecting answers ``session-ended``, and a command
          still in flight answers ``authentication-required``.
        - ``user_id`` None with ``campaign_id`` None: every member connection.
        """
        ended = 0
        for sid, p in list(principals.items()):
            if p.kind != "member" or (user_id is not None and p.id != user_id):
                continue
            if campaign_id is None:
                p.session_ended = True
                ended += 1
                try:
                    hub.unsubscribe(sid)
                    close(sid)
                except Exception:
                    sweep_stats["end_errors"] += 1
                continue
            sub = hub.subscription(sid)
            if sub is not None and sub.campaign_id == campaign_id:
                ended += 1
                end_one(sid)
        return ended

    def access_changed(campaign_id: str) -> None:
        hub.notify(campaign_id)     # subscribed tables: delivery's recheck sends revoked
        sweep(campaign_id)          # connected but unsubscribed ones too

    from vtt.transport.pages import make_pages_blueprint
    app.register_blueprint(make_pages_blueprint(
        db=db, host=host, on_change=hub.notify, legacy_source=legacy_source,
        dist_dir=dist_dir or Path(__file__).resolve().parents[2] / "frontend" / "vtt" / "dist"))

    from vtt.transport.pairing import make_blueprint
    app.register_blueprint(make_blueprint(
        db=db, host=host, grant_key=hmac.new(grant_key, b"vtt-pairing-codes", hashlib.sha256).digest(),
        cookie_name=TABLE_COOKIE, verifier=lambda credential: table_verifier(grant_key, credential),
        on_access_change=access_changed, now=clock, allowed_origins=allowed_origins, limits=pairing_limits))

    @socketio.on("connect", namespace=NAMESPACE)
    def on_connect(auth=None):
        principals[request.sid] = authenticate(auth)

    @socketio.on("disconnect", namespace=NAMESPACE)
    def on_disconnect(*_args):
        principals.pop(request.sid, None)
        hub.unsubscribe(request.sid)
        commands.rate.forget(request.sid)

    @socketio.on("vtt.subscribe", namespace=NAMESPACE)
    def on_subscribe(message=None):
        sid = request.sid
        principal = principals.get(sid)
        if principal is None:
            disconnect()
            return None
        if isinstance(message, dict) and type(message.get("schemaVersion")) is int \
                and message["schemaVersion"] != 1:
            return _rejection("unsupported-version")
        audience = "table" if principal.kind == "table" else "gm"
        try:
            registry.validate(registry.schema_for_event("vtt.subscribe", audience), message)
        except ProtocolValidationError:
            disconnect()
            return None
        campaign_id = principal.campaign_id if audience == "table" else message["campaignId"]
        scene_id = None if audience == "table" else message["sceneId"]
        try:
            hub.subscribe(sid, principal, audience, campaign_id, scene_id)
        except AccessLost:
            # The rejection must reach the client, so the socket is not closed
            # inside this handler (that would drop the acknowledgement). The
            # principal is forgotten instead: any further event disconnects.
            principals.pop(sid, None)
            return _rejection("session-ended")
        return None

    @socketio.on("vtt.command", namespace=NAMESPACE)
    def on_command(message=None):
        sid = request.sid
        principal = principals.get(sid)
        if principal is None or principal.kind == "table":
            disconnect()                # criterion 14: a table has no commands
            return None
        if principal.session_ended:
            # The app ended this member's session; the client signs in again and
            # retries the same commandId (ADR-0002 section 8).
            command_id = message.get("commandId") if isinstance(message, dict) else None
            if isinstance(command_id, str) and OPAQUE.fullmatch(command_id):
                hub.emit(sid, "vtt.ack", {"schemaVersion": 1, "commandId": command_id, "outcome": "rejected",
                                          "code": "authentication-required"})
            disconnect()
            return None
        result = commands.handle(sid, principal, message)
        if result.ack is None:
            disconnect()
            return None
        # The ack goes first, but a failed ack emit must never suppress delivery of
        # what already committed (handoff 031): the wake-up is in a finally. A lost
        # ack needs nothing more here. The issuer's completion frame still comes
        # through delivery, a retry of the same commandId gets the recorded
        # outcome, and if the transport is really gone, delivery's own emit failure
        # recovers or closes the subscription.
        try:
            hub.emit(sid, "vtt.ack", result.ack)
        except Exception:
            commands.stats["ack_errors"] += 1
        finally:
            if result.notify is not None:
                hub.notify(result.notify)
        return None

    @socketio.on("*", namespace=NAMESPACE)
    def on_other(_event=None, *_args):
        # M1 registers no commands, transients or whispers. A table may never send them.
        principal = principals.get(request.sid)
        if principal is None or principal.kind == "table":
            disconnect()
        return None

    return VttService(socketio=socketio, hub=hub, principals=principals, commands=commands,
                      heartbeat=lambda: sweep(None), sweep=sweep, sweep_stats=sweep_stats,
                      end_member=end_member)
