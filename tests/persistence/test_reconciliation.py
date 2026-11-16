"""PE-13c: the max-HP and pool reconciliation hook."""

from __future__ import annotations

import ast
import inspect
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.commands import reconciliation
from core.commands.reconciliation import (
    InEncounter,
    Reservation,
    ReservationBlocked,
    Snapshot,
    apply,
    debt_after_heal,
    preview,
    reconcile,
    reconcile_hp,
    refuse_publish_in_encounter,
)
from core.persistence.models import Campaign, CampaignStateAuthority, Character, User
from core.persistence.state_repositories import EncounterStateRepository

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}


# --------------------------------------------------------------------------
# synthetic adapter: a build is a dict, items is a list of dicts


class Adapter:
    """Max HP = build["hp"] + equipped item bonuses; pools come from the build."""

    def effective_max(self, build, play, items):
        return build["hp"] + sum(i.get("hp", 0) for i in items or ())

    def is_dead(self, play):
        return bool(play.get("dead"))

    def pools(self, build, play, items):
        pools = dict(build.get("pools", {}))
        for item in items or ():
            for pool, extra in item.get("pools", {}).items():
                pools[pool] = pools.get(pool, 0) + extra
        return pools

    def invalid_choices(self, build, play):
        return [{"id": c, "slot": c} for c in build.get("invalid", ())]

    def orphaned_effects(self, build, play, items, removed_pools):
        return [{"id": e["id"], "pool": e.get("pool")} for e in play.get("effects", ())
                if e.get("pool") in removed_pools
                or (e.get("source") and e["source"] not in build.get("sources", ()))]


ADAPTER = Adapter()


def run(before, after, *, hp=10, play=None, used=None, reservations=(), user=False,
        before_items=None, after_items=None):
    kwargs = dict(before=Snapshot(before, before_items), after=Snapshot(after, after_items),
                  play=play or {}, hp=hp, used=used or {}, reservations=reservations,
                  user_initiated=user)
    try:
        result = apply(ADAPTER, **kwargs)
    except ReservationBlocked as refused:
        # Preview refuses exactly as the commit does.
        with pytest.raises(ReservationBlocked) as previewed:
            preview(ADAPTER, **kwargs)
        assert previewed.value.action_ids == refused.action_ids
        raise
    assert preview(ADAPTER, **kwargs) == list(result.entries)  # preview == commit, always
    return result


def hp_build(maximum, **extra):
    return {"hp": maximum, **extra}


# --------------------------------------------------------------------------
# HP and debt (C:176-194)


def test_fall_below_then_rise_back_cancels_exactly():
    assert reconcile_hp(3, 0, 45, 37, False) == (1, 6)
    assert reconcile_hp(1, 6, 37, 45, False) == (3, 0)


def test_worked_example_2_publish_raises_max_and_keeps_damage():
    result = run(hp_build(38), hp_build(45), hp=24)
    assert (result.hp, result.hp_floor_debt) == (31, 0)
    assert result.entries == ({"type": "hp", "hp_before": 24, "hp_after": 31,
                               "debt_before": 0, "debt_after": 0,
                               "effmax_before": 38, "effmax_after": 45},)


def test_round_trip_debt_examples():
    # 1/37 with debt 6 healed to 37/37 (debt stays), then re-equipped: 39/45.
    assert debt_after_heal(37, 6, rest_to_max=False) == 6
    assert reconcile_hp(37, 6, 37, 45, False) == (39, 0)
    # 20/37 with debt 0, re-equipped: 28/45.
    assert reconcile_hp(20, 0, 37, 45, False) == (28, 0)


def test_hp_above_the_old_maximum_is_capped_at_the_new_one():
    # A stored HP above its maximum (an imported over-max value) never survives a change.
    assert reconcile_hp(50, 0, 45, 47, False) == (47, 0)


def test_zero_hp_clears_debt_and_stays_zero():
    assert reconcile_hp(0, 6, 37, 45, False) == (0, 0)
    assert reconcile_hp(0, 6, 45, 37, False) == (0, 0)


