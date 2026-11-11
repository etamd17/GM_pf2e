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
    # PE-01 section 7: an archived character keeps its row (and its state,
    # ledger and event history) but leaves name resolution and file export.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "id", name="uq_characters_campaign_id_id"
        ),
        # A file may be re-created after its character is archived, so the
        # locator is unique only among live rows.
        Index(
            "uq_characters_active_legacy_locator",
            "campaign_id",
            "legacy_storage",
            "legacy_file",
            unique=True,
            sqlite_where=text("archived_at IS NULL"),
            postgresql_where=text("archived_at IS NULL"),
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


class CharacterWorkflowReceipt(Base):
    """Durable deduplication/recovery evidence, independent of deletable rows."""

    __tablename__ = 'character_workflow_receipts'

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    author_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict,
                                                  server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                               default=utc_now, onupdate=utc_now)

    __table_args__ = (
        UniqueConstraint('campaign_id', 'author_id', 'operation', 'key_hash',
                         name='uq_character_workflow_receipts_request'),
        CheckConstraint(
            "(operation = 'create_draft' AND state IN ('committed', 'invalidated')) OR "
            "(operation = 'publish' AND state IN ('publishing', 'committed', 'failed', 'invalidated')) OR "
            "(operation IN ('discard', 'invalidate_member', 'invalidate_target') AND state = 'committed')",
            name='operation_state'),
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
    """Evidence of one import or export.

    Legacy whole-store imports keep ``campaign_id``/``stream_epoch`` NULL and
    stay unique by checksum.  Per-campaign runs (PE-01 section 7) record the
    campaign's ``stream_epoch`` read under the migration gate; an attempt that
    fails before promotion or the export swap is marked ``aborted`` and a
    retry inserts a new row.  There is deliberately no foreign key to
    ``campaigns``: a run is evidence and must outlive the campaign it moved.
    """

    __tablename__ = "migration_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    source_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
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
    campaign_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="import", server_default=text("'import'")
    )
    stream_epoch: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("kind in ('import', 'export')", name="known_kind"),
        CheckConstraint(
            "status in ('running', 'succeeded', 'failed', 'verified', "
            "'exported', 'aborted')",
            name="known_status",
        ),
        # Legacy runs: no campaign, no epoch, import only, legacy statuses.
        # Campaign runs: both columns set and the epoch is a real one.
        CheckConstraint(
            "(campaign_id IS NULL AND stream_epoch IS NULL AND kind = 'import' "
            "AND status NOT IN ('exported', 'aborted')) OR "
            "(campaign_id IS NOT NULL AND stream_epoch IS NOT NULL "
            "AND stream_epoch >= 1)",
            name="scope",
        ),
        CheckConstraint(
            "status <> 'exported' OR kind = 'export'", name="exported_is_export"
        ),
        Index(
            "uq_migration_runs_legacy_source_checksum",
            "source_checksum",
            unique=True,
            sqlite_where=text("campaign_id IS NULL"),
            postgresql_where=text("campaign_id IS NULL"),
        ),
        Index(
            "uq_migration_runs_campaign_attempt",
            "campaign_id",
            "kind",
            "source_checksum",
            "stream_epoch",
            unique=True,
            sqlite_where=text("campaign_id IS NOT NULL AND status <> 'aborted'"),
            postgresql_where=text("campaign_id IS NOT NULL AND status <> 'aborted'"),
        ),
    )


# --------------------------------------------------------------------------
# PE-12: transactional game state (PE-01 sections 1, 2, 4 and 7).
#
# These tables are created empty.  Nothing reads or writes them until a
# campaign's authority row says ``db`` (PE-13/PE-24); until then the JSON
# files stay authoritative and these rows, if any, are a stale baseline.
# Staleness is derived from ``campaign_state_authority.authority = 'file'``
# rather than stored per row, so it can never disagree with the authority.

RULES_PROFILES = ("pf2e", "dnd5e-2014", "dnd5e-2024")
_RULES_PROFILE_CHECK = "rules_profile in ('pf2e', 'dnd5e-2014', 'dnd5e-2024')"


class CharacterState(Base):
    """One versioned document per character and domain (``build``/``play``)."""

    __tablename__ = "character_states"

    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    character_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    domain: Mapped[str] = mapped_column(String(16), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    rules_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    # The ruleset/content pin (PE-01 section 1), kept beside the payload so a
    # reader never has to parse the payload to learn which rules apply.
    content: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("domain in ('build', 'play')", name="known_domain"),
        CheckConstraint(_RULES_PROFILE_CHECK, name="known_rules_profile"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_character_states_character_campaign",
            ondelete="CASCADE",
        ),
    )


class EncounterState(Base):
    """A live or ended encounter: order, turn, creature vitals and effects."""

    __tablename__ = "encounter_states"

    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encounter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    round: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    turn: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Ordered combatant ids.
    initiative: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    # Creature vitals and effects keyed by combatant id; PC vitals live in
    # the character's ``play`` state, never here.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    clock_anchors: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    rules_profile: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("status in ('active', 'ended')", name="known_status"),
        CheckConstraint(_RULES_PROFILE_CHECK, name="known_rules_profile"),
        CheckConstraint("round >= 0", name="round_nonnegative"),
        CheckConstraint("turn >= 0", name="turn_nonnegative"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
        Index(
            "uq_encounter_states_one_active",
            "campaign_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class EncounterCombatant(Base):
    """A combatant row; a PC row points at its character in the same campaign."""

    __tablename__ = "encounter_combatants"

    campaign_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    combatant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    character_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    creature_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The file-era instance key, so an import maps each combatant exactly once.
    legacy_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("kind in ('pc', 'creature')", name="known_kind"),
        CheckConstraint(
            "kind <> 'pc' OR character_id IS NOT NULL", name="pc_has_character"
        ),
        ForeignKeyConstraint(
            ["campaign_id", "encounter_id"],
            ["encounter_states.campaign_id", "encounter_states.encounter_id"],
            name="fk_encounter_combatants_encounter",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_encounter_combatants_character_campaign",
            ondelete="CASCADE",
        ),
        Index(
            "ix_encounter_combatants_character",
            "campaign_id",
            "character_id",
        ),
    )


class CampaignClock(Base):
    """The campaign's world and downtime clocks (PE-01 section 5)."""

    __tablename__ = "campaign_clocks"

    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    clock: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        CheckConstraint("clock in ('world', 'downtime')", name="known_clock"),
        CheckConstraint("value >= 0", name="value_nonnegative"),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )


class CampaignSession(Base):
    """A play session; ids are never reused, at most one is active."""

    __tablename__ = "campaign_sessions"

    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nullable: file-era ids are backfilled as ended rows with unknown times.
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("status in ('active', 'ended')", name="known_status"),
        CheckConstraint(
            "status <> 'active' OR (ended_at IS NULL AND ended_by IS NULL)",
            name="active_not_ended",
        ),
        Index(
            "uq_campaign_sessions_one_active",
            "campaign_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class CampaignStateAuthority(Base):
    """Which store is authoritative for a campaign's game state (section 7)."""

    __tablename__ = "campaign_state_authority"

    campaign_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    authority: Mapped[str] = mapped_column(String(16), nullable=False)
    stream_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    source_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "authority in ('file', 'migrating', 'db', 'exporting')",
            name="known_authority",
        ),
        CheckConstraint("stream_epoch >= 1", name="stream_epoch_positive"),
    )
