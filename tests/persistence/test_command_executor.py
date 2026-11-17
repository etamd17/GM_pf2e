"""PE-14b: the command executor on SQLite.

Every command here is a test-only type registered below; no route calls
``execute``.  ``runtime.database()`` is replaced by a :class:`Tracked`
wrapper that counts transactions and fails the test if anything opens a
second session while the executor's transaction is open.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from core.commands import executor
from core.commands.checks import CheckOrderError, CommandRefusal, run_checks
from core.commands.digest import command_digest
from core.commands.events import EventDraft, NoEventsEmitted, SessionBindError
from core.commands.executor import Applied, execute
from core.commands.registry import CommandType, register
from core.persistence import runtime
from core.persistence.command_locks import LockOrderError
from core.persistence.models import (
    Campaign,
    CampaignMembership,
    CampaignSession,
    CampaignStateAuthority,
    CampaignStream,
    Character,
    CharacterAssignment,
    CommandReceipt,
    GameEvent,
    User,
)
from core.persistence.state_repositories import (
    CampaignPinRepository,
    CharacterStateRepository,
    ProfileMismatch,
    StateRecord,
    StateRowMissing,
)
from core.request_context import Principal, PrincipalKind
from services import session_events

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
PIN = {"profile": "pf2e", "content": CONTENT}
GM = Principal.user("gm")
PLAYER = Principal.user("u1")
TOKEN = Principal.integration_token("cred-1", campaign_id="A")
BOTH = frozenset({PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN})
A1 = [{"kind": "character", "id": "a1"}]

# --------------------------------------------------------------------------
# test-only command types

LOG: list[str] = []
HOOKS: dict = {}
SEEN: dict = {}
HIDDEN: set = set()  # keys the projector omits ("kind:id")


class FakeLocked(Exception):
    """A rules refusal raised from a ``core.commands`` module (answered by attribute)."""

    def __init__(self, code, http_status):
        super().__init__(code)
        self.code, self.http_status = code, http_status


FakeLocked.__module__ = "core.commands.fake_rules"


def same_id(ctx, subject, reference):
    return reference.id == subject.id


class Adapter:
    def check(self, ctx, override):
        LOG.append("check9")
        HOOKS.get("check9", lambda ctx: None)(ctx)

    def build_edition_matches(self, subject, pin):
        return True


def _lock_plan(ctx):
    LOG.append("lock_plan")
    SEEN["lock_plan"] = (ctx.session, ctx.locks, ctx.locks.campaign_id, ctx.pin)
    SEEN["ctx"] = ctx
    if "campaign_id" in ctx.envelope["subject"]:
        cid = ctx.envelope["subject"]["campaign_id"]
        ctx.locks.register_serialized(
            "campaign", cid, StateRecord.serialized("campaign", cid, {}))
    else:
        ids = [ctx.envelope["subject"]["character_id"]]
        target = ctx.envelope["payload"].get("target")
        ctx.locks.character_states([(i, "play") for i in ids + ([target] if target else [])])
    HOOKS.get("lock_plan", lambda ctx: None)(ctx)


def damage(ctx, character_id=None, *, events=1, pending=False):
    character_id = character_id or ctx.envelope["subject"]["character_id"]
    record = ctx.records[("character.play", character_id)]
    amount = ctx.envelope["payload"].get("amount", 1)
    before = record.payload["hp"]
    new = CharacterStateRepository(ctx.session).write(
        ctx.campaign_id, character_id, "play", read_revision=record.revision,
        payload={**record.payload, "hp": before - amount})
    for _ in range(events):
        ctx.emit(EventDraft(
            type="character.damaged", subjects=[{"kind": "character", "id": character_id}],
            changes=[{"kind": "character.play", "id": character_id, "rev": new.revision,
                      "path": "hp", "before": before, "after": before - amount}],
            visibility={"owner": "full", "party": "full"}))
    return Applied(written={("character.play", character_id): new}, pending=pending)


def _apply(ctx):
    LOG.append("apply")
    SEEN["apply"] = (ctx.session, ctx.locks, ctx.locks.campaign_id)
    return HOOKS.get("apply", damage)(ctx)


def _type(name, **fields):
    values = dict(type=name, subject=("character_id", "character.play"),
                  dependencies={"character.play": same_id}, payload_ids={"target": "character.play"},
                  requires_expected=False, derives_rules=False, allowed_principals=BOTH,
                  rules_adapter=Adapter(), lock_plan=_lock_plan, apply=_apply)
    values.update(fields)
    command_type = CommandType(**values)
    register(command_type)
    return name


DAMAGE = _type(f"test.pe14b.damage.{uuid.uuid4().hex}")
USER_ONLY = _type(f"test.pe14b.user_only.{uuid.uuid4().hex}",
                  allowed_principals=frozenset({PrincipalKind.USER}))
CAMPAIGN = _type(f"test.pe14b.campaign.{uuid.uuid4().hex}", subject=("campaign_id", "campaign"),
                 dependencies={}, payload_ids={})


class Authorizer:
    """PE-13b's protocol over memberships and assignments, read from the same session."""

    def __init__(self, session, principal, campaign_id):
        LOG.append("authorizer")
        SEEN["authorizer_session"] = session
        self.campaign_id = campaign_id
        if principal.kind is PrincipalKind.INTEGRATION_TOKEN:
            self.role, self.assigned = "gm", set()
            return
        self.role = session.scalar(select(CampaignMembership.role).where(
            CampaignMembership.campaign_id == campaign_id,
            CampaignMembership.user_id == principal.user_id))
        self.assigned = set(session.scalars(select(CharacterAssignment.character_id).where(
            CharacterAssignment.campaign_id == campaign_id,
            CharacterAssignment.user_id == principal.user_id,
            CharacterAssignment.role.in_(("owner", "editor")))))

    def campaign_role_allows(self, type):
        LOG.append("check4")
        return self.role is not None

    def override_allowed(self, type):
        return self.role == "gm"

    def visible(self, record):
        return (self.role == "gm" or not record.kind.startswith("character.")
                or record.id in self.assigned)

    def subject_role_allows(self, type, subject):
        return self.role == "gm" or subject.id in self.assigned


