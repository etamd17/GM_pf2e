"""Outbox pruning on PostgreSQL (handoffs 077/078): the correlated DELETE keeps
the right rows, and a prune blocked by another transaction's locks fails at
its lock timeout instead of waiting indefinitely. Run with DATABASE_URL
pointing at a disposable PostgreSQL database."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.persistence import Base, Database
from vtt.storage import stream
from vtt.storage import writes as W
from vtt.storage.models import VttOutbox

from .conftest import POSTGRES_URL, seed_identity

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(not POSTGRES_URL.startswith(("postgresql", "postgres")),
                       reason="needs DATABASE_URL for a disposable PostgreSQL database"),
]


@pytest.fixture
def pg():
    db = Database(POSTGRES_URL)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    seed_identity(db)
    try:
        yield db
    finally:
        Base.metadata.drop_all(db.engine)
        db.dispose()


def _positions(db, campaign):
    with db.session() as session:
        return [p for (p,) in session.execute(
            select(VttOutbox.position).where(VttOutbox.campaign_id == campaign).order_by(VttOutbox.position))]


def _age(db):
    with db.transaction() as session:
        session.execute(text("UPDATE vtt_outbox SET created_at = :t"),
                        {"t": datetime.now(timezone.utc) - timedelta(hours=1)})


def test_the_correlated_prune_keeps_each_campaigns_newest_rows(pg):
    W.create_scene(pg, "camp_a", "scene_a", name="A")
    W.create_scene(pg, "camp_b", "scene_b", name="B")
    for x in range(10):
        W.upsert_entity(pg, "camp_a", "scene_a", "token", "tok", {"x": x, "y": 0})
    for x in range(3):
        W.upsert_entity(pg, "camp_b", "scene_b", "token", "tok", {"x": x, "y": 0})
    a, b = _positions(pg, "camp_a"), _positions(pg, "camp_b")
    _age(pg)
    deleted = stream.prune_outbox(pg, keep=4, statement_timeout_ms=5000)
    assert _positions(pg, "camp_a") == [p for p in a if p > a[-1] - 4]
    assert _positions(pg, "camp_b") == [p for p in b if p > b[-1] - 4]
    assert deleted == (len(a) - len(_positions(pg, "camp_a"))) + (len(b) - len(_positions(pg, "camp_b")))


def test_a_prune_blocked_by_locks_fails_at_its_timeout(pg):
    W.create_scene(pg, "camp_a", "scene_a", name="A")
    for x in range(8):
        W.upsert_entity(pg, "camp_a", "scene_a", "token", "tok", {"x": x, "y": 0})
    _age(pg)
    before = _positions(pg, "camp_a")
    holding, release = threading.Event(), threading.Event()

    def hold_rows():
        other = Database(POSTGRES_URL)
        try:
            with other.transaction() as session:
                session.execute(text("SELECT id FROM vtt_outbox FOR UPDATE")).all()
                holding.set()
                release.wait(20)
        finally:
            other.dispose()

    holder = threading.Thread(target=hold_rows, daemon=True)
    holder.start()
    assert holding.wait(10)
    started = time.monotonic()
    try:
        with pytest.raises(DBAPIError):
            stream.prune_outbox(pg, keep=2, statement_timeout_ms=500)
        assert time.monotonic() - started < 5          # bounded, not waiting for the holder
    finally:
        release.set()
        holder.join(10)
    assert _positions(pg, "camp_a") == before          # the failed prune deleted nothing
