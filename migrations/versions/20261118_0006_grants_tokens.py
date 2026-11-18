"""Campaign grants and integration-token records.

``campaign_grants`` holds QM grants and one-use level-up and retrain unlocks
(C:857-861); ``integration_tokens`` is the Obsidian token store in ``sql``
mode (task PE-14c, Decision 1).  Both are authorization inputs, so a
populated downgrade is refused: the previous schema cannot hold them
(C:682, C:831-837).

Revision ID: 20261118_0006
Revises: 20261115_0005
"""

from alembic import op
import sqlalchemy as sa

revision = '20261118_0006'
down_revision = '20261115_0005'
branch_labels = None
depends_on = None

_LIVE = 'consumed_at IS NULL AND revoked_at IS NULL'
_LIVE_SCOPE = """'["@live"]'"""


def _prepare_sqlite(connection):
    """Run the whole SQLite revision as one transaction (see 20261110_0003)."""

    if not connection.connection.dbapi_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')


def _check(table, name, condition):
    return sa.CheckConstraint(condition, name=op.f('ck_%s_%s' % (table, name)))


def upgrade():
    context = op.get_context()
    if context.dialect.name == 'sqlite' and not context.as_sql:
        _prepare_sqlite(op.get_bind())

    op.create_table('campaign_grants',
        sa.Column('grant_id', sa.String(32), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('user_id', sa.String(64), nullable=True),
        sa.Column('character_id', sa.String(64), nullable=True),
        sa.Column('target_level', sa.Integer(), nullable=True),
        sa.Column('build_revision', sa.BigInteger(), nullable=True),
        sa.Column('issued_by_kind', sa.String(32), nullable=False),
        sa.Column('issued_by_id', sa.String(128), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('consumed_by_operation_id', sa.String(128), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        _check('campaign_grants', 'grant_id_length', 'length(grant_id) = 32'),
        _check('campaign_grants', 'known_kind',
               "kind in ('quartermaster', 'level_unlock', 'retrain_grant')"),
        _check('campaign_grants', 'kind_fields',
               "(kind = 'quartermaster' AND user_id IS NOT NULL AND character_id IS NULL"
               " AND target_level IS NULL AND build_revision IS NULL)"
               " OR (kind = 'level_unlock' AND user_id IS NULL AND character_id IS NOT NULL"
               " AND target_level IS NOT NULL AND build_revision IS NULL)"
               " OR (kind = 'retrain_grant' AND user_id IS NULL AND character_id IS NOT NULL"
               " AND target_level IS NULL AND build_revision IS NOT NULL)"),
        _check('campaign_grants', 'target_level_positive',
               'target_level IS NULL OR target_level >= 1'),
        _check('campaign_grants', 'build_revision_nonnegative',
               'build_revision IS NULL OR build_revision >= 0'),
        _check('campaign_grants', 'known_issuer_kind',
               "issued_by_kind in ('user', 'integration_token')"),
        _check('campaign_grants', 'consumed_pair',
               '(consumed_at IS NULL AND consumed_by_operation_id IS NULL)'
               ' OR (consumed_at IS NOT NULL AND consumed_by_operation_id IS NOT NULL)'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_campaign_grants_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['campaign_id', 'character_id'],
                                ['characters.campaign_id', 'characters.id'],
                                name='fk_campaign_grants_character_campaign',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('grant_id', name='pk_campaign_grants'))
    op.create_index('ix_campaign_grants_campaign_id', 'campaign_grants', ['campaign_id'])
    op.create_index('uq_campaign_grants_one_live_quartermaster', 'campaign_grants',
                    ['campaign_id', 'user_id'], unique=True,
                    sqlite_where=sa.text("kind = 'quartermaster' AND " + _LIVE),
                    postgresql_where=sa.text("kind = 'quartermaster' AND " + _LIVE))

    op.create_table('integration_tokens',
        sa.Column('credential_id', sa.String(64), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('label', sa.Text(), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=False),
        sa.Column('last_four', sa.String(8), nullable=False),
        sa.Column('scope', sa.JSON(), server_default=sa.text(_LIVE_SCOPE), nullable=False),
        sa.Column('replaces_credential_id', sa.String(64), nullable=True),
        sa.Column('minted_by_user_id', sa.String(64), nullable=True),
        sa.Column('minted_as', sa.String(8), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        _check('integration_tokens', 'token_digest_length', 'length(token_digest) = 64'),
        _check('integration_tokens', 'minted_as_known',
               "minted_as IS NULL OR (minted_as IN ('gm', 'admin') "
               "AND minted_by_user_id IS NOT NULL)"),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_integration_tokens_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('credential_id', name='pk_integration_tokens'))
    op.create_index('ix_integration_tokens_campaign_id', 'integration_tokens', ['campaign_id'])
    op.create_index('ix_integration_tokens_token_digest', 'integration_tokens',
                    ['token_digest'])


def _refuse_populated(connection):
    """The previous schema cannot hold grants or SQL token records (C:682, C:831-837)."""

    for table, what in (('campaign_grants', 'campaign grants'),
                        ('integration_tokens', 'integration token records')):
        count = connection.scalar(sa.text('SELECT COUNT(*) FROM ' + table)) or 0
        if count:
            raise RuntimeError('downgrade refused: %d %s exist and the previous schema '
                               'cannot hold them' % (count, what))


def downgrade():
    # Offline SQL cannot prove both tables are empty; refuse rather than emit a
    # script that would drop grants or revocations.
    if op.get_context().as_sql:
        raise RuntimeError('grants and tokens downgrade requires an online check for '
                           'grant and token rows')
    connection = op.get_bind()
    if connection.dialect.name == 'sqlite':
        _prepare_sqlite(connection)
    _refuse_populated(connection)
    op.drop_index('ix_integration_tokens_token_digest', table_name='integration_tokens')
    op.drop_index('ix_integration_tokens_campaign_id', table_name='integration_tokens')
    op.drop_table('integration_tokens')
    op.drop_index('uq_campaign_grants_one_live_quartermaster', table_name='campaign_grants')
    op.drop_index('ix_campaign_grants_campaign_id', table_name='campaign_grants')
    op.drop_table('campaign_grants')
