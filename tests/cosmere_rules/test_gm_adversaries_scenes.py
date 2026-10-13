"""Rich custom adversaries and revisioned conversation/endeavor contracts."""
from copy import deepcopy
from pathlib import Path
import uuid

import pytest

from systems.cosmere.build import CosmereBuild
from systems.cosmere.rules.schema import to_dict


def binding():
    return to_dict(CosmereBuild().rules_binding)


def mixed_binding():
    from systems.cosmere.rules.profiles import default_registry, package_pin
    from systems.cosmere.rules.schema import CosmereRulesBinding

    registry = default_registry()
    return to_dict(CosmereRulesBinding(
        package_pin(registry.packs["cosmere.core"]),
        tuple(package_pin(registry.packs[pack_id]) for pack_id in (
            "cosmere.stormlight.legacy", "cosmere.mistborn.core",
            "cosmere.mistborn.era-1")),
        "roshar", ("era-1",), ("roshar", "scadrial")))


@pytest.fixture
def mixed_fixture_registry(monkeypatch):
    from systems.cosmere.rules import profiles
    from systems.cosmere.rules.paths import registry_path
    from systems.cosmere.rules.registry import load_registry
    if not registry_path().is_file():
        fixture = Path(__file__).resolve().parents[1] / "fixtures" / "cosmere_rules" / "rules" / "registry.json"
        registry = load_registry(fixture)
        monkeypatch.setattr(profiles, "default_registry", lambda: registry)


def structured_action(action_id="crushing-blow"):
    return {
        "id": action_id, "source_id": "source." + action_id,
        "item_id": "item." + action_id, "actor_id": None,
        "name": "Crushing Blow", "kind": "action",
        "activation": {"type": "skill_test",
                       "cost": {"type": "action", "value": 2},
                       "consume": [{"type": "resource", "resource": "focus",
                                    "amount": 1, "minimum": 1, "maximum": 1}],
                       "flavor": ""},
        "test": {"type": "skill_test", "skill": "ath",
                 "attribute": "str", "modifier": "+2"},
        "damage": {"formula": "2d6", "type": "impact", "skill": "ath",
                   "attribute": "str", "graze_formula": "1d6"},
        "target": {"type": "creature", "value": 1, "range": "reach"},
        "plot_die": True, "opportunity": "Push the target.",
        "complication": "Become Prone.",
        "use_limit": {"max": 1, "recharge": "per_scene"},
        "description": "A structured original summary.",
        "allow_multiple": False, "permissions": {},
    }


def adversary_payload(name="Rival"):
    return {
        "name": name, "world": "roshar", "era": None,
        "role": "ordinary", "level": 3, "tier": 2, "size": "large",
        "defenses": {"physical": 14, "cognitive": 12, "spiritual": 13},
        "resources": {"health": {"current": 24, "max": 24},
                      "focus": {"current": 3, "max": 3}},
        "traits": ["human", "soldier"],
        "equipment": [{"id": "item.test.armor", "name": "Armor"}],
        "actions": [structured_action()],
    }


