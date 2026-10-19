"""E-3: bestiary search and add-to-tracker from the VTT, through the tracker's
own add_combatant. The real add_combatant runs here (it only appends to the
roster and schedules persistence and broadcast); the browser harness runs it
end to end against a live server."""

from __future__ import annotations

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


def add(ref, request_id="req-00000001", campaign="camp_a"):
    return app_module._vtt_bestiary_add(campaign, ref, request_id)


def test_search_finds_by_name_with_prefix_matches_first(tracker):
    code, detail = search("skel")
    assert code == "ok"
    assert [r["name"] for r in detail["results"]] == ["Skeleton Guard", "Giant Skeleton"]
    assert detail["results"][1] == {"ref": "b/giant-skeleton.json", "name": "Giant Skeleton", "level": 5, "size": 3}
    assert search("s") == ("ok", {"results": []})                  # under two characters


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
    assert search("arch") == ("ok", {"results": [{"ref": "adv1", "name": "Archer", "level": 1, "size": 2}]})
    assert search("skel") == ("ok", {"results": []})                # PF2e's monsters are not offered
    assert add("b/goblin.json") == ("not-found", {})
    monkeypatch.setattr(app_module, "_cosmere_doc_by_id", lambda ref: None)   # e.g. a Cosmere PC's id
    assert add("pc-1234") == ("not-found", {})


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
