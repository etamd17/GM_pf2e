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


# E-1: app.py registers its turn-step adapter here at startup (app.py imports
# this module, never the reverse).
_TURN_STEP = None


def register_turn_step(fn) -> None:
    global _TURN_STEP
    _TURN_STEP = fn


# E-2: the same, for condition changes.
_CONDITION_CHANGE = None


def register_condition_change(fn) -> None:
    global _CONDITION_CHANGE
    _CONDITION_CHANGE = fn


# E-3: the bestiary search and add adapters.
_BESTIARY_SEARCH = None
_BESTIARY_ADD = None


def register_bestiary(search, add) -> None:
    global _BESTIARY_SEARCH, _BESTIARY_ADD
    _BESTIARY_SEARCH, _BESTIARY_ADD = search, add


# E-5: the area-effect adapter.
_AREA_APPLY = None


def register_area_apply(fn) -> None:
    global _AREA_APPLY
    _AREA_APPLY = fn


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

    def turn_step(self, campaign_id: str, direction: str, expected_actor_id: str, expected_round: int,
                  expected_token: str):
        """Change turns through the tracker (E-1). (code, detail); see app._vtt_turn_step."""
        if _TURN_STEP is None:
            return "unsupported", {}
        return _TURN_STEP(campaign_id, direction, expected_actor_id, expected_round, expected_token)

    def condition_change(self, campaign_id: str, actor_id: str, condition: str, operation: str,
                         expected_value: int, rounds: int = 0):
        """Change a condition through the tracker (E-2). (code, detail); see app._vtt_condition_change."""
        if _CONDITION_CHANGE is None:
            return "unsupported", {}
        return _CONDITION_CHANGE(campaign_id, actor_id, condition, operation, expected_value, rounds)

    def bestiary_search(self, campaign_id: str, query: str):
        """Search the bestiary (E-3). (code, detail); see app._vtt_bestiary_search."""
        if _BESTIARY_SEARCH is None:
            return "unsupported", {}
        return _BESTIARY_SEARCH(campaign_id, query)

    def bestiary_add(self, campaign_id: str, ref: str, request_id: str, expected_encounter: str):
        """Add a bestiary creature to the tracker (E-3). (code, detail); see app._vtt_bestiary_add."""
        if _BESTIARY_ADD is None:
            return "unsupported", {}
        return _BESTIARY_ADD(campaign_id, ref, request_id, expected_encounter)

    def area_apply(self, campaign_id: str, request_id: str, encounter_token: str, action: str,
                   actor_ids: list, params: dict):
        """Apply an area effect through the tracker (E-5). (code, detail); see app._vtt_area_apply."""
        if _AREA_APPLY is None:
            return "unsupported", {}
        return _AREA_APPLY(campaign_id, request_id, encounter_token, action, actor_ids, params)

    def campaign_system(self, campaign_id: str) -> str | None:
        """The campaign's game system (pf2e, cosmere or dnd5e); None if unknown."""
        campaign = _campaigns.get_campaign(campaign_id) or {}
        return campaign.get("system")

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