def test_dead_changes_nothing():
    assert reconcile_hp(0, 4, 45, 37, True) == (0, 4)
    result = run(hp_build(45), hp_build(37), hp=0,
                 play={"dead": True, "hp_floor_debt": 4})
    assert (result.hp, result.hp_floor_debt, result.entries) == (0, 4, ())


def test_temp_hp_and_other_play_keys_are_untouched():
    play = {"temp_hp": 5, "hp_floor_debt": 0, "conditions": ["unconscious"]}
    result = run(hp_build(45), hp_build(37), hp=3, play=play)
    updates = result.play_updates(play)
    assert updates == {"hp_floor_debt": 6, "choice_prompts": []}
    assert play == {"temp_hp": 5, "hp_floor_debt": 0, "conditions": ["unconscious"]}


def test_hp_entry_only_when_something_moves():
    assert run(hp_build(45), hp_build(45), hp=20).entries == ()


@pytest.mark.parametrize("hp", [37, 3, 0])
def test_debt_is_not_charged_when_the_maximum_does_not_move(hp):
    # Example C: 37/37 with debt 6 publishes a choice-only change, or an
    # effect expiring lowers a pool; the debt waits for the maximum to rise.
    play = {"hp_floor_debt": 6}
    result = run(hp_build(37, pools={"ki": 3}), hp_build(37, pools={"ki": 2},
                                                         invalid=["feat-9"]),
                 hp=hp, play=play, used={"ki": 2})
    assert (result.hp, result.hp_floor_debt) == (hp, 6)
    assert "hp" not in [e["type"] for e in result.entries]
    assert reconcile_hp(result.hp, result.hp_floor_debt, 37, 45, False) == (
        (39, 0) if hp == 37 else (5, 0) if hp == 3 else (0, 0))


def test_example_c_variant_kept_debt_versus_cleared_debt():
    assert reconcile_hp(20, 6, 37, 45, False) == (22, 0)  # debt kept
    assert reconcile_hp(20, 0, 37, 45, False) == (28, 0)  # debt cleared at 0 HP


@pytest.mark.parametrize("seed", range(40))
def test_unequip_and_reequip_never_heals(seed):
    rng = random.Random(seed)
    start_max = rng.randint(1, 80)
    start_hp = rng.randint(1, start_max)
    hp, debt, maximum = start_hp, 0, start_max
    for _ in range(rng.randint(1, 12)):
        new_max = rng.randint(1, 120)
        hp, debt = reconcile_hp(hp, debt, maximum, new_max, False)
        maximum = new_max
        assert 1 <= hp <= maximum and debt >= 0
        # Never above what the original damage allows at this maximum (the
        # 1-HP floor is paid for by debt).
        assert hp <= max(1, start_hp + (maximum - start_max))
        assert hp - debt - maximum == start_hp - start_max  # damage stays counted
    hp, debt = reconcile_hp(hp, debt, maximum, start_max, False)
    assert (hp, debt) == (start_hp, 0)


def test_debt_cleared_by_rest_to_max_and_at_zero_not_by_partial_heal():
    assert debt_after_heal(30, 6, rest_to_max=False) == 6
    assert debt_after_heal(37, 6, rest_to_max=True) == 0
    assert debt_after_heal(0, 6, rest_to_max=False) == 0


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3", None])
def test_counts_must_be_non_negative_integers(bad):
    with pytest.raises(ValueError):
        reconcile_hp(bad, 0, 10, 10, False)
    with pytest.raises(ValueError):
        reconcile_hp(5, bad, 10, 10, False)


# --------------------------------------------------------------------------
# pools and reservations (C:196-212)

R1 = Reservation("slot1", "act-1", 101, 1, ("ana",))
R2 = Reservation("slot1", "act-2", 105, 1, ("g1", "g2"))
R3 = Reservation("slot1", "act-3", 103, 1, ("bo",))
F1 = Reservation("focus", "act-9", 99, 1, ())


