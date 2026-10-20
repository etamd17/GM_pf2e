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
- ``vtt_pairing_challenges`` (M3): one short-lived pairing code per row, stored
  only as a keyed HMAC of its normalized form (section 10).
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
    LargeBinary,
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
    # M3: the campaign's table epoch. A table grant is valid only while its own
    # epoch equals this; "revoke every table" bumps it in one row (section 10).
    table_epoch: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("table_epoch >= 0", name="table_epoch_nonnegative"),
    )


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
    # E-6 (migration 0011): dynamic lighting, and a party token's default vision
    # radius in scene pixels (0: it sees only by light).
    lighting_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_vision: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default=text("0"))
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
        CheckConstraint("default_vision >= 0 AND default_vision <= 16384", name="default_vision_bounded"),
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
        CheckConstraint("kind in ('token', 'light', 'wall', 'template')", name="known_entity_kind"),
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


class VttActor(Base):
    """A tracker combatant or party character as the VTT sees it (parity P3,
    handoff 069). Derived: app.py rewrites these rows from its live state and
    deletes the ones that are gone, so nothing here is authoritative. ``id`` is
    ``cmb_<combatant instance id>`` or ``chr_<character id>``; tokens refer to
    it through their ``actorId``. ``data`` is the record vtt.live builds."""

    __tablename__ = "vtt_actors"

    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE", name="fk_vtt_actors_campaign"),
        nullable=False,
    )
    id: Mapped[str] = mapped_column(String(128), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (PrimaryKeyConstraint("campaign_id", "id", name="pk_vtt_actors"),)


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


class VttPairingChallenge(Base):
    __tablename__ = "vtt_pairing_challenges"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True,
                                    autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    # HMAC-SHA256 hex of the normalized code under a server key; never the code.
    code_mac: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (Index("ix_vtt_pairing_challenges_campaign_id", "campaign_id"),)


class VttImportedScene(Base):
    """Provenance (M5): this VTT scene was written by an import of ``source``.

    The importer only ever updates a destination row that it can prove it wrote
    from the same source; any other existing row with that id is a collision and
    the scene is refused. The row cascades away with its scene, so a row created
    natively with the same id later on carries no provenance."""

    __tablename__ = "vtt_imported_scenes"

    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "scene_id", name="pk_vtt_imported_scenes"),
        ForeignKeyConstraint(
            ["campaign_id", "scene_id"], ["vtt_scenes.campaign_id", "vtt_scenes.id"],
            name="fk_vtt_imported_scenes_scene", ondelete="CASCADE",
        ),
    )


class VttImportedEntity(Base):
    """Provenance (M5) for one imported entity; see ``VttImportedScene``. It
    cascades away when the entity is deleted."""

    __tablename__ = "vtt_imported_entities"

    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    id: Mapped[str] = mapped_column(String(128), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "scene_id", "kind", "id", name="pk_vtt_imported_entities"),
        ForeignKeyConstraint(
            ["campaign_id", "scene_id", "kind", "id"],
            ["vtt_entities.campaign_id", "vtt_entities.scene_id", "vtt_entities.kind", "vtt_entities.id"],
            name="fk_vtt_imported_entities_entity", ondelete="CASCADE",
        ),
    )


class VttAsset(Base):
    """An image in the campaign's asset store (M6): a scene background or token
    art. The bytes live in the asset backend (local disk or S3-compatible),
    keyed by campaign and content hash; this row is the campaign's claim on
    them and what authorizes reading them. The id is derived from the content
    hash, so uploading the same bytes twice is the same asset."""

    __tablename__ = "vtt_assets"

    campaign_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    id: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "id", name="pk_vtt_assets"),
        CheckConstraint("media_type in ('image/png', 'image/jpeg', 'image/webp')", name="known_media_type"),
        CheckConstraint("byte_size > 0 AND byte_size <= 26214400", name="size_bounded"),
        CheckConstraint("width >= 1 AND width <= 16384 AND height >= 1 AND height <= 16384",
                        name="dimensions_bounded"),
    )


class VttFog(Base):
    """A scene's fog of war (parity P1, handoff 060). At most one per scene,
    created by the first fog command; no row means fog off and nothing
    revealed. ``revealed`` is a bitset over ``cols`` x ``rows`` grid cells,
    row-major, least significant bit first. ``cols``/``rows`` record the grid
    the bits were written for: if the scene's grid no longer matches (only
    possible while nothing is revealed, see vtt.fog), the bits are read as
    empty."""

    __tablename__ = "vtt_fog"

    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cols: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    revealed: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "scene_id", name="pk_vtt_fog"),
        ForeignKeyConstraint(
            ["campaign_id", "scene_id"], ["vtt_scenes.campaign_id", "vtt_scenes.id"],
            name="fk_vtt_fog_scene", ondelete="CASCADE",
        ),
        CheckConstraint("cols >= 1 AND rows >= 1 AND cols * rows <= 262144", name="grid_bounded"),
    )


class VttTerrain(Base):
    """A scene's painted terrain (parity P2, handoff 065): cosmetic, like /map.
    At most one per scene; no row means nothing painted. ``cells`` holds a 4-bit
    kind per grid cell, two cells per byte, low nibble first (0 none, 1 lava,
    2 water, 3 poison, 4 blood). ``cols``/``rows`` record the grid it was
    painted on; a mismatch (only possible while nothing is painted) reads as
    nothing painted."""

    __tablename__ = "vtt_terrain"

    campaign_id: Mapped[str] = mapped_column(String(64), nullable=False)
    scene_id: Mapped[str] = mapped_column(String(128), nullable=False)
    cols: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)
    cells: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", "scene_id", name="pk_vtt_terrain"),
        ForeignKeyConstraint(
            ["campaign_id", "scene_id"], ["vtt_scenes.campaign_id", "vtt_scenes.id"],
            name="fk_vtt_terrain_scene", ondelete="CASCADE",
        ),
        CheckConstraint("cols >= 1 AND rows >= 1 AND cols * rows <= 262144", name="grid_bounded"),
    )
