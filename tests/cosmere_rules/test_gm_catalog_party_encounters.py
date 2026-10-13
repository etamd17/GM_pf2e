"""Package-only compendium, neutral party projection, and encounter staging."""
from copy import deepcopy
import uuid

import pytest


PACKAGE = {
    "id": "cosmere.test.world", "version": "1.2.0",
    "package_hash": "c" * 64, "dependencies": ["cosmere.core"],
}


def record(record_id, *, kind="item", name="Steel Knife", spoiler="player",
           world="roshar", era=()):
    return {
        "id": record_id, "version": "1.0.0", "kind": kind, "name": name,
        "summary": f"Independent summary for {name}.",
        "worlds": [world], "eras": list(era), "spoiler_scope": spoiler,
        "automation": "reference", "audit_status": "provisional",
        "citations": [{"artifact_id": "stormlight.handbook",
                       "page_start": 20, "page_end": 20}],
        "parameters": {"tags": ["gear"]}, "package": deepcopy(PACKAGE),
    }


def catalog(*records):
    return {"packages": [deepcopy(PACKAGE)],
            "catalogs": {"item": [deepcopy(row) for row in records]}}


def build(name="Hero"):
    return {
        "name": name, "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2,
                       "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1},
        "talents": [{"id": "talent.test.brace", "name": "Brace"}],
        "expertises": ["Military Tactics"], "epic_choices": [],
    }


def character(name="Hero", *, health=7):
    return {
        "id": uuid.uuid4().hex, "system": "cosmere", "name": name,
        "build": build(name),
        "runtime_state": {
            "health": health, "focus": 3, "investiture": 0,
            "effects": [{"id": "effect.test", "source_id": "source.test",
                         "target_id": "ab" * 16, "parameters": {},
                         "selected_attribute": None,
                         "stacking_identity": "effect.test.stack",
                         "duration_unit": "scene", "remaining_duration": 1,
                         "trigger": "scene_end", "refresh_policy": "independent",
                         "expiry_state": {}, "expired": False}],
        },
    }


def test_compendium_search_and_detail_use_only_normalized_active_records():
    from systems.cosmere.compendium import CompendiumNotFound, detail, search

    visible = record("test.item.knife", name="Steel Knife")
    secret = record("test.item.secret", name="Secret Blade", spoiler="gm")
    wrong_world = record("test.item.scadrial", name="Scadrian Knife",
                         world="scadrial", era=("era-1",))
    source = catalog(visible, secret, wrong_world)

    results = search(source, query="knife", world="roshar",
                     spoiler_scope="player")
    assert results["total"] == 1
    assert results["items"][0]["id"] == "test.item.knife"
    assert results["items"][0]["package"] == PACKAGE
    assert results["items"][0]["citations"][0]["page_start"] == 20
    assert "html" not in results["items"][0]
    assert detail(source, "test.item.knife", world="roshar",
                  spoiler_scope="player")["parameters"] == {"tags": ["gear"]}
    with pytest.raises(CompendiumNotFound):
        detail(source, "test.item.secret", world="roshar",
               spoiler_scope="player")
    with pytest.raises(CompendiumNotFound):
        detail(source, "test.item.scadrial", world="roshar",
               spoiler_scope="gm")


def test_compendium_order_and_limits_are_deterministic_and_never_search_raw_html():
    from systems.cosmere.compendium import CompendiumValidationError, search

    source = catalog(record("test.item.z", name="Knife Z"),
                     record("test.item.a", name="Knife A"))
    assert [row["id"] for row in search(
        source, query="knife", world="roshar", spoiler_scope="gm")["items"]] == [
            "test.item.a", "test.item.z"]
    assert search(source, query="", world="roshar", spoiler_scope="gm",
                  limit=1)["total"] == 2
    assert len(search(source, query="", world="roshar", spoiler_scope="gm",
                      limit=1)["items"]) == 1
    unsafe = record("test.item.unsafe")
    unsafe["raw_html"] = "<p>Foundry prose</p>"
    with pytest.raises(CompendiumValidationError, match="normalized"):
        search(catalog(unsafe), query="", world="roshar", spoiler_scope="gm")


