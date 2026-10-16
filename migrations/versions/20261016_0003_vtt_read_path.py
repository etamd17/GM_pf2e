"""VTT read path (M1): campaign streams, scenes, entities, table pointers,
outbox and table grants. Additive; no existing table changes.

Revision ID: 20261016_0003
Revises: 20261001_0002
"""

from alembic import op
import sqlalchemy as sa

revision = '20261016_0003'
down_revision = '20261001_0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vtt_campaign_streams',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('position', sa.BigInteger(), nullable=False, server_default=sa.text('0')),
        sa.PrimaryKeyConstraint('campaign_id', name='pk_vtt_campaign_streams'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_campaign_streams_campaign_id_campaigns'),
        sa.CheckConstraint('position >= 0', name=op.f('ck_vtt_campaign_streams_position_nonnegative')),
    )
    op.create_table(
        'vtt_scenes',
        sa.Column('id', sa.String(128), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('width', sa.Float(), nullable=False),
        sa.Column('height', sa.Float(), nullable=False),
        sa.Column('grid_size', sa.Float(), nullable=False),
        sa.Column('show_grid', sa.Boolean(), nullable=False),
        sa.Column('background_asset_id', sa.String(128), nullable=True),
        sa.Column('published', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_vtt_scenes'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_scenes_campaign_id_campaigns'),
        sa.UniqueConstraint('campaign_id', 'id', name='uq_vtt_scenes_campaign_scene'),
        sa.CheckConstraint('width >= 1 AND width <= 16384', name=op.f('ck_vtt_scenes_width_bounded')),
        sa.CheckConstraint('height >= 1 AND height <= 16384', name=op.f('ck_vtt_scenes_height_bounded')),
        sa.CheckConstraint('grid_size >= 8 AND grid_size <= 1024', name=op.f('ck_vtt_scenes_grid_bounded')),
    )
    op.create_index('ix_vtt_scenes_campaign_id', 'vtt_scenes', ['campaign_id'])
    op.create_table(
        'vtt_entities',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('id', sa.String(128), nullable=False),
        sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('scene_id', 'kind', 'id', name='pk_vtt_entities'),
        sa.ForeignKeyConstraint(['campaign_id', 'scene_id'], ['vtt_scenes.campaign_id', 'vtt_scenes.id'],
                                ondelete='CASCADE', name='fk_vtt_entities_scene'),
        sa.CheckConstraint("kind in ('token', 'light', 'wall')", name=op.f('ck_vtt_entities_known_kind')),
    )
    op.create_index('ix_vtt_entities_campaign_scene', 'vtt_entities', ['campaign_id', 'scene_id'])
    op.create_table(
        'vtt_table_pointers',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=True),
        sa.PrimaryKeyConstraint('campaign_id', name='pk_vtt_table_pointers'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_table_pointers_campaign_id_campaigns'),
        sa.ForeignKeyConstraint(['campaign_id', 'scene_id'], ['vtt_scenes.campaign_id', 'vtt_scenes.id'],
                                ondelete='SET NULL', name='fk_vtt_table_pointers_scene'),
    )
    op.create_table(
        'vtt_outbox',
        # INTEGER on SQLite so it aliases the rowid and autoincrements; BIGINT elsewhere.
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False, autoincrement=True),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('position', sa.BigInteger(), nullable=False),
        sa.Column('event', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_vtt_outbox'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_outbox_campaign_id_campaigns'),
        sa.UniqueConstraint('campaign_id', 'position', name='uq_vtt_outbox_campaign_position'),
        sa.CheckConstraint('position >= 1', name=op.f('ck_vtt_outbox_position_positive')),
    )
    op.create_table(
        'vtt_table_grants',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('verifier', sa.String(64), nullable=False),
        sa.Column('epoch', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_vtt_table_grants'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_table_grants_campaign_id_campaigns'),
        sa.UniqueConstraint('verifier', name='uq_vtt_table_grants_verifier'),
        sa.CheckConstraint('epoch >= 0', name=op.f('ck_vtt_table_grants_epoch_nonnegative')),
    )
    op.create_index('ix_vtt_table_grants_campaign_id', 'vtt_table_grants', ['campaign_id'])


def downgrade():
    # VTT tables hold only VTT data; dropping them loses no other data, but it
    # does lose table grants and scene state, so refuse offline scripts the same
    # way 0002 does rather than emit a silent destructive script.
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT state')
    op.drop_index('ix_vtt_table_grants_campaign_id', table_name='vtt_table_grants')
    op.drop_table('vtt_table_grants')
    op.drop_table('vtt_outbox')
    op.drop_table('vtt_table_pointers')
    op.drop_index('ix_vtt_entities_campaign_scene', table_name='vtt_entities')
    op.drop_table('vtt_entities')
    op.drop_index('ix_vtt_scenes_campaign_id', table_name='vtt_scenes')
    op.drop_table('vtt_scenes')
    op.drop_table('vtt_campaign_streams')
