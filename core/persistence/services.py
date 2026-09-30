"""Transactional membership, invitation, and character-claim operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AuditEvent,
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Invitation,
    InviteRedemption,
    User,
    new_id,
    utc_now,
)
from .repositories import normalize_invite_code


class TransactionalStoreError(RuntimeError):
    """Base class for expected workflow rejections."""


class RecordNotFoundError(TransactionalStoreError):
    pass


class InviteUnavailableError(TransactionalStoreError):
    pass


class CharacterAlreadyClaimedError(TransactionalStoreError):
    pass


class MembershipRequiredError(TransactionalStoreError):
    pass


class LastGameMasterError(TransactionalStoreError):
    pass


@dataclass(frozen=True, slots=True)
class InviteRedemptionResult:
    invitation_code: str
    campaign_id: str
    user_id: str
    character_id: str | None
    remaining_uses: int
    already_redeemed: bool = False


@dataclass(frozen=True, slots=True)
class CharacterClaimResult:
    campaign_id: str
    character_id: str
    user_id: str
    already_owned: bool = False


class TransactionalStore:
    """Execute security-bearing mutations inside database transactions.

    PostgreSQL row locks provide the production concurrency guarantee.  Every
    operation first locks the campaign row, giving membership and ownership
    changes a shared order and making last-GM checks safe under concurrency.
    SQLite remains useful for deterministic unit tests, but it is not treated
    as proof of row-lock behavior.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._session_factory = session_factory

    def redeem_invite(
        self,
        *,
        code: str,
        user_id: str,
        now: datetime | None = None,
    ) -> InviteRedemptionResult:
        normalized_code = normalize_invite_code(code)
        timestamp = now or utc_now()
        now_epoch = timestamp.timestamp()

        with self._session_factory.begin() as session:
            invitation = session.scalar(
                select(Invitation)
                .where(Invitation.code == normalized_code)
                .with_for_update()
            )
            if invitation is None:
                raise InviteUnavailableError("invite does not exist")

            # Retrying the same committed request is idempotent, even though a
            # one-use invite now has zero uses. The invitation row lock ensures
            # a concurrent retry sees the first redemption after it commits.
            prior = session.scalar(
                select(InviteRedemption).where(
                    InviteRedemption.invitation_code == normalized_code,
                    InviteRedemption.user_id == user_id,
                )
            )
            if prior is not None:
                return InviteRedemptionResult(
                    invitation_code=normalized_code,
                    campaign_id=prior.campaign_id,
                    user_id=user_id,
                    character_id=prior.character_id,
                    remaining_uses=invitation.remaining_uses,
                    already_redeemed=True,
                )

            if invitation.revoked_at is not None:
                raise InviteUnavailableError("invite has been revoked")
            if invitation.remaining_uses <= 0:
                raise InviteUnavailableError("invite has no remaining uses")
            if (
                invitation.expires_at is not None
                and now_epoch > invitation.expires_at
            ):
                raise InviteUnavailableError("invite has expired")
            if session.get(User, user_id) is None:
                raise RecordNotFoundError("user does not exist")

            campaign = session.scalar(
                select(Campaign)
                .where(Campaign.id == invitation.campaign_id)
                .with_for_update()
            )
            if campaign is None:
                raise RecordNotFoundError("campaign does not exist")

            character = None
            if invitation.character_id is not None:
                character = session.scalar(
                    select(Character)
                    .where(
                        Character.id == invitation.character_id,
                        Character.campaign_id == invitation.campaign_id,
                    )
                    .with_for_update()
                )
                if character is None:
                    raise RecordNotFoundError(
                        "invited character does not exist in the campaign"
                    )

            membership = session.scalar(
                select(CampaignMembership)
                .where(
                    CampaignMembership.campaign_id == invitation.campaign_id,
                    CampaignMembership.user_id == user_id,
                )
                .with_for_update()
            )
            if membership is None:
                membership = CampaignMembership(
                    campaign_id=invitation.campaign_id,
                    user_id=user_id,
                    role=invitation.role,
                    character_id=invitation.character_id,
                    joined_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(membership)
            else:
                # Player/character invites can be tested by an existing GM and
                # must never demote that GM as a side effect.
                if membership.role != "gm":
                    membership.role = invitation.role
                if invitation.character_id is not None:
                    membership.character_id = invitation.character_id
                membership.updated_at = timestamp
            session.flush()

            if character is not None:
                self._claim_locked_character(
                    session,
                    character=character,
                    user_id=user_id,
                    timestamp=timestamp,
                )

            invitation.remaining_uses -= 1
            redemption = InviteRedemption(
                id=new_id(),
                invitation_code=normalized_code,
                user_id=user_id,
                campaign_id=invitation.campaign_id,
                character_id=invitation.character_id,
                redeemed_at=timestamp,
                details={"role": invitation.role},
            )
            session.add(redemption)
            session.add(
                AuditEvent(
                    actor_user_id=user_id,
                    campaign_id=invitation.campaign_id,
                    action="invite.redeemed",
                    target_type="invite",
                    target_id=normalized_code,
                    details={
                        "character_id": invitation.character_id,
                        "remaining_uses": invitation.remaining_uses,
                        "role": invitation.role,
                    },
                    occurred_at=timestamp,
                )
            )
            session.flush()

            return InviteRedemptionResult(
                invitation_code=normalized_code,
                campaign_id=invitation.campaign_id,
                user_id=user_id,
                character_id=invitation.character_id,
                remaining_uses=invitation.remaining_uses,
            )

    def claim_character(
        self,
        *,
        character_id: str,
        user_id: str,
        now: datetime | None = None,
    ) -> CharacterClaimResult:
        timestamp = now or utc_now()
        with self._session_factory.begin() as session:
            # Read only to discover the campaign; lock ordering still starts
            # with Campaign before the authoritative Character re-read.
            locator = session.get(Character, character_id)
            if locator is None:
                raise RecordNotFoundError("character does not exist")
            campaign_id = locator.campaign_id
            campaign = session.scalar(
                select(Campaign)
                .where(Campaign.id == campaign_id)
                .with_for_update()
            )
            if campaign is None:
                raise RecordNotFoundError("campaign does not exist")
            character = session.scalar(
                select(Character)
                .where(
                    Character.id == character_id,
                    Character.campaign_id == campaign_id,
                )
                .with_for_update()
            )
            if character is None:
                raise RecordNotFoundError("character no longer exists")
            membership = session.scalar(
                select(CampaignMembership)
                .where(
                    CampaignMembership.campaign_id == campaign_id,
                    CampaignMembership.user_id == user_id,
                )
                .with_for_update()
            )
            if membership is None:
                raise MembershipRequiredError(
                    "campaign membership is required to claim a character"
                )
            already_owned = self._claim_locked_character(
                session,
                character=character,
                user_id=user_id,
                timestamp=timestamp,
            )
            membership.character_id = character_id
            membership.updated_at = timestamp
            session.add(
                AuditEvent(
                    actor_user_id=user_id,
                    campaign_id=campaign_id,
                    action="character.claimed",
                    target_type="character",
                    target_id=character_id,
                    details={"already_owned": already_owned},
                    occurred_at=timestamp,
                )
            )
            session.flush()
            return CharacterClaimResult(
                campaign_id=campaign_id,
                character_id=character_id,
                user_id=user_id,
                already_owned=already_owned,
            )

    def set_membership_role(
        self,
        *,
        campaign_id: str,
        user_id: str,
        role: str,
        actor_user_id: str | None = None,
        now: datetime | None = None,
    ) -> CampaignMembership:
        if role not in {"gm", "player"}:
            raise ValueError("membership role must be gm or player")
        timestamp = now or utc_now()
        with self._session_factory.begin() as session:
            self._lock_campaign(session, campaign_id)
            membership = self._locked_membership(session, campaign_id, user_id)
            if membership is None:
                raise RecordNotFoundError("campaign membership does not exist")
            if membership.role == "gm" and role == "player":
                self._reject_if_last_gm(session, campaign_id)
            previous_role = membership.role
            membership.role = role
            membership.updated_at = timestamp
            session.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    campaign_id=campaign_id,
                    action="membership.role_changed",
                    target_type="user",
                    target_id=user_id,
                    details={"from": previous_role, "to": role},
                    occurred_at=timestamp,
                )
            )
            session.flush()
            return membership

    def remove_membership(
        self,
        *,
        campaign_id: str,
        user_id: str,
        actor_user_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or utc_now()
        with self._session_factory.begin() as session:
            self._lock_campaign(session, campaign_id)
            membership = self._locked_membership(session, campaign_id, user_id)
            if membership is None:
                return False
            if membership.role == "gm":
                self._reject_if_last_gm(session, campaign_id)
            session.delete(membership)
            session.add(
                AuditEvent(
                    actor_user_id=actor_user_id,
                    campaign_id=campaign_id,
                    action="membership.removed",
                    target_type="user",
                    target_id=user_id,
                    details={"role": membership.role},
                    occurred_at=timestamp,
                )
            )
            session.flush()
            return True

    @staticmethod
    def _lock_campaign(session: Session, campaign_id: str) -> Campaign:
        campaign = session.scalar(
            select(Campaign)
            .where(Campaign.id == campaign_id)
            .with_for_update()
        )
        if campaign is None:
            raise RecordNotFoundError("campaign does not exist")
        return campaign

    @staticmethod
    def _locked_membership(
        session: Session, campaign_id: str, user_id: str
    ) -> CampaignMembership | None:
        return session.scalar(
            select(CampaignMembership)
            .where(
                CampaignMembership.campaign_id == campaign_id,
                CampaignMembership.user_id == user_id,
            )
            .with_for_update()
        )

    @staticmethod
    def _reject_if_last_gm(session: Session, campaign_id: str) -> None:
        gm_count = session.scalar(
            select(func.count())
            .select_from(CampaignMembership)
            .where(
                CampaignMembership.campaign_id == campaign_id,
                CampaignMembership.role == "gm",
            )
        )
        if int(gm_count or 0) <= 1:
            raise LastGameMasterError("a campaign must retain at least one GM")

    @staticmethod
    def _claim_locked_character(
        session: Session,
        *,
        character: Character,
        user_id: str,
        timestamp: datetime,
    ) -> bool:
        owner = session.scalar(
            select(CharacterAssignment)
            .where(
                CharacterAssignment.campaign_id == character.campaign_id,
                CharacterAssignment.character_id == character.id,
                CharacterAssignment.role == "owner",
            )
            .with_for_update()
        )
        if owner is not None and owner.user_id != user_id:
            raise CharacterAlreadyClaimedError(
                "character is already owned by another user"
            )
        if owner is not None:
            return True

        assignment = session.get(
            CharacterAssignment,
            (character.campaign_id, character.id, user_id),
        )
        if assignment is None:
            assignment = CharacterAssignment(
                campaign_id=character.campaign_id,
                character_id=character.id,
                user_id=user_id,
                role="owner",
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(assignment)
        else:
            assignment.role = "owner"
            assignment.updated_at = timestamp
        session.flush()
        return False
