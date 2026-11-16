"""PE-14a on PostgreSQL: the stream lock, seq allocation, watermark reads and the migration.

These run only in CI (``-m postgresql``; Docker is retired locally).  SQLite
treats ``FOR UPDATE`` as a no-op, so every locking claim is proved here.  The
greenlet case runs in a subprocess because ``gevent.monkey.patch_all()`` must
happen before anything else is imported.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from core.persistence import Campaign, CampaignStream, GameEvent, User, streams
from core.persistence.database import Database, normalize_database_url

pytestmark = pytest.mark.postgresql

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
HEAD = "20261115_0005"
PE10 = "20261111_0004"
WAIT = 0.5  # seconds a blocked call must still be blocked


def _postgresql_url():
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL stream tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql"
    return url


def _schema_url(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3D{schema}"


@pytest.fixture
def postgres_schema():
    url = _postgresql_url()
    schema = "gm_pf2e_pe14a_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url, schema
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def database(postgres_schema):
    url, schema = postgres_schema
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        with database.transaction() as session:
            session.add(User(id="gm", username="gm", normalized_username="gm",
                             display_name="gm", password_hash="h", created_at=NOW))
            session.flush()
            for campaign_id in ("A", "B"):
                session.add(Campaign(id=campaign_id, slug=campaign_id, name=campaign_id,
                                     system="pf2e", created_by_user_id="gm",
                                     created_at=NOW))
        yield database
    finally:
        database.dispose()


def _event(seq, campaign_id="A"):
    return GameEvent(event_id=f"{campaign_id}{seq:031x}", campaign_id=campaign_id, seq=seq,
                     stream_epoch=1, type="character.damaged", occurred_at=NOW,
                     origin="website", rules_profile="pf2e")


def _append(database, n, campaign_id="A"):
    """One writer transaction: lock, allocate ``n`` seqs, insert their events."""

    with database.transaction() as session:
        seqs = streams.allocate(streams.lock_stream(session, campaign_id), n)
        session.add_all([_event(seq, campaign_id) for seq in seqs])
    return seqs


def _run_threads(count, target):
    barrier = threading.Barrier(count)
    results, errors = [None] * count, []

    def run(index):
        try:
            barrier.wait(timeout=10)
            results[index] = target(index)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    return results


def _last_seq(database, campaign_id="A"):
    with database.session() as session:
        return session.scalar(select(CampaignStream.last_seq)
                              .where(CampaignStream.campaign_id == campaign_id))


# --------------------------------------------------------------------------
# The lock and allocation


def test_first_time_lock_race_creates_one_row(database):
    # Force the overlap: the first transaction inserts and locks the new row and
    # stays open, so the second's first-time lock_stream meets the uncommitted
    # insert instead of a committed row.
    first, second = database.session_factory(), database.session_factory()
    try:
        first.begin()
        row = streams.lock_stream(first, "A")
        second.begin()
        thread, done, box = _blocked_lock(database, second)
        assert not done.wait(WAIT)
        first.add(_event(streams.allocate(row, 1)[0]))
        first.commit()
        assert done.wait(30)
        thread.join()
        assert "error" not in box
        second.add(_event(streams.allocate(box["row"], 1)[0]))
        second.commit()
    finally:
        first.close()
        second.close()
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(CampaignStream)) == 1
        assert session.scalar(select(func.count()).select_from(GameEvent)) == 2
    assert _last_seq(database) == 2


def test_contending_transactions_allocate_disjoint_contiguous_ranges(database):
    def writer(index):
        return [_append(database, 1 + (index + round) % 3) for round in range(4)]

    ranges = [seqs for result in _run_threads(8, writer) for seqs in result]
    allocated = sorted(seq for seqs in ranges for seq in seqs)
    assert allocated == list(range(1, len(allocated) + 1))
    assert _last_seq(database) == len(allocated)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(GameEvent)) == len(allocated)


def _blocked_lock(database, session, campaign_id="A"):
    """Start ``lock_stream`` in a thread; return (thread, done event, result box)."""

    done, box = threading.Event(), {}

    def run():
        try:
            box["row"] = streams.lock_stream(session, campaign_id)
        except BaseException as exc:  # pragma: no cover - asserted by the caller
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return thread, done, box


def test_lock_stream_reentry_same_campaign(database):
    _append(database, 1)
    session = database.session_factory()
    holder = database.session_factory()
    try:
        # Re-entry returns the held row and never waits, while nobody else holds it.
        with session.begin():
            row = streams.lock_stream(session, "A")
            assert streams.lock_stream(session, "A") is row
            with pytest.raises(streams.StreamLockError):
                streams.lock_stream(session, "B")
        # After commit the next transaction takes the lock again: it waits while a
        # second connection holds the row and proceeds once that one commits.
        holder.begin()
        streams.lock_stream(holder, "A")
        session.begin()
        thread, done, box = _blocked_lock(database, session)
        assert not done.wait(WAIT)
        holder.commit()
        assert done.wait(30)
        thread.join()
        assert "error" not in box
        assert streams.allocate(box["row"], 1) == range(2, 3)
        session.rollback()
        # After rollback, too; and a different campaign is free in the new transaction.
        holder.begin()
        streams.lock_stream(holder, "A")
        session.begin()
        thread, done, box = _blocked_lock(database, session)
        assert not done.wait(WAIT)
        holder.rollback()
        assert done.wait(30)
        thread.join()
        assert "error" not in box
        session.rollback()
        with session.begin():
            streams.lock_stream(session, "B")
    finally:
        session.close()
        holder.close()


# --------------------------------------------------------------------------
# Watermark and replay reads


def test_watermark_matches_events_read_in_one_repeatable_read_snapshot(database):
    _append(database, 3)
    with database.engine.connect() as connection:
        connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with Session(bind=connection) as reader, reader.begin():
            watermark = streams.stream_watermark(reader, "A")
            assert watermark == 3
            # A concurrent writer commits more events after the snapshot was taken.
            assert list(_append(database, 2)) == [4, 5]
            seen = streams.events_after(reader, "A", 0, 100)
            assert [event.seq for event in seen] == [1, 2, 3]
            assert max(event.seq for event in seen) == watermark
            assert streams.stream_watermark(reader, "A") == watermark
    assert _last_seq(database) == 5


# --------------------------------------------------------------------------
# Greenlet contention (C:826-828)

_GREENLET_SCRIPT = textwrap.dedent("""
    from gevent import monkey
    monkey.patch_all()

    import json, sys, time
    import gevent
    from gevent.event import Event
    from datetime import datetime, timezone
    from core.persistence import Campaign, User, streams
    from core.persistence.database import Database

    url, schema, waiters, hold = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    existing = sys.argv[5] == "existing"
    database = Database(url, engine_options={
        "connect_args": {"options": "-csearch_path=" + schema}})
    database.create_schema()
    now = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm", display_name="gm",
                         password_hash="h", created_at=now))
        session.flush()
        session.add(Campaign(id="A", slug="A", name="A", system="pf2e",
                             created_by_user_id="gm", created_at=now))
    if existing:
        # Waiters then block in SELECT ... FOR UPDATE on a committed row (the
        # steady state) rather than on the holder's first-time insert.
        with database.transaction() as session:
            streams.allocate(streams.lock_stream(session, "A"), 1)

    beats, stop, locked = [], Event(), Event()
    out = {"waiters": waiters, "hold": hold, "waiter_results": [], "errors": []}

    def heartbeat():
        while not stop.is_set():
            beats.append(time.monotonic())
            gevent.sleep(0.01)

    def holder():
        with database.transaction() as session:
            out["holder_seqs"] = list(streams.allocate(streams.lock_stream(session, "A"), 1))
            locked.set()
            gevent.sleep(hold)
            out["holder_releasing"] = time.monotonic()

    def waiter(index):
        locked.wait()
        started = time.monotonic()
        try:
            with database.transaction() as session:
                row = streams.lock_stream(session, "A")
                got = time.monotonic()
                seqs = list(streams.allocate(row, 1))
            out["waiter_results"].append({"index": index, "started": started, "locked": got,
                                          "seqs": seqs})
        except Exception as exc:
            out["errors"].append("%s: %s" % (type(exc).__name__, exc))

    beat = gevent.spawn(heartbeat)
    gevent.sleep(0.05)
    jobs = [gevent.spawn(holder)] + [gevent.spawn(waiter, i) for i in range(waiters)]
    gevent.joinall(jobs, timeout=120)
    out["unfinished"] = sum(1 for job in jobs if not job.dead)
    stop.set()
    beat.join()
    out["max_heartbeat_gap"] = max(b - a for a, b in zip(beats, beats[1:]))
    out["pool"] = database.engine.pool.status()
    database.dispose()
    print(json.dumps(out))
