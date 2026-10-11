"""The operator interface must fail cleanly and leave existing packages intact."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "pf2e_rules" / "fixtures" / "synthetic.json"
CENSUS = ROOT / "tests" / "pf2e_rules" / "fixtures" / "synthetic-aon-census.json"
LEDGER = ROOT / "tests" / "pf2e_rules" / "fixtures" / "synthetic-evidence-ledger.json"


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "pf2e_rules.py"), *map(str, args)],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")


def _snapshot_tree(tmp_path):
    from tests.pf2e_rules.test_evidence_snapshot import _snapshot_tree as build_tree

    return build_tree(tmp_path)


def _offline_snapshot_cli(manifest, *, env=None):
    script = f"""
import sys

def refuse_network(event, _args):
    if event.startswith('socket.'):
        raise AssertionError('snapshot verification must remain offline')

sys.addaudithook(refuse_network)
from tools.pf2e_rules import main
raise SystemExit(main(['evidence-snapshot-verify', {str(manifest)!r}]))
"""
    return subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8",
    )


def _production_class_review_authoring(tmp_path):
    from tests.pf2e_rules.test_class_review_overlay import (
        CURRENT_SNAPSHOT,
        _production_authoring,
    )

    source = tmp_path / "class-review-authoring.json"
    source.write_text(
        json.dumps(_production_authoring(), ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    return source, CURRENT_SNAPSHOT, ROOT / "compendium_data"


def _production_v2_class_review_authoring(tmp_path):
    from tests.pf2e_rules.test_class_review_overlay import (
        CURRENT_CLASS_REVIEW,
        CURRENT_CLASS_SOURCE_EVIDENCE,
        CURRENT_SNAPSHOT,
        _production_v2_authoring,
    )

    source = tmp_path / "class-source-review-authoring.json"
    source.write_text(
        json.dumps(_production_v2_authoring(), ensure_ascii=True, sort_keys=True),
        encoding="utf-8",
    )
    return (
        source,
        CURRENT_SNAPSHOT,
        ROOT / "compendium_data",
        CURRENT_CLASS_REVIEW,
        CURRENT_CLASS_SOURCE_EVIDENCE,
    )


def _tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _offline_class_review_cli(arguments, *, env=None):
    script = f"""
import sys

def refuse_network(event, _args):
    if event.startswith('socket.'):
        raise AssertionError('class-review commands must remain offline')

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
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8",
    )


def test_compile_validate_and_diff_use_real_files(tmp_path):
    result = cli("compile", FIXTURE, "--store", tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "draft"
    package = Path(report["path"])
    verified = cli("validate", package, "--expected-hash", report["package_hash"])
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["enabled_records"] == 2
    diff = cli("diff", package, package)
    assert diff.returncode == 0, diff.stderr
    assert json.loads(diff.stdout)["records"] == {"added": [], "removed": [], "changed": []}
    assert cli("compile", FIXTURE, "--store", tmp_path).returncode == 2
    rejected = cli("validate", package, "--expected-hash", "0" * 64)
    assert rejected.returncode == 2
    assert json.loads(rejected.stderr)["error"] == "binding_mismatch"


@pytest.mark.parametrize("data", ['{"manifest":{},"manifest":{}}', '{"x": NaN}', '{'])
def test_bad_json_reports_a_domain_error_without_traceback(tmp_path, data):
    source = tmp_path / "bad.json"
    source.write_text(data, encoding="utf-8")
    result = cli("compile", source, "--store", tmp_path / "packages")
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] in {"duplicate_key", "invalid_json"}
    assert not (tmp_path / "packages").exists()
    assert "Traceback" not in result.stderr


def test_nonexistent_input_reports_io_error(tmp_path):
    result = cli("compile", tmp_path / "missing.json", "--store", tmp_path)
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "io_error"


def test_symlink_input_is_refused(tmp_path):
    source = tmp_path / "linked.json"
    try:
        source.symlink_to(FIXTURE)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")
    result = cli("compile", source, "--store", tmp_path / "store")
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "invalid_package_layout"
    assert not (tmp_path / "store").exists()


def _corpus_record():
    return {
        "_id": "abcdefghijklmnop",
        "name": "Synthetic Class",
        "type": "class",
        "system": {
            "publication": {
                "title": "Synthetic Core",
                "license": "ORC",
                "remaster": True,
            },
            "traits": {"value": ["synthetic"]},
        },
    }