def project(principal, records):
    return {f"{kind}:{id}": {"rev": record.revision, **record.payload}
            for (kind, id), record in records.items() if f"{kind}:{id}" not in HIDDEN}


# --------------------------------------------------------------------------
# database


class Tracked:
    """``runtime.database()`` for the tests: counts transactions, forbids nesting."""

    def __init__(self, database):
        self.database = database
        self.transactions = 0
        self.open = False
        self.on_open = []  # callbacks(session, n) run when transaction n opens
        self.before = {}  # n -> callback run before transaction n opens

    def transaction(self):
        if self.open:
            raise AssertionError("a second transaction was opened inside the executor's")
        self.transactions += 1
        n = self.transactions
        if n in self.before:
            self.before.pop(n)()

        @contextmanager
        def scope():
            with self.database.transaction() as session:
                self.open = True
                try:
                    for callback in self.on_open:
                        callback(session, n)
                    yield session
                finally:
                    self.open = False
        return scope()

    def session(self):
        if self.open:
            raise AssertionError("a session was opened inside the executor's transaction")
        return self.database.session()


def _seed(database):
    with database.transaction() as session:
        for uid in ("gm", "u1", "u2"):
            session.add(User(id=uid, username=uid, normalized_username=uid, display_name=uid,
                             password_hash="h", created_at=NOW))
        session.flush()
        for cid, settings in (("A", {"rules": PIN}), ("B", {"rules": PIN}), ("C", {}),
                              ("M", {"rules": {"profile": "pf2e", "content": {"x": 1}}})):
            session.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e",
                                 created_by_user_id="gm", created_at=NOW, settings=settings))
        session.flush()
        for character_id, cid in (("a1", "A"), ("a2", "A"), ("b1", "B"), ("c1", "C"),
                                  ("m1", "M")):
            session.add(Character(id=character_id, campaign_id=cid, system="pf2e",
                                  display_name=character_id, legacy_storage="party_data",
                                  legacy_file=character_id + ".json",
                                  content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        for cid in ("A", "B", "C", "M"):
            session.add(CampaignStateAuthority(campaign_id=cid, authority="db",
                                               stream_epoch=1, changed_at=NOW))
            session.add(CampaignMembership(campaign_id=cid, user_id="gm", role="gm"))
        session.add(CampaignMembership(campaign_id="A", user_id="u1", role="player"))
        session.add(CampaignMembership(campaign_id="A", user_id="u2", role="player"))
        session.flush()
        session.add(CharacterAssignment(campaign_id="A", character_id="a1", user_id="u1",
                                        role="owner"))
        session.flush()
        states = CharacterStateRepository(session)
        for cid, character_id in (("A", "a1"), ("A", "a2"), ("B", "b1")):
            states.insert(cid, character_id, "play", rules_profile="pf2e", content=CONTENT,
                          payload={"hp": 30, "notes": "secret"})


@pytest.fixture
def db(sqlite_database, monkeypatch):
    _seed(sqlite_database)
    tracked = Tracked(sqlite_database)
    monkeypatch.setattr(runtime, "database", lambda: tracked)
    monkeypatch.setattr(executor, "authorizer_factory", Authorizer)
    LOG.clear(), HOOKS.clear(), SEEN.clear(), HIDDEN.clear()
    yield tracked
    LOG.clear(), HOOKS.clear(), SEEN.clear(), HIDDEN.clear()


_OPS = iter(range(10 ** 6))


def op():
    return f"op-{next(_OPS):016d}"


def env(operation_id=None, *, cid="A", type=DAMAGE, subject="a1", payload=None,
        expected=(), epoch=1, override=None, client=None):
    body = {"schema": 1, "operation_id": operation_id or op(), "campaign_id": cid,
            "epoch": epoch, "type": type,
            "subject": {"campaign_id": cid} if type == CAMPAIGN else {"character_id": subject},
            "expected": list(expected), "rules": {"profile": "pf2e"},
            "payload": {"amount": 5} if payload is None else payload}
    if override is not None:
        body["override"] = {"reason": override}
    if client is not None:
        body["client"] = client
    return body


def run(body, principal=GM, *, route=None):
    return execute(body, principal, route_campaign_id=route or body["campaign_id"],
                   content_descriptor=CONTENT, project=project)


def counts(db, cid="A"):
    with db.database.session() as session:
        return (session.scalar(select(func.count()).select_from(CommandReceipt)
                               .where(CommandReceipt.campaign_id == cid)),
                session.scalar(select(func.count()).select_from(GameEvent)
                               .where(GameEvent.campaign_id == cid)),
                session.scalar(select(CampaignStream.last_seq)
                               .where(CampaignStream.campaign_id == cid)) or 0)


def hp(db, character_id="a1", cid="A"):
    with db.database.session() as session:
        record = CharacterStateRepository(session).get(cid, character_id, "play")
        return record.payload["hp"], record.revision


def receipt(db, operation_id, principal_kind="user", principal_id="gm", cid="A"):
    with db.database.session() as session:
        return session.get(CommandReceipt, (cid, principal_kind, principal_id, operation_id))


def events(db, cid="A"):
    with db.database.session() as session:
        return list(session.scalars(select(GameEvent).where(GameEvent.campaign_id == cid)
                                    .order_by(GameEvent.seq)))


def set_authority(database, cid="A", **values):
    with database.transaction() as session:
        session.execute(update(CampaignStateAuthority)
                        .where(CampaignStateAuthority.campaign_id == cid).values(**values))


# --------------------------------------------------------------------------
# signature, order and check 1


@pytest.mark.parametrize("missing", ["route_campaign_id", "content_descriptor", "project"])
def test_every_keyword_is_required(missing):
    kwargs = dict(route_campaign_id="A", content_descriptor=CONTENT, project=project)
    del kwargs[missing]
    with pytest.raises(TypeError):
        execute(env(), GM, **kwargs)


def test_check_order(db):
    def spy(session, n):
        original = session.get

        def get(*args, **kwargs):
            if args and args[0] is CommandReceipt:
                LOG.append("receipt")
            return original(*args, **kwargs)
        session.get = get
    db.on_open.append(spy)
    response = run(env())
    assert response.status == 200
    assert LOG == ["receipt", "authorizer", "lock_plan", "check4", "check9", "apply"]


def test_bad_envelope_opens_no_session(db):
    body = env()
    body["operation_id"] = "short"
    response = run(body)
    assert (response.status, response.body["code"]) == (400, "bad_envelope")
    assert db.transactions == 0 and counts(db) == (0, 0, 0)


def test_run_checks_without_the_executor_flags_is_a_check_order_error(db):
    with db.database.transaction() as session:
        from core.commands.checks import CheckContext, CheckSpec
        from core.persistence import streams
        from core.persistence.command_locks import CommandLocks
        locks = CommandLocks(session, "A", lock_stream=streams.lock_stream)
        locks.stream()
        ctx = CheckContext(session=session, locks=locks, principal=GM, campaign_id="A",
                           envelope=env(), authorizer=Authorizer(session, GM, "A"),
                           project=project, content_descriptor=CONTENT, pin=PIN)
        with pytest.raises(CheckOrderError):
            run_checks(ctx, CheckSpec(type=DAMAGE, subject=("character_id", "character.play")),
                       Adapter())


def test_check1_principal_refusals_write_nothing(db):
    legacy = run(env(), Principal.legacy_gm("A"))
    token = run(env(type=USER_ONLY), TOKEN)
    for response in (legacy, token):
        assert (response.status, response.body["code"]) == (403, "not_permitted")
    not_a_principal = execute(env(), {"kind": "user", "user_id": "gm"}, route_campaign_id="A",
                              content_descriptor=CONTENT, project=project)
    assert (not_a_principal.status, not_a_principal.body["code"]) == (500, "server_error")
    assert db.transactions == 0 and counts(db) == (0, 0, 0)


def test_receipt_and_events_keyed_by_authenticated_principal(db):
    operation_id = op()
    assert run(env(operation_id), PLAYER).status == 200
    assert run(env(operation_id), TOKEN).status == 200
    assert receipt(db, operation_id, "user", "u1").status == "committed"
    assert receipt(db, operation_id, "integration_token", "cred-1").status == "committed"
    assert [(e.principal_kind, e.principal_id) for e in events(db)] == [
        ("user", "u1"), ("integration_token", "cred-1")]
    assert hp(db) == (20, 3)


def test_callbacks_share_executor_session_and_locks(db):
    opened = []
    db.on_open.append(lambda session, n: opened.append(session))
    assert run(env()).status == 200
    assert db.transactions == 1
    session = opened[0]
    for name in ("lock_plan", "apply"):
        assert SEEN[name][0] is session and SEEN[name][2] == "A"
    assert SEEN["lock_plan"][1] is SEEN["apply"][1] and SEEN["authorizer_session"] is session

    db.transactions = 0
    stale = run(env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}]))
    assert stale.status == 409 and db.transactions == 2

    db.transactions = 0
    assert run(env(), Principal.user("nobody")).status == 403 and db.transactions == 1

    body, db.transactions = env(), 0
    run(body)
    db.transactions = 0
    assert run(body).body["replayed"] is True and db.transactions == 1


