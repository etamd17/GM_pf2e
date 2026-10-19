"""E-3: bestiary search and add-to-tracker from the VTT, through the tracker's
own add_combatant. The real add_combatant runs here (it only appends to the
roster and schedules persistence and broadcast); the browser harness runs it
end to end against a live server."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import app as app_module


class Mon:
    def __init__(self, name, level=1, size="Medium"):
        self.name, self.level, self.size = name, level, size
        self.is_pc = False
        self.instance_id = "template"
        self.initiative = 0


@pytest.fixture
def tracker(monkeypatch):
    library = {"b/skeleton-guard.json": Mon("Skeleton Guard", -1),
               "b/giant-skeleton.json": Mon("Giant Skeleton", 5, "Huge"),
               "b/goblin.json": Mon("Goblin Warrior", -1, "Small")}
    monkeypatch.setattr(app_module, "MONSTER_LIBRARY", library)
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [])
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    campaign = {"id": "camp_a", "system": "pf2e"}
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: campaign)
    monkeypatch.setattr(app_module, "_VTT_BESTIARY_ADDED", type(app_module._VTT_BESTIARY_ADDED)())
    monkeypatch.setattr(app_module, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "_persist_encounter_state", lambda: None)
    return campaign


def search(q, campaign="camp_a"):
    return app_module._vtt_bestiary_search(campaign, q)


def add(ref, request_id="req-00000001", campaign="camp_a", encounter=None):
    token = app_module._vtt_encounter_token() if encounter is None else encounter
    return app_module._vtt_bestiary_add(campaign, ref, request_id, token)


def test_search_finds_by_name_with_prefix_matches_first(tracker):
    code, detail = search("skel")
    assert code == "ok" and detail["encounterToken"] == app_module._vtt_encounter_token()
    assert [r["name"] for r in detail["results"]] == ["Skeleton Guard", "Giant Skeleton"]
    assert detail["results"][1] == {"ref": "b/giant-skeleton.json", "name": "Giant Skeleton", "level": 5, "size": 3}
    assert search("s")[1]["results"] == []                           # under two characters


def test_add_goes_through_the_tracker_and_returns_the_linked_actor(tracker):
    code, detail = add("b/giant-skeleton.json")
    assert code == "ok"
    roster = app_module._loaded_encounter_snapshot()
    assert len(roster) == 1 and roster[0].name == "Giant Skeleton"
    assert roster[0] is not app_module.MONSTER_LIBRARY["b/giant-skeleton.json"]   # the tracker's own copy
    assert detail == {"actorId": "cmb_" + roster[0].instance_id, "name": "Giant Skeleton", "size": 3}


def test_the_same_request_id_adds_nothing_twice(tracker):
    first = add("b/goblin.json")
    again = add("b/goblin.json")
    assert again == ("ok", {**first[1], "repeat": True})
    assert len(app_module._loaded_encounter_snapshot()) == 1
    assert add("b/goblin.json", request_id="req-00000002")[0] == "ok"    # a new click is a new creature
    assert len(app_module._loaded_encounter_snapshot()) == 2


def test_another_campaign_is_never_activated(tracker):
    assert add("b/goblin.json", campaign="camp_b") == ("not-live", {})
    assert search("gob", campaign="camp_b") == ("not-live", {})
    assert app_module._loaded_encounter_snapshot() == ()


def test_a_request_id_from_another_campaign_is_refused(tracker, monkeypatch):
    assert add("b/goblin.json")[0] == "ok"
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_b")
    assert add("b/goblin.json", campaign="camp_b")[0] == "blocked"


def test_dnd5e_and_unknown_refs_are_refused(tracker):
    assert add("b/nope.json") == ("not-found", {})
    tracker["system"] = "dnd5e"
    assert add("b/goblin.json")[0] == "unsupported"
    assert search("gob")[0] == "unsupported"
    assert app_module._loaded_encounter_snapshot() == ()


def test_cosmere_uses_its_own_bestiary_and_never_a_pc_id(tracker, monkeypatch):
    tracker["system"] = "cosmere"
    docs = [{"_id": "adv1", "name": "Archer", "type": "adversary", "system": {"tier": 1, "size": "large"}}]
    monkeypatch.setattr(app_module.systems.cosmere, "adversary_docs", lambda: docs)
    monkeypatch.setattr(app_module, "_load_cosmere_custom_adversaries", lambda: [])
    assert search("arch")[1]["results"] == [{"ref": "adv1", "name": "Archer", "level": 1, "size": 2}]
    assert search("skel")[1]["results"] == []                       # PF2e's monsters are not offered
    assert add("b/goblin.json") == ("not-found", {})
    monkeypatch.setattr(app_module, "_cosmere_doc_by_id", lambda ref: None)   # e.g. a Cosmere PC's id
    assert add("pc-1234") == ("not-found", {})


# --- review 098: a failure after the append, and the encounter binding ------------------------

def test_a_failure_after_the_append_keeps_the_receipt_and_a_retry_adds_nothing(tracker, monkeypatch):
    # The real add_combatant appends, then persists and broadcasts; the
    # broadcast fails once.
    failures = iter([OSError("broadcast down")])

    def broadcast():
        error = next(failures, None)
        if error is not None:
            raise error

    monkeypatch.setattr(app_module, "_broadcast_encounter_state", broadcast)
    code, first = add("b/goblin.json", request_id="req-retry0001")
    assert code == "ok" and first["incomplete"] == "OSError"
    assert len(app_module._loaded_encounter_snapshot()) == 1
    again = add("b/goblin.json", request_id="req-retry0001")
    assert again == ("ok", {**first, "repeat": True})                # the same creature, nothing added
    assert len(app_module._loaded_encounter_snapshot()) == 1


def test_a_refusal_before_the_append_adds_nothing_and_can_be_retried(tracker, monkeypatch):
    real = app_module.add_combatant

    def refuse():
        raise PermissionError("locked")

    monkeypatch.setattr(app_module, "add_combatant", refuse)
    assert add("b/goblin.json", request_id="req-retry0002") == ("blocked", {"reason": "PermissionError"})
    assert app_module._loaded_encounter_snapshot() == ()
    monkeypatch.setattr(app_module, "add_combatant", real)
    assert add("b/goblin.json", request_id="req-retry0002")[0] == "ok"
    assert len(app_module._loaded_encounter_snapshot()) == 1


def test_a_request_for_a_cleared_encounter_is_stale(tracker):
    shown = app_module._vtt_encounter_token()
    assert add("b/goblin.json", request_id="req-enc00001", encounter=shown)[0] == "ok"
    app_module._clear_live_encounter_roster()                       # the tracker's clear, load, stage ...
    code, detail = add("b/goblin.json", request_id="req-enc00002", encounter=shown)
    assert (code, detail["encounterToken"]) == ("stale", app_module._vtt_encounter_token())
    assert app_module._loaded_encounter_snapshot() == ()            # nothing added to the new encounter
    assert add("b/goblin.json", request_id="req-enc00003")[0] == "ok"   # with the current token it works


def test_the_encounter_token_ignores_turns_and_single_additions(tracker):
    shown = app_module._vtt_encounter_token()
    assert add("b/goblin.json", request_id="req-enc00004", encounter=shown)[0] == "ok"
    app_module.TURN_INDEX = 0
    app_module._persist_encounter_state()
    assert add("b/goblin.json", request_id="req-enc00005", encounter=shown)[0] == "ok"   # the same encounter
    assert len(app_module._loaded_encounter_snapshot()) == 2


def test_an_empty_encounter_has_a_token_and_takes_its_first_creature(tracker):
    assert app_module._loaded_encounter_snapshot() == ()
    assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", app_module._vtt_encounter_token())
    assert add("b/goblin.json", request_id="req-enc00006")[0] == "ok"


def test_every_wholesale_roster_replacement_goes_through_the_helper():
    # Only _clear_live_encounter_roster may clear the roster (it moves the
    # encounter generation). Other assignments to it reorder or filter the
    # same objects: removing one combatant, initiative reorder, Cosmere phases.
    source = Path(app_module.__file__).read_text(encoding="utf-8")
    clears = [m.start() for m in re.finditer(r"ACTIVE_ENCOUNTER\.clear\(\)", source)]
    helper = source.index("def _clear_live_encounter_roster():")
    assert len(clears) == 1 and helper < clears[0] < source.index("\ndef ", helper + 1)
    assigned = re.findall(r"^\s*ACTIVE_ENCOUNTER(?:\[:\])?\s*=\s*(.+)$", source, re.M)
    assert sorted(assigned) == sorted([
        "[]",                                                        # the module's initial value
        "[it['_c'] for it in _cc.order_combatants(items)]",          # Cosmere phase order
        "[c for c in ACTIVE_ENCOUNTER if c.instance_id != instance_id]",   # remove one
        "new_order",                                                 # initiative reorder
    ]), assigned


def test_monsters_read_their_size_from_the_statblock():
    def monster(size):
        data = {"name": "M", "type": "npc", "system": {"traits": {"value": [], "size": {"value": size}}}}
        return app_module.Monster(data)

    assert [monster(s).size for s in ("tiny", "sm", "med", "lg", "huge", "grg", None)] == [
        "Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan", "Medium"]
    assert app_module._vtt_squares("Gargantuan") == 4 and app_module._vtt_squares("Tiny") == 0.5


# --- the real library ---------------------------------------------------------------------------

LIBRARY = Path(app_module.__file__).resolve().parent / "monster_data"


@pytest.mark.skipif(not LIBRARY.is_dir(), reason="no monster library")
def test_the_real_library_has_sizes():
    sizes = {getattr(m, "size", None) for m in app_module.MONSTER_LIBRARY.values()}
    assert {"Large", "Huge", "Gargantuan"} <= sizes
