"""Cosmere role defeat and action-economy behavior."""
from systems.cosmere.combat import (
    defeat_outcome,
    turn_permissions,
    turn_schedule,
)


def test_ordinary_minion_and_boss_defeat_rules():
    assert defeat_outcome("rival", 0, suffered_injury=True)["defeated"] is True
    assert defeat_outcome("ordinary", 0, suffered_injury=True)["defeated"] is True
    assert defeat_outcome("minion", 7, suffered_injury=True)["defeated"] is True
    assert defeat_outcome("boss", 0, suffered_injury=True)["defeated"] is True
    assert defeat_outcome("boss", 0, suffered_injury=True,
                          significant_npc_injuries=True)["defeated"] is False


def test_nonlethal_defeat_and_explicit_injury_spiral():
    nonlethal = defeat_outcome("rival", 0, suffered_injury=True, nonlethal=True)
    assert nonlethal == {"defeated": True, "unconscious": True, "dead": False,
                         "use_injury_spiral": False}
    significant = defeat_outcome("rival", 0, suffered_injury=True,
                                 significant_npc_injuries=True)
    assert significant["use_injury_spiral"] is True and significant["unconscious"] is True


def test_minion_injury_defeat_cannot_be_overridden_by_significant_npc_flag():
    outcome = defeat_outcome(
        "minion", 7, suffered_injury=True, significant_npc_injuries=True)
    assert outcome == {
        "defeated": True,
        "unconscious": False,
        "dead": True,
        "use_injury_spiral": False,
    }


def test_boss_gets_fast_and_slow_turn_slots():
    schedule = turn_schedule([
        {"id": "hero", "role": "hero", "is_pc": True, "choice": "slow", "speed": 2},
        {"id": "boss", "role": "boss", "is_pc": False, "choice": "slow", "speed": 3},
    ])
    boss = [slot for slot in schedule if slot["id"] == "boss"]
    assert [slot["choice"] for slot in boss] == ["fast", "slow"]
    assert boss[0]["phase"] == "fast_npc" and boss[1]["phase"] == "slow_npc"


def test_stunned_surprised_unconscious_and_disoriented_permissions():
    assert turn_permissions("fast", {"stunned": True}) == {
        "choice": "fast", "actions": 0, "reaction": False}
    assert turn_permissions("fast", {"surprised": True}) == {
        "choice": "slow", "actions": 2, "reaction": False}
    assert turn_permissions("fast", {"unconscious": True}) == {
        "choice": "slow", "actions": 0, "reaction": False}
    assert turn_permissions("slow", {"disoriented": True}) == {
        "choice": "slow", "actions": 3, "reaction": False}
