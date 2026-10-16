"""M2 guarantees that only PostgreSQL can prove: concurrent receipt keys
(acceptance criterion 7), commit-ordered positions under contention, and the
stream-lock timeout. Run with DATABASE_URL pointing at a disposable PostgreSQL
database: ``pytest -m postgresql tests/vtt/delivery``."""

from __future__ import annotations

import secrets
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from core.persistence import Base, Database
from vtt.commands import CommandService, RateLimiter
from vtt.delivery.hub import Principal
from vtt.projection import serializers as S
from vtt.protocol import ContractRegistry
from vtt.storage import stream
from vtt.storage import writes as W
from vtt.storage.models import VttCommandReceipt, VttEntity, VttOutbox

from .conftest import POSTGRES_URL, seed_identity

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(not POSTGRES_URL.startswith(("postgresql", "postgres")),
                       reason="needs DATABASE_URL for a disposable PostgreSQL database"),
]

KEY = S.TokenKey.derive("pg-m2")
GM = Principal(kind="member", id="user_gm")


@pytest.fixture
def pg():
    db = Database(POSTGRES_URL)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    seed_identity(db)
    W.create_scene(db, "camp_a", "scene_a", name="A", published=True)
    for i in range(8):
        W.upsert_entity(db, "camp_a", "scene_a", "token", f"t{i}", {"x": 10, "y": 10})
    try:
        yield db
    finally:
        Base.metadata.drop_all(db.engine)
        db.dispose()


def service(db, lock_timeout_ms=5000):
    sub = SimpleNamespace(live=True, audience="gm", campaign_id="camp_a", subscription_id="sub_" + "x" * 20)
    hub = SimpleNamespace(subscription=lambda sid: sub)
    return CommandService(db, ContractRegistry(), KEY, hub, lambda session, user, campaign: True,
                          rate_limiter=RateLimiter(rate=1e6, burst=1e6), lock_timeout_ms=lock_timeout_ms)


def version(db, entity_id):
    with db.session() as session:
        return S.gm_entity(KEY, session.get(VttEntity, ("camp_a", "scene_a", "token", entity_id)))["version"]


def move(db, entity_id, x, command_id=None, expected=None):
    return {"schemaVersion": 1, "commandId": command_id or "cmd_" + secrets.token_urlsafe(12),
            "campaignId": "camp_a", "sceneId": "scene_a", "type": "token.move",
            "expected": [{"kind": "token", "id": entity_id, "version": expected or version(db, entity_id)}],
            "payload": {"id": entity_id, "x": x, "y": 10}}


def count(db, model):
    with db.session() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def race(db, svc, messages):
    """Hold the stream lock, start every command (each blocks on it), release, and
    return the acks. All commands contend for the lock at the same moment."""
    holder = db.session_factory()
    holder.begin()
    stream.lock_stream(holder, "camp_a")
    acks: list = [None] * len(messages)
    started = threading.Barrier(len(messages) + 1)

    def run(index, message):
        started.wait()
        acks[index] = svc.handle(f"sid{index}", GM, message).ack

    threads = [threading.Thread(target=run, args=(i, m)) for i, m in enumerate(messages)]
    for thread in threads:
        thread.start()
    started.wait()
    threading.Event().wait(0.3)              # let every command reach the lock
    holder.rollback()
    holder.close()
    for thread in threads:
        thread.join(10)
    return acks


def create(kind):
    command_id = "cmd_" + secrets.token_urlsafe(12)
    if kind == "scene":
        return {"schemaVersion": 1, "commandId": command_id, "campaignId": "camp_a", "sceneId": "scene_new",
                "type": "scene.create", "expected": [],
                "payload": {"name": "New", "width": 64, "height": 64, "gridSize": 8, "showGrid": True,
                            "backgroundAssetId": None}}
    return {"schemaVersion": 1, "commandId": command_id, "campaignId": "camp_a", "sceneId": "scene_a",
            "type": "token.create", "expected": [],
            "payload": {"id": "t_new", "x": 1, "y": 1, "size": 1, "label": "", "color": 0, "artAssetId": None,
                        "actorId": None, "hidden": False, "locked": False, "controllerIds": []}}


# A create locks no existing row, so only the stream lock taken before the
# receipt lookup keeps the second identical command from racing the first.
@pytest.mark.parametrize("make", [lambda db: move(db, "t0", 99), lambda db: create("scene"),
                                  lambda db: create("token")], ids=["move", "scene-create", "token-create"])
def test_two_concurrent_identical_receipt_keys_apply_once_with_the_same_ack(pg, make):
    message = make(pg)
    outbox = count(pg, VttOutbox)
    acks = race(pg, service(pg), [message, dict(message)])
    assert acks[0] == acks[1] == {"schemaVersion": 1, "commandId": message["commandId"], "outcome": "applied"}
    assert count(pg, VttOutbox) == outbox + 1
    assert count(pg, VttCommandReceipt) == 1


def test_a_concurrent_different_digest_on_the_same_key_is_command_id_reused(pg):
    first = move(pg, "t0", 99)
    second = {**first, "payload": {"id": "t0", "x": 55, "y": 10}}
    acks = race(pg, service(pg), [first, second])
    outcomes = sorted(a.get("code", a["outcome"]) for a in acks)
    assert outcomes == ["applied", "command-id-reused"]
    assert count(pg, VttCommandReceipt) == 1


def test_two_moves_from_the_same_version_apply_once_and_the_other_is_stale(pg):
    token = version(pg, "t0")
    acks = race(pg, service(pg), [move(pg, "t0", 50, expected=token), move(pg, "t0", 60, expected=token)])
    assert sorted(a.get("code", a["outcome"]) for a in acks) == ["applied", "stale"]
    assert count(pg, VttCommandReceipt) == 2


def test_concurrent_commands_get_dense_commit_ordered_positions(pg):
    before = count(pg, VttOutbox)
    acks = race(pg, service(pg), [move(pg, f"t{i}", 100 + i) for i in range(8)])
    assert [a["outcome"] for a in acks] == ["applied"] * 8
    with pg.session() as session:
        positions = session.execute(select(VttOutbox.position).order_by(VttOutbox.position)).scalars().all()
    assert positions == list(range(1, before + 9))


def test_a_held_stream_lock_times_out_as_temporarily_unavailable_with_nothing_committed(pg):
    holder = pg.session_factory()
    holder.begin()
    stream.lock_stream(holder, "camp_a")
    svc = service(pg, lock_timeout_ms=200)
    message = move(pg, "t0", 77)
    try:
        ack = svc.handle("sid", GM, message).ack
    finally:
        holder.rollback()
        holder.close()
    assert ack["code"] == "temporarily-unavailable"
    assert count(pg, VttCommandReceipt) == 0
    # the client retries the same commandId once the dependency recovers
    assert svc.handle("sid", GM, message).ack["outcome"] == "applied"
    assert count(pg, VttCommandReceipt) == 1
