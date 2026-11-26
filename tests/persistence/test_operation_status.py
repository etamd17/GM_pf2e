"""PE-14f: the operation-status read and ``operation.abandon`` on SQLite.

The real authorizer (``authorizer.load``) decides who may read; commands run
through the real executor with PE-14d's grant commands and this task's
``held_projections``.  ``runtime.database()`` is PE-14b's :class:`Tracked`
wrapper, which fails a test that nests a second transaction.  Campaign ``A``
and ``B`` have db authority at epoch 1, ``N`` has no authority row and ``F``
has file authority at epoch 2.  Concurrency is proved on PostgreSQL in
``test_operation_status_postgresql.py``.
"""

from __future__ import annotations

import inspect
import itertools
import uuid

import pytest
from sqlalchemy import event as sa_event, func, select, update
from sqlalchemy.exc import OperationalError

from core import obsidian_sync, storage
from core.commands import authorizer as auth_module, events, grant_commands, operations
from core.commands.authorizer import held_projections
from core.commands.executor import execute
from core.persistence import runtime, streams
from core.persistence.models import (
    Campaign, CampaignGrant, CampaignMembership, CampaignStateAuthority, CampaignStream,
    Character, CharacterAssignment, CommandReceipt, GameEvent, IntegrationToken,
)
from core.persistence.state_repositories import CharacterStateRepository
from core.request_context import Principal

from .test_command_authorizer import _token, _user
from .test_command_executor import NOW, Tracked, op

A, B, N, F = "a" * 32, "b" * 32, "c" * 32, "f" * 32
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
PIN = {"profile": "pf2e", "content": CONTENT}
UNLOCK = grant_commands.ISSUE_LEVEL_UNLOCK
# token -> its campaign
TOKENS = {"t1": A, "t1r": A, "t2": A, "tb": B, "tbx": B, "tn": N, "tnr": N, "tf": F, "tfr": F}
_SEQ = itertools.count(1)


@pytest.fixture
def db(sqlite_database, monkeypatch):
    _seed(sqlite_database)
    tracked = Tracked(sqlite_database)
    monkeypatch.setattr(runtime, "database", lambda: tracked)
    return tracked


