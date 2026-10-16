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

    def is_campaign_gm(self, session, user_id: str, campaign_id: str) -> bool:
        if _runtime.sql_enabled():
            membership = session.get(CampaignMembership, (campaign_id, user_id))
            return membership is not None and membership.role == "gm"
        return _campaigns.is_gm(_campaigns.get_campaign(campaign_id, refresh=True), user_id)


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
    return init_vtt(app, db=Database.from_env("DATABASE_URL", environ=env), host=AppHost(),
                    grant_key=grant_key, token_key=TokenKey.derive(secret), allowed_origins=origins,
                    async_mode=env.get("VTT_ASYNC_MODE") or None)
