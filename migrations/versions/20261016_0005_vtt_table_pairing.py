"""VTT table pairing (M3): pairing challenges and the campaign table epoch.

Additive. A challenge row holds only a keyed HMAC of the code. The table epoch
starts at 0, which every existing grant (epoch 0) already matches.

Revision ID: 20261016_0005
Revises: 20261016_0004
"""

from alembic import op
import sqlalchemy as sa

revision = '20261016_0005'
down_revision = '20261016_0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('vtt_campaign_streams') as batch:
        batch.add_column(sa.Column('table_epoch', sa.Integer(), nullable=False, server_default=sa.text('0')))
        batch.create_check_constraint(op.f('ck_vtt_campaign_streams_table_epoch_nonnegative'), 'table_epoch >= 0')
    op.create_table(
        'vtt_pairing_challenges',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False, autoincrement=True),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('code_mac', sa.String(64), nullable=False),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_vtt_pairing_challenges'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_pairing_challenges_campaign_id_campaigns'),
        sa.UniqueConstraint('code_mac', name='uq_vtt_pairing_challenges_code_mac'),
    )
    op.create_index('ix_vtt_pairing_challenges_campaign_id', 'vtt_pairing_challenges', ['campaign_id'])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT pairing state')
    op.drop_index('ix_vtt_pairing_challenges_campaign_id', table_name='vtt_pairing_challenges')
    op.drop_table('vtt_pairing_challenges')
    with op.batch_alter_table('vtt_campaign_streams') as batch:
        batch.drop_constraint(op.f('ck_vtt_campaign_streams_table_epoch_nonnegative'), type_='check')
        batch.drop_column('table_epoch')
