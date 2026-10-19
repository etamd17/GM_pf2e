"""E-2: condition changes from the VTT go through each system's own tracker
engine, bound to the value the GM page showed.

The engines themselves are replaced by recorders here; the browser harness
runs the real PF2e one end to end.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module
import systems.cosmere


@pytest.fixture
def tracker(monkeypatch):
    calls = []
    skeleton = SimpleNamespace(instance_id="a1", system="pf2e", conditions={"frightened": 2, "prone": True})
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [skeleton])
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    campaign = {"id": "camp_a", "system": "pf2e"}
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: campaign)

    def pf2e(instance_id, condition, action, rounds=0):
        calls.append(("pf2e", instance_id, condition, action, rounds, app_module._LIVE_CAMPAIGN_DISPATCH_LOCK._is_owned()))
        value = skeleton.conditions.get(condition, 0)
        if condition in app_module.VALUED_CONDITIONS:
            skeleton.conditions[condition] = {"add": value + 1, "decrease": max(0, value - 1), "remove": 0}[action]
        else:
            skeleton.conditions[condition] = action == "add"
        return True

    def cosmere(instance_id, condition, action="toggle", effect_data=None):
        calls.append(("cosmere", instance_id, condition, action))
        skeleton.conditions[condition] = action == "add"
        return skeleton

    monkeypatch.setattr(app_module, "_apply_condition_change", pf2e)
    monkeypatch.setattr(app_module, "_apply_cosmere_condition_change", cosmere)
    return SimpleNamespace(calls=calls, skeleton=skeleton, campaign=campaign)


def change(condition="frightened", operation="add", expected=2, rounds=0, actor="cmb_a1", campaign="camp_a"):
    return app_module._vtt_condition_change(campaign, actor, condition, operation, expected, rounds)


def test_a_matching_request_goes_through_the_tracker_under_the_lock(tracker):
    assert change(rounds=3) == ("ok", {"value": 3})
    assert tracker.calls == [("pf2e", "a1", "frightened", "add", 3, True)]


def test_a_duplicate_click_is_stale_and_stacks_nothing(tracker):
    assert change()[0] == "ok"
    assert change() == ("stale", {"value": 3})
    assert len(tracker.calls) == 1


def test_flags_count_as_one_and_absent_as_zero(tracker):
    assert change("prone", "remove", expected=1) == ("ok", {"value": 0})
    assert change("off_guard", "add", expected=0) == ("ok", {"value": 1})
    assert change("off_guard", "add", expected=0)[0] == "stale"


def test_lowering_a_valued_condition(tracker):
    assert change("frightened", "decrease", expected=2) == ("ok", {"value": 1})


def test_another_campaign_is_never_activated(tracker):
    assert change(campaign="camp_b") == ("not-live", {})
    assert tracker.calls == []


def test_dnd5e_is_refused(tracker):
    tracker.campaign["system"] = "dnd5e"
    assert change()[0] == "unsupported"
    assert tracker.calls == []


def test_an_actor_outside_the_encounter_is_refused(tracker):
    assert change(actor="cmb_gone") == ("not-in-encounter", {})
    assert change(actor="chr_valeros") == ("not-in-encounter", {})
    assert tracker.calls == []


def test_systems_never_share_conditions(tracker):
    # A PF2e condition name in a Cosmere campaign, a Cosmere one in PF2e, and a
    # combatant of the other system: all refused.
    assert change("determined", expected=0) == ("invalid", {"reason": "condition"})
    tracker.campaign["system"] = "cosmere"
    assert change() == ("invalid", {"reason": "system"})
    tracker.skeleton.system = "cosmere"
    assert change("frightened") == ("invalid", {"reason": "condition"})
    assert tracker.calls == []


def test_cosmere_goes_through_its_own_engine(tracker):
    tracker.campaign["system"] = "cosmere"
    tracker.skeleton.system = "cosmere"
    assert change("determined", "add", expected=0) == ("ok", {"value": 1})
    assert tracker.calls == [("cosmere", "a1", "determined", "add")]
    # No lowering a Cosmere condition and no tracker timer: those are PF2e's.
    assert change("determined", "decrease", expected=1) == ("invalid", {"reason": "operation"})
    assert change("focused", "add", expected=0, rounds=2) == ("invalid", {"reason": "operation"})


def test_a_refusal_inside_the_tracker_is_reported(tracker, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("actor is not mutable")

    monkeypatch.setattr(app_module, "_apply_condition_change", refuse)
    assert change() == ("blocked", {"reason": "PermissionError"})
    assert tracker.skeleton.conditions["frightened"] == 2


def test_an_unknown_operation_is_refused(tracker):
    assert change(operation="toggle")[0] == "invalid"
    assert tracker.calls == []


# --- the client's condition lists match the server's -------------------------------------------

CLIENT = Path(__file__).resolve().parents[2] / "frontend" / "vtt" / "src" / "app" / "actions" / "conditions.ts"


def client_list(name):
    source = CLIENT.read_text(encoding="utf-8")
    block = re.search(rf"const {name} = \[(.*?)\] as const", source, re.S)
    assert block, name
    return set(re.findall(r"'([a-z_]+)'", block.group(1)))


def test_the_client_offers_exactly_each_systems_conditions():
    assert client_list("PF2E_VALUED") == set(app_module.VALUED_CONDITIONS)
    assert client_list("PF2E_FLAGS") == set(app_module.FLAG_CONDITIONS)
    assert client_list("COSMERE_VALUED") == set(systems.cosmere._VALUED)
    assert client_list("COSMERE_FLAGS") == set(systems.cosmere.CONDITION_INFO) - set(systems.cosmere._VALUED)
