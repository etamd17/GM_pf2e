"""Request-lifecycle coverage for the process-global campaign lock."""
from __future__ import annotations

import pytest
from flask import jsonify

import app


class _RecordingLock:
    def __init__(self):
        self.depth = 0
        self.events = []

    def acquire(self):
        assert self.depth == 0, "test endpoint unexpectedly acquired recursively"
        self.depth = 1
        self.events.append(("acquire", self.depth))
        return True

    def release(self):
        assert self.depth == 1, "request released an unheld live lock"
        self.depth = 0
        self.events.append(("release", self.depth))


def test_legacy_live_policy_holds_dispatch_lock_through_view(monkeypatch):
    """Legacy auth bypass happens only after live-state serialization begins."""
    lock = _RecordingLock()
    monkeypatch.setattr(app, "_LIVE_CAMPAIGN_DISPATCH_LOCK", lock)
    monkeypatch.setattr(app, "_account_mode", lambda: False)

    def probe_view():
        lock.events.append(("view", lock.depth))
        return jsonify({"held_during_view": lock.depth == 1})

    # Keep Flask's endpoint identity (`api_campaign`) so policy lookup still
    # resolves LIVE_CAMPAIGN_MEMBER while avoiding unrelated file/config reads.
    monkeypatch.setitem(app.app.view_functions, "api_campaign", probe_view)

    response = app.app.test_client().get("/api/campaign")

    assert response.status_code == 200
    assert response.get_json() == {"held_during_view": True}
    assert lock.events == [
        ("acquire", 1),
        ("view", 1),
        ("release", 0),
    ]
    assert lock.depth == 0


@pytest.mark.parametrize(
    ("method", "path", "endpoint"),
    (
        ("GET", "/api/healing_log", "healing_log_get"),
        ("GET", "/api/session_journal", "session_journal_get"),
        ("GET", "/api/gm_party_state", "gm_party_state"),
        ("GET", "/api/list_encounters", "list_encounters"),
        ("POST", "/api/cosmere/add_party", "add_cosmere_party"),
        ("POST", "/api/cosmere/speed/probe", "cosmere_speed_choice"),
    ),
)
def test_legacy_password_mode_centrally_gates_gm_policy_routes(
    monkeypatch, method, path, endpoint
):
    """GM policy is authoritative even when a legacy route has no decorator."""
    monkeypatch.setattr(app, "GM_PASSWORD", "legacy-test-secret")
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(
        app._auth,
        "account_store_state",
        lambda: app._auth.ACCOUNT_STORE_UNINITIALIZED,
    )

    def probe_view(**route_values):
        return jsonify({"endpoint": endpoint, "route_values": route_values})

    # Preserve the registered endpoint identity so the central policy lookup is
    # exercised while avoiding unrelated filesystem and live-state mutations.
    monkeypatch.setitem(app.app.view_functions, endpoint, probe_view)
    client = app.app.test_client()

    anonymous = client.open(path, method=method)
    assert anonymous.status_code == 403
    assert anonymous.get_json()["error"] == "campaign_gm_required"

    with client.session_transaction() as legacy_session:
        legacy_session["gm_authenticated"] = True

    authorized = client.open(path, method=method)
    assert authorized.status_code == 200
    assert authorized.get_json()["endpoint"] == endpoint
