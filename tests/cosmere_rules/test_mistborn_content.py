"""Contracts for the source-audited Mistborn authoring shards."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from systems.cosmere.rules import mistborn_authoring as authoring
from systems.cosmere.rules.paths import registry_path
from systems.cosmere.rules.schema import RuleRecord, from_dict


ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = registry_path().parent
MISTBORN_DIR = DATA_PATH / "mistborn"
COVERAGE_PATH = DATA_PATH / "mistborn_coverage.json"
pytestmark = pytest.mark.skipif(not registry_path().is_file(),
                                reason="private Cosmere rules data is not installed")


def load_bundle():
    return authoring.load_compiled_bundle(MISTBORN_DIR, COVERAGE_PATH)


def records_by_id(bundle):
    return {
        record["id"]: record
        for package in bundle["packages"]
        for record in package["records"]
    }


def test_compiler_canonicalizes_audit_ids_and_rejects_semantic_conflicts():
    assert authoring.canonical_id("mistborn.path.key.mistborn_snap") == "mistborn.path.key.mistborn-snap"
    assert authoring.canonical_id("mistborn.era.1") == "mistborn.era.n-1"
    assert authoring.canonical_source_id("coppermind") == (
        "mistborn.adversary.feruchemist.resource.coppermind"
    )

    first = {
        "id": "mistborn.rule.same_record",
        "version": "1.1.0",
        "kind": "rule",
        "name": "Same Record",
        "summary": "One independently summarized rule.",
        "worlds": ["scadrial"],
        "eras": ["era-1"],
        "spoiler_scope": "player",
        "automation": "tracked",
        "audit_status": "verified",
        "prerequisites": [],
        "references": [],
        "citations": [{"artifact_id": "mistborn.handbook", "page_start": 20, "page_end": 20}],
        "parameters": {"source_pages": [{
            "artifact_id": "mistborn.handbook",
            "printed_page_start": 17,
            "printed_page_end": 17,
            "pdf_page_start": 20,
            "pdf_page_end": 20,
        }], "value": 2},
    }
    duplicate = deepcopy(first)
    duplicate["citations"] = [
        {"artifact_id": "mistborn.handbook", "page_start": 20, "page_end": 20},
        {"artifact_id": "mistborn.handbook", "page_start": 21, "page_end": 21},
    ]
    merged = authoring.merge_normalized_records([first, duplicate])
    assert list(merged) == ["mistborn.rule.same-record"]
    assert merged["mistborn.rule.same-record"]["citations"][-1]["page_start"] == 21

    conflicting = deepcopy(duplicate)
    conflicting["parameters"]["value"] = 3
    with pytest.raises(authoring.MistbornAuthoringError, match="semantic conflict"):
        authoring.merge_normalized_records([first, conflicting])


def test_compiled_packages_are_immutable_v110_successors_to_empty_seeds():
    bundle = load_bundle()
    packages = {package["id"]: package for package in bundle["packages"]}
    assert set(packages) == {
        "cosmere.mistborn.core",
        "cosmere.mistborn.era-1",
        "cosmere.mistborn.era-2",
    }
    assert {package["version"] for package in packages.values()} == {"1.1.0"}
    assert packages["cosmere.mistborn.core"]["dependencies"] == ["cosmere.core"]
    assert packages["cosmere.mistborn.era-1"]["dependencies"] == [
        "cosmere.core", "cosmere.mistborn.core"
    ]
    assert packages["cosmere.mistborn.era-2"]["dependencies"] == [
        "cosmere.core", "cosmere.mistborn.core"
    ]
    assert all(len(package["package_hash"]) == 64 for package in packages.values())
    assert len(bundle["manifest"]["bundle_hash"]) == 64

    shared = json.loads((DATA_PATH / "authoring.json").read_text(encoding="utf-8"))
    packs_by_identity = {
        (pack["id"], pack["version"]): pack for pack in shared["packs"]
    }
    assert packs_by_identity[("cosmere.mistborn.era-1", "1.0.0")]["records"] == []
    assert packs_by_identity[("cosmere.mistborn.era-2", "1.0.0")]["records"] == []
    assert len(packs_by_identity[("cosmere.mistborn.era-1", "1.1.0")]["records"]) == 71
    assert len(packs_by_identity[("cosmere.mistborn.era-2", "1.1.0")]["records"]) == 152


def test_source_audit_counts_and_high_risk_catalog_counts_are_exact():
    bundle = load_bundle()
    manifest = bundle["manifest"]
    coverage = bundle["coverage"]
    assert manifest["source_audits"] == {
        "mistborn-content-audit": {
            "sha256": "91b81b4aab5222edaeb66b9b4b204015670b5e615742ee50f4b0c4372947543b",
            "declared_id_count": 667,
            "compiled_record_count": 658,
        },
        "mistborn-powers-audit": {
            "sha256": "a209ab3a840a546a66b748c938a6cdc819f15dbe0dbc899f7a8dbe52039120f8",
            "declared_id_count": 260,
            "compiled_record_count": 260,
        },
    }
    assert manifest["record_count"] == 918
    assert coverage["summary"]["record_count"] == 918
    assert manifest["verified_content_counts"] == {
        "adversary_actions": 188,
        "adversary_era_labels": {"both": 24, "era1": 5, "era2": 9},
        "adversary_roles": {"boss": 5, "minion": 8, "rival": 25},
        "adversary_tiers": {"1": 14, "2": 11, "3": 10, "4": 3},
        "adversaries": 38,
        "advanced_crafting_features": 6,
        "armor": 8,
        "currencies": 3,
        "equipment_entries": 71,
        "firearm_prototype_components": 9,
        "modules_by_family": {"allomancy": 15, "ancestry": 2, "feruchemy": 16, "hemalurgy": 12},
        "modules_total": 45,
        "pc_hemalurgic_spike_rewards": 12,
        "special_tables": 18,
        "starting_kits": 7,
        "weapon_traits": 17,
        "weapons": 35,
    }

    counts = coverage["summary"]["source_counts"]
    assert counts["powers.goals"] == 4
    assert counts["powers.path_keys"] == 5
    assert counts["powers.path_talents"] == 19
    assert counts["powers.allomancy_actions"] == 20
    assert counts["powers.feruchemy_actions"] == 34
    assert counts["powers.power_talents"] == 157
    assert counts["content.weapons"] == 35
    assert counts["content.armor"] == 8
    assert counts["content.equipment"] == 71
    assert counts["content.adversaries"] == 38
    assert counts["content.adversary_actions"] == 188
    assert counts["content.modules"] == 45
    assert counts["content.special_tables"] == 18


def test_every_record_has_scope_payload_disposition_and_exact_page_mapping():
    bundle = load_bundle()
    records = records_by_id(bundle)
    assert len(records) == 918
    for record in records.values():
        assert record["worlds"] == ["scadrial"]
        assert record["automation"] in {"calculated", "tracked", "reference", "gm_adjudicated"}
        assert isinstance(record["prerequisites"], list)
        assert isinstance(record["references"], list)
        assert record["citations"]
        assert record["parameters"]
        pages = record["parameters"]["source_pages"]
        assert pages
        for page in pages:
            assert page["pdf_page_start"] == page["printed_page_start"] + 3
            assert page["pdf_page_end"] == page["printed_page_end"] + 3
        citation_pages = {
            (citation["artifact_id"], citation["page_start"], citation["page_end"])
            for citation in record["citations"]
        }
        assert citation_pages == {
            (page["artifact_id"], page["pdf_page_start"], page["pdf_page_end"])
            for page in pages
        }


def test_all_compiled_records_cross_the_shared_rule_record_boundary():
    records = records_by_id(load_bundle())
    assert len(records) == 918
    for record in records.values():
        assert from_dict(RuleRecord, record).id == record["id"]


def test_bendalloy_rounding_formulas_are_structured_normalized_data():
    bendalloy = records_by_id(load_bundle())["mistborn.allomancy.bendalloy.action.burn"]
    effect_ops = bendalloy["parameters"]["data"]["effect_ops"]
    assert effect_ops[1:3] == [
        {
            "operation": "assign",
            "target": "conversation_or_endeavor_contributions",
            "expression": {
                "operation": "ceiling_divide",
                "addends": [1, {"variable": "spend"}],
                "divisor": 2,
            },
        },
        {
            "operation": "assign",
            "target": "ongoing_rounds_elapsed",
            "expression": {
                "operation": "ceiling_divide",
                "addends": [1, {"variable": "spend"}],
                "divisor": 2,
            },
        },
    ]
    assert effect_ops[0] == "bonus_actions_per_occupant=1+spend"
    assert effect_ops[3:] == [
        "crossing:lose_focus=spend_and_stun_if_zero",
        "cross_boundary_attack:disadvantages=spend+dangerous+requires_opportunity_to_hit",
        "one_personal_bubble",
        "overlap:add_spend",
    ]


def test_record_validation_rejects_a_citation_beyond_the_source_pdf():
    record = deepcopy(records_by_id(load_bundle())["mistborn.core.allomancy-governing-attribute"])
    record["citations"] = [
        {"artifact_id": "mistborn.handbook", "page_start": 411, "page_end": 411}
    ]
    record["parameters"]["source_pages"] = [{
        "artifact_id": "mistborn.handbook",
        "printed_page_start": 408,
        "printed_page_end": 408,
        "pdf_page_start": 411,
        "pdf_page_end": 411,
    }]
    with pytest.raises(authoring.MistbornAuthoringError, match="exceeds source page count"):
        authoring.validate_normalized_record(record)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"printed_page_start": "17"}, "invalid printed page"),
        ({"printed_page_end": True}, "invalid printed page"),
        ({"printed_page_start": 18, "printed_page_end": 17}, "invalid printed page range"),
        ({"pdf_page_start": "20"}, "invalid PDF page"),
        ({"pdf_page_end": True}, "invalid PDF page"),
        ({"pdf_page_start": 21, "pdf_page_end": 20}, "invalid PDF page range"),
        ({"printed_page_start": 9999, "printed_page_end": 9999}, r"must be \+3"),
        ({"pdf_page_start": 21, "pdf_page_end": 21}, r"must be \+3"),
    ],
)
def test_record_validation_rejects_invalid_printed_pdf_page_mappings(updates, message):
    record = deepcopy(records_by_id(load_bundle())["mistborn.core.allomancy-governing-attribute"])
    record["parameters"]["source_pages"][0].update(updates)
    with pytest.raises(authoring.MistbornAuthoringError, match=message):
        authoring.validate_normalized_record(record)


@pytest.mark.parametrize(
    "payload",
    [
        {"source_text": "copied prose"},
        {"page_image": "raw pixels"},
        {"note": "data:image/png;base64,iVBORw0KGgo="},
    ],
)
def test_audit_payload_normalization_rejects_raw_source_and_media(payload):
    with pytest.raises(authoring.MistbornAuthoringError, match="prohibited"):
        authoring.normalize_audit_payload(payload)


@pytest.mark.parametrize(
    ("audit_name", "payload"),
    [
        ("powers", {"source_text": "copied prose"}),
        ("content", {"page_image": "raw pixels"}),
        ("content", {"note": "data:image/png;base64,iVBORw0KGgo="}),
    ],
)
def test_compile_audits_fails_closed_on_raw_source_and_media(audit_name, payload):
    powers = {"source_artifacts": []}
    content = {"source_artifacts": []}
    (powers if audit_name == "powers" else content).update(payload)
    with pytest.raises(authoring.MistbornAuthoringError, match="prohibited"):
        authoring.compile_audits(powers, content)


@pytest.mark.parametrize("invalid_label", ["nonsense", "bogus-era1", "era2ish"])
def test_era_scope_rejects_each_invalid_label_even_after_a_valid_label(invalid_label):
    with pytest.raises(authoring.MistbornAuthoringError, match="unknown era scope"):
        authoring.normalize_era_scope(["era1", invalid_label])


def test_literal_encounter_threat_table_preserves_zero_cells():
    record = records_by_id(load_bundle())["mistborn.encounter-budget.core"]
    assert record["parameters"]["data"]["level_delta_table"] == [
        {"adversary_level_relative_to_party": -3, "minion": 4, "rival": 8, "boss": 32},
        {"adversary_level_relative_to_party": -2, "minion": 2, "rival": 4, "boss": 16},
        {"adversary_level_relative_to_party": -1, "minion": 1, "rival": 2, "boss": 8},
        {"adversary_level_relative_to_party": 0, "minion": 0.5, "rival": 1, "boss": 4},
        {"adversary_level_relative_to_party": 1, "minion": 0.25, "rival": 0.5, "boss": 2},
        {"adversary_level_relative_to_party": 2, "minion": 0, "rival": 0.25, "boss": 1},
        {"adversary_level_relative_to_party": 3, "minion": 0, "rival": 0, "boss": 0.5},
    ]


def test_metallic_art_records_keep_profile_consumable_structured_mechanics():
    records = records_by_id(load_bundle())
    allomancy = records["mistborn.allomancy.aluminum.action.burn"]
    assert allomancy["parameters"]["data"]["action_cost"] == "one"
    assert allomancy["parameters"]["data"]["metal"] == "aluminum"
    assert "investiture:1_then_set_current_to_0" in allomancy["parameters"]["data"]["resources"]
    feruchemy = records["mistborn.feruchemy.aluminum.action.tap-identity"]
    assert feruchemy["parameters"]["data"]["duration"] == "feruchemy_rank_rounds"
    assert feruchemy["parameters"]["data"]["resources"] == ["charges:1", "maintain:free_1"]
    assert records["mistborn.core.allomancy-governing-attribute"]["automation"] == "calculated"
    assert records["mistborn.core.feruchemy-governing-attribute"]["automation"] == "calculated"


def test_every_exact_reference_resolves_and_selectors_match():
    bundle = load_bundle()
    result = authoring.validate_reference_graph(
        records_by_id(bundle), bundle["manifest"]["external_references"]
    )
    assert result == {"exact": 0, "selectors": 0}
