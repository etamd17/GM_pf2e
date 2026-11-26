"""PE-14f on PostgreSQL: the status read and abandon under real stream-lock contention.

CI only (``-m postgresql``).  SQLite treats ``FOR UPDATE`` as a no-op, so the
claim that a read observes either the whole of a command commit, revocation or
epoch change or none of it is proved here (C:263-268).  The seed and helpers
are the SQLite suite's; ``runtime.database()`` is the PostgreSQL database
itself (PE-14b's ``Tracked`` wrapper is single-threaded).
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from core.commands import executor, operations
from core.persistence import integration_tokens, runtime, streams
from core.persistence.models import CampaignStateAuthority, CommandReceipt, IntegrationToken
from tests.persistence.test_operation_status import (
    NOW, A, N, _seed, drop, issue, op, read, receipts, state, who,
)
from tests.persistence.test_postgresql_concurrency import postgres_database  # noqa: F401

pytestmark = pytest.mark.postgresql
HOLD = 0.5  # seconds a waiting transaction is given to prove that it waits


@pytest.fixture
def pg(postgres_database, monkeypatch):  # noqa: F811
    _seed(postgres_database)
    for cid in (A, N):  # existing stream rows: the waits are row locks
        with postgres_database.transaction() as session:
            streams.lock_stream(session, cid)
    monkeypatch.setattr(runtime, "database", lambda: postgres_database)
    return SimpleNamespace(database=postgres_database)


def _in_thread(target):
    box = {}

    def body():
        try:
            box["value"] = target()
        except BaseException as exc:  # reported by the caller
            box["error"] = exc
    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread, box


def _done(thread, box):
    thread.join(20)
    assert not thread.is_alive(), "the thread never finished"
    assert "error" not in box, box.get("error")
    return box.get("value")


class _Rollback(Exception):
    pass


def _holder(database, cid, release, acquired, *, write=None, roll_back=False):
    """Another writer: take ``cid``'s stream row, write, wait for ``release``, then end."""

    def target():
        try:
            with database.transaction() as session:
                streams.lock_stream_for_write(session, cid)
                if write is not None:
                    write(session)
                acquired.set()
                assert release.wait(20)
                if roll_back:
                    raise _Rollback()
        except _Rollback:
            return "rolled back"
        return "committed"
    return _in_thread(target)


def _pause_after_lock(monkeypatch):
    """Hold the first read inside its transaction, after the stream lock, until released."""

    inside, release, first = threading.Event(), threading.Event(), []
    real = operations.load

    def load(session, reader, campaign_id):
        if not first:
            first.append(1)
            inside.set()
            assert release.wait(20)
        return real(session, reader, campaign_id)
    monkeypatch.setattr(operations, "load", load)
    return inside, release


def _revoke(cid, credential_id):
    def write(session):
        session.execute(update(IntegrationToken).where(
            IntegrationToken.credential_id == credential_id,
            IntegrationToken.campaign_id == cid).values(revoked_at=NOW))
    return write


def _bump_epoch(cid, epoch):
    def write(session):
        session.execute(update(CampaignStateAuthority).where(
            CampaignStateAuthority.campaign_id == cid).values(stream_epoch=epoch))
    return write


def _receipt(original, operation_id, cid=A):
    kind, principal_id = original.split(":", 1)

    def write(session):
        session.add(CommandReceipt(
            campaign_id=cid, principal_kind=kind, principal_id=principal_id,
            operation_id=operation_id, digest="0" * 64, status="committed",
            applied_status="committed", code=None, event_ids=[], stream_epoch=1,
            seq_first=1, seq_last=1))
        session.flush()
    return write


@pytest.mark.parametrize("outcome", ["committed", "rolled back"])
def test_read_waits_for_a_writer_holding_the_stream_then_sees_its_outcome(pg, outcome):
    o = op()
    acquired, release = threading.Event(), threading.Event()
    writer, writer_box = _holder(pg.database, A, release, acquired,
                                 write=_receipt("user:p1", o),
                                 roll_back=outcome == "rolled back")
    assert acquired.wait(10)
    reader, reader_box = _in_thread(lambda: read("gm", "user:p1", o))
    time.sleep(HOLD)
    assert reader.is_alive(), "the read did not wait for the stream row"
    release.set()
    assert _done(writer, writer_box) == outcome
    status, body = _done(reader, reader_box)
    expected = "committed" if outcome == "committed" else "absent_pending"
    assert (status, body["state"]) == (200, expected)


