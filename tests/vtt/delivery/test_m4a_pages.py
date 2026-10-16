"""M4a: serving the client, the GM scene list, and ending member connections
when the app revokes a member (handoff 040)."""

from __future__ import annotations

import json
import re
import shutil

from vtt.storage import writes as W
from vtt.transport import pages
from vtt.transport.socket import NAMESPACE

from .conftest import payload, received
from .test_m1_read_path import seed_scene, subscribe_gm

BOOT = re.compile(r'<script id="vtt-boot" type="application/json">(.*?)</script>')


def boot_of(response):
    match = BOOT.search(response.get_data(as_text=True))
    assert match, "no boot JSON"
    return json.loads(match.group(1))


def get(stack, path):
    return stack.app.test_client().get(path, base_url="https://localhost")


# --- pages ---------------------------------------------------------------------------------

def test_the_gm_page_needs_gm_of_that_campaign(stack):
    assert get(stack, "/vtt/gm?campaignId=camp_b").status_code == 403
    assert get(stack, "/vtt/gm").status_code == 403
    stack.host.member_id = None
    assert get(stack, "/vtt/gm?campaignId=camp_a").status_code == 403


def test_the_gm_page_carries_its_boot_data_and_is_never_cached(stack):
    seed_scene(stack.db)
    response = get(stack, "/vtt/gm?campaignId=camp_a")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    assert boot_of(response) == {"campaignId": "camp_a", "sceneId": "scene_a",
                                 "csrf": "csrf-token-for-tests-000000000000"}
    assert '<div id="root"></div>' in response.get_data(as_text=True)


def test_the_gm_page_opens_the_requested_then_the_table_then_the_first_scene(stack):
    W.create_scene(stack.db, "camp_a", "b_scene", name="Beta")
    W.create_scene(stack.db, "camp_a", "a_scene", name="Alpha")
    W.create_scene(stack.db, "camp_b", "other", name="Aaa other campaign")
    assert boot_of(get(stack, "/vtt/gm?campaignId=camp_a"))["sceneId"] == "a_scene"        # first by name
    W.set_table_pointer(stack.db, "camp_a", "b_scene")
    assert boot_of(get(stack, "/vtt/gm?campaignId=camp_a"))["sceneId"] == "b_scene"        # on the table
    assert boot_of(get(stack, "/vtt/gm?campaignId=camp_a&sceneId=a_scene"))["sceneId"] == "a_scene"
    # another campaign's scene id is never opened
    assert boot_of(get(stack, "/vtt/gm?campaignId=camp_a&sceneId=other"))["sceneId"] == "b_scene"


def test_a_campaign_with_no_scenes_boots_with_none(stack):
    assert boot_of(get(stack, "/vtt/gm?campaignId=camp_a"))["sceneId"] is None


def test_the_boot_json_cannot_end_its_script_element():
    script = pages._boot_script({"x": "</script><script>alert(1)</script>&"})
    assert script.count("</script>") == 1 and "<script>alert" not in script
    inner = BOOT.search(script).group(1)
    assert json.loads(inner) == {"x": "</script><script>alert(1)</script>&"}


def test_the_table_page_is_a_shell_with_no_data(stack):
    seed_scene(stack.db)
    response = get(stack, "/vtt/table")
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    body = response.get_data(as_text=True)
    assert "vtt-boot" not in body and "camp_a" not in body and "Battlefield" not in body


def test_assets_are_served_immutable_and_cannot_escape_the_build(stack):
    response = get(stack, "/vtt/assets/app-abc123.js")
    assert response.status_code == 200 and "immutable" in response.headers["Cache-Control"]
    assert get(stack, "/vtt/assets/../index.html").status_code == 404
    assert get(stack, "/vtt/assets/%2e%2e/index.html").status_code == 404
    assert get(stack, "/vtt/assets/missing.js").status_code == 404
    # the HTML entries and Vite's manifest sit in dist/ too, and are never assets
    assert get(stack, "/vtt/assets/index.html").status_code == 404
    assert get(stack, "/vtt/assets/table.html").status_code == 404
    assert get(stack, "/vtt/assets/.vite/manifest.json").status_code == 404


def test_without_a_build_the_pages_say_so(stack):
    shutil.rmtree(stack.dist)
    for path in ("/vtt/gm?campaignId=camp_a", "/vtt/table", "/vtt/assets/app-abc123.js"):
        response = get(stack, path)
        assert response.status_code == 503 and "not built" in response.get_data(as_text=True)


def test_the_scene_list_is_the_gms_and_marks_the_table_scene(stack):
    seed_scene(stack.db)
    W.create_scene(stack.db, "camp_a", "scene_z", name="Zed")
    W.create_scene(stack.db, "camp_b", "secret", name="Other")
    body = get(stack, "/vtt/api/scenes?campaignId=camp_a").get_json()
    assert body == {"scenes": [
        {"id": "scene_a", "name": "Battlefield", "published": True, "onTable": True},
        {"id": "scene_z", "name": "Zed", "published": False, "onTable": False},
    ]}
    assert get(stack, "/vtt/api/scenes?campaignId=camp_b").status_code == 403


# --- the app revoking a member -----------------------------------------------------------------

