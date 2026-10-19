"""Live tracker state for the VTT (parity P3, handoff 069).

The tracker keeps HP, conditions and turn order in app.py's process globals.
The VTT cannot read those at projection time (delivery replays from
PostgreSQL), so app.py hands this module its live indexes and this module
mirrors them into ``vtt_actors``: one row per combatant (``cmb_<instance>``)
and per party character (``chr_<character>``). Only changed rows are written,
in one transaction with one ``actors`` outbox event naming them; actors that
are gone are deleted. Tokens link to an actor through their ``actorId``.

Nothing here decides what the table may see. The record carries the GM's full
view plus ``status``, which app.py computes with ``_npc_hp_status`` (the one
place that policy lives); the table serializer chooses from it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select

from core.persistence.database import Database
from vtt.storage.models import VttActor, VttScene
from vtt.storage.stream import advance, lock_stream

log = logging.getLogger(__name__)

MAX_ACTORS = 64            # per campaign: a party and a large encounter
MAX_CONDITIONS = 24        # per actor
NAME_MAX = 64
CONDITION_NAME_MAX = 40
CONDITION_VALUE_MAX = 99
STATUSES = ("", "Wounded", "Dead")
ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# A sheet link the GM page may open (U-4): a same-origin path only, never a
# scheme, a protocol-relative URL or a backslash.
SHEET_URL = re.compile(r"/(?![/\\])[A-Za-z0-9._~%!$&'()*+,;=:@/?#-]{0,255}")
# Condition names come from the tracker's own vocabulary ('frightened',
# 'off_guard'); the contract keeps them ASCII so a full actor set stays small.
CONDITION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _'-]{0,39}$")
# The contract's Label rule: no C0/C1 controls, bidi overrides/isolates or lone surrogates.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩\ud800-\udfff]")


def actor_id(kind: str, raw: Any) -> str | None:
    """``cmb_<id>`` or ``chr_<id>``, or None when the source id cannot be one."""
    value = f"{kind}_{raw}" if raw else ""
    return value if ID_PATTERN.fullmatch(value) else None


def _text(value: Any, limit: int) -> str:
    return CONTROL.sub("", str(value or ""))[:limit]


def _int(value: Any, low: int, high: int) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(low, min(high, number))


def _conditions(raw: Any) -> list[dict[str, Any]]:
    """[{name, value or None}], sorted, capped. A plain flag has no value."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return out
    for name in sorted(str(k) for k in raw):
        value = raw.get(name)
        if not value or (isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0):
            continue                                      # absent, zero or negative: no condition
        label = name[:CONDITION_NAME_MAX]
        if not CONDITION_NAME.fullmatch(label):
            log.warning("vtt live: condition name %r left out", name[:60])
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            out.append({"name": label, "value": None})
        else:
            out.append({"name": label, "value": _int(value, 0, CONDITION_VALUE_MAX)})
    return out[:MAX_CONDITIONS]


def record(entry: dict[str, Any], *, active: bool) -> dict[str, Any]:
    """The stored record for one live-index entry (app._scene_live_indexes)."""
    pc = bool(entry.get("is_pc"))
    status = entry.get("hp_status") if not pc else ""
    return {
        "name": _text(entry.get("name"), NAME_MAX),
        "pc": pc,
        "hp": _int(entry.get("current_hp"), -9999, 99999),
        "maxHp": _int(entry.get("max_hp"), 0, 99999),
        "conditions": _conditions(entry.get("conditions")),
        "active": bool(active),
        "hidden": entry.get("visible_to_players") is False,
        "status": status if status in STATUSES else "",
        **_placement(entry.get("vtt_hints")),
    }


def _placement(hints: Any) -> dict[str, Any]:
    """Token size in squares (0.25-10, default 1) and a checked sheet link."""
    hints = hints if isinstance(hints, dict) else {}
    try:
        size = float(hints.get("size") or 1)
    except (TypeError, ValueError):
        size = 1.0
    size = 1.0 if size != size else max(0.25, min(10.0, size))
    url = hints.get("sheet_url")
    url = url if isinstance(url, str) and SHEET_URL.fullmatch(url) else None
    return {"size": size, "sheetUrl": url}


def build(combatants: dict[str, dict[str, Any]], characters: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Actor records from app.py's live indexes. A character is active while
    any combatant for that character holds the turn. Beyond MAX_ACTORS the
    rest are left out (combatants first, in tracker order, then characters)."""
    out: dict[str, dict[str, Any]] = {}
    active_characters = {c.get("character_id") for c in combatants.values() if c.get("is_active")}
    for raw_id, entry in combatants.items():
        aid = actor_id("cmb", raw_id)
        if aid:
            out[aid] = record(entry, active=bool(entry.get("is_active")))
    for raw_id, entry in characters.items():
        aid = actor_id("chr", raw_id)
        if aid:
            out[aid] = record(entry, active=raw_id in active_characters)
    if len(out) > MAX_ACTORS:
        log.warning("vtt live: %d actors, keeping the first %d", len(out), MAX_ACTORS)
        out = dict(list(out.items())[:MAX_ACTORS])
    return out


def sync(db: Database, campaign_id: str, actors: dict[str, dict[str, Any]]) -> list[str]:
    """Make ``vtt_actors`` for the campaign equal ``actors``. Returns the ids
    that changed (written or deleted); an empty list wrote nothing and
    recorded no event."""
    with db.transaction() as session:
        # A campaign that has never used the VTT gets no writes at all, not even
        # the stream row lock_stream would insert (it may not exist in SQL).
        used = session.execute(select(VttScene.id).where(VttScene.campaign_id == campaign_id).limit(1)).first()
        if used is None and session.execute(select(VttActor.id).where(
                VttActor.campaign_id == campaign_id).limit(1)).first() is None:
            return []
        # Then the stream lock, as commands and imports take it, before reading.
        lock_stream(session, campaign_id)
        rows = {row.id: row for row in session.execute(
            select(VttActor).where(VttActor.campaign_id == campaign_id)).scalars()}
        changed: list[str] = []
        for aid, data in actors.items():
            row = rows.get(aid)
            if row is None:
                session.add(VttActor(campaign_id=campaign_id, id=aid, data=data))
                changed.append(aid)
            elif row.data != data:
                row.data = data
                changed.append(aid)
        for aid, row in rows.items():
            if aid not in actors:
                session.delete(row)
                changed.append(aid)
        if changed:
            session.flush()
            advance(session, campaign_id, {"kind": "actors", "changed": sorted(changed)})
        return sorted(changed)


def load(session, campaign_id: str) -> dict[str, dict[str, Any]]:
    """Every actor record of a campaign, by id."""
    return {row.id: row.data for row in session.execute(
        select(VttActor).where(VttActor.campaign_id == campaign_id)).scalars()}
