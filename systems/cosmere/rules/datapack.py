"""Build and install private Cosmere rules bundles outside Git.

The archive is never extracted by tarfile: names are checked against a short
allow-list, and each approved regular file is copied into a new staging tree.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import uuid

from .compiler import compile_registry
from .registry import load_registry


MAX_ARCHIVE = 48 * 1024 * 1024
MAX_UNPACKED = 32 * 1024 * 1024
MAX_FILE = 12 * 1024 * 1024
# Each Mistborn release adds three package files; MAX_UNPACKED is the real bound.
MAX_MEMBERS = 64
REQUIRED = frozenset({"rules/authoring.json", "rules/registry.json"})
OPTIONAL = frozenset({"rules/mistborn_coverage.json", "rules/mistborn/manifest.json"})
PACKAGE_FILE = re.compile(r"rules/mistborn/(core|era-1|era-2)-[0-9]+\.[0-9]+\.[0-9]+\.json\Z")
REQUIRED_PACKS = frozenset({"cosmere.core", "cosmere.stormlight.legacy",
                            "cosmere.mistborn.core", "cosmere.mistborn.era-1",
                            "cosmere.mistborn.era-2"})


class BundleError(ValueError):
    pass


def allowed(name: str) -> bool:
    return name in REQUIRED or name in OPTIONAL or bool(PACKAGE_FILE.fullmatch(name))


def _validate_tree(root: Path):
    """Return the install summary and the registry that was verified."""
    rules = root / "rules"
    authoring_path = rules / "authoring.json"
    registry_path = rules / "registry.json"
    if not authoring_path.is_file() or not registry_path.is_file():
        raise BundleError("The bundle needs authoring.json and registry.json.")
    try:
        authoring = json.loads(authoring_path.read_text(encoding="utf-8"))
        compiled = compile_registry(authoring)
        # Git's Windows checkout may have converted the JSON's line ending.
        if compiled != registry_path.read_bytes().replace(b"\r\n", b"\n"):
            raise BundleError("The compiled registry does not match its authoring data.")
        registry = load_registry(registry_path)
    except (ValueError, OSError, UnicodeError) as error:
        if isinstance(error, BundleError):
            raise
        raise BundleError("The rules package failed validation.") from error
    if not REQUIRED_PACKS <= registry.packs.keys():
        raise BundleError("The bundle is missing a required Cosmere or Mistborn package.")
    for path in rules.rglob("*.json"):
        if path in (authoring_path, registry_path):
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeError) as error:
            raise BundleError(f"Invalid JSON in {path.name}.") from error
    return {"packages": len(registry.pack_versions), "records": len(registry.records),
            "hash": registry.package_hash}, registry


def build(data_dir: str | Path, output: str | Path) -> Path:
    """Bundle one already validated private data directory for site-admin upload."""
    data_dir, output = Path(data_dir), Path(output)
    files = sorted(path for path in (data_dir / "rules").rglob("*") if path.is_file())
    names = [path.relative_to(data_dir).as_posix() for path in files]
    if not REQUIRED <= set(names) or any(not allowed(name) for name in names):
        raise BundleError("The data directory has missing or unexpected rules files.")
    _validate_tree(data_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz", format=tarfile.USTAR_FORMAT) as bundle:
        for path, name in zip(files, names):
            if path.is_symlink() or path.stat().st_size > MAX_FILE:
                raise BundleError("A rules file is linked or too large.")
            bundle.add(path, arcname=name, recursive=False)
    if output.stat().st_size > MAX_ARCHIVE:
        output.unlink()
        raise BundleError("The bundle exceeds the upload limit.")
    return output


def install(archive_path: str | Path, dest: str | Path, *, loaded=None) -> dict:
    """Validate an entire bundle, then replace the installed tree with rollback.

    ``loaded`` receives the verified registry once the new tree is live, so the
    caller can serve it without verifying the same files again.
    """
    archive_path, dest = Path(archive_path), Path(dest)
    if archive_path.stat().st_size > MAX_ARCHIVE:
        raise BundleError("The bundle exceeds the upload limit.")
    if dest.is_symlink():
        raise BundleError("The install directory may not be a symlink.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    stage = dest.parent / f".cosmere-stage-{uuid.uuid4().hex}"
    backup = dest.parent / f".cosmere-backup-{uuid.uuid4().hex}"
    stage.mkdir()
    try:
        try:
            bundle = tarfile.open(archive_path, "r:*")
        except (tarfile.TarError, OSError) as error:
            raise BundleError("That is not a Cosmere data bundle.") from error
        with bundle:
            members = []
            for member in bundle:
                members.append(member)
                if len(members) > MAX_MEMBERS:
                    raise BundleError("The bundle has too many files.")
            names = [member.name for member in members]
            if (not REQUIRED <= set(names) or len(names) != len(set(names))
                    or any(not allowed(name) for name in names)):
                raise BundleError("The bundle has missing, duplicate, or unexpected files.")
            if any(not member.isfile() or member.size > MAX_FILE for member in members):
                raise BundleError("The bundle has a linked or oversized entry.")
            if sum(member.size for member in members) > MAX_UNPACKED:
                raise BundleError("The bundle expands beyond the size limit.")
            for member in members:
                source = bundle.extractfile(member)
                if source is None:
                    raise BundleError("The bundle has an unreadable file.")
                target = stage / member.name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
        result, registry = _validate_tree(stage)
        old_exists = dest.exists()
        if old_exists:
            os.replace(dest, backup)
        try:
            os.replace(stage, dest)
        except OSError:
            if old_exists:
                os.replace(backup, dest)
            raise
        if old_exists:
            shutil.rmtree(backup, ignore_errors=True)
        if loaded is not None:
            loaded(registry)
        return result
    finally:
        shutil.rmtree(stage, ignore_errors=True)
