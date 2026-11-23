from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from core.persistence import (
    Campaign,
    CampaignMembership,
    Character,
    CharacterAlreadyClaimedError,
    CharacterAssignment,
    Invite,
    InviteRedemption,
    InviteUnavailableError,
    LastGameMasterError,
    MembershipRequiredError,
    RecordNotFoundError,
    TransactionalStore,
    User,
)


NOW = datetime(2026, 9, 30, 16, 0, tzinfo=timezone.utc)


def _seed(
    database,
    *,
    invite_code: str = "JOIN-ONE",
    character_invite: bool = True,
    include_second_gm: bool = False,
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
        if include_second_gm:
            session.add(
                CampaignMembership(
                    campaign_id="campaign-1",
                    user_id="gm-2",
                    role="gm",
                    joined_at=NOW,
                    updated_at=NOW,
                )
            )
        session.flush()
        session.add(
            Invite(
                code=invite_code,
                campaign_id="campaign-1",
                role="player",
                character_id="character-1" if character_invite else None,
                remaining_uses=1,
                creates_account=True,
                created_by_user_id="gm-1",
                created_at=NOW,
                expires_at=(NOW + timedelta(days=1)).timestamp(),
            )
        )


def test_redeem_invite_atomically_adds_membership_claim_and_audit(
    sqlite_database,
):
    _seed(sqlite_database)
    store = TransactionalStore(sqlite_database.session_factory)

    result = store.redeem_invite(
        code="join-one", user_id="player-1", now=NOW
    )
    assert result.remaining_uses == 0
    assert result.character_id == "character-1"
    assert result.already_redeemed is False

    with sqlite_database.session() as session:
        membership = session.get(
            CampaignMembership, ("campaign-1", "player-1")
        )
        assignment = session.get(
            CharacterAssignment,
            ("campaign-1", "character-1", "player-1"),
        )
        assert membership.role == "player"
        assert membership.character_id == "character-1"
        assert assignment.role == "owner"
        assert session.get(Invite, "JOIN-ONE").remaining_uses == 0
        assert session.scalar(select(func.count()).select_from(InviteRedemption)) == 1

    retry = store.redeem_invite(
        code="JOIN-ONE", user_id="player-1", now=NOW
    )
    assert retry.already_redeemed is True
    assert retry.remaining_uses == 0

    with pytest.raises(InviteUnavailableError):
        store.redeem_invite(
            code="JOIN-ONE", user_id="player-2", now=NOW
        )


def test_player_invite_never_demotes_an_existing_gm(sqlite_database):
    _seed(sqlite_database, character_invite=False)
    store = TransactionalStore(sqlite_database.session_factory)

    store.redeem_invite(code="JOIN-ONE", user_id="gm-1", now=NOW)

    with sqlite_database.session() as session:
        membership = session.get(CampaignMembership, ("campaign-1", "gm-1"))
        assert membership.role == "gm"


def test_claim_conflict_rolls_back_membership_and_invite_use(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.add(
            CampaignMembership(
                campaign_id="campaign-1",
                user_id="player-2",
                role="player",
                joined_at=NOW,
                updated_at=NOW,
            )
        )
        session.flush()
        session.add(
            CharacterAssignment(
                campaign_id="campaign-1",
                character_id="character-1",
                user_id="player-2",
                role="owner",
                created_at=NOW,
                updated_at=NOW,
            )
        )

    store = TransactionalStore(sqlite_database.session_factory)
    with pytest.raises(CharacterAlreadyClaimedError):
        store.redeem_invite(
            code="JOIN-ONE", user_id="player-1", now=NOW
        )

    with sqlite_database.session() as session:
        assert session.get(
            CampaignMembership, ("campaign-1", "player-1")
        ) is None
        assert session.get(Invite, "JOIN-ONE").remaining_uses == 1
        assert session.scalar(select(func.count()).select_from(InviteRedemption)) == 0


def test_claim_requires_campaign_membership_and_is_idempotent(sqlite_database):
    _seed(sqlite_database, character_invite=False)
    store = TransactionalStore(sqlite_database.session_factory)

    with pytest.raises(MembershipRequiredError):
        store.claim_character(character_id="character-1", user_id="player-1")

    with sqlite_database.transaction() as session:
        session.add(
            CampaignMembership(
                campaign_id="campaign-1",
                user_id="player-1",
                role="player",
                joined_at=NOW,
                updated_at=NOW,
            )
        )

    first = store.claim_character(
        character_id="character-1", user_id="player-1", now=NOW
    )
    second = store.claim_character(
        character_id="character-1", user_id="player-1", now=NOW
    )
    assert first.already_owned is False
    assert second.already_owned is True


def test_claim_reports_character_deleted_before_authoritative_locked_read(monkeypatch):
    # PE-14d: the claim now takes the stream row first; this fake session has
    # no bind, so the stream step is stubbed (its order is covered below).
    from core.persistence import services
    monkeypatch.setattr(services, "lock_stream_for_write", lambda _session, _cid: None)

    class ControlledSession:
        scalar_calls = 0

        @staticmethod
        def get(_model, _identifier):
            # The initial unlocked locator read sees the row.
            return type("Locator", (), {"campaign_id": "campaign-1"})()

        def scalar(self, _statement):
            self.scalar_calls += 1
            if self.scalar_calls == 1:
                # Campaign row acquired and locked, still active.
                return type("LockedCampaign", (), {"trashed_at": None})()
            if self.scalar_calls == 2:
                return None  # Character disappeared before its locked re-read.
            raise AssertionError("claim queried past the missing character")

    session = ControlledSession()

    class ControlledTransaction:
        def __enter__(self):
            return session

        def __exit__(self, _exc_type, _exc, _traceback):
            return False

    class ControlledFactory:
        @staticmethod
        def begin():
            return ControlledTransaction()

    store = TransactionalStore(ControlledFactory())
    with pytest.raises(RecordNotFoundError, match="no longer exists"):
        store.claim_character(character_id="character-1", user_id="player-1")
    assert session.scalar_calls == 2


def test_last_gm_cannot_be_removed_or_demoted(sqlite_database):
    _seed(sqlite_database, character_invite=False)
    store = TransactionalStore(sqlite_database.session_factory)

    with pytest.raises(LastGameMasterError):
        store.set_membership_role(
            campaign_id="campaign-1", user_id="gm-1", role="player"
        )
    with pytest.raises(LastGameMasterError):
        store.remove_membership(campaign_id="campaign-1", user_id="gm-1")

    with sqlite_database.transaction() as session:
        session.add(
            CampaignMembership(
                campaign_id="campaign-1",
                user_id="gm-2",
                role="gm",
                joined_at=NOW,
                updated_at=NOW,
            )
        )

    assert store.remove_membership(
        campaign_id="campaign-1", user_id="gm-1", actor_user_id="gm-2"
    )
    with pytest.raises(LastGameMasterError):
        store.set_membership_role(
            campaign_id="campaign-1", user_id="gm-2", role="player"
        )


def test_redeemed_player_can_be_removed_without_losing_redemption_history(
    sqlite_database,
):
    _seed(sqlite_database, character_invite=False)
    store = TransactionalStore(sqlite_database.session_factory)
    store.redeem_invite(code="JOIN-ONE", user_id="player-1", now=NOW)

    assert store.remove_membership(
        campaign_id="campaign-1",
        user_id="player-1",
        actor_user_id="gm-1",
    )

    with sqlite_database.session() as session:
        assert session.get(
            CampaignMembership, ("campaign-1", "player-1")
        ) is None
        redemption = session.scalar(
            select(InviteRedemption).where(
                InviteRedemption.invitation_code == "JOIN-ONE",
                InviteRedemption.user_id == "player-1",
            )
        )
        assert redemption is not None
        assert redemption.campaign_id == "campaign-1"


@pytest.mark.parametrize("writer", [
    "redeem_invite", "claim_character", "set_membership_role", "remove_membership",
])
def test_store_writers_lock_stream_before_campaign_row(sqlite_database, writer):
    """PE-14d Decision 1: stream row, then campaign row, then invitation, then writes."""
    from tests.persistence.lock_order import assert_stream_first, first, record_locks

    _seed(sqlite_database, character_invite=False, include_second_gm=True)
    store = TransactionalStore(sqlite_database.session_factory)
    calls = {
        "redeem_invite": lambda: store.redeem_invite(
            code="JOIN-ONE", user_id="player-1", now=NOW),
        "claim_character": lambda: store.claim_character(
            character_id="character-1", user_id="gm-2", now=NOW),
        "set_membership_role": lambda: store.set_membership_role(
            campaign_id="campaign-1", user_id="gm-2", role="player"),
        "remove_membership": lambda: store.remove_membership(
            campaign_id="campaign-1", user_id="gm-2", actor_user_id="gm-1"),
    }
    with record_locks() as events:
        calls[writer]()
    first(events, "write:")
    if writer == "redeem_invite":
        assert_stream_first(
            events, "campaign-1", before=("for_update:campaigns", "for_update:invites"))
        assert first(events, "for_update:campaigns") < first(events, "for_update:invites")
    else:
        assert_stream_first(events, "campaign-1")


def test_redeem_invite_trashed_campaign_still_unavailable(sqlite_database):
    """The helper never checks ``trashed_at``; the caller's check must survive."""
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        session.get(Campaign, "campaign-1").trashed_at = NOW
    store = TransactionalStore(sqlite_database.session_factory)

    with pytest.raises(InviteUnavailableError, match="^campaign is unavailable$"):
        store.redeem_invite(code="JOIN-ONE", user_id="player-1", now=NOW)

    with sqlite_database.session() as session:
        assert session.get(CampaignMembership, ("campaign-1", "player-1")) is None
        assert session.get(
            CharacterAssignment, ("campaign-1", "character-1", "player-1")) is None
        assert session.scalar(select(func.count()).select_from(InviteRedemption)) == 0
        assert session.get(Invite, "JOIN-ONE").remaining_uses == 1
