"""PE-14e: the legacy session-history reader and the database half of the backfill.

The reader (``core.obsidian_session_history``) reads ``runtime.json`` and
``events.jsonl`` under the campaign lock before any transaction; the database
half (``core.commands.session_history``) classifies the ids and inserts
``ended`` rows only when the gate holds.  Files live under ``tmp_path``.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import event, func, select

from core import obsidian_session_history, obsidian_sync, storage
from core.commands.folder_form import folder_key
from core.commands.session_history import (
    Collision,
    LegacyHistory,
    UnreadableItem,
    backfill_session_history,
)
from core.obsidian_session_history import read_session_history
from core.persistence import runtime, streams
from core.persistence.models import Campaign, CampaignSession, GameEvent

from .test_command_executor import NOW

CID = "a" * 32
LEGACY = "session-2026-11-08-ab12cd34"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def db(sqlite_database, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    with sqlite_database.transaction() as session:
        session.add(Campaign(id=CID, slug="c", name="c", system="pf2e", created_at=NOW))
    return sqlite_database


def write_runtime(doc):
    path = Path(obsidian_sync.runtime_file(CID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def write_events(data: bytes):
    path = Path(obsidian_sync.events_file(CID))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def lines(*items):
    return b"".join((item if isinstance(item, bytes) else json.dumps(item).encode()) + b"\n"
                    for item in items)


def backfill(db, history=None, accepted=()):
    history = history or read_session_history(CID)
    with db.transaction() as session:
        return backfill_session_history(session, CID, history, frozenset(accepted))


def rows(db):
    with db.session() as session:
        return {row.session_id: (row.status, row.origin, row.started_at, row.ended_at)
                for row in session.scalars(select(CampaignSession).where(
                    CampaignSession.campaign_id == CID))}


def add_row(db, session_id, origin="command", status="ended"):
    with db.transaction() as session:
        session.add(CampaignSession(campaign_id=CID, session_id=session_id, status=status,
                                    origin=origin, started_at=NOW,
                                    ended_at=NOW if status == "ended" else None))


def history(*ids, unreadable=(), live=None):
    return LegacyHistory(ids=tuple(ids), live_session=live, unreadable=tuple(unreadable),
                         source_sha256="0" * 64)


ENDED = ("ended", "file_backfill", None, None)


# --------------------------------------------------------------------------
# reading


def test_main_format_history_with_null_ids_is_complete(db):
    with obsidian_sync.campaign_lock(CID):
        state = obsidian_sync.default_runtime()
        obsidian_sync.append_event(CID, state, {"type": "combat_command", "session_id": None})
        obsidian_sync.save_runtime(CID, state)
    obsidian_sync.append_player_request(CID, {"kind": "question", "text": "Can I?"})
    with obsidian_sync.campaign_lock(CID):
        state = obsidian_sync.load_runtime(CID)
        state["current_session"] = {"id": LEGACY, "label": LEGACY, "status": "live",
                                    "started_at": "2026-11-08T19:00:00Z", "ended_at": None}
        for kind in ("session_start", "combat_command"):
            obsidian_sync.append_event(CID, state, {"type": kind, "session_id": LEGACY})
        state["current_session"].update(status="awaiting_processing",
                                        ended_at="2026-11-08T23:00:00Z")
        obsidian_sync.append_event(CID, state, {"type": "session_end", "session_id": LEGACY})
        obsidian_sync.save_runtime(CID, state)
    events = Path(obsidian_sync.events_file(CID)).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["session_id"] for line in events] == [None, None, LEGACY,
                                                                   LEGACY, LEGACY]
    read = read_session_history(CID)
    assert (read.ids, read.live_session, read.unreadable, read.read_complete) == (
        (LEGACY,), None, (), True)
    report = backfill(db, read)
    assert report.read_complete and report.gate_satisfied
    assert report.inserted == (LEGACY,) and report.quarantined == () and report.collisions == ()
    assert rows(db) == {LEGACY: ENDED}


def test_inserts_ended_rows_from_all_three_sources(db):
    write_runtime({"current_session": {"id": "cur-1", "status": "awaiting_processing"},
                   "used_session_ids": ["used-1", "used-2", "cur-1"]})
    write_events(lines({"session_id": "ev-1"}, {"session_id": "used-1"}))
    report = backfill(db)
    assert report.inserted == ("cur-1", "ev-1", "used-1", "used-2")
    assert rows(db) == dict.fromkeys(report.inserted, ENDED)


def test_missing_files_and_absent_used_session_ids_are_not_partial(db):
    empty = read_session_history(CID)
    assert empty.read_complete and empty.ids == () and empty.live_session is None
    assert empty.source_sha256 == hashlib.sha256(b"missing\nmissing\n").hexdigest()
    write_runtime({"current_session": None, "revision": 3})
    read = read_session_history(CID)
    assert read.read_complete and read.ids == ()
    report = backfill(db, read)
    assert report.gate_satisfied and report.inserted == () and rows(db) == {}


def test_malformed_json_line_and_malformed_session_id_are_distinct(db):
    write_events(lines(
        {"session_id": "ok-1"},  # 1
        b"{not json",  # 2
        {"session_id": 5}, {"session_id": []}, {"session_id": ""},  # 3-5
        {"session_id": "x" * 121},  # 6
        {"type": "ordinary"}, {"session_id": None},  # 7-8: no session, skipped
        b"[1, 2]",  # 9
        b"   ",  # 10: whitespace only
        {"session_id": "y" * 120},  # 11
        {"session_id": "nul\u0000id"},  # 12: SQL cannot store it
        b'{"session_id": "\\ud800lone"}',  # 13: a lone surrogate
    ))
    read = read_session_history(CID)
    assert read.ids == ("ok-1", "y" * 120)
    events = obsidian_session_history.EVENTS
    assert read.unreadable == (
        UnreadableItem(events, 2, None, "malformed_json"),
        *(UnreadableItem(events, n, "session_id", "malformed_session_id")
          for n in (3, 4, 5, 6)),
        UnreadableItem(events, 9, None, "not_object"),
        UnreadableItem(events, 12, "session_id", "malformed_session_id"),
        UnreadableItem(events, 13, "session_id", "malformed_session_id"))
    assert not read.read_complete


def test_invalid_utf8_line_is_unreadable_not_fatal(db):
    write_events(lines({"session_id": "ok-1"}, b'{"session_id": "bad-\xff"}',
                       {"session_id": "ok-2"}))
    read = read_session_history(CID)
    assert read.ids == ("ok-1", "ok-2")
    assert read.unreadable == (UnreadableItem("events.jsonl", 2, None, "malformed_json"),)


def test_truncated_final_line_is_malformed_json(db):
    write_events(lines({"session_id": "ok-1"}) + b'{"session_id": "ok-2"}\r\n'
                 + b'{"session_id": "trunc')
    read = read_session_history(CID)
    assert read.ids == ("ok-1", "ok-2")
    assert read.unreadable == (UnreadableItem("events.jsonl", 3, None, "malformed_json"),)


def test_deeply_nested_line_is_malformed_json_not_a_crash(db):
    write_events(b"[" * 100000 + b"\n" + lines({"session_id": "ok-1"}))
    read = read_session_history(CID)
    assert read.ids == ("ok-1",)
    assert read.unreadable == (UnreadableItem("events.jsonl", 1, None, "malformed_json"),)


@pytest.mark.parametrize("data, reason", [
    (b"[]", "not_object"), (b'"text"', "not_object"), (b"{", "malformed_json"),
    (b'{"current_session": "\xff"}', "malformed_json"), (b"", "malformed_json"),
])
def test_runtime_not_object_is_unreadable(db, data, reason):
    write_runtime({})
    Path(obsidian_sync.runtime_file(CID)).write_bytes(data)
    write_events(lines({"session_id": "ev-1"}))
    read = read_session_history(CID)
    assert read.unreadable == (UnreadableItem("runtime.json", None, None, reason),)
    assert read.ids == ("ev-1",)


def test_malformed_current_session_and_used_session_ids_element(db):
    field = "current_session"
    for current in ("session-1", {"id": 5}, {"status": "live"}, {"id": ""}, [LEGACY]):
        write_runtime({"current_session": current})
        assert read_session_history(CID).unreadable == (
            UnreadableItem("runtime.json", None, field, "malformed_session_id"),), current
    write_runtime({"used_session_ids": "used-1"})
    assert read_session_history(CID).unreadable == (
        UnreadableItem("runtime.json", None, "used_session_ids", "malformed_session_id"),)
    write_runtime({"used_session_ids": ["ok-1", 7, "", "ok-2", None]})
    read = read_session_history(CID)
    assert read.ids == ("ok-1", "ok-2")
    assert read.unreadable == tuple(
        UnreadableItem("runtime.json", None, f"used_session_ids[{i}]", "malformed_session_id")
        for i in (1, 2, 4))


def test_unreadable_file_is_io_error(db, monkeypatch):
    write_events(lines({"session_id": "ev-1"}))
    os.makedirs(obsidian_sync.runtime_file(CID))  # a directory where the file should be
    read = read_session_history(CID)
    assert read.unreadable == (UnreadableItem("runtime.json", None, None, "io_error"),)
    assert read.ids == ("ev-1",)


def test_the_reader_opens_no_database_session(db, monkeypatch):
    write_events(lines({"session_id": "ev-1"}))

    def no_database():
        raise AssertionError("the reader must not touch the database")
    monkeypatch.setattr(runtime, "database", no_database)
    assert read_session_history(CID).ids == ("ev-1",)


def test_source_sha256_changes_with_file_content(db):
    missing = read_session_history(CID).source_sha256
    write_events(b"")
    empty = read_session_history(CID).source_sha256
    write_events(lines({"session_id": "ev-1"}))
    one = read_session_history(CID).source_sha256
    assert len({missing, empty, one}) == 3  # a missing file and an empty one differ
    assert read_session_history(CID).source_sha256 == one
    write_events(lines({"session_id": "ev-1"}, {"session_id": None}))
    assert read_session_history(CID).source_sha256 != one
    write_events(lines({"session_id": "ev-1"}))
    write_runtime({})
    assert read_session_history(CID).source_sha256 != one


def test_reader_lives_outside_core_commands():
    path = Path(obsidian_session_history.__file__).resolve()
    assert path == REPO_ROOT / "core" / "obsidian_session_history.py"


# --------------------------------------------------------------------------
# classification and the gate


def test_rerun_after_reimport_skips_backfilled_ids_without_collisions(db):
    legacy = history("A:b-sess", "a-b-sess", "plain-1")
    first = backfill(db, legacy, [Collision("legacy", ("A:b-sess", "a-b-sess")).key])
    assert first.inserted == ("A:b-sess", "a-b-sess", "plain-1")
    assert first.quarantined == (Collision("legacy", ("A:b-sess", "a-b-sess")),)
    again = backfill(db, legacy)
    assert again.collisions == () and again.unaccepted == () and again.gate_satisfied
    assert again.inserted == () and again.skipped_existing == ("A:b-sess", "a-b-sess", "plain-1")
    assert set(rows(db)) == {"A:b-sess", "a-b-sess", "plain-1"}


def test_file_era_reuse_of_command_row_is_row_collision(db):
    add_row(db, "reused-01", status="active")
    report = backfill(db, history("reused-01", "other-01"))
    collision = Collision("row", ("reused-01",))
    assert report.collisions == (collision,) and report.unaccepted == (collision,)
    assert not report.gate_satisfied and report.inserted == ()
    before = rows(db)
    assert set(before) == {"reused-01"} and before["reused-01"][:2] == ("active", "command")
    # Accepted: the command row stays as it was, the collision is quarantined.
    accepted = backfill(db, history("reused-01", "other-01"), [collision.key])
    assert accepted.quarantined == (collision,) and accepted.gate_satisfied
    assert accepted.inserted == ("other-01",)
    assert rows(db)["reused-01"] == before["reused-01"]
    assert rows(db)["other-01"] == ENDED


def test_folder_collisions_with_rows_and_among_legacy_ids_are_listed(db):
    add_row(db, "s-1-session")
    add_row(db, "file-era", origin="file_backfill")
    report = backfill(db, history("S:1-session", "x:y-legacy", "X-Y-legacy", "File-Era",
                                  "fine-1"))
    assert set(report.collisions) == {
        Collision("row", ("S:1-session", "s-1-session")),
        Collision("row", ("File-Era", "file-era")),
        Collision("legacy", ("X-Y-legacy", "x:y-legacy"))}
    assert report.inserted == () and set(report.unaccepted) == set(report.collisions)
    assert folder_key("x:y-legacy") == folder_key("X-Y-legacy")


def test_partial_history_accepted_satisfies_gate_without_claiming_read(db):
    add_row(db, "s-1-session")
    write_events(lines({"session_id": "ok-1"}, b"{broken", {"session_id": "S:1-session"}))
    read = read_session_history(CID)
    bad, collision = read.unreadable[0], Collision("row", ("S:1-session", "s-1-session"))
    report = backfill(db, read, [bad.key, collision.key])
    assert report.read_complete is False and report.gate_satisfied is True
    assert set(report.quarantined) == {bad, collision} and report.unaccepted == ()
    assert report.inserted == ("S:1-session", "ok-1")
    assert rows(db)["ok-1"] == rows(db)["S:1-session"] == ENDED
    assert bad.key == ("unreadable", "events.jsonl", 2, None)
    assert collision.key == ("collision", "row", ("S:1-session", "s-1-session"))


def test_unaccepted_item_inserts_nothing(db):
    add_row(db, "s-1-session")
    write_events(lines({"session_id": "ok-1"}, b"{broken", {"session_id": "S:1-session"}))
    read = read_session_history(CID)
    collision = Collision("row", ("S:1-session", "s-1-session"))
    for accepted in ([], [collision.key], [read.unreadable[0].key]):
        report = backfill(db, read, accepted)
        assert not report.gate_satisfied and report.inserted == (), accepted
        assert report.unaccepted and set(rows(db)) == {"s-1-session"}


def test_live_current_session_inserts_nothing(db):
    write_runtime({"current_session": {"id": "live-1", "status": "live"},
                   "used_session_ids": ["old-1"]})
    write_events(lines({"session_id": "old-2"}, {"session_id": "live-1"}))
    read = read_session_history(CID)
    assert read.live_session == "live-1" and read.ids == ("old-1", "old-2")
    report = backfill(db, read)
    assert report.live_session == "live-1" and report.gate_satisfied
    assert report.inserted == () and rows(db) == {}


@pytest.mark.parametrize("current, named", [
    ({"id": "nul\u0000id", "status": "live"}, '"nul\\u0000id"'),  # main's writer keeps a NUL
    ({"id": 5, "status": "live"}, "5"),
    ({"id": "x" * 121, "status": "live"}, json.dumps("x" * 121)[:120]),
    ({"status": "live"}, "null"),
], ids=["nul", "number", "121_chars", "no_id"])
def test_live_current_session_with_unusable_id_still_blocks(db, current, named):
    """Implementation review 1, R1-2: accepting the unreadable id must not waive C:665."""
    write_runtime({"current_session": current})
    write_events(lines({"session_id": "old-1"}, {"session_id": "5"}))
    read = read_session_history(CID)
    item = UnreadableItem("runtime.json", None, "current_session", "malformed_session_id")
    assert read.unreadable == (item,) and read.live_session == named
    assert read.ids == ("5", "old-1")  # a rendered name never hides a real candidate id
    report = backfill(db, read, [item.key])
    assert report.gate_satisfied and report.quarantined == (item,)
    assert report.live_session == named and report.inserted == () and rows(db) == {}


def test_history_refuses_ids_sql_cannot_store():
    for bad in ("", "x" * 121, "nul\u0000", "\ud800", 5):
        with pytest.raises(ValueError):
            history(bad)
    with pytest.raises(ValueError):
        history(live="x" * 121)


def test_backfill_does_no_file_io_or_process_lock(db, monkeypatch):
    write_events(lines({"session_id": "ev-1"}, {"session_id": "ev-2"}))
    read = read_session_history(CID)
    order = []
    real_lock = streams.lock_stream

    def spy(session, campaign_id):
        order.append(("lock_stream", campaign_id))
        return real_lock(session, campaign_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("file I/O or a process lock inside the backfill")
    monkeypatch.setattr(streams, "lock_stream", spy)
    with db.transaction() as session:
        event.listen(session, "do_orm_execute", lambda state: order.append("sql"))
        with monkeypatch.context() as patched:
            patched.setattr(builtins, "open", forbidden)
            patched.setattr(os, "open", forbidden)
            patched.setattr(obsidian_sync, "campaign_lock", forbidden)
            report = backfill_session_history(session, CID, read, frozenset())
    assert report.inserted == ("ev-1", "ev-2")
    assert order[0] == ("lock_stream", CID)
    with db.session() as session:
        assert session.scalar(select(func.count()).select_from(GameEvent)) == 0
