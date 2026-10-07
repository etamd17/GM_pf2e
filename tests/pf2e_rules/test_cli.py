"""The operator interface must fail cleanly and leave existing packages intact."""
import json
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


def test_audit_result_must_fit_the_strict_readback_node_budget():
    import tools.pf2e_rules as command

    result = {"rows": [None] * 500_000}

    with pytest.raises(ValueError) as error:
        command._render_result(result, require_readback=True)

    assert error.value.code == "limit_exceeded"
    assert error.value.path == "$"
