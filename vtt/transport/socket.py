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
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from flask import request
from flask_socketio import ConnectionRefusedError, SocketIO, disconnect

from core.persistence.database import Database
from vtt.commands import CommandService, RateLimiter
from vtt.delivery.hub import AccessLost, Hub, Principal
from vtt.projection.serializers import TokenKey
from vtt.protocol import ContractRegistry, ProtocolValidationError, StrictJSON
from vtt.storage.models import VttTableGrant

NAMESPACE = "/vtt"
TABLE_COOKIE = "vtt_table"


class VttHost(Protocol):
    """What the VTT needs from the host app (wired in app.py, faked in tests)."""

    def current_member_id(self) -> str | None: ...
    def csrf_valid(self, token: Any) -> bool: ...
    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool: ...


def _rejection(reason: str) -> dict[str, Any]:
    return {"schemaVersion": 1, "reason": reason}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class VttService:
    socketio: SocketIO
    hub: Hub
    principals: dict[str, Principal]
    commands: CommandService


def table_verifier(grant_key: bytes, credential: str) -> str:
    return hmac.new(grant_key, credential.encode("utf-8"), hashlib.sha256).hexdigest()


def init_vtt(app, *, db: Database, host: VttHost, grant_key: bytes, token_key: TokenKey,
             registry: ContractRegistry | None = None, allowed_origins: list[str] | None = None,
             async_mode: str | None = None, now: Callable[[], datetime] | None = None,
             rate_limiter: RateLimiter | None = None) -> VttService:
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
        return (grant is not None and grant.campaign_id == campaign_id and grant.revoked_at is None
                and _utc(grant.expires_at) > clock())

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
                if grant.revoked_at is not None:
                    raise ConnectionRefusedError(_rejection("grant-revoked"))
                if _utc(grant.expires_at) <= clock():
                    raise ConnectionRefusedError(_rejection("grant-expired"))
                return Principal(kind="table", id=grant.id, campaign_id=grant.campaign_id)
        user_id = host.current_member_id()
        token = auth.get("csrf") if isinstance(auth, dict) else None
        if not user_id or not host.csrf_valid(token):
            raise ConnectionRefusedError(_rejection("session-ended"))
        return Principal(kind="member", id=user_id)

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
        hub.emit(sid, "vtt.ack", result.ack)
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

    return VttService(socketio=socketio, hub=hub, principals=principals, commands=commands)
