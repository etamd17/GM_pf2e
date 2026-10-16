"""M3 guarantees only PostgreSQL can prove: one code redeems once under
concurrency (criterion 12), a grant is never minted under an epoch being
retired, and a revocation racing a commit never lets a frame follow the
terminal control (criterion 11). Run with DATABASE_URL pointing at a
disposable PostgreSQL database: ``pytest -m postgresql tests/vtt/delivery``."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest
from flask import Flask
from sqlalchemy import func, select

from core.persistence import Base, Database
from vtt.projection.serializers import TokenKey
from vtt.storage import grants as G
from vtt.storage import stream
from vtt.storage import writes as W
from vtt.storage.models import VttTableGrant
from vtt.transport.socket import NAMESPACE, TABLE_COOKIE, init_vtt

from .conftest import CSRF, GRANT_KEY, POSTGRES_URL, FakeHost, seed_identity

pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(not POSTGRES_URL.startswith(("postgresql", "postgres")),
                       reason="needs DATABASE_URL for a disposable PostgreSQL database"),
]
ORIGIN = "https://localhost"


@pytest.fixture
def pg_stack():
    db = Database(POSTGRES_URL)
    Base.metadata.drop_all(db.engine)
    Base.metadata.create_all(db.engine)
    seed_identity(db)
    W.create_scene(db, "camp_a", "scene_a", name="A", published=True)
    W.upsert_entity(db, "camp_a", "scene_a", "token", "t1", {"x": 10, "y": 10})
    W.set_table_pointer(db, "camp_a", "scene_a")
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "pg-m3"
    service = init_vtt(app, db=db, host=FakeHost(), grant_key=GRANT_KEY, token_key=TokenKey.derive("pg-m3"),
                       async_mode="threading", start_heartbeat=False)
    sent: list = []
    real_emit = service.hub.emit

    def recording(sid, event, data):
        real_emit(sid, event, data)
        sent.append((sid, event, data))

    service.hub.emit = recording

    class S:
        pass

    s = S()
    s.db, s.app, s.service, s.sent = db, app, service, sent
    try:
        yield s
    finally:
        Base.metadata.drop_all(db.engine)
        db.dispose()


def issue(s):
    response = s.app.test_client().post("/vtt/api/table-pairing", json={"campaignId": "camp_a"},
                                        headers={"X-CSRF-Token": CSRF}, base_url=ORIGIN)
    return response.get_json()["code"]


def redeem(s, code, addr):
    return s.app.test_client().post("/vtt/api/table-pairing/redeem", json={"code": code},
                                    headers={"Origin": ORIGIN}, base_url=ORIGIN,
                                    environ_base={"REMOTE_ADDR": addr})


def grants(s):
    with s.db.session() as session:
        return session.execute(select(func.count()).select_from(VttTableGrant)).scalar_one()


def test_concurrent_redemptions_of_one_code_issue_exactly_one_grant(pg_stack):
    code = issue(pg_stack)
    statuses: list = [None] * 5
    start = threading.Barrier(5)

    def run(i):
        start.wait()
        statuses[i] = redeem(pg_stack, code, f"10.9.0.{i}").status_code

    threads = [threading.Thread(target=run, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert sorted(statuses) == [200, 400, 400, 400, 400]
    assert grants(pg_stack) == 1


def test_a_grant_redeemed_during_revoke_all_gets_the_new_epoch(pg_stack):
    code = issue(pg_stack)
    revoker = pg_stack.db.session_factory()
    revoker.begin()
    result: dict = {}
    thread = threading.Thread(target=lambda: result.setdefault("r", redeem(pg_stack, code, "10.9.1.1")))
    try:
        G.revoke_all(revoker, "camp_a")             # epoch 1, stream row locked, not committed
        thread.start()
        thread.join(0.5)
        waited = thread.is_alive()                  # the redemption waits on the stream lock
        revoker.commit()
    finally:
        revoker.close()                             # never leave the lock held for teardown
    thread.join(10)
    assert waited
    assert result["r"].status_code == 200
    with pg_stack.db.session() as session:
        grant = session.execute(select(VttTableGrant)).scalar_one()
        assert grant.epoch == 1
        assert G.grant_status(session, grant, datetime.now(timezone.utc)) == "ok"


def test_no_frame_follows_the_revoked_control_when_revocation_races_commits(pg_stack):
    s = pg_stack
    for round_ in range(10):
        s.sent.clear()
        response = redeem(s, issue(s), f"10.9.2.{round_}")
        credential = response.headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        flask_client = s.app.test_client()
        flask_client.set_cookie(TABLE_COOKIE, credential)
        table = s.service.socketio.test_client(s.app, namespace=NAMESPACE, flask_test_client=flask_client)
        table.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "table"}, namespace=NAMESPACE)
        table_sid = next(sid for sid, e, _ in s.sent if e == "vtt.snapshot")
        go = threading.Barrier(2)

        def mover():
            go.wait()
            for x in range(20):
                W.upsert_entity(s.db, "camp_a", "scene_a", "token", "t1", {"x": 20 + x + round_, "y": 10})
                s.service.hub.notify("camp_a")

        def revoker():
            go.wait()
            with s.db.transaction() as session:
                G.revoke_all(session, "camp_a")
            s.service.hub.notify("camp_a")

        threads = [threading.Thread(target=mover), threading.Thread(target=revoker)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        mine = [(e, d) for sid, e, d in s.sent if sid == table_sid]
        controls = [i for i, (e, d) in enumerate(mine) if e == "vtt.resync_required" and d["reason"] == "revoked"]
        assert len(controls) == 1, mine
        assert all(e != "vtt.patch" for e, _ in mine[controls[0] + 1:])
        assert not table.is_connected(NAMESPACE)


def test_the_heartbeat_sweep_runs_on_postgresql_with_its_timeouts(pg_stack):
    # The sweep sets statement and lock timeouts that only PostgreSQL executes.
    s = pg_stack
    response = redeem(s, issue(s), "10.9.3.1")
    credential = response.headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
    flask_client = s.app.test_client()
    flask_client.set_cookie(TABLE_COOKIE, credential)
    table = s.service.socketio.test_client(s.app, namespace=NAMESPACE, flask_test_client=flask_client)
    assert s.service.heartbeat() == 0 and table.is_connected(NAMESPACE)
    assert s.service.sweep_stats["unverifiable"] == 0
    with s.db.transaction() as session:
        G.revoke_all(session, "camp_a")
    assert s.service.heartbeat() == 1 and not table.is_connected(NAMESPACE)
