from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.exc import IntegrityError

import core.persistence.database as database_module
from core.persistence import (
    Base,
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    CharacterDraft,
    Database,
    Invite,
    User,
    normalize_database_url,
    normalize_username,
)


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _user(user_id: str) -> User:
    return User(
        id=user_id,
        username=user_id,
        normalized_username=user_id,
        display_name=user_id,
        password_hash="hash",
        created_at=NOW,
    )


def _campaign(campaign_id: str, creator: str, *, slug: str = "same") -> Campaign:
    return Campaign(
        id=campaign_id,
        slug=slug,
        name="Campaign " + campaign_id,
        system="pf2e",
        created_by_user_id=creator,
        created_at=NOW,
        settings={},
        source_payload={},
    )


def _character(character_id: str, campaign_id: str) -> Character:
    return Character(
        id=character_id,
        campaign_id=campaign_id,
        system="pf2e",
        display_name=character_id,
        legacy_storage="party_data",
        legacy_file=character_id + ".json",
        content_checksum="a" * 64,
        created_at=NOW,
        updated_at=NOW,
    )


def test_database_setup_is_lazy(monkeypatch):
    real_create_engine = database_module.create_engine
    calls: list[str] = []

    def recording_create_engine(url, **options):
        calls.append(str(url))
        return real_create_engine(url, **options)

    monkeypatch.setattr(database_module, "create_engine", recording_create_engine)
    database = Database("sqlite+pysqlite:///:memory:")
    assert calls == []
    assert database.url == "sqlite+pysqlite:///:memory:"

    first = database.engine
    assert calls == ["sqlite+pysqlite:///:memory:"]
    assert database.engine is first
    database.dispose()


def test_import_does_not_read_or_connect_to_database_url(tmp_path):
    database_path = tmp_path / "must-not-exist.sqlite"
    environment = os.environ.copy()
    environment["DATABASE_URL"] = (
        "sqlite+pysqlite:///" + database_path.as_posix()
    )
    subprocess.run(
        [sys.executable, "-c", "import core.persistence"],
        check=True,
        env=environment,
    )
    assert not database_path.exists()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgres://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        (
            "postgresql://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
        (
            "postgresql+psycopg://user:pass@host/db",
            "postgresql+psycopg://user:pass@host/db",
        ),
    ],
)
def test_railway_database_urls_select_psycopg3(raw, expected):
    assert normalize_database_url(raw) == expected


def test_username_normalization_matches_authoritative_json_auth():
    assert normalize_username(" Straße ") == "straße"
    assert normalize_username(" Straße ") != "strasse"


def test_username_normalization_rejects_values_unsafe_for_unique_index():
    with pytest.raises(ValueError, match="index length"):
        normalize_username("x" * 2_001)


def test_schema_exposes_the_approved_additive_tables():
    assert set(Base.metadata.tables) == {
        "audit_events",
        "campaign_memberships",
        "campaigns",
        "character_assignments",
        "character_drafts",
        "characters",
        "invite_redemptions",
        "invites",
        "migration_runs",
        "users",
    }


