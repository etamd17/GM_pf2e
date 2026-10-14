"""Live Cosmere tracker integration for effects, roles, and turn state."""

from contextlib import nullcontext
from copy import deepcopy
import json

import app as host
import pytest

from systems.cosmere.actor import CosmereActor
from systems.cosmere.effects import condition_effect, project_conditions


def adversary(*, role="rival", health=10, deflect_types=None):
    actor = CosmereActor({
        "_id": "actor.runtime.test", "name": "Runtime Test", "type": "adversary",
        "system": {
            "role": role,
            "resources": {"hea": {"value": health, "max": {
                "useOverride": True, "override": health}}},
            "deflect": {"natural": 2, "types": deflect_types or {}},
        },
    })
    actor.instance_id = "combatant.runtime.test"
    return actor


def test_phase_start_expiry_is_applied_before_turn_permissions(monkeypatch):
    actor = adversary()
    actor.speed_choice = "fast"
    actor.effect_instances = [condition_effect(
        actor.instance_id, "stunned", instance_id="effect.runtime.stunned",
        duration_unit="round", remaining_duration=1, trigger="phase_start",
        expiry_state={"phase": "fast_npc", "not_before_round": 2},
    )]
    actor.conditions = project_conditions(actor.effect_instances)
    monkeypatch.setattr(host, "ROUND_NUMBER", 2)
    # The host may restore durable encounter phase markers at import time.
    # This unit test owns an isolated actor and must not inherit that campaign
    # runtime state, or its phase-start delivery becomes order/environment
    # dependent.
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)

    host._apply_start_of_turn(actor)

    assert actor.effect_instances == []
    assert actor.conditions == {}
    assert actor.max_actions == 2
    assert actor.reaction_used is False


def test_nonlethal_defeat_creates_canonical_unconscious_effect(monkeypatch):
    actor = adversary(health=4)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)

    host._cosmere_adjust_hp(actor, 20, "damage", "impact", nonlethal=True)

    assert actor.current_hp == 0
    assert actor.defeated is True and actor.nonlethal_defeat is True
    assert actor.conditions == {"unconscious": True}
    assert len(actor.effect_instances) == 1
    assert actor.effect_instances[0]["parameters"]["condition"] == "unconscious"


def test_healing_removes_canonical_unconscious_effect(monkeypatch):
    actor = adversary(health=4)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)
    host._cosmere_adjust_hp(actor, 20, "damage", "impact", nonlethal=True)

    host._cosmere_adjust_hp(actor, 2, "heal", "impact")

    assert actor.current_hp == 2 and actor.defeated is False
    assert actor.conditions == {}
    assert actor.effect_instances == []


def test_tracker_surprised_expires_after_the_affected_turn(monkeypatch):
    actor = adversary()
    actor.speed_choice = "fast"
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [actor])
    monkeypatch.setattr(host, "ROUND_NUMBER", 1)
    monkeypatch.setattr(host, "_persist_encounter_state", lambda: True)
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)

    host._apply_cosmere_condition_change(actor.instance_id, "surprised", "add")

    effect = actor.effect_instances[0]
    assert effect["duration_unit"] == "turn"
    assert effect["trigger"] == "turn_end"
    assert effect["expiry_state"] == {
        "actor_id": actor.instance_id, "not_before_turn": 1}

    host._apply_start_of_turn(actor)
    assert actor.speed_choice == "slow" and actor.max_actions == 2
    assert actor.reaction_used is True
    expired = host._advance_cosmere_combatant_effects(actor, {
        "trigger": "turn_end", "actor_id": actor.instance_id,
        "turn": actor.turn_serial,
    })
    assert [row["id"] for row in expired] == [effect["id"]]
    assert actor.conditions == {}


def test_start_of_turn_uses_stable_character_identity_for_pc_effects(monkeypatch):
    actor = adversary()
    actor.is_pc = True
    actor.restore_id = "cosmere.actor.stable"
    actor.instance_id = "combatant.ephemeral"
    actor.effect_instances = [condition_effect(
        actor.restore_id, "enhanced", instance_id="effect.turn-start.stable",
        duration_unit="turn", remaining_duration=1, trigger="turn_start",
        expiry_state={"actor_id": actor.restore_id},
    )]
    actor.conditions = project_conditions(actor.effect_instances)
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [actor])
    monkeypatch.setattr(host, "ROUND_NUMBER", 1)
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_sync_cosmere_pc_from_combatant", lambda *_args: None)

    host._apply_start_of_turn(actor)

    assert actor.effect_instances == []
    assert actor.conditions == {}


