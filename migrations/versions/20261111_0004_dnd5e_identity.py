"""D&D 5e campaign and character identities (PE-01 section 1; task PE-10).

Widens the campaign and character system checks to ``dnd5e``, admits the
``dnd5e_characters`` legacy store, and adds ``ck_characters_dnd5e_locator``:
a D&D row uses that store with ``legacy_file = id || '.json'``, and no other
system uses that store.  Every existing PF2e/Cosmere row already satisfies the
new checks, so no row changes.

The active-locator index ``uq_characters_active_legacy_locator`` belongs to
PE-12 and keeps its exact definition.  PostgreSQL never touches it.  SQLite
must rebuild ``characters`` to change a CHECK, and a rebuild drops the table's
indexes, so it is dropped and recreated with the same columns and predicate.

Revision ID: 20261111_0004
Revises: 20261110_0003
"""

from alembic import op
import sqlalchemy as sa

revision = '20261111_0004'
down_revision = '20261110_0003'
branch_labels = None
depends_on = None

_OLD_SYSTEM_CHECK = "system in ('pf2e', 'cosmere')"
_NEW_SYSTEM_CHECK = "system in ('pf2e', 'dnd5e', 'cosmere')"
_OLD_STORAGE_CHECK = "legacy_storage in ('party_data', 'cosmere_pcs')"
_NEW_STORAGE_CHECK = "legacy_storage in ('party_data', 'cosmere_pcs', 'dnd5e_characters')"
_DND5E_LOCATOR_CHECK = (
    "(system = 'dnd5e' AND legacy_storage = 'dnd5e_characters'"
    " AND legacy_file = id || '.json')"
    " OR (system <> 'dnd5e' AND legacy_storage <> 'dnd5e_characters')"
)
_LOCATOR_INDEX = 'uq_characters_active_legacy_locator'
_LOCATOR_COLUMNS = ['campaign_id', 'legacy_storage', 'legacy_file']


# --------------------------------------------------------------------------
# Guards (the same SQLite rules as 20261110_0003)


def _is_sqlite(connection):
    return connection.dialect.name == 'sqlite'


def _require_sqlite_foreign_keys_off(connection):
    """Refuse a SQLite table rebuild while foreign keys are enforced.

    Rebuilding ``campaigns`` or ``characters`` drops the original table; with
    ``PRAGMA foreign_keys=ON`` that DROP cascades into every referencing table
    and silently empties it.  ``migrations/env.py`` leaves the pragma off.
    """

    if connection.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError(
            'SQLite foreign keys are enforced on the migration connection; a '
            'table rebuild would cascade deletes. Run Alembic through '
            'migrations/env.py, which leaves them off.')


def _require_sqlite_foreign_keys_intact(connection, when):
    problems = connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()
    if problems:
        raise RuntimeError(
            'foreign key check failed %s: %r' % (when, problems[:5]))


def _prepare_sqlite(connection):
    """Run the whole SQLite revision as one transaction (see 20261110_0003)."""

    _require_sqlite_foreign_keys_off(connection)
    _require_sqlite_foreign_keys_intact(connection, 'before the revision changed anything; '
                                                    'repair these rows first')
    if not connection.connection.dbapi_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')


def _count(connection, sql):
    return connection.scalar(sa.text(sql)) or 0


# --------------------------------------------------------------------------
# Explicit table shapes for SQLite batch rebuilds.
#
# ``copy_from`` stops batch mode from reflecting the live table, because
# reflection on SQLite drops CHECK constraint names and partial-index
# predicates.  Each shape must match the table exactly as the previous
# revision (``dnd5e=False``) or this one (``dnd5e=True``) leaves it.


