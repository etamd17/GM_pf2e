"""Server-derived Mistborn goal entitlements and auditable advancement."""
from copy import deepcopy
import uuid

import pytest

from systems.cosmere.rules.compiler import compile_registry
from systems.cosmere.rules.registry import load_registry
from systems.cosmere.rules.schema import (CosmereRulesBinding, PackagePin,
                                           RulesScope, RulesValidationError,
                                           to_dict)


PID = "3520ca4cb56243f095ec8a5209174a68"


def _registry(tmp_path):
    artifact = {"product_id": "mistborn.handbook", "basename": "book.pdf",
                "edition": "v1.02", "sha256": "a" * 64, "byte_count": 100,
                "page_count": 410, "authority_tier": "handbook",
                "extraction_class": "rules", "spoiler_scope": "player",
                "rights_state": "private"}
    def record(record_id, kind, prerequisites=(), eras=("era-1", "era-2"),
               path=None, effect_ops=()):
        data = {"id": record_id, "version": "1.1.0", "kind": kind,
                "name": record_id.rsplit(".", 1)[-1].replace("-", " ").title(),
                "summary": "Test fixture for authoritative Mistborn build validation.",
                "citations": [{"artifact_id": "mistborn.handbook",
                               "page_start": 138, "page_end": 140}],
                "worlds": ["scadrial"], "eras": list(eras),
                "spoiler_scope": "player", "automation": "calculated",
                "audit_status": "verified", "parameters": {"data": {}} ,
                "prerequisites": list(prerequisites)}
        if path:
            data["parameters"]["data"]["path"] = path
        if effect_ops:
            data["parameters"]["data"]["effect_ops"] = list(effect_ops)
        return data

    records = [
        record("mistborn.path.key.misting-snap", "path",
               ("ancestry:human|koloss_blooded", "no_snap_or_heritage"),
               path="misting", effect_ops=("atium_starts_unlocked",)),
        record("mistborn.path.talent.invested", "talent",
               ("allomantic_path_key",)),
        record("mistborn.path.talent.trace-metals", "talent",
               ("allomancy_rank>=3", "talent:invested")),
        record("mistborn.test.talent.unmodeled", "talent",
               ("unmodeled_build_fact",)),
        record("mistborn.test.talent.level-two", "talent"),
        record("mistborn.test.talent.creation-one", "talent"),
        record("mistborn.test.talent.creation-two", "talent"),
    ]

    def pack(pack_id, kind, dependencies, eras=(), records=()):
        return {"id": pack_id, "version": "1.1.0", "kind": kind,
                "dependencies": list(dependencies), "sources": ["mistborn.handbook"],
                "worlds": ["cosmere"] if pack_id == "cosmere.core" else ["scadrial"],
                "eras": list(eras), "spoiler_scope": "player",
                "audit_status": "verified", "records": list(records)}
    path = tmp_path / "registry.json"
    path.write_bytes(compile_registry({"schema_version": 1, "artifacts": [artifact],
        "packs": [pack("cosmere.core", "core", ()),
                  pack("cosmere.mistborn.core", "world", ("cosmere.core",),
                       records=records),
                  pack("cosmere.mistborn.era-1", "world",
                       ("cosmere.core", "cosmere.mistborn.core"), ("era-1",))]}))
    return load_registry(path)


def _profile_and_binding(tmp_path):
    from systems.cosmere.mistborn_profile import MistbornRulesProfile
    registry = _registry(tmp_path)
    def pin(pack_id):
        pack = registry.packs[pack_id]
        return PackagePin(pack.id, pack.version, pack.package_hash)
    binding = CosmereRulesBinding(pin("cosmere.core"),
                                  (pin("cosmere.mistborn.core"),
                                   pin("cosmere.mistborn.era-1")),
                                  "scadrial", ("era-1",), ("scadrial",))
    return MistbornRulesProfile(binding, registry, scope=RulesScope("scadrial", "era-1")), binding


