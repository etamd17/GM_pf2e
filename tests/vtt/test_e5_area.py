"""E-5: area effects from the VTT, through the tracker's own HP engine
(_apply_hp_delta, the defeated-monster auto-removal) and the check_request a
save sends to the players. The real engine runs on real Monster objects here;
only persistence, broadcast and SSE are recorded."""

from __future__ import annotations

import pytest

import app as app_module


def monster(name, hp, instance_id, **attributes):
    data = {"name": name, "type": "npc",
            "system": {"attributes": {"hp": {"value": hp, "max": hp}, **attributes}, "traits": {"value": []}}}
    m = app_module.Monster(data)
    m.instance_id = instance_id
    return m


@pytest.fixture
def tracker(monkeypatch):
    sent = []
    encounter = [monster("Goblin A", 10, "ga"), monster("Goblin B", 10, "gb"), monster("Ogre", 50, "og")]
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", encounter)
    monkeypatch.setattr(app_module, "TURN_INDEX", 0)
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    campaign = {"id": "camp_a", "system": "pf2e"}
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: campaign)
    monkeypatch.setattr(app_module, "_VTT_AREA_RECEIPTS", type(app_module._VTT_AREA_RECEIPTS)())
    monkeypatch.setattr(app_module, "_persist_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "_broadcast_encounter_state", lambda: None)
    monkeypatch.setattr(app_module, "sse_broadcast", lambda event, data, **kw: sent.append((event, data)))
    monkeypatch.setattr(app_module, "_bump_campaign_stat", lambda *a, **k: None)
    return {"campaign": campaign, "sent": sent, "encounter": encounter}


def hp(instance_id):
    found = next((c for c in app_module._loaded_encounter_snapshot() if c.instance_id == instance_id), None)
    return found.current_hp if found else None


def apply(action="damage", targets=("cmb_ga", "cmb_og"), request_id="req-area0001", token=None, **params):
    params = params or {"amount": 6, "damageType": "fire"}
    token = app_module._vtt_encounter_token() if token is None else token
    return app_module._vtt_area_apply("camp_a", request_id, token, action, list(targets), params)


def test_damage_goes_through_the_tracker_to_each_reviewed_target(tracker):
    code, detail = apply()
    assert code == "ok" and "incomplete" not in detail
    assert detail["results"] == [{"actorId": "cmb_ga", "outcome": "applied", "net": 6, "defeated": False},
                                 {"actorId": "cmb_og", "outcome": "applied", "net": 6, "defeated": False}]
    assert (hp("ga"), hp("gb"), hp("og")) == (4, 10, 44)       # Goblin B was not in the list


def test_a_defeated_monster_leaves_the_encounter_as_from_the_tracker(tracker):
    code, detail = apply(amount=12, damageType="slashing", targets=("cmb_ga",))
    assert detail["results"][0]["defeated"] is True
    assert hp("ga") is None                                    # auto-removed, stashed for Undo


def test_the_same_request_id_applies_nothing_twice(tracker):
    first = apply()
    again = apply()
    assert again == ("ok", {**first[1], "repeat": True})
    assert (hp("ga"), hp("og")) == (4, 44)


def test_a_failure_stops_the_run_and_a_retry_resumes_without_repeating(tracker, monkeypatch):
    real = app_module._apply_hp_delta
    calls = []

    def flaky(instance_id, *args, **kwargs):
        calls.append(instance_id)
        if instance_id == "gb" and calls.count("gb") == 1:
            raise OSError("disk")
        return real(instance_id, *args, **kwargs)

    monkeypatch.setattr(app_module, "_apply_hp_delta", flaky)
    targets = ("cmb_ga", "cmb_gb", "cmb_og")
    code, detail = apply(targets=targets)
    assert code == "ok" and detail["incomplete"] == "OSError"
    assert [r["outcome"] for r in detail["results"]] == ["applied", "failed"]
    assert (hp("ga"), hp("gb"), hp("og")) == (4, 10, 50)
    code, resumed = apply(targets=targets)                     # the same request id
    assert code == "ok" and "incomplete" not in resumed
    assert [r["outcome"] for r in resumed["results"]] == ["applied", "failed", "applied"]
    assert (hp("ga"), hp("gb"), hp("og")) == (4, 10, 44)       # A once, B never retried, Ogre now
    assert calls == ["ga", "gb", "og"]


def test_a_resume_after_the_encounter_was_replaced_is_stale(tracker, monkeypatch):
    real = app_module._apply_hp_delta

    def fail_on_b(instance_id, *args, **kwargs):
        if instance_id == "gb":
            raise OSError("disk")
        return real(instance_id, *args, **kwargs)

    monkeypatch.setattr(app_module, "_apply_hp_delta", fail_on_b)
    targets = ("cmb_ga", "cmb_gb", "cmb_og")
    assert "incomplete" in apply(targets=targets)[1]
    app_module._clear_live_encounter_roster()
    code, detail = apply(targets=targets)
    assert code == "stale" and [r["actorId"] for r in detail["results"]] == ["cmb_ga", "cmb_gb"]


def test_guards_refuse_before_anything_is_applied(tracker):
    assert apply(token="old-g0")[0] == "stale"
    code, detail = apply(targets=("cmb_ga", "cmb_gone"), request_id="req-area0002")
    assert code == "stale" and detail["missing"] == ["cmb_gone"]
    assert apply(damageType="keen", amount=5, request_id="req-area0003")[0] == "invalid"      # Cosmere's
    assert apply(amount=0, damageType="fire", request_id="req-area0004")[0] == "invalid"
    assert app_module._vtt_area_apply("camp_b", "req-area0005", app_module._vtt_encounter_token(), "damage",
                                      ["cmb_ga"], {"amount": 1, "damageType": "fire"}) == ("not-live", {})
    tracker["campaign"]["system"] = "dnd5e"
    assert apply(request_id="req-area0006")[0] == "unsupported"
    assert (hp("ga"), hp("og")) == (10, 50)


def test_a_reused_request_id_with_different_content_is_refused(tracker):
    assert apply()[0] == "ok"
    assert apply(amount=20, damageType="fire")[0] == "blocked"
    assert apply(targets=("cmb_gb",))[0] == "blocked"


def test_heal(tracker):
    apply(amount=8, damageType="fire", targets=("cmb_og",))
    code, detail = apply("heal", targets=("cmb_og",), request_id="req-area0007", amount=5)
    assert detail["results"] == [{"actorId": "cmb_og", "outcome": "applied", "net": 5, "defeated": False}]
    assert hp("og") == 47


def test_a_save_asks_the_players_once_and_names_the_gm_rolls(tracker):
    tracker["encounter"][1].is_pc = True
    tracker["encounter"][1].name = "Valeros"
    code, detail = apply("save", targets=("cmb_ga", "cmb_gb"), save="reflex", dc=21)
    assert [r["outcome"] for r in detail["results"]] == ["gm-rolls", "requested"]
    assert tracker["sent"] == [("check_request", {"skill": "reflex", "dc": 21, "targets": ["Valeros"], "secret": False})]
    apply("save", targets=("cmb_ga", "cmb_gb"), save="reflex", dc=21)       # retried
    assert len(tracker["sent"]) == 1
    assert apply("save", targets=("cmb_ga",), save="luck", dc=21, request_id="req-area0008")[0] == "invalid"


def test_cosmere_uses_its_own_damage_types_and_has_no_saves(tracker):
    tracker["campaign"]["system"] = "cosmere"
    assert apply(damageType="keen", amount=3, request_id="req-area0009")[0] == "stale"   # PF2e combatants, Cosmere campaign
    for c in tracker["encounter"]:
        c.system = "cosmere"
    assert apply(damageType="fire", amount=3, request_id="req-area0010")[0] == "invalid"
    assert apply("save", save="reflex", dc=15, request_id="req-area0011")[0] == "invalid"
    assert apply(damageType="fire", amount=3, nonlethal=True, request_id="req-area0012")[0] == "invalid"


def test_cosmere_damage_goes_through_its_own_engine(tracker, monkeypatch):
    import systems.cosmere
    tracker["campaign"]["system"] = "cosmere"
    doc = next(d for d in systems.cosmere.adversary_docs() if d["name"] == "Archer")
    archer = systems.cosmere.CosmereActor(doc)
    archer.instance_id = "ar"
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [archer])
    monkeypatch.setattr(app_module, "_sync_cosmere_pc_from_combatant", lambda c: None)
    before = archer.current_hp
    code, detail = apply(targets=("cmb_ar",), damageType="vital", amount=3, request_id="req-area0013")
    assert code == "ok" and detail["results"][0]["outcome"] == "applied"
    assert archer.current_hp == before - 3                      # vital ignores Deflect


def test_the_client_offers_exactly_each_systems_damage_types():
    import re
    from pathlib import Path
    import systems.cosmere.combat as cosmere_combat
    source = (Path(app_module.__file__).resolve().parent / "frontend" / "vtt" / "src" / "app" / "actions"
              / "areaEffects.ts").read_text(encoding="utf-8")

    def listed(name):
        block = re.search(rf"const {name} = \[(.*?)\] as const", source, re.S)
        return re.findall(r"'([a-z]+)'", block.group(1))

    assert listed("PF2E_DAMAGE") == list(app_module.PF2E_DAMAGE_TYPES)
    assert listed("COSMERE_DAMAGE") == list(cosmere_combat.DAMAGE_TYPES)
    assert listed("PF2E_SAVES") == list(app_module._VTT_PF2E_SAVES)
