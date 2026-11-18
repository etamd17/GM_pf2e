"""PE-14a: stream, receipt and event tables, the ``_0005`` migration and stream access.

Model and stream cases run on SQLite (``FOR UPDATE`` is a no-op there; the
PostgreSQL file proves the locking).  Migration cases drive Alembic in a
subprocess, as the PE-12 schema tests do.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from core.persistence import (
    Campaign,
    CampaignStream,
    Character,
    CommandLocks,
    CommandReceipt,
    GameEvent,
    User,
    streams,
)
from core.persistence.streams import StreamLockError

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
PE14A = "20261115_0005"  # this module's own revision (PE-14c moved the head on)
PE10 = "20261111_0004"
DIGEST = "d" * 64


def _seed(database, *campaigns):
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm", display_name="gm",
                         password_hash="h", created_at=NOW))
        session.flush()
        for campaign_id in campaigns:
            session.add(Campaign(id=campaign_id, slug=campaign_id, name=campaign_id,
                                 system="pf2e", created_by_user_id="gm", created_at=NOW))


def _receipt(operation_id="op-0000000000000001", **fields):
    values = dict(campaign_id="A", principal_kind="user", principal_id="u1",
                  operation_id=operation_id, digest=DIGEST, status="committed",
                  applied_status="committed", code=None, revisions={"character.play:c1": 2},
                  event_ids=["e" * 32], seq_first=1, seq_last=1, stream_epoch=1)
    values.update(fields)
    return CommandReceipt(**values)


def _event(seq=1, *, campaign_id="A", event_id=None, **fields):
    values = dict(event_id=event_id or f"{seq:032x}", campaign_id=campaign_id, seq=seq,
                  stream_epoch=1, type="character.damaged", occurred_at=NOW, origin="website",
                  principal_kind="user", principal_id="u1", rules_profile="pf2e")
    values.update(fields)
    return GameEvent(**values)


def _refused(database, row, match):
    with pytest.raises(IntegrityError) as caught, database.transaction() as session:
        session.add(row)
        session.flush()
    assert re.search(match, str(caught.value.orig)), str(caught.value.orig)


# --------------------------------------------------------------------------
# Tables


def test_receipt_key_and_event_seq_are_unique(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add_all([_receipt(), _receipt(principal_id="u2"),
                         _receipt(principal_kind="integration_token", principal_id="u1"),
                         _receipt(campaign_id="B"), _event(1), _event(1, campaign_id="B",
                                                              event_id="b" * 32)])
    _refused(sqlite_database, _receipt(digest="e" * 64), "UNIQUE constraint failed")
    _refused(sqlite_database, _event(1, event_id="f" * 32), "UNIQUE constraint failed: "
             "game_events.campaign_id, game_events.seq")


def test_receipts_and_events_survive_deleting_their_records(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add(Character(id="c1", campaign_id="A", system="pf2e", display_name="c1",
                              legacy_storage="party_data", legacy_file="c1.json",
                              content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        session.add_all([_receipt(), _event(1), CampaignStream(campaign_id="A", last_seq=1)])
    with sqlite_database.transaction() as session:
        session.delete(session.get(Character, "c1"))
    with sqlite_database.transaction() as session:
        session.delete(session.get(Campaign, "A"))
    with sqlite_database.transaction() as session:
        assert session.scalar(select(CommandReceipt.operation_id)) == "op-0000000000000001"
        assert session.scalar(select(GameEvent.seq)) == 1
        # Only the stream row, a counter, goes with its campaign.
        assert session.scalar(select(CampaignStream.campaign_id)) is None


@pytest.mark.parametrize("fields", [
    {"applied_status": "committed"},
    {"applied_status": "pending"},
    {"seq_first": None, "seq_last": None, "event_ids": []},
    {"status": "rejected", "applied_status": None, "code": "stale_revision",
     "seq_first": None, "seq_last": None, "event_ids": [], "revisions": {}},
    {"status": "rejected", "applied_status": None, "code": "abandoned", "digest": None,
     "seq_first": None, "seq_last": None, "event_ids": [], "revisions": {}},
])
def test_receipt_shapes_that_insert(sqlite_database, fields):
    with sqlite_database.transaction() as session:
        session.add(_receipt(**fields))


_REJECTED = {"status": "rejected", "applied_status": None, "code": "not_found",
             "seq_first": None, "seq_last": None}


@pytest.mark.parametrize("fields, constraint", [
    ({"applied_status": None}, "status_fields"),
    ({"applied_status": "rejected"}, "status_fields"),
    ({"code": "not_found"}, "status_fields"),
    ({**_REJECTED, "applied_status": "committed"}, "status_fields"),
    ({**_REJECTED, "code": None}, "status_fields"),
    ({**_REJECTED, "seq_first": 1, "seq_last": 1}, "status_fields"),
    ({"status": "pending"}, "known_status"),
    ({"principal_kind": "legacy_gm"}, "known_principal_kind"),
    ({"digest": None}, "digest_unless_abandoned"),
    ({**_REJECTED, "digest": None}, "digest_unless_abandoned"),
    ({**_REJECTED, "code": "abandoned"}, "digest_unless_abandoned"),
    ({"digest": "d" * 63}, "digest_unless_abandoned"),
    ({"seq_first": 2, "seq_last": 1}, "seq_range"),
    ({"seq_first": 0, "seq_last": 0}, "seq_range"),
    ({"seq_first": 1, "seq_last": None}, "seq_range"),
    ({"stream_epoch": 0}, "stream_epoch_positive"),
])
def test_receipt_checks_refuse(sqlite_database, fields, constraint):
    _refused(sqlite_database, _receipt(**fields),
             r"CHECK constraint failed: ck_command_receipts_" + constraint + "$")


@pytest.mark.parametrize("fields, constraint", [
    ({"event_id": "e" * 31}, "event_id_length"),
    ({"seq": 0}, "seq_positive"),
    ({"stream_epoch": 0}, "stream_epoch_positive"),
    ({"rules_profile": "cosmere"}, "known_rules_profile"),
    ({"principal_kind": None}, "principal_pair"),
    ({"principal_id": None}, "principal_pair"),
    ({"principal_kind": "publish_token"}, "principal_pair"),
])
def test_event_checks_refuse(sqlite_database, fields, constraint):
    _refused(sqlite_database, _event(**{"seq": 1, **fields}),
             r"CHECK constraint failed: ck_game_events_" + constraint + "$")


def test_event_without_principal_and_with_defaults(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add(_event(1, principal_kind=None, principal_id=None))
    with sqlite_database.transaction() as session:
        row = session.get(GameEvent, f"{1:032x}")
        assert (row.actor, row.subjects, row.changes, row.dice, row.explain,
                row.visibility) == ({}, [], [], [], [], {})


def test_stream_row_check(sqlite_database):
    _seed(sqlite_database, "A")
    _refused(sqlite_database, CampaignStream(campaign_id="A", last_seq=-1),
             r"CHECK constraint failed: ck_campaign_streams_last_seq_nonnegative$")
    _refused(sqlite_database, CampaignStream(campaign_id="missing"), "FOREIGN KEY")


# --------------------------------------------------------------------------
# lock_stream and allocate


class _Statements:
    """Records every SQL statement the engine runs."""

    def __init__(self, engine):
        self.seen = []
        event.listen(engine, "before_cursor_execute", self._record)
        self._engine = engine

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.seen.append(" ".join(statement.split()))

    def count(self, prefix):
        return sum(1 for s in self.seen if s.startswith(prefix) and "campaign_streams" in s)

    def close(self):
        event.remove(self._engine, "before_cursor_execute", self._record)


@pytest.fixture
def spy(sqlite_database):
    statements = _Statements(sqlite_database.engine)
    yield statements
    statements.close()


def test_lock_stream_creates_the_row_and_allocate_is_contiguous(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        stream = streams.lock_stream(session, "A")
        assert stream.last_seq == 0
        assert list(streams.allocate(stream, 3)) == [1, 2, 3]
        assert list(streams.allocate(stream, 3)) == [4, 5, 6]
    with sqlite_database.transaction() as session:
        stream = streams.lock_stream(session, "A")
        assert stream.last_seq == 6 and list(streams.allocate(stream, 1)) == [7]
        assert session.scalar(select(CampaignStream.last_seq)) == 7


def test_lock_stream_after_inserting_the_campaign_in_the_same_transaction(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.add(Campaign(id="new", slug="new", name="new", system="pf2e",
                             created_by_user_id="gm", created_at=NOW))
        session.flush()
        assert list(streams.allocate(streams.lock_stream(session, "new"), 2)) == [1, 2]


def test_lock_stream_reentry_same_campaign(sqlite_database, spy):
    _seed(sqlite_database, "A", "B")
    session = sqlite_database.session_factory()
    try:
        with session.begin():
            first = streams.lock_stream(session, "A")
            inserts, selects = spy.count("INSERT"), spy.count("SELECT")
            assert (inserts, selects) == (1, 1)
            assert streams.lock_stream(session, "A") is first
            assert (spy.count("INSERT"), spy.count("SELECT")) == (inserts, selects)
            with pytest.raises(StreamLockError, match="campaign A"):
                streams.lock_stream(session, "B")
        # A new transaction on the same Session locks again, and may lock another campaign.
        with session.begin():
            again = streams.lock_stream(session, "A")
            assert spy.count("SELECT") == selects + 1 and spy.count("INSERT") == inserts + 1
            assert list(streams.allocate(again, 1)) == [1]
        with session.begin():
            assert streams.lock_stream(session, "B").campaign_id == "B"
        # The same after a rollback.
        session.begin()
        streams.lock_stream(session, "A")
        session.rollback()
        with session.begin():
            before = spy.count("SELECT")
            assert streams.lock_stream(session, "B").campaign_id == "B"
            assert spy.count("SELECT") == before + 1
    finally:
        session.close()


def test_lock_stream_guard_keeps_no_module_state(sqlite_database):
    _seed(sqlite_database, "A")
    mutable = [name for name, value in vars(streams).items()
               if isinstance(value, (dict, list, set)) and not name.startswith("__")]
    assert mutable == []
    with sqlite_database.transaction() as session:
        streams.lock_stream(session, "A")
        assert streams._HELD in session.info


def test_lock_stream_requires_a_transaction_and_refuses_a_first_lock_in_a_savepoint(
        sqlite_database):
    _seed(sqlite_database, "A")
    session = sqlite_database.session_factory()
    try:
        with pytest.raises(StreamLockError, match="active transaction"):
            streams.lock_stream(session, "A")
        with session.begin():
            with session.begin_nested():
                with pytest.raises(StreamLockError, match="SAVEPOINT"):
                    streams.lock_stream(session, "A")
            held = streams.lock_stream(session, "A")
            with session.begin_nested() as savepoint:
                assert streams.lock_stream(session, "A") is held
                streams.allocate(held, 2)
                savepoint.rollback()
            assert list(streams.allocate(held, 1)) == [1]
        for bad in ("", None, 7):
            with session.begin(), pytest.raises(ValueError):
                streams.lock_stream(session, bad)
    finally:
        session.close()


def test_allocate_needs_the_row_this_transaction_holds(sqlite_database):
    _seed(sqlite_database, "A")
    session = sqlite_database.session_factory()
    try:
        with session.begin():
            held = streams.lock_stream(session, "A")
            for n in (0, -1, True, 1.0, None):
                with pytest.raises(ValueError):
                    streams.allocate(held, n)
            with pytest.raises(StreamLockError):
                streams.allocate(CampaignStream(campaign_id="A"), 1)
        with session.begin(), pytest.raises(StreamLockError):
            streams.allocate(held, 1)  # locked by a transaction that has ended
    finally:
        session.close()


def test_lock_stream_conforms_to_command_locks(sqlite_database):
    _seed(sqlite_database, "A", "B")
    with sqlite_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=streams.lock_stream)
        locks.stream()
        assert locks.stream_row.last_seq == 0 and locks.stream_row.campaign_id == "A"
        assert session.scalars(select(CampaignStream.campaign_id)).all() == ["A"]


# --------------------------------------------------------------------------
# Reads for PE-31


def test_stream_watermark_reads_without_locking_or_creating(sqlite_database, spy):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        assert streams.stream_watermark(session, "A") == 0
        assert spy.count("INSERT") == 0 and session.get(CampaignStream, "A") is None
    with sqlite_database.transaction() as session:
        streams.allocate(streams.lock_stream(session, "A"), 4)
    with sqlite_database.transaction() as session:
        assert streams.stream_watermark(session, "A") == 4
        assert streams._HELD not in session.info


def test_events_after_returns_later_events_in_seq_order(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add_all([_event(seq) for seq in (5, 2, 4, 1, 3)])
        session.add_all([_event(seq, campaign_id="B", event_id=f"b{seq:031x}")
                         for seq in (1, 2, 3)])
    with sqlite_database.transaction() as session:
        assert [e.seq for e in streams.events_after(session, "A", 0, 200)] == [1, 2, 3, 4, 5]
        assert [e.seq for e in streams.events_after(session, "A", 2, 2)] == [3, 4]
        assert streams.events_after(session, "A", 5, 10) == []
        assert {e.campaign_id for e in streams.events_after(session, "B", 0, 10)} == {"B"}
        for after_seq, limit in ((-1, 1), (0, 0), (True, 1), (0, 1.5), (None, 1)):
            with pytest.raises(ValueError):
                streams.events_after(session, "A", after_seq, limit)


# --------------------------------------------------------------------------
# Migration


def _alembic(url, *args, check=True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT, env=environment,
        check=check, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture
def sqlite_url(tmp_path):
    return "sqlite+pysqlite:///" + (tmp_path / "migrated.sqlite3").as_posix()


def _tables(url):
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _execute(url, *statements):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def _version(url):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


_RECEIPT_SQL = ("INSERT INTO command_receipts (campaign_id, principal_kind, principal_id,"
                " operation_id, digest, status, applied_status, revisions, event_ids,"
                " stream_epoch, created_at) VALUES ('c1', 'user', 'u1', 'op-0000000000000001',"
                " '" + DIGEST + "', 'committed', 'committed', '{}', '[]', 1, '2026-11-15')")
_EVENT_SQL = ("INSERT INTO game_events (event_id, campaign_id, seq, stream_epoch, type,"
              " occurred_at, origin, rules_profile) VALUES ('" + "e" * 32 + "', 'c1', 1, 1,"
              " 'character.damaged', '2026-11-15', 'website', 'pf2e')")


def test_migration_adds_exactly_three_tables_and_round_trips(sqlite_url):
    _alembic(sqlite_url, "upgrade", PE10)
    before = _tables(sqlite_url)
    _alembic(sqlite_url, "upgrade", PE14A)
    assert _tables(sqlite_url) - before == {"campaign_streams", "command_receipts",
                                            "game_events"}
    assert before <= _tables(sqlite_url) and _version(sqlite_url) == PE14A
    _execute(sqlite_url, "INSERT INTO campaign_streams (campaign_id, last_seq) VALUES ('c1', 0)")
    _alembic(sqlite_url, "downgrade", PE10)  # an empty stream row is only a counter
    assert _tables(sqlite_url) == before and _version(sqlite_url) == PE10
    _alembic(sqlite_url, "upgrade", PE14A)
    assert _version(sqlite_url) == PE14A


@pytest.mark.parametrize("statement, refusal", [
    (_RECEIPT_SQL, "1 command receipts exist"),
    (_EVENT_SQL, "1 game events exist"),
    ("INSERT INTO campaign_state_authority (campaign_id, authority, stream_epoch, changed_at)"
     " VALUES ('c1', 'db', 2, '2026-11-15')", "campaign state authority is not file for c1 (db)"),
    ("INSERT INTO campaign_state_authority (campaign_id, authority, stream_epoch, changed_at)"
     " VALUES ('c1', 'migrating', 2, '2026-11-15')", "campaign state authority is not file for c1 (migrating)"),
    ("INSERT INTO campaign_state_authority (campaign_id, authority, stream_epoch, changed_at)"
     " VALUES ('c1', 'exporting', 2, '2026-11-15')", "campaign state authority is not file for c1 (exporting)"),
])
def test_populated_downgrade_is_refused(sqlite_url, statement, refusal):
    _alembic(sqlite_url, "upgrade", PE14A)
    _execute(sqlite_url, statement)
    completed = _alembic(sqlite_url, "downgrade", PE10, check=False)
    assert completed.returncode != 0
    errors = [line for line in completed.stderr.splitlines() if line.startswith("RuntimeError: ")]
    assert errors and "downgrade refused: " + refusal in errors[-1], completed.stderr
    assert _version(sqlite_url) == PE14A
    assert {"command_receipts", "game_events", "campaign_streams"} <= _tables(sqlite_url)


def test_downgrade_allows_file_authority(sqlite_url):
    _alembic(sqlite_url, "upgrade", PE14A)
    _execute(sqlite_url, "INSERT INTO campaign_state_authority (campaign_id, authority,"
                         " stream_epoch, changed_at) VALUES ('c1', 'file', 2, '2026-11-15')")
    _alembic(sqlite_url, "downgrade", PE10)
    assert _version(sqlite_url) == PE10


def test_offline_downgrade_is_refused(sqlite_url):
    completed = _alembic(sqlite_url, "downgrade", f"{PE14A}:{PE10}", "--sql", check=False)
    assert completed.returncode != 0
    assert "requires an online check for receipts and events" in completed.stderr
