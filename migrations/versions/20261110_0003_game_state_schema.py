"""Transactional game state: character/encounter state, clocks, sessions, authority.

Also widens ``migration_runs`` for per-campaign runs and gives ``characters``
an ``archived_at`` column with a locator index unique only among live rows
(PE-01 sections 1, 2, 4 and 7; task PE-12).

Revision ID: 20261110_0003
Revises: 20261001_0002
"""

from alembic import op
import sqlalchemy as sa

revision = '20261110_0003'
down_revision = '20261001_0002'
branch_labels = None
depends_on = None

_NEW_TABLES = (
    # Children first, so a downgrade can drop them in this order.
    'encounter_combatants',
    'character_states',
    'encounter_states',
    'campaign_clocks',
    'campaign_sessions',
    'campaign_state_authority',
)
_LEGACY_RUN_STATUSES = ('running', 'succeeded', 'failed', 'verified')
_RULES_PROFILE_CHECK = "rules_profile in ('pf2e', 'dnd5e-2014', 'dnd5e-2024')"
_RUN_CHECKS = (
    ('known_kind', "kind in ('import', 'export')"),
    ('known_status', "status in ('running', 'succeeded', 'failed', 'verified', "
                     "'exported', 'aborted')"),
    ('scope', "(campaign_id IS NULL AND stream_epoch IS NULL AND kind = 'import' "
              "AND status NOT IN ('exported', 'aborted')) OR "
              "(campaign_id IS NOT NULL AND stream_epoch IS NOT NULL "
              "AND stream_epoch >= 1)"),
    ('exported_is_export', "status <> 'exported' OR kind = 'export'"),
)


# --------------------------------------------------------------------------
# Guards


def _is_sqlite(connection):
    return connection.dialect.name == 'sqlite'


def _require_sqlite_foreign_keys_off(connection):
    """Refuse a SQLite table rebuild while foreign keys are enforced.

    Alembic batch mode rebuilds ``characters`` by copying it, dropping the
    original and renaming the copy.  With ``PRAGMA foreign_keys=ON`` that
    DROP cascades into every table that references a character (state rows,
    assignments, drafts) and silently empties them.  ``migrations/env.py``
    opens its own engine without the pragma; this makes the dependency
    explicit instead of accidental.
    """

    if connection.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError(
            'SQLite foreign keys are enforced on the migration connection; a '
            'table rebuild would cascade deletes. Run Alembic through '
            'migrations/env.py, which leaves them off.')


def _require_sqlite_foreign_keys_intact(connection):
    problems = connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()
    if problems:
        raise RuntimeError(
            'foreign key check failed after the table rebuild: %r' % (problems[:5],))


def _count(connection, sql):
    return connection.scalar(sa.text(sql)) or 0


# --------------------------------------------------------------------------
# Explicit table shapes for SQLite batch rebuilds.
#
# ``copy_from`` stops batch mode from reflecting the live table, because
# reflection on SQLite drops CHECK constraint names and partial-index
# predicates.  Each shape here must match the table exactly as the previous
# revision (or this one) left it.


