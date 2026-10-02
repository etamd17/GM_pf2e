"""Exercise real package files and subprocesses, including untrusted authoring."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def compiler():
    return importlib.import_module("systems.pf2e.rules.ingestion.compile_pack")


def registry():
    return importlib.import_module("systems.pf2e.rules.registry")


def test_same_inputs_compile_to_identical_bytes_after_reordering(authoring):
    first = compiler().compile_package(authoring)
    reordered = deepcopy(authoring)
    reordered["records"].reverse()
    reordered["records"][0]["traits"] = list(reversed(reordered["records"][0]["traits"]))
    second = compiler().compile_package(reordered)
    assert first == second
    assert set(first) == {"authoring.json", "sources.json", "records.json", "manifest.json"}
    manifest = json.loads(first["manifest.json"])
    assert len(manifest["package_hash"]) == 64
    changed = deepcopy(authoring)
    changed["records"][0]["name"] = "Changed mechanical reference"
    assert json.loads(compiler().compile_package(changed)["manifest.json"])["package_hash"] != manifest["package_hash"]
    assert all(b"\r\n" not in value for value in first.values())


def test_repeat_process_compilation_is_hash_seed_independent():
    script = """
import json
from pathlib import Path
from systems.pf2e.rules.ingestion.compile_pack import compile_package
value = json.loads(Path('tests/pf2e_rules/fixtures/synthetic.json').read_text(encoding='utf-8'))
print(compile_package(value)['manifest.json'].decode('utf-8'))
"""
    outputs = []
    for seed in ("1", "417"):
        result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "PYTHONHASHSEED": seed},
                                text=True, encoding="utf-8", capture_output=True, check=True)
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


def test_loaded_records_are_deeply_immutable_and_detached(authoring, tmp_path):
    path = compiler().write_package(authoring, tmp_path / "store")
    package = registry().load_package(path)
    record = package.get("pf2e.class.example")
    assert record.name == "Example class"
    assert record.traits == ("test",)
    with pytest.raises((TypeError, AttributeError)):
        record.name = "corrupt"
    with pytest.raises(TypeError):
        package.manifest["reviews"]["rules"] = {}
    with pytest.raises(TypeError):
        package.records["pf2e.class.other"] = record
    authoring["records"][0]["name"] = "changed input"
    assert package.get("pf2e.class.example").name == "Example class"


def test_quarantine_cannot_be_looked_up(authoring, tmp_path):
    authoring["records"][1].update(state="quarantined", quarantine_reason="Unverified")
    authoring["manifest"]["exclusions"] = [{"id": "pf2e.feat.example", "reason": "Unverified"}]
    package = registry().load_package(compiler().write_package(authoring, tmp_path))
    with pytest.raises(KeyError):
        package.get("pf2e.feat.example")
    assert package.records["pf2e.feat.example"].state == "quarantined"


@pytest.mark.parametrize("filename", ["authoring.json", "sources.json", "records.json", "manifest.json"])
def test_any_changed_file_fails_loading(authoring, tmp_path, filename):
    path = compiler().write_package(authoring, tmp_path)
    original = (path / filename).read_bytes()
    (path / filename).write_bytes(original + b" ")
    with pytest.raises(ValueError) as error:
        registry().load_package(path)
    assert error.value.code == "integrity_mismatch"


def test_trusted_hash_rejects_self_consistent_replacement(authoring, tmp_path):
    path = compiler().write_package(authoring, tmp_path / "first")
    package = registry().load_package(path)
    changed = deepcopy(authoring)
    changed["records"][0]["name"] = "Altered"
    other = compiler().write_package(changed, tmp_path / "second")
    with pytest.raises(ValueError) as error:
        registry().load_package(other, expected_hash=package.manifest["package_hash"])
    assert error.value.code == "binding_mismatch"
    assert registry().load_package(path, expected_hash=package.manifest["package_hash"]).get("pf2e.class.example")


def test_existing_package_cannot_be_overwritten_even_with_identical_content(authoring, tmp_path):
    path = compiler().write_package(authoring, tmp_path)
    before = {p.name: p.read_bytes() for p in path.iterdir()}
    with pytest.raises(ValueError) as error:
        compiler().write_package(authoring, tmp_path)
    assert error.value.code == "package_exists"
    assert {p.name: p.read_bytes() for p in path.iterdir()} == before


def test_concurrent_publication_has_one_winner(authoring, tmp_path):
    def publish():
        try:
            return compiler().write_package(authoring, tmp_path)
        except ValueError as error:
            assert error.code in {"package_exists", "publication_locked"}
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert sum(p is not None for p in results) == 1
    registry().load_package(next(p for p in results if p is not None))


@pytest.mark.parametrize("mutation", ["extra", "missing", "directory"])
def test_package_layout_is_exact(authoring, tmp_path, mutation):
    path = compiler().write_package(authoring, tmp_path)
    if mutation == "extra":
        (path / "script.py").write_text("raise RuntimeError()", encoding="utf-8")
    elif mutation == "missing":
        (path / "sources.json").unlink()
    else:
        (path / "sources.json").unlink()
        (path / "sources.json").mkdir()
    with pytest.raises(ValueError) as error:
        registry().load_package(path)
    assert error.value.code == "invalid_package_layout"


def test_symlinked_package_files_are_refused(authoring, tmp_path):
    path = compiler().write_package(authoring, tmp_path / "store")
    outside = tmp_path / "outside.json"
    original = (path / "records.json").read_bytes()
    outside.write_bytes(original)
    (path / "records.json").unlink()
    try:
        (path / "records.json").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")
    with pytest.raises(ValueError) as error:
        registry().load_package(path)
    assert error.value.code == "invalid_package_layout"
    assert outside.read_bytes() == original


@pytest.mark.parametrize("data", [
    b'{"x":1,"x":2}', b'{"x": NaN}', b'{"x": Infinity}', b'{"x":1.5}', b'\xff',
    b'[' * 1000 + b']' * 1000,
])
def test_strict_json_parser_refuses_ambiguous_or_unbounded_values(data):
    module = importlib.import_module("systems.pf2e.rules.manifest")
    with pytest.raises(ValueError):
        module.read_json(data)


def test_diff_reports_content_and_provenance_changes(authoring, tmp_path):
    before = registry().load_package(compiler().write_package(authoring, tmp_path / "before"))
    changed = deepcopy(authoring)
    changed["manifest"]["ruleset_id"] = "pf2e-test-fixture-0.2.0"
    changed["manifest"]["content_version"] = "0.2.0"
    changed["sources"][0]["edition"] = "New fixture edition"
    changed["records"][0]["name"] = "Updated"
    changed["records"].pop()
    after = registry().load_package(compiler().write_package(changed, tmp_path / "after"))
    diff = importlib.import_module("systems.pf2e.rules.ingestion.diff_pack").diff_packages(before, after)
    assert diff["records"] == {"added": [], "removed": ["pf2e.feat.example"], "changed": ["pf2e.class.example"]}
    assert diff["sources"]["changed"] == ["pf2e.source.synthetic"]
    assert "content_version" in diff["manifest_changed"]


def test_import_does_not_import_application_or_touch_data(tmp_path):
    script = """
import sys
import systems.pf2e.rules.registry
assert 'app' not in sys.modules
assert 'flask' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", script],
                            env={**os.environ, "DATA_DIR": str(tmp_path)},
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_publication_destination_comes_from_compiled_snapshot(authoring, tmp_path, monkeypatch):
    module = compiler()
    original = module.compile_package

    def compile_then_mutate(value):
        result = original(value)
        # Reproduce a caller changing shared input after the compiler copied it.
        value["manifest"]["ruleset_id"] = "../unvalidated-destination"
        return result

    monkeypatch.setattr(module, "compile_package", compile_then_mutate)
    path = module.write_package(authoring, tmp_path / "store")
    assert path == tmp_path / "store" / "pf2e-test-fixture-0.1.0"
    assert not (tmp_path / "unvalidated-destination").exists()