def test_postgresql_offline_migration_preserves_orm_check_constraint_names():
    repository_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    # Offline mode needs a dialect URL but never opens a connection.
    environment["DATABASE_URL"] = (
        "postgresql+psycopg://offline:offline@localhost/offline"
    )
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        cwd=repository_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    check_names = {
        str(constraint.name): table.name
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert check_names
    for constraint_name, table_name in check_names.items():
        assert completed.stdout.count(constraint_name) == 1
        double_prefixed = f"ck_{table_name}_{constraint_name}"
        assert double_prefixed not in completed.stdout


def test_database_constraints_reject_unknown_ruleset(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add(_user("u1"))
    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        campaign = _campaign("c1", "u1")
        campaign.system = "unknown-system"
        session.add(campaign)
        session.flush()

def test_duplicate_and_long_legacy_campaign_names_and_slugs_are_preserved(
    sqlite_database,
):
    long_value = "legacy-" + ("x" * 5000)
    with sqlite_database.transaction() as session:
        session.add(_user("u1"))
        session.flush()
        session.add_all(
            [
                _campaign("c1", "u1", slug=long_value),
                _campaign("c2", "u1", slug=long_value),
            ]
        )
        session.flush()
        session.get(Campaign, "c1").name = long_value

    with sqlite_database.session() as session:
        assert session.get(Campaign, "c1").name == long_value
        assert session.get(Campaign, "c2").slug == long_value


def test_campaign_scoped_foreign_keys_reject_cross_campaign_references(
    sqlite_database,
):
    with sqlite_database.transaction() as session:
        session.add_all([_user("u1"), _user("u2")])
        session.flush()
        session.add_all([_campaign("c1", "u1"), _campaign("c2", "u2")])
        session.flush()
        session.add(_character("ch1", "c1"))
        session.add(
            CampaignMembership(
                campaign_id="c1",
                user_id="u1",
                role="gm",
                joined_at=NOW,
                updated_at=NOW,
            )
        )
        session.add(
            CampaignMembership(
                campaign_id="c2",
                user_id="u2",
                role="gm",
                joined_at=NOW,
                updated_at=NOW,
            )
        )

    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        session.add(
            CampaignMembership(
                campaign_id="c2",
                user_id="u1",
                role="player",
                character_id="ch1",
                joined_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()

    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        session.add(
            CharacterAssignment(
                campaign_id="c2",
                character_id="ch1",
                user_id="u2",
                role="owner",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()

    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        session.add(
            Invite(
                code="CROSS-CHAR",
                campaign_id="c2",
                role="player",
                character_id="ch1",
                remaining_uses=1,
                created_at=NOW,
            )
        )
        session.flush()


def test_draft_owner_must_be_a_member_of_the_same_campaign(sqlite_database):
    with sqlite_database.transaction() as session:
        session.add_all([_user("u1"), _user("u2")])
        session.flush()
        session.add_all([_campaign("c1", "u1"), _campaign("c2", "u2")])
        session.flush()
        session.add(
            CampaignMembership(
                campaign_id="c1",
                user_id="u1",
                role="gm",
                joined_at=NOW,
                updated_at=NOW,
            )
        )

    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        session.add(
            CharacterDraft(
                id="draft-1",
                campaign_id="c2",
                user_id="u1",
                kind="builder",
                payload={},
                created_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()


def test_partial_owner_index_allows_many_collaborators_but_one_owner(
    sqlite_database,
):
    with sqlite_database.transaction() as session:
        session.add_all([_user("u1"), _user("u2"), _user("u3")])
        session.flush()
        session.add(_campaign("c1", "u1"))
        session.flush()
        session.add(_character("ch1", "c1"))
        session.add_all(
            [
                CampaignMembership(
                    campaign_id="c1",
                    user_id=user_id,
                    role="gm" if user_id == "u1" else "player",
                    joined_at=NOW,
                    updated_at=NOW,
                )
                for user_id in ("u1", "u2", "u3")
            ]
        )
        session.flush()
        session.add_all(
            [
                CharacterAssignment(
                    campaign_id="c1",
                    character_id="ch1",
                    user_id="u1",
                    role="owner",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                CharacterAssignment(
                    campaign_id="c1",
                    character_id="ch1",
                    user_id="u2",
                    role="editor",
                    created_at=NOW,
                    updated_at=NOW,
                ),
                CharacterAssignment(
                    campaign_id="c1",
                    character_id="ch1",
                    user_id="u3",
                    role="editor",
                    created_at=NOW,
                    updated_at=NOW,
                ),
            ]
        )

    with pytest.raises(IntegrityError), sqlite_database.transaction() as session:
        session.get(
            CharacterAssignment, ("c1", "ch1", "u2")
        ).role = "owner"
        session.flush()
