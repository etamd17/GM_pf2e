"""Exact package versions and actor-scoped mixed-world profile dispatch."""
from dataclasses import FrozenInstanceError

import pytest

from systems.cosmere.rules.compiler import compile_registry
from systems.cosmere.rules.registry import load_registry
from systems.cosmere.rules.schema import (CosmereRulesBinding, PackagePin,
                                           RulesScope, RulesValidationError,
                                           to_dict)


def _artifact():
    return {
        "product_id": "test.handbook", "basename": "test.pdf", "edition": "v1",
        "sha256": "a" * 64, "byte_count": 100, "page_count": 20,
        "authority_tier": "handbook", "extraction_class": "rules",
        "spoiler_scope": "player", "rights_state": "private",
    }


def _pack(pack_id, version, kind, worlds, eras=(), dependencies=()):
    return {
        "id": pack_id, "version": version, "kind": kind,
        "dependencies": list(dependencies), "sources": ["test.handbook"],
        "worlds": list(worlds), "eras": list(eras), "spoiler_scope": "player",
        "audit_status": "verified", "records": [],
    }


@pytest.fixture
def versioned_registry(tmp_path):
    authoring = {
        "schema_version": 1,
        "artifacts": [_artifact()],
        "packs": [
            _pack("cosmere.core", "1.0.0", "core", ("cosmere",)),
            _pack("cosmere.stormlight.legacy", "1.0.0", "compatibility", ("roshar",),
                  dependencies=("cosmere.core",)),
            _pack("cosmere.mistborn.core", "1.0.0", "world", ("scadrial",),
                  dependencies=("cosmere.core",)),
            _pack("cosmere.mistborn.era-1", "1.0.0", "world", ("scadrial",),
                  ("era-1",), ("cosmere.core", "cosmere.mistborn.core")),
            _pack("cosmere.mistborn.era-1", "1.1.0", "world", ("scadrial",),
                  ("era-1",), ("cosmere.core", "cosmere.mistborn.core")),
            _pack("cosmere.mistborn.era-2", "1.1.0", "world", ("scadrial",),
                  ("era-2",), ("cosmere.core", "cosmere.mistborn.core")),
        ],
    }
    path = tmp_path / "registry.json"
    path.write_bytes(compile_registry(authoring))
    return load_registry(path)


def _pin(pack):
    return PackagePin(pack.id, pack.version, pack.package_hash)


