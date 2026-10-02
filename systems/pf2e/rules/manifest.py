"""Canonical bytes and integrity rules shared by compilation and verification."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

from .validation import RulesValidationError, bounded_json, require

COMPILER_VERSION = "pf2e-rules-1"
COMPILER_VERSIONS = {1: COMPILER_VERSION, 2: "pf2e-rules-2"}
MAX_FILE_BYTES = 16 * 1024 * 1024
PACKAGE_FILES = frozenset({"authoring.json", "sources.json", "records.json", "manifest.json"})


def canonical_json(value) -> bytes:
    bounded_json(value)
    try:
        result = (json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        raise RulesValidationError("invalid_json", "$") from None
    require(len(result) <= MAX_FILE_BYTES, "limit_exceeded", "$")
    return result


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(data: bytes):
    require(type(data) is bytes, "invalid_type", "$")
    require(len(data) <= MAX_FILE_BYTES, "limit_exceeded", "$")

    def pairs(items):
        obj = {}
        for key, value in items:
            require(key not in obj, "duplicate_key", "$")
            obj[key] = value
        return obj

    def forbidden(_):
        raise RulesValidationError("invalid_json", "$")

    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=forbidden, parse_float=forbidden)
    except RulesValidationError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise RulesValidationError("invalid_json", "$") from None
    bounded_json(result)
    return result


def reject_links(path: Path, code: str = "invalid_package_layout") -> None:
    """Refuse symlinks and Windows reparse points before filesystem operations."""
    for part in (path.absolute(), *path.absolute().parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, "st_file_attributes", 0)
                         & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)), code, "$files")


def read_file(path: Path) -> bytes:
    reject_links(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "invalid_package_layout", "$files")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        opened = os.fstat(handle.fileno())
        require(stat.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino)
                == (info.st_dev, info.st_ino), "invalid_package_layout", "$files")
        result = handle.read(MAX_FILE_BYTES + 1)
    require(len(result) <= MAX_FILE_BYTES, "limit_exceeded", "$files")
    return result
