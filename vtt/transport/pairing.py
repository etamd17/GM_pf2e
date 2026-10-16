"""Table pairing and revocation over same-origin HTTP (VTT-ADR-0002 section 10, M3).

GM routes need the member session, the app's CSRF token in ``X-CSRF-Token`` and
current GM membership of the campaign named in the body:

- ``POST /vtt/api/table-pairing``             issue a code (shown once)
- ``GET  /vtt/api/table-grants?campaignId=``  list active grants
- ``POST /vtt/api/table-grants/<id>/revoke``  revoke one table
- ``POST /vtt/api/table-grants/revoke-all``   bump the campaign table epoch

The device route has no session. The code is its only authority:

- ``POST /vtt/api/table-pairing/redeem``

Redemption, in order:
1. The request must be same-origin (``Origin`` equals this app's origin, or
   is one of the configured allowed origins).
2. It must come from a secure context: HTTPS, or a loopback/``.localhost``
   host. The cookie is always ``Secure`` (never relaxed for LAN use), so an
   insecure LAN origin is refused here, visibly, before any code is touched.
3. Rate limits per IP and per normalized code, then the code itself, then per
   campaign. Every way a code can be wrong gets the same detail-free
   ``pairing-failed``.
4. One transaction: consume the challenge (conditional UPDATE) and create the
   grant under the current table epoch. Then the ``vtt_table`` cookie.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from flask import Blueprint, jsonify, make_response, request

from core.persistence.database import Database
from vtt.storage import grants as G

GM_HEADER = "X-CSRF-Token"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


class SlidingWindow:
    """At most ``limit`` events per key in any ``seconds`` window (in process,
    which is honest for production's single worker)."""

    MAX_KEYS = 4096

    def __init__(self, limit: int, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.seconds, self.clock = limit, seconds, clock
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record an attempt; False when the key is over its limit."""
        now = self.clock()
        with self._lock:
            if len(self._events) > self.MAX_KEYS:
                # Random codes from many addresses must not grow this without
                # bound: drop every key with nothing left in its window.
                for stale in [k for k, ev in self._events.items() if not ev or ev[-1] <= now - self.seconds]:
                    del self._events[stale]
            events = self._events.setdefault(key, deque())
            while events and events[0] <= now - self.seconds:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class Limits:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.ip = SlidingWindow(10, 300, clock)            # redeem attempts per address
        self.code = SlidingWindow(5, 300, clock)           # attempts at one normalized code
        self.campaign = SlidingWindow(10, 3600, clock)     # successful pairings per campaign
        self.issue = SlidingWindow(10, 300, clock)         # codes issued per campaign


def _secure_context() -> bool:
    if request.is_secure:
        return True
    host = request.host or ""
    name = host[:host.index("]") + 1] if host.startswith("[") and "]" in host else host.split(":")[0]
    name = name.lower()
    return name in LOOPBACK_HOSTS or name.endswith(".localhost")


def _same_origin(allowed: list[str] | None) -> bool:
    origin = request.headers.get("Origin")
    if not origin:
        return False
    own = request.host_url.rstrip("/")
    return secrets.compare_digest(origin, own) or origin in (allowed or [])


def _failed(status: int = 400, reason: str = "pairing-failed"):
    return jsonify({"error": reason}), status


def make_blueprint(*, db: Database, host, grant_key: bytes, cookie_name: str, verifier: Callable[[str], str],
                   on_access_change: Callable[[str], None], now: Callable[[], datetime],
                   allowed_origins: list[str] | None = None, limits: Limits | None = None) -> Blueprint:
    bp = Blueprint("vtt_pairing", __name__)
    limits = limits or Limits()
    bp.limits = limits          # exposed for tests

    def gm_campaign(campaign_id: Any) -> tuple[str | None, Any]:
        """(user_id, None) when the caller is this campaign's GM, else (None, error response)."""
        user_id = host.current_member_id()
        if not user_id or not host.csrf_valid(request.headers.get(GM_HEADER)):
            return None, _failed(401, "authentication-required")
        if not isinstance(campaign_id, str) or not campaign_id or len(campaign_id) > 64:
            return None, _failed(403, "unauthorized")
        with db.session() as session:
            if not host.is_campaign_gm(session, user_id, campaign_id):
                return None, _failed(403, "unauthorized")
        return user_id, None

    def body() -> dict:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    @bp.post("/vtt/api/table-pairing")
    def issue():
        campaign_id = body().get("campaignId")
        user_id, error = gm_campaign(campaign_id)
        if error:
            return error
        if not limits.issue.hit(campaign_id):
            return _failed(429, "rate-limited")
        with db.transaction() as session:
            code = G.issue_challenge(session, grant_key, campaign_id, user_id, now())
        return jsonify({"code": G.display_code(code), "expiresInSeconds": int(G.CHALLENGE_TTL.total_seconds())})

    @bp.get("/vtt/api/table-grants")
    def list_grants():
        campaign_id = request.args.get("campaignId")
        _user, error = gm_campaign(campaign_id)
        if error:
            return error
        with db.session() as session:
            rows = G.active_grants(session, campaign_id, now())
            return jsonify({"grants": [{"id": g.id, "createdAt": G.utc(g.created_at).isoformat(),
                                        "expiresAt": G.utc(g.expires_at).isoformat()} for g in rows]})

    @bp.post("/vtt/api/table-grants/<grant_id>/revoke")
    def revoke(grant_id):
        campaign_id = body().get("campaignId")
        _user, error = gm_campaign(campaign_id)
        if error:
            return error
        with db.transaction() as session:
            found = G.revoke_grant(session, campaign_id, grant_id, now())
        if not found:
            return _failed(404, "not-found")
        on_access_change(campaign_id)
        return jsonify({"revoked": True})

    @bp.post("/vtt/api/table-grants/revoke-all")
    def revoke_all():
        campaign_id = body().get("campaignId")
        _user, error = gm_campaign(campaign_id)
        if error:
            return error
        with db.transaction() as session:
            G.revoke_all(session, campaign_id)
        on_access_change(campaign_id)
        return jsonify({"revoked": True})

    @bp.post("/vtt/api/table-pairing/redeem")
    def redeem():
        if not _same_origin(allowed_origins):
            return _failed(403, "origin-refused")
        if not _secure_context():
            # Before the code is read: an insecure LAN origin consumes nothing.
            return _failed(400, "insecure-origin")
        if not limits.ip.hit(request.remote_addr or "unknown"):
            return _failed(429, "rate-limited")
        code = G.normalize_code(body().get("code"))
        if code is None:
            return _failed()
        if not limits.code.hit(code):
            return _failed(429, "rate-limited")
        current = now()
        with db.session() as session:
            campaign_id = G.challenge_campaign(session, grant_key, code, current)
        if campaign_id is None:
            return _failed()
        if not limits.campaign.hit(campaign_id):
            return _failed(429, "rate-limited")
        credential = secrets.token_urlsafe(32)
        with db.transaction() as session:
            # A redeemer that lost the race (or a code that expired in between)
            # gets None here and writes nothing; the empty transaction commits.
            consumed = G.consume_challenge(session, grant_key, code, current)
            if consumed is not None:
                G.create_grant(session, verifier(credential), consumed, current)
        if consumed is None:
            return _failed()
        response = make_response(jsonify({"paired": True}))
        response.set_cookie(cookie_name, credential, max_age=int(G.GRANT_LIFETIME.total_seconds()), secure=True,
                            httponly=True, samesite="Strict", path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    return bp