def test_registry_retains_exact_versions_and_resolves_all_three_identity_fields(
        versioned_registry):
    registry = versioned_registry
    identities = [identity for identity in registry.pack_versions
                  if identity[0] == "cosmere.mistborn.era-1"]
    assert {(identity[1]) for identity in identities} == {"1.0.0", "1.1.0"}
    old = next(pack for identity, pack in registry.pack_versions.items()
               if identity[:2] == ("cosmere.mistborn.era-1", "1.0.0"))
    new = next(pack for identity, pack in registry.pack_versions.items()
               if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    assert registry.resolve_pin(_pin(old)) is old
    assert registry.resolve_pin(_pin(new)) is new
    with pytest.raises(RulesValidationError, match="version/hash"):
        registry.resolve_pin(PackagePin(new.id, new.version, old.package_hash))
    # Existing callers still receive the original first registered seed.
    assert registry.packs["cosmere.mistborn.era-1"].version == "1.0.0"


def test_binding_allows_mixed_worlds_but_never_two_versions_of_one_pack(
        versioned_registry):
    registry = versioned_registry
    core = registry.packs["cosmere.core"]
    stormlight = registry.packs["cosmere.stormlight.legacy"]
    mistborn_core = registry.packs["cosmere.mistborn.core"]
    era1 = next(pack for identity, pack in registry.pack_versions.items()
                if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    binding = CosmereRulesBinding(
        _pin(core), (_pin(stormlight), _pin(mistborn_core), _pin(era1)),
        "roshar", ("era-1",), ("roshar", "scadrial"),
    )
    assert registry.resolve_binding(binding)[-1] is era1
    assert to_dict(binding)["allowed_worlds"] == ["roshar", "scadrial"]
    with pytest.raises(RulesValidationError, match="duplicate bound pack"):
        CosmereRulesBinding(_pin(core), (_pin(era1), _pin(
            registry.packs["cosmere.mistborn.era-1"])), "scadrial", ("era-1",),
            ("scadrial",))


def test_rules_scope_is_immutable_and_rejects_world_era_mismatches():
    scope = RulesScope("scadrial", "era-2")
    assert scope.world == "scadrial" and scope.era == "era-2"
    with pytest.raises(FrozenInstanceError):
        scope.world = "roshar"
    for args in (("roshar", "era-1"), ("cosmere", None), ("scadrial", None)):
        with pytest.raises(RulesValidationError):
            RulesScope(*args)


def test_mixed_binding_dispatches_from_actor_scope_not_default_or_theme(
        versioned_registry):
    from systems.cosmere.mistborn_profile import MistbornRulesProfile
    from systems.cosmere.rules.profiles import (StormlightRulesProfile,
                                                resolve_profile)

    registry = versioned_registry
    core = registry.packs["cosmere.core"]
    stormlight = registry.packs["cosmere.stormlight.legacy"]
    mbcore = registry.packs["cosmere.mistborn.core"]
    era1 = next(pack for identity, pack in registry.pack_versions.items()
                if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    binding = CosmereRulesBinding(_pin(core), (_pin(stormlight), _pin(mbcore), _pin(era1)),
                                  "roshar", ("era-1",), ("roshar", "scadrial"))
    payload = {"rules_binding": to_dict(binding), "cosmere_theme": "stormlight"}
    roshar = resolve_profile({**payload, "origin_world": "roshar"}, registry=registry)
    scadrial = resolve_profile({**payload, "origin_world": "scadrial", "era": "era-1"},
                               registry=registry)
    assert isinstance(roshar, StormlightRulesProfile)
    assert isinstance(scadrial, MistbornRulesProfile)
    # Theme and campaign default cannot reinterpret a persisted actor origin.
    assert isinstance(resolve_profile({**payload, "cosmere_theme": "mistborn",
                                       "origin_world": "roshar"}, registry=registry),
                      StormlightRulesProfile)
    with pytest.raises(RulesValidationError, match="era|scope"):
        scadrial.create_build({"origin_world": "scadrial", "era": "era-2"})


def test_document_promotes_persisted_build_scope_instead_of_campaign_default(
        versioned_registry):
    from systems.cosmere.documents import normalize_character_document
    from systems.cosmere.rules.profiles import resolve_profile

    registry = versioned_registry
    core = registry.packs["cosmere.core"]
    stormlight = registry.packs["cosmere.stormlight.legacy"]
    mbcore = registry.packs["cosmere.mistborn.core"]
    era1 = next(pack for identity, pack in registry.pack_versions.items()
                if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    binding = CosmereRulesBinding(_pin(core), (_pin(stormlight), _pin(mbcore), _pin(era1)),
                                  "roshar", ("era-1",), ("roshar", "scadrial"))
    profile = resolve_profile({"rules_binding": to_dict(binding)}, registry=registry)
    build = {
        "name": "Scoped Misting", "level": 1, "origin_world": "scadrial",
        "era": "era-1", "ancestry": "Human", "starting_path": "agent",
        "heroic_paths": ["agent", "misting"],
        "metalborn_keys": ["mistborn.path.key.misting-snap"],
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2,
                       "awa": 2, "pre": 2},
        "skills": {"allomancy": 2}, "talents": [], "expertises": [],
        "allomancy_rank": 2, "allomantic_powers": {"iron": "nascent"},
        "metal_access": {"allomancy": ["iron"], "feruchemy": []},
    }
    result = normalize_character_document(
        {"id": "cosmere.actor.scoped", "system": "cosmere",
         "rules_binding": to_dict(binding), "build": build}, profile=profile)
    assert (result["origin_world"], result["era"]) == ("scadrial", "era-1")
    assert (result["build"]["origin_world"], result["build"]["era"]) == (
        "scadrial", "era-1")


def test_document_injects_canonical_top_level_scope_before_composite_dispatch(
        versioned_registry):
    from systems.cosmere.documents import normalize_character_document
    from systems.cosmere.rules.profiles import resolve_profile

    registry = versioned_registry
    core = registry.packs["cosmere.core"]
    stormlight = registry.packs["cosmere.stormlight.legacy"]
    mbcore = registry.packs["cosmere.mistborn.core"]
    era1 = next(pack for identity, pack in registry.pack_versions.items()
                if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    binding = CosmereRulesBinding(_pin(core), (_pin(stormlight), _pin(mbcore), _pin(era1)),
                                  "roshar", ("era-1",), ("roshar", "scadrial"))
    profile = resolve_profile({"rules_binding": to_dict(binding)}, registry=registry)
    build = {
        "name": "Top-level Scoped Misting", "level": 1,
        "ancestry": "Human", "starting_path": "agent",
        "heroic_paths": ["agent", "misting"],
        "metalborn_keys": ["mistborn.path.key.misting-snap"],
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2,
                       "awa": 2, "pre": 2},
        "skills": {"allomancy": 2}, "talents": [], "expertises": [],
        "allomancy_rank": 2,
        "allomantic_powers": {"iron": "nascent"},
        "metal_access": {"allomancy": ["iron"], "feruchemy": []},
    }
    result = normalize_character_document(
        {"id": "cosmere.actor.top-scoped", "system": "cosmere",
         "origin_world": "scadrial", "era": "era-1",
         "rules_binding": to_dict(binding), "build": build}, profile=profile)
    assert result["build"]["origin_world"] == "scadrial"
    assert result["build"]["era"] == "era-1"


def test_composite_encounter_budget_dispatches_from_world_projection_and_rejects_mixing(
        versioned_registry, monkeypatch):
    from systems.cosmere.rules.profiles import resolve_profile

    registry = versioned_registry
    core = registry.packs["cosmere.core"]
    stormlight = registry.packs["cosmere.stormlight.legacy"]
    mbcore = registry.packs["cosmere.mistborn.core"]
    era1 = next(pack for identity, pack in registry.pack_versions.items()
                if identity[:2] == ("cosmere.mistborn.era-1", "1.1.0"))
    binding = CosmereRulesBinding(
        _pin(core), (_pin(stormlight), _pin(mbcore), _pin(era1)),
        "roshar", ("era-1",), ("roshar", "scadrial"))
    profile = resolve_profile({"rules_binding": to_dict(binding)}, registry=registry)
    delegated = []

    class Delegate:
        def encounter_budget(self, actors, adversaries):
            return {"strategy": "scoped", "actors": len(actors),
                    "adversaries": len(adversaries)}

    def delegate(scope):
        delegated.append(scope)
        return Delegate()

    monkeypatch.setattr(profile, "_delegate", delegate)
    result = profile.encounter_budget(
        [{"id": "pc.one", "world": "scadrial", "era": "era-1"}],
        [{"id": "foe.one", "world": "scadrial", "era": "era-1"}])
    assert result["strategy"] == "scoped"
    assert delegated == [{"origin_world": "scadrial", "era": "era-1"}]

    with pytest.raises(RulesValidationError, match="one world|mixed-world"):
        profile.encounter_budget(
            [{"id": "pc.roshar", "world": "roshar", "era": None}],
            [{"id": "foe.scadrial", "world": "scadrial", "era": "era-1"}])
