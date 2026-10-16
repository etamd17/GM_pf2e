"""M3: table pairing, revocation, the campaign table epoch and the heartbeat
(VTT-ADR-0002 section 10; acceptance criteria 11, 12 and the server half of 14).

Pairing goes through the real HTTP routes; the paired device then connects
through the real namespace with the cookie the redemption set."""

from __future__ import annotations

import re
from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from sqlalchemy import func, select

from vtt.storage import grants as G
from vtt.storage.models import VttPairingChallenge, VttTableGrant
from vtt.transport import socket as socket_module
from vtt.transport.socket import NAMESPACE

from .conftest import CSRF, GRANT_KEY, payload, received
from .test_m1_read_path import seed_scene, subscribe_table

ORIGIN = "https://localhost"


def gm_post(stack, path, body):
    client = stack.app.test_client()
    return client.post(path, json=body, headers={"X-CSRF-Token": CSRF}, base_url=ORIGIN)


def issue(stack, campaign="camp_a"):
    response = gm_post(stack, "/vtt/api/table-pairing", {"campaignId": campaign})
    assert response.status_code == 200, response.get_json()
    return response.get_json()["code"]


def redeem(stack, code, *, base_url=ORIGIN, origin=ORIGIN, addr="10.0.0.1"):
    client = stack.app.test_client()
    headers = {"Origin": origin} if origin else {}
    return client.post("/vtt/api/table-pairing/redeem", json={"code": code}, headers=headers,
                       base_url=base_url, environ_base={"REMOTE_ADDR": addr})


def credential_of(response):
    cookie = SimpleCookie()
    cookie.load(response.headers["Set-Cookie"])
    return cookie["vtt_table"].value


def pair(stack, campaign="camp_a"):
    response = redeem(stack, issue(stack, campaign))
    assert response.status_code == 200, response.get_json()
    return credential_of(response)


def count(stack, model):
    with stack.db.session() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def limits(stack):
    return stack.app.blueprints["vtt_pairing"].limits


# --- issuing ---------------------------------------------------------------------------------

def test_a_code_is_eight_crockford_characters_and_is_never_stored(stack):
    code = issue(stack)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", code)
    with stack.db.session() as session:
        row = session.execute(select(VttPairingChallenge)).scalar_one()
        stored = " ".join(str(getattr(row, c.name)) for c in VttPairingChallenge.__table__.columns)
    assert code.replace("-", "") not in stored and code not in stored
    assert len(row.code_mac) == 64


def test_issuing_needs_csrf_and_gm_of_that_campaign(stack):
    client = stack.app.test_client()
    assert client.post("/vtt/api/table-pairing", json={"campaignId": "camp_a"},
                       base_url=ORIGIN).status_code == 401
    assert gm_post(stack, "/vtt/api/table-pairing", {"campaignId": "camp_b"}).status_code == 403
    assert count(stack, VttPairingChallenge) == 0


def test_issuing_again_retires_the_previous_code(stack):
    first = issue(stack)
    second = issue(stack)
    assert redeem(stack, first).get_json() == {"error": "pairing-failed"}
    assert redeem(stack, second).status_code == 200


# --- redeeming -------------------------------------------------------------------------------

def test_redeeming_sets_a_locked_down_cookie_and_the_device_gets_the_table(stack):
    seed_scene(stack.db)
    response = redeem(stack, issue(stack))
    assert response.status_code == 200 and response.get_json() == {"paired": True}
    header = response.headers["Set-Cookie"]
    for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/", f"Max-Age={12 * 3600}"):
        assert flag in header, flag
    assert response.headers["Cache-Control"] == "no-store"
    with stack.db.session() as session:
        grant = session.execute(select(VttTableGrant)).scalar_one()
        assert grant.campaign_id == "camp_a" and grant.epoch == 0
        assert credential_of(response) not in grant.verifier
    table, ack = subscribe_table(stack, credential_of(response))
    assert ack is None
    [snap] = [payload(m) for m in received(table, "vtt.snapshot")]
    assert snap["audience"] == "table" and snap["sceneId"] == "scene_a"


def test_codes_normalize_case_hyphens_and_crockford_aliases(stack, monkeypatch):
    monkeypatch.setattr(G, "new_code", lambda: "0123ABCD")
    issue(stack)
    assert redeem(stack, "ol 23-abcd").status_code == 200


