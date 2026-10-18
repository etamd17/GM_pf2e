"""VTT area templates and terrain (parity P2).

- vtt_entities accepts a fourth kind, 'template' (the check constraint is
  replaced; no row changes).
- vtt_terrain: one row per scene that has painted terrain.

Revision ID: 20261017_0009
Revises: 20261017_0008
"""

from alembic import op
import sqlalchemy as sa

revision = '20261017_0009'
down_revision = '20261017_0008'
branch_labels = None
depends_on = None

OLD_KINDS = "kind in ('token', 'light', 'wall')"
NEW_KINDS = "kind in ('token', 'light', 'wall', 'template')"


# The widened rule gets a new name (known_entity_kind), so each ORM check
# constraint name appears once in the migration chain's SQL.
OLD_NAME = 'ck_vtt_entities_known_kind'
NEW_NAME = 'ck_vtt_entities_known_entity_kind'


def _replace_kind_check(drop_name, add_name, expression):
    # SQLite rebuilds the table to change a constraint; with foreign keys
    # enforced, dropping vtt_entities would cascade into vtt_imported_entities,
    # so refuse rather than lose rows (the same guard as 0004). PostgreSQL
    # drops and re-adds the named constraint in place.
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite' and bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Run this migration with SQLite foreign keys off; the table rebuild would cascade')
    with op.batch_alter_table('vtt_entities', recreate='auto') as batch:
        batch.drop_constraint(op.f(drop_name), type_='check')
        batch.create_check_constraint(op.f(add_name), expression)


def upgrade():
    _replace_kind_check(OLD_NAME, NEW_NAME, NEW_KINDS)
    op.create_table(
        'vtt_terrain',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('scene_id', sa.String(128), nullable=False),
        sa.Column('cols', sa.Integer(), nullable=False),
        sa.Column('rows', sa.Integer(), nullable=False),
        sa.Column('cells', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('campaign_id', 'scene_id', name='pk_vtt_terrain'),
        sa.ForeignKeyConstraint(['campaign_id', 'scene_id'], ['vtt_scenes.campaign_id', 'vtt_scenes.id'],
                                ondelete='CASCADE', name='fk_vtt_terrain_scene'),
        sa.CheckConstraint('cols >= 1 AND rows >= 1 AND cols * rows <= 262144',
                           name=op.f('ck_vtt_terrain_grid_bounded')),
    )


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT templates and terrain')
    op.drop_table('vtt_terrain')
    # Fails (and rolls back) while any template row exists: delete them first.
    _replace_kind_check(NEW_NAME, OLD_NAME, OLD_KINDS)