def test_cross_campaign_payload_id_is_404(db):
    response = run(env(payload={"amount": 5, "target": "b1"}))
    assert (response.status, response.body["code"]) == (404, "not_found")
    assert hp(db, "b1", "B") == (30, 1) and counts(db, "B") == (0, 0, 0)
    assert counts(db)[1:] == (0, 0)


# --------------------------------------------------------------------------
# commit path, seqs and replay


def test_commit_writes_state_receipt_and_events_together(db):
    body = env()
    response = run(body)
    assert response.status == 200
    assert response.body == {
        "ok": True, "operation_id": body["operation_id"], "status": "committed",
        "replayed": False, "revisions": {"character.play:a1": 2},
        "events": [{"event_id": events(db)[0].event_id, "seq": 1}], "watermark": 1,
        "result": {"records": {"character.play:a1": {"rev": 2, "hp": 25, "notes": "secret"}}}}
    stored = receipt(db, body["operation_id"])
    assert (stored.status, stored.applied_status, stored.code, stored.digest) == (
        "committed", "committed", None, command_digest(body))
    assert (stored.revisions, stored.seq_first, stored.seq_last, stored.stream_epoch) == (
        {"character.play:a1": 2}, 1, 1, 1)
    assert hp(db) == (25, 2) and counts(db) == (1, 1, 1)


def test_exception_after_state_write_leaves_nothing(db):
    def boom(ctx):
        damage(ctx)
        raise RuntimeError("after the write, before the receipt")
    HOOKS["apply"] = boom
    response = run(env())
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert hp(db) == (30, 1) and counts(db) == (0, 0, 0)


def test_seqs_are_contiguous_and_a_row_can_store_its_declare_seq(db):
    def three(ctx):
        _, first = ctx.emit(EventDraft(type="action.declared", subjects=A1))
        record = ctx.records[("character.play", "a1")]
        new = CharacterStateRepository(ctx.session).write(
            "A", "a1", "play", read_revision=record.revision,
            payload={**record.payload, "declare_seq": first})
        ctx.emit(EventDraft(type="x.second", subjects=A1))
        ctx.emit(EventDraft(type="x.third", subjects=A1))
        return Applied(written={("character.play", "a1"): new})
    assert run(env()).status == 200
    HOOKS["apply"] = three
    body = env()
    response = run(body)
    stored, rows = receipt(db, body["operation_id"]), events(db)[1:]
    assert [e.seq for e in rows] == [2, 3, 4]
    assert (stored.seq_first, stored.seq_last) == (2, 4)
    assert stored.event_ids == [e.event_id for e in rows]
    assert [e["seq"] for e in response.body["events"]] == [2, 3, 4]
    with db.database.session() as session:
        assert CharacterStateRepository(session).get("A", "a1", "play").payload["declare_seq"] == 2


def test_same_key_and_digest_replays_with_a_fresh_projection(db):
    body = env()
    first = run(body)
    assert run(env(subject="a1")).status == 200  # a later command moves a1 on
    again = run(body)
    assert again.status == 200
    assert again.body["replayed"] is True and again.body["status"] == "committed"
    assert again.body["revisions"] == first.body["revisions"]
    assert again.body["events"] == first.body["events"] and again.body["watermark"] == 1
    assert again.body["result"] == {
        "records": {"character.play:a1": {"rev": 3, "hp": 20, "notes": "secret"}}}
    assert counts(db) == (2, 2, 2) and hp(db) == (20, 3)