""")


def _greenlets(postgres_schema, waiters, hold, row):
    url, schema = postgres_schema
    completed = subprocess.run(
        [sys.executable, "-c", _GREENLET_SCRIPT, url, schema, str(waiters), str(hold), row],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_greenlet_waiting_on_the_stream_lock_does_not_block_the_hub(postgres_schema):
    # First-time path: B blocks on A's uncommitted insert of the stream row.
    out = _greenlets(postgres_schema, waiters=1, hold=1.0, row="new")
    assert out["errors"] == [] and out["unfinished"] == 0, out
    (result,) = out["waiter_results"]
    # B waited for A's commit, the hub kept running meanwhile, and B then proceeded.
    assert result["locked"] - result["started"] >= 0.8, out
    assert result["locked"] >= out["holder_releasing"], out
    assert out["holder_seqs"] == [1] and result["seqs"] == [2], out
    assert out["max_heartbeat_gap"] <= 0.1, out


def test_more_greenlet_waiters_than_the_pool_allows(postgres_schema):
    # QueuePool defaults: 5 connections plus 10 overflow; the holder takes one, so
    # 20 waiters leave 6 queued for a connection rather than for the row lock.
    waiters = 20
    # Steady-state path: the row exists, so every waiter blocks in FOR UPDATE.
    out = _greenlets(postgres_schema, waiters=waiters, hold=1.0, row="existing")
    # Recorded outcome (PE-14a report): every waiter completes, in turn, and the hub
    # keeps ticking while some wait on the pool and the rest on the row lock.
    # A warning, so the numbers reach the CI log even when the test passes.
    warnings.warn("PE-14a pool-exhaustion outcome: " + json.dumps({
        key: out[key] for key in ("errors", "unfinished", "max_heartbeat_gap", "pool")}))
    assert out["errors"] == [] and out["unfinished"] == 0, out
    seqs = sorted(seq for result in out["waiter_results"] for seq in result["seqs"])
    assert out["holder_seqs"] == [2] and seqs == list(range(3, waiters + 3)), out
    assert all(result["locked"] >= out["holder_releasing"]
               for result in out["waiter_results"]), out
    assert out["max_heartbeat_gap"] <= 0.1, out


# --------------------------------------------------------------------------
# Migration


def _alembic(url, *args, check=True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT, env=environment,
        check=check, capture_output=True, text=True, encoding="utf-8")


def test_postgresql_migration_round_trip_and_populated_downgrade(postgres_schema):
    url, schema = postgres_schema
    migrated_url = _schema_url(url, schema)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})

    def tables():
        with engine.connect() as connection:
            return set(connection.execute(text(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = :schema"), {"schema": schema}).scalars())

    try:
        _alembic(migrated_url, "upgrade", PE10)
        before = tables()
        _alembic(migrated_url, "upgrade", HEAD)
        assert tables() - before == {"campaign_streams", "command_receipts", "game_events"}
        for statement, table, refusal in (
            ("INSERT INTO command_receipts (campaign_id, principal_kind, principal_id,"
             " operation_id, digest, status, applied_status, revisions, event_ids,"
             " stream_epoch, created_at) VALUES ('c1', 'user', 'u1', 'op-0000000000000001',"
             " '" + "d" * 64 + "', 'committed', 'committed', '{}', '[]', 1, now())",
             "command_receipts", "1 command receipts exist"),
            ("INSERT INTO game_events (event_id, campaign_id, seq, stream_epoch, type,"
             " occurred_at, origin, rules_profile) VALUES ('" + "e" * 32 + "', 'c1', 1, 1,"
             " 'character.damaged', now(), 'website', 'pf2e')",
             "game_events", "1 game events exist"),
        ):
            with engine.begin() as connection:
                connection.execute(text(statement))
            completed = _alembic(migrated_url, "downgrade", PE10, check=False)
            assert completed.returncode != 0
            assert "downgrade refused: " + refusal in completed.stderr
            with engine.connect() as connection:
                assert connection.execute(text(
                    "SELECT version_num FROM alembic_version")).scalar() == HEAD
            with engine.begin() as connection:
                connection.execute(text(f"DELETE FROM {table}"))
        _alembic(migrated_url, "downgrade", PE10)
        assert tables() == before
        _alembic(migrated_url, "upgrade", HEAD)
        assert tables() - before == {"campaign_streams", "command_receipts", "game_events"}
    finally:
        engine.dispose()
