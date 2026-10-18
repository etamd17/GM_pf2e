"""Pinned production capture, immutable chain, and offline readiness boundaries."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from systems.pf2e.rules.ingestion.class_legal_readiness import (
    class_legal_readiness_report, verify_class_legal_readiness_bundle,
)
from systems.pf2e.rules.ingestion.class_review_overlay import verify_class_review_overlay
from systems.pf2e.rules.manifest import canonical_json, read_json

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "systems/pf2e/rules"
BUNDLE_ID = "pf2e-class-legal-readiness-2026-10-11.1"
BUNDLE = RULES / "evidence/paizo" / BUNDLE_ID
MANIFEST = BUNDLE / "manifest.json"
OVERLAY = RULES / "reviews/pf2e-class-source-review-2026-10-10.1"
PARENT = RULES / "reviews/pf2e-class-identities-2026-10-07.1"
SOURCE = RULES / "evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json"
SNAPSHOT = RULES / "evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json"
CORPUS = ROOT / "compendium_data"
EXPECTED_BUNDLE_HASH = "4eeaef808fae0aa8b7ccc1356fcb7381b4d672a8d2281427f01a7ae9cc7cb410"
EXPECTED_DOSSIER_HASHES = {
    "pf2e.source.battlecry": "0aa52077fd2f021ad2e0a50295837a4b6e5dad9cb938e40c4e3b1c31b26d1c44",
    "pf2e.source.dark-archive-remastered": "bb210c575898bd5b698aa38fcf0545fdb1f0430193c353b59125f7466136919f",
    "pf2e.source.guns-and-gears-remastered": "7986ba22031e1fa12a3eebb74940c1d08daec27fd9457ffd27ee99c576404ea8",
    "pf2e.source.impossible-magic": "ba219b1e7bd860f5fe47b42a7121b51b148b6420589dc45207eed372b90baac4",
    "pf2e.source.player-core": "d3a504760533ddd27a10105c3da7d1944438facf9707979eb74691b3abc0ab1c",
    "pf2e.source.player-core-2": "0700b239b6c3f9c68c885d9786aefa55bbfe489c299bfc774351436bcfde543f",
    "pf2e.source.rage-of-elements": "ee899b285116997e3cbc9790eab4b6a9ef888ec6f2427f84ba366fc4afaaed17",
    "pf2e.source.war-of-immortals": "e305d551ae246882ca249ee7b92b689d1c1a5684458089f655cbea29834923ec",
}
EXPECTED_PRODUCTS = {
    "pf2e.source.battlecry": {
        "hardcover": "PZO12008-HC",
        "pdf": "PZO12008E"
    },
    "pf2e.source.dark-archive-remastered": {
        "hardcover": "PZO12012-HC",
        "pdf": "PZO12012E"
    },
    "pf2e.source.guns-and-gears-remastered": {
        "hardcover": "PZO12010-HC",
        "pdf": "PZO12010E"
    },
    "pf2e.source.impossible-magic": {
        "hardcover": "PZO12014-HC",
        "pdf": "PZO12014E"
    },
    "pf2e.source.player-core": {
        "hardcover": "PZO12001-HC",
        "pdf": "PZO12001E"
    },
    "pf2e.source.player-core-2": {
        "hardcover": "PZO12004-HC",
        "pdf": "PZO12004E"
    },
    "pf2e.source.rage-of-elements": {
        "hardcover": "PZO2113-HC",
        "pdf": "PZO2113E"
    },
    "pf2e.source.war-of-immortals": {
        "hardcover": "PZO12006-HC",
        "pdf": "PZO12006E"
    }
}
EXPECTED_SOURCE_DOSSIER_HASHES = {
    "pf2e.source.battlecry": "8bf8c631db470a6be9e7ac97a862889ae65126242225232db5ad24b7fbf5472e",
    "pf2e.source.dark-archive-remastered": "8822d8d476b6995a9561857de07e6b76d1fd9b0dd7a901a53bdd4084f2e075d7",
    "pf2e.source.guns-and-gears-remastered": "b1e6c819fa8922b4289128810bdb2e9358228951fe5ba4979273c6898d37ff6d",
    "pf2e.source.impossible-magic": "bbc4c71c7ed58c17c899482329058d2ce4c96c7b42b154d233e97d645f0b514f",
    "pf2e.source.player-core": "f18b63ad8ca08669f94d332d4f6414aa35ef26d42692a5e95ce9ff920559c443",
    "pf2e.source.player-core-2": "0b569a54aeb989dbc6d7879819a37120b5d0363d52e27edf257c0ca9e3e14064",
    "pf2e.source.rage-of-elements": "335474b94b818d79ead8116d6ec309c34e6a687b871133c0ade5b9c759923d5e",
    "pf2e.source.war-of-immortals": "7b3efef93d2b801a69fca3b90660f78279452bf81f4afa820662cd1ef503ed94"
}
EXPECTED_FROZEN_FILE_HASHES = {
    "systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/authoring.json": "559ae00442f4cf6bf8ddfb1f1ce0b1cf299901381bdcd76a66dd04f24f89b79f",
    "systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/manifest.json": "8e38fb848dfe4e846e9152ca0ceb365c4e0013a321831fc058ae7eab5844592c",
    "systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/records.json": "89cc38e82c3670c155ba82301f1776e96dbd79cf8f94347826ad10f7ba6a8c55",
    "systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/sources.json": "c4b29c383b0c885eeedbe953b67aa7f1cd731ad26adf3ea6825ea0d4ced88639",
    "systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/authoring.json": "568731ca9ce7ea08c74eb93ce42e4b52df35acc111ad2ca4f241fd7acac5f430",
    "systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/manifest.json": "4aafd2afe79e2ad52aae4398ce084a2b933bc799947b2ac2825b70e8e18ab17e",
    "systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/records.json": "89cc38e82c3670c155ba82301f1776e96dbd79cf8f94347826ad10f7ba6a8c55",
    "systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/sources.json": "7632de03e4825464bb9fe0374c2b5e6c6e24d42d35ea0daa166d0ce361eaecc5",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/battlecry.json": "8bf8c631db470a6be9e7ac97a862889ae65126242225232db5ad24b7fbf5472e",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/dark-archive-remastered.json": "8822d8d476b6995a9561857de07e6b76d1fd9b0dd7a901a53bdd4084f2e075d7",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/guns-and-gears-remastered.json": "b1e6c819fa8922b4289128810bdb2e9358228951fe5ba4979273c6898d37ff6d",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/impossible-magic.json": "bbc4c71c7ed58c17c899482329058d2ce4c96c7b42b154d233e97d645f0b514f",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/player-core-2.json": "0b569a54aeb989dbc6d7879819a37120b5d0363d52e27edf257c0ca9e3e14064",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/player-core.json": "f18b63ad8ca08669f94d332d4f6414aa35ef26d42692a5e95ce9ff920559c443",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/rage-of-elements.json": "335474b94b818d79ead8116d6ec309c34e6a687b871133c0ade5b9c759923d5e",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/dossiers/war-of-immortals.json": "7b3efef93d2b801a69fca3b90660f78279452bf81f4afa820662cd1ef503ed94",
    "systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json": "5638e0df2e66ba97b7f01755bd833bf931318703a8c79132862d1e09f9f56a7c"
}
EXPECTED_REVIEW_DIRECTORIES = ["pf2e-class-identities-2026-10-07.1","pf2e-class-source-review-2026-10-10.1"]
EXPECTED_COUNTS = {
    "sources": 8,
    "artifact_hashes_observed": 1,
    "final_artifacts_missing": 7,
    "final_legal_notices_observed": 1,
    "final_legal_notices_missing": 7,
    "printings_unverified": 8,
    "revisions_unverified": 8,
    "errata_inventories_complete_as_captured": 1,
    "errata_inventories_unverified": 7,
    "license_reviews_ready": 0,
    "license_reviews_blocked": 8,
    "license_reviews_approved": 0,
    "enabled_mechanics": 0
}
EXPECTED_OVERLAY_HASH = "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb"
EXPECTED_PARENT_HASH = "273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614"
EXPECTED_SOURCE_BUNDLE_HASH = "4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3"
# Independently reviewed exact decoded curl response; no network is used by tests.
EXPECTED_CAPTURE = {
    "input_url": "https://paizo.com/pathfinder/faq",
    "final_url": "https://paizo.com/pathfinder/faq",
    "http_status": 200,
    "content_type": "text/html; charset=utf-8",
    "captured_at": "2026-10-11T16:59:43Z",
    "response_sha256": "2937aaea1df9eb41a6aa91fbd0eca5595f788a8483ecf70dd1863b4504005427",
    "hash_definition": "decoded-response-entity-bytes-written-by-curl-compressed",
    "list_item_count": 144,
    "normalized_tuple_count": 137,
}
EXPECTED_PLAYER_CORE_ERRATA_LOCATORS = (
    ("Fall 2023", "1st Printing", "erratum", (131,)),
    ("Fall 2023", "1st Printing", "erratum", (141,)),
    ("Fall 2023", "1st Printing", "erratum", (155,)),
    ("Fall 2023", "1st Printing", "erratum", (215,)),
    ("Fall 2023", "1st Printing", "erratum", (235,)),
    ("Fall 2023", "1st Printing", "erratum", (281,)),
    ("Fall 2023", "1st Printing", "erratum", (304, 307, 309)),
    ("Fall 2023", "1st Printing", "erratum", (307,)),
    ("Fall 2023", "1st Printing", "erratum", (314,)),
    ("Fall 2023", "1st Printing", "erratum", (316, 317)),
    ("Fall 2023", "1st Printing", "erratum", (317, 318)),
    ("Fall 2023", "1st Printing", "erratum", (320,)),
    ("Fall 2023", "1st Printing", "erratum", (320, 321, 357, 358, 361)),
    ("Fall 2023", "1st Printing", "erratum", (321,)),
    ("Fall 2023", "1st Printing", "erratum", (329,)),
    ("Fall 2023", "1st Printing", "erratum", (331,)),
    ("Fall 2023", "1st Printing", "erratum", (332, 333)),
    ("Fall 2023", "1st Printing", "erratum", (358,)),
    ("Fall 2023", "1st Printing", "erratum", (363,)),
    ("Fall 2023", "1st Printing", "erratum", (382,)),
    ("Fall 2023", "1st Printing", "erratum", (403,)),
    ("Fall 2023", "1st Printing", "erratum", (411,)),
    ("Fall 2023", "1st Printing", "erratum", (422,)),
    ("Fall 2024", "1st Printing", "clarification", (166,)),
    ("Fall 2024", "1st Printing", "clarification", (195,)),
    ("Fall 2024", "1st Printing", "clarification", (282,)),
    ("Fall 2024", "1st Printing", "erratum", (36, 37, 38)),
    ("Fall 2024", "1st Printing", "erratum", (44,)),
    ("Fall 2024", "1st Printing", "erratum", (48,)),
    ("Fall 2024", "1st Printing", "erratum", (65,)),
    ("Fall 2024", "1st Printing", "erratum", (81,)),
    ("Fall 2024", "1st Printing", "erratum", (89,)),
    ("Fall 2024", "1st Printing", "erratum", (91,)),
    ("Fall 2024", "1st Printing", "erratum", (96,)),
    ("Fall 2024", "1st Printing", "erratum", (110,)),
    ("Fall 2024", "1st Printing", "erratum", (131,)),
    ("Fall 2024", "1st Printing", "erratum", (132,)),
    ("Fall 2024", "1st Printing", "erratum", (133,)),
    ("Fall 2024", "1st Printing", "erratum", (133, 134)),
    ("Fall 2024", "1st Printing", "erratum", (134,)),
    ("Fall 2024", "1st Printing", "erratum", (143,)),
    ("Fall 2024", "1st Printing", "erratum", (154,)),
    ("Fall 2024", "1st Printing", "erratum", (156,)),
    ("Fall 2024", "1st Printing", "erratum", (157,)),
    ("Fall 2024", "1st Printing", "erratum", (159,)),
    ("Fall 2024", "1st Printing", "erratum", (166,)),
    ("Fall 2024", "1st Printing", "erratum", (172,)),
    ("Fall 2024", "1st Printing", "erratum", (174,)),
    ("Fall 2024", "1st Printing", "erratum", (188,)),
    ("Fall 2024", "1st Printing", "erratum", (207,)),
    ("Fall 2024", "1st Printing", "erratum", (210,)),
    ("Fall 2024", "1st Printing", "erratum", (213,)),
    ("Fall 2024", "1st Printing", "erratum", (215,)),
    ("Fall 2024", "1st Printing", "erratum", (226,)),
    ("Fall 2024", "1st Printing", "erratum", (231,)),
    ("Fall 2024", "1st Printing", "erratum", (237,)),
    ("Fall 2024", "1st Printing", "erratum", (245,)),
    ("Fall 2024", "1st Printing", "erratum", (246,)),
    ("Fall 2024", "1st Printing", "erratum", (254,)),
    ("Fall 2024", "1st Printing", "erratum", (263,)),
    ("Fall 2024", "1st Printing", "erratum", (264,)),
    ("Fall 2024", "1st Printing", "erratum", (281,)),
    ("Fall 2024", "1st Printing", "erratum", (301,)),
    ("Fall 2024", "1st Printing", "erratum", (305,)),
    ("Fall 2024", "1st Printing", "erratum", (314,)),
    ("Fall 2024", "1st Printing", "erratum", (326,)),
    ("Fall 2024", "1st Printing", "erratum", (327,)),
    ("Fall 2024", "1st Printing", "erratum", (331,)),
    ("Fall 2024", "1st Printing", "erratum", (332, 333)),
    ("Fall 2024", "1st Printing", "erratum", (333,)),
    ("Fall 2024", "1st Printing", "erratum", (335,)),
    ("Fall 2024", "1st Printing", "erratum", (336,)),
    ("Fall 2024", "1st Printing", "erratum", (338,)),
    ("Fall 2024", "1st Printing", "erratum", (343,)),
    ("Fall 2024", "1st Printing", "erratum", (361,)),
    ("Fall 2024", "1st Printing", "erratum", (366,)),
    ("Fall 2024", "1st Printing", "erratum", (367,)),
    ("Fall 2024", "1st Printing", "erratum", (374,)),
    ("Fall 2024", "1st Printing", "erratum", (384,)),
    ("Fall 2024", "1st Printing", "erratum", (387,)),
    ("Fall 2024", "1st Printing", "erratum", (395,)),
    ("Fall 2024", "1st Printing", "erratum", (398,)),
    ("Fall 2024", "1st Printing", "erratum", (404,)),
    ("Fall 2024", "1st Printing", "erratum", (418,)),
    ("Fall 2024", "1st Printing", "erratum", (437,)),
    ("Fall 2024", "1st Printing", "erratum", (439,)),
    ("Fall 2024", "1st Printing", "erratum", (452,)),
    ("Fall 2024", "1st Printing", "erratum", (456,)),
    ("Fall 2024", "1st Printing", "erratum", (462,)),
    ("Spring 2025", "1st Printing", "erratum", (147,)),
    ("Spring 2025", "1st Printing", "erratum", (195,)),
    ("Spring 2025", "1st Printing", "erratum", (253,)),
    ("Spring 2025", "1st Printing", "erratum", (442, 443, 446)),
    ("Spring 2026", "1st Printing", "clarification", (181, 182)),
    ("Spring 2026", "1st Printing", "clarification", (267,)),
    ("Spring 2026", "1st Printing", "clarification", (268,)),
    ("Spring 2026", "1st Printing", "clarification", (283,)),
    ("Spring 2026", "1st Printing", "clarification", (339, 340)),
    ("Spring 2026", "1st Printing", "clarification", (401,)),
    ("Spring 2026", "1st Printing", "clarification", (424,)),
    ("Spring 2026", "1st Printing", "clarification", (442, 443, 444)),
    ("Spring 2026", "1st Printing", "erratum", (13,)),
    ("Spring 2026", "1st Printing", "erratum", (41,)),
    ("Spring 2026", "1st Printing", "erratum", (84,)),
    ("Spring 2026", "1st Printing", "erratum", (91,)),
    ("Spring 2026", "1st Printing", "erratum", (130,)),
    ("Spring 2026", "1st Printing", "erratum", (147,)),
    ("Spring 2026", "1st Printing", "erratum", (149,)),
    ("Spring 2026", "1st Printing", "erratum", (150,)),
    ("Spring 2026", "1st Printing", "erratum", (160,)),
    ("Spring 2026", "1st Printing", "erratum", (162,)),
    ("Spring 2026", "1st Printing", "erratum", (167,)),
    ("Spring 2026", "1st Printing", "erratum", (171,)),
    ("Spring 2026", "1st Printing", "erratum", (182,)),
    ("Spring 2026", "1st Printing", "erratum", (184,)),
    ("Spring 2026", "1st Printing", "erratum", (195,)),
    ("Spring 2026", "1st Printing", "erratum", (196,)),
    ("Spring 2026", "1st Printing", "erratum", (212,)),
    ("Spring 2026", "1st Printing", "erratum", (230,)),
    ("Spring 2026", "1st Printing", "erratum", (259,)),
    ("Spring 2026", "1st Printing", "erratum", (282,)),
    ("Spring 2026", "1st Printing", "erratum", (292,)),
    ("Spring 2026", "1st Printing", "erratum", (301,)),
    ("Spring 2026", "1st Printing", "erratum", (315,)),
    ("Spring 2026", "1st Printing", "erratum", (320, 321, 357, 358, 361)),
    ("Spring 2026", "1st Printing", "erratum", (324,)),
    ("Spring 2026", "1st Printing", "erratum", (325,)),
    ("Spring 2026", "1st Printing", "erratum", (329,)),
    ("Spring 2026", "1st Printing", "erratum", (354,)),
    ("Spring 2026", "1st Printing", "erratum", (365,)),
    ("Spring 2026", "1st Printing", "erratum", (371,)),
    ("Spring 2026", "1st Printing", "erratum", (378,)),
    ("Spring 2026", "1st Printing", "erratum", (385,)),
    ("Spring 2026", "1st Printing", "erratum", (408,)),
    ("Spring 2026", "1st Printing", "erratum", (409,)),
    ("Spring 2026", "1st Printing", "erratum", (418,)),
    ("Spring 2026", "1st Printing", "erratum", (446,)),
)
COMPLIANCE = [
    "attribution-review", "branding-review", "distribution-review",
    "notice-review", "reserved-material-review", "trademark-review",
]
PC_BLOCKERS = [
    "downstream-compliance-missing", "errata-applicability-unresolved",
    "named-reviewer-missing", "printing-unverified", "revision-unverified",
]
MISSING_BLOCKERS = [
    "downstream-compliance-missing", "errata-applicability-unresolved",
    "errata-inventory-unverified", "final-artifact-missing", "final-legal-notice-missing",
    "named-reviewer-missing", "printing-unverified", "revision-unverified",
]
PENDING = {"status": "pending", "reviewer": None, "reviewed_at": None, "evidence_sha256": []}


def verify():
    assert MANIFEST.is_file(), "required immutable readiness bundle missing"
    return verify_class_legal_readiness_bundle(
        MANIFEST, OVERLAY, SNAPSHOT, CORPUS, expected_hash=EXPECTED_BUNDLE_HASH,
        parent_overlay_path=PARENT, source_evidence_manifest_path=SOURCE,
    )


@pytest.fixture(scope="module")
def verified():
    return verify()


def expected_report():
    return {
        "bundle_id": BUNDLE_ID, "bundle_hash": EXPECTED_BUNDLE_HASH,
        "counts": deepcopy(EXPECTED_COUNTS),
        "sources": [{"source_id": source, "blockers": PC_BLOCKERS if source == "pf2e.source.player-core" else MISSING_BLOCKERS}
                    for source in sorted(EXPECTED_PRODUCTS)],
    }


def expected_dossier(source):
    slug = source.removeprefix("pf2e.source.")
    observed = slug == "player-core"
    return {
        "schema_version": 1, "evidence_id": "paizo.class-legal-readiness." + slug,
        "source_id": source, "source_evidence_id": "paizo.class-source." + slug,
        "source_dossier_sha256": EXPECTED_SOURCE_DOSSIER_HASHES[source],
        "product_ids": EXPECTED_PRODUCTS[source],
        "artifact_observation": {
            "status": "hash_observed" if observed else "missing",
            "product_id": "PZO12001E" if observed else None,
            "sha256": "494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9" if observed else None,
            "retained": False,
        },
        "printing_observation": {"status": "unverified", "designation": None},
        "revision_observation": {"status": "unverified", "designation": None},
        "legal_notice_observation": {
            "status": "observed" if observed else "missing", "family": "orc" if observed else None,
            "notice_locator": {"printed_page": 463, "pdf_page": 464} if observed else None,
        },
        "errata_observation": {
            "inventory_status": "complete_as_of_capture" if observed else "unverified",
            "applicability_status": "unresolved",
            "official_url": EXPECTED_CAPTURE["input_url"],
            "captured_at": EXPECTED_CAPTURE["captured_at"] if observed else None,
            "response_sha256": EXPECTED_CAPTURE["response_sha256"] if observed else None,
            "locators": [{"release_label": release, "printing_label": printing, "entry_type": kind, "pages": list(pages)}
                         for release, printing, kind, pages in EXPECTED_PLAYER_CORE_ERRATA_LOCATORS] if observed else [],
        },
        "downstream_compliance": {"status": "missing", "blockers": COMPLIANCE},
        "review_readiness": {"status": "blocked", "blockers": PC_BLOCKERS if observed else MISSING_BLOCKERS},
        "supported_review_scopes": ["license_readiness"],
    }


def test_current_readiness_has_exact_layout_canonical_bytes_and_trusted_bindings(verified):
    expected_files = {"manifest.json"} | {
        "dossiers/" + source.removeprefix("pf2e.source.") + ".json" for source in EXPECTED_PRODUCTS
    }
    assert {path.relative_to(BUNDLE).as_posix() for path in BUNDLE.rglob("*")} == expected_files | {"dossiers"}
    refs = []
    for source, expected_hash in EXPECTED_DOSSIER_HASHES.items():
        relative = "dossiers/" + source.removeprefix("pf2e.source.") + ".json"
        data = (BUNDLE / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected_hash
        refs.append({"source_id": source, "evidence_id": expected_dossier(source)["evidence_id"],
                     "path": relative, "sha256": expected_hash})
    expected_manifest = {
        "schema_version": 1, "bundle_id": BUNDLE_ID, "scope": "class-license-readiness",
        "authority": "paizo", "created_at": EXPECTED_CAPTURE["captured_at"],
        "class_review_overlay_id": OVERLAY.name, "class_review_overlay_hash": EXPECTED_OVERLAY_HASH,
        "source_bundle_id": SOURCE.parent.name, "source_bundle_hash": EXPECTED_SOURCE_BUNDLE_HASH,
        "counts": EXPECTED_COUNTS, "dossiers": sorted(refs, key=lambda row: row["source_id"]),
        "bundle_hash": EXPECTED_BUNDLE_HASH,
    }
    assert verified["manifest"] == expected_manifest
    unhashed = deepcopy(expected_manifest)
    unhashed.pop("bundle_hash")
    assert hashlib.sha256(canonical_json(unhashed)).hexdigest() == EXPECTED_BUNDLE_HASH
    for relative in expected_files:
        data = (BUNDLE / relative).read_bytes()
        assert data.endswith(b"\n") and b"\r" not in data
        assert canonical_json(read_json(data)) == data


def test_current_readiness_exact_observations_locators_and_closed_schema(verified):
    assert set(verified["dossiers_by_source"]) == set(EXPECTED_PRODUCTS)
    assert set(verified["dossiers_by_hash"]) == set(EXPECTED_DOSSIER_HASHES.values())
    for source, dossier in verified["dossiers_by_source"].items():
        # Exact nested equality excludes arbitrary prose, reviewer/approval/activation/mechanics fields.
        assert dossier == expected_dossier(source)
        assert verified["dossiers_by_hash"][EXPECTED_DOSSIER_HASHES[source]] == dossier
    assert len(EXPECTED_PLAYER_CORE_ERRATA_LOCATORS) == 137
    assert EXPECTED_PLAYER_CORE_ERRATA_LOCATORS == tuple(sorted(set(EXPECTED_PLAYER_CORE_ERRATA_LOCATORS)))
    assert {row[0] for row in EXPECTED_PLAYER_CORE_ERRATA_LOCATORS} == {
        "Fall 2023", "Fall 2024", "Spring 2025", "Spring 2026",
    }


def test_current_readiness_report_exact_counts_blockers_and_detachment(verified):
    before = deepcopy(verified)
    assert class_legal_readiness_report(verified) == expected_report()
    mutated = class_legal_readiness_report(verified)
    mutated["counts"].clear()
    mutated["sources"][0]["blockers"].clear()
    assert class_legal_readiness_report(verified) == expected_report()
    assert verified == before


def test_frozen_upstream_bytes_and_review_lifecycle_remain_unchanged():
    roots = [PARENT, OVERLAY, SOURCE.parent]
    assert {path.relative_to(ROOT).as_posix() for root in roots for path in root.rglob("*") if path.is_file()} == set(EXPECTED_FROZEN_FILE_HASHES)
    for relative, expected_hash in EXPECTED_FROZEN_FILE_HASHES.items():
        raw = (ROOT / relative).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected_hash
        assert canonical_json(read_json(raw)) == raw
    assert read_json((PARENT / "manifest.json").read_bytes())["overlay_hash"] == EXPECTED_PARENT_HASH
    assert read_json(SOURCE.read_bytes())["bundle_hash"] == EXPECTED_SOURCE_BUNDLE_HASH
    sources = read_json((OVERLAY / "sources.json").read_bytes())
    records = read_json((OVERLAY / "records.json").read_bytes())
    assert len(sources) == 8 and all(row["license_review"] == PENDING for row in sources)
    assert all(row["source_review"]["status"] == "approved" for row in sources)
    assert len(records) == 29 and all(row["rules_review"] == PENDING for row in records)
    report = verify_class_review_overlay(
        OVERLAY, SNAPSHOT, CORPUS, expected_hash=EXPECTED_OVERLAY_HASH,
        parent_overlay_path=PARENT, evidence_manifest_path=SOURCE,
    )
    assert report["activation"] == "none"
    assert report["counts"]["overlay_gates"]["by_status"] == {"approved": 8, "pending": 37, "rejected": 0}
    assert report["counts"]["effective_dispositions"]["pending"] == 18493
    assert report["counts"]["enabled_mechanics"] == 0
    assert sorted(path.name for path in (RULES / "reviews").iterdir()) == EXPECTED_REVIEW_DIRECTORIES


def canary_state(path):
    if not path.exists():
        return None
    return {leaf.relative_to(path).as_posix(): (leaf.read_bytes(), leaf.stat().st_mtime_ns)
            for leaf in path.rglob("*") if leaf.is_file()}


@pytest.mark.parametrize("present", [False, True])
def test_verifier_report_in_process_deny_all_data_dir_access(tmp_path, monkeypatch, present):
    data_dir = tmp_path / "protected-data"
    if present:
        data_dir.mkdir()
        (data_dir / "canary.bin").write_bytes(b"readiness canary\x00unchanged")
    before = canary_state(data_dir)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    active = [True]
    def guard(event, args):
        if not active[0]:
            return
        if event.startswith("socket."):
            raise AssertionError("verification must remain offline")
        if event == "open" or event.startswith("os."):
            for arg in args:
                if isinstance(arg, (str, bytes, os.PathLike)):
                    candidate = Path(os.fsdecode(os.fspath(arg))).absolute()
                    if candidate == data_dir or data_dir in candidate.parents:
                        raise AssertionError("verification touched DATA_DIR")
    sys.addaudithook(guard)
    try:
        assert class_legal_readiness_report(verify()) == expected_report()
    finally:
        active[0] = False
    assert canary_state(data_dir) == before


@pytest.mark.parametrize("present", [False, True])
def test_verifier_report_subprocess_canaries_and_hash_seed_determinism(tmp_path, present):
    data_dir = tmp_path / "subprocess-data"
    if present:
        data_dir.mkdir()
        (data_dir / "canary.bin").write_bytes(b"unchanged subprocess canary")
    before = canary_state(data_dir)
    script = f"""
