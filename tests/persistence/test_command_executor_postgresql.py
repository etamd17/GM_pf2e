"""PE-14b on PostgreSQL: the executor under real stream-lock contention.

CI only (``-m postgresql``; Docker is retired locally).  SQLite treats
``FOR UPDATE`` as a no-op, so serialization, the lock timeout and both
transition races with a real contending session are proved here.  The
command types, seed and authorizer are the SQLite suite's.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url

from core.commands import executor
from core.commands.checks import CommandRefusal
from core.persistence import CampaignStateAuthority, CampaignStream, CommandReceipt, GameEvent
from core.persistence import runtime, streams
from core.persistence.database import Database, normalize_database_url
from tests.persistence.test_command_executor import (
    HOOKS,
    LOG,
    SEEN,
    Authorizer,
    _seed,
    damage,
    env,
    run,
)

pytestmark = pytest.mark.postgresql
HOLD = 0.5  # seconds a contending transaction keeps the stream row


@pytest.fixture
def database(monkeypatch):
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL executor tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql"
    schema = "gm_pf2e_pe14b_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        _seed(database)
        with database.transaction() as session:  # an existing stream row: waits are row locks
            streams.lock_stream(session, "A")
        monkeypatch.setattr(runtime, "database", lambda: database)
        monkeypatch.setattr(executor, "authorizer_factory", Authorizer)
        LOG.clear(), HOOKS.clear(), SEEN.clear()
        yield database
    finally:
        HOOKS.clear()
        database.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _counts(database):
    with database.session() as session:
        return (session.scalar(select(func.count()).select_from(CommandReceipt)),
                session.scalar(select(func.count()).select_from(GameEvent)),
                session.scalar(select(CampaignStream.last_seq)
                               .where(CampaignStream.campaign_id == "A")) or 0)


def _in_thread(target):
    box = {}

    def body():
        try:
            box["value"] = target()
        except BaseException as exc:  # reported by the caller
            box["error"] = exc
    thread = threading.Thread(target=body)
    thread.start()
    return thread, box


def _hold_stream(database, acquired, change=None):
    """A contending writer: take the stream row, optionally change authority, hold, commit."""

    def target():
        with database.transaction() as session:
            streams.lock_stream(session, "A")
            if change:
                session.execute(update(CampaignStateAuthority)
                                .where(CampaignStateAuthority.campaign_id == "A")
                                .values(**change))
            acquired.set()
            time.sleep(HOLD)
    return _in_thread(target)


def test_two_commands_serialize_on_the_stream_lock(database):
    inside, release = threading.Event(), threading.Event()
    blocked = env()

    def apply(ctx):
        if ctx.envelope["operation_id"] == blocked["operation_id"]:
            inside.set()
            assert release.wait(10)
        return damage(ctx)
    HOOKS["apply"] = apply
    first, first_box = _in_thread(lambda: run(blocked))
    assert inside.wait(10)
    second, second_box = _in_thread(lambda: run(env(subject="a2")))
    time.sleep(HOLD)
    assert second.is_alive(), "the second command did not wait for the stream row"
    release.set()
    first.join(20), second.join(20)
    assert "error" not in first_box and "error" not in second_box
    assert (first_box["value"].status, second_box["value"].status) == (200, 200)
    assert first_box["value"].body["watermark"] == 1
    assert second_box["value"].body["watermark"] == 2
    with database.session() as session:
        assert list(session.scalars(select(GameEvent.seq).order_by(GameEvent.seq))) == [1, 2]
        ranges = sorted(session.execute(select(CommandReceipt.seq_first,
                                               CommandReceipt.seq_last)).all())
    assert [tuple(r) for r in ranges] == [(1, 1), (2, 2)]


def test_contended_stream_lock_times_out_as_503_writing_nothing(database, monkeypatch):
    monkeypatch.setattr(executor, "STREAM_LOCK_TIMEOUT_MS", 100)
    acquired = threading.Event()
    holder, box = _hold_stream(database, acquired)
    assert acquired.wait(10)
    response = run(env())  # with the default 5000 ms it would wait and answer 200
    holder.join(20)
    assert "error" not in box
    assert (response.status, response.body["code"]) == (503, "temporarily_unavailable")
    assert _counts(database) == (0, 0, 0) and "lock_plan" not in LOG


def test_race_a_authority_flip_while_the_refusal_waits(database, monkeypatch):
    """The refusal transaction waits on a real writer that flips authority, then inserts nothing."""

    acquired, threads = threading.Event(), []
    original = database.transaction
    calls = []

    def transaction():
        calls.append(1)
        if len(calls) == 2:  # the refusal transaction meets the flip in progress
            threads.append(_hold_stream(database, acquired, {"authority": "migrating"}))
            assert acquired.wait(10)
        return original()
    monkeypatch.setattr(database, "transaction", transaction)
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])
    response = run(body)
    threads[0][0].join(20)
    assert "error" not in threads[0][1]
    assert (response.status, response.body["code"]) == (503, "migration_in_progress")
    assert _counts(database)[0] == 0


def test_race_b_old_epoch_retry_waiting_on_an_epoch_bump(database):
    body = env()
    assert run(body).status == 200
    acquired = threading.Event()
    holder, box = _hold_stream(database, acquired, {"stream_epoch": 2})
    assert acquired.wait(10)
    response = run(body)  # waits for the bump to commit, then meets the new epoch
    holder.join(20)
    assert "error" not in box
    assert (response.status, response.body["code"]) == (409, "resync_required")
    assert _counts(database) == (1, 1, 1)


def test_refusal_receipt_and_replay_on_postgresql(database):
    HOOKS["check9"] = lambda ctx: (_ for _ in ()).throw(CommandRefusal("in_encounter"))
    body = env()
    assert run(body).status == 422
    again = run(body)
    assert (again.status, again.body["replayed"]) == (422, True)
    assert _counts(database) == (1, 0, 0)