def _characters_shape(*, archived):
    columns = [
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
    ]
    if archived:
        columns.append(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    constraints = [
        sa.CheckConstraint("legacy_storage in ('party_data', 'cosmere_pcs')",
                           name='ck_characters_known_legacy_storage'),
        sa.CheckConstraint("system in ('pf2e', 'cosmere')", name='ck_characters_known_system'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_characters_campaign_id_campaigns', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_characters'),
        sa.UniqueConstraint('campaign_id', 'id', name='uq_characters_campaign_id_id'),
    ]
    if not archived:
        constraints.append(sa.UniqueConstraint(
            'campaign_id', 'legacy_storage', 'legacy_file', name='uq_characters_legacy_locator'))
    table = sa.Table('characters', sa.MetaData(), *columns, *constraints)
    sa.Index('ix_characters_campaign_id', table.c.campaign_id)
    return table


def _migration_runs_shape(*, campaign_runs):
    columns = [
        sa.Column('id', sa.String(64), nullable=False),
        sa.Column('source_checksum', sa.String(64), nullable=False),
        sa.Column('source_path', sa.Text(), nullable=True),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('tool_version', sa.String(64), nullable=True),
        sa.Column('entity_counts', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('details', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    ]
    constraints = [sa.PrimaryKeyConstraint('id', name='pk_migration_runs')]
    if campaign_runs:
        columns += [
            sa.Column('campaign_id', sa.String(64), nullable=True),
            sa.Column('kind', sa.String(16), server_default=sa.text("'import'"), nullable=False),
            sa.Column('stream_epoch', sa.Integer(), nullable=True),
        ]
        constraints += [sa.CheckConstraint(condition, name='ck_migration_runs_' + name)
                        for name, condition in _RUN_CHECKS]
    else:
        constraints.append(sa.UniqueConstraint(
            'source_checksum', name='uq_migration_runs_source_checksum'))
    return sa.Table('migration_runs', sa.MetaData(), *columns, *constraints)


# --------------------------------------------------------------------------
# Upgrade


def upgrade():
    context = op.get_context()
    sqlite = context.dialect.name == 'sqlite'
    if not context.as_sql:
        connection = op.get_bind()
        if sqlite:
            _require_sqlite_foreign_keys_off(connection)
        # Check every precondition before the first change: SQLite DDL is not
        # transactional here, so a late failure would leave a half-migrated file.
        unknown = _count(connection,
                         'SELECT COUNT(*) FROM migration_runs WHERE status NOT IN '
                         "('running', 'succeeded', 'failed', 'verified')")
        if unknown:
            raise RuntimeError(
                '%d migration_runs rows have an unknown status; resolve them first' % unknown)

    _upgrade_characters(sqlite)
    _upgrade_migration_runs(sqlite)
    _create_state_tables()

    if sqlite and not context.as_sql:
        _require_sqlite_foreign_keys_intact(op.get_bind())


def _upgrade_characters(sqlite):
    if sqlite:
        with op.batch_alter_table('characters', recreate='always',
                                  copy_from=_characters_shape(archived=False)) as batch:
            batch.add_column(sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
            batch.drop_constraint('uq_characters_legacy_locator', type_='unique')
    else:
        op.add_column('characters',
                      sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
        op.drop_constraint('uq_characters_legacy_locator', 'characters', type_='unique')
    op.create_index('uq_characters_active_legacy_locator', 'characters',
                    ['campaign_id', 'legacy_storage', 'legacy_file'], unique=True,
                    sqlite_where=sa.text('archived_at IS NULL'),
                    postgresql_where=sa.text('archived_at IS NULL'))


def _upgrade_migration_runs(sqlite):
    new_columns = (
        sa.Column('campaign_id', sa.String(64), nullable=True),
        sa.Column('kind', sa.String(16), server_default=sa.text("'import'"), nullable=False),
        sa.Column('stream_epoch', sa.Integer(), nullable=True),
    )
    if sqlite:
        with op.batch_alter_table('migration_runs', recreate='always',
                                  copy_from=_migration_runs_shape(campaign_runs=False)) as batch:
            for column in new_columns:
                batch.add_column(column)
            batch.drop_constraint('uq_migration_runs_source_checksum', type_='unique')
            for name, condition in _RUN_CHECKS:
                batch.create_check_constraint(op.f('ck_migration_runs_' + name), condition)
    else:
        for column in new_columns:
            op.add_column('migration_runs', column)
        op.drop_constraint('uq_migration_runs_source_checksum', 'migration_runs', type_='unique')
        for name, condition in _RUN_CHECKS:
            op.create_check_constraint(op.f('ck_migration_runs_' + name), 'migration_runs',
                                       condition)
    op.create_index('uq_migration_runs_legacy_source_checksum', 'migration_runs',
                    ['source_checksum'], unique=True,
                    sqlite_where=sa.text('campaign_id IS NULL'),
                    postgresql_where=sa.text('campaign_id IS NULL'))
    op.create_index('uq_migration_runs_campaign_attempt', 'migration_runs',
                    ['campaign_id', 'kind', 'source_checksum', 'stream_epoch'], unique=True,
                    sqlite_where=sa.text("campaign_id IS NOT NULL AND status <> 'aborted'"),
                    postgresql_where=sa.text("campaign_id IS NOT NULL AND status <> 'aborted'"))


def _timestamps(*, created=True):
    columns = []
    if created:
        columns.append(sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    columns.append(sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
    return columns


def _create_state_tables():
    op.create_table('character_states',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('character_id', sa.String(64), nullable=False),
        sa.Column('domain', sa.String(16), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('rules_profile', sa.String(32), nullable=False),
        sa.Column('content', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('payload', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("domain in ('build', 'play')",
                           name=op.f('ck_character_states_known_domain')),
        sa.CheckConstraint(_RULES_PROFILE_CHECK,
                           name=op.f('ck_character_states_known_rules_profile')),
        sa.CheckConstraint('schema_version >= 1',
                           name=op.f('ck_character_states_schema_version_positive')),
        sa.CheckConstraint('revision >= 0',
                           name=op.f('ck_character_states_revision_nonnegative')),
        sa.ForeignKeyConstraint(['campaign_id', 'character_id'],
                                ['characters.campaign_id', 'characters.id'],
                                name='fk_character_states_character_campaign',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'character_id', 'domain',
                                name='pk_character_states'))

    op.create_table('encounter_states',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('encounter_id', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('round', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('turn', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('initiative', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column('payload', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('clock_anchors', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('rules_profile', sa.String(32), nullable=False),
        sa.Column('content', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('schema_version', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), server_default=sa.text('0'), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status in ('active', 'ended')",
                           name=op.f('ck_encounter_states_known_status')),
        sa.CheckConstraint(_RULES_PROFILE_CHECK,
                           name=op.f('ck_encounter_states_known_rules_profile')),
        sa.CheckConstraint('round >= 0', name=op.f('ck_encounter_states_round_nonnegative')),
        sa.CheckConstraint('turn >= 0', name=op.f('ck_encounter_states_turn_nonnegative')),
        sa.CheckConstraint('schema_version >= 1',
                           name=op.f('ck_encounter_states_schema_version_positive')),
        sa.CheckConstraint('revision >= 0',
                           name=op.f('ck_encounter_states_revision_nonnegative')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_encounter_states_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'encounter_id', name='pk_encounter_states'))
    op.create_index('uq_encounter_states_one_active', 'encounter_states', ['campaign_id'],
                    unique=True, sqlite_where=sa.text("status = 'active'"),
                    postgresql_where=sa.text("status = 'active'"))

    op.create_table('encounter_combatants',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('encounter_id', sa.String(64), nullable=False),
        sa.Column('combatant_id', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('character_id', sa.String(64), nullable=True),
        sa.Column('creature_ref', sa.Text(), nullable=True),
        sa.Column('legacy_ref', sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("kind in ('pc', 'creature')",
                           name=op.f('ck_encounter_combatants_known_kind')),
        sa.CheckConstraint("kind <> 'pc' OR character_id IS NOT NULL",
                           name=op.f('ck_encounter_combatants_pc_has_character')),
        sa.ForeignKeyConstraint(['campaign_id', 'encounter_id'],
                                ['encounter_states.campaign_id', 'encounter_states.encounter_id'],
                                name='fk_encounter_combatants_encounter', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['campaign_id', 'character_id'],
                                ['characters.campaign_id', 'characters.id'],
                                name='fk_encounter_combatants_character_campaign',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'encounter_id', 'combatant_id',
                                name='pk_encounter_combatants'))
    op.create_index('ix_encounter_combatants_character', 'encounter_combatants',
                    ['campaign_id', 'character_id'])

    op.create_table('campaign_clocks',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('clock', sa.String(16), nullable=False),
        sa.Column('value', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('revision', sa.Integer(), server_default=sa.text('0'), nullable=False),
        *_timestamps(created=False),
        sa.CheckConstraint("clock in ('world', 'downtime')",
                           name=op.f('ck_campaign_clocks_known_clock')),
        sa.CheckConstraint('value >= 0', name=op.f('ck_campaign_clocks_value_nonnegative')),
        sa.CheckConstraint('revision >= 0',
                           name=op.f('ck_campaign_clocks_revision_nonnegative')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_campaign_clocks_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'clock', name='pk_campaign_clocks'))

    op.create_table('campaign_sessions',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('session_id', sa.String(128), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_by', sa.String(64), nullable=True),
        sa.Column('origin', sa.String(32), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_by', sa.String(64), nullable=True),
        sa.CheckConstraint("status in ('active', 'ended')",
                           name=op.f('ck_campaign_sessions_known_status')),
        sa.CheckConstraint("status <> 'active' OR (ended_at IS NULL AND ended_by IS NULL)",
                           name=op.f('ck_campaign_sessions_active_not_ended')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_campaign_sessions_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', 'session_id', name='pk_campaign_sessions'))
    op.create_index('uq_campaign_sessions_one_active', 'campaign_sessions', ['campaign_id'],
                    unique=True, sqlite_where=sa.text("status = 'active'"),
                    postgresql_where=sa.text("status = 'active'"))

    op.create_table('campaign_state_authority',
        sa.Column('campaign_id', sa.String(64), nullable=False),
        sa.Column('authority', sa.String(16), nullable=False),
        sa.Column('stream_epoch', sa.Integer(), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source_checksum', sa.String(64), nullable=True),
        sa.CheckConstraint("authority in ('file', 'migrating', 'db', 'exporting')",
                           name=op.f('ck_campaign_state_authority_known_authority')),
        sa.CheckConstraint('stream_epoch >= 1',
                           name=op.f('ck_campaign_state_authority_stream_epoch_positive')),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'],
                                name='fk_campaign_state_authority_campaign_id_campaigns',
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('campaign_id', name='pk_campaign_state_authority'))


# --------------------------------------------------------------------------
# Downgrade
#
# The previous schema cannot hold game state, sessions, clocks, archived
# characters or per-campaign runs.  It is reached only when the files already
# hold everything: every campaign is file-authoritative, nothing exists that
# the files cannot represent, and every campaign's state rows are covered by
# an ``exported`` run completed no earlier than their last write.  The state
# rows and per-campaign runs are then dropped as stale.

_STATE_ROWS_WITHOUT_NEWER_EXPORT = """
SELECT held.campaign_id FROM (
    SELECT campaign_id, MAX(updated_at) AS last_write FROM (
        SELECT campaign_id, updated_at FROM character_states
        UNION ALL SELECT campaign_id, updated_at FROM encounter_states
        UNION ALL SELECT campaign_id, updated_at FROM encounter_combatants
    ) AS state_rows GROUP BY campaign_id
) AS held
WHERE NOT EXISTS (
    SELECT 1 FROM migration_runs AS run
    WHERE run.campaign_id = held.campaign_id
      AND run.kind = 'export' AND run.status = 'exported'
      AND run.completed_at IS NOT NULL AND run.completed_at >= held.last_write
)
ORDER BY held.campaign_id
"""


def _refuse_unrepresentable_state(connection):
    live = connection.execute(sa.text(
        "SELECT campaign_id, authority FROM campaign_state_authority "
        "WHERE authority <> 'file' ORDER BY campaign_id")).fetchall()
    if live:
        raise RuntimeError(
            'downgrade refused: campaign state authority is not file for %s; '
            'export them back to files first' % ', '.join('%s (%s)' % tuple(r) for r in live))
    for table, what in (('campaign_sessions', 'session rows'),
                        ('campaign_clocks', 'campaign clock rows')):
        if _count(connection, 'SELECT COUNT(*) FROM ' + table):
            raise RuntimeError(
                'downgrade refused: %s exist and the previous schema cannot hold them' % what)
    if _count(connection, 'SELECT COUNT(*) FROM characters WHERE archived_at IS NOT NULL'):
        raise RuntimeError(
            'downgrade refused: archived characters exist and the previous schema '
            'cannot represent them')
    unexported = connection.execute(sa.text(_STATE_ROWS_WITHOUT_NEWER_EXPORT)).scalars().all()
    if unexported:
        raise RuntimeError(
            'downgrade refused: state rows in %s have no newer verified export'
            % ', '.join(unexported))


def downgrade():
    # Offline SQL cannot prove the state was exported. Refuse rather than
    # emit a script that could silently erase it.
    if op.get_context().as_sql:
        raise RuntimeError('game-state downgrade requires an online export check')
    connection = op.get_bind()
    sqlite = _is_sqlite(connection)
    if sqlite:
        _require_sqlite_foreign_keys_off(connection)
    _refuse_unrepresentable_state(connection)

    for table in _NEW_TABLES:
        op.drop_table(table)

    # Per-campaign runs have no place in the previous schema, and an aborted
    # run and its retry can share a checksum that the restored column-wide
    # unique would reject.
    connection.execute(sa.text('DELETE FROM migration_runs WHERE campaign_id IS NOT NULL'))
    op.drop_index('uq_migration_runs_campaign_attempt', table_name='migration_runs')
    op.drop_index('uq_migration_runs_legacy_source_checksum', table_name='migration_runs')
    if sqlite:
        with op.batch_alter_table('migration_runs', recreate='always',
                                  copy_from=_migration_runs_shape(campaign_runs=True)) as batch:
            for name, _condition in _RUN_CHECKS:
                batch.drop_constraint(op.f('ck_migration_runs_' + name), type_='check')
            for column in ('stream_epoch', 'kind', 'campaign_id'):
                batch.drop_column(column)
            batch.create_unique_constraint('uq_migration_runs_source_checksum',
                                           ['source_checksum'])
    else:
        for name, _condition in _RUN_CHECKS:
            op.drop_constraint(op.f('ck_migration_runs_' + name), 'migration_runs', type_='check')
        for column in ('stream_epoch', 'kind', 'campaign_id'):
            op.drop_column('migration_runs', column)
        op.create_unique_constraint('uq_migration_runs_source_checksum', 'migration_runs',
                                    ['source_checksum'])

    op.drop_index('uq_characters_active_legacy_locator', table_name='characters')
    if sqlite:
        with op.batch_alter_table('characters', recreate='always',
                                  copy_from=_characters_shape(archived=True)) as batch:
            batch.drop_column('archived_at')
            batch.create_unique_constraint('uq_characters_legacy_locator',
                                           ['campaign_id', 'legacy_storage', 'legacy_file'])
        _require_sqlite_foreign_keys_intact(connection)
    else:
        op.drop_column('characters', 'archived_at')
        op.create_unique_constraint('uq_characters_legacy_locator', 'characters',
                                    ['campaign_id', 'legacy_storage', 'legacy_file'])
