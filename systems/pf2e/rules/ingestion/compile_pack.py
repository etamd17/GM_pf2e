"""Build a reproducible package and publish new IDs without overwriting files."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from ..manifest import COMPILER_VERSIONS, canonical_json, digest, read_json, reject_links
from ..schema import SCHEMA_VERSION, normalize_authoring
from ..validation import RulesValidationError, require


def compile_package(authoring: dict) -> dict[str, bytes]:
    value = normalize_authoring(authoring)
    files = {"authoring.json": canonical_json(value),
             "sources.json": canonical_json(value["sources"]),
             "records.json": canonical_json(value["records"])}
    manifest = {**value["manifest"],
                "compiler_version": COMPILER_VERSIONS[value["manifest"]["schema_version"]],
                "inputs": {"authoring.json": digest(files["authoring.json"])},
                "outputs": {name: digest(files[name]) for name in ("sources.json", "records.json")}}
    # Hash scope excludes only this field; it includes metadata and all other file digests.
    manifest["package_hash"] = digest(canonical_json(manifest))
    files["manifest.json"] = canonical_json(manifest)
    return files


def write_package(authoring: dict, store: Path) -> Path:
    """Publish in an operator-owned store. A stale lock requires operator review."""
    files = compile_package(authoring)
    manifest = read_json(files["manifest.json"])
    require(manifest["publication_status"] != "published"
            or manifest["schema_version"] == SCHEMA_VERSION,
            "legacy_schema_publication", "$.manifest.schema_version")
    # Use the validated snapshot, even if a caller later changes shared input.
    target_name = manifest["ruleset_id"]
    store = Path(store).absolute()
    reject_links(store)
    store.mkdir(parents=True, exist_ok=True)
    target = store / target_name
    require(not os.path.lexists(target), "package_exists", "$.manifest.ruleset_id")
    lock = store / ("." + target_name + ".lock")
    try:
        handle = lock.open("xb")
    except FileExistsError:
        raise RulesValidationError("publication_locked", "$.manifest.ruleset_id") from None
    staging = None
    try:
        with handle:
            require(not os.path.lexists(target), "package_exists", "$.manifest.ruleset_id")
            staging = Path(tempfile.mkdtemp(prefix=".compile-", dir=store))
            for name, data in files.items():
                with (staging / name).open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            require(not os.path.lexists(target), "package_exists", "$.manifest.ruleset_id")
            staging.rename(target)
            staging = None
    finally:
        # Only this invocation's private temporary directory and lock are removed.
        if staging is not None:
            shutil.rmtree(staging)
        lock.unlink()
    return target
