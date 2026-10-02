"""Verify package integrity and return stable-ID lookups without application I/O."""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

from .ingestion.compile_pack import compile_package
from .manifest import COMPILER_VERSION, PACKAGE_FILES, read_file, read_json, reject_links
from .models import RulePackage, RuleRecord, freeze
from .validation import require, text


def load_package(path: Path, *, expected_hash: str | None = None) -> RulePackage:
    """Expected hash must come from a trusted binding, not the package being read."""
    path = Path(path)
    reject_links(path)
    require(path.is_dir(), "invalid_package_layout", "$files")
    require({p.name for p in path.iterdir()} == PACKAGE_FILES, "invalid_package_layout", "$files")
    files = {name: read_file(path / name) for name in sorted(PACKAGE_FILES)}
    manifest = read_json(files["manifest.json"])
    require(type(manifest) is dict, "invalid_type", "$.manifest")
    require(manifest.get("compiler_version") == COMPILER_VERSION,
            "unsupported_version", "$.manifest.compiler_version")
    authoring = read_json(files["authoring.json"])
    expected = compile_package(authoring)
    require(files == expected, "integrity_mismatch", "$files")
    if expected_hash is not None:
        text(expected_hash, "$binding.package_hash", pattern=r"[0-9a-f]{64}")
        require(expected_hash == manifest["package_hash"], "binding_mismatch", "$binding.package_hash")
    # Read normalized, validated records from the compiler's authoritative result.
    sources = read_json(expected["sources.json"])
    records = read_json(expected["records.json"])
    return RulePackage(freeze(manifest), freeze({s["id"]: s for s in sources}),
                       MappingProxyType({r["id"]: RuleRecord(**freeze(r)) for r in records}))
