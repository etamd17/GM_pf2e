"""Black-box HTTP contracts for account-mode campaign containment.

These tests describe the PR2 target behavior and may fail until the central
route-policy enforcement is wired into ``app.py``.  Every scenario imports the
application in a fresh subprocess after binding ``DATA_DIR`` to a pytest-owned
temporary directory; no process-global campaign state or account data is shared
with another test.

Fixture assumptions:

* the committed ``tests/fixtures/kyle_l10.json`` file is a valid PF2e character;
* direct ``core.auth``/``core.campaigns`` setup is trusted test arrangement, while
  all behavior under test crosses Flask's HTTP boundary;
* a GM activation is the only operation used to change the global live campaign;
* templates are stubbed because this suite owns backend authorization contracts,
  not rendered UI; and
* SSE probes use ``buffered=False`` and close immediately without waiting for a
  heartbeat or consuming an unbounded response body.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from urllib.parse import parse_qs, urlparse

import pytest


_REPO = Path(__file__).resolve().parent.parent
_PF2E_FIXTURE = _REPO / "tests" / "fixtures" / "kyle_l10.json"
_RESULT_PREFIX = "CAMPAIGN_CONTAINMENT_RESULT="


_ACCOUNT_FIXTURE = r'''
import builtins
import copy
import json
import os
from pathlib import Path
from urllib.parse import quote

import app as application
from core import auth, campaigns, storage

application.app.config.update(TESTING=True)
application.render_template = (
    lambda template_name, *args, **kwargs: "rendered:" + template_name
)
# An authorized SSE response yields its connected frame immediately.  Suppress
# the unrelated keepalive worker so closing that response is the only cleanup
# needed in these short-lived subprocesses.
application._ensure_sse_keepalive = lambda: None


def _new_user(username, password, display_name):
    return auth.create_user(
        username,
        password,
        display_name=display_name,
        is_admin=False,
    )


gm_a = _new_user("gm-a", "secret-a", "GM Alpha")
player_a = _new_user("player-a", "secret-player-a", "Player Alpha")
peer_a = _new_user("peer-a", "secret-peer-a", "Peer Alpha")
outsider = _new_user("outsider", "secret-outsider", "Outsider")
gm_b = _new_user("gm-b", "secret-b", "GM Bravo")

campaign_a = campaigns.create_campaign("Alpha Campaign", "pf2e", gm_a["id"])
campaigns.add_member(campaign_a["id"], player_a["id"], "player")
campaigns.add_member(campaign_a["id"], peer_a["id"], "player")
campaign_b = campaigns.create_campaign("Bravo Campaign", "pf2e", gm_b["id"])


def _seed_character(campaign_id, file_name, character_name, owner_user_id):
    source = json.loads(
        Path("tests/fixtures/kyle_l10.json").read_text(encoding="utf-8")
    )
    source = copy.deepcopy(source)
    source["build"]["name"] = character_name
    wrapped = storage.wrap_character(
        storage.new_id(),
        campaign_id,
        "pf2e",
        source,
        owner_user_id=owner_user_id,
    )
    path = Path(storage.party_dir(campaign_id)) / file_name
    storage.atomic_write_json(str(path), wrapped, indent=2)
    return path


alpha_owner_path = _seed_character(
    campaign_a["id"], "alpha_owner.json", "ALPHA OWNER", player_a["id"]
)
alpha_peer_path = _seed_character(
    campaign_a["id"], "alpha_peer.json", "ALPHA PEER", peer_a["id"]
)
bravo_secret_path = _seed_character(
    campaign_b["id"], "bravo_secret.json", "BRAVO SECRET CHARACTER", gm_b["id"]
)


def _seed_cosmere_character(campaign_id, name, owner_user_id):
    pid = storage.new_id().replace("-", "")[:32]
    doc = {
        "id": pid,
        "campaign_id": campaign_id,
        "system": "cosmere",
        "name": name,
        "owner_user_id": owner_user_id,
        "build": {"name": name, "level": 1},
    }
    path = Path(storage.cosmere_pc_dir(campaign_id)) / (pid + ".json")
    storage.atomic_write_json(str(path), doc, indent=2)
    return pid, path


alpha_peer_cosmere_pid, alpha_peer_cosmere_path = _seed_cosmere_character(
    campaign_a["id"], "PEER COSMERE HERO", peer_a["id"]
)


def _signed_in_client(username, password):
    client = application.app.test_client()
    response = client.post(
        "/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 302, (username, response.status_code)
    return client


gm_a_client = _signed_in_client("gm-a", "secret-a")
player_a_client = _signed_in_client("player-a", "secret-player-a")
peer_a_client = _signed_in_client("peer-a", "secret-peer-a")
outsider_client = _signed_in_client("outsider", "secret-outsider")
gm_b_client = _signed_in_client("gm-b", "secret-b")


def _activate(client, campaign_id):
    response = client.post(f"/campaign/{campaign_id}/activate")
    assert response.status_code == 302, (campaign_id, response.status_code)


def _probe(client, path, *, method="GET", **kwargs):
    """Return finite response metadata without draining a successful SSE stream."""
    is_events = path == "/api/events"
    response = client.open(
        path,
        method=method,
        buffered=not is_events,
        **kwargs,
    )
    is_stream = (
        response.status_code == 200
        and response.mimetype == "text/event-stream"
    )
    if is_stream:
        payload = None
        body = ""
    else:
        payload = response.get_json(silent=True)
        body = response.get_data(as_text=True)[:4096]
    result = {
        "status": response.status_code,
        "mimetype": response.mimetype,
        "is_json": bool(response.is_json),
        "is_stream": is_stream,
        "location": response.headers.get("Location", ""),
        "json": payload,
        "body": body,
    }
    response.close()
    return result


def _emit(value):
    print("CAMPAIGN_CONTAINMENT_RESULT=" + json.dumps(value, sort_keys=True))
'''


@pytest.fixture
def isolated_app_process(tmp_path):
    """Run one scenario with app import and durable state isolated from pytest."""
    counter = 0

    def run(body: str) -> subprocess.CompletedProcess[str]:
        nonlocal counter
        counter += 1
        data_dir = tmp_path / f"data-{counter}"
        data_dir.mkdir()
        env = dict(os.environ)
        env.update(
            DATA_DIR=str(data_dir),
            GM_PASSWORD="",
            SETUP_TOKEN="",
            SECRET_KEY="campaign-containment-test-only",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            PYTHONUTF8="1",
        )
        script = (
            "import os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            + textwrap.dedent(_ACCOUNT_FIXTURE)
            + "\n"
            + textwrap.dedent(body)
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=_REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    if not _PF2E_FIXTURE.is_file():
        pytest.fail(f"required committed fixture is missing: {_PF2E_FIXTURE}")
    return run


def _result(completed: subprocess.CompletedProcess[str]):
    assert completed.returncode == 0, (
        f"isolated app scenario failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line[len(_RESULT_PREFIX):])
    pytest.fail(
        "isolated app scenario emitted no result marker\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def _assert_json_denial(response, status):
    assert response["status"] == status, response
    assert response["is_json"] is True, response
    assert response["location"] == "", response
    assert isinstance(response["json"], dict), response
    assert response["json"].get("error"), response


def test_anonymous_account_mode_redirects_browser_and_returns_json_401_for_apis(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        anonymous = application.app.test_client()
        _emit({
            path: _probe(anonymous, path)
            for path in (
                "/player",
                "/api/player_state",
                "/api/gm_party_state",
                "/api/events",
            )
        })
        '''
    )
    observed = _result(completed)

    browser = observed["/player"]
    assert browser["status"] == 302, browser
    login_location = urlparse(browser["location"])
    assert login_location.path == "/login", browser
    assert parse_qs(login_location.query).get("next") == ["/player"], browser

    for path in ("/api/player_state", "/api/gm_party_state", "/api/events"):
        _assert_json_denial(observed[path], 401)
        assert observed[path]["is_stream"] is False, observed[path]


def test_account_mode_enforces_nonmember_member_and_gm_policies(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        _activate(player_a_client, campaign_a["id"])
        # Simulate an authenticated client presenting another campaign id.  The
        # application must validate membership instead of trusting the cookie.
        with outsider_client.session_transaction() as session:
            session["active_campaign_id"] = campaign_a["id"]

        paths = (
            "/player",
            "/api/player_state",
            "/api/gm_party_state",
            "/api/events",
        )
        _emit({
            "nonmember": {path: _probe(outsider_client, path) for path in paths},
            "member": {path: _probe(player_a_client, path) for path in paths},
            "gm": {path: _probe(gm_a_client, path) for path in paths},
        })
        '''
    )
    observed = _result(completed)

    for path, response in observed["nonmember"].items():
        assert response["status"] == 403, (path, response)
        if path.startswith("/api/"):
            _assert_json_denial(response, 403)

    # The member may land directly on their sole owned character, so either a
    # rendered 200 or a same-site sheet redirect is an allowed browser outcome.
    member_player = observed["member"]["/player"]
    assert member_player["status"] in {200, 302}, member_player
    if member_player["status"] == 302:
        assert member_player["location"].startswith("/player/sheet/"), member_player
    assert observed["member"]["/api/player_state"]["status"] == 200
    _assert_json_denial(observed["member"]["/api/gm_party_state"], 403)
    member_events = observed["member"]["/api/events"]
    assert member_events["status"] == 200, member_events
    assert member_events["is_stream"] is True, member_events

    assert observed["gm"]["/player"]["status"] == 200
    assert observed["gm"]["/api/player_state"]["status"] == 200
    assert observed["gm"]["/api/gm_party_state"]["status"] == 200
    gm_events = observed["gm"]["/api/events"]
    assert gm_events["status"] == 200, gm_events
    assert gm_events["is_stream"] is True, gm_events


def test_healing_log_is_visible_only_to_the_live_campaign_gm(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        _activate(player_a_client, campaign_a["id"])
        application.SESSION_HEALING_LOG[:] = [
            {"healer": "GM-ONLY-SENTINEL", "target": "ALPHA OWNER"}
        ]
        _emit({
            "player": _probe(player_a_client, "/api/healing_log"),
            "gm": _probe(gm_a_client, "/api/healing_log"),
        })
        '''
    )
    observed = _result(completed)

    _assert_json_denial(observed["player"], 403)
    assert observed["gm"]["status"] == 200, observed["gm"]
    assert observed["gm"]["json"] == {
        "log": [{"healer": "GM-ONLY-SENTINEL", "target": "ALPHA OWNER"}]
    }


def test_campaign_config_read_requires_live_membership_and_rejects_stale_clients(
    isolated_app_process,
):
    """The live campaign document is not a public discovery endpoint.

    This single matrix also pins the ordering of the checks: a valid member may
    read only while their selected campaign owns the live slot; once another GM
    activates a different table, the old client receives the stable 409 denial
    instead of that table's config.
    """
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        saved = _probe(
            gm_a_client,
            "/api/campaign",
            method="POST",
            json={"tagline": "ALPHA-CONFIG-SENTINEL"},
        )
        assert saved["status"] == 200, saved
        _activate(player_a_client, campaign_a["id"])
        with outsider_client.session_transaction() as session:
            session["active_campaign_id"] = campaign_a["id"]

        anonymous = application.app.test_client()
        before_switch = {
            "anonymous": _probe(anonymous, "/api/campaign"),
            "outsider": _probe(outsider_client, "/api/campaign"),
            "member": _probe(player_a_client, "/api/campaign"),
        }

        _activate(gm_b_client, campaign_b["id"])
        stale = _probe(player_a_client, "/api/campaign")
        _emit({"before_switch": before_switch, "stale": stale})
        '''
    )
    observed = _result(completed)

    _assert_json_denial(observed["before_switch"]["anonymous"], 401)
    _assert_json_denial(observed["before_switch"]["outsider"], 403)

    member = observed["before_switch"]["member"]
    assert member["status"] == 200, member
    assert member["json"]["tagline"] == "ALPHA-CONFIG-SENTINEL", member

    _assert_json_denial(observed["stale"], 409)
    assert "ALPHA-CONFIG-SENTINEL" not in observed["stale"]["body"], observed


def test_public_template_context_does_not_inherit_live_campaign_chrome(
    isolated_app_process,
):
    """Logged-out pages get neutral chrome, not the process-wide live table.

    Context processors are called directly because this suite stubs templates;
    they still execute inside a real anonymous Flask request context.
    """
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        saved = _probe(
            gm_a_client,
            "/api/campaign",
            method="POST",
            json={
                "crest_image": "/campaign-assets/ALPHA-CREST-SENTINEL.png",
                "scene_mood": "dread",
                "cosmere_world": "mistborn",
                "advancement_mode": "xp",
            },
        )
        assert saved["status"] == 200, saved

        with application.app.test_request_context("/login"):
            chrome = application._inject_campaign_chrome()
            account_ctx = application._inject_account_ctx()
            account = {
                key: account_ctx[key]
                for key in (
                    "account_user",
                    "active_campaign",
                    "cosmere_world",
                    "advancement_mode",
                )
            }

        _emit({"chrome": chrome, "account": account})
        '''
    )
    observed = _result(completed)

    assert observed["chrome"]["nav_crest"] == "", observed
    assert observed["chrome"]["scene_mood"] == "calm", observed
    assert observed["account"]["account_user"] is None, observed
    assert observed["account"]["active_campaign"] is None, observed
    assert observed["account"]["cosmere_world"] == "stormlight", observed
    assert observed["account"]["advancement_mode"] == "milestone", observed
    assert "ALPHA-CREST-SENTINEL" not in json.dumps(observed), observed


def test_multipart_import_uses_json_authorization_denials(
    isolated_app_process,
):
    """Fetch-based multipart APIs must never turn auth failures into HTML/302."""
    completed = isolated_app_process(
        '''
        import io

        _activate(gm_a_client, campaign_a["id"])
        with outsider_client.session_transaction() as session:
            session["active_campaign_id"] = campaign_a["id"]
        anonymous = application.app.test_client()

        def upload(client):
            return _probe(
                client,
                "/cosmere/pc/import_pdf",
                method="POST",
                data={"file": (io.BytesIO(b"%PDF-test"), "hero.pdf")},
                content_type="multipart/form-data",
            )

        _emit({
            "anonymous": upload(anonymous),
            "outsider": upload(outsider_client),
        })
        '''
    )
    observed = _result(completed)

    _assert_json_denial(observed["anonymous"], 401)
    _assert_json_denial(observed["outsider"], 403)


def test_stale_campaign_client_gets_409_before_new_live_campaign_data_access(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        assert _probe(gm_a_client, "/api/gm_party_state")["status"] == 200

        # A different GM takes the process-wide live slot.  GM Alpha's cookie
        # still identifies campaign A and is now stale relative to that slot.
        _activate(gm_b_client, campaign_b["id"])
        assert storage.get_live_campaign_id() == campaign_b["id"]

        bravo_before = bravo_secret_path.read_bytes()
        library_before = sorted(application.PARTY_LIBRARY)
        bravo_reads = []
        bravo_abs = os.path.normcase(os.path.abspath(str(bravo_secret_path)))
        real_open = builtins.open

        def tracking_open(file, *args, **kwargs):
            try:
                candidate = os.path.normcase(os.path.abspath(os.fspath(file)))
            except TypeError:
                candidate = ""
            if candidate == bravo_abs:
                bravo_reads.append(str(args[0]) if args else "r")
            return real_open(file, *args, **kwargs)

        builtins.open = tracking_open
        try:
            paths = (
                "/player",
                "/api/player_state",
                "/api/gm_party_state",
                "/api/events",
            )
            responses = {path: _probe(gm_a_client, path) for path in paths}
        finally:
            builtins.open = real_open

        with gm_a_client.session_transaction() as session:
            stale_session = {
                "active_campaign_id": session.get("active_campaign_id"),
                "player_name": session.get("player_name"),
            }

        _emit({
            "responses": responses,
            "bravo_reads": bravo_reads,
            "bravo_unchanged": bravo_before == bravo_secret_path.read_bytes(),
            "library_unchanged": library_before == sorted(application.PARTY_LIBRARY),
            "live_campaign_id": storage.get_live_campaign_id(),
            "campaign_a_id": campaign_a["id"],
            "campaign_b_id": campaign_b["id"],
            "stale_session": stale_session,
        })
        '''
    )
    observed = _result(completed)

    for path, response in observed["responses"].items():
        assert response["status"] == 409, (path, response)
        assert "BRAVO SECRET CHARACTER" not in response["body"], (path, response)
        if path.startswith("/api/"):
            _assert_json_denial(response, 409)
            assert response["is_stream"] is False, response

    assert observed["bravo_reads"] == [], observed
    assert observed["bravo_unchanged"] is True, observed
    assert observed["library_unchanged"] is True, observed
    assert observed["live_campaign_id"] == observed["campaign_b_id"], observed
    assert observed["stale_session"] == {
        "active_campaign_id": observed["campaign_a_id"],
        "player_name": None,
    }


def test_player_cannot_select_view_or_mutate_another_players_character(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        _activate(player_a_client, campaign_a["id"])

        own_selection = _probe(
            player_a_client,
            "/api/join_campaign",
            method="POST",
            json={"name": "ALPHA OWNER"},
        )
        own_view = _probe(
            player_a_client,
            "/player/sheet/" + quote("ALPHA OWNER", safe=""),
        )

        # A forged/stale session actor is still only a locator; ownership is
        # reloaded for every mobile or actor-attributed request.
        with player_a_client.session_transaction() as session:
            session["player_name"] = "ALPHA PEER"
        forged_mobile = _probe(player_a_client, "/mobile")
        forged_nomination = _probe(
            player_a_client,
            "/api/hero_nomination",
            method="POST",
            json={"nominator": "ALPHA PEER", "nominee": "ALPHA OWNER"},
        )
        with player_a_client.session_transaction() as session:
            session["player_name"] = "ALPHA OWNER"

        captured_nominations = []
        real_broadcast = application.sse_broadcast
        def capture_broadcast(event_type, data, **kwargs):
            if event_type == "hero_nomination":
                captured_nominations.append(dict(data))
            return real_broadcast(event_type, data, **kwargs)
        application.sse_broadcast = capture_broadcast
        try:
            owned_nomination = _probe(
                player_a_client,
                "/api/hero_nomination",
                method="POST",
                json={"nominator": "ALPHA PEER", "nominee": "ALPHA PEER"},
            )
        finally:
            application.sse_broadcast = real_broadcast

        peer_before = alpha_peer_path.read_bytes()
        peer_hp_before = application.PARTY_LIBRARY["ALPHA PEER"].current_hp

        other_view = _probe(
            player_a_client,
            "/player/sheet/" + quote("ALPHA PEER", safe=""),
        )
        with player_a_client.session_transaction() as session:
            actor_after_view = session.get("player_name")

        other_selection = _probe(
            player_a_client,
            "/api/join_campaign",
            method="POST",
            json={"name": "ALPHA PEER"},
        )
        with player_a_client.session_transaction() as session:
            actor_after_selection = session.get("player_name")

        other_mutation = _probe(
            player_a_client,
            "/api/adjust_party_hp/" + quote("ALPHA PEER", safe=""),
            method="POST",
            data={"amount": "7", "action": "damage"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        with player_a_client.session_transaction() as session:
            actor_after_mutation = session.get("player_name")

        peer_cosmere_before = alpha_peer_cosmere_path.read_bytes()
        other_builder_view = _probe(
            player_a_client,
            "/cosmere/builder?pc=" + quote(alpha_peer_cosmere_pid, safe=""),
        )
        other_builder_mutation = _probe(
            player_a_client,
            "/cosmere/builder",
            method="POST",
            json={
                "id": alpha_peer_cosmere_pid,
                "build": {"name": "FORGED NAME", "level": 2},
            },
        )

        _emit({
            "own_selection": own_selection,
            "own_view": own_view,
            "forged_mobile": forged_mobile,
            "forged_nomination": forged_nomination,
            "owned_nomination": owned_nomination,
            "captured_nominations": captured_nominations,
            "other_view": other_view,
            "other_selection": other_selection,
            "other_mutation": other_mutation,
            "actor_after_view": actor_after_view,
            "actor_after_selection": actor_after_selection,
            "actor_after_mutation": actor_after_mutation,
            "peer_file_unchanged": peer_before == alpha_peer_path.read_bytes(),
            "peer_hp_unchanged": (
                peer_hp_before
                == application.PARTY_LIBRARY["ALPHA PEER"].current_hp
            ),
            "other_builder_view": other_builder_view,
            "other_builder_mutation": other_builder_mutation,
            "peer_cosmere_unchanged": (
                peer_cosmere_before == alpha_peer_cosmere_path.read_bytes()
            ),
        })
        '''
    )
    observed = _result(completed)

    assert observed["own_selection"]["status"] == 200, observed["own_selection"]
    assert observed["own_view"]["status"] == 200, observed["own_view"]
    assert observed["forged_mobile"]["status"] == 403, observed
    _assert_json_denial(observed["forged_nomination"], 403)
    assert observed["owned_nomination"]["status"] == 200, observed
    assert observed["captured_nominations"][-1]["nominator"] == "ALPHA OWNER", observed

    assert observed["other_view"]["status"] == 403, observed["other_view"]
    assert observed["actor_after_view"] == "ALPHA OWNER", observed

    _assert_json_denial(observed["other_selection"], 403)
    assert observed["actor_after_selection"] == "ALPHA OWNER", observed

    _assert_json_denial(observed["other_mutation"], 403)
    assert observed["actor_after_mutation"] == "ALPHA OWNER", observed
    assert observed["peer_file_unchanged"] is True, observed
    assert observed["peer_hp_unchanged"] is True, observed
    assert observed["other_builder_view"]["status"] == 403, observed
    assert observed["other_builder_mutation"]["status"] == 403, observed
    assert observed["peer_cosmere_unchanged"] is True, observed


def test_cosmere_live_combat_uses_stable_id_when_owned_and_peer_names_collide(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        shared_name = "TWIN COSMERE HERO"
        own_pid, own_path = _seed_cosmere_character(
            campaign_a["id"], shared_name, player_a["id"]
        )
        peer_pid, peer_path = _seed_cosmere_character(
            campaign_a["id"], shared_name, peer_a["id"]
        )

        _activate(gm_a_client, campaign_a["id"])
        _activate(player_a_client, campaign_a["id"])

        own_combatant = application._cosmere_combatant(own_pid)
        peer_combatant = application._cosmere_combatant(peer_pid)
        assert own_combatant is not None and peer_combatant is not None
        assert own_combatant.name == peer_combatant.name == shared_name
        own_combatant.instance_id = "owned-twin"
        peer_combatant.instance_id = "peer-twin"
        own_combatant.speed_choice = "slow"
        peer_combatant.speed_choice = "slow"
        own_combatant.max_actions = 3
        peer_combatant.max_actions = 3
        own_combatant.current_hp = 30
        peer_combatant.current_hp = 30
        application.ACTIVE_ENCOUNTER[:] = [own_combatant, peer_combatant]
        application.TURN_INDEX = 0

        # Keep this regression focused on request identity and in-memory combat
        # selection; durable encounter persistence has its own race tests.
        application._persist_encounter_state = lambda *args, **kwargs: None
        application._broadcast_encounter_state = lambda *args, **kwargs: None

        own_speed = _probe(
            player_a_client,
            "/api/cosmere/my_speed",
            method="POST",
            json={"pid": own_pid, "choice": "fast"},
        )
        after_own_speed = {
            combatant.restore_id: {
                "speed_choice": combatant.speed_choice,
                "max_actions": combatant.max_actions,
            }
            for combatant in application.ACTIVE_ENCOUNTER
        }

        peer_speed = _probe(
            player_a_client,
            "/api/cosmere/my_speed",
            method="POST",
            json={"pid": peer_pid, "choice": "fast"},
        )
        after_peer_speed = {
            combatant.restore_id: {
                "speed_choice": combatant.speed_choice,
                "max_actions": combatant.max_actions,
            }
            for combatant in application.ACTIVE_ENCOUNTER
        }

        peer_file_before = peer_path.read_bytes()
        own_state = _probe(
            player_a_client,
            "/cosmere/pc/" + quote(own_pid, safe="") + "/state",
            method="POST",
            json={"health": 11},
        )
        after_own_state = {
            combatant.restore_id: combatant.current_hp
            for combatant in application.ACTIVE_ENCOUNTER
        }
        peer_state = _probe(
            player_a_client,
            "/cosmere/pc/" + quote(peer_pid, safe="") + "/state",
            method="POST",
            json={"health": 3},
        )
        after_peer_state = {
            combatant.restore_id: combatant.current_hp
            for combatant in application.ACTIVE_ENCOUNTER
        }

        _emit({
            "own_pid": own_pid,
            "peer_pid": peer_pid,
            "own_speed": own_speed,
            "peer_speed": peer_speed,
            "after_own_speed": after_own_speed,
            "after_peer_speed": after_peer_speed,
            "own_state": own_state,
            "peer_state": peer_state,
            "after_own_state": after_own_state,
            "after_peer_state": after_peer_state,
            "own_saved_health": (
                json.loads(own_path.read_text(encoding="utf-8"))
                .get("play_state", {})
                .get("health")
            ),
            "peer_file_unchanged": peer_file_before == peer_path.read_bytes(),
        })
        '''
    )
    observed = _result(completed)
    own_pid = observed["own_pid"]
    peer_pid = observed["peer_pid"]

    assert observed["own_speed"]["status"] == 200, observed
    assert observed["after_own_speed"][own_pid] == {
        "speed_choice": "fast",
        "max_actions": 2,
    }
    assert observed["after_own_speed"][peer_pid] == {
        "speed_choice": "slow",
        "max_actions": 3,
    }
    _assert_json_denial(observed["peer_speed"], 403)
    assert observed["after_peer_speed"] == observed["after_own_speed"], observed

    assert observed["own_state"]["status"] == 200, observed
    assert observed["after_own_state"] == {own_pid: 11, peer_pid: 30}, observed
    _assert_json_denial(observed["peer_state"], 403)
    assert observed["after_peer_state"] == observed["after_own_state"], observed
    assert observed["own_saved_health"] == 11, observed
    assert observed["peer_file_unchanged"] is True, observed


def test_forged_session_character_cannot_impersonate_chat_or_plot_die_actor(
    isolated_app_process,
):
    completed = isolated_app_process(
        '''
        _activate(gm_a_client, campaign_a["id"])
        _activate(player_a_client, campaign_a["id"])
        with player_a_client.session_transaction() as session:
            session["player_name"] = "ALPHA PEER"

        captured_logs = []
        real_combat_log = application._combat_log
        application._combat_log = (
            lambda message, *args, **kwargs: captured_logs.append(str(message))
        )
        try:
            chat = _probe(
                player_a_client,
                "/api/chat",
                method="POST",
                json={"text": "Forged attribution probe"},
            )
            plot = _probe(
                player_a_client,
                "/api/plot_die",
                method="POST",
                json={},
            )
        finally:
            application._combat_log = real_combat_log

        _emit({
            "chat": chat,
            "plot": plot,
            "captured_logs": captured_logs,
            "stored_chat": list(application.CHAT_MESSAGES),
        })
        '''
    )
    observed = _result(completed)

    assert observed["chat"]["status"] == 200, observed
    assert observed["chat"]["json"]["message"]["sender"] == "Player Alpha", observed
    assert observed["stored_chat"][-1]["sender"] == "Player Alpha", observed
    assert observed["plot"]["status"] == 200, observed
    assert observed["captured_logs"], observed
    assert observed["captured_logs"][-1].startswith(
        "Player Alpha rolled the Plot Die"
    ), observed
    assert all("ALPHA PEER" not in entry for entry in observed["captured_logs"]), observed