def test_replay_after_access_is_lost_is_403_without_a_body(db):
    body = env()
    assert run(body, PLAYER).status == 200
    with db.database.transaction() as session:
        session.execute(CharacterAssignment.__table__.delete())
        session.execute(CampaignMembership.__table__.delete().where(
            CampaignMembership.user_id == "u1"))
    response = run(body, PLAYER)
    assert (response.status, response.body) == (403, None)


def test_pending_answers_202_and_replays_202(db):
    HOOKS["apply"] = lambda ctx: damage(ctx, pending=True)
    body = env()
    first = run(body)
    assert (first.status, first.body["status"]) == (202, "pending")
    assert receipt(db, body["operation_id"]).applied_status == "pending"
    again = run(body)
    assert (again.status, again.body["status"], again.body["replayed"]) == (202, "pending", True)


def test_replay_of_a_rejected_receipt(db):
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])
    first = run(body)
    assert first.status == 409 and first.body["code"] == "stale_revision"
    assert first.body["conflicts"] == [
        {"kind": "character.play", "id": "a1", "expected": 99, "current": 1}]
    again = run(body)
    assert (again.status, again.body) == (409, {
        "ok": False, "code": "stale_revision", "operation_id": body["operation_id"],
        "replayed": True})


def test_reused_and_abandoned_keys(db):
    body = env()
    assert run(body).status == 200
    changed = dict(body, payload={"amount": 6})
    reused = run(changed)
    assert (reused.status, reused.body["code"]) == (409, "operation_id_reused")
    abandoned = op()
    with db.database.transaction() as session:
        session.add(CommandReceipt(campaign_id="A", principal_kind="user", principal_id="gm",
                                   operation_id=abandoned, digest=None, status="rejected",
                                   code="abandoned", stream_epoch=1))
    for payload in ({"amount": 5}, {"amount": 9}):
        response = run(env(abandoned, payload=payload))
        assert (response.status, response.body["code"]) == (409, "abandoned")
    assert counts(db)[0] == 2


# --------------------------------------------------------------------------
# the code table and exceptions

WRITES_RECEIPT = {"not_found", "already_finalized", "stale_revision", "rules_mismatch",
                  "item_locked", "insufficient_funds", "insufficient_stock",
                  "insufficient_quantity", "validation_failed", "rule_violation", "not_live",
                  "in_encounter", "unlock_required", "edition_mismatch", "target_invalid"}


@pytest.mark.parametrize("code", sorted(set(executor.CODES)))
def test_every_code_table_row(db, code):
    def refuse(ctx):
        raise CommandRefusal(code)
    HOOKS["check9"] = refuse
    body = env()
    response = run(body)
    assert response.status == executor.CODES[code]
    assert response.body["code"] == code and response.body["operation_id"] == body["operation_id"]
    stored = receipt(db, body["operation_id"])
    assert (stored is not None) == (code in WRITES_RECEIPT)
    if stored is not None:
        assert (stored.status, stored.code, stored.applied_status, stored.seq_first) == (
            "rejected", code, None, None)
    assert counts(db)[1:] == (0, 0) and hp(db) == (30, 1)
    assert executor.RECEIPT_CODES == WRITES_RECEIPT


@pytest.mark.parametrize("authority, epoch, code, status", [
    ("migrating", 1, "migration_in_progress", 503), ("exporting", 1, "migration_in_progress", 503),
    ("file", 1, "authority_unavailable", 503), ("db", 2, "resync_required", 409),
])
def test_fence(db, authority, epoch, code, status):
    set_authority(db.database, authority=authority, stream_epoch=epoch)
    response = run(env())
    assert (response.status, response.body["code"]) == (status, code)
    assert counts(db)[:2] == (0, 0) and "lock_plan" not in LOG


def test_no_authority_row_is_authority_unavailable(db):
    with db.database.transaction() as session:
        session.execute(CampaignStateAuthority.__table__.delete().where(
            CampaignStateAuthority.campaign_id == "A"))
    assert run(env()).body["code"] == "authority_unavailable"


@pytest.mark.parametrize("code, status, writes", [
    ("in_encounter", 422, True), ("rule_violation", 422, True), ("content_mismatch", 503, False),
])
def test_core_commands_exceptions_answer_by_attribute(db, code, status, writes):
    def raise_it(ctx):
        raise FakeLocked(code, status)
    HOOKS["apply"] = raise_it
    body = env()
    response = run(body)
    assert (response.status, response.body["code"]) == (status, code)
    assert (receipt(db, body["operation_id"]) is not None) == writes
    assert hp(db) == (30, 1)


def test_coded_exception_outside_core_commands_is_500(db):
    class Lookalike(Exception):
        code, http_status = "in_encounter", 422
    HOOKS["apply"] = lambda ctx: (_ for _ in ()).throw(Lookalike())
    response = run(env())
    assert (response.status, response.body["code"]) == (500, "server_error")


@pytest.mark.parametrize("exc, code, status", [
    (StateRowMissing("gone"), "authority_unavailable", 503),
    (ProfileMismatch("profile"), "server_error", 500),
    (LockOrderError("order"), "server_error", 500),
    (SessionBindError("bind"), "server_error", 500),
    (OperationalError("SELECT", {}, type("E", (Exception,), {"sqlstate": "55P03"})()),
     "temporarily_unavailable", 503),
    (OperationalError("SELECT", {}, type("E", (Exception,), {"sqlstate": "40001"})()),
     "temporarily_unavailable", 503),
    (OperationalError("SELECT", {}, type("E", (Exception,), {"sqlstate": "08006"})()),
     "server_error", 500),
])
def test_exceptions_map_to_codes_and_write_nothing(db, exc, code, status):
    def raise_it(ctx):
        damage(ctx)
        raise exc
    HOOKS["apply"] = raise_it
    response = run(env())
    assert (response.status, response.body["code"]) == (status, code)
    assert counts(db) == (0, 0, 0) and hp(db) == (30, 1)


@pytest.mark.parametrize("campaign", ["C", "M"])
def test_missing_pin_is_503_authority_unavailable(db, campaign):
    response = run(env(cid=campaign, subject=campaign.lower() + "1"))
    assert (response.status, response.body["code"]) == (503, "authority_unavailable")
    assert counts(db, campaign) == (0, 0, 0) and "lock_plan" not in LOG


