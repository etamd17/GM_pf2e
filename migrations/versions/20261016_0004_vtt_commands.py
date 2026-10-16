"""VTT commands (M2): campaign-scoped scene and entity keys, and durable
command receipts.

Scene and entity ids are chosen by the client on create, so they are unique per
campaign rather than globally. The primary keys become ``(campaign_id, id)``
and ``(campaign_id, scene_id, kind, id)``; the existing
``uq_vtt_scenes_campaign_scene`` stays because the entity and pointer foreign
keys reference it. No row changes.

Revision ID: 20261016_0004
Revises: 20261016_0003
"""

from alembic import op
import sqlalchemy as sa

revision = '20261016_0004'
down_revision = '20261016_0003'
branch_labels = None
depends_on = None


def _rekey(table, columns):
    # SQLite cannot alter a primary key in place; batch mode rebuilds the table,
    # dropping the old one. With foreign keys enforced, dropping vtt_scenes would
    # cascade-delete every entity, so refuse rather than lose rows. (Alembic's
    # own engine leaves them off; PRAGMA cannot change inside a transaction.)
    # PostgreSQL drops and re-adds the named constraint in place.
    bind = op.get_bind()
    if bind.dialect.name == 'sqlite' and bind.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Run this migration with SQLite foreign keys off; the table rebuild would cascade')
    with op.batch_alter_table(table, recreate='auto') as batch:
        batch.drop_constraint(f'pk_{table}', type_='primary')
        batch.create_primary_key(f'pk_{table}', columns)


def upgrade():
    _rekey('vtt_entities', ['campaign_id', 'scene_id', 'kind', 'id'])
    _rekey('vtt_scenes', ['campaign_id', 'id'])
    op.create_table(
        'vtt_command_receipts',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False, autoincrement=True),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('principal_kind', sa.String(16), nullable=False),
        sa.Column('principal_id', sa.String(128), nullable=False),
        sa.Column('command_id', sa.String(128), nullable=False),
        sa.Column('digest', sa.String(64), nullable=False),
        sa.Column('outcome', sa.String(16), nullable=False),
        sa.Column('code', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_vtt_command_receipts'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE',
                                name='fk_vtt_command_receipts_campaign_id_campaigns'),
        sa.UniqueConstraint('campaign_id', 'principal_kind', 'principal_id', 'command_id',
                            name='uq_vtt_command_receipts_key'),
        sa.CheckConstraint("principal_kind in ('member', 'table')",
                           name=op.f('ck_vtt_command_receipts_known_principal_kind')),
        sa.CheckConstraint(
            "(outcome = 'applied' AND code IS NULL) OR (outcome = 'rejected' AND code in "
            "('stale', 'conflict', 'validation-failed', 'rule-violation'))",
            name=op.f('ck_vtt_command_receipts_outcome_code_consistent')),
    )


def downgrade():
    # Receipts are the no-double-apply record; dropping them is destructive, so
    # refuse an offline script the same way 0003 does.
    if op.get_context().as_sql:
        raise RuntimeError('Refusing an offline downgrade that drops VTT command receipts')
    op.drop_table('vtt_command_receipts')
    # Fails (and rolls back) if two campaigns now share a scene or entity id.
    _rekey('vtt_scenes', ['id'])
    _rekey('vtt_entities', ['scene_id', 'kind', 'id'])
