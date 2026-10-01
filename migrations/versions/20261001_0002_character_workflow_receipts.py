"""Independent character workflow receipts; never cascade away retry history.

Revision ID: 20261001_0002
Revises: 20260930_0001
"""

from alembic import op
import sqlalchemy as sa

revision = '20261001_0002'
down_revision = '20260930_0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('character_workflow_receipts',
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('author_id', sa.String(64), nullable=False),
        sa.Column('draft_id', sa.String(64), nullable=True),
        sa.Column('target_id', sa.String(64), nullable=True),
        sa.Column('operation', sa.String(32), nullable=False),
        sa.Column('key_hash', sa.String(64), nullable=False),
        sa.Column('request_digest', sa.String(64), nullable=False),
        sa.Column('state', sa.String(32), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name='pk_character_workflow_receipts'),
        sa.UniqueConstraint('campaign_id', 'author_id', 'operation', 'key_hash',
                            name='uq_character_workflow_receipts_request'),
        sa.CheckConstraint(
            "(operation = 'create_draft' AND state IN ('committed', 'invalidated')) OR "
            "(operation = 'publish' AND state IN ('publishing', 'committed', 'failed', 'invalidated')) OR "
            "(operation IN ('discard', 'invalidate_member', 'invalidate_target') AND state = 'committed')",
            name=op.f('ck_character_workflow_receipts_operation_state')))
    op.create_index('ix_character_workflow_receipts_campaign_id',
                    'character_workflow_receipts', ['campaign_id'])


def downgrade():
    # Offline SQL cannot prove the data is disposable. Refuse rather than emit
    # a script that could silently erase deduplication and recovery evidence.
    if op.get_context().as_sql:
        raise RuntimeError('workflow downgrade requires an online empty-store check')
    connection = op.get_bind()
    for table in ('character_workflow_receipts', 'character_drafts'):
        if connection.scalar(sa.text('SELECT COUNT(*) FROM ' + table)):
            raise RuntimeError('workflow records exist; export and resolve them before downgrade')
    op.drop_index('ix_character_workflow_receipts_campaign_id', table_name='character_workflow_receipts')
    op.drop_table('character_workflow_receipts')
