"""create the additive transactional persistence foundation

Revision ID: 20260930_0001
Revises: None
Create Date: 2026-09-30
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260930_0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("normalized_username", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("session_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_campaign_id", sa.String(length=64), nullable=True),
        sa.Column("source_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "session_version >= 0",
            name=op.f("ck_users_session_version_nonnegative"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("normalized_username", name="uq_users_normalized_username"),
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("system", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_number", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("settings", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("source_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "system in ('pf2e', 'cosmere')",
            name=op.f("ck_campaigns_known_system"),
        ),
        sa.CheckConstraint(
            "session_number >= 0",
            name=op.f("ck_campaigns_session_number_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_campaigns_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
    )

    op.create_table(
        "characters",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("system", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("legacy_storage", sa.String(length=32), nullable=False),
        sa.Column("legacy_file", sa.Text(), nullable=False),
        sa.Column("source_schema_version", sa.Integer(), nullable=True),
        sa.Column("content_checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "legacy_storage in ('party_data', 'cosmere_pcs')",
            name=op.f("ck_characters_known_legacy_storage"),
        ),
        sa.CheckConstraint(
            "system in ('pf2e', 'cosmere')",
            name=op.f("ck_characters_known_system"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_characters_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_characters"),
        sa.UniqueConstraint(
            "campaign_id", "id", name="uq_characters_campaign_id_id"
        ),
        sa.UniqueConstraint(
            "campaign_id",
            "legacy_storage",
            "legacy_file",
            name="uq_characters_legacy_locator",
        ),
    )
    op.create_index("ix_characters_campaign_id", "characters", ["campaign_id"])

    op.create_table(
        "campaign_memberships",
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role in ('gm', 'player')",
            name=op.f("ck_campaign_memberships_known_role"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_campaign_memberships_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_campaign_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_campaign_memberships_character_campaign",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("campaign_id", "user_id", name="pk_campaign_memberships"),
    )
    op.create_index(
        "ix_campaign_memberships_user_id",
        "campaign_memberships",
        ["user_id"],
    )

    op.create_table(
        "character_assignments",
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role in ('owner', 'editor', 'viewer')",
            name=op.f("ck_character_assignments_known_role"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_character_assignments_character_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "user_id"],
            ["campaign_memberships.campaign_id", "campaign_memberships.user_id"],
            name="fk_character_assignments_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "campaign_id",
            "character_id",
            "user_id",
            name="pk_character_assignments",
        ),
    )
    op.create_index(
        "ix_character_assignments_user_id",
        "character_assignments",
        ["user_id"],
    )
    op.create_index(
        "uq_character_assignments_one_owner",
        "character_assignments",
        ["campaign_id", "character_id"],
        unique=True,
        postgresql_where=sa.text("role = 'owner'"),
        sqlite_where=sa.text("role = 'owner'"),
    )

    op.create_table(
        "invites",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=True),
        sa.Column("creates_account", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("remaining_uses", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.Column("source_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.CheckConstraint(
            "role in ('gm', 'player')",
            name=op.f("ck_invites_known_role"),
        ),
        sa.CheckConstraint(
            "remaining_uses >= 0",
            name=op.f("ck_invites_remaining_uses_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_invites_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_invites_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_invites_character_campaign",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("code", name="pk_invites"),
        sa.UniqueConstraint("code", "campaign_id", name="uq_invites_code_campaign_id"),
    )
    op.create_index("ix_invites_campaign_id", "invites", ["campaign_id"])
    op.create_index("ix_invites_character_id", "invites", ["character_id"])

    op.create_table(
        "invite_redemptions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("invitation_code", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=True),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_invite_redemptions_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_invite_redemptions_campaign_id_campaigns",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_code", "campaign_id"],
            ["invites.code", "invites.campaign_id"],
            name="fk_invite_redemptions_invite_campaign",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_invite_redemptions_character_campaign",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invite_redemptions"),
        sa.UniqueConstraint(
            "invitation_code",
            "user_id",
            name="uq_invite_redemptions_invitation_user",
        ),
    )
    op.create_index(
        "ix_invite_redemptions_invitation_code",
        "invite_redemptions",
        ["invitation_code"],
    )
    op.create_index(
        "ix_invite_redemptions_user_id", "invite_redemptions", ["user_id"]
    )

    op.create_table(
        "character_drafts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("campaign_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("character_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "revision >= 0",
            name=op.f("ck_character_drafts_revision_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_character_drafts_campaign_id_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "character_id"],
            ["characters.campaign_id", "characters.id"],
            name="fk_character_drafts_character_campaign",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id", "user_id"],
            ["campaign_memberships.campaign_id", "campaign_memberships.user_id"],
            name="fk_character_drafts_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_character_drafts"),
    )
    op.create_index(
        "ix_character_drafts_campaign_id", "character_drafts", ["campaign_id"]
    )
    op.create_index(
        "ix_character_drafts_user_id", "character_drafts", ["user_id"]
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("campaign_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_events_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name="fk_audit_events_campaign_id_campaigns",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index(
        "ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"]
    )
    op.create_index(
        "ix_audit_events_campaign_id", "audit_events", ["campaign_id"]
    )
    op.create_index(
        "ix_audit_events_occurred_at", "audit_events", ["occurred_at"]
    )

    op.create_table(
        "migration_runs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tool_version", sa.String(length=64), nullable=True),
        sa.Column("entity_counts", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_migration_runs"),
        sa.UniqueConstraint("source_checksum", name="uq_migration_runs_source_checksum"),
    )


def downgrade() -> None:
    op.drop_table("migration_runs")
    op.drop_table("audit_events")
    op.drop_table("character_drafts")
    op.drop_table("invite_redemptions")
    op.drop_table("invites")
    op.drop_table("character_assignments")
    op.drop_table("campaign_memberships")
    op.drop_table("characters")
    op.drop_table("campaigns")
    op.drop_table("users")
