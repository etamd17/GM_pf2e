"""VTT asset store (M6): one row per image a campaign holds.

Additive. The bytes live in the asset backend, keyed by campaign and content
hash; this row is what authorizes reading them.

Revision ID: 20261017_0007
Revises: 20261016_0006
"""

from alembic import op
import sqlalchemy as sa

revision = '20261017_0007'
down_revision = '20261016_0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vtt_assets',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('id', sa.String(128), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('media_type', sa.String(32), nullable=False),
        sa.Column('byte_size', sa.Integer(), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'id', name='pk_vtt_assets'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_assets_campaign_id_campaigns'),
        sa.CheckConstraint("media_type in ('image/png', 'image/jpeg', 'image/webp')",
                           name=op.f('ck_vtt_assets_known_media_type')),
        sa.CheckConstraint('byte_size > 0 AND byte_size <= 26214400', name=op.f('ck_vtt_assets_size_bounded')),
        sa.CheckConstraint('width >= 1 AND width <= 16384 AND height >= 1 AND height <= 16384',
                           name=op.f('ck_vtt_assets_dimensions_bounded')),
    )


def downgrade():
    # The rows authorize reading the stored bytes; dropping them strands every
    # uploaded image, so refuse an offline script like the other VTT tables.
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT assets')
    op.drop_table('vtt_assets')