def _seed(database):
    """The four campaigns, users and tokens; shared with the PostgreSQL suite."""

    with database.transaction() as s:
        for uid in ("gm", "gmb", "p1", "p2", "viewer", "party", "qm", "out"):
            _user(s, uid)
        _user(s, "admin", admin=True)
        s.flush()
        for cid in (A, B, N, F):
            s.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e", created_by_user_id="gm",
                           created_at=NOW, settings={"rules": PIN}))
        s.flush()
        for cid in (A, B, N, F):
            s.add(Character(id=cid[0] + "1", campaign_id=cid, system="pf2e", display_name="x",
                            legacy_storage="party_data", legacy_file=cid[0] + "1.json",
                            content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        for cid, epoch in ((A, 1), (B, 1)):
            s.add(CampaignStateAuthority(campaign_id=cid, authority="db", stream_epoch=epoch,
                                         changed_at=NOW))
        s.add(CampaignStateAuthority(campaign_id=F, authority="file", stream_epoch=2,
                                     changed_at=NOW))
        s.add(CampaignMembership(campaign_id=B, user_id="gmb", role="gm"))
        s.add(CampaignMembership(campaign_id=B, user_id="out", role="player"))
        for cid in (A, N, F):
            s.add(CampaignMembership(campaign_id=cid, user_id="gm", role="gm"))
            for uid in ("p1", "p2", "viewer", "party", "qm"):
                s.add(CampaignMembership(campaign_id=cid, user_id=uid, role="player"))
        s.flush()
        for cid in (A, N, F):
            for uid, role in (("p1", "owner"), ("p2", "editor"), ("viewer", "viewer")):
                s.add(CharacterAssignment(campaign_id=cid, character_id=cid[0] + "1",
                                          user_id=uid, role=role))
        s.add(CampaignGrant(grant_id="q" * 32, campaign_id=A, kind="quartermaster",
                            user_id="qm", issued_by_kind="user", issued_by_id="gm",
                            issued_at=NOW))
        for name, cid in TOKENS.items():
            replaces = {"t1r": "t1", "tbx": "t1", "tnr": "tn", "tfr": "tf"}.get(name)
            minter = {"replaces_credential_id": replaces,
                      "minted_by_user_id": "gmb" if cid == B else "gm",
                      "minted_as": "gm"} if replaces else {}
            _token(s, name, cid, [UNLOCK], **minter)
        s.flush()
        states = CharacterStateRepository(s)
        for kind in ("build", "play"):
            states.insert(A, "a1", kind, rules_profile="pf2e", content=CONTENT, payload={})


def who(name):
    if name in TOKENS:
        return Principal.integration_token(name, campaign_id=TOKENS[name])
    return Principal.user(name)


def read(name, original, operation_id, epoch=1, cid=A):
    answer = operations.read_status(campaign_id=cid, reader=who(name), original=original,
                                    operation_id=operation_id, epoch=epoch)
    return answer.status, answer.body


def drop(name, original, operation_id, cid=A):
    answer = operations.abandon(campaign_id=cid, reader=who(name), original=original,
                                operation_id=operation_id)
    return answer.status, answer.body


def add_event(session, cid=A, *, visibility=None, subjects=(), encounter_id=None, **raw):
    """One committed event; ``raw=...`` stores a visibility map as given (no normalising)."""

    event_id = uuid.uuid4().hex
    stored = raw["raw"] if "raw" in raw else events.visibility(visibility or {})
    session.add(GameEvent(event_id=event_id, campaign_id=cid, seq=next(_SEQ), stream_epoch=1,
                          type="test.event", occurred_at=NOW, origin="test",
                          rules_profile="pf2e", subjects=list(subjects),
                          encounter_id=encounter_id, visibility=stored))
    session.flush()
    return event_id


def add_receipt(db, original, operation_id, *, cid=A, status="committed", applied="committed",
                code=None, event_ids=()):
    kind, principal_id = original.split(":", 1)
    with db.database.transaction() as s:
        s.add(CommandReceipt(
            campaign_id=cid, principal_kind=kind, principal_id=principal_id,
            operation_id=operation_id, digest=None if code == "abandoned" else "0" * 64,
            status=status, applied_status=applied if status == "committed" else None,
            code=code, event_ids=list(event_ids), stream_epoch=1,
            **({"seq_first": 1, "seq_last": 1} if status == "committed" else {})))


def set_epoch(db, cid, epoch):
    with db.database.transaction() as s:
        s.execute(update(CampaignStateAuthority).where(
            CampaignStateAuthority.campaign_id == cid).values(stream_epoch=epoch))


def revoke(db, credential_id):
    with db.database.transaction() as s:
        s.execute(update(IntegrationToken).where(
            IntegrationToken.credential_id == credential_id).values(revoked_at=NOW))


def receipts(db, cid=A):
    with db.database.session() as s:
        return s.scalar(select(func.count()).select_from(CommandReceipt)
                        .where(CommandReceipt.campaign_id == cid))


def grant_count(db):
    with db.database.session() as s:
        return s.scalar(select(func.count()).select_from(CampaignGrant)
                        .where(CampaignGrant.kind == "level_unlock"))


def issue(operation_id, principal=Principal.user("gm"), *, cid=A, level=2, epoch=1):
    """PE-14d's ``grant.issue_level_unlock`` for a1 through the real executor."""

    body = {"schema": 1, "operation_id": operation_id, "campaign_id": cid, "epoch": epoch,
            "type": UNLOCK, "subject": {"character_id": cid[0] + "1"}, "expected": [],
            "rules": {"profile": "pf2e"}, "payload": {"target_level": level}}
    answer = execute(body, principal, route_campaign_id=cid, content_descriptor=CONTENT,
                     project=lambda p, records: {f"{k}:{i}": {} for (k, i) in records},
                     held_projections=held_projections)
    return answer.status, (answer.body or {})


def state(body):
    return body.get("state")


# --------------------------------------------------------------------------
# C:308-337, one test per server case


def test_late_arrival_after_absent_pending_commits_once_and_next_read_returns_it(db):
    o = op()
    assert read("gm", "user:gm", o) == (200, {"ok": True, "operation_id": o,
                                               "state": "absent_pending"})
    status, body = issue(o)
    assert status == 200 and not body["replayed"]
    status, answer = read("gm", "user:gm", o)
    assert (status, answer["state"], answer["code"]) == (200, "committed", None)
    assert answer["event_ids"] == [e["event_id"] for e in body["events"]] != []
    assert issue(o)[1]["replayed"] is True
    assert (receipts(db), grant_count(db)) == (1, 1)


@pytest.mark.parametrize("change", ["revoke", "epoch"])
def test_commit_then_revocation_or_epoch_change_returns_the_receipt(db, change):
    o = op()
    assert issue(o, who("t1"))[0] == 200
    revoke(db, "t1") if change == "revoke" else set_epoch(db, A, 2)
    for reader in ("gm", "t1r"):
        status, body = read(reader, "integration_token:t1", o)
        assert (status, state(body)) == (200, "committed"), reader


@pytest.mark.parametrize("stored_epoch", [1, "stale"])
@pytest.mark.parametrize("with_receipt", [True, False])
def test_unauthorized_reader_gets_the_same_404_for_a_user_original(db, stored_epoch,
                                                                    with_receipt):
    o = op()
    if with_receipt:
        add_receipt(db, "user:p1", o)
    if stored_epoch == "stale":
        set_epoch(db, A, 3)
    # Another editor of the same character, and tokens (gm_integration is not the GM).
    for reader in ("p2", "t1", "t1r"):
        assert read(reader, "user:p1", o, epoch=1) == (
            404, {"ok": False, "code": "not_found", "operation_id": o}), reader


@pytest.mark.parametrize("revoked", [False, True])
@pytest.mark.parametrize("with_receipt", [True, False])
def test_unauthorized_reader_gets_the_same_404_for_a_token_original(db, revoked,
                                                                     with_receipt):
    o = op()
    if with_receipt:
        add_receipt(db, "integration_token:t1", o)
    if revoked:
        revoke(db, "t1")
    for reader in ("p2", "p1", "out", "gmb"):
        assert read(reader, "integration_token:t1", o) == (
            404, {"ok": False, "code": "not_found", "operation_id": o}), reader


@pytest.mark.parametrize("epoch", [1, 5])
def test_gm_and_recorded_replacement_after_revocation_read_absent_final(db, epoch):
    o = op()
    assert read("t1r", "integration_token:t1", o) == (
        200, {"ok": True, "operation_id": o, "state": "absent_pending"})
    revoke(db, "t1")
    for reader in ("gm", "admin", "t1r"):
        assert read(reader, "integration_token:t1", o, epoch=epoch) == (
            200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": epoch})


def test_non_replacement_and_foreign_tokens_get_404_without_state(db):
    o = op()
    add_receipt(db, "integration_token:t1", o)
    revoke(db, "t1")
    for reader, cid in (("t2", A), ("tbx", A), ("tbx", B), ("tb", A)):
        status, body = read(reader, "integration_token:t1", o, cid=cid)
        assert status == 404 and "state" not in body and "event_ids" not in body, reader


def test_stale_stored_epoch_with_a_committed_receipt_returns_the_receipt(db):
    o = op()
    assert issue(o)[0] == 200
    set_epoch(db, A, 4)
    status, body = read("gm", "user:gm", o, epoch=1)
    assert (status, state(body)) == (200, "committed")


def test_gm_reads_a_listed_plugin_operation_pending_and_epoch_is_required(db):
    o = op()
    assert read("gm", "integration_token:t1", o) == (
        200, {"ok": True, "operation_id": o, "state": "absent_pending"})
    for epoch in (None, "", "one", "1.5", True, "1e3", " 1"):
        status, body = read("gm", "integration_token:t1", o, epoch=epoch)
        assert (status, body["code"]) == (400, "bad_envelope"), epoch


def test_refusal_receipt_written_after_absent_final_is_returned(db):
    o = op()
    set_epoch(db, A, 2)
    assert state(read("gm", "user:p1", o)[1]) == "absent_final"
    add_receipt(db, "user:p1", o, status="rejected", code="rule_violation")
    status, body = read("gm", "user:p1", o)
    assert (status, body["state"], body["code"], body["event_ids"]) == (
        200, "rejected", "rule_violation", [])
    assert grant_count(db) == 0


def test_pending_applied_status_reads_committed(db):
    o = op()
    add_receipt(db, "user:p1", o, applied="pending")
    assert state(read("p1", "user:p1", o)[1]) == "committed"


def test_original_reads_itself_only_while_still_valid(db):
    o = op()
    add_receipt(db, "user:p1", o)
    assert state(read("p1", "user:p1", o)[1]) == "committed"
    assert state(read("t1", "integration_token:t1", op())[1]) == "absent_pending"
    with db.database.transaction() as s:
        s.execute(CampaignMembership.__table__.delete().where(
            CampaignMembership.user_id == "p1", CampaignMembership.campaign_id == A))
    assert read("p1", "user:p1", o)[0] == 404
    revoke(db, "t1")
    assert read("t1", "integration_token:t1", op())[0] == 404


# --------------------------------------------------------------------------
# Finality without an authority row, and under file authority


def test_revoked_original_no_authority_row_gm_reads_absent_final(db):
    o = op()
    revoke(db, "tn")
    assert read("gm", "integration_token:tn", o, epoch=9, cid=N) == (
        200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": 9})


def test_revoked_original_no_authority_row_replacement_reads_absent_final(db):
    o = op()
    revoke(db, "tn")
    assert read("tnr", "integration_token:tn", o, epoch=3, cid=N) == (
        200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": 3})


def test_valid_original_token_no_authority_row_stays_pending(db):
    for reader, original, epoch in (("gm", "integration_token:tn", 1),
                                    ("tnr", "integration_token:tn", 0),
                                    ("tn", "integration_token:tn", -4),
                                    ("gm", "user:p1", 1)):
        assert state(read(reader, original, op(), epoch=epoch, cid=N)[1]) == "absent_pending"


def test_file_only_revocation_stays_pending(db, tmp_path, monkeypatch):
    """Option A: only SQL ``revoked_at`` is final; a revocation only in tokens.json is not."""

    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    storage.ensure_campaign_dirs(N)
    monkeypatch.setenv("OWNERSHIP_BACKEND", "json")
    _raw, file_only = obsidian_sync.create_token(N, "legacy")  # no SQL row
    assert obsidian_sync.revoke_token(N, file_only["id"])
    doc = obsidian_sync._load_tokens(N)  # a partial revoke: file stamped, SQL row still live
    doc["tokens"].append({"id": "tn", "label": "x", "token_hash": "0" * 64, "last_four": "abcd",
                          "created_at": "2026-11-01T00:00:00Z", "revoked_at": "2026-11-02T00:00:00Z"})
    storage.atomic_write_json(obsidian_sync.tokens_file(N), doc)
    assert all(r["revoked_at"] for r in obsidian_sync._load_tokens(N)["tokens"])
    for name in ("_load_tokens", "_load_tokens_strict", "verify_token"):
        monkeypatch.setattr(obsidian_sync, name, _boom)
    for credential in (file_only["id"], "tn"):
        o = op()
        assert state(read("gm", f"integration_token:{credential}", o, cid=N)[1]) == "absent_pending"
        assert drop("gm", f"integration_token:{credential}", o, cid=N)[1]["state"] == "abandoned"
        assert state(read("gm", f"integration_token:{credential}", o, cid=N)[1]) == "abandoned"


def test_abandon_without_authority_row_then_read_returns_abandoned(db):
    o = op()
    assert drop("gm", "user:p1", o, cid=N) == (200, {
        "ok": True, "operation_id": o, "state": "abandoned", "code": "abandoned",
        "event_ids": []})
    assert state(read("p1", "user:p1", o, cid=N)[1]) == "abandoned"
    with db.database.session() as s:
        row = s.get(CommandReceipt, (N, "user", "p1", o))
    assert (row.stream_epoch, row.digest, row.applied_status) == (1, None, None)


def test_unauthorized_read_with_no_authority_row_is_404(db):
    o = op()
    revoke(db, "tn")
    for reader in ("p2", "out", "tb"):
        assert read(reader, "integration_token:tn", o, cid=N)[0] == 404


def test_revoked_original_authority_file_reads_absent_final(db):
    o = op()
    assert state(read("gm", "integration_token:tf", o, epoch=2, cid=F)[1]) == "absent_pending"
    revoke(db, "tf")
    for reader in ("gm", "tfr"):
        assert read(reader, "integration_token:tf", o, epoch=2, cid=F)[1]["state"] == "absent_final"


def test_stale_epoch_under_file_authority_reads_absent_final(db):
    o = op()
    assert read("gm", "user:p1", o, epoch=1, cid=F) == (
        200, {"ok": True, "operation_id": o, "state": "absent_final", "epoch": 1})
    assert state(read("p1", "user:p1", o, epoch=2, cid=F)[1]) == "absent_pending"


# --------------------------------------------------------------------------
# Abandon (C:299-304)


def test_abandon_inserts_once_and_a_second_abandon_answers_from_it(db):
    o = op()
    first = drop("p1", "user:p1", o)
    assert first == drop("gm", "user:p1", o) == (200, {
        "ok": True, "operation_id": o, "state": "abandoned", "code": "abandoned",
        "event_ids": []})
    assert receipts(db) == 1
    with db.database.session() as s:
        assert s.get(CommandReceipt, (A, "user", "p1", o)).stream_epoch == 1


@pytest.mark.parametrize("abandoner", ["gm", "t1", "t1r"])
def test_late_original_after_abandon_gets_409_abandoned_whatever_its_digest(db, abandoner):
    o = op()
    set_epoch(db, A, 3)
    assert drop(abandoner, "integration_token:t1", o)[1]["state"] == "abandoned"
    with db.database.session() as s:
        assert s.get(CommandReceipt, (A, "integration_token", "t1", o)).stream_epoch == 3
    for level in (2, 3):
        assert issue(o, who("t1"), level=level, epoch=3) == (
            409, {"ok": False, "code": "abandoned", "operation_id": o})
    assert (receipts(db), grant_count(db)) == (1, 0)


def test_late_original_with_no_authority_row_is_503_and_writes_nothing(db):
    o = op()
    assert drop("gm", "integration_token:tn", o, cid=N)[1]["state"] == "abandoned"
    assert issue(o, who("tn"), cid=N) == (
        503, {"ok": False, "code": "authority_unavailable", "operation_id": o})
    assert (receipts(db, N), grant_count(db)) == (1, 0)


def test_abandon_of_a_committed_operation_answers_from_its_receipt(db):
    o = op()
    status, body = issue(o)
    assert status == 200
    assert drop("gm", "user:gm", o) == (200, {
        "ok": True, "operation_id": o, "state": "committed", "code": None,
        "event_ids": [e["event_id"] for e in body["events"]]})
    assert receipts(db) == 1


def test_unauthorized_abandon_is_404_and_writes_nothing(db):
    o = op()
    for reader, cid in (("p2", A), ("t2", A), ("out", N), ("tb", N)):
        assert drop(reader, "user:p1", o, cid=cid)[0] == 404
    assert drop("p2", "integration_token:t1", o)[0] == 404
    with db.database.session() as s:
        assert s.scalar(select(func.count()).select_from(CommandReceipt)) == 0
        # the rolled-back transaction did not even keep N's lazily created stream row
        assert s.get(CampaignStream, N) is None


# --------------------------------------------------------------------------
# Parsing, transactions and the authorization read


@pytest.mark.parametrize("original, operation_id", [
    ("user:p1", "short"), ("user:p1", "x" * 129), ("user:p1", "bad op id!xxxxxxxx"),
    (None, "op-0000000000000001"), ("p1", "op-0000000000000001"),
    ("admin:p1", "op-0000000000000001"), ("user:", "op-0000000000000001"),
    ("user:" + "x" * 129, "op-0000000000000001"), ("user:p\x001", "op-0000000000000001"),
    ("legacy_gm:A", "op-0000000000000001"), (["user:p1"], "op-0000000000000001"),
    ("user:p\ud8001", "op-0000000000000001"),  # a lone surrogate a JSON body can carry
])
def test_malformed_input_is_400_before_any_transaction(db, original, operation_id):
    for answer in (operations.read_status(campaign_id=A, reader=who("gm"), original=original,
                                          operation_id=operation_id, epoch=1),
                   operations.abandon(campaign_id=A, reader=who("gm"), original=original,
                                      operation_id=operation_id)):
        assert (answer.status, answer.body["code"]) == (400, "bad_envelope")
    assert db.transactions == 0


def test_unknown_or_malformed_campaign_is_404(db):
    o = op()
    for cid in ("0" * 32, "", "x" * 65, "a\x00b", "a\udc80b"):
        assert read("gm", "user:gm", o, cid=cid)[0] == 404
        assert drop("gm", "user:gm", o, cid=cid)[0] == 404
    with db.database.session() as s:
        assert s.scalar(select(func.count()).select_from(CampaignStream)) == 0


def test_signatures_take_no_session_and_no_projector():
    def shape(function):
        return [(p.name, p.kind) for p in inspect.signature(function).parameters.values()]

    kw = inspect.Parameter.KEYWORD_ONLY
    assert shape(operations.read_status) == [
        (name, kw) for name in ("campaign_id", "reader", "original", "operation_id", "epoch")]
    assert shape(operations.abandon) == [
        (name, kw) for name in ("campaign_id", "reader", "original", "operation_id")]


def test_authorization_is_loaded_after_the_stream_lock_and_before_the_receipt(db, monkeypatch):
    log, sessions, seen = [], [], {}
    db.on_open.append(lambda session, n: sessions.append(session))
    real_lock, real_load = streams.lock_stream, operations.load
    real_visible = operations.visible_event_ids

    def lock(session, campaign_id):
        log.append("lock")
        return real_lock(session, campaign_id)

    def load(session, principal, campaign_id):
        log.append("load")
        seen["authorizer"] = real_load(session, principal, campaign_id)
        seen["load_session"] = session
        return seen["authorizer"]

    def visible(session, authorizer, campaign_id, event_ids):
        seen["visible"] = (session, authorizer)
        return real_visible(session, authorizer, campaign_id, event_ids)

    def on_cursor(conn, cursor, statement, *args):
        if "command_receipts" in statement:
            log.append("receipt")

    o = op()
    assert issue(o)[0] == 200
    monkeypatch.setattr(streams, "lock_stream", lock)
    monkeypatch.setattr(operations, "load", load)
    monkeypatch.setattr(operations, "visible_event_ids", visible)
    engine = db.database.engine
    sa_event.listen(engine, "before_cursor_execute", on_cursor)
    try:
        transactions = db.transactions
        assert state(read("gm", "user:gm", o)[1]) == "committed"
    finally:
        sa_event.remove(engine, "before_cursor_execute", on_cursor)
    assert db.transactions == transactions + 1
    assert log == ["lock", "load", "receipt"]
    session = sessions[-1]
    assert seen["load_session"] is session
    assert seen["visible"] == (session, seen["authorizer"])
    assert seen["authorizer"]._session is session


def test_flask_g_caches_and_principal_flags_change_nothing(db, monkeypatch):
    from flask import Flask, g

    import core.auth
    import core.campaigns

    def boom(*args, **kwargs):
        raise AssertionError("the status read used a request cache")

    o = op()
    add_receipt(db, "user:p1", o)
    monkeypatch.setattr(core.auth, "_load_users", boom)
    monkeypatch.setattr(core.campaigns, "get_campaign", boom)
    app = Flask(__name__)
    with app.test_request_context("/"):
        setattr(g, core.auth._USERS_MEMO, {"p2": {"id": "p2", "is_admin": True}})
        setattr(g, core.campaigns._CAMPAIGN_MEMO, {A: {"id": A, "gm_user_id": "p2"}})
        g.principal = Principal.user("p2", is_admin=True)
        for reader in (Principal.user("p2"), Principal.user("p2", is_admin=True)):
            for answer in (
                    operations.read_status(campaign_id=A, reader=reader, original="user:p1",
                                           operation_id=o, epoch=1),
                    operations.abandon(campaign_id=A, reader=reader, original="user:p1",
                                       operation_id=o)):
                assert answer.status == 404
    assert receipts(db) == 1


def test_a_lock_timeout_is_503_temporarily_unavailable(db, monkeypatch):
    def timeout(*args, **kwargs):
        raise OperationalError("SELECT", {}, type("E", (Exception,), {"sqlstate": "55P03"})())

    monkeypatch.setattr(streams, "lock_stream", timeout)
    o = op()
    for answer in (read("gm", "user:gm", o), drop("gm", "user:gm", o)):
        assert answer == (503, {"ok": False, "code": "temporarily_unavailable",
                                "operation_id": o})
    assert receipts(db) == 0


def test_missing_event_row_is_server_error(db, caplog):
    o = op()
    with db.database.transaction() as s:
        kept = add_event(s, visibility={"owner": "full"}, subjects=[{"kind": "character",
                                                                     "id": "a1"}])
    add_receipt(db, "user:p1", o, event_ids=[kept, uuid.uuid4().hex])
    assert read("p1", "user:p1", o) == (
        500, {"ok": False, "code": "server_error", "operation_id": o})
    # an event row of another campaign is not this receipt's event either
    with db.database.transaction() as s:
        foreign = add_event(s, B, visibility={"member": "full"})
    o2 = op()
    add_receipt(db, "user:p1", o2, event_ids=[foreign])
    assert read("gm", "user:p1", o2)[0] == 500


def _boom(*args, **kwargs):
    raise AssertionError("the status read touched the token file")


# --------------------------------------------------------------------------
# The website mount (``app.py``); the token mount is in tests/test_obsidian_sync.py


def test_user_routes_pass_only_the_session_identity(db, monkeypatch):
    import app as A_

    o = op()
    add_receipt(db, "user:p1", o)
    calls = []
    real = operations.load

    def load(session, principal, campaign_id):
        calls.append(principal)
        return real(session, principal, campaign_id)

    monkeypatch.setattr(operations, "load", load)
    monkeypatch.setattr(A_._auth, "current_user", lambda: {"id": "p1", "is_admin": True})
    url = f"/api/campaigns/{A}/operations/{o}"
    with A_.app.test_request_context(url + "?principal=user:p1&epoch=1"):
        response, status = A_.api_operation_status(A, o)
    assert (status, response.get_json()["state"]) == (200, "committed")
    with A_.app.test_request_context(url + "/abandon", method="POST",
                                     json={"principal": "user:p1"}):
        response, status = A_.api_operation_abandon(A, o)
    assert (status, response.get_json()["state"]) == (200, "committed")
    assert calls == [Principal.user("p1", is_admin=True)] * 2  # is_admin is ignored by load
    with A_.app.test_request_context(url + "?principal=user:p1"):
        response, status = A_.api_operation_status(A, o)
    assert (status, response.get_json()["code"]) == (400, "bad_envelope")
    monkeypatch.setattr(A_._auth, "current_user", lambda: None)
    with A_.app.test_request_context(url + "?principal=user:p1&epoch=1"):
        response, status = A_.api_operation_status(A, o)
    assert (status, response.get_json()["code"]) == (404, "not_found")
    assert len(calls) == 2
