"""Revisioned Cosmere action API and durable tracker coordination."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import pytest

from systems.cosmere.effects import condition_effect, project_conditions
from systems.cosmere.origins import path_key_talent


def build():
    return {
        "name": "Action Hero", "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1, "prc": 1, "sur": 1},
        "talents": [path_key_talent("warrior"), {"id": "test.creation", "name": "Bonus"}],
        "expertises": [], "epic_choices": [],
    }


@pytest.fixture
def action_env(tmp_path, monkeypatch):
    import app as host
    directory = tmp_path / "cosmere_pcs"
    directory.mkdir()
    encounter_directory = tmp_path / "encounters"
    encounter_directory.mkdir()
    monkeypatch.setattr(host, "COSMERE_PC_DIR", str(directory))
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_directory))
    monkeypatch.setattr(host._storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    monkeypatch.setattr(host, "ACTIVE_CAMPAIGN_ID", None)
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": pid, "system": "cosmere", "name": "Action Hero", "build": build(),
        "runtime_state": {
            "health": 12, "focus": 3, "investiture": 0,
            "turn": {"actions_remaining": 3, "reaction_available": True,
                     "named_actions_used": [], "turn_number": 1},
            "scene": {"id": "scene.one", "uses": {}},
        },
    })
    yield host, host.app.test_client(), pid
    host.ACTIVE_ENCOUNTER[:] = []


def test_action_route_orders_save_sync_autosave_then_sse(action_env, monkeypatch):
    host, client, pid = action_env
    order = []
    real_save = host._save_cosmere_pc

    def save(*args, **kwargs):
        result = real_save(*args, **kwargs)
        order.append("save")
        return result

    monkeypatch.setattr(host, "_save_cosmere_pc", save)
    monkeypatch.setattr(host, "_sync_cosmere_combatant_state",
                        lambda actor_id, state, character_revision=None: order.append("sync") or True)
    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked",
                        lambda: order.append("autosave") or True)
    monkeypatch.setattr(host, "sse_broadcast", lambda event, *a, **k: order.append(event))

    response = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": 0, "request_id": "request.api.brace",
    })
    assert response.status_code == 200, response.get_json()
    assert order == [
        "save", "sync", "autosave", "encounter_update",
        "cosmere_player_state",
    ]
    body = response.get_json()
    assert body["revision"] == 1 and body["receipt"]["action_id"] == "brace"
    assert host._load_cosmere_pc(pid)["runtime_state"]["turn"]["actions_remaining"] == 2


def test_action_route_rejects_stale_and_replays_exact_request_without_mutation(action_env):
    host, client, pid = action_env
    payload = {"expected_revision": 0, "request_id": "request.api.once"}
    first = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert first.status_code == 200
    replay = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert replay.status_code == 200 and replay.get_json()["replayed"] is True
    assert host._load_cosmere_pc(pid)["revision"] == 1
    stale = client.post(f"/api/cosmere/pc/{pid}/actions/disengage", json={
        "expected_revision": 0, "request_id": "request.api.stale"})
    assert stale.status_code == 409 and stale.get_json()["code"] == "stale_revision"


def test_action_effects_and_receipts_survive_restart_normalization(action_env):
    host, client, pid = action_env
    document = host._load_cosmere_pc(pid)
    document["runtime_state"].pop("effects", None)  # historical boolean-only payload
    document["runtime_state"]["conditions"] = {"afflicted": 2}
    document["play_state"] = deepcopy(document["runtime_state"])
    host._save_cosmere_pc(document)
    reloaded = host._load_cosmere_pc(pid)
    assert len(reloaded["runtime_state"]["effects"]) == 1
    response = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": reloaded["revision"], "request_id": "request.api.restart"})
    assert response.status_code == 200
    stored = host._load_cosmere_pc(pid)["runtime_state"]
    assert stored["conditions"] == {"afflicted": 2}
    assert stored["action_receipts"][-1]["request_id"] == "request.api.restart"


def test_tracker_rejects_stale_character_overlay(action_env):
    host, _client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.character_revision = 3
    actor.current_hp = 10
    host.ACTIVE_ENCOUNTER[:] = [actor]
    assert host._sync_cosmere_combatant_state(
        pid, {"health": 1}, character_revision=2) is False
    assert actor.current_hp == 10 and actor.character_revision == 3
    assert host._sync_cosmere_combatant_state(
        pid, {"health": 7}, character_revision=4) is True
    assert actor.current_hp == 7 and actor.character_revision == 4


def test_legacy_state_save_cannot_reopen_consumed_goal_reward(action_env):
    host, client, pid = action_env
    document = host._load_cosmere_pc(pid)
    document["runtime_state"]["goals"] = [{
        "id": "goal.one", "text": "Protect the crew", "milestones": [True, True, True],
        "completed": True, "rewards": {"talent": 1},
        "consumed_by": "event.advance.used",
    }]
    document["play_state"] = deepcopy(document["runtime_state"])
    host._save_cosmere_pc(document)

    saved = client.post(f"/cosmere/pc/{pid}/state", json={"goals": [{
        "id": "goal.one", "text": "Protect the crew", "milestones": [True, True, True],
        "concluded": False, "reward": {"category": "talent", "text": "again"},
    }]})
    assert saved.status_code == 200
    goal = host._load_cosmere_pc(pid)["runtime_state"]["goals"][0]
    assert goal["completed"] is True
    assert goal["rewards"] == {"talent": 1}
    assert goal["consumed_by"] == "event.advance.used"

    current = host._load_cosmere_pc(pid)
    candidate = deepcopy(current["build"])
    candidate["level"] = 2
    candidate["skills"]["agi"] += 1
    candidate["skills"]["hwp"] += 1
    candidate["talents"].extend([
        {"id": "test.level-two", "name": "Level Two"},
        {"id": "test.reused-goal", "name": "Reused Goal"},
    ])
    preview = client.post("/api/cosmere/advancement/preview", json={
        "pid": pid, "expected_revision": current["revision"], "target_level": 2,
        "build": candidate, "goal_ids": ["goal.one"],
        "request_id": "request.goal.reuse",
    })
    assert preview.status_code == 422
    assert preview.get_json()["code"] == "goal_unavailable"


def test_live_turn_serial_resets_named_actions_and_synchronizes_pips(
        action_env, monkeypatch):
    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.action.hero"
    actor.turn_serial = 1
    actor.max_actions = 3
    host.ACTIVE_ENCOUNTER[:] = [actor]
    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked", lambda: True)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)

    first = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": 0, "request_id": "request.turn.one",
    })
    assert first.status_code == 200, first.get_json()
    assert actor.actions_used == 1

    actor.turn_serial = 2
    actor.max_actions = 3
    actor.actions_used = 0
    actor.reaction_used = False
    second = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": 1, "request_id": "request.turn.two",
    })
    assert second.status_code == 200, second.get_json()
    turn = host._load_cosmere_pc(pid)["runtime_state"]["turn"]
    assert turn == {
        "actions_remaining": 2,
        "reaction_available": True,
        "named_actions_used": ["brace"],
        "turn_number": 2,
        "choice": "slow",
    }
    assert actor.actions_used == 1 and actor.character_revision == 2


def test_stale_encounter_restart_projects_authoritative_turn_state(action_env):
    host, client, pid = action_env
    response = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": 0, "request_id": "request.restart.turn",
    })
    assert response.status_code == 200, response.get_json()

    restored = host._restore_cosmere_combatant({
        "system": "cosmere", "cosmere_id": pid,
        "instance_id": "combatant.restart.hero",
        "character_revision": 0,
        "current_hp": 1,
        "injuries": 4,
        "conditions": {"unconscious": True},
        "turn_serial": 0,
        "visible_to_players": False,
        "injury_log": [{"kind": "lasting", "note": "Encounter scar"}],
        "tactics": "Protect the eastern door.",
        "epithet": "The Survivor",
        "delaying": True,
        "defeated": True,
        "nonlethal_defeat": True,
    })

    assert restored.character_revision == 1
    assert restored.current_hp == 12
    assert restored.turn_serial == 1
    assert restored.max_actions == 3 and restored.actions_used == 1
    assert restored.reaction_used is False
    assert restored.injuries == 0 and restored.conditions == {}
    assert restored.visible_to_players is False
    assert restored.injury_log == [
        {"kind": "lasting", "note": "Encounter scar"}]
    assert restored.tactics == "Protect the eastern door."
    assert restored.epithet == "The Survivor"
    assert restored.delaying is True
    assert restored.defeated is True and restored.nonlethal_defeat is True


def test_action_request_reconciles_same_turn_legacy_tracker_action_spend(
        action_env, monkeypatch):
    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.same.turn.action"
    actor.turn_serial = 1
    actor.max_actions = 3
    actor.actions_used = 0
    host.ACTIVE_ENCOUNTER[:] = [actor]
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked", lambda: True)
    monkeypatch.setattr(host, "_do_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)

    legacy = client.post(f"/api/use_action/{actor.instance_id}", json={
        "slot": "action", "delta": 1,
    })
    assert legacy.status_code == 200 and actor.actions_used == 1

    response = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json={
        "expected_revision": 0, "request_id": "request.same-turn.action",
    })

    assert response.status_code == 200, response.get_json()
    turn = host._load_cosmere_pc(pid)["runtime_state"]["turn"]
    assert turn["actions_remaining"] == 1
    assert turn["named_actions_used"] == ["brace"]
    assert actor.actions_used == 2


def test_action_request_reconciles_same_turn_legacy_tracker_reaction_spend(
        action_env, monkeypatch):
    import systems.cosmere.actions as action_rules

    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.same.turn.reaction"
    actor.turn_serial = 1
    actor.max_actions = 3
    actor.reaction_used = False
    host.ACTIVE_ENCOUNTER[:] = [actor]
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    legacy = client.post(f"/api/use_action/{actor.instance_id}", json={
        "slot": "reaction", "delta": 1,
    })
    assert legacy.status_code == 200 and actor.reaction_used is True

    reaction = action_rules.action_by_id("brace")
    reaction["id"] = "test.same-turn-reaction"
    reaction["source_id"] = "action.test.same-turn-reaction"
    reaction["name"] = "Same Turn Reaction"
    reaction["activation"]["cost"] = {"type": "reaction", "value": 1}
    monkeypatch.setattr(action_rules, "action_by_id", lambda *args, **kwargs: reaction)

    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/{reaction['id']}",
        json={"expected_revision": 0,
              "request_id": "request.same-turn.reaction"})

    assert response.status_code == 409
    assert response.get_json()["code"] == "reaction_unavailable"
    assert actor.reaction_used is True


def test_pending_action_retry_finishes_durable_coordination_without_recharge(
        action_env, monkeypatch):
    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.pending.retry"
    actor.turn_serial = 1
    actor.max_actions = 3
    host.ACTIVE_ENCOUNTER[:] = [actor]
    persist_results = iter([False, True])
    persistence_calls = []
    events = []

    def persist():
        persistence_calls.append("autosave")
        return next(persist_results)

    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked", persist)
    monkeypatch.setattr(
        host, "sse_broadcast", lambda event, *args, **kwargs: events.append(event))

    payload = {"expected_revision": 0, "request_id": "request.pending.retry"}
    first = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert first.status_code == 503
    after_failure = host._load_cosmere_pc(pid)
    assert after_failure["revision"] == 1
    assert after_failure["runtime_state"]["turn"]["actions_remaining"] == 2
    assert after_failure["runtime_state"]["action_receipts"][-1][
        "coordination_status"] == "pending"
    assert events == []

    pending_conflict = client.post(
        f"/api/cosmere/pc/{pid}/actions/disengage", json=payload)
    assert pending_conflict.status_code == 409
    assert pending_conflict.get_json()["code"] == "request_id_conflict"
    assert persistence_calls == ["autosave"] and events == []

    retry = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert retry.status_code == 200, retry.get_json()
    assert retry.get_json()["replayed"] is True
    completed = host._load_cosmere_pc(pid)
    assert completed["revision"] == 1
    assert completed["runtime_state"]["turn"]["actions_remaining"] == 2
    assert completed["runtime_state"]["action_receipts"][-1][
        "coordination_status"] == "complete"
    assert persistence_calls == ["autosave", "autosave"]
    assert events == ["encounter_update", "cosmere_player_state"]

    settled = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert settled.status_code == 200 and settled.get_json()["replayed"] is True
    assert persistence_calls == ["autosave", "autosave"]
    assert events == ["encounter_update", "cosmere_player_state"]

    conflict = client.post(f"/api/cosmere/pc/{pid}/actions/disengage", json=payload)
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "request_id_conflict"


@pytest.mark.parametrize(
    ("live_turn_serial", "request_suffix"),
    [(1, "same"), (2, "new")],
)
def test_pending_action_retry_never_rewinds_live_tracker_spending(
        action_env, monkeypatch, live_turn_serial, request_suffix):
    """A coordination retry may project resources, but cannot refund pips.

    The serial=1 case is same-turn spending through the legacy tracker route.
    The serial=2 case also proves an old pending receipt cannot overwrite a
    newer turn's exhausted availability.
    """
    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = f"combatant.pending.pips.{live_turn_serial}"
    actor.turn_serial = 1
    actor.max_actions = 3
    host.ACTIVE_ENCOUNTER[:] = [actor]
    persistence_calls = []
    persist_results = iter([False, True])
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: persistence_calls.append("autosave") or next(persist_results))
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)

    payload = {
        "expected_revision": 0,
        "request_id": f"request.pending.pips-{request_suffix}",
    }
    first = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)
    assert first.status_code == 503
    assert actor.actions_used == 1 and actor.reaction_used is False

    if live_turn_serial == 2:
        actor.turn_serial = 2
        actor.actions_used = 0
        actor.reaction_used = False
        actor.speed_choice = "fast"
    for _ in range(3 - actor.actions_used):
        spent = client.post(f"/api/use_action/{actor.instance_id}", json={
            "slot": "action", "delta": 1,
        })
        assert spent.status_code == 200
    reacted = client.post(f"/api/use_action/{actor.instance_id}", json={
        "slot": "reaction", "delta": 1,
    })
    assert reacted.status_code == 200
    assert actor.actions_used == 3 and actor.reaction_used is True

    retry = client.post(f"/api/cosmere/pc/{pid}/actions/brace", json=payload)

    assert retry.status_code == 200, retry.get_json()
    assert retry.get_json()["replayed"] is True
    assert actor.actions_used == 3
    assert actor.reaction_used is True
    assert actor.turn_serial == live_turn_serial
    if live_turn_serial == 2:
        assert actor.speed_choice == "fast"
    stored = host._load_cosmere_pc(pid)
    assert stored["revision"] == 1
    assert stored["runtime_state"]["action_receipts"][-1][
        "coordination_status"] == "complete"
    assert persistence_calls == ["autosave", "autosave"]


def test_action_resource_cost_projects_focus_to_live_tracker(
        action_env, monkeypatch):
    import systems.cosmere.actions as action_rules

    host, client, pid = action_env
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.focus.cost"
    actor.turn_serial = 1
    actor.max_actions = 3
    assert actor.current_focus == 3
    host.ACTIVE_ENCOUNTER[:] = [actor]

    focus_action = action_rules.action_by_id("brace")
    focus_action["id"] = "test.focus-cost"
    focus_action["source_id"] = "action.test.focus-cost"
    focus_action["name"] = "Focus Cost"
    focus_action["activation"]["consume"] = [
        {"type": "resource", "resource": "focus", "amount": 1},
    ]
    monkeypatch.setattr(
        action_rules, "action_by_id", lambda *args, **kwargs: focus_action)
    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked", lambda: True)
    monkeypatch.setattr(host, "_do_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)

    response = client.post(
        f"/api/cosmere/pc/{pid}/actions/{focus_action['id']}",
        json={"expected_revision": 0, "request_id": "request.focus.cost"})

    assert response.status_code == 200, response.get_json()
    assert host._load_cosmere_pc(pid)["runtime_state"]["focus"] == 2
    assert actor.current_focus == 2


def test_clear_encounter_applies_scene_and_encounter_lifecycle_before_notifications(
        action_env, monkeypatch):
    import systems.cosmere.actions as action_rules

    host, client, pid = action_env
    recover = action_rules.action_by_id("recover")
    recover["activation"]["cost"] = {"type": "free", "value": 0}
    original_lookup = action_rules.action_by_id
    monkeypatch.setattr(
        action_rules, "action_by_id",
        lambda action_id, **kwargs: deepcopy(recover)
        if action_id == "recover" else original_lookup(action_id, **kwargs))
    first = client.post(f"/api/cosmere/pc/{pid}/actions/recover", json={
        "expected_revision": 0, "request_id": "request.recover.before-clear",
    })
    assert first.status_code == 200, first.get_json()

    scene_effect = condition_effect(
        pid, "empowered", instance_id="effect.api.scene-end",
        duration_unit="scene", remaining_duration=1, trigger="scene_end")
    encounter_effect = condition_effect(
        pid, "focused", instance_id="effect.api.encounter-end",
        duration_unit="encounter", remaining_duration=1,
        trigger="encounter_end")

    def add_lifecycle_state(document):
        state = document["runtime_state"]
        state["effects"] = [scene_effect, encounter_effect]
        state["conditions"] = project_conditions(state["effects"])
        state["encounter"] = {
            "id": "encounter.one", "uses": {"encounter-rally": 1}}
        document["runtime_state"] = state
        return document

    prepared = host._mutate_cosmere_pc(pid, 1, add_lifecycle_state)
    assert prepared["revision"] == 2
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.lifecycle.clear"
    host.ACTIVE_ENCOUNTER[:] = [actor]
    order = []
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: order.append("autosave") or True)
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: order.append(event))

    cleared = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert cleared.status_code == 200, cleared.get_json()
    stored = host._load_cosmere_pc(pid)
    assert stored["revision"] == 3
    assert stored["runtime_state"]["effects"] == []
    assert stored["runtime_state"]["conditions"] == {}
    assert stored["runtime_state"]["scene"]["uses"] == {}
    assert stored["runtime_state"]["encounter"]["uses"] == {}
    assert order[0] == "autosave"
    assert "encounter_update" in order[1:]
    assert "cosmere_player_state" in order[1:]

    restarted = host._cosmere_combatant(pid)
    restarted.instance_id = "combatant.lifecycle.restarted"
    restarted.turn_serial = 2
    restarted.max_actions = 3
    restarted.actions_used = 0
    restarted.reaction_used = False
    host.ACTIVE_ENCOUNTER[:] = [restarted]
    second = client.post(f"/api/cosmere/pc/{pid}/actions/recover", json={
        "expected_revision": 3, "request_id": "request.recover.after-clear",
    })
    assert second.status_code == 200, second.get_json()


def test_clear_encounter_emits_nothing_when_durable_autosave_fails(
        action_env, monkeypatch):
    host, client, pid = action_env
    lasting = condition_effect(
        pid, "empowered", instance_id="effect.scene.retry",
        duration_unit="scene", remaining_duration=2, trigger="scene_end")

    def add_lasting_scene_effect(document):
        document["runtime_state"]["effects"] = [lasting]
        document["runtime_state"]["conditions"] = {
            "empowered": True}
        return document

    host._mutate_cosmere_pc(pid, 0, add_lasting_scene_effect)
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.lifecycle.failure"
    host.ACTIVE_ENCOUNTER[:] = [actor]
    events = []
    persist_results = iter([False, True])
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: next(persist_results))
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: events.append(event))

    response = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})

    assert response.status_code == 503
    assert events == []
    pending = host._load_cosmere_pc(pid)
    assert pending["revision"] == 2
    assert pending["runtime_state"]["effects"][0]["remaining_duration"] == 1

    retry = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert retry.status_code == 200
    completed = host._load_cosmere_pc(pid)
    assert completed["revision"] == 2
    assert completed["runtime_state"]["effects"][0]["remaining_duration"] == 1


def test_cosmere_scene_activation_runs_scene_end_before_durable_notifications(
        action_env, monkeypatch):
    host, client, _pid = action_env
    order = []
    scene = {"id": "scene.two", "name": "Next Scene"}
    monkeypatch.setattr(host, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(host, "_active_campaign_id", lambda: "c" * 32)
    monkeypatch.setattr(
        host._scenes, "table_scene_id", lambda _cid: "scene.one")
    monkeypatch.setattr(
        host._scenes, "set_table_scene",
        lambda _cid, scene_id: order.append(("set_scene", scene_id)))
    monkeypatch.setattr(host._scenes, "load_scene", lambda _cid, _sid: scene)
    monkeypatch.setattr(
        host, "_transition_cosmere_lifecycle",
        lambda *triggers, **kwargs: order.append(
            ("lifecycle", triggers, kwargs.get("next_scope_id"))) or [])
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: order.append("autosave") or True)
    monkeypatch.setattr(
        host, "_broadcast_scene", lambda _scene: order.append("scene_update"))
    monkeypatch.setattr(
        host, "_do_broadcast_encounter_state",
        lambda: order.append("encounter_update"))
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: order.append(event))

    response = client.post("/api/scenes/scene.two/activate")

    assert response.status_code == 200, response.get_json()
    assert order == [
        ("lifecycle", ("scene_end", "scene_start"), "scene.two"),
        "autosave", ("set_scene", "scene.two"), "scene_update",
        "encounter_update", "scene_activated",
    ]


def test_scene_activation_publishes_identity_only_after_resumable_durability(
        action_env, monkeypatch, tmp_path):
    host, client, pid = action_env
    encounter_dir = tmp_path / "scene_encounters"
    encounter_dir.mkdir()
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_dir))
    lasting = condition_effect(
        pid, "empowered", instance_id="effect.scene.activation-retry",
        duration_unit="scene", remaining_duration=2, trigger="scene_end")

    def add_effect(document):
        document["runtime_state"]["effects"] = [lasting]
        document["runtime_state"]["conditions"] = {"empowered": True}
        return document

    host._mutate_cosmere_pc(pid, 0, add_effect)
    public_identity = {"scene_id": "scene.one"}
    scene = {"id": "scene.two", "name": "Next Scene"}
    calls = []
    events = []
    persistence_results = iter([False, False, True])
    monkeypatch.setattr(host, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(host, "_active_campaign_id", lambda: "c" * 32)
    monkeypatch.setattr(
        host._scenes, "table_scene_id",
        lambda _cid: public_identity["scene_id"])

    def publish_scene(_cid, scene_id):
        calls.append(("set_scene", scene_id))
        public_identity["scene_id"] = scene_id

    monkeypatch.setattr(host._scenes, "set_table_scene", publish_scene)
    monkeypatch.setattr(host._scenes, "load_scene", lambda _cid, _sid: scene)
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: calls.append("autosave") or next(persistence_results))
    monkeypatch.setattr(
        host, "_broadcast_scene",
        lambda _scene: events.append("scene_update"))
    monkeypatch.setattr(
        host, "_do_broadcast_encounter_state",
        lambda: events.append("encounter_update"))
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: events.append(event))

    first = client.post("/api/scenes/scene.two/activate")
    assert first.status_code == 503
    assert public_identity["scene_id"] == "scene.one"
    assert calls == ["autosave"] and events == []
    journal_path = Path(host._cosmere_lifecycle_journal_path("c" * 32))
    assert journal_path.exists()
    with open(journal_path, encoding="utf-8") as handle:
        pending = json.load(handle)
    assert pending["kind"] == "scene_activate"
    assert pending["source_scope_id"] == "scene.one"
    assert pending["target_scope_id"] == "scene.two"
    boundary_id = pending["boundary_id"]
    after_first = host._load_cosmere_pc(pid)
    assert after_first["revision"] == 2
    assert after_first["runtime_state"]["effects"][0][
        "remaining_duration"] == 1

    second = client.post("/api/scenes/scene.two/activate")
    assert second.status_code == 503
    assert public_identity["scene_id"] == "scene.one"
    assert calls == ["autosave", "autosave"] and events == []
    assert host._load_cosmere_pc(pid)["revision"] == 2
    with open(journal_path, encoding="utf-8") as handle:
        assert json.load(handle)["boundary_id"] == boundary_id

    third = client.post("/api/scenes/scene.two/activate")
    assert third.status_code == 200, third.get_json()
    assert public_identity["scene_id"] == "scene.two"
    assert calls == [
        "autosave", "autosave", "autosave", ("set_scene", "scene.two")]
    assert events.count("scene_update") == 1
    assert events.count("encounter_update") == 1
    assert events.count("cosmere_player_state") == 1
    assert events.count("scene_activated") == 1
    assert not journal_path.exists()
    completed = host._load_cosmere_pc(pid)
    assert completed["revision"] == 2
    assert completed["runtime_state"]["effects"][0][
        "remaining_duration"] == 1


def test_clear_boundary_survives_old_autosave_restore_without_double_tick(
        action_env, monkeypatch, tmp_path):
    host, client, pid = action_env
    encounter_dir = tmp_path / "restart_encounters"
    encounter_dir.mkdir()
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_dir))
    lasting = condition_effect(
        pid, "empowered", instance_id="effect.scene.restart-clear",
        duration_unit="scene", remaining_duration=2, trigger="scene_end")

    def add_effect(document):
        document["runtime_state"]["effects"] = [lasting]
        document["runtime_state"]["conditions"] = {"empowered": True}
        return document

    host._mutate_cosmere_pc(pid, 0, add_effect)
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.clear.restart"
    host.ACTIVE_ENCOUNTER[:] = [actor]
    real_persist = host._do_persist_encounter_state_unlocked
    assert real_persist() is True
    autosave_path = encounter_dir / "_autosave.json"
    assert autosave_path.exists()
    events = []
    monkeypatch.setattr(host, "sse_broadcast",
                        lambda event, *args, **kwargs: events.append(event))
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked", lambda: False)

    first = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert first.status_code == 503
    assert events == [] and autosave_path.exists()
    pending = host._load_cosmere_pc(pid)
    assert pending["revision"] == 2
    assert pending["runtime_state"]["effects"][0]["remaining_duration"] == 1
    journal_path = Path(host._cosmere_lifecycle_journal_path())
    assert journal_path.exists()
    with open(journal_path, encoding="utf-8") as handle:
        boundary_id = json.load(handle)["boundary_id"]

    # Simulate a process restart restoring the last successful encounter
    # autosave. The overlay is old, while the character mutation is durable.
    host._restore_encounter_autosave()
    assert len(host.ACTIVE_ENCOUNTER) == 1
    assert host.ACTIVE_ENCOUNTER[0].character_revision == 2
    monkeypatch.setattr(host, "_do_persist_encounter_state_unlocked", real_persist)

    retry = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert retry.status_code == 200, retry.get_json()
    completed = host._load_cosmere_pc(pid)
    assert completed["revision"] == 2
    assert completed["runtime_state"]["effects"][0]["remaining_duration"] == 1
    assert boundary_id in " ".join(
        completed["runtime_state"].get("lifecycle_boundaries") or [])
    assert not journal_path.exists() and not autosave_path.exists()
    assert events.count("encounter_update") == 1
    assert events.count("cosmere_player_state") == 1


def test_clear_boundary_retries_partial_character_save_only_for_unapplied_pc(
        action_env, monkeypatch, tmp_path):
    host, client, first_pid = action_env
    encounter_dir = tmp_path / "partial_encounters"
    encounter_dir.mkdir()
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(encounter_dir))
    second_pid = uuid.uuid4().hex
    host._save_cosmere_pc({
        "id": second_pid, "system": "cosmere", "name": "Second Hero",
        "build": {**build(), "name": "Second Hero"},
        "runtime_state": {
            "health": 12, "focus": 3, "investiture": 0,
            "turn": {"actions_remaining": 3, "reaction_available": True,
                     "named_actions_used": [], "turn_number": 1},
            "scene": {"id": "scene.one", "uses": {}},
        },
    })
    for pid in (first_pid, second_pid):
        effect = condition_effect(
            pid, "empowered", instance_id=f"effect.partial.e-{pid}",
            duration_unit="scene", remaining_duration=2,
            trigger="scene_end")

        def add_effect(document, row=effect):
            document["runtime_state"]["effects"] = [row]
            document["runtime_state"]["conditions"] = {"empowered": True}
            return document

        host._mutate_cosmere_pc(pid, 0, add_effect)
    actor = host._cosmere_combatant(first_pid)
    actor.instance_id = "combatant.clear.partial"
    host.ACTIVE_ENCOUNTER[:] = [actor]
    events = []
    persistence_calls = []
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked",
        lambda: persistence_calls.append("autosave") or True)
    monkeypatch.setattr(
        host, "sse_broadcast",
        lambda event, *args, **kwargs: events.append(event))
    real_mutate = host._mutate_cosmere_pc
    mutation_calls = []
    failed = {"done": False}

    def fail_second_mutation(pid, expected_revision, transform):
        mutation_calls.append(pid)
        if not failed["done"] and len(mutation_calls) == 2:
            failed["done"] = True
            raise OSError("simulated second character save failure")
        return real_mutate(pid, expected_revision, transform)

    monkeypatch.setattr(host, "_mutate_cosmere_pc", fail_second_mutation)
    first = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert first.status_code == 500
    assert events == [] and persistence_calls == []
    journal_path = Path(host._cosmere_lifecycle_journal_path())
    assert journal_path.exists()
    midway = [host._load_cosmere_pc(pid) for pid in (first_pid, second_pid)]
    assert sorted(document["revision"] for document in midway) == [1, 2]
    assert sorted(
        document["runtime_state"]["effects"][0]["remaining_duration"]
        for document in midway) == [1, 2]

    monkeypatch.setattr(host, "_mutate_cosmere_pc", real_mutate)
    retry = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert retry.status_code == 200, retry.get_json()
    completed = [host._load_cosmere_pc(pid) for pid in (first_pid, second_pid)]
    assert [document["revision"] for document in completed] == [2, 2]
    assert [document["runtime_state"]["effects"][0]["remaining_duration"]
            for document in completed] == [1, 1]
    assert persistence_calls == ["autosave"]
    assert events.count("encounter_update") == 1
    assert events.count("cosmere_player_state") == 2
    assert not journal_path.exists()


def _leave_pending_clear_journal(host, client, pid, monkeypatch):
    actor = host._cosmere_combatant(pid)
    actor.instance_id = f"combatant.private-journal.{uuid.uuid4().hex}"
    host.ACTIVE_ENCOUNTER[:] = [actor]
    monkeypatch.setattr(
        host, "_do_persist_encounter_state_unlocked", lambda: False)
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: None)
    response = client.post(
        "/api/clear_encounter",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert response.status_code == 503
    pending = host._load_cosmere_lifecycle_transition()
    assert pending is not None
    return pending


def test_pending_lifecycle_journal_is_absent_from_encounter_listings(
        action_env, monkeypatch):
    host, client, pid = action_env
    _leave_pending_clear_journal(host, client, pid, monkeypatch)
    captured = {}
    monkeypatch.setattr(
        host, "render_template",
        lambda template, **context: captured.update(context) or "rendered")

    tracker = client.get("/tracker")
    listed = client.get("/api/list_encounters")

    assert tracker.status_code == 200
    assert "_cosmere_lifecycle_transition" not in captured["saved_encounters"]
    assert all(row["name"] != "_cosmere_lifecycle_transition"
               for row in listed.get_json()["encounters"])


def test_load_encounter_cannot_address_pending_lifecycle_journal(
        action_env, monkeypatch):
    host, client, pid = action_env
    pending = _leave_pending_clear_journal(host, client, pid, monkeypatch)

    loaded = client.post("/api/load_encounter", data={
        "encounter_name": "_cosmere_lifecycle_transition",
    })

    assert loaded.status_code == 404
    assert host._load_cosmere_lifecycle_transition()[
        "boundary_id"] == pending["boundary_id"]


def test_save_encounter_cannot_overwrite_pending_lifecycle_journal(
        action_env, monkeypatch):
    host, client, pid = action_env
    pending = _leave_pending_clear_journal(host, client, pid, monkeypatch)
    actor = host._cosmere_combatant(pid)
    actor.instance_id = "combatant.private-journal.save"
    host.ACTIVE_ENCOUNTER[:] = [actor]

    saved = client.post("/api/save_encounter", data={
        "encounter_name": "_cosmere_lifecycle_transition",
    })

    assert saved.status_code == 302
    assert host._load_cosmere_lifecycle_transition()[
        "boundary_id"] == pending["boundary_id"]


def test_delete_encounter_cannot_remove_pending_lifecycle_journal(
        action_env, monkeypatch):
    host, client, pid = action_env
    pending = _leave_pending_clear_journal(host, client, pid, monkeypatch)

    deleted = client.post("/api/delete_encounter", data={
        "encounter_name": "_cosmere_lifecycle_transition",
    })

    assert deleted.status_code == 404
    assert host._load_cosmere_lifecycle_transition()[
        "boundary_id"] == pending["boundary_id"]
