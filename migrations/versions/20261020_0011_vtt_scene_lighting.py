"""VTT scene lighting (E-6): dynamic lighting on or off, and the default vision
radius of a party token.

Additive. Lighting starts off on every existing scene, so nothing the table
shows changes until the GM turns it on. The default vision is in scene pixels
(like a light's radius); 0 means a party token sees only by light.

Revision ID: 20261020_0011
Revises: 20261017_0010
"""

from alembic import op
import sqlalchemy as sa

revision = '20261020_0011'
down_revision = '20261017_0010'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vtt_scenes') as batch:
        batch.add_column(sa.Column('lighting_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')))
        batch.add_column(sa.Column('default_vision', sa.Float(), nullable=False, server_default=sa.text('0')))
        batch.create_check_constraint(op.f('ck_vtt_scenes_default_vision_bounded'),
                                      'default_vision >= 0 AND default_vision <= 16384')


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT scene lighting settings')
    with op.batch_alter_table('vtt_scenes') as batch:
        batch.drop_constraint(op.f('ck_vtt_scenes_default_vision_bounded'), type_='check')
        batch.drop_column('default_vision')
        batch.drop_column('lighting_enabled')
