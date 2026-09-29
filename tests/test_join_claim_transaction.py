"""Transactional character-claim regressions for ``POST /join``.

Each scenario imports the application in a subprocess with its own ``DATA_DIR``.
That keeps the process-wide live campaign and party library isolated while the
tests exercise the real HTTP route, durable invite store, campaign membership,
and PF2e character envelope together.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


_REPO = Path(__file__).resolve().parent.parent
_RESULT_PREFIX = "JOIN_CLAIM_RESULT="


_SCENARIO_FIXTURE = r'''
import copy
import json
import os
from pathlib import Path

import app as application
from core import auth, campaigns, storage

application.app.config.update(TESTING=True)


def _new_user(username, display_name):
    return auth.create_user(
        username,
        "password-123",
        display_name=display_name,
        is_admin=False,
    )


gm = _new_user("join-gm", "Join GM")
outsider = _new_user("join-outsider", "Join Outsider")
outsider_two = _new_user("join-outsider-two", "Join Outsider Two")
owner = _new_user("join-owner", "Existing Owner")


def _campaign(name):
    return campaigns.create_campaign(name, "pf2e", gm["id"])


def _seed_character(campaign_id, name, owner_user_id=None):
    source = json.loads(
        Path("tests/fixtures/kyle_l10.json").read_text(encoding="utf-8")
    )
    source = copy.deepcopy(source)
    source["build"]["name"] = name
    character_id = storage.new_id()
    document = storage.wrap_character(
        character_id,
        campaign_id,
        "pf2e",
        source,
        owner_user_id=owner_user_id,
    )
    path = Path(storage.party_dir(campaign_id)) / (character_id + ".json")
    storage.atomic_write_json(str(path), document, indent=2)
    return character_id, path


def _signed_in_client(user):
    client = application.app.test_client()
    response = client.post(
        "/login",
        data={"username": user["username"], "password": "password-123"},
    )
    assert response.status_code == 302, response.status_code
    return client


def _activate(campaign_id):
    client = _signed_in_client(gm)
    response = client.post(f"/campaign/{campaign_id}/activate")
    assert response.status_code == 302, response.status_code


def _prepared_interruption(character_path):
    """Leave a realistic prepared journal after one staged file went live."""
    character_path = Path(character_path).resolve()
    original = storage.load_json(str(character_path))
    partial = copy.deepcopy(original)
    partial["owner_user_id"] = "partial-generation-must-be-rolled-back"
    partial["build"]["notes"] = "partial interrupted generation"

    stage = character_path.parent / ".join-claim.batch-stage"
    backup = character_path.parent / ".join-claim.batch-backup"
    journal = character_path.parent / ".join-claim.character-batch-journal"
    application._atomic_write_json(str(stage), partial, indent=2)
    os.link(str(character_path), str(backup))
    entry = {
        "name": original["build"]["name"],
        "path": str(character_path),
        "stage": str(stage.resolve()),
        "backup": str(backup.resolve()),
    }
    application._write_character_batch_journal(
        str(journal),
        "prepared",
        [entry],
    )
    os.replace(str(stage), str(character_path))
    return journal


def _invite_uses(code):
    return auth._load_invites()["invites"][code]["uses_left"]


def _emit(value):
    print("JOIN_CLAIM_RESULT=" + json.dumps(value, sort_keys=True))
'''


@pytest.fixture
def isolated_join_process(tmp_path):
    counter = 0

    def run(body: str) -> subprocess.CompletedProcess[str]:
        nonlocal counter
        counter += 1
        data_dir = tmp_path / f"join-data-{counter}"
        data_dir.mkdir()
        environment = dict(os.environ)
        environment.update(
            DATA_DIR=str(data_dir),
            GM_PASSWORD="",
            SETUP_TOKEN="",
            SECRET_KEY="join-claim-test-only",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            PYTHONUTF8="1",
        )
        script = (
            "import os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            + textwrap.dedent(_SCENARIO_FIXTURE)
            + "\n"
            + textwrap.dedent(body)
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=_REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

    return run


def _result(completed: subprocess.CompletedProcess[str]):
    assert completed.returncode == 0, (
        "isolated join scenario failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line[len(_RESULT_PREFIX):])
    pytest.fail(
        "isolated join scenario emitted no result marker\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def test_prepared_journal_aborts_join_before_claim_membership_or_invite_spend(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        campaign = _campaign("Interrupted Join")
        character_id, character_path = _seed_character(
            campaign["id"],
            "Interrupted Hero",
        )
        _activate(campaign["id"])
        code = auth.create_invite(
            campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )
        journal = _prepared_interruption(character_path)
        client = _signed_in_client(outsider)

        interrupted = client.post("/join", data={"code": code})
        recovered = storage.load_json(str(character_path))
        after_interruption = {
            "status": interrupted.status_code,
            "recovery_error": (
                "previous interrupted character operation was recovered"
                in interrupted.get_data(as_text=True).lower()
            ),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(campaign["id"]), outsider["id"]
            ),
            "owner": recovered.get("owner_user_id"),
            "journal_exists": journal.exists(),
            "notes": recovered["build"].get("notes"),
        }

        retry = client.post("/join", data={"code": code})
        claimed = storage.load_json(str(character_path))
        after_retry = {
            "status": retry.status_code,
            "location": retry.headers.get("Location", ""),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(campaign["id"]), outsider["id"]
            ),
            "owner": claimed.get("owner_user_id"),
            "expected_owner": outsider["id"],
        }
        _emit({"interrupted": after_interruption, "retry": after_retry})
    '''))

    assert result["interrupted"] == {
        "status": 503,
        "recovery_error": True,
        "uses": 1,
        "role": None,
        "owner": None,
        "journal_exists": False,
        "notes": None,
    }
    assert result["retry"]["status"] == 302
    assert result["retry"]["location"] == "/me"
    assert result["retry"]["uses"] == 0
    assert result["retry"]["role"] == "player"
    assert result["retry"]["owner"] == result["retry"]["expected_owner"]


def test_already_owned_character_conflict_is_side_effect_free(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        campaign = _campaign("Owned Character")
        character_id, character_path = _seed_character(
            campaign["id"],
            "Owned Hero",
            owner["id"],
        )
        _activate(campaign["id"])
        code = auth.create_invite(
            campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )

        response = _signed_in_client(outsider).post(
            "/join",
            data={"code": code},
        )
        document = storage.load_json(str(character_path))
        _emit({
            "status": response.status_code,
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(campaign["id"]), outsider["id"]
            ),
            "owner": document.get("owner_user_id"),
            "expected_owner": owner["id"],
        })
    '''))

    assert result["status"] == 409
    assert result["uses"] == 1
    assert result["role"] is None
    assert result["owner"] == result["expected_owner"]


def test_character_invite_claim_failure_keeps_invite_retryable(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        campaign = _campaign("Retryable Character Claim")
        character_id, character_path = _seed_character(
            campaign["id"],
            "Retryable Hero",
        )
        _activate(campaign["id"])
        code = auth.create_invite(
            campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )
        client = _signed_in_client(outsider)
        real_claim = application._claim_by_id
        claim_calls = []

        def fail_claim(*_args, **_kwargs):
            claim_calls.append(True)
            raise OSError("simulated character write failure")

        application._claim_by_id = fail_claim
        try:
            failed_response = client.post("/join", data={"code": code})
        finally:
            application._claim_by_id = real_claim

        after_failure_doc = storage.load_json(str(character_path))
        after_failure_campaign = campaigns.get_campaign(campaign["id"])
        after_failure = {
            "status": failed_response.status_code,
            "claim_calls": len(claim_calls),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(after_failure_campaign, outsider["id"]),
            "owner": after_failure_doc.get("owner_user_id"),
        }

        retry = client.post("/join", data={"code": code})
        claimed = storage.load_json(str(character_path))
        retried_campaign = campaigns.get_campaign(campaign["id"])
        after_retry = {
            "status": retry.status_code,
            "location": retry.headers.get("Location", ""),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(retried_campaign, outsider["id"]),
            "owner": claimed.get("owner_user_id"),
            "expected_owner": outsider["id"],
        }
        _emit({"failure": after_failure, "retry": after_retry})
    '''))

    assert result["failure"] == {
        "status": 500,
        "claim_calls": 1,
        "uses": 1,
        "role": "player",
        "owner": None,
    }
    assert result["retry"]["status"] == 302
    assert result["retry"]["location"] == "/me"
    assert result["retry"]["uses"] == 0
    assert result["retry"]["role"] == "player"
    assert result["retry"]["owner"] == result["retry"]["expected_owner"]


def test_generic_invite_membership_failure_spends_invite_and_rejects_retry(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        campaign = _campaign("Spent Generic Invite")
        code = auth.create_invite(
            campaign["id"],
            "player",
            created_by=gm["id"],
            uses=1,
        )
        client = _signed_in_client(outsider)
        real_add_member = campaigns.add_member
        add_member_calls = []

        def fail_add_member(*_args, **_kwargs):
            add_member_calls.append(True)
            raise OSError("simulated campaign membership write failure")

        campaigns.add_member = fail_add_member
        try:
            failed_response = client.post("/join", data={"code": code})
        finally:
            campaigns.add_member = real_add_member

        after_failure = {
            "status": failed_response.status_code,
            "add_member_calls": len(add_member_calls),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(campaign["id"]), outsider["id"]
            ),
        }
        retry = client.post("/join", data={"code": code})
        after_retry = {
            "status": retry.status_code,
            "invalid": "invalid or expired invite code"
            in retry.get_data(as_text=True).lower(),
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(campaign["id"]), outsider["id"]
            ),
        }
        _emit({"failure": after_failure, "retry": after_retry})
    '''))

    assert result == {
        "failure": {
            "status": 500,
            "add_member_calls": 1,
            "uses": 0,
            "role": None,
        },
        "retry": {
            "status": 400,
            "invalid": True,
            "uses": 0,
            "role": None,
        },
    }


def test_distinct_generic_invites_preserve_both_concurrent_memberships(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        import threading
        from concurrent.futures import ThreadPoolExecutor

        campaign = _campaign("Concurrent Distinct Invites")
        first_code = auth.create_invite(
            campaign["id"],
            "player",
            created_by=gm["id"],
            uses=1,
        )
        second_code = auth.create_invite(
            campaign["id"],
            "player",
            created_by=gm["id"],
            uses=1,
        )
        first_client = _signed_in_client(outsider)
        second_client = _signed_in_client(outsider_two)

        # Both requests load their request-local campaign memo before either can
        # enter the commit section. This deterministically recreates the stale
        # read that used to let the second whole-document save erase the first.
        real_get_campaign = campaigns.get_campaign
        preload_barrier = threading.Barrier(2)
        preload_lock = threading.Lock()
        preload_count = [0]

        def coordinated_get_campaign(cid, *args, **kwargs):
            document = real_get_campaign(cid, *args, **kwargs)
            should_wait = False
            if not kwargs.get("refresh", False):
                with preload_lock:
                    if preload_count[0] < 2:
                        preload_count[0] += 1
                        should_wait = True
            if should_wait:
                preload_barrier.wait(timeout=10)
            return document

        campaigns.get_campaign = coordinated_get_campaign

        def redeem(client, code):
            response = client.post("/join", data={"code": code})
            return response.status_code

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(redeem, first_client, first_code)
                second = pool.submit(redeem, second_client, second_code)
                statuses = sorted((first.result(timeout=30), second.result(timeout=30)))
        finally:
            campaigns.get_campaign = real_get_campaign

        refreshed = campaigns.get_campaign(campaign["id"], refresh=True)
        _emit({
            "statuses": statuses,
            "preload_count": preload_count[0],
            "first_uses": _invite_uses(first_code),
            "second_uses": _invite_uses(second_code),
            "first_role": campaigns.user_role(refreshed, outsider["id"]),
            "second_role": campaigns.user_role(refreshed, outsider_two["id"]),
        })
    '''))

    assert result == {
        "statuses": [302, 302],
        "preload_count": 2,
        "first_uses": 0,
        "second_uses": 0,
        "first_role": "player",
        "second_role": "player",
    }


def test_one_use_invite_allows_exactly_one_concurrent_claim(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        import threading
        from concurrent.futures import ThreadPoolExecutor

        campaign = _campaign("Concurrent Join")
        character_id, character_path = _seed_character(
            campaign["id"],
            "One Claim Hero",
        )
        _activate(campaign["id"])
        code = auth.create_invite(
            campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )
        first_client = _signed_in_client(outsider)
        second_client = _signed_in_client(outsider_two)
        barrier = threading.Barrier(2)

        def claim(client):
            barrier.wait(timeout=10)
            response = client.post("/join", data={"code": code})
            return response.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(claim, first_client)
            second = pool.submit(claim, second_client)
            statuses = sorted((first.result(timeout=30), second.result(timeout=30)))

        document = storage.load_json(str(character_path))
        refreshed = campaigns.get_campaign(campaign["id"])
        roles = {
            outsider["id"]: campaigns.user_role(refreshed, outsider["id"]),
            outsider_two["id"]: campaigns.user_role(
                refreshed, outsider_two["id"]
            ),
        }
        winners = [user_id for user_id, role in roles.items() if role == "player"]
        _emit({
            "statuses": statuses,
            "uses": _invite_uses(code),
            "winners": winners,
            "owner": document.get("owner_user_id"),
        })
    '''))

    assert result["statuses"] == [302, 400]
    assert result["uses"] == 0
    assert len(result["winners"]) == 1
    assert result["owner"] == result["winners"][0]


def test_non_live_join_recovery_does_not_publish_foreign_party_actors(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        live_campaign = _campaign("Live Table")
        _seed_character(live_campaign["id"], "Live Sentinel", gm["id"])
        target_campaign = _campaign("Non-Live Join Target")
        character_id, character_path = _seed_character(
            target_campaign["id"],
            "Foreign Join Hero",
        )
        _activate(live_campaign["id"])
        before = {
            name: {
                "identity": id(actor),
                "path": str(Path(actor.file_path).resolve()),
            }
            for name, actor in application.PARTY_LIBRARY.items()
        }
        code = auth.create_invite(
            target_campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )
        _prepared_interruption(character_path)

        response = _signed_in_client(outsider).post(
            "/join",
            data={"code": code},
        )
        after = {
            name: {
                "identity": id(actor),
                "path": str(Path(actor.file_path).resolve()),
            }
            for name, actor in application.PARTY_LIBRARY.items()
        }
        recovered = storage.load_json(str(character_path))
        _emit({
            "status": response.status_code,
            "party_unchanged": after == before,
            "party_names": sorted(after),
            "foreign_loaded": "Foreign Join Hero" in after,
            "uses": _invite_uses(code),
            "role": campaigns.user_role(
                campaigns.get_campaign(target_campaign["id"]), outsider["id"]
            ),
            "owner": recovered.get("owner_user_id"),
        })
    '''))

    assert result == {
        "status": 503,
        "party_unchanged": True,
        "party_names": ["Live Sentinel"],
        "foreign_loaded": False,
        "uses": 1,
        "role": None,
        "owner": None,
    }


def test_player_invites_never_demote_existing_campaign_gms(
    isolated_join_process,
):
    result = _result(isolated_join_process(r'''
        # A sole GM testing a generic player invite must remain the GM.
        generic_campaign = _campaign("Generic Role Lattice")
        generic_code = auth.create_invite(
            generic_campaign["id"],
            "player",
            created_by=gm["id"],
            uses=1,
        )
        generic_response = _signed_in_client(gm).post(
            "/join",
            data={"code": generic_code},
        )
        generic_doc = campaigns.get_campaign(generic_campaign["id"])

        # A co-GM may still claim the character carried by a player invite, but
        # their campaign role remains GM while the character becomes theirs.
        character_campaign = _campaign("Character Role Lattice")
        campaigns.add_member(character_campaign["id"], outsider["id"], "gm")
        character_id, character_path = _seed_character(
            character_campaign["id"],
            "Co-GM Hero",
        )
        character_code = auth.create_invite(
            character_campaign["id"],
            "player",
            character_id=character_id,
            created_by=gm["id"],
            uses=1,
        )
        character_response = _signed_in_client(outsider).post(
            "/join",
            data={"code": character_code},
        )
        character_doc = campaigns.get_campaign(character_campaign["id"])
        claimed = storage.load_json(str(character_path))

        _emit({
            "generic": {
                "status": generic_response.status_code,
                "uses": _invite_uses(generic_code),
                "role": campaigns.user_role(generic_doc, gm["id"]),
                "gm_count": campaigns.gm_count(generic_doc),
            },
            "character": {
                "status": character_response.status_code,
                "uses": _invite_uses(character_code),
                "role": campaigns.user_role(character_doc, outsider["id"]),
                "gm_count": campaigns.gm_count(character_doc),
                "member_character_id": next(
                    member.get("character_id")
                    for member in character_doc["members"]
                    if member.get("user_id") == outsider["id"]
                ),
                "owner": claimed.get("owner_user_id"),
                "expected_owner": outsider["id"],
                "expected_character_id": character_id,
            },
        })
    '''))

    assert result["generic"] == {
        "status": 302,
        "uses": 0,
        "role": "gm",
        "gm_count": 1,
    }
    assert result["character"]["status"] == 302
    assert result["character"]["uses"] == 0
    assert result["character"]["role"] == "gm"
    assert result["character"]["gm_count"] == 2
    assert result["character"]["member_character_id"] == result["character"][
        "expected_character_id"
    ]
    assert result["character"]["owner"] == result["character"][
        "expected_owner"
    ]
