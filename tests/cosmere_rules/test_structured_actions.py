"""Structured action ingestion and pure action transactions."""
from copy import deepcopy

import pytest

from systems.cosmere.actions import (
    ActionTransactionError,
    apply_lifecycle_transition,
    apply_action_transaction,
    normalize_action_item,
)
from systems.cosmere.effects import condition_effect
from systems.cosmere.actor import CosmereActor


def raw_action():
    return {
        "_id": "item-action-1", "type": "action", "name": "Crushing Test",
        "system": {
            "id": "crushing-test",
            "activation": {
                "type": "skill_test", "skill": "ath", "attribute": "str",
                "modifierFormula": "+ 2", "cost": {"type": "act", "value": 2},
                "consume": [{"type": "resource", "resource": "foc",
                             "value": {"min": 1, "max": 1, "actual": 0}}],
                "plotDie": True,
                "opportunity": "Push the target.",
                "complication": "Become Prone.",
                "uses": {"max": 1, "value": 1, "recharge": "per_scene", "type": "use"},
            },
            "damage": {"formula": "2d6 + @str", "type": "impact", "skill": "ath",
                       "attribute": "str", "grazeOverrideFormula": "1d6"},
            "target": {"type": "creature", "value": 1, "range": "reach"},
            "description": {"short": "Structured test."},
        },
    }


def test_action_normalization_preserves_mechanical_structure_and_stable_ids():
    action = normalize_action_item(raw_action(), actor_id="stormlight.actor.test")
    assert action["id"] == "crushing-test"
    assert action["source_id"] == "item-action-1" and action["item_id"] == "item-action-1"
    assert action["activation"]["cost"] == {"type": "action", "value": 2}
    assert action["activation"]["consume"][0]["resource"] == "focus"
    assert action["test"] == {"type": "skill_test", "skill": "ath",
                              "attribute": "str", "modifier": "+ 2"}
    assert action["damage"]["formula"] == "2d6 + @str"
    assert action["target"] == {"type": "creature", "value": 1, "range": "reach"}
    assert action["plot_die"] is True
    assert action["opportunity"] == "Push the target."
    assert action["complication"] == "Become Prone."
    assert action["use_limit"]["recharge"] == "per_scene"


def test_actor_keeps_structured_actions_and_weapons():
    weapon = {
        "_id": "weapon-1", "type": "weapon", "name": "Maul",
        "system": {"activation": {"cost": {"type": "act", "value": 1}},
                   "damage": {"formula": "2d6", "type": "impact", "skill": "hwp"},
                   "target": {"type": "creature", "value": 1, "range": "reach"}},
    }
    actor = CosmereActor({"_id": "actor-1", "name": "Rival", "type": "adversary",
                          "system": {"skills": {"hwp": {"rank": 2, "attribute": "str"}}},
                          "items": [raw_action(), weapon]})
    assert actor.actions[0]["source_id"] == "item-action-1"
    assert actor.actions[0]["activation"]["cost"]["value"] == 2
    assert actor.strikes[0]["item_id"] == "weapon-1"
    assert actor.strikes[0]["activation"]["cost"]["value"] == 1
    assert actor.strikes[0]["target"]["range"] == "reach"


def runtime():
    return {
        "focus": 3, "investiture": 2, "conditions": {}, "effects": [],
        "turn": {"actions_remaining": 3, "reaction_available": True,
                 "named_actions_used": [], "turn_number": 1},
        "scene": {"id": "scene.one", "uses": {}},
        "action_receipts": [],
    }


def test_action_transaction_pays_costs_tracks_named_use_and_returns_receipt():
    action = normalize_action_item(raw_action())
    changed, receipt = apply_action_transaction(runtime(), action, request_id="request.action.one")
    assert changed["focus"] == 2
    assert changed["turn"]["actions_remaining"] == 1
    assert changed["turn"]["named_actions_used"] == ["crushing-test"]
    assert receipt["request_id"] == "request.action.one"
    assert receipt["costs"] == {"actions": 2, "focus": 1}
    assert receipt["test"]["skill"] == "ath" and receipt["damage"]["type"] == "impact"
    assert receipt["plot_die"] is True
    with pytest.raises(ActionTransactionError, match="once per turn"):
        apply_action_transaction(changed, action, request_id="request.action.two")

    same_name = deepcopy(action)
    same_name["id"] = "different-source-id"
    with pytest.raises(ActionTransactionError, match="once per turn"):
        apply_action_transaction(changed, same_name, request_id="request.action.three")


@pytest.mark.parametrize("condition,message", [
    ("unconscious", "cannot take actions"),
    ("stunned", "enough actions"),
])
def test_severe_conditions_restrict_actions(condition, message):
    state = runtime()
    state["conditions"] = {condition: True}
    with pytest.raises(ActionTransactionError, match=message):
        apply_action_transaction(state, normalize_action_item(raw_action()),
                                 request_id="request.blocked")


def test_disoriented_blocks_reactions_and_recover_is_once_per_scene():
    reaction = raw_action()
    reaction["system"]["activation"]["cost"] = {"type": "rea", "value": None}
    state = runtime()
    state["conditions"] = {"disoriented": True}
    with pytest.raises(ActionTransactionError, match="reaction"):
        apply_action_transaction(state, normalize_action_item(reaction), request_id="request.react")

    recover = raw_action()
    recover["system"]["id"] = "recover"
    recover["name"] = "Recover"
    recover["system"]["activation"]["cost"] = {"type": "act", "value": 1}
    recover["system"]["activation"]["consume"] = []
    changed, _ = apply_action_transaction(runtime(), normalize_action_item(recover),
                                          request_id="request.recover.one")
    changed["turn"]["named_actions_used"] = []
    with pytest.raises(ActionTransactionError, match="once per scene"):
        apply_action_transaction(changed, normalize_action_item(recover),
                                 request_id="request.recover.two")


