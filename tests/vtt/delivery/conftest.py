"""M1 fixtures: an in-memory SQLite store, a minimal Flask app with the /vtt
namespace, a fake host for member login, and helpers to open GM and table
clients through Flask-SocketIO's test client (real handlers, real codec)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from sqlalchemy.pool import StaticPool

from core.persistence import Database
from core.persistence.models import Campaign, User
from vtt.projection.serializers import TokenKey
from vtt.storage import models as vtt_models  # noqa: F401  (registers the VTT tables)
from vtt.storage.models import VttTableGrant
from vtt.transport.socket import NAMESPACE, TABLE_COOKIE, init_vtt, table_verifier

CSRF = "csrf-token-for-tests-000000000000"
GRANT_KEY = b"grant-key-for-tests"


@dataclass
class FakeHost:
    member_id: str | None = "user_gm"
    gms: set = field(default_factory=lambda: {("user_gm", "camp_a")})

    def current_member_id(self):
        return self.member_id

    def csrf_valid(self, token):
        return isinstance(token, str) and secrets.compare_digest(token, CSRF)

    def is_campaign_gm(self, session, user_id, campaign_id):
        return (user_id, campaign_id) in self.gms


def _database(url: str | None = None) -> Database:
    if url:
        return Database(url)
    return Database("sqlite+pysqlite:///:memory:",
                    engine_options={"connect_args": {"check_same_thread": False}, "poolclass": StaticPool})


def seed_identity(db: Database) -> None:
    with db.transaction() as session:
        for uid in ("user_gm", "user_other"):
            session.add(User(id=uid, username=uid, normalized_username=uid, display_name=uid, password_hash="x"))
        for cid in ("camp_a", "camp_b"):
            session.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e"))


def add_grant(db: Database, campaign_id: str = "camp_a", *, expires_in: timedelta = timedelta(hours=8),
              revoked: bool = False) -> str:
    credential = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with db.transaction() as session:
        session.add(VttTableGrant(id="grant_" + secrets.token_hex(8), campaign_id=campaign_id,
                                  verifier=table_verifier(GRANT_KEY, credential), expires_at=now + expires_in,
                                  revoked_at=now if revoked else None))
    return credential


@pytest.fixture
def stack():
    db = _database()
    db.create_schema()
    seed_identity(db)
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    host = FakeHost()
    service = init_vtt(app, db=db, host=host, grant_key=GRANT_KEY, token_key=TokenKey.derive("test-secret"),
                       async_mode="threading")

    class Stack:
        pass

    s = Stack()
    s.db, s.app, s.host, s.service, s.hub = db, app, host, service, service.hub
    # Every server emit, in order, readable even after a connection is closed.
    s.sent = []
    real_emit = service.hub.emit

    def recording_emit(sid, event, data):
        real_emit(sid, event, data)
        s.sent.append((sid, event, data))

    service.hub.emit = recording_emit

    def gm_client(csrf=CSRF):
        return service.socketio.test_client(app, namespace=NAMESPACE, auth={"csrf": csrf})

    def table_client(credential):
        flask_client = app.test_client()
        flask_client.set_cookie(TABLE_COOKIE, credential)
        return service.socketio.test_client(app, namespace=NAMESPACE, flask_test_client=flask_client)

    s.gm_client, s.table_client = gm_client, table_client
    try:
        yield s
    finally:
        db.dispose()


def received(client, event=None):
    out = client.get_received(NAMESPACE)
    return [m for m in out if event is None or m["name"] == event]


def payload(message):
    args = message["args"]
    return args[0] if isinstance(args, list) else args


POSTGRES_URL = os.environ.get("DATABASE_URL", "")