import os
from pathlib import Path
import sys
import json
data_dir = Path(os.environ["DATA_DIR"]).absolute()
def guard(event, args):
    if event.startswith("socket."):
        raise AssertionError("verification must remain offline")
    if event == "open" or event.startswith("os."):
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(os.fspath(arg))).absolute()
                if candidate == data_dir or data_dir in candidate.parents:
                    raise AssertionError("verification touched DATA_DIR")
sys.addaudithook(guard)
from systems.pf2e.rules.ingestion.class_legal_readiness import verify_class_legal_readiness_bundle, class_legal_readiness_report
bundle = verify_class_legal_readiness_bundle(
    Path({str(MANIFEST)!r}), Path({str(OVERLAY)!r}), Path({str(SNAPSHOT)!r}), Path({str(CORPUS)!r}),
    expected_hash={EXPECTED_BUNDLE_HASH!r}, parent_overlay_path=Path({str(PARENT)!r}),
    source_evidence_manifest_path=Path({str(SOURCE)!r}))
assert "app" not in sys.modules and "flask" not in sys.modules
print(json.dumps(class_legal_readiness_report(bundle), sort_keys=True))
"""
    outputs = []
    for seed in ("1", "81"):
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONHASHSEED": seed},
                                capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert json.loads(result.stdout) == expected_report()
        outputs.append(result.stdout)
        assert canary_state(data_dir) == before
    assert outputs[0] == outputs[1]


def test_runtime_import_and_live_do_not_import_or_read_readiness(tmp_path):
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir()
    script = f"""
import os
from pathlib import Path
import sys
protected = Path({str(BUNDLE)!r}).absolute()
def guard(event, args):
    if event == "import" and args and args[0] == "systems.pf2e.rules.ingestion.class_legal_readiness":
        raise AssertionError("runtime imported readiness")
    if event == "open" or event.startswith("os."):
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(os.fspath(arg))).absolute()
                if candidate == protected or protected in candidate.parents:
                    raise AssertionError("runtime touched readiness bundle")
sys.addaudithook(guard)
import app
assert "systems.pf2e.rules.ingestion.class_legal_readiness" not in sys.modules
with app.app.test_client() as client:
    response = client.get("/live")
    assert response.status_code == 200
    assert response.get_json() == {{"status": "alive"}}
assert "systems.pf2e.rules.ingestion.class_legal_readiness" not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env={**os.environ, "DATA_DIR": str(data_dir), "APP_ENV": "development",
                                 "SECRET_KEY": "task2-readiness-runtime-isolation-secret"},
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
