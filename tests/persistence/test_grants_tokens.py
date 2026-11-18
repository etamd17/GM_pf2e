"""Grants and integration-token records: schema, migration and grant helpers (PE-14c)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from core.persistence import (
    Campaign,
    CampaignGrant,
    Character,
    Database,
    IntegrationToken,
    User,
    normalize_database_url,
    runtime,
)
from core.persistence import grants

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
HEAD = "20261118_0006"
PE14A = "20261115_0005"
DIGEST = "a" * 64


def _seed(database, *campaigns):
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm", display_name="gm",
                         password_hash="h", created_at=NOW))
        session.flush()
        for campaign_id in campaigns:
            session.add(Campaign(id=campaign_id, slug=campaign_id, name=campaign_id,
                                 system="pf2e", created_by_user_id="gm", created_at=NOW))
            session.flush()
            session.add(Character(id="c1", campaign_id=campaign_id, system="pf2e",
                                  legacy_storage="party_data", legacy_file="c1.json",
                                  content_checksum="x"))


def _grant(kind="level_unlock", grant_id=None, **fields):
    values = dict(grant_id=grant_id or uuid.uuid4().hex, campaign_id="A", kind=kind,
                  issued_by_kind="user", issued_by_id="gm", issued_at=NOW)
    values.update({"level_unlock": dict(character_id="c1", target_level=2),
                   "retrain_grant": dict(character_id="c1", build_revision=3),
                   "quartermaster": dict(user_id="u1")}.get(kind, {}))
    values.update(fields)
    return CampaignGrant(**values)


def _token(credential_id="t1", campaign_id="A", **fields):
    values = dict(credential_id=credential_id, campaign_id=campaign_id, label="Obsidian",
                  token_digest=DIGEST, last_four="abcd", created_at=NOW)
    values.update(fields)
    return IntegrationToken(**values)


def _refused(database, row, match):
    with pytest.raises(IntegrityError) as caught, database.transaction() as session:
        session.add(row)
        session.flush()
    assert re.search(match, str(caught.value.orig)), str(caught.value.orig)


# --------------------------------------------------------------------------
# Grant rows


@pytest.mark.parametrize("kind", ["quartermaster", "level_unlock", "retrain_grant"])
def test_each_grant_kind_inserts_with_its_columns(sqlite_database, kind):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add(_grant(kind))


@pytest.mark.parametrize("kind, fields", [
    ("quartermaster", {"user_id": None}),
    ("quartermaster", {"character_id": "c1"}),
    ("level_unlock", {"character_id": None}),
    ("level_unlock", {"target_level": None}),
    ("level_unlock", {"build_revision": 1}),
    ("retrain_grant", {"build_revision": None}),
    ("retrain_grant", {"character_id": None}),
    ("retrain_grant", {"user_id": "u1"}),
])
def test_grant_kind_requires_exactly_its_columns(sqlite_database, kind, fields):
    _seed(sqlite_database, "A")
    _refused(sqlite_database, _grant(kind, **fields), "kind_fields")


@pytest.mark.parametrize("fields, constraint", [
    ({"kind": "other", "character_id": "c1", "target_level": 2}, "known_kind"),
    ({"grant_id": "short"}, "grant_id_length"),
    ({"target_level": 0}, "target_level_positive"),
    ({"issued_by_kind": "legacy_gm"}, "known_issuer_kind"),
    ({"consumed_at": NOW}, "consumed_pair"),
    ({"consumed_by_operation_id": "op-1"}, "consumed_pair"),
])
def test_grant_checks_refuse(sqlite_database, fields, constraint):
    _seed(sqlite_database, "A")
    kind = fields.pop("kind", "level_unlock")
    row = _grant("level_unlock", **fields)
    row.kind = kind
    _refused(sqlite_database, row, constraint)


def test_grant_character_must_belong_to_the_campaign(sqlite_database):
    _seed(sqlite_database, "A")
    _refused(sqlite_database, _grant(character_id="other"), "FOREIGN KEY")


def test_one_live_quartermaster_per_user(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add(_grant("quartermaster"))
    _refused(sqlite_database, _grant("quartermaster"), "UNIQUE")
    with sqlite_database.transaction() as session:  # revoked and consumed rows do not count
        session.add(_grant("quartermaster", revoked_at=NOW, user_id="u2"))
        session.add(_grant("quartermaster", user_id="u2"))
        session.add(_grant("quartermaster", user_id="u3", consumed_at=NOW,
                           consumed_by_operation_id="op-1"))
        session.add(_grant("quartermaster", user_id="u3"))


def test_consume_succeeds_once_and_writes_nothing_after(sqlite_database):
    _seed(sqlite_database, "A")
    gid = uuid.uuid4().hex
    with sqlite_database.transaction() as session:
        session.add(_grant(grant_id=gid))
    with sqlite_database.transaction() as session:
        grants.consume(session, gid, "op-first")
    with pytest.raises(grants.GrantNotLive), sqlite_database.transaction() as session:
        grants.consume(session, gid, "op-second")
    with sqlite_database.transaction() as session:
        row = session.get(CampaignGrant, gid)
        assert row.consumed_by_operation_id == "op-first" and row.consumed_at is not None


def test_consume_of_revoked_or_missing_grant_raises_and_writes_nothing(sqlite_database):
    _seed(sqlite_database, "A")
    gid = uuid.uuid4().hex
    with sqlite_database.transaction() as session:
        session.add(_grant(grant_id=gid, revoked_at=NOW))
    for target in (gid, uuid.uuid4().hex):
        with pytest.raises(grants.GrantNotLive), sqlite_database.transaction() as session:
            grants.consume(session, target, "op-1")
    with sqlite_database.transaction() as session:
        row = session.get(CampaignGrant, gid)
        assert row.consumed_at is None and row.consumed_by_operation_id is None


def test_consume_refuses_a_quartermaster_grant(sqlite_database):
    _seed(sqlite_database, "A")
    gid = uuid.uuid4().hex
    with sqlite_database.transaction() as session:
        session.add(_grant("quartermaster", grant_id=gid))
    with pytest.raises(grants.GrantNotLive), sqlite_database.transaction() as session:
        grants.consume(session, gid, "op-1")
    with sqlite_database.transaction() as session:
        assert session.get(CampaignGrant, gid).consumed_at is None


def test_consume_is_campaign_scoped_when_asked(sqlite_database):
    _seed(sqlite_database, "A")
    gid = uuid.uuid4().hex
    with sqlite_database.transaction() as session:
        session.add(_grant(grant_id=gid))
    with pytest.raises(grants.GrantNotLive), sqlite_database.transaction() as session:
        grants.consume(session, gid, "op-1", campaign_id="B")


def test_live_unlock_and_retrain_match_exactly_and_only_live_rows(sqlite_database):
    _seed(sqlite_database, "A")
    live_unlock, live_retrain = uuid.uuid4().hex, uuid.uuid4().hex
    with sqlite_database.transaction() as session:
        session.add(_grant(grant_id=live_unlock, target_level=3))
        session.add(_grant(target_level=4, revoked_at=NOW))
        session.add(_grant(target_level=5, consumed_at=NOW, consumed_by_operation_id="op"))
        session.add(_grant("retrain_grant", grant_id=live_retrain, build_revision=7))
        session.add(_grant("retrain_grant", build_revision=8, revoked_at=NOW))
    with sqlite_database.transaction() as session:
        assert grants.live_level_unlock(session, "A", "c1", 3)["grant_id"] == live_unlock
        for level in (2, 4, 5):
            assert grants.live_level_unlock(session, "A", "c1", level) is None
        assert grants.live_level_unlock(session, "B", "c1", 3) is None
        assert grants.live_retrain(session, "A", "c1", 7)["grant_id"] == live_retrain
        assert grants.live_retrain(session, "A", "c1", 8) is None
        assert grants.live_retrain(session, "A", "c1", 6) is None
        assert [g["grant_id"] for g in grants.live_grants(session, "A")] == sorted(
            [live_unlock, live_retrain])


# --------------------------------------------------------------------------
# Token rows


def test_token_row_defaults_and_checks(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add(_token())
    with sqlite_database.transaction() as session:
        row = session.get(IntegrationToken, "t1")
        assert row.scope == ["@live"] and row.revoked_at is None
        assert row.replaces_credential_id is None and row.minted_by_user_id is None
    _refused(sqlite_database, _token("t2", token_digest="short"), "token_digest_length")
    _refused(sqlite_database, _token("t3", campaign_id="missing"), "FOREIGN KEY")


def test_deleting_the_campaign_drops_its_grants_and_tokens(sqlite_database):
    _seed(sqlite_database, "A")
    with sqlite_database.transaction() as session:
        session.add(_grant())
        session.add(_token())
    with sqlite_database.transaction() as session:
        session.execute(text("DELETE FROM character_assignments"))
        session.execute(text("DELETE FROM characters"))
        session.execute(text("DELETE FROM campaigns WHERE id = 'A'"))
    with sqlite_database.transaction() as session:
        assert session.scalar(select(CampaignGrant.grant_id)) is None
        assert session.scalar(select(IntegrationToken.credential_id)) is None


# --------------------------------------------------------------------------
# Migration


def _alembic(url, *args, check=True):
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=REPO_ROOT, env=environment,
        check=check, capture_output=True, text=True, encoding="utf-8",
    )


@pytest.fixture
def sqlite_url(tmp_path):
    return "sqlite+pysqlite:///" + (tmp_path / "migrated.sqlite3").as_posix()


def _tables(url):
    engine = create_engine(url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _execute(url, *statements):
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    finally:
        engine.dispose()


def _version(url):
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()


_SEED_SQL = (
    "INSERT INTO users (id, username, normalized_username, display_name, password_hash,"
    " is_admin, session_version, created_at, source_payload) VALUES ('gm', 'gm', 'gm', 'gm',"
    " 'h', false, 0, '2026-11-18', '{}')",
    "INSERT INTO campaigns (id, slug, name, system, created_by_user_id, created_at,"
    " session_number, settings, source_payload) VALUES ('c1', 'c1', 'c1', 'pf2e', 'gm',"
    " '2026-11-18', 0, '{}', '{}')",
)
_GRANT_SQL = ("INSERT INTO campaign_grants (grant_id, campaign_id, kind, user_id, issued_by_kind,"
              " issued_by_id, issued_at) VALUES ('" + "g" * 32 + "', 'c1', 'quartermaster',"
              " 'u1', 'user', 'gm', '2026-11-18')")
_TOKEN_SQL = ("INSERT INTO integration_tokens (credential_id, campaign_id, label, token_digest,"
              " last_four, created_at) VALUES ('t1', 'c1', 'Obsidian', '" + DIGEST + "', 'abcd',"
              " '2026-11-18')")


def test_runtime_readiness_pin_is_this_revision_and_the_single_head():
    completed = _alembic("sqlite+pysqlite:///:memory:", "heads")
    heads = [line.split()[0] for line in completed.stdout.splitlines() if line.strip()]
    assert heads == [runtime._REQUIRED_REVISION] == [HEAD]


def test_migration_adds_exactly_two_tables_and_round_trips(sqlite_url):
    _alembic(sqlite_url, "upgrade", PE14A)
    before = _tables(sqlite_url)
    _alembic(sqlite_url, "upgrade", "head")
    assert _tables(sqlite_url) - before == {"campaign_grants", "integration_tokens"}
    assert _version(sqlite_url) == HEAD
    _alembic(sqlite_url, "check")
    _alembic(sqlite_url, "downgrade", PE14A)  # empty tables: allowed
    assert _tables(sqlite_url) == before and _version(sqlite_url) == PE14A
    _alembic(sqlite_url, "upgrade", "head")
    assert _version(sqlite_url) == HEAD


@pytest.mark.parametrize("statement, refusal", [
    (_GRANT_SQL, "1 campaign grants exist"),
    (_TOKEN_SQL, "1 integration token records exist"),
])
def test_populated_downgrade_is_refused(sqlite_url, statement, refusal):
    _alembic(sqlite_url, "upgrade", "head")
    _execute(sqlite_url, *_SEED_SQL, statement)
    completed = _alembic(sqlite_url, "downgrade", PE14A, check=False)
    assert completed.returncode != 0
    errors = [line for line in completed.stderr.splitlines() if line.startswith("RuntimeError: ")]
    assert errors and "downgrade refused: " + refusal in errors[-1], completed.stderr
    assert _version(sqlite_url) == HEAD
    assert {"campaign_grants", "integration_tokens"} <= _tables(sqlite_url)


def test_offline_downgrade_is_refused(sqlite_url):
    completed = _alembic(sqlite_url, "downgrade", f"{HEAD}:{PE14A}", "--sql", check=False)
    assert completed.returncode != 0
    assert "requires an online check for grant and token rows" in completed.stderr


# --------------------------------------------------------------------------
# PostgreSQL


def _postgresql_url():
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL schema tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql"
    return url


@pytest.fixture
def postgres_schema():
    url = _postgresql_url()
    schema = "gm_pf2e_pe14c_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url, schema
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def _schema_url(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options=-csearch_path%3D{schema}"


@pytest.mark.postgresql
def test_postgresql_grants_and_tokens_round_trip(postgres_schema):
    url, schema = postgres_schema
    migrated = _schema_url(url, schema)
    _alembic(migrated, "upgrade", PE14A)
    _alembic(migrated, "upgrade", "head")
    _alembic(migrated, "check")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            for statement in (*_SEED_SQL, _GRANT_SQL, _TOKEN_SQL):
                connection.execute(text(statement))
        completed = _alembic(migrated, "downgrade", PE14A, check=False)
        assert completed.returncode != 0
        assert "downgrade refused: 1 campaign grants exist" in completed.stderr
        with engine.begin() as connection:
            assert connection.execute(text(
                "SELECT scope FROM integration_tokens")).scalar() == ["@live"]
            connection.execute(text("DELETE FROM campaign_grants"))
            connection.execute(text("DELETE FROM integration_tokens"))
        _alembic(migrated, "downgrade", PE14A)
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == PE14A
        _alembic(migrated, "upgrade", "head")
    finally:
        engine.dispose()


@pytest.mark.postgresql
def test_postgresql_one_live_quartermaster_and_consume(postgres_schema):
    url, schema = postgres_schema
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        _seed(database, "A")
        gid = uuid.uuid4().hex
        with database.transaction() as session:
            session.add(_grant("quartermaster", grant_id=gid))
        _refused(database, _grant("quartermaster"), "uq_campaign_grants_one_live_quartermaster")
        with pytest.raises(grants.GrantNotLive), database.transaction() as session:
            grants.consume(session, gid, "op-1")  # a QM grant is never consumed
        with database.transaction() as session:
            session.get(CampaignGrant, gid).revoked_at = NOW
        with database.transaction() as session:  # revoked: a new live QM grant is allowed
            session.add(_grant("quartermaster"))
        unlock = uuid.uuid4().hex
        with database.transaction() as session:
            session.add(_grant(grant_id=unlock))
        with database.transaction() as session:
            grants.consume(session, unlock, "op-2")
        with pytest.raises(grants.GrantNotLive), database.transaction() as session:
            grants.consume(session, unlock, "op-3")
    finally:
        database.dispose()