def test_a_code_redeems_once(stack):
    code = issue(stack)
    assert redeem(stack, code).status_code == 200
    again = redeem(stack, code, addr="10.0.0.2")
    assert again.status_code == 400 and again.get_json() == {"error": "pairing-failed"}
    assert count(stack, VttTableGrant) == 1


def test_an_expired_code_fails_the_same_way(stack):
    code = issue(stack)
    stack.advance_clock(G.CHALLENGE_TTL + timedelta(seconds=1))
    response = redeem(stack, code)
    assert response.get_json() == {"error": "pairing-failed"} and count(stack, VttTableGrant) == 0


@pytest.mark.parametrize("code", ["ZZZZ-ZZZZ", "short", "", None, 12345678, "U" * 8, "0123ABCD9"])
def test_wrong_and_malformed_codes_fail_without_detail(stack, code):
    issue(stack)
    response = redeem(stack, code)
    assert response.status_code == 400 and response.get_json() == {"error": "pairing-failed"}
    assert count(stack, VttTableGrant) == 0


def test_cross_origin_and_insecure_lan_redemption_consume_nothing(stack):
    code = issue(stack)
    assert redeem(stack, code, origin="https://evil.example").status_code == 403
    assert redeem(stack, code, origin=None).status_code == 403
    lan = redeem(stack, code, base_url="http://192.168.1.20:5000", origin="http://192.168.1.20:5000")
    assert lan.status_code == 400 and lan.get_json() == {"error": "insecure-origin"}
    assert "Set-Cookie" not in lan.headers
    # none of those touched the challenge: it still redeems from a secure origin
    assert redeem(stack, code).status_code == 200


def test_plain_http_localhost_is_a_secure_context(stack):
    code = issue(stack)
    response = redeem(stack, code, base_url="http://localhost:5000", origin="http://localhost:5000")
    assert response.status_code == 200 and "Secure" in response.headers["Set-Cookie"]


def test_the_ip_limit(stack):
    issue(stack)
    codes = [redeem(stack, "ZZZZ-ZZZ" + str(i)).status_code for i in range(11)]
    assert codes == [400] * 10 + [429]


def test_the_per_code_limit_holds_across_addresses(stack):
    issue(stack)
    codes = [redeem(stack, "ZZZZ-ZZZZ", addr=f"10.0.1.{i}").status_code for i in range(6)]
    assert codes == [400] * 5 + [429]


def test_the_per_campaign_limit(stack, monkeypatch):
    monkeypatch.setattr(limits(stack).campaign, "limit", 2)
    assert redeem(stack, issue(stack)).status_code == 200
    assert redeem(stack, issue(stack), addr="10.0.0.2").status_code == 200
    third = issue(stack)
    assert redeem(stack, third, addr="10.0.0.3").status_code == 429
    assert count(stack, VttTableGrant) == 2


# --- revocation ------------------------------------------------------------------------------

def _revoked_and_closed(stack, client, sub_id):
    sent = [(e, d) for _, e, d in stack.sent if e == "vtt.resync_required" and d["subscriptionId"] == sub_id]
    assert sent == [("vtt.resync_required", {"schemaVersion": 1, "subscriptionId": sub_id, "reason": "revoked"})]
    assert not client.is_connected(NAMESPACE)


def _reconnect_reason(stack, credential):
    client = stack.table_client(credential)
    assert not client.is_connected(NAMESPACE)
    return client


def test_revoking_one_table_ends_it_and_leaves_the_other(stack):
    seed_scene(stack.db)
    a, b = pair(stack), pair(stack)
    table_a, _ = subscribe_table(stack, a)
    table_b, _ = subscribe_table(stack, b)
    sub_a = payload(received(table_a, "vtt.snapshot")[0])["subscriptionId"]
    received(table_b)
    with stack.db.session() as session:
        grant_a = session.execute(select(VttTableGrant).where(
            VttTableGrant.verifier == socket_module.table_verifier(GRANT_KEY, a))).scalar_one().id
    response = gm_post(stack, f"/vtt/api/table-grants/{grant_a}/revoke", {"campaignId": "camp_a"})
    assert response.status_code == 200
    _revoked_and_closed(stack, table_a, sub_a)
    assert table_b.is_connected(NAMESPACE) and received(table_b) == []
    _reconnect_reason(stack, a)


