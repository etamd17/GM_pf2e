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


# --- the heartbeat when storage or a transport fails (handoffs 035, 037) ---------------------
# Clock-driven: the stack clock is real time plus an offset the tests move. A slow
# read is modelled by advancing that clock inside the read itself.

H = timedelta(seconds=socket_module.HEARTBEAT_SECONDS)


def _storage_down(monkeypatch, stack=None, takes=timedelta(0)):
    def broken(*_args, **_kwargs):
        if stack is not None and takes:
            stack.advance_clock(takes)          # time spent waiting before the failure
        raise OSError("database unreachable")
    monkeypatch.setattr(G, "grant_status", broken)


def test_the_constants_hold_the_thirty_second_bound_by_construction():
    v = timedelta(seconds=socket_module.VERIFY_TIMEOUT_SECONDS)
    limit, bound = socket_module.UNVERIFIED_LIMIT, socket_module.AUTH_BOUND
    assert bound <= timedelta(seconds=30)
    assert H + v < limit                        # one slow failed sweep is tolerated
    assert limit <= 2 * H                       # the second sweep always sees the limit passed
    assert 2 * H + v <= bound                   # and has finished, however slow, inside the bound
    assert socket_module.SWEEP_STATEMENT_TIMEOUT_MS / 1000 < socket_module.VERIFY_TIMEOUT_SECONDS


def test_an_expired_table_is_ended_even_while_storage_cannot_be_checked(stack, monkeypatch):
    seed_scene(stack.db)
    subscribed, _ = subscribe_table(stack, pair(stack))
    sub = payload(received(subscribed, "vtt.snapshot")[0])["subscriptionId"]
    idle = stack.table_client(pair(stack))                  # connected, never subscribed
    stack.advance_clock(G.GRANT_LIFETIME + timedelta(seconds=1))
    _storage_down(monkeypatch)
    assert stack.service.heartbeat() == 2                   # one failed sweep: expiry is known locally
    _revoked_and_closed(stack, subscribed, sub)
    assert not idle.is_connected(NAMESPACE)
    assert stack.service.sweep_stats["unverifiable"] == 1


def test_slow_failing_reads_still_end_an_unseen_revocation_inside_thirty_seconds(stack, monkeypatch):
    # Codex's 037 case: each read fails only after the full read timeout.
    seed_scene(stack.db)
    subscribed, _ = subscribe_table(stack, pair(stack))
    received(subscribed)
    idle = stack.table_client(pair(stack))
    with stack.db.transaction() as session:                 # revoked behind the hub's back
        G.revoke_all(session, "camp_a")
    slow = timedelta(seconds=socket_module.VERIFY_TIMEOUT_SECONDS)
    _storage_down(monkeypatch, stack, takes=slow)
    elapsed = timedelta(0)
    stack.advance_clock(H); elapsed += H
    assert stack.service.heartbeat() == 0                   # fails at 15 s: tolerated once
    elapsed += slow
    assert subscribed.is_connected(NAMESPACE) and idle.is_connected(NAMESPACE)
    stack.advance_clock(2 * H - elapsed); elapsed = 2 * H   # the cadence does not drift
    assert stack.service.heartbeat() == 2                   # fails at 25 s: ended
    elapsed += slow
    assert elapsed <= socket_module.AUTH_BOUND
    assert not subscribed.is_connected(NAMESPACE) and not idle.is_connected(NAMESPACE)


def test_a_stalled_read_is_abandoned_and_never_renews(stack, monkeypatch):
    # Connection acquisition (or anything else) hangs: the sweep stops waiting
    # after the read timeout, does not stack a second read on the stuck one, and
    # the stuck read's late answer renews nothing.
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, pair(stack))
    received(table)
    principal = next(p for p in stack.service.principals.values() if p.kind == "table")
    verified = principal.verified_at
    release = __import__("threading").Event()
    real_status = G.grant_status

    def stalled(*args, **kwargs):
        release.wait(10)
        return real_status(*args, **kwargs)

    monkeypatch.setattr(G, "grant_status", stalled)
    monkeypatch.setattr(socket_module, "VERIFY_TIMEOUT_SECONDS", 0.2)
    try:
        stack.advance_clock(H)
        assert stack.service.heartbeat() == 0               # abandoned at the timeout, tolerated
        assert stack.service.sweep_stats["timeouts"] == 1 and table.is_connected(NAMESPACE)
        stack.advance_clock(H)
        assert stack.service.heartbeat() == 1               # still stuck: no new read, ended
        assert stack.service.sweep_stats["timeouts"] == 2
        assert not table.is_connected(NAMESPACE)
    finally:
        release.set()                                       # the stuck read finishes late
    __import__("time").sleep(0.2)
    assert principal.verified_at == verified                # and renewed nothing


def test_a_successful_sweep_renews_verification_to_when_its_read_started(stack, monkeypatch):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, pair(stack))
    received(table)
    principal = next(p for p in stack.service.principals.values() if p.kind == "table")
    real_status = G.grant_status

    def slow_ok(*args, **kwargs):
        stack.advance_clock(timedelta(seconds=3))
        return real_status(*args, **kwargs)

    stack.advance_clock(H)
    read_start = stack.now()
    monkeypatch.setattr(G, "grant_status", slow_ok)
    assert stack.service.heartbeat() == 0
    monkeypatch.setattr(G, "grant_status", real_status)
    # stamped at the read's start, not after the 3 s it took
    assert read_start <= principal.verified_at < read_start + timedelta(seconds=1)
    assert stack.now() - principal.verified_at >= timedelta(seconds=3)
    _storage_down(monkeypatch)
    stack.advance_clock(H)
    assert stack.service.heartbeat() == 0 and table.is_connected(NAMESPACE)
    stack.advance_clock(H)
    assert stack.service.heartbeat() == 1 and not table.is_connected(NAMESPACE)


def test_the_cadence_is_fixed_and_a_slow_sweep_does_not_push_it_back():
    nxt = socket_module.next_start
    assert nxt(100.0, 103.0, 10.0) == 110.0                 # sweep took 3 s: next still at 110
    assert nxt(100.0, 109.9, 10.0) == 110.0
    assert nxt(100.0, 115.0, 10.0) == 115.0                 # fell behind: run at once


def test_run_bounded_returns_raises_and_times_out():
    assert socket_module.run_bounded(lambda: 7, 1.0) == 7
    with pytest.raises(KeyError):
        socket_module.run_bounded(lambda: {}["x"], 1.0)
    gate = __import__("threading").Event()
    with pytest.raises(socket_module.VerifyTimeout):
        socket_module.run_bounded(lambda: gate.wait(5), 0.05)
    gate.set()


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
