"""VTT fog of war (parity P1): one row per scene that has used fog.

Additive. A scene without a row has fog off and nothing revealed.

Revision ID: 20261017_0008
Revises: 20261017_0007
"""

from alembic import op
import sqlalchemy as sa

revision = '20261017_0008'
down_revision = '20261017_0007'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vtt_fog',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('cols', sa.Integer(), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('revealed', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'scene_id', name='pk_vtt_fog'),
        sa.ForeignKeyConstraint(['campaign_id', 'scene_id'], ['vtt_scenes.campaign_id', 'vtt_scenes.id'],
                                ondelete='CASCADE', name='fk_vtt_fog_scene'),
        sa.CheckConstraint('cols >= 1 AND rows >= 1 AND cols * rows <= 262144',
                           name=op.f('ck_vtt_fog_grid_bounded')),
    )


def downgrade():
    # Fog rows are the GM's revealed areas; dropping them is destructive, so
    # refuse an offline script like the other VTT tables.
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT fog')
    op.drop_table('vtt_fog')