def test_evidence_scan_uses_real_files_and_is_deterministic(tmp_path):
    pack = tmp_path / "classes"
    pack.mkdir()
    source = pack / "synthetic.json"
    source.write_text(json.dumps(_corpus_record()), encoding="utf-8")
    (pack / "_folders.json").write_text("[]", encoding="utf-8")
    spells = tmp_path / "spells"
    spells.mkdir()
    (spells / "master_spells.json").write_text("[]", encoding="utf-8")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }

    first = cli(
        "evidence-scan", tmp_path,
        env={**os.environ, "PYTHONHASHSEED": "1"},
    )
    second = cli(
        "evidence-scan", tmp_path,
        env={**os.environ, "PYTHONHASHSEED": "417"},
    )

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert report["coverage"]["files"] == 3
    assert report["coverage"]["records"] == 1
    assert report["coverage"]["structural_files"] == 1
    assert report["coverage"]["generated_files"] == 1
    assert report["records"][0]["local_identity"] == {
        "pack": "classes",
        "declared_type": "class",
        "foundry_id": "abcdefghijklmnop",
    }
    summary = cli("evidence-scan", tmp_path, "--summary")
    assert summary.returncode == 0, summary.stderr
    assert json.loads(summary.stdout) == {
        "schema_version": 1,
        "coverage": report["coverage"],
    }
    assert {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    } == before


def test_evidence_audit_returns_zero_for_complete_and_one_for_missing(tmp_path):
    before = (CENSUS.read_bytes(), LEDGER.read_bytes())

    complete = cli("evidence-audit", CENSUS, LEDGER)

    assert complete.returncode == 0, complete.stderr
    complete_report = json.loads(complete.stdout)
    assert complete_report["complete"] is True
    assert complete_report["coverage"]["by_disposition"]["pending"] == 1
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    ledger["entries"].pop(1)
    incomplete_ledger = tmp_path / "incomplete-ledger.json"
    incomplete_ledger.write_text(json.dumps(ledger), encoding="utf-8")

    incomplete = cli("evidence-audit", CENSUS, incomplete_ledger)

    assert incomplete.returncode == 1, incomplete.stderr
    assert incomplete.stderr == ""
    assert json.loads(incomplete.stdout)["missing"] == ["Feats.aspx:7"]
    assert (CENSUS.read_bytes(), LEDGER.read_bytes()) == before


def test_evidence_diff_returns_zero_when_clean_and_one_for_drift(tmp_path):
    from copy import deepcopy

    from systems.pf2e.rules.ingestion.evidence_inventory import (
        audit_evidence_inventory,
        evidence_fingerprint,
    )

    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    before_report = audit_evidence_inventory(census, ledger)
    after_census = deepcopy(census)
    after_census["records"][0]["name"] = "Renamed Synthetic Class"
    after_census["records"][0]["fingerprint"] = evidence_fingerprint(
        after_census["records"][0]
    )
    after_report = audit_evidence_inventory(after_census, ledger)
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    before_path.write_text(json.dumps(before_report), encoding="utf-8")
    after_path.write_text(json.dumps(after_report), encoding="utf-8")
    incomplete_ledger = deepcopy(ledger)
    incomplete_ledger["entries"].pop(1)
    incomplete_path = tmp_path / "incomplete.json"
    incomplete_path.write_text(
        json.dumps(audit_evidence_inventory(census, incomplete_ledger)),
        encoding="utf-8",
    )

    clean = cli("evidence-diff", before_path, before_path)
    drift = cli("evidence-diff", before_path, after_path)

    assert clean.returncode == 0, clean.stderr
    assert all(not value for key, value in json.loads(clean.stdout).items()
               if key not in {"before_census", "after_census"})
    assert drift.returncode == 1, drift.stderr
    assert json.loads(drift.stdout)["evidence_changed"] == ["Classes.aspx:7"]
    rejected = cli("evidence-diff", incomplete_path, before_path)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr)["error"] == "incomplete_inventory"
    assert "Traceback" not in rejected.stderr


def test_evidence_snapshot_verify_is_compact_deterministic_offline_and_nonmutating(
        tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    before = {
        path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
        for path in manifest_path.parent.rglob("*") if path.is_file()
    }

    first = _offline_snapshot_cli(
        manifest_path, env={**os.environ, "PYTHONHASHSEED": "1"}
    )
    second = _offline_snapshot_cli(
        manifest_path, env={**os.environ, "PYTHONHASHSEED": "417"}
    )

    assert first.returncode == second.returncode == 0
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    assert first.stdout.count("\n") == 1
    assert json.loads(first.stdout) == {
        "schema_version": 1,
        "snapshot_id": manifest["snapshot_id"],
        "scope_id": manifest["scope_id"],
        "resolved_index": manifest["resolved_index"],
        "site_update_date": "2026-10-07",
        "complete": True,
        "census_schema_version": 1,
        "categories": {"deferred": 1, "excluded": 1, "included": 2},
        "records": {"deferred": 2, "excluded": 1, "included": 2, "total": 5},
        "dispositions": {"excluded": 0, "mapped": 0, "pending": 2},
        "reviews": {"pending": 2, "reviewed": 0},
        "kinds": {"class": 1, "feat": 1},
        "page_families": {"Classes.aspx": 1, "Feats.aspx": 1},
        "class_count": 1,
        "shard_count": 2,
    }
    assert {
        path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
        for path in manifest_path.parent.rglob("*") if path.is_file()
    } == before


def test_evidence_snapshot_verify_reports_invalid_manifest_without_traceback(tmp_path):
    manifest = tmp_path / "snapshot-manifest.json"
    manifest.write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )

    result = cli("evidence-snapshot-verify", manifest)

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": "duplicate_key", "path": "$"}
    assert "Traceback" not in result.stderr


