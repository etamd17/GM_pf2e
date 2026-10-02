"""The operator interface must fail cleanly and leave existing packages intact."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "pf2e_rules" / "fixtures" / "synthetic.json"


def cli(*args):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "pf2e_rules.py"), *map(str, args)],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


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