def test_replay_needs_no_pin_and_ctx_pin_is_the_campaign_pin(db):
    body = env()
    assert run(body).status == 200
    with db.database.session() as session:
        assert SEEN["lock_plan"][3] == CampaignPinRepository(session).pin("A")
    with db.database.transaction() as session:
        session.execute(update(Campaign).where(Campaign.id == "A").values(settings={}))
    assert run(body).body["replayed"] is True
    assert run(env()).body["code"] == "authority_unavailable"


def test_projection_omits_hidden_records_and_their_events(db):
    def two(ctx):
        a1 = damage(ctx)
        a2 = damage(ctx, "a2")
        return Applied(written={**a1.written, **a2.written})
    HOOKS["apply"] = two
    HIDDEN.add("character.play:a2")
    response = run(env(payload={"amount": 5, "target": "a2"}))
    assert response.body["revisions"] == {"character.play:a1": 2}
    assert [e["seq"] for e in response.body["events"]] == [1]
    assert response.body["result"].keys() == {"records"}
    assert set(response.body["result"]["records"]) == {"character.play:a1"}
    assert hp(db, "a2") == (25, 2)


def test_events_about_hidden_records_are_omitted_fresh_and_on_replay(db):
    def mixed(ctx):
        result = damage(ctx)  # seq 1, about a1: shown
        ctx.emit(EventDraft(type="character.noted", changes=[
            {"kind": "character.play", "id": "a2", "path": "notes", "after": "x"}]))
        ctx.emit(EventDraft(type="character.seen", subjects=[{"kind": "character", "id": "a2"}]))
        ctx.emit(EventDraft(type="nothing.named"))  # no record: fails closed
        ctx.emit(EventDraft(type="odd.subject", subjects=["a2"],  # malformed: fails closed
                            changes=[{"kind": "character.play", "id": "a1", "path": "hp"}]))
        ctx.emit(EventDraft(type="mystery.kind", subjects=[{"kind": "mystery", "id": "q"}]))
        return result
    HOOKS["apply"] = mixed
    HIDDEN.add("character.play:a2")
    body = env(payload={"amount": 5, "target": "a2"})
    first = run(body)
    assert [e["seq"] for e in first.body["events"]] == [1]
    HOOKS.clear()
    again = run(body)
    assert again.body["replayed"] is True and [e["seq"] for e in again.body["events"]] == [1]
    HIDDEN.clear()
    assert [e["seq"] for e in run(body).body["events"]] == [1, 2, 3]


def test_campaign_subject_events_are_listed(db):
    HOOKS["apply"] = lambda ctx: (ctx.emit(EventDraft(
        type="session.noted", subjects=[{"kind": "campaign", "id": "A"}])), None)[1]
    body = env(type=CAMPAIGN)
    assert [e["seq"] for e in run(body).body["events"]] == [1]
    HOOKS.clear()
    assert [e["seq"] for e in run(body).body["events"]] == [1]


# --------------------------------------------------------------------------
# refusal transaction and both transition races


def test_refusal_receipt_commits_before_the_answer(db):
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])
    response = run(body)
    assert response.status == 409 and not db.open
    assert receipt(db, body["operation_id"]).code == "stale_revision"


def test_race_a_authority_flip_before_the_refusal_insert(db):
    db.before[2] = lambda: set_authority(db.database, authority="migrating")
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])
    response = run(body)
    assert (response.status, response.body["code"]) == (503, "migration_in_progress")
    assert receipt(db, body["operation_id"]) is None


def test_race_a_epoch_bump_before_the_refusal_insert(db):
    db.before[2] = lambda: set_authority(db.database, stream_epoch=2)
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])
    response = run(body)
    assert (response.status, response.body["code"]) == (409, "resync_required")
    assert receipt(db, body["operation_id"]) is None


def test_refusal_transaction_answers_from_a_receipt_that_appeared(db):
    body = env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}])

    def insert_rejected():
        with db.database.transaction() as session:
            session.add(CommandReceipt(campaign_id="A", principal_kind="user", principal_id="gm",
                                       operation_id=body["operation_id"],
                                       digest=command_digest(body), status="rejected",
                                       code="not_found", stream_epoch=1))
    db.before[2] = insert_rejected
    response = run(body)
    assert (response.status, response.body["code"], response.body["replayed"]) == (
        404, "not_found", True)


def test_race_b_old_epoch_retry_is_resync_not_replay(db):
    body = env()
    assert run(body).status == 200
    set_authority(db.database, stream_epoch=2)
    response = run(body)
    assert (response.status, response.body["code"]) == (409, "resync_required")


# --------------------------------------------------------------------------
# crash cases


def test_receipt_insert_fails_once_then_retry_applies_exactly_once(db):
    failed = []

    def fail_once(session, flush_context, instances):
        if not failed and any(isinstance(o, CommandReceipt) for o in session.new):
            failed.append(True)
            raise OperationalError("INSERT", {}, Exception("disk I/O error"))
    event.listen(Session, "before_flush", fail_once)
    try:
        body = env()
        first = run(body)
        assert (first.status, first.body["code"]) == (500, "server_error")
        assert hp(db) == (30, 1)
        again = run(body)
    finally:
        event.remove(Session, "before_flush", fail_once)
    assert again.status == 200 and again.body["replayed"] is False
    assert hp(db) == (25, 2) and counts(db) == (1, 1, 1)
    assert run(body).body["replayed"] is True and hp(db) == (25, 2)


@pytest.mark.parametrize("hook, landed", [("before_commit", False), ("after_commit", True)])
def test_commit_raising_is_503_and_the_retry_answers_from_the_receipt(db, hook, landed):
    def boom(session):
        raise OperationalError("COMMIT", {}, Exception("connection lost"))

    def arm(session, n):
        if n == 1:
            event.listen(session, hook, boom, once=True)
    db.on_open.append(arm)
    calls = []
    session_events.subscribe(lambda c, s: calls.append(s))
    try:
        body = env()
        first = run(body)
        assert (first.status, first.body["code"]) == (503, "temporarily_unavailable")
        assert calls == []
        db.on_open.remove(arm)
        again = run(body)
    finally:
        session_events._SUBSCRIBERS.clear()
    assert again.status == 200 and again.body["replayed"] is landed
    assert hp(db) == (25, 2) and counts(db)[0] == 1


