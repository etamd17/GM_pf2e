"""Pinned integration contract for the current PF2e class-source review."""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from systems.pf2e.rules.ingestion.class_review_overlay import (
    compile_class_review_overlay,
    verify_class_review_overlay,
)
from systems.pf2e.rules.ingestion.class_source_evidence import (
    verify_class_source_evidence_bundle,
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
PARENT_ROOT = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "reviews"
    / "pf2e-class-identities-2026-10-07.1"
)
OVERLAY_ROOT = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "reviews"
    / "pf2e-class-source-review-2026-10-10.1"
)
EVIDENCE_MANIFEST = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "evidence"
    / "paizo"
    / "pf2e-class-sources-2026-10-10.1"
    / "manifest.json"
)
OVERLAY_FILES = {
    "authoring.json",
    "manifest.json",
    "records.json",
    "sources.json",
}
EXPECTED_PARENT_HASH = (
    "273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614"
)
EXPECTED_PARENT_FILE_HASHES = {
    "authoring.json": "559ae00442f4cf6bf8ddfb1f1ce0b1cf299901381bdcd76a66dd04f24f89b79f",
    "manifest.json": "8e38fb848dfe4e846e9152ca0ceb365c4e0013a321831fc058ae7eab5844592c",
    "records.json": "89cc38e82c3670c155ba82301f1776e96dbd79cf8f94347826ad10f7ba6a8c55",
    "sources.json": "c4b29c383b0c885eeedbe953b67aa7f1cd731ad26adf3ea6825ea0d4ced88639",
}
EXPECTED_EVIDENCE_BUNDLE_HASH = (
    "4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3"
)
EXPECTED_OVERLAY_HASH = (
    "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb"
)
EXPECTED_DOSSIER_HASHES = {
    "pf2e.source.battlecry": (
        "8bf8c631db470a6be9e7ac97a862889ae65126242225232db5ad24b7fbf5472e"
    ),
    "pf2e.source.dark-archive-remastered": (
        "8822d8d476b6995a9561857de07e6b76d1fd9b0dd7a901a53bdd4084f2e075d7"
    ),
    "pf2e.source.guns-and-gears-remastered": (
        "b1e6c819fa8922b4289128810bdb2e9358228951fe5ba4979273c6898d37ff6d"
    ),
    "pf2e.source.impossible-magic": (
        "bbc4c71c7ed58c17c899482329058d2ce4c96c7b42b154d233e97d645f0b514f"
    ),
    "pf2e.source.player-core": (
        "f18b63ad8ca08669f94d332d4f6414aa35ef26d42692a5e95ce9ff920559c443"
    ),
    "pf2e.source.player-core-2": (
        "0b569a54aeb989dbc6d7879819a37120b5d0363d52e27edf257c0ca9e3e14064"
    ),
    "pf2e.source.rage-of-elements": (
        "335474b94b818d79ead8116d6ec309c34e6a687b871133c0ade5b9c759923d5e"
    ),
    "pf2e.source.war-of-immortals": (
        "7b3efef93d2b801a69fca3b90660f78279452bf81f4afa820662cd1ef503ed94"
    ),
}
EXPECTED_ASSIGNMENTS = {
    "pf2e.class.alchemist": "pf2e.source.player-core-2",
    "pf2e.class.animist": "pf2e.source.war-of-immortals",
    "pf2e.class.barbarian": "pf2e.source.player-core-2",
    "pf2e.class.bard": "pf2e.source.player-core",
    "pf2e.class.champion": "pf2e.source.player-core-2",
    "pf2e.class.cleric": "pf2e.source.player-core",
    "pf2e.class.commander": "pf2e.source.battlecry",
    "pf2e.class.druid": "pf2e.source.player-core",
    "pf2e.class.exemplar": "pf2e.source.war-of-immortals",
    "pf2e.class.fighter": "pf2e.source.player-core",
    "pf2e.class.guardian": "pf2e.source.battlecry",
    "pf2e.class.gunslinger": "pf2e.source.guns-and-gears-remastered",
    "pf2e.class.inventor": "pf2e.source.guns-and-gears-remastered",
    "pf2e.class.investigator": "pf2e.source.player-core-2",
    "pf2e.class.kineticist": "pf2e.source.rage-of-elements",
    "pf2e.class.magus": "pf2e.source.impossible-magic",
    "pf2e.class.monk": "pf2e.source.player-core-2",
    "pf2e.class.necromancer": "pf2e.source.impossible-magic",
    "pf2e.class.oracle": "pf2e.source.player-core-2",
    "pf2e.class.psychic": "pf2e.source.dark-archive-remastered",
    "pf2e.class.ranger": "pf2e.source.player-core",
    "pf2e.class.rogue": "pf2e.source.player-core",
    "pf2e.class.runesmith": "pf2e.source.impossible-magic",
    "pf2e.class.sorcerer": "pf2e.source.player-core-2",
    "pf2e.class.summoner": "pf2e.source.impossible-magic",
    "pf2e.class.swashbuckler": "pf2e.source.player-core-2",
    "pf2e.class.thaumaturge": "pf2e.source.dark-archive-remastered",
    "pf2e.class.witch": "pf2e.source.player-core",
    "pf2e.class.wizard": "pf2e.source.player-core",
}
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
        "by_status": {"pending": 37, "approved": 8, "rejected": 0},
    },
    "enabled_mechanics": 0,
}


