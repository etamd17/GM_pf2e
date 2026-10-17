"""VTT import provenance (M5): which scenes and entities an import wrote.

Additive. The legacy importer updates an existing destination row only when
one of these rows proves it wrote that row from the same source; anything else
with that id is a collision and the scene is refused (handoff 048). Each row
cascades away with the scene or entity it describes.

Revision ID: 20261016_0006
Revises: 20261016_0005
"""

from alembic import op
import sqlalchemy as sa

revision = '20261016_0006'
down_revision = '20261016_0005'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'vtt_imported_scenes',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'scene_id', name='pk_vtt_imported_scenes'),
        sa.ForeignKeyConstraint(['campaign_id', 'scene_id'], ['vtt_scenes.campaign_id', 'vtt_scenes.id'],
                                ondelete='CASCADE', name='fk_vtt_imported_scenes_scene'),
    )
    op.create_table(
        'vtt_imported_entities',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('id', sa.String(128), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'scene_id', 'kind', 'id', name='pk_vtt_imported_entities'),
        sa.ForeignKeyConstraint(
            ['campaign_id', 'scene_id', 'kind', 'id'],
            ['vtt_entities.campaign_id', 'vtt_entities.scene_id', 'vtt_entities.kind', 'vtt_entities.id'],
            ondelete='CASCADE', name='fk_vtt_imported_entities_entity'),
    )


def downgrade():
    # Dropping provenance makes every earlier import look like a collision on a
    # re-run (refused, never overwritten), so it is safe, but not reversible.
    op.drop_table('vtt_imported_entities')
    op.drop_table('vtt_imported_scenes')