@pytest.mark.parametrize("override", [None, {"reason": "gm"}])
def test_user_initiated_lowering_below_reserved_is_refused(override):
    # override is not an input of the hook at all: it cannot reach this rule.
    with pytest.raises(ReservationBlocked) as info:
        run(hp_build(10, pools={"slot1": 4}), hp_build(10, pools={"slot1": 2}),
            used={"slot1": 1}, reservations=[R2, R1, R3], user=True)
    exc = info.value
    assert (exc.code, exc.http_status) == ("rule_violation", 422)
    assert exc.action_ids == ("act-1", "act-3", "act-2")  # declare_seq order


def test_user_initiated_removal_of_a_reserved_pool_is_refused_naming_every_pool():
    with pytest.raises(ReservationBlocked) as info:
        run(hp_build(10, pools={"slot1": 4, "focus": 1}), hp_build(10, pools={"slot1": 1}),
            used={"slot1": 0}, reservations=[R1, R2, F1], user=True)
    assert info.value.action_ids == ("act-9", "act-1", "act-2")


def test_only_the_blocking_actions_are_named_and_each_once():
    both = [Reservation("slot1", "act-5", 107, 1, ()), Reservation("focus", "act-5", 107, 1, ())]
    with pytest.raises(ReservationBlocked) as info:
        run(hp_build(10, pools={"slot1": 4, "focus": 3, "ki": 1}),
            hp_build(10, pools={"slot1": 1, "focus": 0, "ki": 1}),
            used={"slot1": 0, "focus": 0, "ki": 0},
            reservations=[R1, R2, Reservation("ki", "act-8", 98, 1, ()), *both], user=True)
    # act-8 (ki still fits) is not named; act-5 blocks two pools but appears once.
    assert info.value.action_ids == ("act-1", "act-2", "act-5")


def test_an_action_released_from_two_pools_is_one_released_action():
    both = [Reservation("slot1", "act-5", 107, 1, ("g1",)),
            Reservation("focus", "act-5", 107, 1, ("g1",))]
    result = run(hp_build(10, pools={"slot1": 1, "focus": 1}),
                 hp_build(10, pools={"slot1": 0, "focus": 0}),
                 used={"slot1": 0, "focus": 0}, reservations=both)
    assert len(result.released) == 2 and result.released_actions == ("act-5",)


def test_user_initiated_lowering_that_still_fits_passes_and_unreserved_pool_clamps():
    result = run(hp_build(10, pools={"slot1": 4, "spare": 3}),
                 hp_build(10, pools={"slot1": 3, "spare": 1}),
                 used={"slot1": 1, "spare": 3}, reservations=[R1, R2], user=True)
    assert result.released == ()
    assert result.used == {"slot1": 1, "spare": 1}
    assert result.entries == ({"type": "pool_clamped", "pool": "spare", "max_before": 3,
                               "max_after": 1, "used_before": 3, "used_after": 1},)


def test_automatic_lowering_releases_newest_first_then_clamps():
    result = run(hp_build(10, pools={"slot1": 4}), hp_build(10, pools={"slot1": 1}),
                 used={"slot1": 2}, reservations=[R1, R2, R3])
    assert [r.action_id for r in result.released] == ["act-2", "act-3", "act-1"]
    assert result.entries == (
        {"type": "reservation_released", "pool": "slot1", "action_id": "act-2",
         "declare_seq": 105, "amount": 1, "targets": ["g1", "g2"],
         "reason": "reservation_invalidated"},
        {"type": "reservation_released", "pool": "slot1", "action_id": "act-3",
         "declare_seq": 103, "amount": 1, "targets": ["bo"],
         "reason": "reservation_invalidated"},
        {"type": "reservation_released", "pool": "slot1", "action_id": "act-1",
         "declare_seq": 101, "amount": 1, "targets": ["ana"],
         "reason": "reservation_invalidated"},
        {"type": "pool_clamped", "pool": "slot1", "max_before": 4, "max_after": 1,
         "used_before": 2, "used_after": 1},
    )
    assert result.used == {"slot1": 1}


