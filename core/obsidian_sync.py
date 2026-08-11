"""Durable storage primitives for the Obsidian Session Operations integration.

The website remains the authoritative PF2e state engine.  This module only
owns integration credentials, a monotonic revision ledger, idempotency data,
and the append-only event stream consumed by the vault plugin and the later
post-session workflow.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid

from core import storage


SCHEMA_VERSION = 2
TOKEN_PREFIX = "obs1"
RECENT_COMMAND_LIMIT = 250
PLAYER_REQUEST_LIMIT = 100
REVEAL_HISTORY_LIMIT = 100

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def integration_dir(campaign_id: str) -> str:
    return os.path.join(storage.campaign_dir(campaign_id), "integrations", "obsidian")


def tokens_file(campaign_id: str) -> str:
    return os.path.join(integration_dir(campaign_id), "tokens.json")


def runtime_file(campaign_id: str) -> str:
    return os.path.join(integration_dir(campaign_id), "runtime.json")


def events_file(campaign_id: str) -> str:
    return os.path.join(integration_dir(campaign_id), "events.jsonl")


def campaign_lock(campaign_id: str) -> threading.RLock:
    # campaign_dir validates the id before it can become a lock key.
    storage.campaign_dir(campaign_id)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(campaign_id, threading.RLock())


def _token_digest(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _load_tokens(campaign_id: str) -> dict:
    doc = storage.load_json(tokens_file(campaign_id), {}) or {}
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("schema_version", SCHEMA_VERSION)
    doc.setdefault("tokens", [])
    return doc


def create_token(campaign_id: str, label: str = "Obsidian") -> tuple[str, dict]:
    """Create a campaign-scoped bearer token and return it once.

    Only its SHA-256 digest is persisted.  The token carries 256 bits of random
    secret material, so a fast digest is appropriate here (unlike a password).
    """
    label = (label or "Obsidian").strip()[:80]
    token_id = uuid.uuid4().hex[:12]
    raw_token = f"{TOKEN_PREFIX}_{token_id}_{secrets.token_urlsafe(32)}"
    record = {
        "id": token_id,
        "label": label,
        "token_hash": _token_digest(raw_token),
        "last_four": raw_token[-4:],
        "created_at": utc_now(),
        "last_used_at": None,
        "revoked_at": None,
    }
    with campaign_lock(campaign_id):
        doc = _load_tokens(campaign_id)
        doc["tokens"].append(record)
        storage.atomic_write_json(tokens_file(campaign_id), doc)
    return raw_token, public_token_record(record)


def public_token_record(record: dict) -> dict:
    return {k: record.get(k) for k in (
        "id", "label", "last_four", "created_at", "last_used_at", "revoked_at"
    )}


def list_tokens(campaign_id: str) -> list[dict]:
    with campaign_lock(campaign_id):
        return [public_token_record(r) for r in _load_tokens(campaign_id)["tokens"]]


def verify_token(campaign_id: str, raw_token: str) -> dict | None:
    if not isinstance(raw_token, str) or not raw_token.startswith(TOKEN_PREFIX + "_"):
        return None
    digest = _token_digest(raw_token)
    with campaign_lock(campaign_id):
        doc = _load_tokens(campaign_id)
        matched = None
        for record in doc["tokens"]:
            if record.get("revoked_at"):
                continue
            if hmac.compare_digest(str(record.get("token_hash", "")), digest):
                matched = record
                break
        if matched is None:
            return None
        # Avoid a durable write on every one-second poll.  A five-minute
        # granularity still gives a useful security audit without disk churn.
        now = time.time()
        try:
            prior = time.mktime(time.strptime(
                matched.get("last_used_at") or "1970-01-01T00:00:00Z",
                "%Y-%m-%dT%H:%M:%SZ",
            ))
        except (TypeError, ValueError, OverflowError):
            prior = 0
        if now - prior >= 300:
            matched["last_used_at"] = utc_now()
            storage.atomic_write_json(tokens_file(campaign_id), doc)
        return public_token_record(matched)


def revoke_token(campaign_id: str, token_id: str) -> bool:
    with campaign_lock(campaign_id):
        doc = _load_tokens(campaign_id)
        for record in doc["tokens"]:
            if record.get("id") == token_id and not record.get("revoked_at"):
                record["revoked_at"] = utc_now()
                storage.atomic_write_json(tokens_file(campaign_id), doc)
                return True
    return False


def default_runtime() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "state_hash": None,
        "updated_at": None,
        "event_sequence": 0,
        "current_session": None,
        "active_room": None,
        "player_requests": [],
        "reveal_history": [],
        "recent_commands": {},
        "recent_command_order": [],
    }


def load_runtime(campaign_id: str) -> dict:
    runtime = storage.load_json(runtime_file(campaign_id), {}) or {}
    if not isinstance(runtime, dict):
        runtime = {}
    base = default_runtime()
    base.update(runtime)
    if not isinstance(base.get("recent_commands"), dict):
        base["recent_commands"] = {}
    if not isinstance(base.get("recent_command_order"), list):
        base["recent_command_order"] = []
    if not isinstance(base.get("player_requests"), list):
        base["player_requests"] = []
    if not isinstance(base.get("reveal_history"), list):
        base["reveal_history"] = []
    return base


def save_runtime(campaign_id: str, runtime: dict) -> None:
    runtime["schema_version"] = SCHEMA_VERSION
    runtime["updated_at"] = utc_now()
    storage.atomic_write_json(runtime_file(campaign_id), runtime)


def snapshot_hash(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def remember_command(runtime: dict, command_id: str, response: dict) -> None:
    commands = runtime.setdefault("recent_commands", {})
    order = runtime.setdefault("recent_command_order", [])
    if command_id in commands:
        try:
            order.remove(command_id)
        except ValueError:
            pass
    commands[command_id] = response
    order.append(command_id)
    while len(order) > RECENT_COMMAND_LIMIT:
        expired = order.pop(0)
        commands.pop(expired, None)


def append_player_request(campaign_id: str, request_doc: dict) -> dict:
    """Persist one player-to-GM request for the Obsidian inbox.

    The website's player authentication remains responsible for establishing
    the sender.  This storage seam only assigns durable identifiers and keeps
    the campaign-scoped inbox bounded.
    """
    with campaign_lock(campaign_id):
        runtime = load_runtime(campaign_id)
        item = dict(request_doc or {})
        item.setdefault("id", "request-" + uuid.uuid4().hex[:12])
        item.setdefault("created_at", utc_now())
        item.setdefault("status", "open")
        item.setdefault("resolved_at", None)
        item.setdefault("resolution", None)
        requests = runtime.setdefault("player_requests", [])
        requests.append(item)
        del requests[:-PLAYER_REQUEST_LIMIT]
        runtime["revision"] = int(runtime.get("revision", 0) or 0) + 1
        session = runtime.get("current_session") or {}
        append_event(campaign_id, runtime, {
            "type": "player_request",
            "actor": "website_player",
            "campaign_id": campaign_id,
            "session_id": session.get("id"),
            "revision": runtime["revision"],
            "payload": {
                "request_id": item.get("id"),
                "kind": item.get("kind"),
                "pc_name": item.get("pc_name"),
                "text": item.get("text"),
            },
            "result": {"status": item.get("status")},
        })
        # Force the next state read to establish a hash containing this new
        # runtime-owned object rather than treating it as a website-side drift.
        runtime["state_hash"] = None
        save_runtime(campaign_id, runtime)
        return dict(item)


def remember_reveal(runtime: dict, reveal: dict) -> None:
    history = runtime.setdefault("reveal_history", [])
    history.append(dict(reveal or {}))
    del history[:-REVEAL_HISTORY_LIMIT]


def append_event(campaign_id: str, runtime: dict, event: dict) -> dict:
    """Append an event while the caller holds ``campaign_lock``."""
    os.makedirs(integration_dir(campaign_id), exist_ok=True)
    sequence = int(runtime.get("event_sequence", 0) or 0) + 1
    event = dict(event)
    event.setdefault("schema_version", SCHEMA_VERSION)
    event.setdefault("event_id", uuid.uuid4().hex)
    event.setdefault("occurred_at", utc_now())
    event["sequence"] = sequence
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with open(events_file(campaign_id), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    runtime["event_sequence"] = sequence
    return event


def read_events(campaign_id: str, after: int = 0, limit: int = 250) -> list[dict]:
    limit = max(1, min(int(limit or 250), 1000))
    path = events_file(campaign_id)
    if not os.path.isfile(path):
        return []
    found: list[dict] = []
    with campaign_lock(campaign_id):
        try:
            with open(path, encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if int(event.get("sequence", 0) or 0) > int(after or 0):
                        found.append(event)
                        if len(found) >= limit:
                            break
        except OSError:
            return []
    return found
