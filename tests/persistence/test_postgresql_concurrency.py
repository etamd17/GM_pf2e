from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from core.persistence import (
    Campaign,
    CampaignMembership,
    Character,
    CharacterAlreadyClaimedError,
    CharacterAssignment,
    Database,
    Invite,
    InviteRedemption,
    InviteUnavailableError,
    LastGameMasterError,
    MigrationRun,
    TransactionalStore,
    User,
    normalize_database_url,
)
from tools import migrate_transactional_store as migration


pytestmark = pytest.mark.postgresql
NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)
MIGRATION_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"


@pytest.fixture
def postgres_database():
    raw_url = os.environ.get("DATABASE_URL")
    assert raw_url, "DATABASE_URL is required for PostgreSQL concurrency tests"
    url = normalize_database_url(raw_url)
    assert make_url(url).get_backend_name() == "postgresql", (
        "DATABASE_URL must point to PostgreSQL for concurrency tests"
    )

    schema = "gm_pf2e_test_" + uuid.uuid4().hex
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    database = Database(
        url,
        engine_options={
            "connect_args": {"options": f"-csearch_path={schema}"},
        },
    )
    try:
        database.create_schema()
        yield database
    finally:
        database.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _seed(
    database: Database,
    *,
    two_gms: bool = False,
    character_invites: bool = False,
) -> None:
    with database.transaction() as session:
        session.add_all(
            [
                User(
                    id=user_id,
                    username=user_id,
                    normalized_username=user_id,
                    display_name=user_id,
                    password_hash="hash",
                    created_at=NOW,
                )
                for user_id in ("gm-1", "gm-2", "player-1", "player-2")
            ]
        )
        session.flush()
        session.add(
            Campaign(
                id="campaign-1",
                slug="campaign",
                name="Campaign",
                system="pf2e",
                created_by_user_id="gm-1",
                created_at=NOW,
                settings={},
                source_payload={},
            )
        )
        session.flush()
        session.add(
            Character(
                id="character-1",
                campaign_id="campaign-1",
                system="pf2e",
                display_name="Hero",
                legacy_storage="party_data",
                legacy_file="hero.json",
                content_checksum="a" * 64,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            CampaignMembership(
                campaign_id="campaign-1",
                user_id="gm-1",
                role="gm",
                joined_at=NOW,
                updated_at=NOW,
            )
        )
        if two_gms:
            session.add(
                CampaignMembership(
                    campaign_id="campaign-1",
                    user_id="gm-2",
                    role="gm",
                    joined_at=NOW,
                    updated_at=NOW,
                )
            )
        if character_invites:
            session.add_all(
                [
                    CampaignMembership(
                        campaign_id="campaign-1",
                        user_id=user_id,
                        role="player",
                        joined_at=NOW,
                        updated_at=NOW,
                    )
                    for user_id in ("player-1", "player-2")
                ]
            )
        session.flush()

        if character_invites:
            session.add_all(
                [
                    Invite(
                        code=code,
                        campaign_id="campaign-1",
                        role="player",
                        character_id="character-1",
                        remaining_uses=1,
                        created_by_user_id="gm-1",
                        created_at=NOW,
                        expires_at=(NOW + timedelta(days=1)).timestamp(),
                    )
                    for code in ("CLAIM-ONE", "CLAIM-TWO")
                ]
            )
        else:
            session.add(
                Invite(
                    code="JOIN-ONE",
                    campaign_id="campaign-1",
                    role="player",
                    remaining_uses=1,
                    created_by_user_id="gm-1",
                    created_at=NOW,
                    expires_at=(NOW + timedelta(days=1)).timestamp(),
                )
            )


def _race(callables):
    barrier = threading.Barrier(len(callables))

    def run(callable_):
        barrier.wait(timeout=10)
        try:
            return callable_()
        except Exception as exc:  # assertions inspect the exact domain errors
            return exc

    with ThreadPoolExecutor(max_workers=len(callables)) as executor:
        futures = [executor.submit(run, callable_) for callable_ in callables]
        return [future.result(timeout=30) for future in futures]


def _migration_url(database: Database) -> str:
    with database.session() as session:
        schema = session.scalar(text("SELECT current_schema()"))
    return (
        make_url(database.url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_one_use_invite_has_exactly_one_concurrent_winner(postgres_database):
    _seed(postgres_database)
    store = TransactionalStore(postgres_database.session_factory)

    outcomes = _race(
        [
            lambda: store.redeem_invite(
                code="JOIN-ONE", user_id="player-1", now=NOW
            ),
            lambda: store.redeem_invite(
                code="JOIN-ONE", user_id="player-2", now=NOW
            ),
        ]
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, InviteUnavailableError) for outcome in outcomes) == 1
    with postgres_database.session() as session:
        assert session.get(Invite, "JOIN-ONE").remaining_uses == 0
        assert session.scalar(select(func.count()).select_from(InviteRedemption)) == 1
        assert session.scalar(
            select(func.count())
            .select_from(CampaignMembership)
            .where(CampaignMembership.role == "player")
        ) == 1


def test_concurrent_demotions_cannot_remove_every_gm(postgres_database):
    _seed(postgres_database, two_gms=True)
    store = TransactionalStore(postgres_database.session_factory)

    outcomes = _race(
        [
            lambda: store.set_membership_role(
                campaign_id="campaign-1", user_id="gm-1", role="player"
            ),
            lambda: store.set_membership_role(
                campaign_id="campaign-1", user_id="gm-2", role="player"
            ),
        ]
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(isinstance(outcome, LastGameMasterError) for outcome in outcomes) == 1
    with postgres_database.session() as session:
        assert session.scalar(
            select(func.count())
            .select_from(CampaignMembership)
            .where(
                CampaignMembership.campaign_id == "campaign-1",
                CampaignMembership.role == "gm",
            )
        ) == 1


def test_concurrent_character_invites_cannot_create_two_owners(
    postgres_database,
):
    _seed(postgres_database, character_invites=True)
    store = TransactionalStore(postgres_database.session_factory)

    outcomes = _race(
        [
            lambda: store.redeem_invite(
                code="CLAIM-ONE", user_id="player-1", now=NOW
            ),
            lambda: store.redeem_invite(
                code="CLAIM-TWO", user_id="player-2", now=NOW
            ),
        ]
    )

    assert sum(not isinstance(outcome, Exception) for outcome in outcomes) == 1
    assert sum(
        isinstance(outcome, CharacterAlreadyClaimedError) for outcome in outcomes
    ) == 1
    with postgres_database.session() as session:
        owners = session.scalars(
            select(CharacterAssignment).where(
                CharacterAssignment.campaign_id == "campaign-1",
                CharacterAssignment.character_id == "character-1",
                CharacterAssignment.role == "owner",
            )
        ).all()
        assert len(owners) == 1
        assert sorted(
            session.scalars(
                select(Invite.remaining_uses).where(
                    Invite.code.in_(["CLAIM-ONE", "CLAIM-TWO"])
                )
            ).all()
        ) == [0, 1]


@pytest.mark.parametrize("same_digest", [True, False])
def test_concurrent_migrations_are_serialized_by_database_lock(
    postgres_database, tmp_path, monkeypatch, same_digest
):
    first_source = tmp_path / "first"
    second_source = tmp_path / "second"
    shutil.copytree(MIGRATION_FIXTURE_ROOT, first_source)
    shutil.copytree(MIGRATION_FIXTURE_ROOT, second_source)
    if not same_digest:
        users_path = second_source / "users.json"
        users = json.loads(users_path.read_text(encoding="utf-8"))
        user_id = "11111111111111111111111111111111"
        users["users"][user_id]["display_name"] = "Different snapshot"
        _write_json(users_path, users)

    first_plan = migration.build_plan(first_source)
    second_plan = migration.build_plan(second_source)
    url = _migration_url(postgres_database)
    original_lock = migration._acquire_import_advisory_lock
    first_acquired = threading.Event()
    second_attempted = threading.Event()
    second_acquired = threading.Event()
    release_first = threading.Event()
    call_guard = threading.Lock()
    call_count = 0

    def controlled_lock(session, api):
        nonlocal call_count
        with call_guard:
            call_index = call_count
            call_count += 1
        if call_index == 1:
            second_attempted.set()
        original_lock(session, api)
        if call_index == 0:
            first_acquired.set()
            assert release_first.wait(timeout=15)
        else:
            second_acquired.set()

    monkeypatch.setattr(migration, "_acquire_import_advisory_lock", controlled_lock)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            migration.import_store,
            first_source,
            url,
            expected_digest=first_plan["source_digest"],
        )
        assert first_acquired.wait(timeout=15)
        second_future = executor.submit(
            migration.import_store,
            second_source,
            url,
            expected_digest=second_plan["source_digest"],
        )
        assert second_attempted.wait(timeout=15)
        assert second_acquired.wait(timeout=0.5) is False
        release_first.set()
        first_result = first_future.result(timeout=30)
        if same_digest:
            second_result = second_future.result(timeout=30)
            assert second_result["status"] == "already_imported"
        else:
            with pytest.raises(migration.MigrationToolError, match="different snapshot"):
                second_future.result(timeout=30)

    assert first_result["status"] == "imported"
    with postgres_database.session() as session:
        assert session.scalar(select(func.count()).select_from(MigrationRun)) == 1


def test_export_verifies_and_reads_from_one_repeatable_snapshot(
    postgres_database, tmp_path, monkeypatch
):
    source = tmp_path / "source"
    output = tmp_path / "export"
    shutil.copytree(MIGRATION_FIXTURE_ROOT, source)
    plan = migration.build_plan(source)
    url = _migration_url(postgres_database)
    migration.import_store(source, url, expected_digest=plan["source_digest"])

    real_verify = migration._verify_store_session
    verification_done = threading.Event()
    release_export = threading.Event()

    def paused_verify(bundle, session, api):
        report = real_verify(bundle, session, api)
        assert session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
        verification_done.set()
        assert release_export.wait(timeout=15)
        return report

    monkeypatch.setattr(migration, "_verify_store_session", paused_verify)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(migration.export_store, source, url, output)
        assert verification_done.wait(timeout=15)
        with postgres_database.transaction() as session:
            user = session.get(User, "11111111111111111111111111111111")
            payload = dict(user.source_payload)
            payload["theme"] = "changed-after-verification"
            user.source_payload = payload
        release_export.set()
        result = future.result(timeout=30)

    assert result["verified"] is True
    exported = json.loads((output / "users.json").read_text(encoding="utf-8"))
    assert exported["users"]["11111111111111111111111111111111"]["theme"] == "night"
