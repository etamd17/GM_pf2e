"""M1 ordering guarantees that only PostgreSQL can prove (VTT-ADR-0002 section 5,
acceptance criteria 3 and 4). Run with DATABASE_URL pointing at a disposable
PostgreSQL database: ``pytest -m postgresql tests/vtt/delivery``."""

from __future__ import annotations

import threading
import time

import pytest

from core.persistence import Base, Database
from vtt.delivery.hub import Hub, Principal
from vtt.projection.serializers import TokenKey
from vtt.protocol import ContractRegistry
from vtt.storage import stream
from vtt.storage import writes as W

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
    W.create_scene(db, "camp_a", "scene_a", name="A", published=True)
    W.upsert_entity(db, "camp_a", "scene_a", "token", "t1", {"x": 10, "y": 10})
    try:
        yield db
    finally:
        Base.metadata.drop_all(db.engine)
        db.dispose()


def _hub(db, sent):
    return Hub(db, ContractRegistry(), TokenKey.derive("pg-test"),
               lambda sid, event, payload: sent.append((event, payload)),
               lambda session, principal, audience, campaign_id: True)


def test_a_commit_during_snapshot_construction_is_delivered_exactly_once(pg, monkeypatch):
    sent: list = []
    hub = _hub(pg, sent)
    original = stream.committed_position
    from vtt.delivery import hub as hub_module

    def commit_then_read(session, campaign_id):
        # The projection is already read in this REPEATABLE READ transaction.
        # Another connection now commits a move; the snapshot must not see it
        # and the cursor must not cover it, so replay delivers it exactly once.
        W.upsert_entity(pg, "camp_a", "scene_a", "token", "t1", {"x": 99, "y": 10})
        return original(session, campaign_id)

    monkeypatch.setattr(hub_module, "committed_position", commit_then_read)
    hub.subscribe("sid1", Principal(kind="member", id="user_gm"), "gm", "camp_a", "scene_a")
    snapshot = next(p for e, p in sent if e == "vtt.snapshot")
    patches = [p for e, p in sent if e == "vtt.patch"]
    assert next(x for x in snapshot["entities"] if x["id"] == "t1")["x"] == 10
    assert [[u["x"] for u in p["upserted"]] for p in patches] == [[99]]
    hub.notify("camp_a")                       # a second wake-up delivers nothing new
    assert len([1 for e, _ in sent if e == "vtt.patch"]) == 1


def test_a_later_writer_waits_so_positions_appear_in_commit_order(pg):
    first = pg.session_factory()
    first.begin()
    stream.advance(first, "camp_a", {"kind": "entities", "sceneId": "scene_a", "changed": [["token", "t1"]]})
    base = stream.committed_position(first, "camp_a")       # first holds the stream row lock

    done = threading.Event()
    result: dict = {}

    def second_writer():
        try:
            result["position"] = W.upsert_entity(pg, "camp_a", "scene_a", "token", "t1", {"x": 5, "y": 5})
        except Exception as error:      # e.g. a duplicate position if the lock were missing
            result["error"] = repr(error)
        done.set()

    worker = threading.Thread(target=second_writer)
    worker.start()
    time.sleep(0.5)
    assert not done.is_set(), "the second writer must wait for the first commit"
    # A reader at the pre-existing cursor sees neither position yet.
    assert stream.outbox_after(pg, "camp_a", base - 1, 10) == []
    first.commit()
    first.close()
    worker.join(5)
    assert "error" not in result, result.get("error")
    assert result["position"] == base + 1
    rows = stream.outbox_after(pg, "camp_a", base - 1, 10)
    assert [r.position for r in rows] == [base, base + 1]