def test_a_membership_change_revokes_that_campaigns_subscriptions_only(stack):
    stack.host.gms.add(("user_gm", "camp_b"))
    seed_scene(stack.db)
    W.create_scene(stack.db, "camp_b", "scene_b", name="B")
    in_a, _ = subscribe_gm(stack)
    in_b, _ = subscribe_gm(stack, scene_id="scene_b", campaign_id="camp_b")
    sub_a = payload(received(in_a, "vtt.snapshot")[0])["subscriptionId"]
    received(in_b)
    stack.sent.clear()
    assert stack.service.end_member("user_gm", "camp_a") == 1
    assert [(e, d) for _, e, d in stack.sent] == [
        ("vtt.resync_required", {"schemaVersion": 1, "subscriptionId": sub_a, "reason": "revoked"})]
    assert not in_a.is_connected(NAMESPACE)
    assert in_b.is_connected(NAMESPACE)


def test_an_ended_session_closes_every_connection_of_that_member(stack):
    seed_scene(stack.db)
    first, _ = subscribe_gm(stack)
    second = stack.gm_client()                       # connected, not subscribed
    received(first)
    stack.sent.clear()
    assert stack.service.end_member("user_gm", None) == 2
    assert not first.is_connected(NAMESPACE) and not second.is_connected(NAMESPACE)
    assert stack.sent == []                           # no control: reconnecting answers session-ended
    assert not stack.hub._by_sid


def test_a_command_from_an_ended_session_answers_authentication_required(stack):
    seed_scene(stack.db)
    client, _ = subscribe_gm(stack)
    received(client)
    sid = next(iter(stack.service.principals))
    stack.service.principals[sid].session_ended = True    # marked, not yet closed
    stack.sent.clear()
    client.emit("vtt.command", {"schemaVersion": 1, "commandId": "cmd_0123456789abcdef", "campaignId": "camp_a",
                                "sceneId": "scene_a", "type": "table.clear", "expected": [], "payload": {}},
                namespace=NAMESPACE)
    assert [(e, d) for _, e, d in stack.sent] == [("vtt.ack", {
        "schemaVersion": 1, "commandId": "cmd_0123456789abcdef", "outcome": "rejected",
        "code": "authentication-required"})]
    assert not client.is_connected(NAMESPACE)


def test_the_apps_revocation_points_reach_the_vtt(monkeypatch):
    import app

    calls = []

    class Service:
        def end_member(self, user_id, campaign_id=None):
            calls.append((user_id, campaign_id))

    monkeypatch.setattr(app, "VTT_SERVICE", Service())
    monkeypatch.setattr(app, "_sse_subscribers", [])
    app._close_sse_subscribers_for_user("camp_a", "user_1")
    app._close_sse_subscribers_for_user(None, "user_2")
    app._close_account_sse_subscribers()
    app._close_sse_subscribers_for_user("camp_a", None)          # no user: nothing to revoke
    assert calls == [("user_1", "camp_a"), ("user_2", None), (None, None)]


def test_a_vtt_failure_never_breaks_the_apps_own_revocation(monkeypatch):
    import app

    class Service:
        def end_member(self, *_args):
            raise RuntimeError("vtt down")

    monkeypatch.setattr(app, "VTT_SERVICE", Service())
    monkeypatch.setattr(app, "_sse_subscribers", [])
    assert app._close_sse_subscribers_for_user("camp_a", "user_1") == 0


# --- a browser holding both credentials (found in the M4a end-to-end run) ------------------

def test_a_member_page_in_a_browser_that_is_also_a_paired_table_connects_as_the_member(stack):
    from .conftest import CSRF, add_grant
    from vtt.transport.socket import TABLE_COOKIE
    seed_scene(stack.db)
    credential = add_grant(stack.db)
    flask_client = stack.app.test_client()
    flask_client.set_cookie(TABLE_COOKIE, credential)
    member = stack.service.socketio.test_client(stack.app, namespace=NAMESPACE, flask_test_client=flask_client,
                                                 auth={"csrf": CSRF})
    table = stack.service.socketio.test_client(stack.app, namespace=NAMESPACE, flask_test_client=flask_client)
    kinds = sorted(p.kind for p in stack.service.principals.values())
    assert member.is_connected(NAMESPACE) and table.is_connected(NAMESPACE)
    assert kinds == ["member", "table"]


def test_a_revoked_table_cookie_does_not_refuse_a_member_page(stack):
    from .conftest import CSRF, add_grant
    from vtt.transport.socket import TABLE_COOKIE
    credential = add_grant(stack.db, revoked=True)
    flask_client = stack.app.test_client()
    flask_client.set_cookie(TABLE_COOKIE, credential)
    member = stack.service.socketio.test_client(stack.app, namespace=NAMESPACE, flask_test_client=flask_client,
                                                 auth={"csrf": CSRF})
    assert member.is_connected(NAMESPACE)


def test_a_member_page_with_a_bad_token_is_refused_even_with_a_valid_table_cookie(stack):
    from .conftest import add_grant
    from vtt.transport.socket import TABLE_COOKIE
    flask_client = stack.app.test_client()
    flask_client.set_cookie(TABLE_COOKIE, add_grant(stack.db))
    member = stack.service.socketio.test_client(stack.app, namespace=NAMESPACE, flask_test_client=flask_client,
                                                 auth={"csrf": "wrong-token-0000000000000000000"})
    assert not member.is_connected(NAMESPACE)
    assert not stack.service.principals
