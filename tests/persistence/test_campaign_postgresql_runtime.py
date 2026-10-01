"""Exercise runtime campaign permission changes against real row locks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from core.persistence import Campaign, CampaignMembership, Database, User
from core.persistence import campaign_runtime, normalize_database_url, runtime
from core.persistence.models import AuditEvent


pytestmark = pytest.mark.postgresql
CAMPAIGN_ID = "c" * 32


@pytest.fixture
def campaign_postgres_database(monkeypatch):
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        pytest.skip("DATABASE_URL is required for PostgreSQL runtime tests")
    url = normalize_database_url(raw_url)
    if make_url(url).get_backend_name() != "postgresql":
        pytest.skip("PostgreSQL is required for runtime row-lock tests")

    schema = "gm_pf2e_campaign_test_" + uuid.uuid4().hex
    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database = Database(
        url,
        engine_options={
            "connect_args": {
                "options": (
                    f"-csearch_path={schema} -clock_timeout=15000 "
                    "-cstatement_timeout=20000"
                ),
            },
        },
    )
    monkeypatch.setattr(runtime, "database", lambda: database)
    try:
        database.create_schema()
        with database.transaction() as session:
            session.add_all([
                User(
                    id=uid, username=uid, normalized_username=uid,
                    display_name=uid, password_hash="unused",
                )
                for uid in ("gm-one", "gm-two", "target")
            ])
            session.flush()
            session.add(Campaign(
                id=CAMPAIGN_ID, slug="campaign", name="Campaign", system="pf2e",
                created_by_user_id="gm-one",
            ))
            session.flush()
            session.add_all([
                CampaignMembership(campaign_id=CAMPAIGN_ID, user_id=uid, role="gm")
                for uid in ("gm-one", "gm-two")
            ])
        yield database
    finally:
        database.dispose()
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _wait_for_database_blocker(database, blocker_pid):
    """Prove the competing write has reached PostgreSQL's lock wait."""
    deadline = time.monotonic() + 10
    with database.engine.connect().execution_options(
        isolation_level="AUTOCOMMIT"
    ) as connection:
        while time.monotonic() < deadline:
            waiting = connection.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                "WHERE :blocker_pid = ANY(pg_blocking_pids(pid)))"
            ), {"blocker_pid": blocker_pid})
            if waiting:
                return
            time.sleep(0.01)
    pytest.fail("campaign mutation did not wait for the demotion transaction")


def test_gm_mutation_rechecks_actor_after_concurrent_demotion_commits(
    campaign_postgres_database,
):
    database = campaign_postgres_database
    # Model a route's earlier successful permission check.
    assert campaign_runtime.user_role(CAMPAIGN_ID, "gm-one") == "gm"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.transaction() as demotion:
            demotion.scalar(select(Campaign).where(
                Campaign.id == CAMPAIGN_ID
            ).with_for_update())
            blocker_pid = demotion.scalar(text("SELECT pg_backend_pid()"))
            actor = demotion.get(CampaignMembership, (CAMPAIGN_ID, "gm-one"))
            actor.role = "player"
            demotion.flush()
            mutation = executor.submit(
                campaign_runtime.add_member,
                CAMPAIGN_ID, "target", "gm", actor_user_id="gm-one",
            )
            _wait_for_database_blocker(database, blocker_pid)
            assert not mutation.done()
        # The route began while its actor was a GM; authority is gone by the
        # time it acquires the campaign lock, so the whole mutation must abort.
        with pytest.raises(campaign_runtime.CampaignPermissionDenied, match="GM permission"):
            mutation.result(timeout=10)

    with database.session() as session:
        assert session.get(CampaignMembership, (CAMPAIGN_ID, "target")) is None
        assert session.get(CampaignMembership, (CAMPAIGN_ID, "gm-one")).role == "player"
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_concurrent_runtime_self_demotions_preserve_last_gm(
    campaign_postgres_database,
):
    database = campaign_postgres_database
    barrier = threading.Barrier(2)

    def demote(user_id):
        barrier.wait(timeout=10)
        return campaign_runtime.set_member_role(
            CAMPAIGN_ID, user_id, "player", actor_user_id=user_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(demote, uid) for uid in ("gm-one", "gm-two")]
        outcomes = [future.result(timeout=20) for future in futures]

    assert sum(outcome is not None for outcome in outcomes) == 1
    assert sum(outcome is None for outcome in outcomes) == 1
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(CampaignMembership)
            .where(CampaignMembership.campaign_id == CAMPAIGN_ID,
                   CampaignMembership.role == "gm")) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)
            .where(AuditEvent.action == "membership.role_changed")) == 1