def test_custom_adversary_crud_is_revisioned_bound_and_structured():
    from systems.cosmere.custom_adversaries import (
        AdversaryStateError,
        create_adversary,
        delete_adversary,
        edit_adversary,
    )

    state, created = create_adversary(
        {}, adversary_payload(), binding=binding(), expected_store_revision=0,
        request_id="request.adversary.create", id_factory=lambda: "ab" * 16)
    adversary = created["adversary"]
    assert state["revision"] == 1 and adversary["revision"] == 0
    assert adversary["id"] == "ab" * 16
    assert adversary["rules_binding"] == binding()
    assert len(adversary["binding_hash"]) == 64
    assert adversary["actions"][0]["activation"]["cost"] == {
        "type": "action", "value": 2}
    assert adversary["actions"][0]["opportunity"] == "Push the target."
    assert adversary["tier"] == 2 and adversary["size"] == "large"

    replay_state, replay = create_adversary(
        state, adversary_payload(), binding=binding(), expected_store_revision=0,
        request_id="request.adversary.create", id_factory=lambda: "cd" * 16)
    assert replay_state == state and replay["replayed"] is True

    changed = adversary_payload("Veteran Rival")
    state, edited = edit_adversary(
        state, adversary["id"], changed, binding=binding(), expected_revision=0,
        request_id="request.adversary.edit")
    assert edited["adversary"]["name"] == "Veteran Rival"
    assert edited["adversary"]["revision"] == 1 and state["revision"] == 2
    with pytest.raises(AdversaryStateError, match="stale"):
        edit_adversary(state, adversary["id"], changed, binding=binding(),
                       expected_revision=0, request_id="request.adversary.stale")
    with pytest.raises(AdversaryStateError, match="referenced"):
        delete_adversary(state, adversary["id"], expected_revision=1,
                         request_id="request.adversary.delete.blocked",
                         referenced_ids={adversary["id"]})
    state, deleted = delete_adversary(
        state, adversary["id"], expected_revision=1,
        request_id="request.adversary.delete", referenced_ids=set())
    assert deleted["id"] == adversary["id"] and state["adversaries"] == []


def test_custom_adversary_legacy_bare_list_normalizes_and_round_trips_actor():
    from systems.cosmere.actor import CosmereActor
    from systems.cosmere.custom_adversaries import (
        adversary_to_actor_document,
        normalize_store,
    )

    legacy = {
        "_id": "12" * 16, "name": "Legacy Rival", "type": "adversary",
        "system": {
            "level": {"value": 3}, "tier": 3, "role": "Boss", "size": "huge",
            "attributes": {key: {"value": 0}
                           for key in ("str", "spd", "int", "wil", "awa", "pre")},
            "defenses": {
                "phy": {"useOverride": True, "override": 15},
                "cog": {"useOverride": True, "override": 12},
                "spi": {"useOverride": True, "override": 14}},
            "resources": {
                "hea": {"value": 40, "max": {"useOverride": True, "override": 40}},
                "foc": {"value": 4, "max": {"useOverride": True, "override": 4}},
                "inv": {"value": 0, "max": {"useOverride": True, "override": 0}}},
            "deflect": {"natural": 2, "types": {"impact": True}},
            "skills": {"hwp": {"rank": 0, "attribute": "str",
                                 "mod": {"useOverride": True, "override": 11}}},
        },
        "items": [
            {"_id": "34" * 16, "type": "weapon", "name": "Smite",
             "system": {"activation": {"cost": {"type": "act", "value": 1}},
                        "damage": {"formula": "1d12+5", "type": "impact",
                                   "skill": "hwp"},
                        "target": {"type": "creature", "range": "reach"}}},
            {"_id": "56" * 16, "type": "trait", "name": "Ancient",
             "system": {}},
            {"_id": "78" * 16, "type": "item", "name": "Shardplate",
             "system": {"description": {"short": "Heavy protection."}}},
        ],
    }
    store = normalize_store([legacy], binding=binding())
    assert store["schema_version"] == 1 and store["revision"] == 0
    rich = store["adversaries"][0]
    assert rich["id"] == legacy["_id"] and rich["name"] == "Legacy Rival"
    assert rich["role"] == "boss" and rich["defenses"]["physical"] == 15
    assert rich["actions"][0]["damage"]["formula"] == "1d12+5"
    assert rich["tier"] == 3 and rich["size"] == "huge"
    assert rich["traits"] == ["Ancient"]
    assert rich["equipment"][0]["name"] == "Shardplate"

    projected = adversary_to_actor_document(rich)
    assert projected["system"]["tier"] == 3
    assert projected["system"]["size"] == "huge"
    assert [(row["type"], row["name"]) for row in projected["items"]] == [
        ("trait", "Ancient"), ("item", "Shardplate"), ("weapon", "Smite")]
    actor = CosmereActor(projected)
    assert actor.name == "Legacy Rival" and actor.health_max == 40
    assert actor.tier == 3 and actor.size == "huge" and actor.traits == ["Ancient"]
    assert actor.strikes[0]["name"] == "Smite"
    assert actor.strikes[0]["damage"] == "1d12+5"