def _build(level=1):
    skills = {"allomancy": 2, "ath": 1, "agi": 1, "hwp": 1, "prc": 1}
    talents = [{"id": "mistborn.path.key.misting-snap"},
               {"id": "mistborn.path.talent.invested"}]
    if level == 2:
        skills.update(ath=2, agi=2)
        talents.append({"id": "mistborn.test.talent.level-two"})
    return {"name": "Misting", "level": level, "origin_world": "scadrial",
            "era": "era-1", "ancestry": "Human", "starting_path": "agent",
            "heroic_paths": ["agent", "misting"],
            "metalborn_keys": ["mistborn.path.key.misting-snap"],
            "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
            "skills": skills, "talents": talents, "expertises": [],
            "allomancy_rank": 2, "feruchemy_rank": 0,
            "allomantic_powers": {"iron": "nascent"}, "feruchemical_powers": {},
            "metal_access": {"allomancy": ["iron"], "feruchemy": []}}


def _ordinary_build(*, acquire_misting=False, exact_key_talent=False):
    talents = [
        {"id": "mistborn.test.talent.creation-one"},
        {"id": "mistborn.test.talent.creation-two"},
    ]
    skills = {"ath": 1, "agi": 1, "hwp": 1, "prc": 1}
    if acquire_misting:
        talents.append({"id": ("mistborn.path.key.misting-snap"
                               if exact_key_talent
                               else "mistborn.test.talent.level-two")})
        skills.update(ath=2, allomancy=1)
    return {
        "name": "Agent", "level": 2 if acquire_misting else 1,
        "origin_world": "scadrial", "era": "era-1", "ancestry": "Human",
        "starting_path": "agent",
        "heroic_paths": (["agent", "misting"] if acquire_misting else ["agent"]),
        "metalborn_keys": (["mistborn.path.key.misting-snap"]
                           if acquire_misting else []),
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2,
                       "awa": 2, "pre": 2},
        "skills": skills, "talents": talents, "expertises": [],
        "allomancy_rank": 1 if acquire_misting else 0, "feruchemy_rank": 0,
        "allomantic_powers": ({"iron": "nascent"} if acquire_misting else {}),
        "feruchemical_powers": {},
        "metal_access": {"allomancy": (["iron"] if acquire_misting else []),
                         "feruchemy": []},
    }


def _document(tmp_path):
    from systems.cosmere.documents import normalize_character_document
    profile, binding = _profile_and_binding(tmp_path)
    source = {"id": PID, "system": "cosmere", "origin_world": "scadrial",
              "era": "era-1", "rules_binding": to_dict(binding), "build": _build(),
              "runtime_state": {"goals": [{
                  "id": "mistborn.goal.instance.iron", "type": "mistborn.goal.train-power",
                  "power_id": "mistborn.power.allomancy.iron", "completed": True,
                  "rewards": {},
              }]}}
    return normalize_character_document(source, profile=profile), profile, binding


def _request(document, candidate, goal_ids=()):
    return {"expected_revision": document["revision"], "target_level": 2,
            "request_id": uuid.uuid4().hex, "goal_ids": list(goal_ids),
            "build": candidate}


def test_document_scope_and_runtime_use_the_actor_profile_registry(tmp_path):
    document, profile, binding = _document(tmp_path)
    assert document["origin_world"] == "scadrial" and document["era"] == "era-1"
    assert document["rules_binding"] == to_dict(binding)
    assert document["derived"]["investiture_max"] == 4
    assert document["runtime_state"]["mistborn"]["allomancy"][
        "current_investiture"] == 4
    assert document["runtime_state"] == document["play_state"]


def test_client_cannot_inject_goal_entitlement_or_unlock_without_selected_goal(tmp_path):
    from systems.cosmere.advancement import AdvancementError, commit_advancement
    document, profile, _binding = _document(tmp_path)
    candidate = _build(2)
    candidate["allomantic_powers"]["iron"] = "unlocked"
    candidate["goal_entitlements"] = [
        "mistborn.entitlement.power.allomancy.iron.unlock"]
    with pytest.raises(AdvancementError, match="goal entitlement") as error:
        commit_advancement(document, _request(document, candidate), profile=profile)
    assert error.value.code == "profile_validation_failed"


