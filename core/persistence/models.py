"""Relational identity and workflow models for the transactional store.

The existing JSON documents remain the runtime authority in PR4A.  These
tables intentionally store account/campaign identity, ownership, workflow,
and migration evidence without copying mutable character-sheet payloads into
SQL.  That keeps the first migration additive and makes a later authority
cutover explicit and reversible.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return an aware UTC timestamp suitable for SQLAlchemy defaults."""

    return datetime.now(timezone.utc)


def new_id() -> str:
    """Generate the same compact identifier shape used by the JSON stores."""

    return uuid.uuid4().hex


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base exported for Alembic and explicit test setup."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Text avoids making the import layer reject a pre-existing account merely
    # because an older build did not enforce the current UI length limit.
    username: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_username: Mapped[str] = mapped_column(
        Text, nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    session_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "session_version >= 0", name="session_version_nonnegative"
        ),
    )

class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    trashed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    session_number: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    # Retaining the canonical source document makes import verification and a
    # deterministic rollback export possible while JSON remains authoritative.
    source_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("system in ('pf2e', 'cosmere')", name="known_system"),
        CheckConstraint(
            "session_number >= 0", name="session_number_nonnegative"
        ),
    )


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_storage: Mapped[str] = mapped_column(String(32), nullable=False)
    legacy_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "id", name="uq_characters_campaign_id_id"
        ),
        UniqueConstraint(
            "campaign_id",
            "legacy_storage",
            "legacy_file",
            name="uq_characters_legacy_locator",
        ),
        CheckConstraint(
            "legacy_storage in ('party_data', 'cosmere_pcs')",
            name="known_legacy_storage",
        ),
        CheckConstraint("system in ('pf2e', 'cosmere')", name="known_system"),
    )


class CampaignMembership(Base):
    __tablename__ = "campaign_memberships"

    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("role in ('gm', 'player')", name="known_role"),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_campaign_memberships_character_campaign",
            ondelete="RESTRICT",
        ),
        Index("ix_campaign_memberships_user_id", "user_id"),
    )


class CharacterAssignment(Base):
    __tablename__ = "character_assignments"

    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    character_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint(
            "role in ('owner', 'editor', 'viewer')", name="known_role"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_character_assignments_character_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "user_id"],
            ["campaign_memberships.campaign_id", "campaign_memberships.user_id"],
            name="fk_character_assignments_membership",
            ondelete="CASCADE",
        ),
        Index("ix_character_assignments_user_id", "user_id"),
        # A normal UNIQUE(character_id, role) would also limit editors and
        # viewers to one each.  The filtered index enforces only the invariant
        # that matters: a character has at most one owner.
        Index(
            "uq_character_assignments_one_owner",
            "campaign_id",
            "character_id",
            unique=True,
            sqlite_where=text("role = 'owner'"),
            postgresql_where=text("role = 'owner'"),
        ),
    )


class Invitation(Base):
    __tablename__ = "invites"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    creates_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    remaining_uses: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    # Legacy invites.json stores Unix epoch floats. Keeping the same lossless
    # representation makes checksum verification and export deterministic.
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        CheckConstraint("role in ('gm', 'player')", name="known_role"),
        CheckConstraint(
            "remaining_uses >= 0", name="remaining_uses_nonnegative"
        ),
        UniqueConstraint(
            "code", "campaign_id", name="uq_invites_code_campaign_id"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_invites_character_campaign",
            ondelete="RESTRICT",
        ),
    )


class InviteRedemption(Base):
    __tablename__ = "invite_redemptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    invitation_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="RESTRICT"),
        nullable=False,
    )
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    redeemed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )

    __table_args__ = (
        UniqueConstraint(
            "invitation_code",
            "user_id",
            name="uq_invite_redemptions_invitation_user",
        ),
        ForeignKeyConstraint(
            ["invitation_code", "campaign_id"],
            ["invites.code", "invites.campaign_id"],
            name="fk_invite_redemptions_invite_campaign",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_invite_redemptions_character_campaign",
            ondelete="RESTRICT",
        ),
    )


class Draft(Base):
    __tablename__ = "character_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_character_drafts_character_campaign",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "user_id"],
            ["campaign_memberships.campaign_id", "campaign_memberships.user_id"],
            name="fk_character_drafts_membership",
            ondelete="CASCADE",
        ),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    actor_user_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class MigrationRun(Base):
    __tablename__ = "migration_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    source_checksum: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_counts: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
