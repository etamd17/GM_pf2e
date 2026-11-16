"""Command stream: per-campaign stream rows, command receipts and game events.

``campaign_streams`` is lock-order step 1 and holds the campaign's seq
counter; rows are created lazily under the lock (``core/persistence/
streams.py``), so no backfill.  ``command_receipts`` and ``game_events`` have
no foreign key: both outlive any record they name (contract section 3;
task PE-14a).

Revision ID: 20261115_0005
Revises: 20261111_0004
"""

from alembic import op
import sqlalchemy as sa

revision = '20261115_0005'
down_revision = '20261111_0004'
branch_labels = None
depends_on = None

_NEW_TABLES = ('game_events', 'command_receipts', 'campaign_streams')
_PRINCIPAL_KIND_CHECK = "principal_kind in ('user', 'integration_token')"
_RULES_PROFILE_CHECK = "rules_profile in ('pf2e', 'dnd5e-2014', 'dnd5e-2024')"


def _prepare_sqlite(connection):
    """Run the whole SQLite revision as one transaction (see 20261110_0003)."""

    if not connection.connection.dbapi_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')


def _check(table, name, condition):
    return sa.CheckConstraint(condition, name=op.f('ck_%s_%s' % (table, name)))


def _json(name, default):
    return sa.Column(name, sa.JSON(), server_default=sa.text(default), nullable=False)


def upgrade():
    context = op.get_context()
    if context.dialect.name == 'sqlite' and not context.as_sql:
        _prepare_sqlite(op.get_bind())

    op.create_table('campaign_streams',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('last_seq', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
        _check('campaign_streams', 'last_seq_nonnegative', 'last_seq >= 0'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_campaign_streams_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', name='pk_campaign_streams'))

    op.create_table('command_receipts',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('principal_kind', sa.String(32), nullable=False),
        sa.Column('principal_id', sa.String(128), nullable=False),
        sa.Column('operation_id', sa.String(128), nullable=False),
        sa.Column('digest', sa.String(64), nullable=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('applied_status', sa.String(16), nullable=True),
        sa.Column('code', sa.String(32), nullable=True),
        _json('revisions', "'{}'"),
        _json('event_ids', "'[]'"),
        sa.Column('seq_first', sa.BigInteger(), nullable=True),
        sa.Column('seq_last', sa.BigInteger(), nullable=True),
        sa.Column('stream_epoch', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        _check('command_receipts', 'known_principal_kind', _PRINCIPAL_KIND_CHECK),
        _check('command_receipts', 'known_status', "status in ('committed', 'rejected')"),
        _check('command_receipts', 'status_fields',
               "(status = 'committed' AND applied_status IS NOT NULL"
            " AND applied_status IN ('committed', 'pending')"
               " AND code IS NULL)"
               " OR (status = 'rejected' AND applied_status IS NULL AND code IS NOT NULL"
               " AND seq_first IS NULL)"),
        _check('command_receipts', 'digest_unless_abandoned',
               "(digest IS NULL AND code IS NOT NULL AND code = 'abandoned')"
               " OR (digest IS NOT NULL AND length(digest) = 64"
               " AND (code IS NULL OR code <> 'abandoned'))"),
        _check('command_receipts', 'seq_range',
               "(seq_first IS NULL AND seq_last IS NULL)"
               " OR (seq_first IS NOT NULL AND seq_last IS NOT NULL"
            " AND seq_first >= 1 AND seq_last >= seq_first)"),
        _check('command_receipts', 'stream_epoch_positive', 'stream_epoch >= 1'),
        sa.PrimaryKeyConstraint('campaign_id', 'principal_kind', 'principal_id',
                                'operation_id', name='pk_command_receipts'))

    op.create_table('game_events',
        sa.Column('event_id', sa.String(32), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('seq', sa.BigInteger(), nullable=False),
        sa.Column('stream_epoch', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(128), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('session_id', sa.String(128), nullable=True),
        sa.Column('encounter_id', sa.String(64), nullable=True),
        sa.Column('operation_id', sa.String(128), nullable=True),
        sa.Column('action_id', sa.String(64), nullable=True),
        sa.Column('causation_event_id', sa.String(32), nullable=True),
        sa.Column('origin', sa.String(32), nullable=False),
        sa.Column('principal_kind', sa.String(32), nullable=True),
        sa.Column('principal_id', sa.String(128), nullable=True),
        sa.Column('rules_profile', sa.String(32), nullable=False),
        _json('actor', "'{}'"),
        _json('subjects', "'[]'"),
        _json('changes', "'[]'"),
        _json('dice', "'[]'"),
        _json('explain', "'[]'"),
        _json('visibility', "'{}'"),
        _check('game_events', 'event_id_length', 'length(event_id) = 32'),
        _check('game_events', 'seq_positive', 'seq >= 1'),
        _check('game_events', 'stream_epoch_positive', 'stream_epoch >= 1'),
        _check('game_events', 'known_rules_profile', _RULES_PROFILE_CHECK),
        _check('game_events', 'principal_pair',
               "(principal_kind IS NULL AND principal_id IS NULL)"
               " OR (principal_kind IS NOT NULL AND principal_id IS NOT NULL AND "
            + _PRINCIPAL_KIND_CHECK + ")"),
        sa.PrimaryKeyConstraint('event_id', name='pk_game_events'),
        sa.UniqueConstraint('campaign_id', 'seq', name='uq_game_events_campaign_seq'))
    op.create_index('ix_game_events_campaign_epoch_seq', 'game_events',
                    ['campaign_id', 'stream_epoch', 'seq'])


def _refuse_populated(connection):
    """The previous schema cannot hold receipts, events or a db-era campaign (C:831-837)."""

    for table, what in (('command_receipts', 'command receipts'),
                        ('game_events', 'game events')):
        count = connection.scalar(sa.text('SELECT COUNT(*) FROM ' + table)) or 0
        if count:
            raise RuntimeError('downgrade refused: %d %s exist and the previous schema '
                               'cannot hold them' % (count, what))
    live = connection.execute(sa.text(
        "SELECT campaign_id, authority FROM campaign_state_authority "
        "WHERE authority IN ('db', 'migrating', 'exporting') ORDER BY campaign_id")).fetchall()
    if live:
        raise RuntimeError(
            'downgrade refused: campaign state authority is not file for %s; '
            'export them back to files first' % ', '.join('%s (%s)' % tuple(r) for r in live))


def downgrade():
    # Offline SQL cannot prove the stream is empty. Refuse rather than emit a
    # script that would drop history.
    if op.get_context().as_sql:
        raise RuntimeError('command stream downgrade requires an online check for '
                           'receipts and events')
    connection = op.get_bind()
    if connection.dialect.name == 'sqlite':
        _prepare_sqlite(connection)
    _refuse_populated(connection)
    # Empty stream rows hold only a counter; with no events it carries nothing.
    op.drop_index('ix_game_events_campaign_epoch_seq', table_name='game_events')
    for table in _NEW_TABLES:
        op.drop_table(table)