def test_read_that_locks_first_answers_pending_and_the_command_waits(pg, monkeypatch):
    o = op()
    inside, release = _pause_after_lock(monkeypatch)
    reader, reader_box = _in_thread(lambda: read("gm", "user:gm", o))
    assert inside.wait(10)
    command, command_box = _in_thread(lambda: issue(o))
    time.sleep(HOLD)
    assert command.is_alive(), "the command did not wait for the read's stream lock"
    release.set()
    assert _done(reader, reader_box) == (
        200, {"ok": True, "operation_id": o, "state": "absent_pending"})
    status, body = _done(command, command_box)
    assert status == 200 and not body["replayed"]
    assert state(read("gm", "user:gm", o)[1]) == "committed"


@pytest.mark.parametrize("change", ["revoke", "epoch"])
def test_commit_then_change_injected_mid_read_never_answers_absent_final(
        pg, monkeypatch, change):
    """X commits and then the token is revoked (or the epoch bumped) while the read
    sits between its receipt lookup and its token/epoch read: both wait."""

    o, threads = op(), []
    real = operations._final

    def x_then_change():
        assert issue(o, who("t1"))[0] == 200
        if change == "revoke":
            integration_tokens.revoke_row(A, "t1", None)
        else:
            with pg.database.transaction() as session:
                streams.lock_stream_for_write(session, A)
                _bump_epoch(A, 2)(session)
        return "done"

    def final(*args, **kwargs):
        threads.append(_in_thread(x_then_change))
        time.sleep(HOLD)
        assert threads[0][0].is_alive(), "the commit did not wait for the read's lock"
        return real(*args, **kwargs)
    monkeypatch.setattr(operations, "_final", final)
    assert read("gm", "integration_token:t1", o) == (
        200, {"ok": True, "operation_id": o, "state": "absent_pending"})
    assert _done(*threads[0]) == "done"
    monkeypatch.setattr(operations, "_final", real)
    status, body = read("gm", "integration_token:t1", o)
    assert (status, body["state"]) == (200, "committed")
    assert body["event_ids"]


def test_no_authority_row_read_first_then_revoke_then_final(pg, monkeypatch):
    o = op()
    inside, release = _pause_after_lock(monkeypatch)
    reader, reader_box = _in_thread(
        lambda: read("gm", "integration_token:tn", o, epoch=1, cid=N))
    assert inside.wait(10)
    revoker, revoker_box = _in_thread(lambda: integration_tokens.revoke_row(N, "tn", None))
    time.sleep(HOLD)
    assert revoker.is_alive(), "the revoke did not wait for the read's stream lock"
    release.set()
    assert _done(reader, reader_box) == (
        200, {"ok": True, "operation_id": o, "state": "absent_pending"})
    _done(revoker, revoker_box)
    assert read("gm", "integration_token:tn", o, epoch=1, cid=N) == (
        200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": 1})
    assert read("tnr", "integration_token:tn", o, epoch=1, cid=N)[1]["state"] == "absent_final"


def test_no_authority_row_revoke_first_read_waits_then_final(pg):
    o = op()
    acquired, release = threading.Event(), threading.Event()
    revoker, revoker_box = _holder(pg.database, N, release, acquired,
                                   write=_revoke(N, "tn"))
    assert acquired.wait(10)
    reader, reader_box = _in_thread(
        lambda: read("gm", "integration_token:tn", o, epoch=1, cid=N))
    time.sleep(HOLD)
    assert reader.is_alive(), "the read did not wait for the revoke's stream lock"
    release.set()
    assert _done(revoker, revoker_box) == "committed"
    assert _done(reader, reader_box) == (
        200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": 1})


def test_contended_read_and_abandon_time_out_as_503_writing_nothing(pg, monkeypatch):
    monkeypatch.setattr(executor, "STREAM_LOCK_TIMEOUT_MS", 100)
    o = op()
    acquired, release = threading.Event(), threading.Event()
    holder, holder_box = _holder(pg.database, A, release, acquired)
    assert acquired.wait(10)
    try:
        started = time.monotonic()
        busy = {"ok": False, "code": "temporarily_unavailable", "operation_id": o}
        assert read("gm", "user:p1", o) == (503, busy)
        assert drop("gm", "user:p1", o) == (503, busy)
        assert time.monotonic() - started < 4  # the default 5000 ms would still be waiting
    finally:
        release.set()
    assert _done(holder, holder_box) == "committed"
    assert receipts(pg) == 0
    assert state(read("gm", "user:p1", o)[1]) == "absent_pending"
