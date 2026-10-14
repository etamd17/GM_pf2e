"""Canonical Cosmere character documents keep rules and runtime authority explicit."""
from copy import deepcopy

import pytest

from systems.cosmere.rules.profiles import normalize_binding
from systems.cosmere.rules.schema import RulesValidationError, to_dict


PID = "3520ca4cb56243f095ec8a5209174a68"


def legacy_build(level=1):
    return {
        "name": "Kaladin", "level": level, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
        "skills": {"ath": 1},
        "talents": [{"id": "8mJC2HnsN3OQ2J20", "name": "Vigilant Stance"}],
        "expertises": [],
    }


def homebrew_store():
    return {
        "surge": [{"type": "surge", "name": "Sandmastery", "code": "snd",
                   "attribute": "wil"}],
        "radiant_path": [{"type": "radiant_path", "name": "Sandbearer",
                           "slug": "sandbearer", "spren": "Sandspren",
                           "surges": ["snd", "grv"], "philosophy": "Endure."}],
        "talent": [{"type": "talent", "id": "hb:sand-heart",
                    "name": "Sand Heart", "path": "any",
                    "effects": [{"target": "health", "value": 3}]}],
    }


def homebrew_build():
    build = legacy_build()
    build.update(radiant_order="sandbearer", ideals_sworn=1, is_radiant=True)
    build["skills"] = {"ath": 1, "snd": 1, "grv": 1}
    build["talents"].append({"id": "hb:sand-heart", "name": "Sand Heart"})
    return build


def test_legacy_document_normalizes_to_versioned_pinned_shape_without_refilling_runtime():
    from systems.cosmere.documents import CHARACTER_SCHEMA_VERSION, normalize_character_document

    source = {
        "id": PID, "system": "cosmere", "name": "Kaladin",
        "build": legacy_build(),
        "play_state": {"health": 3, "focus": 1, "conditions": {"exhausted": 1}},
    }
    original = deepcopy(source)
    result = normalize_character_document(source, actor_id=PID, cosmere_theme="mistborn")
    assert source == original
    assert result["schema_version"] == CHARACTER_SCHEMA_VERSION
    assert result["revision"] == 0
    assert result["rules_binding"] == to_dict(normalize_binding())
    assert result["origin_world"] == "roshar" and result["era"] is None
    assert result["cosmere_theme"] == "mistborn"
    assert result["build"]["rules_binding"] == result["rules_binding"]
    assert result["play_state"] == result["runtime_state"]
    assert result["runtime_state"]["health"] == 3
    assert result["runtime_state"]["conditions"] == {"exhausted": 1}
    assert len(result["runtime_state"]["effects"]) == 1
    assert result["derived"]["health_max"] > result["runtime_state"]["health"]
    assert result["advancement_history"] == []


def test_explicit_runtime_state_is_canonical_and_legacy_projection_is_deliberate():
    from systems.cosmere.documents import normalize_character_document, with_legacy_play_state

    source = {"id": PID, "system": "cosmere", "name": "K",
              "build": legacy_build(), "runtime_state": {"health": 2},
              "play_state": {"health": 99}}
    result = normalize_character_document(source, actor_id=PID)
    assert result["runtime_state"]["health"] == 2
    assert result["play_state"]["health"] == 2
    changed = deepcopy(result)
    changed["runtime_state"]["health"] = 1
    projected = with_legacy_play_state(changed)
    assert projected["play_state"]["health"] == 1


def test_normalization_uses_authoritative_homebrew_for_custom_skills_and_derived_values():
    from systems.cosmere.build import CosmereBuild
    from systems.cosmere.documents import normalize_character_document

    build = homebrew_build()
    expected = CosmereBuild(build, homebrew=homebrew_store())
    result = normalize_character_document(
        {"id": PID, "system": "cosmere", "build": build}, actor_id=PID,
        homebrew=homebrew_store())

    assert result["build"]["skills"]["snd"] == 1
    assert result["derived"]["health_max"] == expected.health_max()
    assert result["derived"]["health_max"] == CosmereBuild(
        build, homebrew={}).health_max() + 3


def test_document_rejects_cross_binding_world_and_era_and_untrusted_history():
    from systems.cosmere.documents import normalize_character_document

    with pytest.raises(RulesValidationError, match="origin world"):
        normalize_character_document({"id": PID, "build": legacy_build(),
                                      "origin_world": "scadrial"}, actor_id=PID)
    with pytest.raises(RulesValidationError, match="era"):
        normalize_character_document({"id": PID, "build": legacy_build(), "era": "era-1"},
                                     actor_id=PID)
    with pytest.raises(RulesValidationError, match="history"):
        normalize_character_document({"id": PID, "build": legacy_build(),
                                      "advancement_history": [{"revision": 100}]}, actor_id=PID)


def test_revisioned_mutation_rejects_stale_and_never_mutates_input():
    from systems.cosmere.documents import (CharacterRevisionError,
                                           apply_revisioned_mutation,
                                           normalize_character_document)

    source = normalize_character_document({"id": PID, "build": legacy_build()}, actor_id=PID)
    original = deepcopy(source)
    changed = apply_revisioned_mutation(
        source, expected_revision=0,
        transform=lambda doc: {**doc, "runtime_state": {"health": 4}},
    )
    assert source == original
    assert changed["revision"] == 1
    assert changed["runtime_state"] == changed["play_state"]
    assert changed["runtime_state"] == {"health": 4, "conditions": {}, "effects": []}
    with pytest.raises(CharacterRevisionError) as error:
        apply_revisioned_mutation(changed, expected_revision=0, transform=lambda doc: doc)
    assert error.value.code == "stale_revision" and error.value.status == 409
