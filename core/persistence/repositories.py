"""Small session-bound repositories for import and transactional workflows.

Repositories flush but never commit.  The caller owns the transaction, which
lets the migration command import a complete snapshot atomically and lets the
workflow service combine invite, membership, ownership, and audit writes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Invitation,
    MigrationRun,
    User,
    utc_now,
)

MAX_INDEXED_USERNAME_BYTES = 2_000


def normalize_username(username: str) -> str:
    value = str(username or "").strip()
    if not value:
        raise ValueError("username is required")
    # Match core.auth exactly. Changing this to casefold() would collapse some
    # distinct legacy usernames (for example ``straße`` and ``strasse``) and
    # make the additive shadow stricter than the authoritative JSON runtime.
    normalized = value.lower()
    if len(normalized.encode("utf-8")) > MAX_INDEXED_USERNAME_BYTES:
        raise ValueError(
            "normalized username exceeds the safe PostgreSQL index length"
        )
    return normalized


def normalize_invite_code(code: str) -> str:
    value = str(code or "").strip().upper()
    if not value:
        raise ValueError("invite code is required")
    return value


class UserRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def get_by_username(self, username: str) -> User | None:
        return self.session.scalar(
            select(User).where(
                User.normalized_username == normalize_username(username)
            )
        )

    def upsert(
        self,
        *,
        user_id: str,
        username: str,
        display_name: str,
        password_hash: str,
        is_admin: bool = False,
        session_version: int = 0,
        created_at: datetime | None = None,
        last_login_at: datetime | None = None,
        last_campaign_id: str | None = None,
        source_payload: dict[str, Any] | None = None,
        source_checksum: str | None = None,
    ) -> User:
        entity = self.get(user_id)
        if entity is None:
            entity = User(id=user_id)
            self.session.add(entity)
        entity.username = str(username)
        entity.normalized_username = normalize_username(username)
        entity.display_name = str(display_name)
        entity.password_hash = str(password_hash)
        entity.is_admin = bool(is_admin)
        entity.session_version = int(session_version)
        entity.created_at = created_at or entity.created_at or utc_now()
        entity.last_login_at = last_login_at
        entity.last_campaign_id = last_campaign_id
        entity.source_payload = dict(source_payload or {})
        entity.source_checksum = source_checksum
        self.session.flush()
        return entity


class CampaignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, campaign_id: str, *, for_update: bool = False) -> Campaign | None:
        statement = select(Campaign).where(Campaign.id == campaign_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def upsert(
        self,
        *,
        campaign_id: str,
        slug: str,
        name: str,
        system: str,
        created_by_user_id: str | None = None,
        created_at: datetime | None = None,
        trashed_at: datetime | None = None,
        session_number: int = 1,
        settings: dict[str, Any] | None = None,
        source_payload: dict[str, Any] | None = None,
        source_checksum: str | None = None,
    ) -> Campaign:
        if system not in {"pf2e", "cosmere"}:
            raise ValueError("unknown campaign system")
        entity = self.get(campaign_id)
        if entity is None:
            entity = Campaign(id=campaign_id)
            self.session.add(entity)
        entity.slug = str(slug)
        entity.name = str(name)
        entity.system = system
        entity.created_by_user_id = created_by_user_id
        entity.created_at = created_at or entity.created_at or utc_now()
        entity.trashed_at = trashed_at
        entity.session_number = int(session_number)
        entity.settings = dict(settings or {})
        entity.source_payload = dict(source_payload or {})
        entity.source_checksum = source_checksum
        self.session.flush()
        return entity

    def upsert_membership(
        self,
        *,
        campaign_id: str,
        user_id: str,
        role: str,
        character_id: str | None = None,
        joined_at: datetime | None = None,
    ) -> CampaignMembership:
        if role not in {"gm", "player"}:
            raise ValueError("membership role must be gm or player")
        entity = self.session.get(CampaignMembership, (campaign_id, user_id))
        if entity is None:
            entity = CampaignMembership(
                campaign_id=campaign_id,
                user_id=user_id,
                joined_at=joined_at or utc_now(),
            )
            self.session.add(entity)
        entity.role = role
        entity.character_id = character_id
        entity.updated_at = utc_now()
        self.session.flush()
        return entity


class CharacterRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, character_id: str, *, for_update: bool = False) -> Character | None:
        statement = select(Character).where(Character.id == character_id)
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def upsert_identity(
        self,
        *,
        character_id: str,
        campaign_id: str,
        system: str,
        legacy_storage: str,
        legacy_file: str,
        content_checksum: str,
        display_name: str | None = None,
        source_schema_version: int | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> Character:
        if system not in {"pf2e", "cosmere"}:
            raise ValueError("unknown character system")
        if legacy_storage not in {"party_data", "cosmere_pcs"}:
            raise ValueError("unknown legacy character store")
        entity = self.get(character_id)
        if entity is None:
            entity = Character(id=character_id)
            self.session.add(entity)
        entity.campaign_id = campaign_id
        entity.system = system
        entity.display_name = display_name
        entity.legacy_storage = legacy_storage
        entity.legacy_file = str(legacy_file)
        entity.source_schema_version = source_schema_version
        entity.content_checksum = content_checksum
        entity.created_at = created_at or entity.created_at or utc_now()
        entity.updated_at = updated_at or utc_now()
        self.session.flush()
        return entity

    def set_assignment(
        self,
        *,
        campaign_id: str,
        character_id: str,
        user_id: str,
        role: str,
    ) -> CharacterAssignment:
        if role not in {"owner", "editor", "viewer"}:
            raise ValueError("unknown character assignment role")
        key = (campaign_id, character_id, user_id)
        entity = self.session.get(CharacterAssignment, key)
        if entity is None:
            entity = CharacterAssignment(
                campaign_id=campaign_id,
                character_id=character_id,
                user_id=user_id,
                created_at=utc_now(),
            )
            self.session.add(entity)
        entity.role = role
        entity.updated_at = utc_now()
        self.session.flush()
        return entity


class InvitationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, code: str, *, for_update: bool = False) -> Invitation | None:
        statement = select(Invitation).where(
            Invitation.code == normalize_invite_code(code)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def upsert(
        self,
        *,
        code: str,
        campaign_id: str,
        role: str,
        remaining_uses: int,
        character_id: str | None = None,
        creates_account: bool = True,
        created_by_user_id: str | None = None,
        created_at: datetime | None = None,
        expires_at: float | None = None,
        revoked_at: datetime | None = None,
        source_payload: dict[str, Any] | None = None,
        source_checksum: str | None = None,
    ) -> Invitation:
        if role not in {"gm", "player"}:
            raise ValueError("invite role must be gm or player")
        if int(remaining_uses) < 0:
            raise ValueError("remaining invite uses cannot be negative")
        normalized_code = normalize_invite_code(code)
        entity = self.get(normalized_code)
        if entity is None:
            entity = Invitation(code=normalized_code)
            self.session.add(entity)
        entity.campaign_id = campaign_id
        entity.role = role
        entity.character_id = character_id
        entity.creates_account = bool(creates_account)
        entity.remaining_uses = int(remaining_uses)
        entity.created_by_user_id = created_by_user_id
        entity.created_at = created_at or entity.created_at or utc_now()
        entity.expires_at = float(expires_at) if expires_at is not None else None
        entity.revoked_at = revoked_at
        entity.source_payload = dict(source_payload or {})
        entity.source_checksum = source_checksum
        self.session.flush()
        return entity


class MigrationRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, run_id: str) -> MigrationRun | None:
        return self.session.get(MigrationRun, run_id)

    def get_by_source_checksum(self, source_checksum: str) -> MigrationRun | None:
        return self.session.scalar(
            select(MigrationRun).where(
                MigrationRun.source_checksum == source_checksum
            )
        )

    def start(
        self,
        *,
        source_checksum: str,
        source_path: str | None,
        tool_version: str | None,
        entity_counts: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> MigrationRun:
        existing = self.get_by_source_checksum(source_checksum)
        if existing is not None:
            return existing
        entity = MigrationRun(
            source_checksum=source_checksum,
            source_path=source_path,
            status="running",
            tool_version=tool_version,
            entity_counts=dict(entity_counts or {}),
            details=dict(details or {}),
            started_at=utc_now(),
        )
        self.session.add(entity)
        self.session.flush()
        return entity

    def finish(
        self,
        run: MigrationRun,
        *,
        status: str,
        entity_counts: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> MigrationRun:
        if status not in {"succeeded", "failed", "verified"}:
            raise ValueError("unknown migration result status")
        run.status = status
        if entity_counts is not None:
            run.entity_counts = dict(entity_counts)
        if details is not None:
            run.details = dict(details)
        run.completed_at = utc_now()
        self.session.flush()
        return run
