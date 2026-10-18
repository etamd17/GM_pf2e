"""Strict contracts for retained Paizo class-publication evidence."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil

import pytest

from systems.pf2e.rules.ingestion.class_source_evidence import (
    class_source_evidence_hashes,
    verify_class_source_evidence_bundle,
)
from systems.pf2e.rules.manifest import canonical_json, digest, read_json
from systems.pf2e.rules.validation import RulesValidationError


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "evidence"
    / "paizo"
    / "pf2e-class-sources-2026-10-10.1"
)
EXPECTED = {
    "pf2e.source.battlecry": {
        "overlay_title": "Battlecry!",
        "official_product_title": "Pathfinder Battlecry!",
        "product_ids": {"hardcover": "PZO12008-HC", "pdf": "PZO12008E"},
        "release_date": "2025-07-31",
        "release_evidence": "https://store.paizo.com/pathfinder-battlecry/",
        "class_rule_ids": ["pf2e.class.commander", "pf2e.class.guardian"],
    },
    "pf2e.source.dark-archive-remastered": {
        "overlay_title": "Dark Archive (Remastered)",
        "official_product_title": "Pathfinder Dark Archive (Remastered)",
        "product_ids": {"hardcover": "PZO12012-HC", "pdf": "PZO12012E"},
        "release_date": "2026-02-04",
        "release_evidence": "https://paizo.com/blog/find-your-path-february-2026",
        "class_rule_ids": ["pf2e.class.psychic", "pf2e.class.thaumaturge"],
    },
    "pf2e.source.guns-and-gears-remastered": {
        "overlay_title": "Guns & Gears (Remastered)",
        "official_product_title": "Pathfinder Guns & Gears (Remastered)",
        "product_ids": {"hardcover": "PZO12010-HC", "pdf": "PZO12010E"},
        "release_date": "2025-02-05",
        "release_evidence": (
            "https://store.paizo.com/pathfinder-guns-gears-remastered-pdf/"
        ),
        "class_rule_ids": ["pf2e.class.gunslinger", "pf2e.class.inventor"],
    },
    "pf2e.source.impossible-magic": {
        "overlay_title": "Impossible Magic",
        "official_product_title": "Pathfinder Impossible Magic",
        "product_ids": {"hardcover": "PZO12014-HC", "pdf": "PZO12014E"},
        "release_date": "2026-07-30",
        "release_evidence": "https://store.paizo.com/pathfinder-impossible-magic/",
        "class_rule_ids": [
            "pf2e.class.magus",
            "pf2e.class.necromancer",
            "pf2e.class.runesmith",
            "pf2e.class.summoner",
        ],
    },
    "pf2e.source.player-core": {
        "overlay_title": "Player Core",
        "official_product_title": "Pathfinder Player Core",
        "product_ids": {"hardcover": "PZO12001-HC", "pdf": "PZO12001E"},
        "release_date": "2023-11-15",
        "release_evidence": "https://store.paizo.com/pathfinder-player-core/",
        "class_rule_ids": [
            "pf2e.class.bard",
            "pf2e.class.cleric",
            "pf2e.class.druid",
            "pf2e.class.fighter",
            "pf2e.class.ranger",
            "pf2e.class.rogue",
            "pf2e.class.witch",
            "pf2e.class.wizard",
        ],
    },
    "pf2e.source.player-core-2": {
        "overlay_title": "Player Core 2",
        "official_product_title": "Pathfinder Player Core 2",
        "product_ids": {"hardcover": "PZO12004-HC", "pdf": "PZO12004E"},
        "release_date": "2024-08-01",
        "release_evidence": "https://store.paizo.com/pathfinder-player-core-2/",
        "class_rule_ids": [
            "pf2e.class.alchemist",
            "pf2e.class.barbarian",
            "pf2e.class.champion",
            "pf2e.class.investigator",
            "pf2e.class.monk",
            "pf2e.class.oracle",
            "pf2e.class.sorcerer",
            "pf2e.class.swashbuckler",
        ],
    },
    "pf2e.source.rage-of-elements": {
        "overlay_title": "Rage of Elements",
        "official_product_title": "Pathfinder Rage of Elements",
        "product_ids": {"hardcover": "PZO2113-HC", "pdf": "PZO2113E"},
        "release_date": "2023-08-03",
        "release_evidence": "https://store.paizo.com/pathfinder-rage-of-elements/",
        "class_rule_ids": ["pf2e.class.kineticist"],
    },
    "pf2e.source.war-of-immortals": {
        "overlay_title": "War of Immortals",
        "official_product_title": "Pathfinder: War of Immortals",
        "product_ids": {"hardcover": "PZO12006-HC", "pdf": "PZO12006E"},
        "release_date": "2024-10-30",
        "release_evidence": "https://store.paizo.com/pathfinder-war-of-immortals/",
        "class_rule_ids": ["pf2e.class.animist", "pf2e.class.exemplar"],
    },
}
DOSSIER_FIELDS = {
    "schema_version",
    "evidence_id",
    "source_id",
    "overlay_title",
    "official_product_title",
    "publisher",
    "product_ids",
    "release_date",
    "class_rule_ids",
    "official_urls",
    "artifact_observation",
    "printing_observation",
    "revision_observation",
    "rights_observation",
    "supported_review_scopes",
}


def _copy_bundle(tmp_path: Path) -> Path:
    target = tmp_path / "bundle"
    shutil.copytree(BUNDLE, target)
    return target


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _write_document(path: Path, document: dict, *, canonical: bool = True) -> None:
    if canonical:
        path.write_bytes(canonical_json(document))
    else:
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")


def _rehash_manifest(root: Path, changed_dossier: str | None = None) -> dict:
    path = _manifest_path(root)
    manifest = read_json(path.read_bytes())
    if changed_dossier is not None:
        for reference in manifest["dossiers"]:
            if reference["path"] == changed_dossier:
                reference["sha256"] = digest((root / changed_dossier).read_bytes())
                break
        else:
            raise AssertionError(f"missing dossier reference: {changed_dossier}")
    without_hash = deepcopy(manifest)
    without_hash.pop("bundle_hash")
    manifest["bundle_hash"] = digest(canonical_json(without_hash))
    _write_document(path, manifest)
    return manifest


def _assert_error(code: str, path: str | None, operation) -> None:
    with pytest.raises(RulesValidationError) as caught:
        operation()
    assert caught.value.code == code
    if path is not None:
        assert caught.value.path == path


def test_current_bundle_has_exact_canonical_inventory_and_hash_closure():
    """Catches an untracked dossier, noncanonical bytes, or an unbound digest."""
    expected_files = {"manifest.json"} | {
        f"dossiers/{source_id.removeprefix('pf2e.source.')}.json"
        for source_id in EXPECTED
    }
    observed_files = {
        path.relative_to(BUNDLE).as_posix()
        for path in BUNDLE.rglob("*")
        if path.is_file()
    }
    assert observed_files == expected_files

    manifest_bytes = _manifest_path(BUNDLE).read_bytes()
    manifest = read_json(manifest_bytes)
    assert manifest_bytes == canonical_json(manifest)
    assert manifest["schema_version"] == 1
    assert manifest["bundle_id"] == "pf2e-class-sources-2026-10-10.1"
    assert manifest["authority"] == "paizo"
    assert manifest["scope"] == "class-publication-identity"
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["bundle_hash"])
    without_hash = deepcopy(manifest)
    without_hash.pop("bundle_hash")
    assert manifest["bundle_hash"] == digest(canonical_json(without_hash))

    assert len(manifest["dossiers"]) == 8
    for reference in manifest["dossiers"]:
        dossier_path = BUNDLE / reference["path"]
        dossier_bytes = dossier_path.read_bytes()
        dossier = read_json(dossier_bytes)
        assert dossier_bytes == canonical_json(dossier)
        assert reference["sha256"] == digest(dossier_bytes)
        assert reference["evidence_id"] == dossier["evidence_id"]
        assert reference["source_id"] == dossier["source_id"]


def test_current_dossiers_pin_exact_metadata_without_prose_payloads():
    """Catches normalized titles presented as Paizo titles or prose-field creep."""
    report = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))
    assert set(report["dossiers_by_source"]) == set(EXPECTED)
    for source_id, expected in EXPECTED.items():
        dossier = report["dossiers_by_source"][source_id]
        assert set(dossier) == DOSSIER_FIELDS
        assert dossier["schema_version"] == 1
        assert dossier["source_id"] == source_id
        assert dossier["publisher"] == "Paizo Inc."
        for field in (
            "overlay_title",
            "official_product_title",
            "product_ids",
            "release_date",
            "class_rule_ids",
        ):
            assert dossier[field] == expected[field]
        assert dossier["official_urls"]["release_evidence"] == expected[
            "release_evidence"
        ]
        assert dossier["supported_review_scopes"] == ["source_review"]
        assert dossier["printing_observation"] == {
            "designation": None,
            "status": "unverified",
        }
        assert dossier["revision_observation"] == {
            "designation": None,
            "status": "unverified",
        }
        assert dossier["rights_observation"]["review_status"] == "pending"


def test_player_core_hash_is_pdf_metadata_only_and_rights_stay_pending():
    """Catches the supplied PDF hash being mislabeled as hardcover or retained."""
    report = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))
    player_core = report["dossiers_by_source"]["pf2e.source.player-core"]
    assert player_core["artifact_observation"] == {
        "notice_locator": {"pdf_page": 464, "printed_page": 463},
        "product_id": "PZO12001E",
        "retained": False,
        "sha256": "494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9",
        "status": "hash_observed",
    }
    assert player_core["rights_observation"] == {
        "blockers": [
            "downstream-attribution-review",
            "downstream-notice-review",
            "reserved-material-review",
            "trademark-review",
        ],
        "book_notice_status": "observed",
        "context": "orc-notice-observed-compliance-unreviewed",
        "review_status": "pending",
    }


def test_rage_of_elements_does_not_infer_a_license_family():
    """Catches a Remaster-compatible catalog label being treated as ORC proof."""
    report = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))
    rage = report["dossiers_by_source"]["pf2e.source.rage-of-elements"]
    assert rage["rights_observation"] == {
        "blockers": ["downstream-compliance-review", "final-book-legal-page"],
        "book_notice_status": "not_captured",
        "context": "transition_schedule_unverified",
        "review_status": "pending",
    }
    serialized = canonical_json(rage).decode("utf-8").casefold()
    assert '"context":"ogl"' not in serialized
    assert '"context":"orc"' not in serialized


def test_verifier_returns_detached_indexes_and_scope_specific_hashes():
    """Catches callers receiving mutable aliases or evidence from a wrong scope."""
    report = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))
    source_id = "pf2e.source.player-core"
    dossier = report["dossiers_by_source"][source_id]
    dossier_hash = digest(canonical_json(dossier))
    assert report["dossiers_by_hash"][dossier_hash] == dossier
    assert class_source_evidence_hashes(
        report, source_id, "source_review"
    ) == frozenset({dossier_hash})
    assert class_source_evidence_hashes(
        report, source_id, "license_review"
    ) == frozenset()
    assert class_source_evidence_hashes(
        report, "pf2e.source.unknown", "source_review"
    ) == frozenset()

    report["manifest"]["bundle_id"] = "changed"
    report["dossiers_by_source"][source_id]["overlay_title"] = "changed"
    fresh = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))
    assert fresh["manifest"]["bundle_id"] == "pf2e-class-sources-2026-10-10.1"
    assert fresh["dossiers_by_source"][source_id]["overlay_title"] == "Player Core"


def test_verifier_enforces_optional_trusted_bundle_hash():
    """Catches a self-consistent replacement bundle accepted as the trusted one."""
    trusted = verify_class_source_evidence_bundle(_manifest_path(BUNDLE))[
        "manifest"
    ]["bundle_hash"]
    verify_class_source_evidence_bundle(_manifest_path(BUNDLE), trusted)
    _assert_error(
        "binding_mismatch",
        "$.binding.bundle_hash",
        lambda: verify_class_source_evidence_bundle(
            _manifest_path(BUNDLE), "f" * 64
        ),
    )


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda root: (root / "extra.json").write_text("{}\n", encoding="utf-8"),
         "invalid_package_layout"),
        (lambda root: (root / "dossiers" / "extra.json").write_text(
            "{}\n", encoding="utf-8"), "invalid_package_layout"),
    ],
)
def test_verifier_rejects_files_outside_the_exact_layout(tmp_path, mutation, code):
    """Catches unmanifested evidence being smuggled beside canonical dossiers."""
    root = _copy_bundle(tmp_path)
    mutation(root)
    _assert_error(
        code,
        "$files",
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )


def test_verifier_rejects_noncanonical_and_tampered_dossier_bytes(tmp_path):
    """Catches semantic JSON rewrites and content changes after review."""
    root = _copy_bundle(tmp_path)
    relative = "dossiers/player-core.json"
    path = root / relative
    document = read_json(path.read_bytes())
    _write_document(path, document, canonical=False)
    _assert_error(
        "noncanonical_bytes",
        "$.files.dossiers/player-core.json",
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )

    root = _copy_bundle(tmp_path / "tampered")
    path = root / relative
    document = read_json(path.read_bytes())
    document["release_date"] = "2023-11-16"
    _write_document(path, document)
    _assert_error(
        "integrity_mismatch",
        "$.files.dossiers/player-core.json",
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )


@pytest.mark.parametrize(
    "bad_path",
    [
        "../player-core.json",
        "/dossiers/player-core.json",
        "dossiers\\player-core.json",
        "dossiers/../player-core.json",
        "dossiers/CON.json",
        "dossiers/player-core.json ",
    ],
)
def test_manifest_rejects_nonportable_or_escaping_dossier_paths(tmp_path, bad_path):
    """Catches path traversal or cross-platform aliasing in retained references."""
    root = _copy_bundle(tmp_path)
    manifest = read_json(_manifest_path(root).read_bytes())
    manifest["dossiers"][0]["path"] = bad_path
    without_hash = deepcopy(manifest)
    without_hash.pop("bundle_hash")
    manifest["bundle_hash"] = digest(canonical_json(without_hash))
    _write_document(_manifest_path(root), manifest)
    _assert_error(
        "invalid_package_layout",
        "$.manifest.dossiers[0].path",
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )


@pytest.mark.parametrize(
    ("field", "source_index", "expected_code"),
    [
        ("source_id", 0, "duplicate_id"),
        ("evidence_id", 0, "duplicate_id"),
        ("path", 0, "duplicate_id"),
        ("sha256", 0, "duplicate_id"),
    ],
)
def test_manifest_rejects_duplicate_reference_identity(tmp_path, field, source_index, expected_code):
    """Catches one dossier being reused to satisfy more than one source gate."""
    root = _copy_bundle(tmp_path)
    manifest = read_json(_manifest_path(root).read_bytes())
    manifest["dossiers"][1][field] = manifest["dossiers"][source_index][field]
    without_hash = deepcopy(manifest)
    without_hash.pop("bundle_hash")
    manifest["bundle_hash"] = digest(canonical_json(without_hash))
    _write_document(_manifest_path(root), manifest)
    _assert_error(
        expected_code,
        None,
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (lambda dossier: dossier.update(source_id="pf2e.source.player-core-2"),
         "source_mismatch", "$.dossiers[4].source_id"),
        (lambda dossier: dossier.update(supported_review_scopes=["license_review"]),
         "invalid_value", "$.dossiers[4].supported_review_scopes[0]"),
        (lambda dossier: dossier.update(overlay_title="Pathfinder Player Core"),
         "evidence_mismatch", "$.dossiers[4].overlay_title"),
        (lambda dossier: dossier.update(class_rule_ids=["pf2e.class.fighter"]),
         "evidence_mismatch", "$.dossiers[4].class_rule_ids"),
        (lambda dossier: dossier.update(description="copied product prose"),
         "unknown_field", "$.dossiers[4]"),
    ],
)
def test_self_consistent_dossier_cannot_change_source_scope_or_reviewed_mapping(
    tmp_path, mutation, code, path
):
    """Catches a rehashed dossier being applied to a different review assertion."""
    root = _copy_bundle(tmp_path)
    relative = "dossiers/player-core.json"
    dossier_path = root / relative
    dossier = read_json(dossier_path.read_bytes())
    mutation(dossier)
    _write_document(dossier_path, dossier)
    _rehash_manifest(root, relative)
    _assert_error(
        code,
        path,
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )


def test_verifier_rejects_linked_manifest_or_dossier(tmp_path):
    """Catches a mutable link target substituted for retained evidence bytes."""
    root = _copy_bundle(tmp_path)
    original = root / "dossiers" / "player-core.json"
    target = tmp_path / "target.json"
    shutil.copy2(original, target)
    original.unlink()
    try:
        os.symlink(target, original)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"links unavailable: {error}")
    _assert_error(
        "invalid_package_layout",
        "$files",
        lambda: verify_class_source_evidence_bundle(_manifest_path(root)),
    )