def test_evidence_snapshot_verify_reports_tampering_without_mutation_or_traceback(
        tmp_path):
    manifest_path, _manifest = _snapshot_tree(tmp_path)
    census = manifest_path.parent / "census" / "class.json"
    census.write_bytes(census.read_bytes() + b" ")
    before = {
        path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
        for path in manifest_path.parent.rglob("*") if path.is_file()
    }

    result = cli("evidence-snapshot-verify", manifest_path)

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {
        "error": "hash_mismatch",
        "path": "$.shards[0].census.sha256",
    }
    assert "Traceback" not in result.stderr
    assert {
        path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
        for path in manifest_path.parent.rglob("*") if path.is_file()
    } == before


def test_class_review_compile_and_verify_use_real_frozen_inputs_without_mutation(
        tmp_path):
    authoring, snapshot, corpus = _production_class_review_authoring(tmp_path)
    store = tmp_path / "reviews"
    snapshot_before = _tree_bytes(snapshot.parent)
    classes_before = _tree_bytes(corpus / "classes")
    authoring_before = authoring.read_bytes()

    compiled = cli(
        "class-review-compile", authoring,
        "--snapshot", snapshot,
        "--corpus", corpus,
        "--store", store,
    )

    assert compiled.returncode == 0, compiled.stderr
    assert compiled.stderr == ""
    compiled.stdout.encode("ascii")
    report = json.loads(compiled.stdout)
    assert set(report) == {
        "activation", "counts", "overlay_hash", "overlay_id", "path",
    }
    assert report["overlay_id"] == "pf2e-class-identities-2026-10-07.1"
    assert report["activation"] == "none"
    assert report["counts"]["sources"] == 8
    assert report["counts"]["records"] == 29
    assert report["counts"]["enabled_mechanics"] == 0
    package = Path(report["path"])
    assert package.parent == store.absolute()

    verified = cli(
        "class-review-verify", package,
        "--snapshot", snapshot,
        "--corpus", corpus,
        "--expected-hash", report["overlay_hash"],
    )

    assert verified.returncode == 0, verified.stderr
    assert verified.stderr == ""
    verified.stdout.encode("ascii")
    assert json.loads(verified.stdout) == report
    duplicate = cli(
        "class-review-compile", authoring,
        "--snapshot", snapshot,
        "--corpus", corpus,
        "--store", store,
    )
    assert duplicate.returncode == 2
    assert duplicate.stdout == ""
    assert json.loads(duplicate.stderr) == {
        "error": "overlay_exists", "path": "$.manifest.overlay_id",
    }
    rejected = cli(
        "class-review-verify", package,
        "--snapshot", snapshot,
        "--corpus", corpus,
        "--expected-hash", "0" * 64,
    )
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert json.loads(rejected.stderr) == {
        "error": "binding_mismatch", "path": "$.binding.overlay_hash",
    }
    assert "Traceback" not in duplicate.stderr + rejected.stderr
    assert authoring.read_bytes() == authoring_before
    assert _tree_bytes(snapshot.parent) == snapshot_before
    assert _tree_bytes(corpus / "classes") == classes_before

    from tests.pf2e_rules.test_class_review_overlay import (
        CURRENT_CLASS_REVIEW,
        CURRENT_CLASS_SOURCE_EVIDENCE,
    )

    for option, value, expected_path in (
        ("--parent", CURRENT_CLASS_REVIEW, "$.paths.parent"),
        ("--evidence", CURRENT_CLASS_SOURCE_EVIDENCE, "$.paths.evidence"),
    ):
        supplied_to_v1_compile = cli(
            "class-review-compile", authoring,
            "--snapshot", snapshot,
            "--corpus", corpus,
            "--store", tmp_path / f"v1-{option.removeprefix('--')}",
            option, value,
        )
        supplied_to_v1_verify = cli(
            "class-review-verify", package,
            "--snapshot", snapshot,
            "--corpus", corpus,
            option, value,
        )
        for result in (supplied_to_v1_compile, supplied_to_v1_verify):
            assert result.returncode == 2
            assert result.stdout == ""
            assert json.loads(result.stderr) == {
                "error": "unsupported_composition",
                "path": expected_path,
            }
            assert "Traceback" not in result.stderr


