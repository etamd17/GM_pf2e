"""Pinned integration contract for the current PF2e class-review overlay."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from systems.pf2e.rules.ingestion.class_review_overlay import (
    verify_class_review_overlay,
)
from systems.pf2e.rules.manifest import canonical_json, read_file, read_json


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_MANIFEST = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "evidence"
    / "archives-of-nethys"
    / "2026-10-07-player-build-v1"
    / "snapshot-manifest.json"
)
CORPUS_ROOT = ROOT / "compendium_data"
OVERLAY_ROOT = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "reviews"
    / "pf2e-class-identities-2026-10-07.1"
)
OVERLAY_FILES = {
    "authoring.json",
    "manifest.json",
    "records.json",
    "sources.json",
}
EXPECTED_OVERLAY_HASH = (
    "273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614"
)
PENDING_REVIEW = {
    "status": "pending",
    "reviewer": None,
    "reviewed_at": None,
    "evidence_sha256": [],
}
EXPECTED_COUNTS = {
    "sources": 8,
    "records": 29,
    "local_records": 27,
    "reconciliation": {
        "aligned": 21,
        "source-drift": 6,
        "missing-local": 2,
    },
    "effective_dispositions": {
        "excluded": 0,
        "mapped": 29,
        "pending": 18_493,
    },
    "base_reviews": {"pending": 18_522, "reviewed": 0},
    "overlay_gates": {
        "sources": 8,
        "licenses": 8,
        "rules": 29,
        "total": 45,
        "by_status": {"pending": 45, "approved": 0, "rejected": 0},
    },
    "enabled_mechanics": 0,
}
EXPECTED_SOURCES = {
    "pf2e.source.battlecry": ("Battlecry!", 2),
    "pf2e.source.dark-archive-remastered": (
        "Dark Archive (Remastered)",
        2,
    ),
    "pf2e.source.guns-and-gears-remastered": (
        "Guns & Gears (Remastered)",
        2,
    ),
    "pf2e.source.impossible-magic": ("Impossible Magic", 4),
    "pf2e.source.player-core": ("Player Core", 8),
    "pf2e.source.player-core-2": ("Player Core 2", 8),
    "pf2e.source.rage-of-elements": ("Rage of Elements", 1),
    "pf2e.source.war-of-immortals": ("War of Immortals", 2),
}
EXPECTED_CLASSES = {
    19: (
        "Inventor",
        "pf2e.class.inventor",
        "pf2e.source.guns-and-gears-remastered",
    ),
    20: (
        "Gunslinger",
        "pf2e.class.gunslinger",
        "pf2e.source.guns-and-gears-remastered",
    ),
    23: ("Kineticist", "pf2e.class.kineticist", "pf2e.source.rage-of-elements"),
    32: ("Bard", "pf2e.class.bard", "pf2e.source.player-core"),
    33: ("Cleric", "pf2e.class.cleric", "pf2e.source.player-core"),
    34: ("Druid", "pf2e.class.druid", "pf2e.source.player-core"),
    35: ("Fighter", "pf2e.class.fighter", "pf2e.source.player-core"),
    36: ("Ranger", "pf2e.class.ranger", "pf2e.source.player-core"),
    37: ("Rogue", "pf2e.class.rogue", "pf2e.source.player-core"),
    38: ("Witch", "pf2e.class.witch", "pf2e.source.player-core"),
    39: ("Wizard", "pf2e.class.wizard", "pf2e.source.player-core"),
    56: ("Alchemist", "pf2e.class.alchemist", "pf2e.source.player-core-2"),
    57: ("Barbarian", "pf2e.class.barbarian", "pf2e.source.player-core-2"),
    58: ("Champion", "pf2e.class.champion", "pf2e.source.player-core-2"),
    59: ("Investigator", "pf2e.class.investigator", "pf2e.source.player-core-2"),
    60: ("Monk", "pf2e.class.monk", "pf2e.source.player-core-2"),
    61: ("Oracle", "pf2e.class.oracle", "pf2e.source.player-core-2"),
    62: ("Sorcerer", "pf2e.class.sorcerer", "pf2e.source.player-core-2"),
    63: ("Swashbuckler", "pf2e.class.swashbuckler", "pf2e.source.player-core-2"),
    64: ("Animist", "pf2e.class.animist", "pf2e.source.war-of-immortals"),
    65: ("Exemplar", "pf2e.class.exemplar", "pf2e.source.war-of-immortals"),
    66: ("Commander", "pf2e.class.commander", "pf2e.source.battlecry"),
    67: ("Guardian", "pf2e.class.guardian", "pf2e.source.battlecry"),
    68: (
        "Psychic",
        "pf2e.class.psychic",
        "pf2e.source.dark-archive-remastered",
    ),
    69: (
        "Thaumaturge",
        "pf2e.class.thaumaturge",
        "pf2e.source.dark-archive-remastered",
    ),
    74: ("Magus", "pf2e.class.magus", "pf2e.source.impossible-magic"),
    75: ("Necromancer", "pf2e.class.necromancer", "pf2e.source.impossible-magic"),
    76: ("Runesmith", "pf2e.class.runesmith", "pf2e.source.impossible-magic"),
    77: ("Summoner", "pf2e.class.summoner", "pf2e.source.impossible-magic"),
}
EXPECTED_RUNTIME_CLASSES = {
    "alchemist", "animist", "barbarian", "bard", "champion", "cleric",
    "commander", "druid", "exemplar", "fighter", "guardian", "gunslinger",
    "inventor", "investigator", "kineticist", "magus", "monk", "oracle",
    "psychic", "ranger", "rogue", "sorcerer", "summoner", "swashbuckler",
    "thaumaturge", "witch", "wizard",
}
COMPILED_RECORD_FIELDS = {
    "identity", "name", "canonical_url", "fingerprint", "evidence_sha256",
    "disposition", "rule_id", "source_id", "source_ref", "local",
    "reconciliation", "rules_review",
}


def _documents():
    return {
        name: read_json(read_file(OVERLAY_ROOT / name))
        for name in OVERLAY_FILES
    }


def _offline_cli(arguments, *, environment):
    script = f"""