@pytest.mark.parametrize("field,value,message", [
    ("role", "pf2e-elite", "role"),
    ("defenses", {"physical": -1, "cognitive": 10, "spiritual": 10}, "defense"),
    ("resources", {"health": {"current": 12, "max": 10}}, "resource"),
])
def test_custom_adversary_contract_rejects_invalid_mechanics(field, value, message):
    from systems.cosmere.custom_adversaries import AdversaryValidationError, create_adversary

    payload = adversary_payload()
    payload[field] = value
    with pytest.raises(AdversaryValidationError, match=message):
        create_adversary({}, payload, binding=binding(), expected_store_revision=0,
                         request_id="request.invalid", id_factory=lambda: "ab" * 16)


@pytest.mark.parametrize("field,value,message", [
    ("test", "bad", "test"),
    ("damage", "bad", "damage"),
    ("target", "bad", "target"),
    ("permissions", "bad", "permissions"),
    ("plot_die", "yes", "plot die"),
])
def test_custom_adversary_rejects_incomplete_structured_action_contract(
        field, value, message):
    from systems.cosmere.custom_adversaries import (
        AdversaryValidationError,
        create_adversary,
    )

    payload = adversary_payload()
    payload["actions"][0][field] = value
    with pytest.raises(AdversaryValidationError, match=message):
        create_adversary({}, payload, binding=binding(), expected_store_revision=0,
                         request_id=f"request.invalid.action.{field}",
                         id_factory=lambda: "ab" * 16)


def test_custom_adversary_rejects_non_numeric_attack_modifier_before_persistence():
    from systems.cosmere.custom_adversaries import (
        AdversaryValidationError,
        create_adversary,
    )

    payload = adversary_payload()
    payload["actions"][0]["attack_modifier"] = "not-a-number"

    with pytest.raises(AdversaryValidationError, match="attack modifier"):
        create_adversary({}, payload, binding=binding(), expected_store_revision=0,
                         request_id="request.invalid.action.attack-modifier",
                         id_factory=lambda: "ab" * 16)


def scene_payload(scene_type="conversation"):
    track = "disposition" if scene_type == "conversation" else "progress"
    return {
        "name": "Parley" if scene_type == "conversation" else "Cross the Chasm",
        "world": "roshar", "era": None,
        "participants": [{"actor_id": "ab" * 16, "role": "lead"}],
        "tracks": {track: {"value": 0, "target": 5}},
    }


@pytest.mark.parametrize("scene_type", ["conversation", "endeavor"])
def test_narrative_scene_create_update_close_is_revisioned_and_emits_scene_end(scene_type):
    from systems.cosmere.narrative_scenes import (
        close_scene,
        create_scene,
        update_scene,
    )

    state, created = create_scene(
        {}, scene_type, scene_payload(scene_type), binding=binding(),
        expected_store_revision=0, request_id=f"request.{scene_type}.create",
        id_factory=lambda: "cd" * 16)
    scene = created["scene"]
    assert scene["type"] == scene_type and scene["revision"] == 0
    assert scene["status"] == "active" and scene["rules_binding"] == binding()
    assert len(scene["binding_hash"]) == 64

    changed = deepcopy(scene_payload(scene_type))
    track = next(iter(changed["tracks"]))
    changed["tracks"][track]["value"] = 2
    state, edited = update_scene(
        state, scene["id"], changed, binding=binding(), expected_revision=0,
        request_id=f"request.{scene_type}.update")
    assert edited["scene"]["revision"] == 1
    assert edited["scene"]["tracks"][track]["value"] == 2

    state, closed = close_scene(
        state, scene["id"], expected_revision=1,
        request_id=f"request.{scene_type}.close")
    assert closed["scene"]["status"] == "closed"
    assert closed["scene"]["revision"] == 2
    assert closed["lifecycle"] == {
        "trigger": "scene_end", "scene_id": scene["id"],
        "scene_type": scene_type}
    replay_state, replay = close_scene(
        state, scene["id"], expected_revision=1,
        request_id=f"request.{scene_type}.close")
    assert replay_state == state and replay["replayed"] is True