def _campaigns_shape(*, dnd5e):
    return sa.Table(
        'campaigns', sa.MetaData(),
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('system', sa.String(64), nullable=False),
        sa.Column('created_by_user_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('trashed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('session_number', sa.Integer(), server_default=sa.text('1'),
                  nullable=False),
        sa.Column('settings', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('source_payload', sa.JSON(), server_default=sa.text("'{}'"),
                  nullable=False),
        sa.Column('source_checksum', sa.String(64), nullable=True),
        sa.CheckConstraint(_NEW_SYSTEM_CHECK if dnd5e else _OLD_SYSTEM_CHECK,
                           name='ck_campaigns_known_system'),
        sa.CheckConstraint('session_number >= 0',
                           name='ck_campaigns_session_number_nonnegative'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'],
                                name='fk_campaigns_created_by_user_id_users',
                                ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name='pk_campaigns'),
    )


def _characters_shape(*, dnd5e):
    constraints = [
        sa.CheckConstraint(_NEW_STORAGE_CHECK if dnd5e else _OLD_STORAGE_CHECK,
                           name='ck_characters_known_legacy_storage'),
        sa.CheckConstraint(_NEW_SYSTEM_CHECK if dnd5e else _OLD_SYSTEM_CHECK,
                           name='ck_characters_known_system'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_characters_campaign_id_campaigns', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_characters'),
        sa.UniqueConstraint('campaign_id', 'id', name='uq_characters_campaign_id_id'),
    ]
    if dnd5e:
        constraints.append(sa.CheckConstraint(_DND5E_LOCATOR_CHECK,
                                              name='ck_characters_dnd5e_locator'))
    table = sa.Table(
        'characters', sa.MetaData(),
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('system', sa.String(64), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('legacy_storage', sa.String(32), nullable=False),
        sa.Column('legacy_file', sa.Text(), nullable=False),
        sa.Column('source_schema_version', sa.Integer(), nullable=True),
        sa.Column('content_checksum', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        *constraints,
    )
    sa.Index('ix_characters_campaign_id', table.c.campaign_id)
    return table


def _create_locator_index():
    # Exactly as 20261110_0003 created it.
    op.create_index(_LOCATOR_INDEX, 'characters', _LOCATOR_COLUMNS, unique=True,
                    sqlite_where=sa.text('archived_at IS NULL'),
                    postgresql_where=sa.text('archived_at IS NULL'))


def _swap_checks(*, dnd5e):
    """Move both tables' checks to the ``dnd5e`` (or previous) definitions."""

    system_check = _NEW_SYSTEM_CHECK if dnd5e else _OLD_SYSTEM_CHECK
    storage_check = _NEW_STORAGE_CHECK if dnd5e else _OLD_STORAGE_CHECK
    sqlite = op.get_context().dialect.name == 'sqlite'
    if sqlite:
        with op.batch_alter_table('campaigns', recreate='always',
                                  copy_from=_campaigns_shape(dnd5e=not dnd5e)) as batch:
            batch.drop_constraint(op.f('ck_campaigns_known_system'), type_='check')
            batch.create_check_constraint(op.f('ck_campaigns_known_system'), system_check)
        op.drop_index(_LOCATOR_INDEX, table_name='characters')
        with op.batch_alter_table('characters', recreate='always',
                                  copy_from=_characters_shape(dnd5e=not dnd5e)) as batch:
            batch.drop_constraint(op.f('ck_characters_known_system'), type_='check')
            batch.drop_constraint(op.f('ck_characters_known_legacy_storage'), type_='check')
            if not dnd5e:
                batch.drop_constraint(op.f('ck_characters_dnd5e_locator'), type_='check')
            batch.create_check_constraint(op.f('ck_characters_known_legacy_storage'), storage_check)
            batch.create_check_constraint(op.f('ck_characters_known_system'), system_check)
            if dnd5e:
                batch.create_check_constraint(op.f('ck_characters_dnd5e_locator'),
                                              _DND5E_LOCATOR_CHECK)
        _create_locator_index()
        return
    op.drop_constraint(op.f('ck_campaigns_known_system'), 'campaigns', type_='check')
    op.create_check_constraint(op.f('ck_campaigns_known_system'), 'campaigns', system_check)
    op.drop_constraint(op.f('ck_characters_known_system'), 'characters', type_='check')
    op.drop_constraint(op.f('ck_characters_known_legacy_storage'), 'characters', type_='check')
    if not dnd5e:
        op.drop_constraint(op.f('ck_characters_dnd5e_locator'), 'characters', type_='check')
    op.create_check_constraint(op.f('ck_characters_known_legacy_storage'), 'characters',
                               storage_check)
    op.create_check_constraint(op.f('ck_characters_known_system'), 'characters', system_check)
    if dnd5e:
        op.create_check_constraint(op.f('ck_characters_dnd5e_locator'), 'characters',
                                   _DND5E_LOCATOR_CHECK)


# --------------------------------------------------------------------------
# Upgrade / downgrade


def upgrade():
    context = op.get_context()
    sqlite = context.dialect.name == 'sqlite'
    if sqlite and not context.as_sql:
        _prepare_sqlite(op.get_bind())
    _swap_checks(dnd5e=True)
    if sqlite and not context.as_sql:
        _require_sqlite_foreign_keys_intact(op.get_bind(), 'after the table rebuild')


def _refuse_dnd5e_rows(connection):
    campaigns = _count(connection, "SELECT COUNT(*) FROM campaigns WHERE system = 'dnd5e'")
    characters = _count(connection,
                        "SELECT COUNT(*) FROM characters WHERE system = 'dnd5e' "
                        "OR legacy_storage = 'dnd5e_characters'")
    if campaigns or characters:
        raise RuntimeError(
            'downgrade refused: %d D&D campaign(s) and %d D&D character(s) exist and the '
            'previous schema cannot hold them; remove them first' % (campaigns, characters))


def downgrade():
    # Offline SQL cannot prove there are no D&D rows. Refuse rather than emit
    # a script that would fail part way, or drop rows to fit.
    context = op.get_context()
    if context.as_sql:
        raise RuntimeError('D&D identity downgrade requires an online check for D&D rows')
    connection = op.get_bind()
    sqlite = _is_sqlite(connection)
    if sqlite:
        _prepare_sqlite(connection)
    _refuse_dnd5e_rows(connection)
    _swap_checks(dnd5e=False)
    if sqlite:
        _require_sqlite_foreign_keys_intact(connection, 'after the table rebuild')
