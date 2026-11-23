"""PE-14d on PostgreSQL: authorization writers under real stream-lock contention.

CI only (``-m postgresql``; Docker is retired locally).  SQLite treats
``FOR UPDATE`` as a no-op, so the one lock order (stream row, then campaign
row), the lock timeout and the races between an authorization write and a
command are proved here.  Commands run through the real authorizer
(``executor.authorizer_factory`` is not patched); the command type, seed and
projector are the SQLite executor suite's.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from core import auth, obsidian_sync, storage
from core.commands import authorizer, executor
from core.persistence import identity, runtime, streams
from core.persistence.database import Database, normalize_database_url
from core.persistence.models import (
    AuditEvent, Campaign, CampaignMembership, CampaignStateAuthority, Character,
    CharacterAssignment, CommandReceipt, IntegrationToken, Invitation, InviteRedemption, User,
)
from core.persistence.services import InviteUnavailableError, TransactionalStore
from core.persistence.state_repositories import CharacterStateRepository
from core.request_context import Principal, PrincipalKind
from tests.persistence.test_command_executor import (
    CONTENT, DAMAGE, GM, HOOKS, NOW, PIN, PLAYER, _seed, damage, env, project,
)
from tests.persistence.test_postgresql_concurrency import _race

pytestmark = pytest.mark.postgresql
TCID = "c" * 32  # token cases: ``campaign_lock`` needs a storage-safe campaign id
_N = itertools.count()


def _prepare(database, monkeypatch, tmp_path):
    """Seed campaign A (db authority, pin, u1 owns a1) and point the runtime at ``database``."""

    _seed(database)
    with database.transaction() as session:  # an existing stream row: waits are row locks
        streams.lock_stream(session, "A")
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: database)
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setattr(executor, "STREAM_LOCK_TIMEOUT_MS", 15000)
    monkeypatch.setattr(auth, "_PW_METHOD", "pbkdf2:sha256:1000")  # signup reaches its locks fast
    # The real authorizer: the test type is open to a character's owner and to "@live" tokens.
    monkeypatch.setitem(authorizer._POLICIES, DAMAGE,
                        authorizer.RolePolicy(roles=frozenset({"owner"}), live=True))
    HOOKS.clear()


@pytest.fixture
def pg(monkeypatch, tmp_path):
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL authorization-write tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql", "DATABASE_URL must be PostgreSQL"
    schema = "gm_pf2e_pe14d_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        _prepare(database, monkeypatch, tmp_path)
        yield database
    finally:
        HOOKS.clear()
        database.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


# --------------------------------------------------------------------------
# helpers


def _invite(database, *, uses=1, character=True):
    """A fresh account-creating invite on A, its own unowned character and a non-member user."""

    n = next(_N)
    user_id, character_id = f"b{n}", f"h{n}" if character else None
    with database.transaction() as session:
        session.add(User(id=user_id, username=user_id, normalized_username=user_id,
                         display_name=user_id, password_hash="h", created_at=NOW))
        if character:
            session.add(Character(
                id=character_id, campaign_id="A", system="pf2e", display_name=character_id,
                legacy_storage="party_data", legacy_file=character_id + ".json",
                content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        session.flush()
        session.add(Invitation(
            code=f"INV-{n:04d}", campaign_id="A", role="player", character_id=character_id,
            creates_account=True, remaining_uses=uses, created_by_user_id="gm",
            created_at=NOW, expires_at=time.time() + 86400))
    return SimpleNamespace(code=f"INV-{n:04d}", user=user_id, character=character_id,
                           signup=f"new{n}")


def _signup(inv):
    return identity.redeem_invite(inv.code, username=inv.signup, password="password-1")


def _existing(inv):
    return identity.redeem_invite(inv.code, user_id=inv.user)


def _state(database, inv):
    with database.session() as session:
        return SimpleNamespace(
            uses=session.get(Invitation, inv.code).remaining_uses,
            redemptions=session.scalar(select(func.count()).select_from(InviteRedemption).where(
                InviteRedemption.invitation_code == inv.code)),
            owners=list(session.scalars(select(CharacterAssignment.user_id).where(
                CharacterAssignment.character_id == inv.character,
                CharacterAssignment.role == "owner"))),
            signup_id=session.scalar(select(User.id).where(
                User.normalized_username == inv.signup)),
            members=set(session.scalars(select(CampaignMembership.user_id).where(
                CampaignMembership.campaign_id == "A"))),
            audits=session.scalar(select(func.count()).select_from(AuditEvent)))


def _held(authorizer, *, subjects, encounter_id):
    if authorizer.is_gm:
        return frozenset({"gm"})
    if authorizer.principal_kind is PrincipalKind.INTEGRATION_TOKEN:
        return frozenset({"gm_integration"})
    return frozenset({"owner", "party", "member"})


def _command(body, principal):
    return executor.execute(body, principal, route_campaign_id=body["campaign_id"],
                            content_descriptor=CONTENT, project=project, held_projections=_held)


def _receipt_and_hp(database, body, character_id):
    with database.session() as session:
        receipt = session.scalar(select(CommandReceipt).where(
            CommandReceipt.operation_id == body["operation_id"]))
        record = CharacterStateRepository(session).get(body["campaign_id"], character_id, "play")
        return receipt, record.payload["hp"]


class _Pause:
    """Wrap a function: the marked thread stops after its first call, holding its locks."""

    def __init__(self, real, session_of=lambda args: args[0]):
        self.real, self.session_of = real, session_of
        self.thread = self.pid = None
        self.reached, self.release = threading.Event(), threading.Event()

    def __call__(self, *args, **kwargs):
        result = self.real(*args, **kwargs)
        if threading.get_ident() == self.thread and not self.reached.is_set():
            self.pid = self.session_of(args).scalar(text("SELECT pg_backend_pid()"))
            self.reached.set()
            assert self.release.wait(15), "the paused transaction was never released"
        return result


def _wait_for_lock_wait(database, blocker_pid):
    """Return once a session blocked by ``blocker_pid`` waits on a lock; fail after 10 s."""

    deadline = time.monotonic() + 10
    with database.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT") as connection:
        while time.monotonic() < deadline:
            if connection.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' "
                    "AND :blocker = ANY(pg_blocking_pids(pid)))"), {"blocker": blocker_pid}):
                return
            time.sleep(0.01)
    pytest.fail("the second writer never waited on the first writer's lock")


def _interleave(database, pause, first, second):
    """``first`` runs to its pause point; ``second`` is seen waiting on it; then both finish."""

    def caught(action, mark=False):
        if mark:
            pause.thread = threading.get_ident()
        try:
            return action()
        except Exception as exc:  # assertions inspect the exact domain errors
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(caught, first, True)
        try:
            assert pause.reached.wait(10), "the first writer never reached its pause point"
            b = pool.submit(caught, second)
            _wait_for_lock_wait(database, pause.pid)
        finally:
            pause.release.set()  # at once: well inside 2 s of seeing the wait
        return a.result(timeout=30), b.result(timeout=30)


def _token_campaign(database):
    """Campaign TCID in db authority with character t1, and a minted ``["@live"]`` token."""

    with database.transaction() as session:
        session.add(Campaign(id=TCID, slug="tokens", name="Tokens", system="pf2e",
                             created_by_user_id="gm", created_at=NOW, settings={"rules": PIN}))
        session.flush()
        session.add(Character(id="t1", campaign_id=TCID, system="pf2e", display_name="t1",
                              legacy_storage="party_data", legacy_file="t1.json",
                              content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        session.add(CampaignStateAuthority(campaign_id=TCID, authority="db", stream_epoch=1,
                                           changed_at=NOW))
        session.flush()
        CharacterStateRepository(session).insert(
            TCID, "t1", "play", rules_profile="pf2e", content=CONTENT, payload={"hp": 30})
        streams.lock_stream(session, TCID)
    storage.ensure_campaign_dirs(TCID)
    raw, record = obsidian_sync.create_token(TCID, "race")
    return raw, record["id"]


def _token_revoked(database, token_id):
    """``(revoked in tokens.json, revoked in SQL)``."""

    with open(obsidian_sync.tokens_file(TCID), encoding="utf-8") as handle:
        in_file = {r["id"]: r for r in json.load(handle)["tokens"]}[token_id]["revoked_at"]
    with database.session() as session:
        in_sql = session.scalar(select(IntegrationToken.revoked_at).where(
            IntegrationToken.credential_id == token_id))
    return in_file is not None, in_sql is not None


# --------------------------------------------------------------------------
# same-invitation redemption: signup against an existing user


def test_signup_and_existing_user_redeem_same_invite_interleaved(pg, monkeypatch):
    inv = _invite(pg)
    pause = _Pause(identity._insert_user)  # A holds stream, campaign, invitation and its user
    monkeypatch.setattr(identity, "_insert_user", pause)
    signup, existing = _interleave(pg, pause, lambda: _signup(inv), lambda: _existing(inv))
    assert type(existing) is InviteUnavailableError, existing
    assert not isinstance(signup, Exception), signup
    state, new_user = _state(pg, inv), signup[0]["id"]
    assert (state.uses, state.redemptions, state.owners) == (0, 1, [new_user])
    assert new_user in state.members and inv.user not in state.members


def test_existing_user_holds_stream_then_signup_redeems_same_invite(pg, monkeypatch):
    inv = _invite(pg)
    pause = _Pause(streams.lock_stream)  # B stops right after lock_stream returns
    monkeypatch.setattr(streams, "lock_stream", pause)
    existing, signup = _interleave(pg, pause, lambda: _existing(inv), lambda: _signup(inv))
    assert type(signup) is InviteUnavailableError, signup
    assert not isinstance(existing, Exception), existing
    state = _state(pg, inv)
    assert (state.uses, state.redemptions, state.owners) == (0, 1, [inv.user])
    assert state.signup_id is None and inv.user in state.members


@pytest.mark.parametrize("uses", [1, 2], ids=["one_use_character", "two_use_plain"])
def test_signup_and_existing_user_redeem_race_completes(pg, uses):
    for _ in range(20):
        inv = _invite(pg, uses=uses, character=uses == 1)
        signup, existing = _race([lambda: _signup(inv), lambda: _existing(inv)])
        state = _state(pg, inv)
        failed = [o for o in (signup, existing) if isinstance(o, Exception)]
        if uses == 2:
            assert not failed, failed
            assert (state.uses, state.redemptions) == (0, 2)
            assert state.signup_id and {state.signup_id, inv.user} <= state.members
            continue
        assert len(failed) == 1 and type(failed[0]) is InviteUnavailableError, failed
        signup_lost = isinstance(signup, Exception)
        winner = inv.user if signup_lost else signup[0]["id"]
        assert (state.uses, state.redemptions, state.owners) == (0, 1, [winner])
        assert (state.signup_id is None) == signup_lost  # a losing signup leaves no user row


# --------------------------------------------------------------------------
# an authorization write against a command on the same campaign


@pytest.mark.parametrize("first", ["command", "writer"])
@pytest.mark.parametrize("writer", ["remove_membership", "revoke_token"])
def test_command_and_authorization_write_serialize_on_the_stream(pg, monkeypatch, writer, first):
    """Whoever holds the stream row first decides: the other waits, then sees the commit."""

    if writer == "revoke_token":
        cid, subject = TCID, "t1"
        _, token_id = _token_campaign(pg)
        principal = Principal.integration_token(token_id, campaign_id=TCID)

        def write():
            return obsidian_sync.revoke_token(TCID, token_id)
    else:
        cid, subject, principal = "A", "a1", PLAYER

        def write():
            return TransactionalStore(pg.session_factory).remove_membership(
                campaign_id="A", user_id="u1")
    body = env(cid=cid, subject=subject)
    if first == "command":
        pause = HOOKS["apply"] = _Pause(damage, lambda args: args[0].session)
        response, wrote = _interleave(pg, pause, lambda: _command(body, principal), write)
    else:
        pause = _Pause(streams.lock_stream)
        monkeypatch.setattr(streams, "lock_stream", pause)
        wrote, response = _interleave(pg, pause, write, lambda: _command(body, principal))
    assert wrote is True, wrote
    assert not isinstance(response, Exception), response
    if writer == "revoke_token":
        assert _token_revoked(pg, token_id) == (True, True)
    else:
        with pg.session() as session:
            assert session.get(CampaignMembership, ("A", "u1")) is None
    receipt, hp = _receipt_and_hp(pg, body, subject)
    if first == "command":  # committed under the authority it started with
        assert response.status == 200, response.body
        assert receipt is not None and receipt.status == "committed" and hp == 25
    else:  # the authorizer read the committed removal: refused, nothing applied
        assert response.status in (403, 404), response.body
        assert (receipt is None or receipt.status == "rejected") and hp == 30
        if writer == "revoke_token":
            assert (response.status, response.body["code"]) == (403, "not_permitted")


def test_membership_write_and_command_never_deadlock(pg):
    store = TransactionalStore(pg.session_factory)
    for i in range(100):
        role = ("gm", "player")[i % 2]
        written, response = _race([
            lambda: store.set_membership_role(campaign_id="A", user_id="u2", role=role),
            lambda: _command(env(payload={"amount": 0}), GM)])
        assert not isinstance(written, Exception), (i, written)
        assert not isinstance(response, Exception) and response.status == 200, (i, response)


def test_signup_redeem_and_command_run_concurrently_and_both_finish(pg):
    for _ in range(10):
        inv = _invite(pg)
        signup, response = _race([lambda: _signup(inv),
                                  lambda: _command(env(payload={"amount": 0}), GM)])
        assert not isinstance(signup, Exception), signup
        assert not isinstance(response, Exception) and response.status == 200, response
        assert _state(pg, inv).owners == [signup[0]["id"]]


# --------------------------------------------------------------------------
# the lock timeout


@pytest.mark.parametrize("writer", ["remove_membership", "existing_redeem", "signup_redeem"])
def test_writer_behind_a_held_stream_row_times_out_as_store_unavailable(pg, monkeypatch, writer):
    inv = _invite(pg)
    store = TransactionalStore(pg.session_factory)
    call = {
        "remove_membership": lambda: runtime.store_call(
            store.remove_membership, campaign_id="A", user_id="u2"),
        "existing_redeem": lambda: _existing(inv),
        "signup_redeem": lambda: _signup(inv),
    }[writer]
    before = _state(pg, inv)
    monkeypatch.setattr(executor, "STREAM_LOCK_TIMEOUT_MS", 200)
    with pg.transaction() as holder:
        streams.lock_stream(holder, "A")
        started = time.monotonic()
        with pytest.raises(runtime.StoreUnavailable):
            call()
        assert time.monotonic() - started < 10  # the 200 ms timeout, not the holder's release
    after = _state(pg, inv)
    assert vars(after) == vars(before)
    assert (after.uses, after.redemptions, after.owners, after.signup_id) == (1, 0, [], None)
    assert "u2" in after.members and inv.user not in after.members


def test_revoke_whose_stream_lock_times_out_leaves_file_revoked_and_sql_live(pg, monkeypatch):
    raw, token_id = _token_campaign(pg)
    assert obsidian_sync.verify_token(TCID, raw) is not None
    monkeypatch.setattr(executor, "STREAM_LOCK_TIMEOUT_MS", 200)
    with pg.transaction() as holder:
        streams.lock_stream(holder, TCID)
        with pytest.raises(obsidian_sync.TokenStoreUnavailable):  # the route answers 503
            obsidian_sync.revoke_token(TCID, token_id)
        assert _token_revoked(pg, token_id) == (True, False)
    # Fails closed meanwhile; the next bearer request records the revocation in SQL.
    assert obsidian_sync.verify_token(TCID, raw) is None
    assert _token_revoked(pg, token_id) == (True, True)
