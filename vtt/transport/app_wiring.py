"""Wire the /vtt namespace into the real app, only when ``VTT_ENABLED=1``.

Off by default: with the flag unset nothing is imported into the request path,
no Socket.IO server exists, and production's shape is unchanged. Turned on, it
needs ``DATABASE_URL`` (the same transactional database as the ownership store)
and fails at startup rather than serving a half-configured VTT.

Membership recheck: when SQL is the ownership authority the GM check reads
``campaign_memberships`` inside the snapshot transaction itself (ADR-0002
section 5 step 3). With JSON authority it reads the campaign document through
``core.campaigns.get_campaign`` at the same point; that read cannot join the
SQL transaction, which is a recorded limitation of JSON mode, not of the VTT.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Any

from flask import session as flask_session

from core import auth as _auth
from core import campaigns as _campaigns
from core.persistence import CampaignMembership, Database
from core.persistence import runtime as _runtime
from vtt.projection.serializers import TokenKey
from vtt.transport.socket import VttService, init_vtt


class AppHost:
    def current_member_id(self) -> str | None:
        user = _auth.current_user()
        return user.get("id") if user else None

    def csrf_valid(self, token: Any) -> bool:
        # The app's per-session CSRF token (core.auth.csrf_token), sent in the Socket.IO auth payload.
        expected = flask_session.get("_csrf", "")
        return isinstance(token, str) and bool(token) and bool(expected) and secrets.compare_digest(token, expected)

    def csrf_token(self) -> str:
        # The app's per-session token (creates one if the session has none).
        return _auth.csrf_token()

    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool:
        if _runtime.sql_enabled():
            membership = session.get(CampaignMembership, (campaign_id, user_id))
            return membership is not None and membership.role == "gm"
        return _campaigns.is_gm(_campaigns.get_campaign(campaign_id, refresh=True), user_id)


def _legacy_asset(campaign_id: str, filename: str) -> bytes | None:
    """One /map image file, read only, for the M6 import. Only a bare file name
    inside the campaign's scene asset folder; nothing larger than the asset
    store would accept."""
    import os

    from core import storage
    from vtt.assets.images import MAX_BYTES
    name = os.path.basename(filename or "")
    if not name or name != filename:
        return None
    path = os.path.join(storage.scene_assets_dir(campaign_id), name)
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _legacy_scenes(campaign_id: str):
    """The legacy /map store, read only, for the M5 import."""
    from core import scenes as legacy
    found = [legacy.normalize_scene(s) for s in legacy.list_scenes(campaign_id)]
    return found, legacy.table_scene_id(campaign_id)


from vtt.assets import backends as asset_backends  # noqa: E402 - beside the wiring it serves


def enabled(environ=None) -> bool:
    return (environ or os.environ).get("VTT_ENABLED") == "1"


def init_from_app(app, *, environ=None) -> VttService | None:
    env = environ or os.environ
    if not enabled(env):
        return None
    secret = app.config.get("SECRET_KEY") or app.secret_key
    if not secret:
        raise RuntimeError("VTT_ENABLED needs the app SECRET_KEY")
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    grant_key = hmac.new(raw, b"vtt-table-grant-v1", hashlib.sha256).digest()
    origins = [o.strip() for o in env.get("VTT_ALLOWED_ORIGINS", "").split(",") if o.strip()] or None
    # The VTT's own engine: waiting for a pooled connection, and opening a new
    # one, are both bounded, so the table heartbeat's storage read cannot stall
    # on them (handoff 037). The read is also abandoned after its own timeout.
    url = env.get("DATABASE_URL", "")
    options: dict[str, Any] = {"pool_timeout": 4}
    if url.startswith(("postgres://", "postgresql")):
        options["connect_args"] = {"connect_timeout": 4}
    return init_vtt(app, db=Database.from_env("DATABASE_URL", environ=env, engine_options=options), host=AppHost(),
                    grant_key=grant_key, token_key=TokenKey.derive(secret), allowed_origins=origins,
                    async_mode=env.get("VTT_ASYNC_MODE") or None,
                    dist_dir=Path(env["VTT_DIST_DIR"]) if env.get("VTT_DIST_DIR") else None,
                    legacy_source=_legacy_scenes, legacy_asset=_legacy_asset,
                    asset_backend=asset_backends.from_env(env))