def _documents(root=OVERLAY_ROOT):
    return {
        name: read_json(read_file(root / name))
        for name in OVERLAY_FILES
    }


def _file_hash(path):
    return hashlib.sha256(read_file(path)).hexdigest()


def _offline_cli(arguments, *, environment):
    script = f"""
import sys

def refuse_network(event, _args):
    if event.startswith('socket.'):
        raise AssertionError('current class-source review must remain offline')

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


def test_current_class_source_review_has_exact_layout_bindings_and_counts():
    assert {
        path.relative_to(OVERLAY_ROOT).as_posix()
        for path in OVERLAY_ROOT.rglob("*")
        if path.is_file()
    } == OVERLAY_FILES
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
        parent_overlay_path=PARENT_ROOT,
        evidence_manifest_path=EVIDENCE_MANIFEST,
    )
    manifest = _documents()["manifest.json"]

    assert report == {
        "overlay_id": "pf2e-class-source-review-2026-10-10.1",
        "overlay_hash": EXPECTED_OVERLAY_HASH,
        "activation": "none",
        "counts": EXPECTED_COUNTS,
    }
    assert manifest["schema_version"] == 2
    assert manifest["compiler_version"] == "pf2e-class-review-2"
    assert manifest["snapshot_id"] == "pf2e-aon-2026-10-07-player-build-v1"
    assert manifest["snapshot_manifest_sha256"] == (
        "c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de"
    )
    assert manifest["parent_overlay_id"] == PARENT_ROOT.name
    assert manifest["parent_overlay_hash"] == EXPECTED_PARENT_HASH
    assert manifest["evidence_bundle_id"] == EVIDENCE_MANIFEST.parent.name
    assert manifest["evidence_bundle_hash"] == EXPECTED_EVIDENCE_BUNDLE_HASH
    assert manifest["activation"] == "none"
    assert manifest["counts"] == EXPECTED_COUNTS


def test_current_class_source_review_pins_parent_evidence_and_review_lifecycle():
    assert {
        name: _file_hash(PARENT_ROOT / name) for name in OVERLAY_FILES
    } == EXPECTED_PARENT_FILE_HASHES
    parent = _documents(PARENT_ROOT)
    child = _documents()
    assert parent["manifest.json"]["overlay_hash"] == EXPECTED_PARENT_HASH
    assert read_file(OVERLAY_ROOT / "records.json") == read_file(
        PARENT_ROOT / "records.json"
    )
    assert child["records.json"] == parent["records.json"]
    assert child["authoring.json"]["records"] == parent["authoring.json"]["records"]

    bundle = verify_class_source_evidence_bundle(
        EVIDENCE_MANIFEST,
        expected_hash=EXPECTED_EVIDENCE_BUNDLE_HASH,
    )
    assert bundle["manifest"]["bundle_id"] == EVIDENCE_MANIFEST.parent.name
    assert {
        reference["source_id"]: reference["sha256"]
        for reference in bundle["manifest"]["dossiers"]
    } == EXPECTED_DOSSIER_HASHES

    sources = child["sources.json"]
    assert sources == child["authoring.json"]["sources"]
    assert len(sources) == 8
    for source in sources:
        source_id = source["source_id"]
        assert source["source_review"] == {
            "status": "approved",
            "reviewer": "OpenAI Codex",
            "reviewed_at": "2026-10-10T13:39:32Z",
            "evidence_sha256": [EXPECTED_DOSSIER_HASHES[source_id]],
        }
        assert source["license_review"] == PENDING_REVIEW
    records = child["records.json"]
    assert len(records) == 29
    assert {
        record["rule_id"]: record["source_id"] for record in records
    } == EXPECTED_ASSIGNMENTS
    assert all(record["rules_review"] == PENDING_REVIEW for record in records)
    assert Counter(record["reconciliation"] for record in records) == {
        "aligned": 21,
        "source-drift": 6,
        "missing-local": 2,
    }


def test_current_class_source_review_recompiles_v1_and_v2_identically():
    parent_files = compile_class_review_overlay(
        read_json(read_file(PARENT_ROOT / "authoring.json")),
        SNAPSHOT_MANIFEST,
        CORPUS_ROOT,
    )
    child_files = compile_class_review_overlay(
        read_json(read_file(OVERLAY_ROOT / "authoring.json")),
        SNAPSHOT_MANIFEST,
        CORPUS_ROOT,
        parent_overlay_path=PARENT_ROOT,
        evidence_manifest_path=EVIDENCE_MANIFEST,
    )

    assert parent_files == {
        name: read_file(PARENT_ROOT / name) for name in OVERLAY_FILES
    }
    assert child_files == {
        name: read_file(OVERLAY_ROOT / name) for name in OVERLAY_FILES
    }


def test_current_class_source_review_recompiles_offline_without_app_or_data_dir(
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
            "--parent",
            PARENT_ROOT,
            "--evidence",
            EVIDENCE_MANIFEST,
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
    assert list(data_dir.iterdir()) == []

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
            "--parent",
            PARENT_ROOT,
            "--evidence",
            EVIDENCE_MANIFEST,
        ],
        environment=environment,
    )

    assert verified.returncode == 0, verified.stderr
    assert verified.stderr == ""
    assert json.loads(verified.stdout) == report
    assert list(data_dir.iterdir()) == []


def test_runtime_application_does_not_import_or_read_class_review_artifacts(
    tmp_path,
):
    data_dir = tmp_path / "runtime-data"
    data_dir.mkdir()
    script = f"""
import os
from pathlib import Path
import sys

protected_roots = tuple(Path(value).resolve() for value in {[
    str(PARENT_ROOT), str(OVERLAY_ROOT), str(EVIDENCE_MANIFEST.parent)
]!r})

def protect_review_artifacts(event, args):
    if event in {{'open', 'os.listdir', 'os.scandir'}} and args and isinstance(
        args[0], (str, bytes, os.PathLike)
    ):
        candidate = Path(os.fsdecode(os.fspath(args[0]))).resolve()
        if any(candidate == root or root in candidate.parents for root in protected_roots):
            raise AssertionError('runtime must not read review or evidence artifacts')

sys.addaudithook(protect_review_artifacts)
import app
assert not any(
    name == 'systems.pf2e.rules' or name.startswith('systems.pf2e.rules.')
    for name in sys.modules
)
with app.app.test_client() as client:
    response = client.get('/live')
    assert response.status_code == 200
    assert response.get_json() == {{'status': 'alive'}}
assert not any(
    name == 'systems.pf2e.rules' or name.startswith('systems.pf2e.rules.')
    for name in sys.modules
)
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
