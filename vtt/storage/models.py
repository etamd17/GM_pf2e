"""VTT storage (M1, M2). PostgreSQL is authoritative (VTT-ADR-0002 sections 5, 10, 11).

These tables share the app's declarative ``Base`` so Alembic and the existing
explicit ``Database.create_schema`` see them. Nothing here is read by the
legacy ``/map``.

- ``vtt_campaign_streams``: the commit-ordered position per campaign. Every
  durable change that can affect a projection locks this row and increments it
  in the same transaction as the change and its outbox row, so positions become
  visible in commit order (section 5). Server-only; never on the wire.
- ``vtt_scenes`` / ``vtt_entities``: canonical scene state. Entities keep their
  canonical fields in ``data``; ``hidden`` is a column because the table
  projection filters on it.
- ``vtt_table_pointers``: the server-owned published table scene (section 3).
  A table never chooses a campaign or scene.
- ``vtt_outbox``: one row per committed position, naming what changed.
- ``vtt_table_grants``: table device credentials. Only a verifier is stored
  (section 10). M1 creates the table; pairing/redemption is M3.
- ``vtt_command_receipts`` (M2): one durable receipt per
  ``(campaign, principal kind, principal id, commandId)`` (section 7), kept for
  the campaign's lifetime so an old retry can never apply twice.

Scene and entity ids are chosen by the client on create (so a retried create is
idempotent), so they are unique per campaign, not globally: scenes are keyed
``(campaign_id, id)`` and entities ``(campaign_id, scene_id, kind, id)``
(migration 0004). A global key would let one campaign learn that an id exists
in another.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.persistence.models import Base, utc_now

ENTITY_KINDS = ("token", "light", "wall")
RECEIPT_REJECTION_CODES = ("stale", "conflict", "validation-failed", "rule-violation")


class VttCampaignStream(Base):
    __tablename__ = "vtt_campaign_streams"

    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (CheckConstraint("position >= 0", name="position_nonnegative"),)


class VttScene(Base):
    __tablename__ = "vtt_scenes"

    id: Mapped[str] = mapped_column(String(128), nullable=False)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    grid_size: Mapped[float] = mapped_column(Float, nullable=False)
    show_grid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    background_asset_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "id", name="pk_vtt_scenes"),
        # Redundant with the key since 0004, kept because the entity and pointer
        # foreign keys reference it.
        UniqueConstraint("campaign_id", "id", name="uq_vtt_scenes_campaign_scene"),
        CheckConstraint("width >= 1 AND width <= 16384", name="width_bounded"),
        CheckConstraint("height >= 1 AND height <= 16384", name="height_bounded"),
        CheckConstraint("grid_size >= 8 AND grid_size <= 1024", name="grid_bounded"),
        Index("ix_vtt_scenes_campaign_id", "campaign_id"),
    )


class VttEntity(Base):
    __tablename__ = "vtt_entities"

    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    id: Mapped[str] = mapped_column(String(128), nullable=False)
    hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "scene_id", "kind", "id", name="pk_vtt_entities"),
        ForeignKeyConstraint(
            ["campaign_id", "scene_id"], ["vtt_scenes.campaign_id", "vtt_scenes.id"],
            name="fk_vtt_entities_scene", ondelete="CASCADE",
        ),
        CheckConstraint("kind in ('token', 'light', 'wall')", name="known_kind"),
        Index("ix_vtt_entities_campaign_scene", "campaign_id", "scene_id"),
    )


class VttTablePointer(Base):
    __tablename__ = "vtt_table_pointers"

    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    scene_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "scene_id"], ["vtt_scenes.campaign_id", "vtt_scenes.id"],
            name="fk_vtt_table_pointers_scene", ondelete="SET NULL",
        ),
    )


class VttOutbox(Base):
    __tablename__ = "vtt_outbox"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                    autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("campaign_id", "position", name="uq_vtt_outbox_campaign_position"),
        CheckConstraint("position >= 1", name="position_positive"),
    )


class VttTableGrant(Base):
    __tablename__ = "vtt_table_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # HMAC-SHA256 hex of the credential under a server key; never the credential.
    verifier: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        CheckConstraint("epoch >= 0", name="epoch_nonnegative"),
        Index("ix_vtt_table_grants_campaign_id", "campaign_id"),
    )


class VttCommandReceipt(Base):
    __tablename__ = "vtt_command_receipts"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                    autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    principal_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    command_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # SHA-256 hex of the canonical request (schemaVersion, campaign, scene, type,
    # expected, payload); a retry with another digest is ``command-id-reused``.
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        UniqueConstraint("campaign_id", "principal_kind", "principal_id", "command_id",
                         name="uq_vtt_command_receipts_key"),
        CheckConstraint("principal_kind in ('member', 'table')", name="known_principal_kind"),
        CheckConstraint(
            "(outcome = 'applied' AND code IS NULL) OR (outcome = 'rejected' AND code in "
            "('stale', 'conflict', 'validation-failed', 'rule-violation'))",
            name="outcome_code_consistent",
        ),
    )