def test_schema_v2_class_review_cli_round_trip_is_offline_bounded_and_nonmutating(
        tmp_path):
    authoring, snapshot, corpus, parent, evidence = (
        _production_v2_class_review_authoring(tmp_path)
    )
    store = tmp_path / "reviews"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = {**os.environ, "DATA_DIR": str(data_dir)}
    before = {
        "authoring": authoring.read_bytes(),
        "snapshot": _tree_bytes(snapshot.parent),
        "classes": _tree_bytes(corpus / "classes"),
        "parent": _tree_bytes(parent),
        "evidence": _tree_bytes(evidence.parent),
    }

    compiled = _offline_class_review_cli(
        [
            "class-review-compile", authoring,
            "--snapshot", snapshot,
            "--corpus", corpus,
            "--store", store,
            "--parent", parent,
            "--evidence", evidence,
        ],
        env=environment,
    )

    assert compiled.returncode == 0, compiled.stderr
    assert compiled.stderr == ""
    assert compiled.stdout.count("\n") == 1
    compiled.stdout.encode("ascii")
    report = json.loads(compiled.stdout)
    assert report["overlay_id"] == "pf2e-class-source-review-2026-10-10.1"
    assert report["activation"] == "none"
    assert report["counts"]["overlay_gates"]["by_status"] == {
        "pending": 37,
        "approved": 8,
        "rejected": 0,
    }
    assert report["counts"]["enabled_mechanics"] == 0
    package = Path(report["path"])

    verified = _offline_class_review_cli(
        [
            "class-review-verify", package,
            "--snapshot", snapshot,
            "--corpus", corpus,
            "--expected-hash", report["overlay_hash"],
            "--parent", parent,
            "--evidence", evidence,
        ],
        env=environment,
    )

    assert verified.returncode == 0, verified.stderr
    assert verified.stderr == ""
    assert json.loads(verified.stdout) == report

    missing_cases = (
        ("class-review-compile", ["--evidence", evidence], "$.paths.parent"),
        ("class-review-compile", ["--parent", parent], "$.paths.evidence"),
        ("class-review-verify", ["--evidence", evidence], "$.paths.parent"),
        ("class-review-verify", ["--parent", parent], "$.paths.evidence"),
    )
    for index, (command, ancestry, expected_path) in enumerate(missing_cases):
        if command == "class-review-compile":
            arguments = [
                command, authoring,
                "--snapshot", snapshot,
                "--corpus", corpus,
                "--store", tmp_path / f"missing-{index}",
                *ancestry,
            ]
        else:
            arguments = [
                command, package,
                "--snapshot", snapshot,
                "--corpus", corpus,
                *ancestry,
            ]
        result = _offline_class_review_cli(arguments, env=environment)
        assert result.returncode == 2
        assert result.stdout == ""
        assert json.loads(result.stderr) == {
            "error": "missing_binding",
            "path": expected_path,
        }
        assert "Traceback" not in result.stderr

    wrong_inputs = (
        ("--parent", tmp_path / "missing-parent"),
        ("--evidence", tmp_path / "missing-evidence" / "manifest.json"),
    )
    for index, (option, wrong_path) in enumerate(wrong_inputs):
        ancestry = ["--parent", parent, "--evidence", evidence]
        ancestry[ancestry.index(option) + 1] = wrong_path
        result = _offline_class_review_cli(
            [
                "class-review-compile", authoring,
                "--snapshot", snapshot,
                "--corpus", corpus,
                "--store", tmp_path / f"wrong-{index}",
                *ancestry,
            ],
            env=environment,
        )
        assert result.returncode == 2
        assert result.stdout == ""
        assert json.loads(result.stderr) == {
            "error": "invalid_package_layout",
            "path": "$files",
        }
        assert "Traceback" not in result.stderr

    assert authoring.read_bytes() == before["authoring"]
    assert _tree_bytes(snapshot.parent) == before["snapshot"]
    assert _tree_bytes(corpus / "classes") == before["classes"]
    assert _tree_bytes(parent) == before["parent"]
    assert _tree_bytes(evidence.parent) == before["evidence"]
    assert list(data_dir.iterdir()) == []


def test_class_review_commands_are_offline_application_independent_and_ignore_data_dir(
        tmp_path):
    authoring, snapshot, corpus = _production_class_review_authoring(tmp_path)
    store = tmp_path / "reviews"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    package = store / "pf2e-class-identities-2026-10-07.1"
    environment = {**os.environ, "DATA_DIR": str(data_dir)}

    compiled = _offline_class_review_cli(
        [
            "class-review-compile", authoring,
            "--snapshot", snapshot,
            "--corpus", corpus,
            "--store", store,
        ],
        env=environment,
    )
    verified = _offline_class_review_cli(
        [
            "class-review-verify", package,
            "--snapshot", snapshot,
            "--corpus", corpus,
        ],
        env=environment,
    )

    assert compiled.returncode == 0, compiled.stderr
    assert verified.returncode == 0, verified.stderr
    assert json.loads(compiled.stdout) == json.loads(verified.stdout)
    assert list(data_dir.iterdir()) == []