def test_live_expiry_reconciles_mistborn_effect_mirrors(monkeypatch):
    actor = adversary()
    actor.is_pc = True
    actor.restore_id = "ab" * 16
    effect = condition_effect(
        actor.restore_id, "enhanced", instance_id="effect.mistborn.live-expiry",
        duration_unit="turn", remaining_duration=1, trigger="turn_end",
        expiry_state={"actor_id": actor.restore_id},
    )
    actor.effect_instances = [effect]
    actor.conditions = project_conditions(actor.effect_instances)
    actor.mistborn_runtime = {
        "allomancy": {"active_burns": [deepcopy(effect)]},
        "feruchemy": {"by_metal": {
            "iron": {"active_effects": [deepcopy(effect)]},
        }},
    }
    stored = {
        "id": actor.restore_id,
        "revision": 7,
        "runtime_state": {
            "effects": [deepcopy(effect)],
            "conditions": {"enhanced": True},
            "mistborn": deepcopy(actor.mistborn_runtime),
        },
    }
    captured = {}
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_load_cosmere_pc", lambda _pid: deepcopy(stored))
    monkeypatch.setattr(host, "_cosmere_pc_path", lambda _pid: "unused.json")
    monkeypatch.setattr(host, "_path_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        host, "_save_cosmere_pc",
        lambda doc, **_kwargs: captured.setdefault("document", deepcopy(doc)))
    monkeypatch.setattr(host, "sse_broadcast", lambda *_args, **_kwargs: None)

    expired = host._advance_cosmere_combatant_effects(actor, {
        "trigger": "turn_end", "actor_id": actor.restore_id, "turn": 1,
    })

    assert [row["id"] for row in expired] == [effect["id"]]
    assert actor.mistborn_runtime["allomancy"]["active_burns"] == []
    assert actor.mistborn_runtime["feruchemy"]["by_metal"]["iron"][
        "active_effects"] == []
    saved = captured["document"]["runtime_state"]
    assert saved["effects"] == []
    assert saved["mistborn"]["allomancy"]["active_burns"] == []
    assert saved["mistborn"]["feruchemy"]["by_metal"]["iron"][
        "active_effects"] == []


def test_live_duration_decrement_is_written_through_before_later_actions(monkeypatch):
    actor = adversary()
    actor.is_pc = True
    actor.restore_id = "cd" * 16
    effect = condition_effect(
        actor.restore_id, "enhanced", instance_id="effect.live-decrement",
        duration_unit="turn", remaining_duration=2, trigger="turn_end",
        expiry_state={"actor_id": actor.restore_id},
    )
    actor.effect_instances = [effect]
    actor.conditions = project_conditions(actor.effect_instances)
    stored = {
        "id": actor.restore_id, "revision": 3,
        "runtime_state": {"effects": [deepcopy(effect)],
                          "conditions": {"enhanced": True}},
    }
    captured = {}
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_load_cosmere_pc", lambda _pid: deepcopy(stored))
    monkeypatch.setattr(host, "_cosmere_pc_path", lambda _pid: "unused.json")
    monkeypatch.setattr(host, "_path_lock", lambda _path: nullcontext())
    monkeypatch.setattr(
        host, "_save_cosmere_pc",
        lambda doc, **_kwargs: captured.setdefault("document", deepcopy(doc)))
    monkeypatch.setattr(host, "sse_broadcast", lambda *_args, **_kwargs: None)

    expired = host._advance_cosmere_combatant_effects(actor, {
        "trigger": "turn_end", "actor_id": actor.restore_id, "turn": 1,
    })

    assert expired == []
    assert actor.effect_instances[0]["remaining_duration"] == 1
    assert captured["document"]["runtime_state"]["effects"][0][
        "remaining_duration"] == 1