def test_party_projection_is_system_neutral_and_uses_canonical_documents():
    from systems.cosmere.party import project_party

    first = character("Kal", health=7)
    second = character("Shallan", health=5)
    projection = project_party([second, first])

    assert [row["name"] for row in projection["members"]] == ["Kal", "Shallan"]
    kal = projection["members"][0]
    assert kal["world"] == "roshar" and kal["era"] is None
    assert set(kal["defenses"]) == {"physical", "cognitive", "spiritual"}
    assert kal["resources"]["health"]["current"] == 7
    assert kal["skills"]["athletics"] == 1
    assert kal["capabilities"][0]["id"] == "talent.test.brace"
    assert kal["effects"][0]["id"] == "effect.test"
    encoded_keys = set().union(*(row.keys() for row in projection["members"]))
    assert not {"ac", "fortitude", "reflex", "will_save", "three_actions"} & encoded_keys
    assert "notes" not in kal and "build" not in kal


class FakeBinding:
    default_world = "roshar"
    allowed_eras = ()


class FakeProfile:
    binding = FakeBinding()

    def encounter_budget(self, participants, adversaries):
        return {"automation": "gm_adjudicated",
                "blocked_reasons": ["Use GM judgment."],
                "participant_count": len(participants),
                "adversary_count": len(adversaries)}


class MixedBinding:
    default_world = "roshar"
    allowed_worlds = ("roshar", "scadrial")
    allowed_eras = ("era-1",)


class MixedProfile(FakeProfile):
    binding = MixedBinding()


def actor(actor_id, name, *, role="ordinary", world="roshar", era=None):
    return {"id": actor_id, "name": name, "world": world, "era": era,
            "role": role, "defenses": {"physical": 12},
            "resources": {"health": {"current": 10, "max": 10}},
            "actions": []}


def test_encounter_preview_is_profile_driven_and_contains_no_pf2e_fields():
    from systems.cosmere.encounters import preview_encounter

    preview = preview_encounter(
        FakeProfile(), [actor("pc.one", "Hero")],
        [actor("adversary.one", "Rival")], world="roshar")
    assert preview["budget"]["automation"] == "gm_adjudicated"
    assert preview["budget"]["participant_count"] == 1
    assert preview["binding"]["default_world"] == "roshar"
    assert len(preview["binding_hash"]) == 64
    forbidden = {"xp", "ac", "fortitude", "reflex", "will_save",
                 "elite", "weak", "three_action"}
    assert not forbidden & set(str(preview).lower().replace("'", "").split())
    assert not forbidden & set(preview)


def test_encounter_preview_rejects_world_and_era_outside_binding():
    from systems.cosmere.encounters import EncounterValidationError, preview_encounter

    with pytest.raises(EncounterValidationError, match="world"):
        preview_encounter(FakeProfile(), [actor("pc.one", "Hero")],
                          [actor("adversary.one", "Rival", world="scadrial")],
                          world="roshar")
    with pytest.raises(EncounterValidationError, match="era"):
        preview_encounter(FakeProfile(), [actor("pc.one", "Hero")],
                          [actor("adversary.one", "Rival", era="era-1")],
                          world="roshar")


def test_mixed_campaign_previews_one_explicit_nondefault_world_at_a_time():
    from systems.cosmere.encounters import EncounterValidationError, preview_encounter

    preview = preview_encounter(
        MixedProfile(),
        [actor("pc.scadrial", "Misting", world="scadrial", era="era-1")],
        [actor("adversary.scadrial", "Hazekiller", world="scadrial", era="era-1")],
        world="scadrial", era="era-1")

    assert preview["world"] == "scadrial" and preview["era"] == "era-1"
    assert preview["binding"]["allowed_worlds"] == ["roshar", "scadrial"]
    with pytest.raises(EncounterValidationError, match="world"):
        preview_encounter(
            MixedProfile(),
            [actor("pc.roshar", "Radiant", world="roshar")],
            [actor("adversary.scadrial", "Hazekiller", world="scadrial", era="era-1")],
            world="scadrial", era="era-1")


def test_revisioned_staging_is_idempotent_and_self_contains_adversaries():
    from systems.cosmere.encounters import (
        EncounterStateError,
        preview_encounter,
        stage_encounter,
    )

    adversary = actor("adversary.one", "Rival")
    preview = preview_encounter(FakeProfile(), [actor("pc.one", "Hero")],
                                [adversary], world="roshar")
    state, staged = stage_encounter(
        {}, preview, expected_revision=0, request_id="request.stage.1")
    adversary["name"] = "Changed after staging"
    assert state["revision"] == 1
    assert staged["stage"]["adversaries"][0]["name"] == "Rival"

    replay_state, replay = stage_encounter(
        state, preview, expected_revision=0, request_id="request.stage.1")
    assert replay_state == state and replay["replayed"] is True
    with pytest.raises(EncounterStateError, match="stale"):
        stage_encounter(state, preview, expected_revision=0,
                        request_id="request.stage.2")
