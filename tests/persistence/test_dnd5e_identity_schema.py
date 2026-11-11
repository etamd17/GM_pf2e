"""PE-10: D&D 5e campaign and character identities (PE-01 section 1).

Model and repository cases run against ``Base.metadata`` on SQLite (the
PostgreSQL-marked cases against an isolated schema).  Migration cases drive
the real Alembic revisions in a subprocess, as ``test_game_state_schema``
does, because the Alembic environment configures logging globally.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from core.persistence import (
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Database,
    User,
    normalize_database_url,
)
from core.persistence import runtime
from core.persistence.repositories import CampaignRepository, CharacterRepository


REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
HEAD = runtime._REQUIRED_REVISION
PREVIOUS = "20261110_0003"
PID = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"


# --------------------------------------------------------------------------
# helpers


def _user(user_id):
    return User(id=user_id, username=user_id, normalized_username=user_id,
                display_name=user_id, password_hash="hash", created_at=NOW)


def _campaign(campaign_id, *, system="dnd5e"):
    return Campaign(id=campaign_id, slug=campaign_id, name=campaign_id,
                    system=system, created_by_user_id="gm", created_at=NOW)


def _character(character_id, campaign_id, *, system="dnd5e", storage="dnd5e_characters",
               legacy_file=None, archived_at=None):
    return Character(
        id=character_id, campaign_id=campaign_id, system=system,
        display_name="Ana", legacy_storage=storage,
        legacy_file=legacy_file if legacy_file is not None else character_id + ".json",
        content_checksum="0" * 64, created_at=NOW, updated_at=NOW,
        archived_at=archived_at,
    )


def _seed(database):
    with database.transaction() as session:
        session.add_all([_user("gm"), _user("pl")])
        session.flush()
        session.add_all([_campaign("c1"), _campaign("c2"), _campaign("p1", system="pf2e")])
        session.flush()
        session.add(_character(PID, "c1"))


def _refused(database, *rows, match):
    """Insert ``rows`` and require the driver's own message to match.

    SQLite names the violated CHECK; PostgreSQL names every constraint.
    SQLite reports every foreign-key failure with one generic text, so those
    cases are pinned by their fixtures, each of which breaks one key.
    """
    with pytest.raises(IntegrityError) as caught, database.transaction() as session:
        for row in rows:
            session.add(row)
            session.flush()
    message = str(caught.value.orig)
    assert re.search(match, message), message


def _check(name):
    return r"CHECK constraint failed: %s$" % re.escape(name)


_FOREIGN_KEY = r"^FOREIGN KEY constraint failed$"


# --------------------------------------------------------------------------
# schema


def test_dnd5e_identities_and_assignments_persist(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.add(CampaignMembership(campaign_id="c1", user_id="pl", role="player",
                                       character_id=PID, joined_at=NOW, updated_at=NOW))
        session.add(CharacterAssignment(campaign_id="c1", character_id=PID, user_id="pl",
                                        role="owner", created_at=NOW, updated_at=NOW))
    with sqlite_database.session() as session:
        campaign = session.get(Campaign, "c1")
        character = session.get(Character, PID)
        assignment = session.get(CharacterAssignment, ("c1", PID, "pl"))
        assert campaign.system == "dnd5e"
        assert (character.system, character.legacy_storage, character.legacy_file) == (
            "dnd5e", "dnd5e_characters", PID + ".json")
        assert assignment.role == "owner"


@pytest.mark.parametrize("row, constraint", [
    (lambda: _campaign("bad", system="dnd5e-2024"), "ck_campaigns_known_system"),
    (lambda: _character("x1", "c1", system="dnd"), "ck_characters_known_system"),
    (lambda: _character("x1", "c1", storage="dnd5e_pcs"),
     "ck_characters_known_legacy_storage"),
    # D&D in another store, and another system in the D&D store.
    (lambda: _character("x1", "c1", storage="party_data"), "ck_characters_dnd5e_locator"),
    (lambda: _character("x1", "c1", system="pf2e"), "ck_characters_dnd5e_locator"),
    (lambda: _character("x1", "c1", system="cosmere"), "ck_characters_dnd5e_locator"),
    # The file must be exactly <id>.json: not a display name, path or suffix.
    (lambda: _character("x1", "c1", legacy_file="Ana.json"), "ck_characters_dnd5e_locator"),
    (lambda: _character("x1", "c1", legacy_file="characters/x1.json"),
     "ck_characters_dnd5e_locator"),
    (lambda: _character("x1", "c1", legacy_file="x1"), "ck_characters_dnd5e_locator"),
    (lambda: _character("x1", "c1", legacy_file="X1.json"), "ck_characters_dnd5e_locator"),
    # Archived rows keep the same locator rule (PE-01 section 7).
    (lambda: _character("x1", "c1", legacy_file="Ana.json", archived_at=NOW),
     "ck_characters_dnd5e_locator"),
])
def test_schema_refuses_invalid_dnd5e_identities(sqlite_database, row, constraint):
    _seed(sqlite_database)
    _refused(sqlite_database, row(), match=_check(constraint))


def test_existing_systems_keep_their_stores(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.add(_character("pf", "p1", system="pf2e", storage="party_data",
                               legacy_file="Ana.json"))
        session.add(_character("co", "p1", system="cosmere", storage="cosmere_pcs",
                               legacy_file="anything.json"))
    with sqlite_database.session() as session:
        assert session.get(Character, "pf").legacy_file == "Ana.json"


def test_dnd5e_locator_cannot_be_held_twice(sqlite_database):
    _seed(sqlite_database)
    # The file names the id, so a second live row for the same file is
    # either the same primary key or a locator that breaks the D&D rule.
    _refused(sqlite_database, _character(PID, "c1"), match=r"UNIQUE constraint failed")
    _refused(sqlite_database, _character("other", "c1", legacy_file=PID + ".json"),
             match=_check("ck_characters_dnd5e_locator"))


def test_assignment_must_use_the_characters_campaign(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        # pl is a member of c2, so the only key this assignment can break is
        # (campaign_id, character_id) -> characters.
        session.add(CampaignMembership(campaign_id="c2", user_id="pl", role="player",
                                       joined_at=NOW, updated_at=NOW))
    _refused(sqlite_database,
             CharacterAssignment(campaign_id="c2", character_id=PID, user_id="pl",
                                 role="owner", created_at=NOW, updated_at=NOW),
             match=_FOREIGN_KEY)
    # Likewise a c2 membership naming c1's character (gm has no c2 membership).
    _refused(sqlite_database,
             CampaignMembership(campaign_id="c2", user_id="gm", role="player",
                                character_id=PID, joined_at=NOW, updated_at=NOW),
             match=_FOREIGN_KEY)


# --------------------------------------------------------------------------
# repositories


def _identity(repository, **overrides):
    values = dict(character_id=PID, campaign_id="c1", system="dnd5e",
                  legacy_storage="dnd5e_characters", legacy_file=PID + ".json",
                  content_checksum="1" * 64, display_name="Ana")
    values.update(overrides)
    return repository.upsert_identity(**values)


def _seed_campaigns(database):
    with database.transaction() as session:
        session.add_all([_user("gm"), _user("pl")])
        session.flush()
        repository = CampaignRepository(session)
        for campaign_id, system in (("c1", "dnd5e"), ("c2", "dnd5e"), ("p1", "pf2e")):
            repository.upsert(campaign_id=campaign_id, slug=campaign_id, name=campaign_id,
                              system=system, created_by_user_id="gm")


def test_repository_creates_looks_up_and_updates_a_dnd5e_identity(sqlite_database):
    _seed_campaigns(sqlite_database)
    with sqlite_database.transaction() as session:
        repository = CharacterRepository(session)
        _identity(repository)
        CampaignRepository(session).upsert_membership(
            campaign_id="c1", user_id="pl", role="player", character_id=PID)
        repository.set_assignment(campaign_id="c1", character_id=PID, user_id="pl",
                                  role="owner")
    with sqlite_database.transaction() as session:
        repository = CharacterRepository(session)
        assert repository.get(PID).legacy_file == PID + ".json"
        _identity(repository, display_name="Ana Vale", content_checksum="2" * 64)
    with sqlite_database.session() as session:
        stored = session.get(Character, PID)
        assert (stored.display_name, stored.content_checksum) == ("Ana Vale", "2" * 64)
        assert session.get(CharacterAssignment, ("c1", PID, "pl")).role == "owner"
        assert session.get(Campaign, "c1").system == "dnd5e"


@pytest.mark.parametrize("overrides, message", [
    ({"system": "dnd"}, "unknown character system"),
    ({"legacy_storage": "dnd5e_pcs"}, "unknown legacy character store"),
    ({"legacy_storage": "party_data"}, "only they, use dnd5e_characters"),
    ({"system": "pf2e"}, "only they, use dnd5e_characters"),
    ({"legacy_file": "Ana.json"}, "legacy_file must be the character id"),
    ({"legacy_file": "dnd5e/characters/%s.json" % PID}, "legacy_file must be the character id"),
    ({"character_id": "x" * 65, "legacy_file": "x" * 65 + ".json"},
     "invalid D&D character id"),
    ({"character_id": "../x", "legacy_file": "../x.json"}, "invalid D&D character id"),
    ({"character_id": "a.b", "legacy_file": "a.b.json"}, "invalid D&D character id"),
    ({"character_id": "", "legacy_file": ".json"}, "invalid D&D character id"),
    ({"character_id": "ab\n", "legacy_file": "ab\n.json"}, "invalid D&D character id"),
])
def test_repository_refuses_invalid_dnd5e_identities(sqlite_database, overrides, message):
    _seed_campaigns(sqlite_database)
    with pytest.raises(ValueError, match=re.escape(message)), \
            sqlite_database.transaction() as session:
        _identity(CharacterRepository(session), **overrides)
    with sqlite_database.session() as session:
        assert session.query(Character).count() == 0


def test_repository_accepts_the_longest_valid_dnd5e_id(sqlite_database):
    _seed_campaigns(sqlite_database)
    longest = "A-_9" * 16
    with sqlite_database.transaction() as session:
        _identity(CharacterRepository(session), character_id=longest,
                  legacy_file=longest + ".json")
    with sqlite_database.session() as session:
        assert session.get(Character, longest).legacy_file == longest + ".json"


@pytest.mark.parametrize("overrides", [
    {"campaign_id": "c2"},
    {"campaign_id": "p1"},
    {"system": "pf2e", "legacy_storage": "party_data", "legacy_file": "Ana.json"},
])
def test_identity_cannot_move_campaign_or_system(sqlite_database, overrides):
    _seed_campaigns(sqlite_database)
    with sqlite_database.transaction() as session:
        _identity(CharacterRepository(session))
    with pytest.raises(ValueError, match="already exists in another campaign or system"), \
            sqlite_database.transaction() as session:
        _identity(CharacterRepository(session), **overrides)
    with sqlite_database.session() as session:
        stored = session.get(Character, PID)
        assert (stored.campaign_id, stored.system, stored.legacy_storage) == (
            "c1", "dnd5e", "dnd5e_characters")


def test_existing_identity_cannot_move_campaign_in_any_system(sqlite_database):
    _seed_campaigns(sqlite_database)
    pf2e = dict(character_id="pf", campaign_id="p1", system="pf2e",
                legacy_storage="party_data", legacy_file="Ana.json")
    with sqlite_database.transaction() as session:
        _identity(CharacterRepository(session), **pf2e)
    with pytest.raises(ValueError, match="already exists in another campaign or system"), \
            sqlite_database.transaction() as session:
        _identity(CharacterRepository(session), **{**pf2e, "campaign_id": "c1"})


def test_campaign_repository_accepts_exactly_the_three_systems(sqlite_database):
    _seed_campaigns(sqlite_database)
    with sqlite_database.transaction() as session:
        repository = CampaignRepository(session)
        repository.upsert(campaign_id="co", slug="co", name="co", system="cosmere")
        for system in ("dnd5e-2014", "5e", "DND5E", ""):
            with pytest.raises(ValueError, match="unknown campaign system"):
                repository.upsert(campaign_id="zz", slug="zz", name="zz", system=system)


# --------------------------------------------------------------------------
# migration


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


_TS = "'2026-11-01 00:00:00'"
_PREVIOUS_ROWS = [
    "INSERT INTO users (id, username, normalized_username, display_name, password_hash,"
    " is_admin, session_version, created_at, source_payload) VALUES"
    " ('gm', 'gm', 'gm', 'GM', 'h', {false}, 0, " + _TS + ", '{}')",
    "INSERT INTO users (id, username, normalized_username, display_name, password_hash,"
    " is_admin, session_version, created_at, source_payload) VALUES"
    " ('pl', 'pl', 'pl', 'PL', 'h', {false}, 0, " + _TS + ", '{}')",
    "INSERT INTO campaigns (id, slug, name, system, created_by_user_id, created_at,"
    " session_number, settings, source_payload, source_checksum) VALUES"
    " ('p1', 's', 'P', 'pf2e', 'gm', " + _TS + ", 3, '{\"a\": 1}', '{}', 'cs-p1')",
    "INSERT INTO campaigns (id, slug, name, system, created_by_user_id, created_at,"
    " session_number, settings, source_payload, source_checksum) VALUES"
    " ('k1', 'k', 'K', 'cosmere', 'gm', " + _TS + ", 1, '{}', '{}', 'cs-k1')",
    "INSERT INTO characters (id, campaign_id, system, display_name, legacy_storage,"
    " legacy_file, content_checksum, created_at, updated_at, archived_at) VALUES"
    " ('pc1', 'p1', 'pf2e', 'Ana', 'party_data', 'Ana.json', 'sum-pc1', " + _TS + ", "
    + _TS + ", NULL)",
    "INSERT INTO characters (id, campaign_id, system, display_name, legacy_storage,"
    " legacy_file, content_checksum, created_at, updated_at, archived_at) VALUES"
    " ('pc0', 'p1', 'pf2e', 'Ana', 'party_data', 'Ana.json', 'sum-pc0', " + _TS + ", "
    + _TS + ", " + _TS + ")",
    "INSERT INTO characters (id, campaign_id, system, display_name, legacy_storage,"
    " legacy_file, content_checksum, created_at, updated_at, archived_at) VALUES"
    " ('kc1', 'k1', 'cosmere', 'Kal', 'cosmere_pcs', 'kc1.json', 'sum-kc1', " + _TS + ", "
    + _TS + ", NULL)",
    "INSERT INTO campaign_memberships (campaign_id, user_id, role, character_id,"
    " joined_at, updated_at) VALUES ('p1', 'pl', 'player', 'pc1', " + _TS + ", " + _TS + ")",
    "INSERT INTO character_assignments (campaign_id, character_id, user_id, role,"
    " created_at, updated_at) VALUES ('p1', 'pc1', 'pl', 'owner', " + _TS + ", " + _TS + ")",
]
_SNAPSHOT = {
    "campaigns": "SELECT * FROM campaigns ORDER BY id",
    "characters": "SELECT * FROM characters ORDER BY id",
    "campaign_memberships": "SELECT * FROM campaign_memberships ORDER BY campaign_id, user_id",
    "character_assignments": "SELECT * FROM character_assignments ORDER BY character_id",
}


def _seed_previous(engine, *, false="0"):
    with engine.begin() as connection:
        for statement in _PREVIOUS_ROWS:
            connection.execute(text(statement.replace("{false}", false)))


def _snapshot(engine):
    with engine.connect() as connection:
        return {name: [tuple(row) for row in connection.execute(text(query))]
                for name, query in _SNAPSHOT.items()}


def _sqlite_ddl(engine, name):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT sql FROM sqlite_master WHERE name = :name"), {"name": name}).scalar()


_DND5E_CAMPAIGN = (
    "INSERT INTO campaigns (id, slug, name, system, created_at, session_number, settings,"
    " source_payload) VALUES ('d1', 'd', 'D', 'dnd5e', " + _TS + ", 1, '{}', '{}')")


def _dnd5e_character(character_id="pid1", campaign_id="d1", legacy_file=None,
                     system="dnd5e", storage="dnd5e_characters"):
    return (
        "INSERT INTO characters (id, campaign_id, system, legacy_storage, legacy_file,"
        " content_checksum, created_at, updated_at) VALUES ('%s', '%s', '%s', '%s', '%s',"
        " 'x', %s, %s)" % (character_id, campaign_id, system, storage,
                           legacy_file or character_id + ".json", _TS, _TS))


def test_sqlite_upgrade_preserves_rows_and_the_pe12_locator_index(sqlite_url):
    _alembic(sqlite_url, "upgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    try:
        _seed_previous(engine)
        before = _snapshot(engine)
        index_before = _sqlite_ddl(engine, "uq_characters_active_legacy_locator")
        assert index_before.endswith("WHERE archived_at IS NULL")
        _alembic(sqlite_url, "upgrade", "head")
        assert _snapshot(engine) == before
        assert _sqlite_ddl(engine, "uq_characters_active_legacy_locator") == index_before
        assert _sqlite_ddl(engine, "ix_characters_campaign_id") is not None
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == HEAD
        with engine.begin() as connection:
            connection.execute(text(_DND5E_CAMPAIGN))
            connection.execute(text(_dnd5e_character()))
        for statement in (
            _dnd5e_character("pid2", legacy_file="Ana.json"),
            _dnd5e_character("pid2", storage="party_data"),
            _dnd5e_character("pid2", system="pf2e"),
            _dnd5e_character("pid2", system="dnd"),
            # PE-12's live-locator uniqueness still holds after the rebuild.
            _dnd5e_character("pc9", "p1", system="pf2e", storage="party_data",
                             legacy_file="Ana.json"),
        ):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(text(statement))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
            connection.execute(text(
                "INSERT INTO character_assignments (campaign_id, character_id, user_id, role,"
                " created_at, updated_at) VALUES ('p1', 'pid1', 'pl', 'owner', "
                + _TS + ", " + _TS + ")"))
    finally:
        engine.dispose()


def test_sqlite_empty_downgrade_restores_previous_checks(sqlite_url):
    _alembic(sqlite_url, "upgrade", PREVIOUS)
    engine = create_engine(sqlite_url)
    try:
        previous_ddl = {name: _sqlite_ddl(engine, name) for name in (
            "campaigns", "characters", "uq_characters_active_legacy_locator")}
        _seed_previous(engine)
        before = _snapshot(engine)
        _alembic(sqlite_url, "upgrade", "head")
        _alembic(sqlite_url, "downgrade", PREVIOUS)
        assert _snapshot(engine) == before
        # Same index; the tables' checks match the previous revision's.
        assert _sqlite_ddl(engine, "uq_characters_active_legacy_locator") == (
            previous_ddl["uq_characters_active_legacy_locator"])
        for table in ("campaigns", "characters"):
            ddl = _sqlite_ddl(engine, table)
            assert "dnd5e" not in ddl
            assert sorted(re.findall(r"CONSTRAINT (\w+)", ddl)) == sorted(
                re.findall(r"CONSTRAINT (\w+)", previous_ddl[table]))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(_DND5E_CAMPAIGN))
    finally:
        engine.dispose()
    _alembic(sqlite_url, "upgrade", "head")


@pytest.mark.parametrize("statements, refusal", [
    ([_DND5E_CAMPAIGN], "1 D&D campaign(s) and 0 D&D character(s)"),
    ([_DND5E_CAMPAIGN, _dnd5e_character()], "1 D&D campaign(s) and 1 D&D character(s)"),
    ([_dnd5e_character(campaign_id="p1")], "0 D&D campaign(s) and 1 D&D character(s)"),
])
def test_downgrade_refuses_to_discard_dnd5e_rows(sqlite_url, statements, refusal):
    _alembic(sqlite_url, "upgrade", "head")
    engine = create_engine(sqlite_url)
    try:
        _seed_previous(engine)
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
        before = _snapshot(engine)
        ddl_before = _sqlite_ddl(engine, "characters")
        completed = _alembic(sqlite_url, "downgrade", PREVIOUS, check=False)
        assert completed.returncode != 0
        assert "RuntimeError: downgrade refused: " + refusal in completed.stderr
        assert _snapshot(engine) == before
        assert _sqlite_ddl(engine, "characters") == ddl_before
        with engine.connect() as connection:
            assert connection.execute(text(
                "SELECT version_num FROM alembic_version")).scalar() == HEAD
            assert connection.execute(text(
                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")
            ).scalar() == 0
    finally:
        engine.dispose()


def test_offline_downgrade_is_refused(sqlite_url):
    completed = _alembic(sqlite_url, "downgrade", "%s:%s" % (HEAD, PREVIOUS), "--sql",
                         check=False)
    assert completed.returncode != 0
    assert "requires an online check for D&D rows" in completed.stderr


def test_runtime_readiness_pin_is_this_revision_and_the_single_head():
    completed = _alembic("sqlite+pysqlite:///:memory:", "heads")
    heads = [line.split()[0] for line in completed.stdout.splitlines() if line.strip()]
    assert heads == [HEAD] == ["20261111_0004"]


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
    schema = "gm_pf2e_pe10_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield url, schema
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.mark.postgresql
def test_postgresql_dnd5e_checks(postgres_schema):
    url, schema = postgres_schema
    database = Database(url, engine_options={
        "connect_args": {"options": f"-csearch_path={schema}"}})
    try:
        database.create_schema()
        _seed(database)
        for row, constraint in (
            (_character("x1", "c1", legacy_file="Ana.json"), "ck_characters_dnd5e_locator"),
            (_character("x1", "c1", system="pf2e"), "ck_characters_dnd5e_locator"),
            # A non-D&D system, so only the store check can fail.
            (_character("x1", "c1", system="pf2e", storage="elsewhere"),
             "ck_characters_known_legacy_storage"),
            (_campaign("bad", system="dnd5e-2024"), "ck_campaigns_known_system"),
        ):
            _refused(database, row, match=r'constraint "%s"' % re.escape(constraint))
    finally:
        database.dispose()


@pytest.mark.postgresql
def test_postgresql_upgrade_downgrade_round_trip(postgres_schema):
    url, schema = postgres_schema
    separator = "&" if "?" in url else "?"
    migrated_url = f"{url}{separator}options=-csearch_path%3D{schema}"
    _alembic(migrated_url, "upgrade", PREVIOUS)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        _seed_previous(engine, false="false")
        before = _snapshot(engine)
        _alembic(migrated_url, "upgrade", "head")
        assert _snapshot(engine) == before
        with engine.connect() as connection:
            predicate = connection.execute(text(
                "SELECT pg_get_expr(i.indpred, i.indrelid) FROM pg_index i"
                " JOIN pg_class c ON c.oid = i.indexrelid"
                " WHERE c.relname = 'uq_characters_active_legacy_locator'")).scalar()
        assert predicate == "(archived_at IS NULL)"
        with engine.begin() as connection:
            connection.execute(text(_DND5E_CAMPAIGN))
            connection.execute(text(_dnd5e_character()))
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(_dnd5e_character("pid2", legacy_file="Ana.json")))
        completed = _alembic(migrated_url, "downgrade", PREVIOUS, check=False)
        assert completed.returncode != 0
        assert "downgrade refused: 1 D&D campaign(s) and 1 D&D character(s)" in (
            completed.stderr)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM campaigns WHERE id = 'd1'"))
        _alembic(migrated_url, "downgrade", PREVIOUS)
        assert _snapshot(engine) == before
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(_DND5E_CAMPAIGN))
        _alembic(migrated_url, "upgrade", "head")
    finally:
        engine.dispose()