def test_phase_boundary_retry_after_mid_loop_failure_is_per_actor_idempotent(
        monkeypatch):
    first = adversary()
    first.is_pc = True
    first.restore_id = "de" * 16
    first.instance_id = "combatant.phase.first"
    second = adversary()
    second.is_pc = True
    second.restore_id = "ef" * 16
    second.instance_id = "combatant.phase.second"
    for actor, suffix in ((first, "first"), (second, "second")):
        actor.effect_instances = [condition_effect(
            actor.restore_id, "enhanced",
            instance_id=f"effect.phase-retry.{suffix}",
            duration_unit="round", remaining_duration=2,
            trigger="phase_start",
            expiry_state={"phase": "fast_pc", "not_before_round": 2},
        )]
        actor.conditions = project_conditions(actor.effect_instances)
    pre_boundary_second = deepcopy(second)
    synced_first = {}

    def fail_second(actor):
        if actor is second:
            raise RuntimeError("simulated character write failure")
        synced_first["actor"] = deepcopy(actor)

    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [first, second])
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_sync_cosmere_pc_from_combatant", fail_second)

    with pytest.raises(RuntimeError, match="simulated character write failure"):
        host._dispatch_cosmere_phase_start("fast_pc", 2)

    assert "2:fast_pc" not in host.COSMERE_PHASE_BOUNDARIES
    assert synced_first["actor"].effect_instances[0]["remaining_duration"] == 1

    # Model restart recovery: the first PC reloads its durable marked state;
    # the failed second PC reloads its pre-boundary character document.
    recovered_first = synced_first["actor"]
    recovered_second = pre_boundary_second
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [recovered_first, recovered_second])
    monkeypatch.setattr(
        host, "_sync_cosmere_pc_from_combatant", lambda _actor: None)

    host._dispatch_cosmere_phase_start("fast_pc", 2)

    assert recovered_first.effect_instances[0]["remaining_duration"] == 1
    assert recovered_second.effect_instances[0]["remaining_duration"] == 1
    assert "2:fast_pc" in host.COSMERE_PHASE_BOUNDARIES


def test_significant_minion_is_still_defeated_on_injury(monkeypatch):
    actor = adversary(role="minion", health=3)
    actor.significant_npc_injuries = True
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)
    import systems.cosmere.combat as combat
    monkeypatch.setattr(
        combat, "roll_injury",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("minion rolled injury")))

    host._cosmere_adjust_hp(actor, 20, "damage", "impact")

    assert actor.defeated is True
    assert actor.injuries == 0


def test_injury_spiral_creates_canonical_unconscious_effect(monkeypatch):
    actor = adversary(role="rival", health=3)
    actor.significant_npc_injuries = True
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)
    import systems.cosmere.combat as combat
    monkeypatch.setattr(combat, "roll_injury", lambda **kwargs: {
        "d20": 12, "total": 12, "severity": "shallow",
        "duration": "1d6 days", "is_death": False, "effect": "",
    })

    host._cosmere_adjust_hp(actor, 20, "damage", "impact")

    assert actor.conditions == {"unconscious": True}
    assert len(actor.effect_instances) == 1
    assert actor.effect_instances[0]["source_id"] == "injury.unconscious"


def test_restart_migrates_legacy_condition_timer_and_turns_expire_it(monkeypatch):
    actor = adversary()
    monkeypatch.setattr(host, "_cosmere_combatant", lambda _actor_id: actor)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)

    restored = host._restore_cosmere_combatant({
        "system": "cosmere",
        "cosmere_id": "actor.runtime.test",
        "instance_id": "combatant.legacy.timer",
        "conditions": {"surprised": True},
        "effects": [],
        "condition_expiry": {"surprised": 2},
    })

    assert restored.condition_expiry == {}
    assert len(restored.effect_instances) == 1
    assert restored.effect_instances[0]["duration_unit"] == "turn"
    assert restored.effect_instances[0]["remaining_duration"] == 2
    saved = host._augment_combatant_save({}, restored)
    assert saved["condition_expiry"] == {}
    assert saved["effects"][0]["remaining_duration"] == 2

    expired = host._advance_cosmere_combatant_effects(restored, {
        "trigger": "turn_end", "actor_id": restored.instance_id, "turn": 1,
    })
    assert expired == []
    assert restored.effect_instances[0]["remaining_duration"] == 1
    expired = host._advance_cosmere_combatant_effects(restored, {
        "trigger": "turn_end", "actor_id": restored.instance_id, "turn": 2,
    })
    assert len(expired) == 1 and restored.conditions == {}


def test_phase_boundary_dispatches_to_every_actor_despite_speed_switch(monkeypatch):
    starter = adversary()
    starter.instance_id = "combatant.fast.phase.starter"
    starter.speed_choice = "fast"
    target = adversary()
    target.instance_id = "combatant.switched.to.slow"
    target.speed_choice = "slow"
    target.effect_instances = [condition_effect(
        target.instance_id, "stunned", instance_id="effect.original.fast-phase",
        duration_unit="round", remaining_duration=1, trigger="phase_start",
        expiry_state={"phase": "fast_npc", "not_before_round": 2},
    )]
    target.conditions = project_conditions(target.effect_instances)
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [starter, target])
    monkeypatch.setattr(host, "ROUND_NUMBER", 2)
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)

    host._apply_start_of_turn(starter)

    assert target.effect_instances == []
    assert target.conditions == {}