def test_class_review_compile_reports_bad_authoring_without_traceback(tmp_path):
    source = tmp_path / "bad-authoring.json"
    source.write_text('{"manifest":{},"manifest":{}}', encoding="utf-8")
    store = tmp_path / "reviews"

    result = cli(
        "class-review-compile", source,
        "--snapshot", tmp_path / "snapshot-manifest.json",
        "--corpus", tmp_path / "corpus",
        "--store", store,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": "duplicate_key", "path": "$"}
    assert "Traceback" not in result.stderr
    assert not store.exists()


def test_class_review_cli_does_not_expose_synthetic_fixture_mode(tmp_path):
    authoring, snapshot, corpus = _production_class_review_authoring(tmp_path)
    store = tmp_path / "reviews"

    result = cli(
        "class-review-compile", authoring,
        "--snapshot", snapshot,
        "--corpus", corpus,
        "--store", store,
        "--fixture-mode",
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "unrecognized arguments: --fixture-mode" in result.stderr
    assert not store.exists()


@pytest.mark.parametrize("command", ["evidence-audit", "evidence-diff"])
def test_evidence_commands_reject_invalid_json_without_traceback(tmp_path, command):
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")

    result = cli(command, invalid, invalid)

    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "duplicate_key"
    assert "Traceback" not in result.stderr


def test_evidence_scan_refuses_a_linked_root_without_traceback(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    linked = tmp_path / "linked-corpus"
    try:
        linked.symlink_to(corpus, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Host does not permit directory symlink creation: {error}")

    result = cli("evidence-scan", linked)

    assert result.returncode == 2
    assert json.loads(result.stderr) == {
        "error": "invalid_package_layout", "path": "$files",
    }
    assert "Traceback" not in result.stderr


def test_evidence_cli_is_application_independent_and_does_not_touch_data(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    script = f"""
import sys
from tools.pf2e_rules import main
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
raise SystemExit(main(['evidence-audit', {str(CENSUS)!r}, {str(LEDGER)!r}]))
"""

    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        env={**os.environ, "DATA_DIR": str(data_dir)},
        capture_output=True, text=True, encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["complete"] is True
    assert list(data_dir.iterdir()) == []


def test_cli_refuses_to_emit_a_result_larger_than_its_readback_budget(
        monkeypatch, capsys):
    import tools.pf2e_rules as command

    monkeypatch.setattr(command, "MAX_FILE_BYTES", 32, raising=False)

    with pytest.raises(ValueError) as error:
        command._render_result({"value": "x" * 64})

    assert error.value.code == "limit_exceeded"
    assert error.value.path == "$"

    code = command.main(["evidence-audit", str(CENSUS), str(LEDGER)])
    output = capsys.readouterr()
    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "limit_exceeded", "path": "$"}


def test_snapshot_verify_result_must_fit_the_strict_readback_node_budget(
        monkeypatch, capsys):
    import tools.pf2e_rules as command

    monkeypatch.setattr(
        command,
        "verify_evidence_snapshot",
        lambda _manifest: {"rows": [None] * 500_000},
    )

    code = command.main(["evidence-snapshot-verify", "unused-manifest.json"])
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "limit_exceeded", "path": "$"}


def test_class_review_verify_result_must_fit_the_strict_readback_node_budget(
        monkeypatch, capsys):
    import tools.pf2e_rules as command

    seen = {}

    def oversized_result(*_args, **kwargs):
        seen.update(kwargs)
        return {"rows": [None] * 500_000}

    monkeypatch.setattr(
        command,
        "verify_class_review_overlay",
        oversized_result,
        raising=False,
    )

    code = command.main([
        "class-review-verify", "unused-overlay",
        "--snapshot", "unused-snapshot",
        "--corpus", "unused-corpus",
        "--parent", "unused-parent",
        "--evidence", "unused-evidence",
    ])
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "limit_exceeded", "path": "$"}
    assert seen["parent_overlay_path"] == Path("unused-parent")
    assert seen["evidence_manifest_path"] == Path("unused-evidence")


def test_audit_result_must_fit_the_strict_readback_node_budget():
    import tools.pf2e_rules as command

    result = {"rows": [None] * 500_000}

    with pytest.raises(ValueError) as error:
        command._render_result(result, require_readback=True)

    assert error.value.code == "limit_exceeded"
    assert error.value.path == "$"


LEGAL_RULES = ROOT / "systems/pf2e/rules"
LEGAL_MANIFEST = LEGAL_RULES / "evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/manifest.json"
LEGAL_CHAIN = {
    "--overlay": LEGAL_RULES / "reviews/pf2e-class-source-review-2026-10-10.1",
    "--snapshot": LEGAL_RULES / "evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json",
    "--corpus": ROOT / "compendium_data",
    "--parent": LEGAL_RULES / "reviews/pf2e-class-identities-2026-10-07.1",
    "--source-evidence": LEGAL_RULES / "evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json",
}
LEGAL_HASH = "4eeaef808fae0aa8b7ccc1356fcb7381b4d672a8d2281427f01a7ae9cc7cb410"
LEGAL_COUNTS = {
    "sources": 8, "artifact_hashes_observed": 1, "final_artifacts_missing": 7,
    "final_legal_notices_observed": 1, "final_legal_notices_missing": 7,
    "printings_unverified": 8, "revisions_unverified": 8,
    "errata_inventories_complete_as_captured": 1, "errata_inventories_unverified": 7,
    "license_reviews_ready": 0, "license_reviews_blocked": 8,
    "license_reviews_approved": 0, "enabled_mechanics": 0,
}
LEGAL_COMMANDS = ("class-legal-readiness-verify", "class-legal-readiness-report")


def _legal_arguments(command, *, expected_hash=LEGAL_HASH):
    arguments = [command, LEGAL_MANIFEST]
    for option, path in LEGAL_CHAIN.items():
        arguments.extend([option, path])
    if expected_hash is not None:
        arguments.extend(["--expected-hash", expected_hash])
    return arguments


def _offline_class_legal_readiness_cli(arguments, data_dir, *, hash_seed="1"):
    script = f"""
import os
import sys
data_root = os.path.normcase(os.path.abspath({str(data_dir)!r}))

def refuse_external_access(event, args):
    if event.startswith('socket.'):
        raise AssertionError('legal-readiness commands must remain offline')
    if event in {{'open', 'os.listdir', 'os.scandir', 'os.mkdir', 'os.remove',
                  'os.rmdir', 'os.rename', 'os.replace'}}:
        for argument in args:
            if isinstance(argument, (str, bytes, os.PathLike)):
                path = os.path.normcase(os.path.abspath(os.fsdecode(argument)))
                if path == data_root or path.startswith(data_root + os.sep):
                    raise AssertionError('legal-readiness commands must ignore DATA_DIR')

sys.addaudithook(refuse_external_access)
from tools.pf2e_rules import main
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
code = main({[str(argument) for argument in arguments]!r})
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
raise SystemExit(code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT,
        env={**os.environ, "DATA_DIR": str(data_dir), "PYTHONHASHSEED": hash_seed},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert list(data_dir.iterdir()) == []
    return result


def _legal_chain_hashes():
    roots = [LEGAL_MANIFEST.parent, *[
        path.parent if path.suffix == ".json" else path
        for path in LEGAL_CHAIN.values()
    ]]
    return {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in roots for path in root.rglob("*") if path.is_file()
    }


def test_class_legal_readiness_verify_cli_uses_exact_chain_offline_and_nonmutating(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    before = _legal_chain_hashes()

    result = _offline_class_legal_readiness_cli(_legal_arguments(LEGAL_COMMANDS[0]), data_dir)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    expected = {
        "path": str(LEGAL_MANIFEST.absolute()),
        "bundle_id": "pf2e-class-legal-readiness-2026-10-11.1",
        "bundle_hash": LEGAL_HASH,
        "scope": "class-license-readiness",
        "class_review_overlay_id": "pf2e-class-source-review-2026-10-10.1",
        "class_review_overlay_hash": "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb",
        "source_bundle_id": "pf2e-class-sources-2026-10-10.1",
        "source_bundle_hash": "4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3",
        "counts": LEGAL_COUNTS,
    }
    assert json.loads(result.stdout) == expected
    assert result.stdout == json.dumps(expected, sort_keys=True, ensure_ascii=True) + "\n"
    assert _legal_chain_hashes() == before


def test_class_legal_readiness_report_cli_is_deterministic_and_reports_exact_blockers(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    first = _offline_class_legal_readiness_cli(_legal_arguments(LEGAL_COMMANDS[1]), data_dir)
    second = _offline_class_legal_readiness_cli(
        _legal_arguments(LEGAL_COMMANDS[1]), data_dir, hash_seed="417",
    )

    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stderr == second.stderr == ""
    observed_blockers = [
        "downstream-compliance-missing", "errata-applicability-unresolved",
        "named-reviewer-missing", "printing-unverified", "revision-unverified",
    ]
    missing_blockers = [
        "downstream-compliance-missing", "errata-applicability-unresolved",
        "errata-inventory-unverified", "final-artifact-missing",
        "final-legal-notice-missing", "named-reviewer-missing",
        "printing-unverified", "revision-unverified",
    ]
    expected = {
        "bundle_id": "pf2e-class-legal-readiness-2026-10-11.1",
        "bundle_hash": LEGAL_HASH, "counts": LEGAL_COUNTS,
        "sources": [
            {"source_id": "pf2e.source." + slug,
             "blockers": observed_blockers if slug == "player-core" else missing_blockers}
            for slug in (
                "battlecry", "dark-archive-remastered", "guns-and-gears-remastered",
                "impossible-magic", "player-core", "player-core-2",
                "rage-of-elements", "war-of-immortals",
            )
        ],
    }
    assert json.loads(first.stdout) == expected
    assert first.stdout == second.stdout == json.dumps(expected, sort_keys=True, ensure_ascii=True) + "\n"


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
@pytest.mark.parametrize("missing", ["manifest", *LEGAL_CHAIN])
def test_class_legal_readiness_commands_require_every_chain_input(command, missing):
    arguments = _legal_arguments(command)
    if missing == "manifest":
        arguments.pop(1)
    else:
        index = arguments.index(missing)
        del arguments[index:index + 2]
    result = cli(*arguments)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "the following arguments are required: " + missing in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
def test_class_legal_readiness_verify_cli_rejects_wrong_trusted_hash_without_traceback(tmp_path, command):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    before = _legal_chain_hashes()
    result = _offline_class_legal_readiness_cli(
        _legal_arguments(command, expected_hash="0" * 64), data_dir,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert json.loads(result.stderr) == {"error": "binding_mismatch", "path": "$.binding.bundle_hash"}
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stderr
    assert _legal_chain_hashes() == before


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
@pytest.mark.parametrize("expected_hash", [None, LEGAL_HASH])
def test_class_legal_readiness_cli_forwards_paths_and_expected_hash_exactly(monkeypatch, capsys, command, expected_hash):
    import tools.pf2e_rules as module
    from systems.pf2e.rules.ingestion.class_legal_readiness import (
        class_legal_readiness_report, verify_class_legal_readiness_bundle,
    )

    calls, reports, verified = [], [], []

    def verify(*args, **kwargs):
        calls.append((args, kwargs))
        bundle = verify_class_legal_readiness_bundle(*args, **kwargs)
        verified.append(bundle)
        return bundle

    def report(bundle):
        reports.append(bundle)
        return class_legal_readiness_report(bundle)

    monkeypatch.setattr(module, "verify_class_legal_readiness_bundle", verify, raising=False)
    monkeypatch.setattr(module, "class_legal_readiness_report", report, raising=False)
    # Relative paths catch unwanted normalization at the forwarding boundary.
    arguments = [str(value.relative_to(ROOT)) if isinstance(value, Path) else value
                 for value in _legal_arguments(command, expected_hash=expected_hash)]
    code = module.main(arguments)
    output = capsys.readouterr()

    assert code == 0, output.err
    assert len(calls) == 1
    assert calls[0] == ((
        LEGAL_MANIFEST.relative_to(ROOT), LEGAL_CHAIN["--overlay"].relative_to(ROOT),
        LEGAL_CHAIN["--snapshot"].relative_to(ROOT), LEGAL_CHAIN["--corpus"].relative_to(ROOT),
    ), {
        "expected_hash": expected_hash,
        "parent_overlay_path": LEGAL_CHAIN["--parent"].relative_to(ROOT),
        "source_evidence_manifest_path": LEGAL_CHAIN["--source-evidence"].relative_to(ROOT),
    })
    if command.endswith("report"):
        assert len(reports) == 1 and reports[0] is verified[0]
    else:
        assert reports == []
    assert output.err == ""


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
def test_class_legal_readiness_cli_results_fit_strict_readback_budget(tmp_path, command):
    from systems.pf2e.rules.manifest import MAX_FILE_BYTES, read_json

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    result = _offline_class_legal_readiness_cli(_legal_arguments(command), data_dir)

    assert result.returncode == 0, result.stderr
    emitted = result.stdout.encode("ascii")
    assert len(emitted) <= MAX_FILE_BYTES
    assert result.stdout.count("\n") == 1
    assert read_json(emitted) == json.loads(result.stdout)


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
@pytest.mark.parametrize("budget", ["bytes", "nodes"])
def test_class_legal_readiness_cli_rejects_results_over_readback_budget(monkeypatch, capsys, command, budget):
    import tools.pf2e_rules as module

    oversized = "x" * 64 if budget == "bytes" else [None] * 500_000
    manifest = json.loads(LEGAL_MANIFEST.read_text(encoding="utf-8"))
    manifest["counts"] = {"oversized": oversized}
    bundle = {"manifest": manifest, "dossiers_by_hash": {}, "dossiers_by_source": {}}
    monkeypatch.setattr(module, "verify_class_legal_readiness_bundle", lambda *_args, **_kwargs: bundle, raising=False)
    monkeypatch.setattr(module, "class_legal_readiness_report", lambda _bundle: {"counts": manifest["counts"]}, raising=False)
    if budget == "bytes":
        monkeypatch.setattr(module, "MAX_FILE_BYTES", 32)

    code = module.main([str(argument) for argument in _legal_arguments(command)])
    output = capsys.readouterr()

    assert code == 2
    assert output.out == ""
    assert json.loads(output.err) == {"error": "limit_exceeded", "path": "$"}


@pytest.mark.parametrize("command", LEGAL_COMMANDS)
@pytest.mark.parametrize("failure", ["domain", "io"])
def test_class_legal_readiness_cli_stops_before_reporting_failed_verification(monkeypatch, capsys, command, failure):
    import tools.pf2e_rules as module
    from systems.pf2e.rules.validation import RulesValidationError

    calls = []

    def verify(*_args, **_kwargs):
        calls.append("verify")
        if failure == "domain":
            raise RulesValidationError("binding_mismatch", "$.binding.bundle_hash")
        raise OSError(str(ROOT / "private-host-path"))

    def report(_bundle):
        raise AssertionError("unverified data must not reach the report")

    monkeypatch.setattr(module, "verify_class_legal_readiness_bundle", verify, raising=False)
    monkeypatch.setattr(module, "class_legal_readiness_report", report, raising=False)
    code = module.main([str(argument) for argument in _legal_arguments(command)])
    output = capsys.readouterr()

    assert calls == ["verify"]
    assert code == 2
    assert output.out == ""
    expected = ({"error": "binding_mismatch", "path": "$.binding.bundle_hash"}
                if failure == "domain" else {"error": "io_error", "path": "$files"})
    assert json.loads(output.err) == expected
    assert "Traceback" not in output.err
    assert str(ROOT) not in output.err


@pytest.mark.parametrize("mode", ["accept", "reject", "mismatch"])
def test_render_result_reparses_strict_json_before_emitting(monkeypatch, capsys, mode):
    import tools.pf2e_rules as module
    from systems.pf2e.rules.manifest import read_json
    from systems.pf2e.rules.validation import RulesValidationError

    result = {"z": "\u00e9", "a": [1, True, None]}
    seen = []

    def parse(data):
        seen.append(data)
        if mode == "reject":
            raise RulesValidationError("invalid_json", "$")
        if mode == "mismatch":
            return {"different": True}
        return read_json(data)

    monkeypatch.setattr(module, "read_json", parse)
    monkeypatch.setattr(module, "verify_evidence_snapshot", lambda _path: result)
    code = module.main(["evidence-snapshot-verify", "unused.json"])
    output = capsys.readouterr()

    assert seen == [b'{"a": [1, true, null], "z": "\\u00e9"}\n']
    if mode == "accept":
        assert code == 0
        assert output.out.encode("ascii") == seen[0]
        assert output.err == ""
    else:
        assert code == 2
        assert output.out == ""
        assert json.loads(output.err) == {"error": "invalid_json", "path": "$"}


@pytest.mark.parametrize("byte_limit", [None, 9, 8])
def test_strict_readback_matches_raw_stdout_and_enforces_newline_byte_budget(byte_limit):
    # Nine bytes fit with LF; Windows text emission used to add an unchecked CR.
    script = f"""
import json
import sys
import tools.pf2e_rules as command
from systems.pf2e.rules.manifest import read_json

readback = []
def capture_readback(data):
    readback.append(data.hex())
    return read_json(data)

command.read_json = capture_readback
command.verify_evidence_snapshot = lambda _path: {{'a': 1}}
byte_limit = {byte_limit!r}
if byte_limit is not None:
    command.MAX_FILE_BYTES = byte_limit
code = command.main(['evidence-snapshot-verify', 'unused.json'])
# Keep instrumentation separate from CLI diagnostics and capture both as bytes.
sys.stderr.buffer.write(b'\\nREADBACK:' + json.dumps(readback).encode('ascii'))
raise SystemExit(code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, capture_output=True,
    )
    diagnostics, captured_readback = result.stderr.rsplit(b"\nREADBACK:", 1)
    reparsed_bytes = [bytes.fromhex(value) for value in json.loads(captured_readback)]

    if byte_limit == 8:
        assert result.returncode == 2
        assert result.stdout == b""
        assert reparsed_bytes == []
        assert json.loads(diagnostics) == {"error": "limit_exceeded", "path": "$"}
    else:
        assert result.returncode == 0, diagnostics
        assert diagnostics == b""
        assert reparsed_bytes == [b'{"a": 1}\n']
        if byte_limit is not None:
            assert len(result.stdout) <= byte_limit
        assert result.stdout == reparsed_bytes[0]


def test_class_legal_readiness_cli_exposes_no_compile_or_approval_command():
    help_result = cli("--help")
    assert help_result.returncode == 0
    for command in LEGAL_COMMANDS:
        assert command in help_result.stdout
        rejected = cli(*_legal_arguments(command), "--store", "unused-store")
        assert rejected.returncode == 2
        assert "unrecognized arguments: --store" in rejected.stderr
        assert rejected.stdout == ""
    for action in ("compile", "publish", "approve", "approval", "activate", "activation"):
        result = cli("class-legal-readiness-" + action)
        assert result.returncode == 2
        assert "invalid choice:" in result.stderr
        assert result.stdout == ""
