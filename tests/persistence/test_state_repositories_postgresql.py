"""PE-13a on PostgreSQL: lock order and compare-and-set under real concurrency."""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from core.persistence import (
    Campaign,
    CampaignStateAuthority,
    Character,
    CharacterStateRepository,
    CommandLocks,
    Database,
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
                try:
                    # Hold both rows until the other side has asked for them.
                    holding.wait(timeout=2)
                except threading.BrokenBarrierError:
                    pass
                return sorted(locked)
        return run

    for _ in range(5):
        holding.reset()
        results = _race([lock([("ca", "play"), ("cb", "play")]),
                         lock([("cb", "play"), ("ca", "play")])])
        assert results == [[("ca", "play"), ("cb", "play")]] * 2, results


def test_concurrent_compare_and_set_has_exactly_one_winner(postgres_database):
    _seed(postgres_database)

    def write(hp):
        def run():
            with postgres_database.transaction() as session:
                record = CharacterStateRepository(session).write(
                    "A", "ca", "play", read_revision=1, payload={"hp": hp})
                return record.revision
        return run

    results = _race([write(5), write(6)])
    assert sorted(type(r).__name__ for r in results) == ["RevisionConflict", "int"]
    assert [r for r in results if isinstance(r, int)] == [2]
    assert any(isinstance(r, RevisionConflict) for r in results)
    with postgres_database.session() as session:
        stored = CharacterStateRepository(session).get("A", "ca", "play")
    assert stored.revision == 2 and stored.payload["hp"] in (5, 6)