def test_completed_goal_mints_exact_entitlement_once_and_receipt_pins_packages(tmp_path):
    from systems.cosmere.advancement import (AdvancementError, commit_advancement,
                                             revert_advancement)
    document, profile, binding = _document(tmp_path)
    candidate = _build(2)
    candidate["allomantic_powers"]["iron"] = "unlocked"
    advanced = commit_advancement(
        document, _request(document, candidate, ("mistborn.goal.instance.iron",)),
        profile=profile)
    entitlement = "mistborn.entitlement.power.allomancy.iron.unlock"
    assert advanced["build"]["goal_entitlements"] == [entitlement]
    event = advanced["advancement_history"][-1]
    assert event["choices"]["goal_entitlements"] == [entitlement]
    assert event["package_pins"] == [to_dict(binding.core_package)] + [
        to_dict(pin) for pin in binding.enabled_packs]
    assert advanced["runtime_state"]["goals"][0]["consumed_by"] == event["id"]

    again = _build(3)
    with pytest.raises(AdvancementError) as replay:
        commit_advancement(advanced, {**_request(advanced, again,
            ("mistborn.goal.instance.iron",)), "target_level": 3}, profile=profile)
    assert replay.value.code == "goal_unavailable"

    reverted = revert_advancement(advanced, {
        "expected_revision": advanced["revision"], "request_id": uuid.uuid4().hex},
        profile=profile)
    assert reverted["build"]["allomantic_powers"]["iron"] == "nascent"
    assert reverted["build"].get("goal_entitlements", []) == []
    assert "consumed_by" not in reverted["runtime_state"]["goals"][0]


def test_goal_entitlements_are_derived_from_goal_type_not_reward_payload(tmp_path):
    from systems.cosmere.advancement import AdvancementError, commit_advancement
    document, profile, _binding = _document(tmp_path)
    forged = deepcopy(document)
    forged["runtime_state"]["goals"][0]["type"] = "ordinary.goal"
    forged["runtime_state"]["goals"][0]["rewards"] = {
        "entitlements": ["mistborn.entitlement.power.allomancy.iron.unlock"]}
    forged["play_state"] = deepcopy(forged["runtime_state"])
    candidate = _build(2)
    candidate["allomantic_powers"]["iron"] = "unlocked"
    with pytest.raises(AdvancementError) as error:
        commit_advancement(forged, _request(
            forged, candidate, ("mistborn.goal.instance.iron",)), profile=profile)
    assert error.value.code == "invalid_goal_reward"


def test_bound_catalog_rejects_unknown_talents_and_unmet_or_unknown_prerequisites(
        tmp_path):
    profile, _binding = _profile_and_binding(tmp_path)
    unknown = _build()
    unknown["talents"][-1] = {"id": "mistborn.path.talent.does-not-exist"}
    assert any("bound package" in issue.lower()
               for issue in profile.validate_build(unknown))

    unmet = _build()
    unmet["talents"][-1] = {"id": "mistborn.path.talent.trace-metals"}
    assert any("prerequisites" in issue.lower()
               for issue in profile.validate_build(unmet))

    unsupported = _build()
    unsupported["talents"][-1] = {"id": "mistborn.test.talent.unmodeled"}
    assert any("unsupported prerequisite" in issue.lower()
               for issue in profile.validate_build(unsupported))


def test_character_creation_rejects_client_minted_entitlements_and_unlocks(tmp_path):
    from systems.cosmere.documents import normalize_character_document

    profile, binding = _profile_and_binding(tmp_path)
    forged = _build()
    forged["goal_entitlements"] = [
        "mistborn.entitlement.power.allomancy.iron.unlock"]
    forged["allomantic_powers"]["iron"] = "unlocked"
    source = {"id": PID, "system": "cosmere", "origin_world": "scadrial",
              "era": "era-1", "rules_binding": to_dict(binding),
              "build": forged}
    with pytest.raises(RulesValidationError, match="goal entitlement|unearned"):
        normalize_character_document(source, profile=profile)

    forged.pop("goal_entitlements")
    with pytest.raises(RulesValidationError, match="server goal entitlement"):
        normalize_character_document(
            {**source, "build": forged}, profile=profile)


