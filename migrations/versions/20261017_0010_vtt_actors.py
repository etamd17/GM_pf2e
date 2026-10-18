"""VTT live actors (parity P3): a mirror of the tracker's live state.

Additive. One row per tracker combatant or party character the VTT can link a
token to. The rows are derived: app.py rewrites them from its own state, so a
lost row is restored by the next tracker change.

Revision ID: 20261017_0010
Revises: 20261017_0009
"""

from alembic import op
import sqlalchemy as sa

revision = '20261017_0010'
down_revision = '20261017_0009'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vtt_actors',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('id', sa.String(128), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'id', name='pk_vtt_actors'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_actors_campaign'),
    )


def downgrade():
    # Derived rows only (rebuilt from the tracker), so dropping them loses nothing.
    op.drop_table('vtt_actors')