def test_narrative_scene_scope_tracks_and_stale_revisions_fail_closed():
    from systems.cosmere.narrative_scenes import (
        NarrativeSceneError,
        NarrativeSceneValidationError,
        create_scene,
        update_scene,
    )

    invalid = scene_payload()
    invalid["world"] = "scadrial"
    with pytest.raises(NarrativeSceneValidationError, match="world"):
        create_scene({}, "conversation", invalid, binding=binding(),
                     expected_store_revision=0, request_id="request.scene.world")
    invalid = scene_payload("endeavor")
    invalid["tracks"]["progress"] = {"value": 6, "target": 5}
    with pytest.raises(NarrativeSceneValidationError, match="track"):
        create_scene({}, "endeavor", invalid, binding=binding(),
                     expected_store_revision=0, request_id="request.scene.track")

    state, created = create_scene(
        {}, "conversation", scene_payload(), binding=binding(),
        expected_store_revision=0, request_id="request.scene.create",
        id_factory=lambda: "ef" * 16)
    with pytest.raises(NarrativeSceneError, match="stale"):
        update_scene(state, created["scene"]["id"], scene_payload(),
                     binding=binding(), expected_revision=1,
                     request_id="request.scene.stale")


def test_mixed_binding_accepts_scadrial_adversaries_and_scenes_with_exact_era(mixed_fixture_registry):
    from systems.cosmere.custom_adversaries import create_adversary
    from systems.cosmere.narrative_scenes import create_scene

    adversary = adversary_payload("Hazekiller")
    adversary.update(world="scadrial", era="era-1")
    _state, created = create_adversary(
        {}, adversary, binding=mixed_binding(), expected_store_revision=0,
        request_id="request.mixed.adversary", id_factory=lambda: "ab" * 16)
    assert (created["adversary"]["world"], created["adversary"]["era"]) == (
        "scadrial", "era-1")

    scene = scene_payload()
    scene.update(world="scadrial", era="era-1")
    _state, created = create_scene(
        {}, "conversation", scene, binding=mixed_binding(),
        expected_store_revision=0, request_id="request.mixed.scene",
        id_factory=lambda: "cd" * 16)
    assert (created["scene"]["world"], created["scene"]["era"]) == (
        "scadrial", "era-1")


@pytest.mark.parametrize("kind", ["adversary", "scene"])
def test_mixed_binding_rejects_era_on_non_scadrial_content(kind, mixed_fixture_registry):
    from systems.cosmere.custom_adversaries import (
        AdversaryValidationError, create_adversary)
    from systems.cosmere.narrative_scenes import (
        NarrativeSceneValidationError, create_scene)

    if kind == "adversary":
        payload = adversary_payload()
        payload["era"] = "era-1"
        with pytest.raises(AdversaryValidationError, match="era|Scadrial"):
            create_adversary(
                {}, payload, binding=mixed_binding(), expected_store_revision=0,
                request_id="request.mixed.adversary.invalid",
                id_factory=lambda: "ef" * 16)
    else:
        payload = scene_payload()
        payload["era"] = "era-1"
        with pytest.raises(NarrativeSceneValidationError, match="era|Scadrial"):
            create_scene(
                {}, "conversation", payload, binding=mixed_binding(),
                expected_store_revision=0, request_id="request.mixed.scene.invalid",
                id_factory=lambda: "ef" * 16)