def test_phase_boundary_advances_each_actor_only_once_per_round_phase(monkeypatch):
    first = adversary()
    first.instance_id = "combatant.fast.phase.one"
    first.speed_choice = "fast"
    second = adversary()
    second.instance_id = "combatant.fast.phase.two"
    second.speed_choice = "fast"
    target = adversary()
    target.instance_id = "combatant.phase.counter"
    target.speed_choice = "slow"
    target.effect_instances = [condition_effect(
        target.instance_id, "stunned", instance_id="effect.two.fast-phases",
        duration_unit="round", remaining_duration=2, trigger="phase_start",
        expiry_state={"phase": "fast_npc", "not_before_round": 2},
    )]
    target.conditions = project_conditions(target.effect_instances)
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [first, second, target])
    monkeypatch.setattr(host, "ROUND_NUMBER", 2)
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)

    host._apply_start_of_turn(first)
    host._apply_start_of_turn(second)

    assert target.effect_instances[0]["remaining_duration"] == 1


def test_round_wrap_dispatches_empty_original_phase_after_target_switch(
        monkeypatch):
    target = adversary()
    target.instance_id = "combatant.only.slow.actor"
    target.speed_choice = "slow"
    target.effect_instances = [condition_effect(
        target.instance_id, "stunned", instance_id="effect.empty.fast-phase",
        duration_unit="round", remaining_duration=1, trigger="phase_start",
        expiry_state={"phase": "fast_npc", "not_before_round": 2},
    )]
    target.conditions = project_conditions(target.effect_instances)
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [target])
    monkeypatch.setattr(host, "TURN_INDEX", 0)
    monkeypatch.setattr(host, "ROUND_NUMBER", 1)
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set(), raising=False)
    monkeypatch.setattr(host, "_workflow_preflight_turn", lambda: None)
    monkeypatch.setattr(host, "_expire_token_effects_for_round", lambda: None)
    monkeypatch.setattr(host, "_generate_turn_reminders", lambda: None)
    monkeypatch.setattr(host, "_persist_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(host, "_bump_campaign_stat", lambda *args, **kwargs: None)

    with host.app.test_request_context(
            "/api/cycle_turn/next",
            headers={"X-Requested-With": "XMLHttpRequest"}):
        response = host.cycle_turn("next")

    assert response.status_code == 200
    assert host.ROUND_NUMBER == 2
    assert target.effect_instances == []
    assert target.conditions == {}


def test_phase_dispatch_idempotency_keys_survive_encounter_restart(
        tmp_path, monkeypatch):
    actor = adversary()
    actor.instance_id = "combatant.phase.restart"
    actor.restore_id = "actor.runtime.test"
    monkeypatch.setattr(host, "ENCOUNTER_DIR", str(tmp_path))
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [actor])
    monkeypatch.setattr(
        host, "COSMERE_PHASE_BOUNDARIES", {"2:fast_npc"}, raising=False)

    assert host._do_persist_encounter_state_unlocked() is True
    raw = json.loads((tmp_path / "_autosave.json").read_text(encoding="utf-8"))
    assert raw["cosmere_phase_boundaries"] == ["2:fast_npc"]

    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", {"stale"})
    monkeypatch.setattr(host, "_restore_cosmere_combatant", lambda _item: actor)
    host._restore_encounter_autosave()

    assert host.COSMERE_PHASE_BOUNDARIES == {"2:fast_npc"}


def test_delay_dispatches_crossed_empty_phase_to_all_actors(monkeypatch):
    current = adversary()
    current.instance_id = "combatant.delay.fast"
    current.speed_choice = "fast"
    target = adversary()
    target.instance_id = "combatant.delay.slow"
    target.speed_choice = "slow"
    target.effect_instances = [condition_effect(
        target.instance_id, "stunned", instance_id="effect.delay.slow-pc",
        duration_unit="round", remaining_duration=1, trigger="phase_start",
        expiry_state={"phase": "slow_pc", "not_before_round": 1},
    )]
    target.conditions = project_conditions(target.effect_instances)
    monkeypatch.setattr(host, "ACTIVE_ENCOUNTER", [current, target])
    monkeypatch.setattr(host, "TURN_INDEX", 0)
    monkeypatch.setattr(host, "ROUND_NUMBER", 1)
    monkeypatch.setattr(host, "COSMERE_PHASE_BOUNDARIES", set())
    monkeypatch.setattr(host, "_workflow_preflight_turn", lambda **kwargs: None)
    monkeypatch.setattr(host, "_generate_turn_reminders", lambda: None)
    monkeypatch.setattr(host, "_persist_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(host, "_combat_log", lambda *args, **kwargs: None)

    with host.app.test_request_context(
            f"/api/delay_turn/{current.instance_id}",
            headers={"X-Requested-With": "XMLHttpRequest"}):
        response = host.delay_turn(current.instance_id)

    assert response.status_code == 200
    assert target.effect_instances == []
    assert target.conditions == {}
