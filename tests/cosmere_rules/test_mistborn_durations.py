import pytest

from systems.cosmere.effects import advance_effects
from systems.cosmere.mistborn_runtime import (
    MistbornRuntimeError,
    materialize_active_effect,
)
from systems.cosmere.rules.schema import EffectInstance, from_dict


BASE = {
    "instance_id": "effect.mistborn.test",
    "source_id": "mistborn.allomancy.pewter.action.burn",
    "target_id": "actor.hero",
    "art": "allomancy",
    "metal": "pewter",
}


@pytest.mark.parametrize(
    ("scene_type", "unit", "remaining", "trigger"),
    [
        ("combat", "round", 3, "phase_start"),
        ("conversation", "contribution", 3, "contribution_end"),
        ("endeavor", "contribution", 3, "contribution_end"),
        ("freeplay", "minute", 30, "manual"),
    ],
)
def test_rank_duration_materializes_for_every_scene_type(
        scene_type, unit, remaining, trigger):
    timing = ({"phase": "fast_pc", "round_number": 4}
              if scene_type == "combat" else {})
    if scene_type in {"conversation", "endeavor"}:
        timing["actor_id"] = "actor.hero"
    effect = materialize_active_effect(
        **BASE, scene_type=scene_type, duration="rank_rounds", rank=3,
        **timing)
    assert effect["duration_unit"] == unit
    assert effect["remaining_duration"] == remaining
    assert effect["trigger"] == trigger
    assert effect["parameters"]["automation_disposition"] == "calculated"
    assert from_dict(EffectInstance, effect).id == BASE["instance_id"]
    if scene_type == "combat":
        assert effect["expiry_state"] == {
            "phase": "fast_pc", "not_before_round": 5}
    elif scene_type in {"conversation", "endeavor"}:
        assert effect["expiry_state"] == {"actor_id": "actor.hero"}


@pytest.mark.parametrize("scene_type", ["combat", "conversation", "endeavor", "freeplay"])
def test_materialized_duration_expires_through_shared_effect_engine(scene_type):
    timing = ({"phase": "fast_pc", "round_number": 4}
              if scene_type == "combat" else {})
    if scene_type in {"conversation", "endeavor"}:
        timing["actor_id"] = "actor.hero"
    effect = materialize_active_effect(
        **BASE, scene_type=scene_type, duration="rank_rounds", rank=2,
        **timing)
    event = {"trigger": effect["trigger"]}
    if scene_type == "combat":
        event.update(phase="fast_pc", round=5)
    elif scene_type in {"conversation", "endeavor"}:
        event["actor_id"] = "actor.hero"
    elif effect["duration_unit"] in {"minute", "hour"}:
        event.update(duration_unit=effect["duration_unit"], elapsed=10)
    active, expired = advance_effects([effect], event)
    assert len(active) == 1 and not expired
    if scene_type == "combat":
        event["round"] = 6
    active, expired = advance_effects(active, event)
    assert active == []
    assert expired[0]["expired"] is True


@pytest.mark.parametrize(
    ("duration", "unit", "trigger"),
    [
        ("until_turn_start", "turn", "turn_start"),
        ("until_turn_end", "turn", "turn_end"),
        ("scene", "scene", "scene_end"),
        ("encounter", "encounter", "encounter_end"),
        ("permanent", "permanent", "manual"),
    ],
)
def test_turn_scene_encounter_and_permanent_materialization(duration, unit, trigger):
    effect = materialize_active_effect(
        **BASE, scene_type="combat", duration=duration, rank=2)
    assert effect["duration_unit"] == unit
    assert effect["trigger"] == trigger
    assert effect["remaining_duration"] is (None if duration == "permanent" else 1)


def test_contextual_effect_cannot_be_silently_promoted_to_calculated():
    with pytest.raises(MistbornRuntimeError) as caught:
        materialize_active_effect(
            **BASE, scene_type="conversation", duration="scene", rank=2,
            actor_id="actor.hero", contextual=True,
            automation_disposition="calculated")
    assert caught.value.code == "invalid_automation"

    tracked = materialize_active_effect(
        **BASE, scene_type="conversation", duration="scene", rank=2,
        actor_id="actor.hero", contextual=True,
        automation_disposition="tracked")
    adjudicated = materialize_active_effect(
        **{**BASE, "instance_id": "effect.mistborn.gm"},
        scene_type="endeavor", duration="scene", rank=2,
        actor_id="actor.hero", contextual=True,
        automation_disposition="gm_adjudicated")
    assert tracked["parameters"]["contextual"] is True
    assert adjudicated["parameters"]["automation_disposition"] == "gm_adjudicated"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"scene_type": "downtime"}, "invalid_scene_type"),
        ({"duration": "until_tuesday"}, "invalid_duration"),
        ({"automation_disposition": "automatic"}, "invalid_automation"),
        ({"metal": "wood"}, "invalid_metal"),
        ({"scene_type": []}, "invalid_scene_type"),
        ({"duration": []}, "invalid_duration"),
        ({"automation_disposition": []}, "invalid_automation"),
        ({"art": []}, "invalid_art"),
    ],
)
def test_effect_materialization_rejects_unknown_domain_values(changes, code):
    arguments = {**BASE, "scene_type": "combat", "duration": "scene", "rank": 2,
                 **changes}
    with pytest.raises(MistbornRuntimeError) as caught:
        materialize_active_effect(**arguments)
    assert caught.value.code == code


@pytest.mark.parametrize(
    "missing",
    ["phase", "round_number"],
)
def test_combat_round_duration_requires_authoritative_phase_and_round(missing):
    timing = {"phase": "slow_pc", "round_number": 7}
    timing.pop(missing)
    with pytest.raises(MistbornRuntimeError) as caught:
        materialize_active_effect(
            **BASE, scene_type="combat", duration="rank_rounds", rank=2,
            **timing)
    assert caught.value.code == "missing_timing_context"


def test_combat_duration_uses_actual_same_phase_next_round_lifecycle_events():
    effect = materialize_active_effect(
        **BASE, scene_type="combat", duration="one_round", rank=2,
        phase="slow_pc", round_number=7)

    active, expired = advance_effects(
        [effect], {"trigger": "phase_start", "phase": "fast_pc", "round": 8})
    assert active == [effect] and expired == []
    active, expired = advance_effects(
        active, {"trigger": "phase_start", "phase": "slow_pc", "round": 7})
    assert active == [effect] and expired == []
    active, expired = advance_effects(
        active, {"trigger": "phase_start", "phase": "slow_pc", "round": 8})
    assert active == [] and expired[0]["expired"] is True


@pytest.mark.parametrize("scene_type", ["conversation", "endeavor"])
def test_contribution_duration_ignores_another_actors_contribution(scene_type):
    effect = materialize_active_effect(
        **BASE, scene_type=scene_type, duration="one_round", rank=2,
        actor_id="actor.hero")
    active, expired = advance_effects(
        [effect], {"trigger": "contribution_end", "actor_id": "actor.other"})
    assert active == [effect] and expired == []
    active, expired = advance_effects(
        active, {"trigger": "contribution_end", "actor_id": "actor.hero"})
    assert active == [] and expired[0]["expired"] is True


@pytest.mark.parametrize("scene_type", ["conversation", "endeavor"])
def test_contribution_duration_requires_authoritative_actor_scope(scene_type):
    with pytest.raises(MistbornRuntimeError) as caught:
        materialize_active_effect(
            **BASE, scene_type=scene_type, duration="rank_rounds", rank=2)
    assert caught.value.code == "missing_timing_context"
