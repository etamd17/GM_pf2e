"""Canonical Cosmere effect instances and deterministic expiry semantics."""
from copy import deepcopy

from systems.cosmere.effects import (
    advance_effects,
    apply_effect,
    migrate_condition_expiry,
    normalize_runtime_state,
    project_conditions,
)


ACTOR = "ab" * 16


def effect(identity, condition, *, attribute=None, policy="independent",
           unit="scene", remaining=1, trigger="scene_end", expiry=None):
    return {
        "id": identity,
        "source_id": "stormlight.condition." + condition,
        "target_id": ACTOR,
        "parameters": {"condition": condition, "magnitude": 1},
        "selected_attribute": attribute,
        "stacking_identity": "condition." + condition + ("." + attribute if attribute else ""),
        "duration_unit": unit,
        "remaining_duration": remaining,
        "trigger": trigger,
        "refresh_policy": policy,
        "expiry_state": expiry or {},
        "expired": False,
    }


def test_legacy_conditions_migrate_once_and_projection_is_detached():
    state = normalize_runtime_state({"conditions": {"afflicted": 2, "prone": True}}, ACTOR)
    assert len(state["effects"]) == 2
    assert state["conditions"] == {"afflicted": 2, "prone": True}
    detached = project_conditions(state["effects"])
    detached["afflicted"] = 99
    assert state["conditions"]["afflicted"] == 2
    assert normalize_runtime_state(state, ACTOR)["effects"] == state["effects"]


def test_afflicted_instances_and_enhanced_attributes_remain_independent():
    effects = []
    effects = apply_effect(effects, effect("effect.affliction.one", "afflicted"))
    effects = apply_effect(effects, effect("effect.affliction.two", "afflicted"))
    effects = apply_effect(effects, effect(
        "effect.enhance.strength", "enhanced", attribute="strength"))
    effects = apply_effect(effects, effect(
        "effect.enhance.speed", "enhanced", attribute="speed"))
    assert len(effects) == 4
    assert project_conditions(effects) == {"afflicted": 2, "enhanced": 2}
    assert {row["selected_attribute"] for row in effects if row["parameters"]["condition"] == "enhanced"} == {
        "strength", "speed"}


def test_named_refresh_replace_extend_and_independent_policies():
    base = effect("effect.focus.one", "focused", policy="refresh", remaining=1,
                  unit="round", trigger="phase_start")
    rows = apply_effect([], base)
    refreshed = {**base, "id": "effect.focus.two", "remaining_duration": 3}
    rows = apply_effect(rows, refreshed)
    assert len(rows) == 1 and rows[0]["id"] == "effect.focus.one"
    assert rows[0]["remaining_duration"] == 3

    extended = {**base, "id": "effect.focus.three", "refresh_policy": "extend",
                "remaining_duration": 2}
    rows = apply_effect(rows, extended)
    assert rows[0]["remaining_duration"] == 5

    replacement = {**base, "id": "effect.focus.four", "refresh_policy": "replace"}
    rows = apply_effect(rows, replacement)
    assert rows == [replacement]

    independent = {**base, "id": "effect.focus.five", "refresh_policy": "independent"}
    assert len(apply_effect(rows, independent)) == 2


def test_round_expires_at_start_of_same_phase_in_following_round():
    row = effect(
        "effect.round.one", "slowed", unit="round", trigger="phase_start",
        expiry={"phase": "fast_pc", "not_before_round": 4},
    )
    active, expired = advance_effects([row], {"trigger": "phase_start", "phase": "slow_pc", "round": 4})
    assert active == [row] and expired == []
    active, expired = advance_effects(active, {"trigger": "phase_start", "phase": "fast_pc", "round": 3})
    assert active == [row] and expired == []
    active, expired = advance_effects(active, {"trigger": "phase_start", "phase": "fast_pc", "round": 4})
    assert active == [] and expired[0]["expired"] is True


def test_turn_contribution_scene_encounter_and_elapsed_time_expiry_are_distinct():
    rows = [
        effect("effect.turn.one", "surprised", unit="turn", trigger="turn_end",
               expiry={"actor_id": ACTOR, "not_before_turn": 8}),
        effect("effect.contribution.one", "determined", unit="contribution",
               trigger="contribution_end", expiry={"actor_id": ACTOR}),
        effect("effect.scene.one", "empowered", unit="scene", trigger="scene_end"),
        effect("effect.encounter.one", "focused", unit="encounter", trigger="encounter_end"),
        effect("effect.minute.one", "slowed", unit="minute", remaining=2, trigger="manual"),
        effect("effect.hour.one", "afflicted", unit="hour", remaining=2, trigger="manual"),
    ]
    active, expired = advance_effects(rows, {"trigger": "turn_end", "actor_id": ACTOR, "turn": 7})
    assert not expired
    active, expired = advance_effects(active, {"trigger": "turn_end", "actor_id": ACTOR, "turn": 8})
    assert [x["id"] for x in expired] == ["effect.turn.one"]
    active, expired = advance_effects(active, {"trigger": "contribution_end", "actor_id": ACTOR})
    assert [x["id"] for x in expired] == ["effect.contribution.one"]
    active, expired = advance_effects(active, {"trigger": "manual", "duration_unit": "minute", "elapsed": 2})
    assert [x["id"] for x in expired] == ["effect.minute.one"]
    active, expired = advance_effects(active, {"trigger": "manual", "duration_unit": "hour", "elapsed": 1})
    assert not expired
    active, expired = advance_effects(active, {"trigger": "scene_end"})
    assert [x["id"] for x in expired] == ["effect.scene.one"]
    active, expired = advance_effects(active, {"trigger": "encounter_end"})
    assert [x["id"] for x in expired] == ["effect.encounter.one"]
    assert [x["id"] for x in active] == ["effect.hour.one"]


def test_legacy_condition_timer_migrates_to_canonical_turn_effect():
    rows = normalize_runtime_state(
        {"conditions": {"surprised": True}}, ACTOR)["effects"]

    migrated = migrate_condition_expiry(
        rows, {"surprised": True}, {"surprised": 2}, ACTOR)

    assert len(migrated) == 1
    assert migrated[0]["parameters"]["condition"] == "surprised"
    assert migrated[0]["duration_unit"] == "turn"
    assert migrated[0]["remaining_duration"] == 2
    assert migrated[0]["trigger"] == "turn_end"
    assert migrated[0]["expiry_state"] == {"actor_id": ACTOR}
    active, expired = advance_effects(
        migrated, {"trigger": "turn_end", "actor_id": ACTOR, "turn": 1})
    assert not expired and active[0]["remaining_duration"] == 1
    active, expired = advance_effects(
        active, {"trigger": "turn_end", "actor_id": ACTOR, "turn": 2})
    assert active == [] and expired[0]["parameters"]["condition"] == "surprised"
