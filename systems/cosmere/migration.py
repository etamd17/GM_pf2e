"""Dry-runable, recoverable migration of Cosmere character JSON documents."""
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile

from .documents import normalize_character_document
from .rules.schema import RulesValidationError, require


class MigrationError(RuntimeError):
    pass


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError("duplicate JSON key in character document")
        result[key] = value
    return result


def _read(path):
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=_strict_pairs)
    except MigrationError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise MigrationError(f"could not read character document {path.name}") from None
    if not isinstance(value, dict):
        raise MigrationError(f"character document {path.name} is not an object")
    return value


def _atomic_writer(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".cosmere-migrate-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _changes(before, after, prefix=""):
    changes = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}.{key}" if prefix else key
        if key not in before:
            changes.append({"path": path, "change": "add"})
        elif key not in after:
            changes.append({"path": path, "change": "remove"})
        elif isinstance(before[key], dict) and isinstance(after[key], dict):
            changes.extend(_changes(before[key], after[key], path))
        elif before[key] != after[key]:
            changes.append({"path": path, "change": "replace"})
    return changes


def _safe_run_id(run_id):
    if run_id is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    if not isinstance(run_id, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", run_id) is None:
        raise MigrationError("invalid migration run ID")
    return run_id


def migrate_character_directory(directory, *, apply=False, binding=None,
                                cosmere_theme=None, run_id=None,
                                writer: Callable | None = None,
                                reader: Callable | None = None,
                                profile=None, homebrew=None):
    """Plan or apply v2 rewrites for direct ``<32hex>.json`` children.

    Apply backs up every original before the first rewrite, validates each
    authoritative read-back, and restores every attempted document if any
    writer/read-back fails. Supplying the host's character writer keeps SQL
    content checksums synchronized with each forward and rollback write.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise MigrationError("Cosmere character directory does not exist")
    write = writer or _atomic_writer
    read = reader or _read
    plans = []
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if not path.is_file() or re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None:
            continue
        original = read(path)
        try:
            normalized = normalize_character_document(
                original, actor_id=path.stem, binding=binding,
                cosmere_theme=cosmere_theme, profile=profile,
                homebrew=homebrew)
        except RulesValidationError as error:
            raise MigrationError(f"{path.name}: {error}") from None
        changes = _changes(original, normalized)
        plans.append({"path": path, "original": deepcopy(original),
                      "normalized": normalized, "changes": changes})
    report = {
        "schema": "cosmere-character-migration-v1",
        "mode": "apply" if apply else "dry-run",
        "scanned": len(plans),
        "changed": sum(bool(plan["changes"]) for plan in plans),
        "backup": None,
        "documents": [{"file": plan["path"].name,
                       "changes": deepcopy(plan["changes"])} for plan in plans],
    }
    changed = [plan for plan in plans if plan["changes"]]
    if not apply or not changed:
        return report

    run_id = _safe_run_id(run_id)
    backup_root = root.parent / "cosmere-character-backups" / run_id
    if backup_root.exists():
        raise MigrationError("migration backup already exists")
    backup_root.mkdir(parents=True)
    for plan in changed:
        (backup_root / plan["path"].name).write_bytes(plan["path"].read_bytes())
    manifest = {"schema": report["schema"], "run_id": run_id,
                "files": [plan["path"].name for plan in changed]}
    _atomic_writer(backup_root / "manifest.json", manifest)
    report["backup"] = str(backup_root.relative_to(root.parent)).replace("\\", "/")

    attempted = []
    try:
        for plan in changed:
            attempted.append(plan)
            write(plan["path"], deepcopy(plan["normalized"]))
            observed = read(plan["path"])
            verified = normalize_character_document(
                observed, actor_id=plan["path"].stem, binding=binding,
                cosmere_theme=cosmere_theme, profile=profile,
                homebrew=homebrew)
            if observed != plan["normalized"] or verified != plan["normalized"]:
                raise MigrationError(f"read-back validation failed for {plan['path'].name}")
    except Exception as error:
        rollback_errors = []
        for plan in reversed(attempted):
            try:
                write(plan["path"], deepcopy(plan["original"]))
                if read(plan["path"]) != plan["original"]:
                    rollback_errors.append(plan["path"].name)
            except Exception:
                rollback_errors.append(plan["path"].name)
        if rollback_errors:
            raise MigrationError("migration failed and rollback requires recovery from backup: "
                                 + ", ".join(sorted(set(rollback_errors)))) from None
        raise MigrationError(f"migration failed and was rolled back: {error}") from None
    return report
