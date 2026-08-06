"""Regression tests for services/active_effects.py — the bonus-typed
effects engine that decides how conditions, spells, feats, and items
modify a combatant's stats (attack/AC/saves/etc.). These functions
gate whether a PC lives or dies in combat and had near-zero direct
coverage, so this suite locks the CURRENT behavior exhaustively.

CI-safe: every test drives the engine with plain dicts (the engine is
pure — it operates on token/effect dicts, not live PCs). No party_data,
no Flask, no PARTY_LIBRARY dependency. One fixture-backed test confirms
the engine layers cleanly on top of a real PC's base stat numbers.

Behavior was verified empirically against the live module before each
assertion was written; tests assert what the code ACTUALLY does. A few
spots that look questionable vs strict PF2e RAW are marked
'# POSSIBLE BUG:' and reported separately — they still lock current
behavior so CI stays green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.active_effects import (
    _expand_modifier,
    _predicate_matches,
    _stack_typed,
    catalog_list,
    compute_effects,
    compute_for_action,
    compute_token_stats,
    consume_triggered,
    expire_round_effects,
    find_reaction_triggers,
    instantiate_effect,
    list_active_effects,
    materialize_chains,
    resolve_save,
    EFFECT_CATALOG,
    PF2E_CONDITION_EFFECTS,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ── Helpers ──────────────────────────────────────────────────────────
def _eff(name, mods, **extra):
    """Minimal active-effect record."""
    e = {"name": name, "modifiers": mods}
    e.update(extra)
    return e


def _mod(stat, value, bonus_type="untyped", op="add", **extra):
    m = {"stat": stat, "op": op, "value": value, "bonus_type": bonus_type}
    m.update(extra)
    return m


# ══════════════════════════════════════════════════════════════════════
# _stack_typed — the PF2e bonus-typing core
# ══════════════════════════════════════════════════════════════════════
class TestStackTyped:
    def test_empty_is_zero(self):
        assert _stack_typed([], "status") == 0
        assert _stack_typed([], "untyped") == 0

    def test_untyped_sums_everything(self):
        assert _stack_typed([1, 2, 3], "untyped") == 6
        assert _stack_typed([1, 2, -1, -3], "untyped") == -1
        assert _stack_typed([-1, -2], "untyped") == -3

    def test_typed_best_positive_plus_worst_negative(self):
        # status: highest positive (2) and lowest negative (-3) BOTH apply
        assert _stack_typed([1, 2, -1, -3], "status") == -1
        assert _stack_typed([1, 2, 3], "circumstance") == 3
        assert _stack_typed([-1, -2, -3], "item") == -3

    def test_typed_only_positives(self):
        assert _stack_typed([1, 5, 3], "status") == 5

    def test_typed_only_negatives(self):
        assert _stack_typed([-1, -5, -3], "status") == -5

    def test_typed_single_value(self):
        assert _stack_typed([4], "circumstance") == 4
        assert _stack_typed([-2], "circumstance") == -2

    def test_typed_zero_only_is_zero(self):
        # zero is neither positive nor negative -> contributes nothing
        assert _stack_typed([0], "status") == 0
        assert _stack_typed([0, 0], "status") == 0

    def test_typed_zero_does_not_block_real_bonus(self):
        assert _stack_typed([0, 3], "status") == 3
        assert _stack_typed([0, -3], "status") == -3

    def test_typed_one_of_each_sign_stacks(self):
        # a +2 status bonus AND a -1 status penalty both apply (net +1)
        assert _stack_typed([2, -1], "status") == 1


# ══════════════════════════════════════════════════════════════════════
# _predicate_matches — condition/context gating
# ══════════════════════════════════════════════════════════════════════
class TestPredicateMatches:
    def test_missing_predicate_always_matches(self):
        assert _predicate_matches(None, None) is True
        assert _predicate_matches({}, None) is True
        assert _predicate_matches(None, {"scope": "strike"}) is True

    def test_unfiltered_predicate_matches_without_context(self):
        # scope=always, target=any, no tag -> universally applicable
        assert _predicate_matches({"scope": "always", "target": "any"}, None) is True

    def test_filtered_predicate_dropped_when_no_context(self):
        # The no-context call asks "what applies universally?" — a gated
        # modifier (scope=melee) doesn't qualify.
        assert _predicate_matches({"scope": "melee"}, None) is False
        assert _predicate_matches({"target": "self"}, None) is False
        assert _predicate_matches({"tag": "vs_alignment"}, None) is False

    def test_scope_melee_matches_melee_strike(self):
        assert _predicate_matches(
            {"scope": "melee"}, {"scope": "strike", "strike_kind": "melee"}
        ) is True

    def test_scope_melee_rejects_ranged_strike(self):
        assert _predicate_matches(
            {"scope": "melee"}, {"scope": "strike", "strike_kind": "ranged"}
        ) is False

    def test_scope_melee_accepts_scope_passed_directly(self):
        # caller passed scope='melee' without a separate strike_kind
        assert _predicate_matches({"scope": "melee"}, {"scope": "melee"}) is True

    def test_scope_ranged_matches_ranged_strike(self):
        assert _predicate_matches(
            {"scope": "ranged"}, {"scope": "strike", "strike_kind": "ranged"}
        ) is True

    def test_scope_ranged_rejects_melee_strike(self):
        assert _predicate_matches(
            {"scope": "ranged"}, {"scope": "strike", "strike_kind": "melee"}
        ) is False

    def test_scope_strike_matches_any_attack_kind(self):
        # scope='strike'/'attack' is kind-agnostic
        assert _predicate_matches(
            {"scope": "strike"}, {"scope": "strike", "strike_kind": "ranged"}
        ) is True
        assert _predicate_matches(
            {"scope": "attack"}, {"scope": "strike", "strike_kind": "melee"}
        ) is True

    def test_scope_strike_rejects_non_attack_scope(self):
        assert _predicate_matches({"scope": "strike"}, {"scope": "save"}) is False

    def test_scope_exact_match_for_other_scopes(self):
        assert _predicate_matches({"scope": "save"}, {"scope": "save"}) is True
        assert _predicate_matches({"scope": "save"}, {"scope": "skill"}) is False
        assert _predicate_matches({"scope": "skill"}, {"scope": "skill"}) is True

    def test_target_self_requires_subject_equals_source(self):
        assert _predicate_matches(
            {"target": "self"}, {"subject_id": "A", "source_id": "A"}
        ) is True
        assert _predicate_matches(
            {"target": "self"}, {"subject_id": "A", "source_id": "B"}
        ) is False

    def test_target_self_rejects_missing_subject(self):
        assert _predicate_matches({"target": "self"}, {"source_id": "A"}) is False

    def test_target_specific_instance_id(self):
        assert _predicate_matches({"target": "tok9"}, {"target_id": "tok9"}) is True
        assert _predicate_matches({"target": "tok9"}, {"target_id": "tok1"}) is False

    def test_tag_must_be_present_in_context(self):
        assert _predicate_matches({"tag": "vs_alignment"}, {"tags": ["vs_alignment"]}) is True
        assert _predicate_matches({"tag": "vs_alignment"}, {"tags": []}) is False
        assert _predicate_matches({"tag": "vs_alignment"}, {}) is False

    def test_scope_is_case_insensitive(self):
        assert _predicate_matches({"scope": "MELEE"}, {"scope": "melee"}) is True

    def test_combined_filters_all_must_pass(self):
        pred = {"scope": "melee", "tag": "natural_weapon"}
        ctx_ok = {"scope": "strike", "strike_kind": "melee", "tags": ["natural_weapon"]}
        ctx_no_tag = {"scope": "strike", "strike_kind": "melee", "tags": []}
        assert _predicate_matches(pred, ctx_ok) is True
        assert _predicate_matches(pred, ctx_no_tag) is False


# ══════════════════════════════════════════════════════════════════════
# _expand_modifier — V-marker resolution + 'saves' fan-out
# ══════════════════════════════════════════════════════════════════════
class TestExpandModifier:
    def test_positive_v_marker_resolves_to_condition_value(self):
        rows = _expand_modifier({"stat": "attack", "value": "V"}, condition_value=3)
        assert rows[0]["delta"] == 3

    def test_negative_v_marker_resolves_to_negative_value(self):
        rows = _expand_modifier({"stat": "attack", "value": "-V"}, condition_value=2)
        assert rows[0]["delta"] == -2

    def test_numeric_value_passthrough(self):
        rows = _expand_modifier({"stat": "ac", "value": -2}, condition_value=99)
        assert rows[0]["delta"] == -2  # numeric ignores condition_value

    def test_saves_expands_to_fort_ref_will(self):
        rows = _expand_modifier({"stat": "saves", "value": 1, "bonus_type": "status"})
        stats = {r["stat"] for r in rows}
        assert stats == {"fort", "ref", "will"}
        assert all(r["delta"] == 1 for r in rows)

    def test_defaults_op_add_bonus_untyped(self):
        rows = _expand_modifier({"stat": "ac", "value": 1})
        assert rows[0]["op"] == "add"
        assert rows[0]["bonus_type"] == "untyped"


# ══════════════════════════════════════════════════════════════════════
# compute_token_stats — full stacking resolver
# ══════════════════════════════════════════════════════════════════════
class TestComputeTokenStats:
    def test_no_sources_returns_base(self):
        r = compute_token_stats({}, [], {"attack": 10, "ac": 20})
        assert r["effective"] == {"attack": 10, "ac": 20}
        assert r["breakdown"] == []

    def test_two_same_type_bonuses_do_not_stack(self):
        # Bless +1 status + Heroism +1 status -> only one +1 applies
        r = compute_token_stats(
            {},
            [
                _eff("Bless", [_mod("attack", 1, "status")]),
                _eff("Heroism", [_mod("attack", 1, "status")]),
            ],
            {"attack": 10},
        )
        assert r["effective"]["attack"] == 11

    def test_higher_same_type_bonus_wins(self):
        r = compute_token_stats(
            {},
            [
                _eff("Bless", [_mod("attack", 1, "status")]),
                _eff("Heroism", [_mod("attack", 2, "status")]),
            ],
            {"attack": 10},
        )
        assert r["effective"]["attack"] == 12

    def test_untyped_bonuses_sum(self):
        r = compute_token_stats(
            {},
            [
                _eff("A", [_mod("attack", 1, "untyped")]),
                _eff("B", [_mod("attack", 2, "untyped")]),
            ],
            {"attack": 10},
        )
        assert r["effective"]["attack"] == 13

    def test_different_types_stack_with_each_other(self):
        # +1 status AND +1 circumstance AND +1 item all apply (= +3)
        r = compute_token_stats(
            {},
            [
                _eff("S", [_mod("ac", 1, "status")]),
                _eff("C", [_mod("ac", 1, "circumstance")]),
                _eff("I", [_mod("ac", 1, "item")]),
            ],
            {"ac": 20},
        )
        assert r["effective"]["ac"] == 23

    def test_typed_bonus_and_penalty_both_apply(self):
        # +2 status bonus and -1 status penalty both count -> +1 net
        r = compute_token_stats(
            {},
            [
                _eff("Buff", [_mod("ac", 2, "status")]),
                _eff("Debuff", [_mod("ac", -1, "status")]),
            ],
            {"ac": 20},
        )
        assert r["effective"]["ac"] == 21

    def test_suppressed_effect_contributes_nothing(self):
        r = compute_token_stats(
            {},
            [_eff("X", [_mod("attack", 5, "status")], suppressed=True)],
            {"attack": 10},
        )
        assert r["effective"]["attack"] == 10

    def test_set_op_overrides_value(self):
        r = compute_token_stats(
            {}, [_eff("S", [_mod("ac", 99, op="set")])], {"ac": 20}
        )
        assert r["effective"]["ac"] == 99

    def test_mult_op_multiplies_value(self):
        r = compute_token_stats(
            {}, [_eff("M", [_mod("speed", 2, op="mult")])], {"speed": 25}
        )
        assert r["effective"]["speed"] == 50

    def test_set_op_on_missing_stat_is_ignored(self):
        r = compute_token_stats(
            {}, [_eff("S", [_mod("nope", 99, op="set")])], {"ac": 20}
        )
        assert "nope" not in r["effective"]

    def test_add_to_missing_stat_not_added_to_effective(self):
        # net is computed for the breakdown, but only applied if the
        # stat already exists in base_stats.
        r = compute_token_stats(
            {}, [_eff("A", [_mod("newstat", 3, "untyped")])], {"ac": 20}
        )
        assert "newstat" not in r["effective"]
        assert len(r["breakdown"]) == 1

    def test_modifier_with_none_stat_skipped_in_add_groups(self):
        # marker modifiers (stat=None) don't enter the add grouping
        r = compute_token_stats(
            {}, [_eff("Marker", [_mod(None, 0, "untyped")])], {"ac": 20}
        )
        assert r["effective"]["ac"] == 20

    def test_breakdown_marks_winner_applied_and_loser_suppressed(self):
        r = compute_token_stats(
            {},
            [
                _eff("Bless", [_mod("attack", 1, "status", source="Bless")]),
                _eff("Heroism", [_mod("attack", 2, "status", source="Heroism")]),
            ],
            {"attack": 10},
        )
        rows = {b["source"]: b for b in r["breakdown"]}
        assert rows["Heroism"]["applied"] is True
        assert rows["Bless"]["applied"] is False
        assert rows["Bless"]["suppressed_by"] == "Heroism"

    def test_breakdown_untyped_all_applied(self):
        r = compute_token_stats(
            {},
            [
                _eff("A", [_mod("attack", 1, "untyped", source="A")]),
                _eff("B", [_mod("attack", 2, "untyped", source="B")]),
            ],
            {"attack": 10},
        )
        assert all(b["applied"] for b in r["breakdown"])

    def test_source_falls_back_to_effect_name(self):
        # modifier with no explicit source inherits the effect's name
        r = compute_token_stats(
            {}, [_eff("Heroism", [_mod("attack", 1, "status")])], {"attack": 10}
        )
        assert r["breakdown"][0]["source"] == "Heroism"


# ══════════════════════════════════════════════════════════════════════
# Condition table behavior (via compute_token_stats)
# ══════════════════════════════════════════════════════════════════════
class TestConditions:
    def test_frightened_penalizes_broadly(self):
        r = compute_token_stats(
            {"frightened": 2}, [], {"attack": 10, "ac": 20, "will": 8}
        )
        assert r["effective"]["attack"] == 8
        assert r["effective"]["ac"] == 18
        assert r["effective"]["will"] == 6

    def test_condition_true_treated_as_one(self):
        r = compute_token_stats({"frightened": True}, [], {"attack": 10})
        assert r["effective"]["attack"] == 9

    def test_zero_value_condition_skipped(self):
        r = compute_token_stats({"frightened": 0}, [], {"attack": 10})
        assert r["effective"]["attack"] == 10

    def test_slowed_reduces_actions_untyped(self):
        r = compute_token_stats({"slowed": 2}, [], {"actions": 3})
        assert r["effective"]["actions"] == 1

    def test_quickened_adds_action(self):
        r = compute_token_stats({"quickened": 1}, [], {"actions": 3})
        assert r["effective"]["actions"] == 4

    def test_slowed_and_haste_actions_sum_untyped(self):
        # both untyped action deltas stack (slow -1 + haste +1 = net 0)
        r = compute_token_stats(
            {"slowed": 1},
            [_eff("Haste", [_mod("actions", 1, "untyped")])],
            {"actions": 3},
        )
        assert r["effective"]["actions"] == 3

    def test_off_guard_circumstance_penalty(self):
        r = compute_token_stats({"off_guard": 1}, [], {"ac": 20})
        assert r["effective"]["ac"] == 18

    def test_off_guard_circumstance_does_not_stack_with_prone(self):
        # both Off-Guard and Prone give -2 circumstance to AC; lowest
        # (worst) negative wins per type -> only -2 total, not -4.
        r = compute_token_stats({"off_guard": 1, "prone": 1}, [], {"ac": 20})
        assert r["effective"]["ac"] == 18

    def test_enfeebled_applies_attack_penalty_with_decorative_tag(self):
        # POSSIBLE BUG: enfeebled's attack/damage penalty carries a
        # 'tag': 'str_melee' but NO predicate, so the tag is purely
        # decorative — the penalty applies to ALL attacks (including
        # ranged / non-STR) in the no-context compute, where strict
        # PF2e only penalizes STR-based melee. Locking current behavior.
        r = compute_token_stats({"enfeebled": 3}, [], {"attack": 10, "damage": 5})
        assert r["effective"]["attack"] == 7
        assert r["effective"]["damage"] == 2

    def test_condition_status_penalty_stacks_with_active_status_penalty_capped(self):
        # frightened -1 status + sickened -1 status to attack -> only
        # the worst (-1) applies (same type), not -2.
        r = compute_token_stats(
            {"frightened": 1, "sickened": 1}, [], {"attack": 10}
        )
        assert r["effective"]["attack"] == 9


# ══════════════════════════════════════════════════════════════════════
# compute_for_action — predicate-gated context compute
# ══════════════════════════════════════════════════════════════════════
class TestComputeForAction:
    def test_protection_only_applies_with_vs_alignment_tag(self):
        prot = instantiate_effect("protection", effect_id="p1", current_round=1)
        no_tag = compute_for_action({}, [prot], {"ac": 20}, scope="save", tags=[])
        with_tag = compute_for_action(
            {}, [prot], {"ac": 20}, scope="save", tags=["vs_alignment"]
        )
        assert no_tag["effective"]["ac"] == 20
        assert with_tag["effective"]["ac"] == 21

    def test_bestial_mutagen_attack_only_on_melee_natural_weapon(self):
        bm = instantiate_effect("bestial_mutagen_lesser", effect_id="b1", current_round=1)
        melee_nw = compute_for_action(
            {}, [bm], {"attack": 10, "ac": 20},
            scope="strike", strike_kind="melee", tags=["natural_weapon"],
        )
        # +1 item attack applies; -2 status AC always applies
        assert melee_nw["effective"]["attack"] == 11
        assert melee_nw["effective"]["ac"] == 18

    def test_bestial_mutagen_attack_gated_off_for_ranged(self):
        bm = instantiate_effect("bestial_mutagen_lesser", effect_id="b1", current_round=1)
        ranged = compute_for_action(
            {}, [bm], {"attack": 10},
            scope="strike", strike_kind="ranged", tags=["natural_weapon"],
        )
        assert ranged["effective"]["attack"] == 10

    def test_bestial_mutagen_attack_gated_off_without_natural_weapon_tag(self):
        bm = instantiate_effect("bestial_mutagen_lesser", effect_id="b1", current_round=1)
        melee_no_tag = compute_for_action(
            {}, [bm], {"attack": 10}, scope="strike", strike_kind="melee", tags=[]
        )
        assert melee_no_tag["effective"]["attack"] == 10

    def test_marker_modifier_does_not_alter_stats(self):
        # Sure Strike is a pure marker (stat=None) — even in a strike
        # context it contributes no stat change.
        ss = instantiate_effect("sure_strike", effect_id="s1", current_round=1)
        r = compute_for_action(
            {}, [ss], {"attack": 10}, scope="strike", strike_kind="melee",
        )
        assert r["effective"]["attack"] == 10


# ══════════════════════════════════════════════════════════════════════
# resolve_save — degree of success + outcome application
# ══════════════════════════════════════════════════════════════════════
class TestResolveSave:
    def _eff_with_save(self, **save_extra):
        save = {"type": "will", "dc": 20}
        save.update(save_extra)
        return {"save": save}

    def test_crit_success_on_dc_plus_10(self):
        e = self._eff_with_save(on_crit_success="negate")
        r = resolve_save(e, 30)
        assert r["degree"] == "crit_success"
        assert r["outcome"] == "negate"
        assert e["suppressed"] is True

    def test_success_on_exactly_dc(self):
        e = self._eff_with_save(on_success="negate")
        r = resolve_save(e, 20)
        assert r["degree"] == "success"

    def test_failure_just_below_dc(self):
        e = self._eff_with_save(on_failure="apply")
        r = resolve_save(e, 19)
        assert r["degree"] == "failure"
        assert e["suppressed"] is False

    def test_crit_failure_on_dc_minus_10(self):
        e = self._eff_with_save(on_crit_failure="stronger")
        r = resolve_save(e, 10)
        assert r["degree"] == "crit_failure"
        assert r["outcome"] == "stronger"
        assert e["suppressed"] is False

    def test_crit_success_boundary_dc_plus_9_is_success(self):
        e = self._eff_with_save(on_success="negate")
        assert resolve_save(e, 29)["degree"] == "success"

    def test_crit_failure_boundary_dc_minus_9_is_failure(self):
        e = self._eff_with_save(on_failure="apply")
        assert resolve_save(e, 11)["degree"] == "failure"

    def test_negate_outcome_suppresses(self):
        e = self._eff_with_save(on_success="negate")
        resolve_save(e, 20)
        assert e["suppressed"] is True

    def test_apply_outcome_does_not_suppress(self):
        e = self._eff_with_save(on_failure="apply")
        resolve_save(e, 15)
        assert e["suppressed"] is False

    def test_missing_outcome_key_defaults_to_apply(self):
        # save block with no on_success key -> outcome 'apply', not suppressed
        e = {"save": {"dc": 20}}
        r = resolve_save(e, 25)
        assert r["outcome"] == "apply"
        assert e["suppressed"] is False

    def test_no_save_block_uses_dc_zero(self):
        e = {}
        r = resolve_save(e, 5)
        assert r["dc"] == 0
        assert r["degree"] == "success"  # 5 - 0 >= 0
        assert r["outcome"] == "apply"

    def test_reduce_halves_duration_and_recomputes_expiry(self):
        e = {
            "save": {"dc": 20, "on_success": "reduce"},
            "duration": {"type": "rounds", "value": 6, "expires_at_round": 6},
            "applied_at_round": 0,
        }
        resolve_save(e, 20, current_round=1)
        assert e["duration"]["value"] == 3
        assert e["suppressed"] is False
        # start = applied_at_round(0) is falsy -> falls back to current_round(1)
        # POSSIBLE BUG: applied_at_round == 0 (round zero) is treated as
        # "missing" by the `or current_round` fallback, so expiry is
        # recomputed from round 1 not 0.
        assert e["duration"]["expires_at_round"] == 4

    def test_reduce_minimum_duration_is_one(self):
        e = {
            "save": {"dc": 20, "on_success": "reduce"},
            "duration": {"type": "rounds", "value": 1, "expires_at_round": 1},
            "applied_at_round": 2,
        }
        resolve_save(e, 20, current_round=2)
        assert e["duration"]["value"] == 1  # max(1, 1//2) == 1

    def test_reduce_minutes_recomputes_in_rounds(self):
        e = {
            "save": {"dc": 20, "on_success": "reduce"},
            "duration": {"type": "minutes", "value": 2, "expires_at_round": 20},
            "applied_at_round": 3,
        }
        resolve_save(e, 20, current_round=5)
        assert e["duration"]["value"] == 1  # 2 // 2
        # start = applied_at_round 3; minutes -> +value*10 => 3 + 10
        assert e["duration"]["expires_at_round"] == 13

    def test_save_result_metadata_recorded(self):
        e = self._eff_with_save(on_failure="apply")
        r = resolve_save(e, 15, current_round=4)
        assert e["save_result"] == r
        assert r["roll"] == 15
        assert r["dc"] == 20
        assert r["rolled_at_round"] == 4

    def test_slow_catalog_success_reduces_action_penalty_duration(self):
        slow = instantiate_effect("slow", effect_id="sl1", current_round=1, save_dc=24)
        # success -> reduce; effect stays active but shorter
        resolve_save(slow, 24, current_round=1)
        assert slow["suppressed"] is False
        assert slow["save_result"]["degree"] == "success"

    def test_hideous_laughter_success_negates(self):
        hl = instantiate_effect("hideous_laughter", effect_id="hl1", current_round=1, save_dc=22)
        resolve_save(hl, 22, current_round=1)  # success -> negate
        assert hl["suppressed"] is True


# ══════════════════════════════════════════════════════════════════════
# consume_triggered — one-shot modifier/effect clearing
# ══════════════════════════════════════════════════════════════════════
class TestConsumeTriggered:
    def test_next_strike_modifier_removed(self):
        effs = [
            _eff("SureStrike", [_mod(None, 0, "untyped",
                 predicate={"scope": "strike", "until": "next_strike"})]),
        ]
        kept = consume_triggered(effs, event="next_strike")
        # the only modifier was consumed and the effect has nothing left
        assert kept == []

    def test_permanent_modifier_survives(self):
        effs = [_eff("Bless", [_mod("attack", 1, "status")], id="b")]
        kept = consume_triggered(effs, event="next_strike")
        assert [e["id"] for e in kept] == ["b"]

    def test_only_matching_until_consumed(self):
        effs = [
            _eff("Mixed", [
                _mod("attack", 1, "status",
                     predicate={"until": "next_strike"}),
                _mod("ac", 1, "status",
                     predicate={"until": "end_of_turn"}),
            ], id="m"),
        ]
        kept = consume_triggered(effs, event="next_strike")
        assert len(kept) == 1
        remaining = kept[0]["modifiers"]
        assert len(remaining) == 1
        assert remaining[0]["stat"] == "ac"

    def test_modifier_without_predicate_treated_permanent(self):
        effs = [_eff("Plain", [_mod("attack", 1, "status")], id="p")]
        kept = consume_triggered(effs, event="next_action")
        assert kept[0]["modifiers"]  # still there

    def test_effect_level_consumes_on_drops_whole_effect(self):
        effs = [_eff("Whole", [_mod("ac", 1, "status")],
                     id="w", consumes_on="end_of_turn")]
        assert consume_triggered(effs, event="end_of_turn") == []

    def test_effect_level_consumes_on_case_insensitive(self):
        effs = [_eff("Whole", [_mod("ac", 1, "status")],
                     id="w", consumes_on="END_OF_TURN")]
        assert consume_triggered(effs, event="end_of_turn") == []

    def test_effect_kept_when_tags_remain_even_with_no_modifiers(self):
        effs = [_eff("Marker", [
            _mod(None, 0, "untyped", predicate={"until": "next_strike"})
        ], id="t", tags=["mental"])]
        kept = consume_triggered(effs, event="next_strike")
        assert [e["id"] for e in kept] == ["t"]
        assert kept[0]["modifiers"] == []

    def test_effect_kept_when_save_block_remains(self):
        effs = [_eff("Saved", [
            _mod(None, 0, "untyped", predicate={"until": "next_strike"})
        ], id="s", save={"type": "will", "dc": 20})]
        kept = consume_triggered(effs, event="next_strike")
        assert [e["id"] for e in kept] == ["s"]

    def test_sure_strike_catalog_consumed_on_next_strike(self):
        ss = instantiate_effect("sure_strike", effect_id="ss", current_round=1)
        # Sure Strike: marker modifier (until=next_strike) + consumes_on
        kept = consume_triggered([ss], event="next_strike")
        assert kept == []

    def test_non_matching_event_leaves_effect_intact(self):
        ss = instantiate_effect("sure_strike", effect_id="ss", current_round=1)
        kept = consume_triggered([ss], event="end_of_turn")
        assert len(kept) == 1


# ══════════════════════════════════════════════════════════════════════
# find_reaction_triggers — reaction-window event matching
# ══════════════════════════════════════════════════════════════════════
class TestFindReactionTriggers:
    def test_empty_list_returns_empty(self):
        assert find_reaction_triggers(None, event="on_damaged") == []
        assert find_reaction_triggers([], event="on_damaged") == []

    def test_shield_matches_on_damaged(self):
        shield = instantiate_effect("shield", effect_id="sh", current_round=1)
        out = find_reaction_triggers([shield], event="on_damaged")
        assert len(out) == 1
        assert out[0]["trigger"]["reaction_name"] == "Shield Block"
        assert out[0]["trigger"]["event"] == "on_damaged"
        assert out[0]["trigger"]["event_label"]  # mapped label present

    def test_shield_does_not_match_other_event(self):
        shield = instantiate_effect("shield", effect_id="sh", current_round=1)
        assert find_reaction_triggers([shield], event="on_targeted") == []

    def test_reaction_without_event_matches_any_event(self):
        # permissive: a reaction modifier with no `event` is an
        # always-available window.
        eff = _eff("AnyReact", [_mod(None, 0, "untyped",
                   predicate={"scope": "reaction"})], id="r")
        assert len(find_reaction_triggers([eff], event="on_damaged")) == 1
        assert len(find_reaction_triggers([eff], event="on_struck")) == 1

    def test_suppressed_effect_does_not_fire_reaction(self):
        shield = instantiate_effect("shield", effect_id="sh", current_round=1)
        shield["suppressed"] = True
        assert find_reaction_triggers([shield], event="on_damaged") == []

    def test_one_trigger_per_effect_even_with_multiple_reaction_mods(self):
        eff = _eff("Two", [
            _mod(None, 0, "untyped", predicate={"scope": "reaction", "event": "on_damaged"}),
            _mod(None, 0, "untyped", predicate={"scope": "reaction", "event": "on_damaged"}),
        ], id="t")
        assert len(find_reaction_triggers([eff], event="on_damaged")) == 1

    def test_non_reaction_modifier_ignored(self):
        eff = _eff("Plain", [_mod("ac", 1, "status")], id="p")
        assert find_reaction_triggers([eff], event="on_damaged") == []

    def test_nimble_dodge_matches_on_targeted(self):
        nd = instantiate_effect("nimble_dodge", effect_id="nd", current_round=1)
        out = find_reaction_triggers([nd], event="on_targeted")
        assert len(out) == 1
        assert out[0]["trigger"]["reaction_name"] == "Nimble Dodge"

    def test_liberating_step_matches_on_ally_struck(self):
        ls = instantiate_effect("liberating_step", effect_id="ls", current_round=1)
        assert len(find_reaction_triggers([ls], event="on_ally_struck")) == 1
        assert find_reaction_triggers([ls], event="on_damaged") == []

    def test_trigger_payload_carries_effect_identity(self):
        shield = instantiate_effect("shield", effect_id="sh", current_round=1)
        out = find_reaction_triggers([shield], event="on_damaged")[0]
        assert out["id"] == "sh"
        assert out["name"]  # effect name surfaced


# ══════════════════════════════════════════════════════════════════════
# expire_round_effects — round-duration falloff
# ══════════════════════════════════════════════════════════════════════
class TestExpireRoundEffects:
    def test_effect_expires_at_its_round(self):
        effs = [{"id": "a", "duration": {"expires_at_round": 5}}]
        kept, expired = expire_round_effects(effs, current_round=5)
        assert [e["id"] for e in expired] == ["a"]
        assert kept == []

    def test_effect_expires_when_round_exceeds(self):
        effs = [{"id": "a", "duration": {"expires_at_round": 3}}]
        kept, expired = expire_round_effects(effs, current_round=7)
        assert [e["id"] for e in expired] == ["a"]

    def test_effect_persists_before_expiry(self):
        effs = [{"id": "a", "duration": {"expires_at_round": 10}}]
        kept, expired = expire_round_effects(effs, current_round=5)
        assert [e["id"] for e in kept] == ["a"]
        assert expired == []

    def test_permanent_effect_never_expires(self):
        effs = [{"id": "p", "duration": {}}]  # no expires_at_round
        kept, expired = expire_round_effects(effs, current_round=999)
        assert [e["id"] for e in kept] == ["p"]
        assert expired == []

    def test_none_expiry_never_expires(self):
        effs = [{"id": "p", "duration": {"expires_at_round": None}}]
        kept, expired = expire_round_effects(effs, current_round=999)
        assert [e["id"] for e in kept] == ["p"]

    def test_missing_duration_key_never_expires(self):
        effs = [{"id": "x"}]
        kept, expired = expire_round_effects(effs, current_round=10)
        assert [e["id"] for e in kept] == ["x"]

    def test_partition_mixed_list(self):
        effs = [
            {"id": "gone", "duration": {"expires_at_round": 2}},
            {"id": "stay", "duration": {"expires_at_round": 9}},
            {"id": "perm", "duration": {}},
        ]
        kept, expired = expire_round_effects(effs, current_round=5)
        assert {e["id"] for e in kept} == {"stay", "perm"}
        assert {e["id"] for e in expired} == {"gone"}


# ══════════════════════════════════════════════════════════════════════
# instantiate_effect — catalog -> per-token record
# ══════════════════════════════════════════════════════════════════════
class TestInstantiateEffect:
    def test_unknown_key_returns_none(self):
        assert instantiate_effect("does_not_exist", effect_id="x") is None

    def test_minutes_duration_is_ten_rounds_each(self):
        b = instantiate_effect("bless", effect_id="x", current_round=3)
        # bless = 1 minute = 10 rounds; 3 + 10 = 13
        assert b["duration"]["expires_at_round"] == 13

    def test_rounds_duration_added_directly(self):
        h = instantiate_effect("haste", effect_id="y", current_round=2)
        assert h["duration"]["expires_at_round"] == 3

    def test_permanent_has_no_expiry(self):
        ma = instantiate_effect("mage_armor", effect_id="z", current_round=2)
        assert ma["duration"]["expires_at_round"] is None

    def test_duration_override_applied(self):
        b = instantiate_effect(
            "bless", effect_id="x", current_round=0,
            duration_override={"type": "rounds", "value": 2},
        )
        assert b["duration"]["type"] == "rounds"
        assert b["duration"]["expires_at_round"] == 2

    def test_applied_at_round_recorded(self):
        b = instantiate_effect("bless", effect_id="x", current_round=7)
        assert b["applied_at_round"] == 7

    def test_caster_attached(self):
        b = instantiate_effect("bless", effect_id="x", caster="Go'el")
        assert b["caster"] == "Go'el"

    def test_save_dc_injected_into_save_block(self):
        slow = instantiate_effect("slow", effect_id="sl", save_dc=24)
        assert slow["save"]["dc"] == 24
        assert slow["suppressed"] is False

    def test_save_dc_default_zero_when_unset(self):
        slow = instantiate_effect("slow", effect_id="sl")
        assert slow["save"]["dc"] == 0

    def test_custom_uses_custom_modifiers_and_name(self):
        cust = instantiate_effect(
            "custom", effect_id="c",
            custom_modifiers=[_mod("ac", 3, "untyped")],
            custom_name="MyBuff",
        )
        assert cust["name"] == "MyBuff"
        assert cust["modifiers"] == [_mod("ac", 3, "untyped")]

    def test_non_custom_ignores_custom_modifiers(self):
        # custom_modifiers only honored for the 'custom' catalog key
        b = instantiate_effect(
            "bless", effect_id="x",
            custom_modifiers=[_mod("ac", 99, "untyped")],
        )
        assert b["modifiers"] == EFFECT_CATALOG["bless"]["modifiers"]

    def test_consumes_on_carried_for_sure_strike(self):
        ss = instantiate_effect("sure_strike", effect_id="ss")
        assert ss.get("consumes_on") == "next_strike"

    def test_no_save_block_means_no_suppressed_flag(self):
        b = instantiate_effect("bless", effect_id="x")
        assert "save" not in b
        assert "suppressed" not in b


# ══════════════════════════════════════════════════════════════════════
# materialize_chains — follow-up effect instantiation
# ══════════════════════════════════════════════════════════════════════
class TestMaterializeChains:
    def test_no_source_id_returns_empty(self):
        assert materialize_chains({}, current_round=1) == []

    def test_template_without_chains_returns_empty(self):
        bless = instantiate_effect("bless", effect_id="b", current_round=1)
        # bless's source_id resolves a template but it has no 'chains'
        assert materialize_chains(bless, current_round=1) == []

    def test_unknown_source_id_returns_empty(self):
        assert materialize_chains({"source_id": "nope"}, current_round=1) == []


# ══════════════════════════════════════════════════════════════════════
# catalog_list — GM dropdown summaries
# ══════════════════════════════════════════════════════════════════════
class TestCatalogList:
    def test_returns_one_entry_per_catalog_key(self):
        cl = catalog_list()
        assert len(cl) == len(EFFECT_CATALOG)

    def test_entries_carry_summary_fields(self):
        cl = catalog_list()
        sample = cl[0]
        for field in ("key", "name", "source", "tags", "duration",
                      "description", "modifier_count"):
            assert field in sample

    def test_sorted_by_source_then_name(self):
        cl = catalog_list()
        keys = [(e["source"], e["name"]) for e in cl]
        assert keys == sorted(keys)

    def test_modifier_count_matches_template(self):
        cl = catalog_list()
        by_key = {e["key"]: e for e in cl}
        assert by_key["heroism"]["modifier_count"] == len(
            EFFECT_CATALOG["heroism"]["modifiers"]
        )

    def test_every_catalog_key_present(self):
        cl = catalog_list()
        assert {e["key"] for e in cl} == set(EFFECT_CATALOG.keys())


# ══════════════════════════════════════════════════════════════════════
# Back-compat shims
# ══════════════════════════════════════════════════════════════════════
class TestCompatShims:
    def test_compute_effects_returns_effective_only(self):
        out = compute_effects({"frightened": 1}, {"attack": 10})
        assert out == {"attack": 9}

    def test_list_active_effects_returns_breakdown(self):
        rows = list_active_effects({"frightened": 1})
        # one breakdown row per condition modifier (frightened touches
        # 9 stats); each row carries an 'applied' flag.
        assert len(rows) == len(PF2E_CONDITION_EFFECTS["frightened"])
        assert all("applied" in r for r in rows)


# ══════════════════════════════════════════════════════════════════════
# Fixture-backed integration: engine layers on a real PC's stat numbers
# ══════════════════════════════════════════════════════════════════════
class TestFixtureIntegration:
    """The engine is pure (operates on plain dicts), but combat code
    feeds it a PC's derived stat numbers. Confirm a committed fixture's
    AC/save numbers flow through the resolver correctly. CI-safe: builds
    the Character from a committed fixture, never from party_data."""

    @pytest.fixture(scope="class")
    def kyle(self):
        path = _FIXTURES / "kyle_l10.json"
        if not path.exists():
            pytest.skip("kyle_l10 fixture missing")
        from app import Character
        return Character(json.loads(path.read_text()), file_path=str(path))

    def test_frightened_lowers_a_real_pc_ac_by_value(self, kyle):
        base_ac = int(kyle.ac)
        r = compute_token_stats({"frightened": 2}, [], {"ac": base_ac})
        assert r["effective"]["ac"] == base_ac - 2

    def test_inspire_defense_and_shield_stack_on_real_ac(self, kyle):
        base_ac = int(kyle.ac)
        inspire = instantiate_effect("inspire_defense", effect_id="i", current_round=1)
        shield = instantiate_effect("shield", effect_id="s", current_round=1)
        r = compute_token_stats({}, [inspire, shield], {"ac": base_ac})
        # +1 status (Inspire Defense) + +1 circumstance (Shield) -> +2
        assert r["effective"]["ac"] == base_ac + 2