def test_revoke_all_ends_every_table_and_new_pairing_still_works(stack):
    seed_scene(stack.db)
    a, b = pair(stack), pair(stack)
    tables = [subscribe_table(stack, c)[0] for c in (a, b)]
    subs = [payload(received(t, "vtt.snapshot")[0])["subscriptionId"] for t in tables]
    assert gm_post(stack, "/vtt/api/table-grants/revoke-all", {"campaignId": "camp_a"}).status_code == 200
    for table, sub in zip(tables, subs):
        _revoked_and_closed(stack, table, sub)
    for credential in (a, b):
        _reconnect_reason(stack, credential)
    fresh, ack = subscribe_table(stack, pair(stack))
    assert ack is None and received(fresh, "vtt.snapshot")


def test_reconnect_reasons_name_revocation_and_expiry(stack, monkeypatch):
    refusals = []

    class Recording(socket_module.ConnectionRefusedError):
        def __init__(self, *args):
            refusals.append(args[0] if args else None)
            super().__init__(*args)

    monkeypatch.setattr(socket_module, "ConnectionRefusedError", Recording)
    epoch_revoked = pair(stack)
    gm_post(stack, "/vtt/api/table-grants/revoke-all", {"campaignId": "camp_a"})
    current = pair(stack)
    assert _reconnect_reason(stack, epoch_revoked) and refusals[-1] == {"schemaVersion": 1,
                                                                        "reason": "grant-revoked"}
    assert stack.table_client(current).is_connected(NAMESPACE)
    stack.advance_clock(G.GRANT_LIFETIME + timedelta(seconds=1))
    assert _reconnect_reason(stack, current) and refusals[-1] == {"schemaVersion": 1, "reason": "grant-expired"}


def test_grants_list_and_revocation_are_campaign_scoped(stack):
    pair(stack)
    listed = stack.app.test_client().get("/vtt/api/table-grants?campaignId=camp_a",
                                         headers={"X-CSRF-Token": CSRF}, base_url=ORIGIN).get_json()["grants"]
    assert len(listed) == 1 and set(listed[0]) == {"id", "createdAt", "expiresAt"}
    grant_id = listed[0]["id"]
    assert gm_post(stack, f"/vtt/api/table-grants/{grant_id}/revoke", {"campaignId": "camp_b"}).status_code == 403
    stack.host.gms.add(("user_gm", "camp_b"))
    assert gm_post(stack, f"/vtt/api/table-grants/{grant_id}/revoke", {"campaignId": "camp_b"}).status_code == 404
    with stack.db.session() as session:
        assert session.get(VttTableGrant, grant_id).revoked_at is None


# --- heartbeat -------------------------------------------------------------------------------

def test_the_heartbeat_bound_is_under_thirty_seconds():
    assert socket_module.HEARTBEAT_SECONDS <= 30


def test_the_heartbeat_ends_an_idle_table_whose_grant_expired_with_no_commit(stack):
    seed_scene(stack.db)
    credential = pair(stack)
    table, _ = subscribe_table(stack, credential)
    sub = payload(received(table, "vtt.snapshot")[0])["subscriptionId"]
    idle = stack.table_client(pair(stack))          # connected, never subscribed
    assert stack.service.heartbeat() == 0
    stack.advance_clock(G.GRANT_LIFETIME + timedelta(seconds=1))
    assert stack.service.heartbeat() == 2           # nothing committed in between
    _revoked_and_closed(stack, table, sub)
    assert not idle.is_connected(NAMESPACE)


def test_the_heartbeat_ends_a_table_revoked_behind_the_hubs_back(stack):
    # A revocation written by another process (no notify here) is still bounded.
    seed_scene(stack.db)
    credential = pair(stack)
    table, _ = subscribe_table(stack, credential)
    received(table)
    with stack.db.transaction() as session:
        G.revoke_all(session, "camp_a")
    assert table.is_connected(NAMESPACE)
    assert stack.service.heartbeat() == 1
    assert not table.is_connected(NAMESPACE)


def test_a_paired_table_still_cannot_command(stack):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, pair(stack))
    table.emit("vtt.command", {"schemaVersion": 1, "commandId": "cmd_0123456789abcdef", "campaignId": "camp_a",
                               "sceneId": "scene_a", "type": "table.clear", "expected": [], "payload": {}},
               namespace=NAMESPACE)
    assert not table.is_connected(NAMESPACE)