def test_automatic_lowering_stops_releasing_once_it_fits():
    result = run(hp_build(10, pools={"slot1": 4}), hp_build(10, pools={"slot1": 3}),
                 used={"slot1": 1}, reservations=[R1, R2, R3])
    assert [r.action_id for r in result.released] == ["act-2"]
    assert result.used == {"slot1": 1}
    assert [e["type"] for e in result.entries] == ["reservation_released"]


def test_automatic_removal_of_a_reserved_pool_releases_all():
    play = {"effects": [{"id": "fx-1", "pool": "slot1"}]}
    result = run(hp_build(10, pools={"slot1": 4}), hp_build(10, pools={}), play=play,
                 used={"slot1": 1}, reservations=[R1, R2])
    assert [r.action_id for r in result.released] == ["act-2", "act-1"]
    assert result.dropped_pools == ("slot1",) and result.used == {}
    assert result.entries[-2:] == (
        {"type": "pool_removed", "pool": "slot1", "used": 1, "effects": ["fx-1"]},
        {"type": "effect_ended", "effect_id": "fx-1", "pool": "slot1",
         "effect": {"id": "fx-1", "pool": "slot1"}},
    )


def test_removed_unreserved_pool_is_dropped_with_its_dependent_effect():
    play = {"effects": [{"id": "fx-2", "pool": "ki"}, {"id": "fx-3", "pool": "focus"}]}
    result = run(hp_build(10, pools={"ki": 3, "focus": 2}), hp_build(10, pools={"focus": 2}),
                 play=play, used={"ki": 2}, user=True)
    assert result.dropped_pools == ("ki",) and result.ended_effects == ("fx-2",)
    assert result.used == {"focus": 0}
    assert result.entries == (
        {"type": "pool_removed", "pool": "ki", "used": 2, "effects": ["fx-2"]},
        {"type": "effect_ended", "effect_id": "fx-2", "pool": "ki",
         "effect": {"id": "fx-2", "pool": "ki"}},
    )


def test_new_pool_starts_unused_and_raising_a_pool_lists_nothing():
    result = run(hp_build(10, pools={"slot1": 2}), hp_build(10, pools={"slot1": 3, "ki": 1}),
                 used={"slot1": 2}, reservations=[R1], user=True)
    assert result.used == {"slot1": 2, "ki": 0} and result.entries == ()


def test_pool_inputs_naming_unknown_pools_are_refused():
    with pytest.raises(ValueError):
        run(hp_build(10, pools={}), hp_build(10, pools={}), used={"ghost": 1})
    with pytest.raises(ValueError):
        run(hp_build(10, pools={}), hp_build(10, pools={}), reservations=[R1])


def test_item_change_moves_hp_and_pools_together():
    # Unequipping a +8 HP, +1 slot item: HP debt, release and clamp in one list.
    staff = {"hp": 8, "pools": {"slot1": 1}}
    result = run(hp_build(37, pools={"slot1": 2}), hp_build(37, pools={"slot1": 2}), hp=3,
                 before_items=[staff], after_items=[], used={"slot1": 2},
                 reservations=[R1])
    assert [e["type"] for e in result.entries] == ["hp", "reservation_released"]
    assert (result.hp, result.hp_floor_debt, result.used) == (1, 6, {"slot1": 2})


# --------------------------------------------------------------------------
# effects and choices (C:215-217)


def test_orphaned_effects_end_with_an_entry():
    play = {"effects": [{"id": "fx-4", "source": "feat:rage"}, {"id": "fx-5", "source": "x"}]}
    result = run(hp_build(10, sources=["x"]), hp_build(10, sources=["x"]), play=play)
    assert result.ended_effects == ("fx-4",)
    assert result.entries == ({"type": "effect_ended", "effect_id": "fx-4", "pool": None,
                               "effect": {"id": "fx-4", "pool": None}},)


def test_invalid_choices_become_prompts_and_nothing_is_removed():
    existing = {"kind": "choice", "origin": "reconciliation", "choice": {"id": "feat-2"}}
    play = {"choice_prompts": [existing], "choices": ["feat-1", "feat-2"]}
    result = run(hp_build(10), hp_build(10, invalid=["feat-1", "feat-2"]), play=play)
    prompt = {"kind": "choice", "origin": "reconciliation",
              "choice": {"id": "feat-1", "slot": "feat-1"}}
    assert result.entries == ({"type": "choice_prompt", "prompt": prompt},)
    assert result.play_updates(play)["choice_prompts"] == [existing, prompt]
    assert play["choices"] == ["feat-1", "feat-2"]  # never silently removed