# --------------------------------------------------------------------------
# events


def test_event_rows(db):
    def drafts(ctx):
        result = damage(ctx)
        ctx.emit(EventDraft(type="character.noted",
                            changes=[{"kind": "character.play", "id": "a1", "path": "notes",
                                      "before": "secret", "after": "plans"}],
                            visibility={"gm": "none", "encounter_player": "redact:[exact_hp]"},
                            explain=[{"text": "noted"}]))
        return result
    HOOKS["apply"] = drafts
    assert run(env(override="table ran late", client={"origin": "obsidian"})).status == 200
    damaged, noted = events(db)
    assert noted.changes == [{"kind": "character.play", "id": "a1", "path": "notes",
                              "changed": True}]
    assert noted.visibility == {
        "gm": "full", "gm_integration": "none", "owner": "none", "editor": "none",
        "viewer": "none", "party": "none", "encounter_player": "redact:[exact_hp]",
        "qm": "none", "member": "none"}
    assert damaged.visibility["owner"] == "full" and damaged.visibility["gm"] == "full"
    assert noted.explain == [{"text": "noted"}, {"override": {"reason": "table ran late"}}]
    assert (noted.origin, noted.rules_profile, noted.stream_epoch, len(noted.event_id)) == (
        "obsidian", "pf2e", 1, 32)
    assert noted.operation_id == damaged.operation_id


@pytest.mark.parametrize("visibility", [{"party": "redact:[colour]"}, {"party": "partial"},
                                        {"stranger": "full"}, {"party": "redact:[]"}])
def test_unknown_visibility_is_500_and_writes_nothing(db, visibility):
    def bad(ctx):
        result = damage(ctx)
        ctx.emit(EventDraft(type="x", visibility=visibility))
        return result
    HOOKS["apply"] = bad
    assert run(env()).status == 500 and counts(db) == (0, 0, 0)


@pytest.mark.parametrize("client", [None, {}, {"origin": None}])
def test_absent_origin_is_unknown(db, client):
    body = env(client=client)
    if client is None:
        body.pop("client", None)
    assert run(body).status == 200
    assert events(db)[0].origin == "unknown"


def test_event_draft_rejects_session_id():
    with pytest.raises(TypeError):
        EventDraft(type="x", session_id="s1")


def _add_session(database, session_id, status="active", cid="A"):
    with database.transaction() as session:
        session.add(CampaignSession(campaign_id=cid, session_id=session_id, status=status,
                                    origin="website", started_at=NOW,
                                    ended_at=None if status == "active" else NOW))


def test_event_session_id_null_without_active_session(db):
    assert run(env()).status == 200
    assert events(db)[0].session_id is None


def test_event_session_id_survives_row_ending_in_apply(db):
    _add_session(db.database, "s1")

    def end(ctx):
        ctx.session.execute(update(CampaignSession).where(
            CampaignSession.campaign_id == "A", CampaignSession.session_id == "s1")
            .values(status="ended", ended_at=NOW))
        assert ctx.session.scalar(select(func.count()).select_from(CampaignSession).where(
            CampaignSession.status == "active")) == 0
        ctx.emit(EventDraft(type="session.ended"))
    HOOKS["apply"] = end
    assert run(env(type=CAMPAIGN)).status == 200
    assert [e.session_id for e in events(db)] == ["s1"]


def test_bind_started_session_tags_later_events(db):
    def start(ctx):
        ctx.emit(EventDraft(type="before.bind"))
        ctx.session.add(CampaignSession(campaign_id="A", session_id="s2", status="active",
                                        origin="website", started_at=NOW))
        ctx.bind_started_session("s2")
        ctx.emit(EventDraft(type="session.started"))
    HOOKS["apply"] = start
    assert run(env(type=CAMPAIGN)).status == 200
    assert [e.session_id for e in events(db)] == [None, "s2"]
    HOOKS.clear()
    assert run(env()).status == 200
    assert events(db)[-1].session_id == "s2"


def _bind_cases():
    def add(ctx, cid, sid, status="active"):
        ctx.session.add(CampaignSession(campaign_id=cid, session_id=sid, status=status,
                                        origin="website", started_at=NOW,
                                        ended_at=None if status == "active" else NOW))

    def no_row(ctx):
        ctx.bind_started_session("ghost")

    def other_campaign(ctx):
        add(ctx, "B", "s3")
        ctx.bind_started_session("s3")

    def ended(ctx):
        add(ctx, "A", "s4", "ended")
        ctx.bind_started_session("s4")

    def twice(ctx):
        add(ctx, "A", "s5")
        ctx.bind_started_session("s5")
        ctx.bind_started_session("s5")

    return {"no_row": no_row, "other_campaign": other_campaign, "ended": ended,
            "second_bind": twice, "active_at_snapshot": "active"}


@pytest.mark.parametrize("case", sorted(_bind_cases()))
def test_bind_started_session_refuses(db, case):
    action = _bind_cases()[case]
    if action == "active":
        _add_session(db.database, "s0")

        def action(ctx):
            ctx.bind_started_session("s0")

    def apply(ctx):
        ctx.emit(EventDraft(type="x"))
        action(ctx)
    HOOKS["apply"] = apply
    response = run(env(type=CAMPAIGN))
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert counts(db) == (0, 0, 0)
    with db.database.session() as session:
        assert session.scalar(select(func.count()).select_from(CampaignSession)) == (
            1 if case == "active_at_snapshot" else 0)


# --------------------------------------------------------------------------
# PE-14a review requirements (Decisions 16 and 17)


@pytest.mark.parametrize("where", ["apply", "lock_plan"])
def test_envelope_mutation_is_500_nothing_written(db, where):
    def mutate_apply(ctx):
        result = damage(ctx)
        ctx.envelope["payload"].setdefault("log", []).append("changed")
        return result

    def mutate_lock_plan(ctx):
        ctx.envelope["payload"]["amount"] = 99
    HOOKS[where] = mutate_apply if where == "apply" else mutate_lock_plan
    response = run(env(payload={"amount": 5, "log": []}))
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert counts(db) == (0, 0, 0) and hp(db) == (30, 1)