import sys

def refuse_network(event, _args):
    if event.startswith('socket.'):
        raise AssertionError('current class-review verification must remain offline')

sys.addaudithook(refuse_network)
from tools.pf2e_rules import main
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
code = main({[str(argument) for argument in arguments]!r})
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
raise SystemExit(code)
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_current_class_review_has_exact_layout_binding_and_counts():
    relative_paths = {
        path.relative_to(OVERLAY_ROOT).as_posix()
        for path in OVERLAY_ROOT.rglob("*")
        if path.is_file()
    }

    assert relative_paths == OVERLAY_FILES
    for name in OVERLAY_FILES:
        data = read_file(OVERLAY_ROOT / name)
        assert data.endswith(b"\n")
        assert b"\r" not in data
        assert canonical_json(read_json(data)) == data

    report = verify_class_review_overlay(
        OVERLAY_ROOT,
        SNAPSHOT_MANIFEST,
        CORPUS_ROOT,
        expected_hash=EXPECTED_OVERLAY_HASH,
    )
    documents = _documents()
    manifest = documents["manifest.json"]

    assert report == {
        "overlay_id": "pf2e-class-identities-2026-10-07.1",
        "overlay_hash": EXPECTED_OVERLAY_HASH,
        "activation": "none",
        "counts": EXPECTED_COUNTS,
    }
    assert manifest["snapshot_id"] == "pf2e-aon-2026-10-07-player-build-v1"
    assert manifest["snapshot_manifest_sha256"] == (
        "c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de"
    )
    assert manifest["parent_overlay_id"] is None
    assert manifest["parent_overlay_hash"] is None
    assert manifest["activation"] == "none"
    assert manifest["counts"] == EXPECTED_COUNTS


def test_current_class_review_pins_all_source_and_class_identity_decisions():
    documents = _documents()
    authoring = documents["authoring.json"]
    sources = documents["sources.json"]
    records = documents["records.json"]
    source_counts = Counter(record["source_id"] for record in records)

    assert sources == authoring["sources"]
    assert {
        source["source_id"]: (source["title"], source_counts[source["source_id"]])
        for source in sources
    } == EXPECTED_SOURCES
    assert all(source["source_review"] == PENDING_REVIEW for source in sources)
    assert all(source["license_review"] == PENDING_REVIEW for source in sources)
    assert all(record["rules_review"] == PENDING_REVIEW for record in records)
    assert all(record["disposition"] == "mapped" for record in records)
    assert all(set(record) == COMPILED_RECORD_FIELDS for record in records)
    assert {
        record["identity"]["numeric_id"]: (
            record["name"],
            record["rule_id"],
            record["source_id"],
        )
        for record in records
    } == EXPECTED_CLASSES
    assert all(
        record["identity"]["page_family"] == "Classes.aspx"
        for record in records
    )
    fighter = next(record for record in records if record["name"] == "Fighter")
    assert (fighter["rule_id"], fighter["source_id"]) == (
        "pf2e.class.fighter",
        "pf2e.source.player-core",
    )


