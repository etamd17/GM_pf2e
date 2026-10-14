"""CLI uses actual PDFs and subprocesses; source paths never survive serialization."""
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

from pypdf import PdfWriter
import pytest

ROOT = Path(__file__).resolve().parents[2]


def cli():
    return importlib.import_module("tools.cosmere_rules")


def pdf_artifact(tmp_path):
    path = tmp_path / "synthetic.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Edition": "v1.02"})
    writer.write(path)
    raw = path.read_bytes()
    schema = importlib.import_module("systems.cosmere.rules.schema")
    artifact = schema.SourceArtifact("cosmere.synthetic", path.name, "v1.02",
        hashlib.sha256(raw).hexdigest(), len(raw), 2, "handbook", "rules", "player", "private")
    return path, artifact


def test_inventory_verifies_real_pdf_without_serializing_local_paths(tmp_path):
    path, artifact = pdf_artifact(tmp_path)
    output = tmp_path / "private-inventory.json"
    result = cli().inventory_sources({artifact.product_id: str(path)}, {artifact.product_id: artifact},
                                     output, repo_root=ROOT)
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed == result
    assert parsed["private"] is True
    assert parsed["artifacts"][0]["page_count"] == 2
    assert parsed["artifacts"][0]["verification"] == "verified"
    assert parsed["artifacts"][0]["extraction_status"] == "not_extracted"
    assert str(tmp_path) not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["hash", "bytes", "pages", "edition", "basename", "unknown_id", "missing_file"])
def test_inventory_rejects_source_mismatch_without_writing_output(tmp_path, mutation):
    path, artifact = pdf_artifact(tmp_path)
    mapping = {artifact.product_id: str(path)}
    if mutation == "hash":
        artifact = replace(artifact, sha256="a" * 64)
    elif mutation == "bytes":
        artifact = replace(artifact, byte_count=artifact.byte_count + 1)
    elif mutation == "pages":
        artifact = replace(artifact, page_count=3)
    elif mutation == "edition":
        artifact = replace(artifact, edition="v1.01")
    elif mutation == "basename":
        artifact = replace(artifact, basename="other.pdf")
    elif mutation == "unknown_id":
        mapping = {"cosmere.missing": str(path)}
    elif mutation == "missing_file":
        mapping = {artifact.product_id: str(tmp_path / "missing.pdf")}
    output = tmp_path / "inventory.json"
    with pytest.raises(ValueError):
        cli().inventory_sources(mapping, {artifact.product_id: artifact}, output, repo_root=ROOT)
    assert not output.exists()


def test_inventory_refuses_output_inside_repository(tmp_path):
    path, artifact = pdf_artifact(tmp_path)
    output = tmp_path / "inventory.json"
    with pytest.raises(ValueError, match="outside"):
        cli().inventory_sources({artifact.product_id: str(path)}, {artifact.product_id: artifact},
                                 output, repo_root=tmp_path)
    assert not output.exists()


def test_inventory_keeps_quarantined_sources_comparison_only(tmp_path):
    path, artifact = pdf_artifact(tmp_path)
    artifact = replace(artifact, rights_state="quarantined")
    result = cli().inventory_sources({artifact.product_id: str(path)}, {artifact.product_id: artifact},
                                     tmp_path / "inventory.json", repo_root=ROOT)
    assert result["artifacts"][0]["source_policy"] == "comparison_only"


def test_compile_command_round_trip_from_any_cwd(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "cosmere_rules.py"), "compile",
        "--authoring", str(ROOT / "tests" / "fixtures" / "cosmere_rules" / "rules" / "authoring.json"),
        "--output", str(tmp_path / "registry.json")], cwd=tmp_path, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    registry = importlib.import_module("systems.cosmere.rules.registry").load_registry(tmp_path / "registry.json")
    assert len(registry.artifacts) == 1
    assert len(registry.packs) == 5
    assert str(ROOT) not in (tmp_path / "registry.json").read_text(encoding="utf-8")


def test_compile_command_rejects_source_binary_and_leaves_existing_output(tmp_path):
    path, _ = pdf_artifact(tmp_path)
    output = tmp_path / "registry.json"
    output.write_text("existing", encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "cosmere_rules.py"), "compile",
        "--authoring", str(path), "--output", str(output)], text=True, encoding="utf-8", capture_output=True)
    assert result.returncode != 0
    assert "invalid UTF-8 JSON" in result.stderr
    assert output.read_text(encoding="utf-8") == "existing"
    assert str(path) not in result.stderr


def test_inventory_command_accepts_explicit_id_to_path_mapping(tmp_path):
    path, artifact = pdf_artifact(tmp_path)
    s = importlib.import_module("systems.cosmere.rules.schema")
    authoring = {"schema_version": 1, "artifacts": [s.to_dict(artifact)], "packs": [s.to_dict(s.ContentPack(
        "cosmere.core", "1.0.0", "core", (), (artifact.product_id,), ("cosmere",), (), "player", "provisional", ()))]}
    registry_path = tmp_path / "registry.json"
    registry_path.write_bytes(importlib.import_module("systems.cosmere.rules.compiler").compile_registry(authoring))
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({artifact.product_id: str(path)}), encoding="utf-8")
    output = tmp_path / "inventory.json"
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "cosmere_rules.py"), "inventory",
        "--mapping", str(mapping_path), "--registry", str(registry_path), "--output", str(output)],
        text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["artifacts"][0]["sha256"] == artifact.sha256
    assert str(path) not in output.read_text(encoding="utf-8")


@pytest.mark.parametrize("target", ["mapping", "registry"])
def test_inventory_command_cannot_overwrite_its_input_documents(tmp_path, target):
    path, artifact = pdf_artifact(tmp_path)
    s = importlib.import_module("systems.cosmere.rules.schema")
    data = {"schema_version": 1, "artifacts": [s.to_dict(artifact)], "packs": [s.to_dict(s.ContentPack(
        "cosmere.core", "1.0.0", "core", (), (artifact.product_id,), ("cosmere",), (), "player", "provisional", ()))]}
    registry = tmp_path / "registry.json"
    registry.write_bytes(importlib.import_module("systems.cosmere.rules.compiler").compile_registry(data))
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({artifact.product_id: str(path)}), encoding="utf-8")
    output = mapping if target == "mapping" else registry
    before = output.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "cosmere_rules.py"), "inventory",
        "--mapping", str(mapping), "--registry", str(registry), "--output", str(output)],
        text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 2
    assert output.read_bytes() == before