def test_character_creation_rejects_client_minted_bonus_grants(tmp_path):
    from systems.cosmere.documents import normalize_character_document

    profile, binding = _profile_and_binding(tmp_path)
    forged = _build()
    forged["advancement_bonus_grants"] = {"skill_ranks": 99}
    source = {"id": PID, "system": "cosmere", "origin_world": "scadrial",
              "era": "era-1", "rules_binding": to_dict(binding),
              "build": forged}
    with pytest.raises(RulesValidationError, match="bonus grants"):
        normalize_character_document(source, profile=profile)


def test_advancement_requires_the_exact_catalog_path_key_talent(tmp_path):
    from systems.cosmere.advancement import AdvancementError, preview_advancement
    from systems.cosmere.documents import normalize_character_document

    profile, binding = _profile_and_binding(tmp_path)
    source = {
        "id": PID, "system": "cosmere", "origin_world": "scadrial",
        "era": "era-1", "rules_binding": to_dict(binding),
        "build": _ordinary_build(), "runtime_state": {"goals": []},
    }
    document = normalize_character_document(source, profile=profile)

    named_only = _ordinary_build(acquire_misting=True, exact_key_talent=False)
    with pytest.raises(AdvancementError, match="exact newly acquired path key talent"):
        preview_advancement(document, _request(document, named_only), profile=profile)

    exact = _ordinary_build(acquire_misting=True, exact_key_talent=True)
    assert preview_advancement(
        document, _request(document, exact), profile=profile)["eligible"] is True


def test_bound_misting_atium_power_starts_unlocked_without_a_goal_reward(tmp_path):
    from systems.cosmere.documents import normalize_character_document

    profile, binding = _profile_and_binding(tmp_path)
    nascent = _build()
    nascent["allomantic_powers"] = {"atium": "nascent"}
    nascent["metal_access"]["allomancy"] = ["atium"]
    source = {
        "id": PID, "system": "cosmere", "origin_world": "scadrial",
        "era": "era-1", "rules_binding": to_dict(binding), "build": nascent,
    }
    with pytest.raises(RulesValidationError, match="atium starts unlocked"):
        normalize_character_document(source, profile=profile)

    unlocked = deepcopy(nascent)
    unlocked["allomantic_powers"]["atium"] = "unlocked"
    normalized = normalize_character_document(
        {**source, "build": unlocked}, profile=profile)
    assert normalized["build"]["allomantic_powers"] == {"atium": "unlocked"}


def test_server_goal_bonus_grants_persist_and_revert_from_advancement_history(
        tmp_path):
    from systems.cosmere.advancement import commit_advancement, revert_advancement
    from systems.cosmere.documents import normalize_character_document

    document, profile, _binding = _document(tmp_path)
    document["runtime_state"]["goals"][0]["rewards"] = {"skill_rank": 1}
    document["play_state"] = deepcopy(document["runtime_state"])
    candidate = _build(2)
    candidate["skills"]["hwp"] = 2
    advanced = commit_advancement(
        document, _request(document, candidate, ("mistborn.goal.instance.iron",)),
        profile=profile)
    assert advanced["build"]["advancement_bonus_grants"]["skill_ranks"] == 1
    assert normalize_character_document(advanced, profile=profile)["build"][
        "advancement_bonus_grants"]["skill_ranks"] == 1

    reverted = revert_advancement(advanced, {
        "expected_revision": advanced["revision"], "request_id": uuid.uuid4().hex},
        profile=profile)
    assert reverted["build"]["advancement_bonus_grants"]["skill_ranks"] == 0
    assert normalize_character_document(reverted, profile=profile)["build"][
        "advancement_bonus_grants"]["skill_ranks"] == 0