def test_current_class_review_reconciles_exact_local_files_without_enabling_missing_classes():
    records = _documents()["records.json"]
    reconciliations = Counter(record["reconciliation"] for record in records)
    present = {
        record["local"]["local_key"]: record
        for record in records
        if record["local"]["status"] == "present"
    }
    local_files = sorted((CORPUS_ROOT / "classes").glob("*.json"))

    assert reconciliations == {
        "aligned": 21,
        "source-drift": 6,
        "missing-local": 2,
    }
    assert {
        record["name"]
        for record in records
        if record["reconciliation"] == "source-drift"
    } == {
        "Gunslinger",
        "Inventor",
        "Magus",
        "Psychic",
        "Summoner",
        "Thaumaturge",
    }
    assert {
        record["name"]
        for record in records
        if record["reconciliation"] == "missing-local"
    } == {"Necromancer", "Runesmith"}
    assert len(local_files) == len(present) == 27
    for path in local_files:
        data = path.read_bytes()
        normalized = data.replace(b"\r\n", b"\n")
        assert b"\r" not in normalized
        document = json.loads(data)
        record = present[path.stem]
        assert record["name"] == document["name"]
        assert record["local"] == {
            "status": "present",
            "relative_path": f"classes/{path.name}",
            "foundry_id": document["_id"],
            "content_sha256": hashlib.sha256(normalized).hexdigest(),
            "local_key": path.stem,
        }
    for record in records:
        if record["local"]["status"] == "missing":
            assert record["local"] == {
                "status": "missing",
                "relative_path": None,
                "foundry_id": None,
                "content_sha256": None,
                "local_key": None,
            }

    from class_matrix import CLASS_MATRIX, CLASS_PROGRESSION

    assert set(CLASS_MATRIX) == EXPECTED_RUNTIME_CLASSES
    assert set(CLASS_PROGRESSION) == EXPECTED_RUNTIME_CLASSES
    assert "necromancer" not in CLASS_MATRIX
    assert "necromancer" not in CLASS_PROGRESSION
    assert "runesmith" not in CLASS_MATRIX
    assert "runesmith" not in CLASS_PROGRESSION


def test_current_class_review_recompiles_identically_offline_without_app_or_data_dir(
    tmp_path,
):
    store = tmp_path / "reviews"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = {**os.environ, "DATA_DIR": str(data_dir)}

    compiled = _offline_cli(
        [
            "class-review-compile",
            OVERLAY_ROOT / "authoring.json",
            "--snapshot",
            SNAPSHOT_MANIFEST,
            "--corpus",
            CORPUS_ROOT,
            "--store",
            store,
        ],
        environment=environment,
    )

    assert compiled.returncode == 0, compiled.stderr
    assert compiled.stderr == ""
    report = json.loads(compiled.stdout)
    assert report["overlay_hash"] == EXPECTED_OVERLAY_HASH
    generated = Path(report["path"])
    assert {
        name: read_file(generated / name) for name in OVERLAY_FILES
    } == {
        name: read_file(OVERLAY_ROOT / name) for name in OVERLAY_FILES
    }

    verified = _offline_cli(
        [
            "class-review-verify",
            generated,
            "--snapshot",
            SNAPSHOT_MANIFEST,
            "--corpus",
            CORPUS_ROOT,
            "--expected-hash",
            EXPECTED_OVERLAY_HASH,
        ],
        environment=environment,
    )

    assert verified.returncode == 0, verified.stderr
    assert verified.stderr == ""
    assert json.loads(verified.stdout) == report
    assert list(data_dir.iterdir()) == []


def test_runtime_application_does_not_import_or_read_class_review_overlay(
    tmp_path,
):
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir()
    script = f"""
import os
from pathlib import Path
import sys

overlay = Path({str(OVERLAY_ROOT)!r}).resolve()

def protect_review_artifact(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        candidate = Path(args[0]).resolve()
        if candidate == overlay or overlay in candidate.parents:
            raise AssertionError('runtime must not read the class-review artifact')

sys.addaudithook(protect_review_artifact)
import app
assert 'systems.pf2e.rules.ingestion.class_review_overlay' not in sys.modules
"""
    environment = {
        **os.environ,
        "APP_ENV": "development",
        "DATA_DIR": str(data_dir),
        "SECRET_KEY": "task7-runtime-boundary-secret-key-2026",
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