@pytest.mark.parametrize("where, change", [
    ("lock_plan", lambda envelope: envelope["client"].__setitem__("origin", "website")),
    ("apply", lambda envelope: envelope["client"].__setitem__("origin", "X" * 40)),
    ("apply", lambda envelope: envelope["client"].__setitem__("extra", 1)),
])
def test_client_mutation_is_500_nothing_written(db, where, change):
    def hook(ctx):
        change(ctx.envelope)
        if where == "apply":
            return damage(ctx)
    HOOKS[where] = hook
    response = run(env(client={"origin": "obsidian"}))
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert counts(db) == (0, 0, 0) and hp(db) == (30, 1)


def test_event_origin_is_read_once_at_check_1(db, monkeypatch):
    real = executor.client_origin
    calls = []
    monkeypatch.setattr(executor, "client_origin", lambda e: calls.append(1) or real(e))
    HOOKS["apply"] = lambda ctx: damage(ctx, events=3)
    assert run(env(client={"origin": "obsidian"})).status == 200
    assert calls == [1] and {e.origin for e in events(db)} == {"obsidian"}


def test_digest_is_unchanged_after_a_normal_command(db):
    seen = {}

    def check(ctx):
        result = damage(ctx)
        seen["digest"] = command_digest(ctx.envelope)
        return result
    HOOKS["apply"] = check
    body = env()
    assert run(body).status == 200 and seen["digest"] == command_digest(body)


def test_apply_without_event_is_500_nothing_written(db, caplog):
    HOOKS["apply"] = lambda ctx: damage(ctx, events=0)
    response = run(env())
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert counts(db) == (0, 0, 0) and hp(db) == (30, 1)
    assert any(isinstance(r.exc_info[1], NoEventsEmitted) for r in caplog.records if r.exc_info)


def test_envelope_mutation_before_a_refusal_writes_no_receipt(db, caplog):
    def mutate(ctx):
        ctx.envelope["payload"]["amount"] = 99
    HOOKS["lock_plan"] = mutate
    HOOKS["check9"] = lambda ctx: (_ for _ in ()).throw(CommandRefusal("in_encounter"))
    body = env()
    response = run(body)
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert receipt(db, body["operation_id"]) is None and db.transactions == 1
    assert any(isinstance(r.exc_info[1], executor.EnvelopeMutated)
               for r in caplog.records if r.exc_info)


def test_every_committed_receipt_has_a_seq_range(db):
    for _ in range(3):
        run(env())
    with db.database.session() as session:
        rows = session.scalars(select(CommandReceipt).where(
            CommandReceipt.status == "committed")).all()
    assert len(rows) == 3 and all(r.seq_first is not None and r.seq_last for r in rows)


# --------------------------------------------------------------------------
# post-commit hook and import guards


def test_post_commit_hook_runs_once_after_commit_only(db):
    calls = []

    def hook(campaign_id, last_seq):
        with db.database.session() as session:  # the commit is visible: no transaction open
            committed = session.scalar(select(func.count()).select_from(CommandReceipt))
        calls.append((campaign_id, last_seq, committed, db.open))
    session_events.subscribe(hook)
    try:
        body = env()
        run(body)
        run(body)  # replay
        run(env(expected=[{"kind": "character.play", "id": "a1", "rev": 99}]))  # refusal
        run(env(), Principal.user("nobody"))  # 403
    finally:
        session_events.unsubscribe(hook)
    assert calls == [("A", 1, 1, False)]


def test_a_failing_subscriber_does_not_change_the_answer(db):
    def broken(campaign_id, last_seq):
        raise RuntimeError("hub down")
    session_events.subscribe(broken)
    try:
        assert run(env()).status == 200
    finally:
        session_events.unsubscribe(broken)


BANNED = ("core.persistence.character_files", "app", "core.campaigns", "core.obsidian_sync",
          "core.obsidian_session_history", "systems", "class_matrix", "core.compendium")


def _banned_imports(source, package="core.commands"):
    """Every banned module a source imports, absolute, ``from X import Y`` or relative."""

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "") if not node.level else importlib.util.resolve_name(
                "." * node.level + (node.module or ""), package)
            names = [base] + [f"{base}.{a.name}" for a in node.names]
        else:
            continue
        found += [n for n in names if any(n == b or n.startswith(b + ".") for b in BANNED)]
    return found


@pytest.mark.parametrize("source", [
    "import app", "from core.persistence import character_files", "from core import campaigns",
    "from .. import campaigns", "from ..persistence import character_files",
    "from ..obsidian_sync import campaign_lock", "import systems.dnd5e.paths",
])
def test_the_import_guard_catches_every_import_form(source):
    assert _banned_imports(source) != []


def test_commands_package_imports_no_banned_module():
    offenders = [(path.name, name)
                 for path in sorted((REPO_ROOT / "core" / "commands").glob("*.py"))
                 for name in _banned_imports(path.read_text(encoding="utf-8"))]
    assert offenders == []


