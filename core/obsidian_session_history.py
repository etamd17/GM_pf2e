"""Read a campaign's legacy Obsidian session history for the backfill (PE-14e).

This is the file half of ``core.commands.session_history``: it lives outside
``core/commands`` because it takes ``core.obsidian_sync.campaign_lock`` and
reads files, and it must run before any transaction (C:455-458).  Imports go
one way: this module imports the types; ``core/commands`` never imports it.

Both files are read as bytes under the campaign lock and parsed afterwards.
``load_runtime`` and ``read_events`` are not used, because they hide decode
errors; the bytes are decoded as strict UTF-8 and parsed with ``json.loads``,
the semantics of ``core.storage.load_json_strict``, so the hash covers exactly
the bytes that were parsed.  A missing file is empty, not partial.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core import obsidian_sync
from core.commands.session_history import LegacyHistory, UnreadableItem, is_legacy_id

RUNTIME, EVENTS = "runtime.json", "events.jsonl"
_IO_ERROR = object()
_JSON_SPACE = b" \t\r\n"


def _read(path: str) -> Any:
    """The file's bytes, ``None`` if it does not exist, or ``_IO_ERROR``."""

    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None
    except OSError:
        return _IO_ERROR


def _parse(raw: bytes) -> Any:
    """Strict UTF-8, then JSON; ``ValueError`` for either (and for absurd nesting)."""

    try:
        return json.loads(raw.decode("utf-8"))
    except RecursionError as exc:
        raise ValueError("nested too deeply") from exc


def _runtime(raw: bytes, ids: set[str], bad: list[UnreadableItem]) -> str | None:
    """Collect ``current_session`` and ``used_session_ids``; return a ``live`` session id."""

    try:
        doc = _parse(raw)
    except ValueError:
        bad.append(UnreadableItem(RUNTIME, None, None, "malformed_json"))
        return None
    if not isinstance(doc, dict):
        bad.append(UnreadableItem(RUNTIME, None, None, "not_object"))
        return None
    live = None
    current = doc.get("current_session")
    if current is not None:
        if not isinstance(current, dict) or not is_legacy_id(current.get("id")):
            bad.append(UnreadableItem(RUNTIME, None, "current_session", "malformed_session_id"))
        elif current.get("status") == "live":
            live = current["id"]
        else:
            ids.add(current["id"])  # main writes awaiting_processing on end
    if "used_session_ids" in doc:  # absent from main's format: not partial (C:499-500)
        used = doc["used_session_ids"]
        if not isinstance(used, list):
            bad.append(UnreadableItem(RUNTIME, None, "used_session_ids", "malformed_session_id"))
            used = []
        for i, value in enumerate(used):
            if is_legacy_id(value):
                ids.add(value)
            else:
                bad.append(UnreadableItem(RUNTIME, None, f"used_session_ids[{i}]",
                                          "malformed_session_id"))
    return live


def _events(raw: bytes, ids: set[str], bad: list[UnreadableItem]) -> None:
    """Every ``session_id`` in ``events.jsonl``; lines are 1-based physical lines."""

    for number, line in enumerate(raw.split(b"\n"), 1):
        if not line.strip(_JSON_SPACE):
            continue  # includes the empty piece after the final newline
        try:
            event = _parse(line)
        except ValueError:  # a decode error or bad JSON, a truncated last line included
            bad.append(UnreadableItem(EVENTS, number, None, "malformed_json"))
            continue
        if not isinstance(event, dict):
            bad.append(UnreadableItem(EVENTS, number, None, "not_object"))
            continue
        session_id = event.get("session_id")
        if session_id is None:
            continue  # main's ordinary event outside a session
        if is_legacy_id(session_id):
            ids.add(session_id)
        else:
            bad.append(UnreadableItem(EVENTS, number, "session_id", "malformed_session_id"))


def read_session_history(campaign_id: str) -> LegacyHistory:
    """Read ``runtime.json`` and ``events.jsonl`` under the campaign lock (no transaction)."""

    with obsidian_sync.campaign_lock(campaign_id):
        files = ((RUNTIME, _read(obsidian_sync.runtime_file(campaign_id))),
                 (EVENTS, _read(obsidian_sync.events_file(campaign_id))))
    digest = hashlib.sha256()
    ids: set[str] = set()
    bad: list[UnreadableItem] = []
    live = None
    for name, raw in files:
        # Each file contributes a tag (missing, io_error or its length), then its bytes.
        if raw is None or raw is _IO_ERROR:
            digest.update(b"missing\n" if raw is None else b"io_error\n")
            if raw is _IO_ERROR:
                bad.append(UnreadableItem(name, None, None, "io_error"))
            continue
        digest.update(b"%d\n" % len(raw))
        digest.update(raw)
        if name == RUNTIME:
            live = _runtime(raw, ids, bad)
        else:
            _events(raw, ids, bad)
    ids.discard(live)  # a live session is reported, never a candidate ended row
    return LegacyHistory(ids=tuple(sorted(ids)), live_session=live, unreadable=tuple(bad),
                         source_sha256=digest.hexdigest())
