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
TABLE_COOKIE = "vtt_table"
# Section 10: each connected table is rechecked in PostgreSQL at least every 30 s.
HEARTBEAT_SECONDS = 15
# When storage cannot be checked, a table whose last successful verification is
# older than this is ended anyway (fail closed). With the 15 s interval the
# second consecutive failed sweep ends it, inside the 30 s bound.
UNVERIFIED_LIMIT = timedelta(seconds=25)
# Bound the sweep's own database waits so a hung query cannot stall it.
SWEEP_STATEMENT_TIMEOUT_MS = 5000


class VttHost(Protocol):
    """What the VTT needs from the host app (wired in app.py, faked in tests)."""

    def current_member_id(self) -> str | None: ...
    def csrf_valid(self, token: Any) -> bool: ...
    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool: ...


def _rejection(reason: str) -> dict[str, Any]:
    return {"schemaVersion": 1, "reason": reason}


@dataclass
class VttService:
    socketio: SocketIO
    hub: Hub
    principals: dict[str, Principal]
    commands: CommandService
    heartbeat: Callable[[], int]
    sweep: Callable[[str | None], int]
    sweep_stats: dict


def table_verifier(grant_key: bytes, credential: str) -> str:
    return hmac.new(grant_key, credential.encode("utf-8"), hashlib.sha256).hexdigest()


def init_vtt(app, *, db: Database, host: VttHost, grant_key: bytes, token_key: TokenKey,
             registry: ContractRegistry | None = None, allowed_origins: list[str] | None = None,
             async_mode: str | None = None, now: Callable[[], datetime] | None = None,
             rate_limiter: RateLimiter | None = None, start_heartbeat: bool = True,
             pairing_limits=None) -> VttService:
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
        credential = request.cookies.get(TABLE_COOKIE)
        if credential:
            if not isinstance(credential, str) or not (16 <= len(credential) <= 256):
                raise ConnectionRefusedError(_rejection("session-ended"))
            with db.session() as session:
                grant = session.query(VttTableGrant).filter_by(
                    verifier=table_verifier(grant_key, credential)).one_or_none()
                if grant is None:
                    raise ConnectionRefusedError(_rejection("session-ended"))
                status = G.grant_status(session, grant, clock())
                if status == "revoked":
                    raise ConnectionRefusedError(_rejection("grant-revoked"))
                if status == "expired":
                    raise ConnectionRefusedError(_rejection("grant-expired"))
                return Principal(kind="table", id=grant.id, campaign_id=grant.campaign_id,
                                 verified_at=clock(), expires_at=G.utc(grant.expires_at))
        user_id = host.current_member_id()
        token = auth.get("csrf") if isinstance(auth, dict) else None
        if not user_id or not host.csrf_valid(token):
            raise ConnectionRefusedError(_rejection("session-ended"))
        return Principal(kind="member", id=user_id)

    sweep_stats = {"unverifiable": 0, "end_errors": 0}

    def sweep(campaign_id: str | None = None) -> int:
        """Recheck connected table principals (one campaign, or all) and end each
        whose grant no longer stands: ``revoked`` to a subscribed one, then close.
        Returns how many were ended."""
        tables = [(sid, p) for sid, p in list(principals.items())
                  if p.kind == "table" and (campaign_id is None or p.campaign_id == campaign_id)]
        if not tables:
            return 0
        current = clock()
        try:
            failing = verify(tables, current)
        except Exception:
            # Storage cannot be checked (handoff 035). Fail closed on what is known
            # locally: a grant past its expiry, or one not verified within the
            # limit. Nothing here waits for storage to come back.
            sweep_stats["unverifiable"] += 1
            failing = [sid for sid, p in tables
                       if (p.expires_at is not None and p.expires_at <= current)
                       or p.verified_at is None or current - p.verified_at >= UNVERIFIED_LIMIT]
        for sid in failing:
            end_one(sid)
        return len(failing)

    def verify(tables, current) -> list[str]:
        """One bounded read of every listed table's grant. Returns the failing
        sids and stamps the rest as verified now. Raises if storage fails."""
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
        for p, expires_at in passing:
            p.verified_at, p.expires_at = current, expires_at
        return failing

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
        while True:
            socketio.sleep(HEARTBEAT_SECONDS)
            try:
                sweep(None)
            except Exception:       # a database blip must not stop the heartbeat
                pass

    if start_heartbeat:
        socketio.start_background_task(heartbeat_loop)

    def access_changed(campaign_id: str) -> None:
        hub.notify(campaign_id)     # subscribed tables: delivery's recheck sends revoked
        sweep(campaign_id)          # connected but unsubscribed ones too

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
                      heartbeat=lambda: sweep(None), sweep=sweep, sweep_stats=sweep_stats)