def test_full_entry_order_and_preview_equals_apply():
    play = {"effects": [{"id": "fx-6", "pool": "ki"}, {"id": "fx-7", "source": "gone"}]}
    before = hp_build(45, pools={"ki": 2, "slot1": 3}, sources=["gone"])
    after = hp_build(37, pools={"slot1": 1}, invalid=["feat-3"], sources=[])
    kwargs = dict(before=Snapshot(before), after=Snapshot(after), play=play, hp=3,
                  used={"ki": 1, "slot1": 1}, reservations=[R1, R2], user_initiated=False)
    entries = preview(ADAPTER, **kwargs)
    assert entries == list(apply(ADAPTER, **kwargs).entries)
    assert [e["type"] for e in entries] == [
        "hp", "pool_removed", "reservation_released", "reservation_released",
        "effect_ended", "effect_ended", "choice_prompt"]
    assert entries[1] == {"type": "pool_removed", "pool": "ki", "used": 1,
                          "effects": ["fx-6"]}  # not the source-orphaned fx-7
    # Mutating the preview does not change a later commit.
    entries[0]["hp_after"] = 999
    assert apply(ADAPTER, **kwargs).entries[0]["hp_after"] == 1


# --------------------------------------------------------------------------
# in_encounter (C:218-219), through PE-13a on SQLite


def _seed(database):
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm",
                         display_name="gm", password_hash="h", created_at=NOW))
        session.flush()
        session.add(Campaign(id="A", slug="A", name="A", system="pf2e",
                             created_by_user_id="gm", created_at=NOW,
                             settings={"rules": {"profile": "pf2e", "content": CONTENT}}))
        session.flush()
        for character_id in ("ana", "bo"):
            session.add(Character(id=character_id, campaign_id="A", system="pf2e",
                                  display_name=character_id, legacy_storage="party_data",
                                  legacy_file=character_id + ".json",
                                  content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        session.add(CampaignStateAuthority(campaign_id="A", authority="db", stream_epoch=1,
                                           changed_at=NOW))
        session.flush()
        encounters = EncounterStateRepository(session)
        encounters.insert("A", "e1", rules_profile="pf2e", content=CONTENT)
        encounters.add_combatant("A", "e1", read_revision=1, combatant_id="c1", kind="pc",
                                 character_id="ana")
        encounters.insert("A", "e0", rules_profile="pf2e", content=CONTENT, status="ended")
        encounters.add_combatant("A", "e0", read_revision=1, combatant_id="c2", kind="pc",
                                 character_id="bo")


def test_publish_in_the_active_encounter_is_refused_unless_overridden(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.session() as session:
        with pytest.raises(InEncounter) as info:
            refuse_publish_in_encounter(session, "A", "ana", None)
        exc = info.value
        assert (exc.code, exc.http_status, exc.character_id) == ("in_encounter", 422, "ana")
        refuse_publish_in_encounter(session, "A", "ana", {"reason": "gm"})
        refuse_publish_in_encounter(session, "A", "bo", None)  # only in an ended encounter


# --------------------------------------------------------------------------
# module boundaries


def test_reconciliation_imports_nothing_from_pe13b_or_pe14():
    tree = ast.parse(Path(inspect.getfile(reconciliation)).read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    assert not any(m.startswith((".", "core.commands")) for m in modules), modules
    assert modules <= {"__future__", "copy", "dataclasses", "typing", "sqlalchemy.orm",
                       "core.persistence.state_repositories"}, modules


def test_commands_package_has_no_reexports():
    init = Path(inspect.getfile(reconciliation)).with_name("__init__.py")
    assert init.read_bytes().replace(b"\r\n", b"\n") == (
        b'"""Command pipeline. Import submodules directly; this package re-exports '
        b'nothing."""\n')
