"""PE-13a on PostgreSQL: lock order and compare-and-set under real concurrency."""

from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from core.persistence import (
    Campaign,
    CampaignClockRepository,
    CampaignStateAuthority,
    Character,
    CharacterStateRepository,
    CommandLocks,
    Database,
    EncounterStateRepository,
    RevisionConflict,
    User,
    normalize_database_url,
)

pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}


@pytest.fixture
def postgres_database():
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL state repository tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql", (
        "DATABASE_URL must point to PostgreSQL"
    )
    schema = "gm_pf2e_test_" + uuid.uuid4().hex
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(
        url, engine_options={"connect_args": {"options": f"-csearch_path={schema}"}}
    )
    try:
        database.create_schema()
        yield database
    finally:
        database.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed(database):
    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm",
                         display_name="gm", password_hash="h", created_at=NOW))
        session.flush()
        session.add(Campaign(id="A", slug="A", name="A", system="pf2e",
                             created_by_user_id="gm", created_at=NOW,
                             settings={"rules": {"profile": "pf2e", "content": CONTENT}}))
        session.flush()
        session.add(CampaignStateAuthority(campaign_id="A", authority="db",
                                           stream_epoch=1, changed_at=NOW))
        for character_id in ("ca", "cb"):
            session.add(Character(id=character_id, campaign_id="A", system="pf2e",
                                  display_name=character_id, legacy_storage="party_data",
                                  legacy_file=character_id + ".json",
                                  content_checksum="0" * 64, created_at=NOW,
                                  updated_at=NOW))
        session.flush()
        for character_id in ("ca", "cb"):
            CharacterStateRepository(session).insert(
                "A", character_id, "play", rules_profile="pf2e", content=CONTENT,
                payload={"hp": 10})
        EncounterStateRepository(session).insert("A", "e1", rules_profile="pf2e",
                                                 content=CONTENT)
        CampaignClockRepository(session).insert("A", "world")


def _race(callables):
    barrier = threading.Barrier(len(callables))

    def run(callable_):
        barrier.wait(timeout=10)
        try:
            return callable_()
        except Exception as exc:  # assertions inspect the exact errors
            return exc

    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        futures = [executor.submit(run, callable_) for callable_ in callables]
        return [future.result(timeout=60) for future in futures]


def _no_stream(session, campaign_id):
    # A LockStream that locks nothing, so the two transactions below really
    # contend on the character_states rows (a stream stub that locked a
    # shared row would serialize them and prove nothing about step 4).
    return None


_PROBES = {
    "character_states": "SELECT 1 FROM character_states WHERE campaign_id = 'A' "
                        "AND character_id = 'ca' AND domain = 'play' FOR UPDATE NOWAIT",
    "encounter_states": "SELECT 1 FROM encounter_states WHERE campaign_id = 'A' "
                        "AND encounter_id = 'e1' FOR UPDATE NOWAIT",
    "campaign_clocks": "SELECT 1 FROM campaign_clocks WHERE campaign_id = 'A' "
                       "AND clock = 'world' FOR UPDATE NOWAIT",
}


def _probe(database, table):
    """True when another connection can take the row lock right now."""

    with database.session() as session:
        try:
            session.execute(text(_PROBES[table]))
            return True
        except OperationalError as exc:
            assert "could not obtain lock" in str(exc).lower(), exc
            return False
        finally:
            session.rollback()


def test_command_locks_hold_row_locks_until_commit(postgres_database):
    _seed(postgres_database)
    assert all(_probe(postgres_database, table) for table in _PROBES)
    with postgres_database.transaction() as session:
        locks = CommandLocks(session, "A", lock_stream=_no_stream)
        locks.stream()
        locks.encounter("e1")
        locks.character_states([("ca", "play")])
        locks.records_for("campaign_clocks", ["world"])
        assert not any(_probe(postgres_database, table) for table in _PROBES)
    assert all(_probe(postgres_database, table) for table in _PROBES)


def test_opposite_caller_orders_do_not_deadlock(postgres_database):
    _seed(postgres_database)
    holding = threading.Barrier(2)

    def lock(keys):
        def run():
            with postgres_database.transaction() as session:
                session.execute(text("SET LOCAL lock_timeout = '10s'"))
                locks = CommandLocks(session, "A", lock_stream=_no_stream)
                locks.stream()
                locked = locks.character_states(keys)
                acquired = time.monotonic()
                try:
                    # Hold both rows; the other side is blocked on them, so
                    # this barrier breaks after its timeout.
                    holding.wait(timeout=2)
                except threading.BrokenBarrierError:
                    pass
                releasing = time.monotonic()  # taken before the commit releases
            return sorted(locked), acquired, releasing
        return run

    for _ in range(3):
        holding.reset()
        results = _race([lock([("ca", "play"), ("cb", "play")]),
                         lock([("cb", "play"), ("ca", "play")])])
        assert not any(isinstance(r, Exception) for r in results), results
        assert [r[0] for r in results] == [[("ca", "play"), ("cb", "play")]] * 2
        first, second = sorted(results, key=lambda r: r[1])
        # The second acquired its rows only after the first committed.
        assert second[1] >= first[2], results


def test_concurrent_compare_and_set_has_exactly_one_winner(postgres_database):
    _seed(postgres_database)
    updated, release = threading.Event(), threading.Event()

    def first():
        with postgres_database.transaction() as session:
            record = CharacterStateRepository(session).write(
                "A", "ca", "play", read_revision=1, payload={"hp": 5})
            updated.set()
            release.wait(timeout=10)
            committing = time.monotonic()  # taken before the commit releases
        return record.revision, committing

    def second():
        assert updated.wait(timeout=10)
        with postgres_database.transaction() as session:
            session.execute(text("SET LOCAL lock_timeout = '10s'"))
            try:
                # The first transaction already holds the row with its
                # UPDATE applied, so this compare-and-set must wait for it
                # and then re-check the revision.
                CharacterStateRepository(session).write(
                    "A", "ca", "play", read_revision=1, payload={"hp": 6})
            except RevisionConflict as exc:
                return exc, time.monotonic()
        return "won", time.monotonic()

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(first)
        loser = executor.submit(second)
        assert updated.wait(timeout=10)
        time.sleep(1)
        release.set()
        (revision, committed), (outcome, returned) = winner.result(30), loser.result(30)
    assert revision == 2 and isinstance(outcome, RevisionConflict)
    assert returned >= committed
    with postgres_database.session() as session:
        stored = CharacterStateRepository(session).get("A", "ca", "play")
    assert stored.revision == 2 and stored.payload == {"hp": 5}