def test_issuing_prunes_dead_challenges(stack):
    redeem(stack, issue(stack))                     # one consumed
    stack.advance_clock(G.CHALLENGE_TTL + timedelta(seconds=1))
    issue(stack)
    with stack.db.session() as session:
        rows = session.execute(select(VttPairingChallenge)).scalars().all()
    assert len(rows) == 1 and rows[0].consumed_at is None


def test_the_rate_limiter_does_not_grow_without_bound():
    from vtt.transport.pairing import SlidingWindow
    clock = {"t": 0.0}
    window = SlidingWindow(5, 300, clock=lambda: clock["t"])
    for i in range(SlidingWindow.MAX_KEYS + 10):
        window.hit(f"code{i}")
    clock["t"] = 301.0
    window.hit("fresh")
    assert len(window._events) == 1


# --- the heartbeat when storage or a transport fails (handoff 035) --------------------------

def _storage_down(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("database unreachable")
    monkeypatch.setattr(G, "grant_status", broken)


def test_an_expired_table_is_ended_even_while_storage_cannot_be_checked(stack, monkeypatch):
    seed_scene(stack.db)
    subscribed, _ = subscribe_table(stack, pair(stack))
    sub = payload(received(subscribed, "vtt.snapshot")[0])["subscriptionId"]
    idle = stack.table_client(pair(stack))                  # connected, never subscribed
    stack.advance_clock(G.GRANT_LIFETIME + timedelta(seconds=1))
    _storage_down(monkeypatch)
    assert stack.service.heartbeat() == 2                   # one failed sweep is enough: expiry is known
    _revoked_and_closed(stack, subscribed, sub)
    assert not idle.is_connected(NAMESPACE)
    assert stack.service.sweep_stats["unverifiable"] == 1


def test_an_unseen_revocation_is_bounded_while_storage_stays_down(stack, monkeypatch):
    seed_scene(stack.db)
    subscribed, _ = subscribe_table(stack, pair(stack))
    received(subscribed)
    idle = stack.table_client(pair(stack))
    with stack.db.transaction() as session:                 # revoked behind the hub's back
        G.revoke_all(session, "camp_a")
    _storage_down(monkeypatch)
    stack.advance_clock(timedelta(seconds=socket_module.HEARTBEAT_SECONDS))
    assert stack.service.heartbeat() == 0                   # first failed sweep: still inside the limit
    assert subscribed.is_connected(NAMESPACE) and idle.is_connected(NAMESPACE)
    stack.advance_clock(timedelta(seconds=socket_module.HEARTBEAT_SECONDS))
    assert stack.service.heartbeat() == 2                   # second: 30 s unverified, ended
    assert not subscribed.is_connected(NAMESPACE) and not idle.is_connected(NAMESPACE)
    assert socket_module.HEARTBEAT_SECONDS * 2 <= 30
    assert socket_module.HEARTBEAT_SECONDS < socket_module.UNVERIFIED_LIMIT.total_seconds() <= 30


def test_a_successful_sweep_renews_verification(stack, monkeypatch):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, pair(stack))
    received(table)
    stack.advance_clock(timedelta(seconds=20))
    assert stack.service.heartbeat() == 0                   # storage up: verified now
    _storage_down(monkeypatch)
    stack.advance_clock(timedelta(seconds=20))
    assert stack.service.heartbeat() == 0                   # 20 s since the last good check
    assert table.is_connected(NAMESPACE)
    stack.advance_clock(timedelta(seconds=10))
    assert stack.service.heartbeat() == 1                   # 30 s: ended
    assert not table.is_connected(NAMESPACE)


def test_failed_terminal_sends_do_not_stop_the_sweep(stack):
    seed_scene(stack.db)
    expiring = [subscribe_table(stack, pair(stack))[0] for _ in range(4)]
    stack.advance_clock(G.GRANT_LIFETIME + timedelta(seconds=1))
    valid, _ = subscribe_table(stack, pair(stack))          # paired after the jump: still good
    for client in [*expiring, valid]:
        received(client)
    real = stack.hub.emit

    def broken_controls(sid, event, data):
        if event == "vtt.resync_required":
            raise OSError("transport write failed")
        real(sid, event, data)

    stack.hub.emit = broken_controls
    assert stack.service.heartbeat() == 4                   # one sweep ends all four
    assert all(not c.is_connected(NAMESPACE) for c in expiring)
    assert valid.is_connected(NAMESPACE)
    assert stack.service.sweep_stats["end_errors"] >= 4