def test_commands_package_imports_no_process_lock_module():
    script = (
        "import json, pkgutil, importlib, sys\n"
        "import core.commands as package\n"
        "for m in pkgutil.iter_modules(package.__path__):\n"
        "    importlib.import_module('core.commands.' + m.name)\n"
        "print(json.dumps(sorted(n for n in ('core.obsidian_sync', "
        "'core.obsidian_session_history', 'core.campaigns', 'app', "
        "'core.persistence.character_files', 'class_matrix') if n in sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True)
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []


# --------------------------------------------------------------------------
# Implementation review 1: R1-1 (projector mutation) and R1-2 (command results)


@pytest.mark.parametrize("change", [
    lambda envelope: envelope["payload"].__setitem__("amount", 99),
    lambda envelope: envelope["client"].__setitem__("origin", "website"),
], ids=["hashed_payload", "client_only"])
def test_projector_mutation_is_500_and_rolls_back(db, change):
    calls = []

    def mutating_project(principal, records):
        change(SEEN["ctx"].envelope)  # a projector that kept ctx from lock_plan
        return project(principal, records)
    session_events.subscribe(lambda c, s: calls.append(s))
    try:
        response = execute(env(client={"origin": "obsidian"}), GM, route_campaign_id="A",
                           content_descriptor=CONTENT, project=mutating_project)
    finally:
        session_events._SUBSCRIBERS.clear()
    assert (response.status, response.body["code"]) == (500, "server_error")
    assert hp(db) == (30, 1) and counts(db) == (0, 0, 0) and calls == []


class EffectAdapter:
    """A stateless PE-13c adapter: effects add max HP and focus points."""

    def effective_max(self, build, play, items):
        return build["hp_max"] + sum(e.get("hp_bonus", 0) for e in play["effects"])

    def is_dead(self, play):
        return False

    def pools(self, build, play, items):
        return {"focus": build["focus"] + sum(e.get("focus", 0) for e in play["effects"])}

    def invalid_choices(self, build, play):
        return []

    def orphaned_effects(self, build, play, items, removed_pools):
        return []


BUILD = {"hp_max": 10, "focus": 3}


def _with_bonus(database, character_id):
    with database.transaction() as session:
        states = CharacterStateRepository(session)
        before = states.get("A", character_id, "play")
        states.write("A", character_id, "play", read_revision=before.revision,
                     payload={**before.payload, "hp": 6, "focus_used": 4, "hp_floor_debt": 0,
                              "effects": [{"id": "bonus", "hp_bonus": 8, "focus": 2}]})


def _expiry(play):
    from core.commands.reconciliation import Snapshot
    after = {**play, "effects": []}
    return after, dict(before=Snapshot(BUILD, (), play), after=Snapshot(BUILD, (), after),
                       play=play, hp=play["hp"], used={"focus": play["focus_used"]},
                       user_initiated=False)


def expire_bonus(ctx):
    """Expire a1's (and the payload target's) HP/focus effect through PE-13c."""

    from core.commands.reconciliation import apply as reconcile_apply
    written = {}
    ids = [ctx.envelope["subject"]["character_id"]]
    ids += [ctx.envelope["payload"]["target"]] if ctx.envelope["payload"].get("target") else []
    for character_id in ids:
        current = ctx.records[("character.play", character_id)]
        after, kwargs = _expiry(current.payload)
        result = reconcile_apply(EffectAdapter(), **kwargs)
        new = CharacterStateRepository(ctx.session).write(
            "A", character_id, "play", read_revision=current.revision,
            payload={**after, "hp": result.hp, "focus_used": result.used["focus"],
                     **result.play_updates(after)})
        ctx.emit(EventDraft(
            type="character.build_reconciled",
            subjects=[{"kind": "character", "id": character_id}],
            changes=[{"kind": "character.play", "id": character_id, "rev": new.revision,
                      "path": "hp", "before": current.payload["hp"], "after": result.hp}],
            visibility={"owner": "full"}, result={"reconciliation": list(result.entries)}))
        written[("character.play", character_id)] = new
    return Applied(written=written)


def test_result_reconciliation_is_the_pe13c_entries_fresh_and_on_replay(db):
    from core.commands.reconciliation import preview
    _with_bonus(db.database, "a1")
    with db.database.session() as session:
        play = CharacterStateRepository(session).get("A", "a1", "play").payload
    expected = preview(EffectAdapter(), **_expiry(play)[1])
    assert [e["type"] for e in expected] == ["hp", "pool_clamped"]
    assert expected[0] == {"type": "hp", "hp_before": 6, "hp_after": 1, "debt_before": 0,
                           "debt_after": 3, "effmax_before": 18, "effmax_after": 10}

    HOOKS["apply"] = expire_bonus
    body = env()
    response = run(body)
    assert response.status == 200
    assert response.body["result"]["reconciliation"] == expected
    assert response.body["result"]["records"]["character.play:a1"]["hp_floor_debt"] == 3
    assert hp(db) == (1, 3)
    assert [i for i in events(db)[0].explain if "result" in i] == [
        {"result": "reconciliation", "entry": entry} for entry in expected]

    HOOKS.clear()
    assert run(env(payload={"amount": 0})).status == 200  # a1 moves on
    again = run(body)
    assert again.body["replayed"] is True
    assert again.body["result"]["reconciliation"] == expected  # from the event, not a cache
    assert again.body["result"]["records"]["character.play:a1"]["rev"] == 4  # fresh
    with db.database.session() as session:
        stored = session.get(CommandReceipt, ("A", "user", "gm", body["operation_id"]))
        assert set(stored.revisions) == {"character.play:a1"}  # no response body cached


def test_result_entries_for_a_hidden_record_are_omitted(db):
    _with_bonus(db.database, "a1")
    _with_bonus(db.database, "a2")
    HOOKS["apply"] = expire_bonus
    HIDDEN.add("character.play:a2")
    body = env(payload={"amount": 0, "target": "a2"})
    response = run(body)
    assert response.status == 200 and hp(db, "a2") == (1, 3)
    shown = response.body["result"]
    assert set(shown["records"]) == {"character.play:a1"}
    assert len(shown["reconciliation"]) == 2  # a1's hp and pool_clamped only
    HOOKS.clear()
    assert len(run(body).body["result"]["reconciliation"]) == 2
    HIDDEN.add("character.play:a1")
    assert "reconciliation" not in run(body).body["result"]


@pytest.mark.parametrize("draft", [
    dict(result={"records": [{"x": 1}]}), dict(result={"Bad": [{"x": 1}]}),
    dict(result={"reconciliation": [1]}), dict(result={"reconciliation": {"x": 1}}),
    dict(explain=[{"result": "reconciliation", "entry": {}}]),
])
def test_invalid_result_sections_are_500_and_write_nothing(db, draft):
    def bad(ctx):
        result = damage(ctx)
        ctx.emit(EventDraft(type="x", subjects=A1, **draft))
        return result
    HOOKS["apply"] = bad
    assert run(env()).status == 500 and counts(db) == (0, 0, 0)


@pytest.mark.parametrize("result", [
    {"records": []}, {"Bad": []}, {"x" * 65: []}, {1: []}, {"ok": [1]}, {"ok": {"a": 1}}, [],
])
def test_result_items_refuses_bad_sections(result):
    from core.commands.events import result_items
    with pytest.raises(ValueError):
        result_items(result)
    assert result_items({"a_1": ({"n": 1},), "b": []}) == [{"result": "a_1", "entry": {"n": 1}}]
