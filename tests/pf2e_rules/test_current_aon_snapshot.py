"""Pinned integration contract for the 2026-10-07 AoN identity snapshot."""
from collections import Counter
from pathlib import Path

from systems.pf2e.rules.ingestion.aon_capture import PENDING_REASON
from systems.pf2e.rules.ingestion.evidence_inventory import (
    evidence_fingerprint,
    evidence_metadata_text,
)
from systems.pf2e.rules.ingestion.evidence_snapshot import (
    build_snapshot_manifest,
    verify_evidence_snapshot,
)
from systems.pf2e.rules.manifest import (
    MAX_FILE_BYTES,
    canonical_json,
    digest,
    read_file,
    read_json,
)


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_ROOT = (
    ROOT / "systems" / "pf2e" / "rules" / "evidence"
    / "archives-of-nethys" / "2026-10-07-player-build-v1"
)
MANIFEST_PATH = SNAPSHOT_ROOT / "snapshot-manifest.json"
SNAPSHOT_ID = "pf2e-aon-2026-10-07-player-build-v1"
CLASS_ROSTER = [
    "Alchemist", "Animist", "Barbarian", "Bard", "Champion", "Cleric",
    "Commander", "Druid", "Exemplar", "Fighter", "Guardian", "Gunslinger",
    "Inventor", "Investigator", "Kineticist", "Magus", "Monk",
    "Necromancer", "Oracle", "Psychic", "Ranger", "Rogue", "Runesmith",
    "Sorcerer", "Summoner", "Swashbuckler", "Thaumaturge", "Witch",
    "Wizard",
]
RECORD_FIELDS = {
    "canonical_url", "evidence_sha256", "fingerprint", "identity", "kind",
    "name", "rules_era", "source_refs",
}


def test_current_snapshot_verifies_exact_scope_roster_and_aggregates():
    summary = verify_evidence_snapshot(MANIFEST_PATH)
    manifest_bytes = read_file(MANIFEST_PATH)
    manifest = read_json(manifest_bytes)

    assert digest(manifest_bytes) == (
        "c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de"
    )
    assert summary["complete"] is True
    assert summary["census_schema_version"] == 2
    assert summary["records"] == {
        "deferred": 6_278,
        "excluded": 5_589,
        "included": 18_522,
        "total": 30_389,
    }
    assert summary["categories"] == {
        "deferred": 23, "excluded": 10, "included": 66,
    }
    assert summary["class_count"] == 29
    assert summary["shard_count"] == 66
    assert summary["dispositions"] == {
        "excluded": 0, "mapped": 0, "pending": 18_522,
    }
    assert summary["reviews"] == {"pending": 18_522, "reviewed": 0}
    assert summary["kinds"] == {
        "action": 555, "affliction": 100, "ancestry": 68,
        "archetype": 251, "armor": 38, "background": 538, "class": 29,
        "class-feature": 428, "condition": 56, "deity": 488,
        "equipment": 3_531, "feat": 6_376, "heritage": 314, "item": 163,
        "ritual": 163, "rule": 3_134, "shield": 16, "skill": 33,
        "spell": 1_834, "vehicle": 99, "weapon": 308,
    }
    assert digest(canonical_json(summary)) == (
        "49c9830a3cf38b6fbe1fd98cdb08240c18b21d40feebcecc7e8ab8ff925101a9"
    )
    assert manifest["class_roster"] == CLASS_ROSTER
    assert [manifest[name]["run_id"] for name in (
        "scope_capture", "census_capture", "ledger_enumeration"
    )] == [
        "aon-scope-20261007t164129z",
        "aon-census-20261007t164152z",
        "aon-ledger-20261007t164255z",
    ]


def test_current_snapshot_is_canonical_bounded_and_reproducible():
    all_paths = sorted(
        path for path in SNAPSHOT_ROOT.rglob("*") if path.is_file()
    )
    all_relative_paths = {
        path.relative_to(SNAPSHOT_ROOT).as_posix() for path in all_paths
    }
    manifest = read_json(read_file(MANIFEST_PATH))
    referenced = {
        "snapshot-manifest.json",
        manifest["scope_policy"]["path"],
        manifest["scope_capture"]["receipt"]["path"],
        manifest["census_capture"]["receipt"]["path"],
        manifest["ledger_enumeration"]["receipt"]["path"],
    }
    for shard in manifest["shards"]:
        referenced.add(shard["census"]["path"])
        referenced.add(shard["ledger"]["path"])

    assert len(all_paths) == 138
    assert all_relative_paths == referenced | {"README.md"}
    paths = sorted(SNAPSHOT_ROOT / relative_path for relative_path in referenced)
    sizes = {path.relative_to(SNAPSHOT_ROOT).as_posix(): path.stat().st_size
             for path in paths}
    assert len(paths) == 137
    assert set(sizes) == referenced
    assert sum(sizes.values()) == 12_447_582
    assert max(sizes.items(), key=lambda item: item[1]) == (
        "census/feat.json", 2_773_704,
    )
    for path in paths:
        data = read_file(path)
        assert len(data) <= MAX_FILE_BYTES
        assert data.endswith(b"\n")
        assert canonical_json(read_json(data)) == data

    rebuilt = build_snapshot_manifest(SNAPSHOT_ROOT, snapshot_id=SNAPSHOT_ID)
    assert canonical_json(rebuilt) == read_file(MANIFEST_PATH)


def test_current_snapshot_is_metadata_only_and_entirely_pending():
    manifest = read_json(read_file(MANIFEST_PATH))
    identities = set()
    rules_eras = Counter()
    total = 0

    for shard in manifest["shards"]:
        census = read_json(read_file(SNAPSHOT_ROOT / shard["census"]["path"]))
        ledger = read_json(read_file(SNAPSHOT_ROOT / shard["ledger"]["path"]))
        census_identities = [
            (record["identity"]["page_family"], record["identity"]["numeric_id"])
            for record in census["records"]
        ]
        ledger_identities = [
            (entry["identity"]["page_family"], entry["identity"]["numeric_id"])
            for entry in ledger["entries"]
        ]

        assert census["schema_version"] == 2
        assert ledger["schema_version"] == 1
        assert census_identities == ledger_identities
        assert len(census_identities) == shard["expected_records"]
        for record in census["records"]:
            assert set(record) == RECORD_FIELDS
            retained_metadata = [
                record["name"],
                *(value for source_ref in record["source_refs"]
                  for value in (source_ref["title"], source_ref["locator"])),
            ]
            assert all(evidence_metadata_text(value, "$") == value
                       for value in retained_metadata)
            assert all(set(source_ref) == {"title", "locator"}
                       for source_ref in record["source_refs"])
            assert record["fingerprint"] == evidence_fingerprint(
                record, schema_version=2
            )
            identity = (
                record["identity"]["page_family"],
                record["identity"]["numeric_id"],
            )
            assert identity not in identities
            identities.add(identity)
            rules_eras[record["rules_era"]] += 1
        for entry in ledger["entries"]:
            assert entry["disposition"] == "pending"
            assert entry["rule_id"] is None
            assert entry["reason"] == PENDING_REASON
            assert entry["review"] == {
                "status": "pending", "reviewer": None, "reviewed_at": None,
            }
        total += len(census["records"])

    assert total == len(identities) == 18_522
    assert rules_eras == {"remaster": 8_169, "unverified": 10_353}