def test_lifecycle_transition_expires_effects_and_resets_scoped_uses():
    state = runtime()
    state["turn"]["named_actions_used"] = ["recover", "brace"]
    state["scene"] = {"id": "scene.one", "uses": {"recover": 1}}
    state["encounter"] = {"id": "encounter.one", "uses": {"rally": 1}}
    state["effects"] = [
        condition_effect(
            "ab" * 16, "empowered", instance_id="effect.scene.lifecycle",
            duration_unit="scene", remaining_duration=1,
            trigger="scene_end"),
        condition_effect(
            "ab" * 16, "focused", instance_id="effect.encounter.lifecycle",
            duration_unit="encounter", remaining_duration=1,
            trigger="encounter_end"),
    ]
    state["conditions"] = {"empowered": True, "focused": True}

    after_scene, expired = apply_lifecycle_transition(
        state, "scene_end", next_scope_id="scene.two")
    assert [row["id"] for row in expired] == ["effect.scene.lifecycle"]
    assert after_scene["scene"] == {"id": "scene.two", "uses": {}}
    assert after_scene["encounter"]["uses"] == {"rally": 1}
    assert after_scene["turn"]["named_actions_used"] == []
    assert after_scene["conditions"] == {"focused": True}

    after_encounter, expired = apply_lifecycle_transition(
        after_scene, "encounter_end", next_scope_id="encounter.two")
    assert [row["id"] for row in expired] == ["effect.encounter.lifecycle"]
    assert after_encounter["encounter"] == {
        "id": "encounter.two", "uses": {}}
    assert after_encounter["conditions"] == {}


def test_scene_start_and_actor_scoped_contribution_lifecycle_are_idempotent():
    actor_id = "ab" * 16
    state = runtime()
    state["scene"] = {"id": None, "uses": {"stale": 1}}
    effect = condition_effect(
        actor_id, "focused", instance_id="effect.contribution.lifecycle",
        duration_unit="contribution", remaining_duration=2,
        trigger="contribution_end", expiry_state={"actor_id": actor_id})
    state["effects"] = [effect]
    state["conditions"] = {"focused": True}
    state["mistborn"] = {
        "allomancy": {"active_burns": [effect]},
        "feruchemy": {"by_metal": {
            "iron": {"active_effects": [effect]},
        }},
    }

    started, expired = apply_lifecycle_transition(
        state, "scene_start", next_scope_id="scene.one",
        boundary_id="boundary.scene-start")
    assert expired == []
    assert started["scene"] == {"id": "scene.one", "uses": {}}

    other, expired = apply_lifecycle_transition(
        started, "contribution_end", actor_id="cd" * 16,
        boundary_id="boundary.other-contribution")
    assert expired == []
    assert other["effects"][0]["remaining_duration"] == 2

    advanced, expired = apply_lifecycle_transition(
        other, "contribution_end", actor_id=actor_id,
        boundary_id="boundary.hero-contribution")
    assert expired == []
    assert advanced["effects"][0]["remaining_duration"] == 1
    assert advanced["mistborn"]["allomancy"]["active_burns"][0][
        "remaining_duration"] == 1
    assert advanced["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "active_effects"][0]["remaining_duration"] == 1

    replayed, expired = apply_lifecycle_transition(
        advanced, "contribution_end", actor_id=actor_id,
        boundary_id="boundary.hero-contribution")
    assert replayed == advanced and expired == []

    ended, expired = apply_lifecycle_transition(
        replayed, "contribution_end", actor_id=actor_id,
        boundary_id="boundary.hero-contribution.two")
    assert [row["id"] for row in expired] == ["effect.contribution.lifecycle"]
    assert ended["mistborn"]["allomancy"]["active_burns"] == []
    assert ended["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "active_effects"] == []


def test_per_encounter_action_ledger_resets_only_at_encounter_end():
    source = raw_action()
    source["system"]["id"] = "encounter-rally"
    source["name"] = "Encounter Rally"
    source["system"]["activation"]["cost"] = {"type": "free", "value": 0}
    source["system"]["activation"]["consume"] = []
    source["system"]["activation"]["uses"]["recharge"] = "per_encounter"
    action = normalize_action_item(source)

    changed, _ = apply_action_transaction(
        runtime(), action, request_id="request.encounter-rally.one")
    changed["turn"]["named_actions_used"] = []
    with pytest.raises(ActionTransactionError, match="once per encounter"):
        apply_action_transaction(
            changed, action, request_id="request.encounter-rally.two")

    reset, _ = apply_lifecycle_transition(
        changed, "encounter_end", next_scope_id="encounter.two")
    allowed, _ = apply_action_transaction(
        reset, action, request_id="request.encounter-rally.three")
    assert allowed["encounter"]["uses"] == {"encounter-rally": 1}


def test_transaction_is_pure_on_failure_and_rejects_request_id_reuse():
    state = runtime()
    before = deepcopy(state)
    action = normalize_action_item(raw_action())
    with pytest.raises(ActionTransactionError, match="request ID"):
        apply_action_transaction(state, action, request_id="bad id")
    assert state == before
