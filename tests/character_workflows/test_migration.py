"""Private workflow records must survive both supported rollback paths."""

from dataclasses import replace
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from core import storage
from core.character_workflows.json_store import JsonWorkflowStore
from core.character_workflows.types import WorkflowError
from core.persistence import Database, runtime
from core.request_context import Principal, resolve_campaign_context
from test_drafts import partial, service
from test_publication import imported, setup_workflow
from test_recovery import interrupt_completion
from tools import migrate_transactional_store as migration
from tools import export_transactional_runtime as exporter

CID, AUTHOR = 'a' * 32, '2' * 32
FIXTURE = Path(__file__).resolve().parents[1] / 'persistence' / 'fixtures' / 'valid'


@pytest.fixture
def migration_env(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURE, source)
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'json')
    for name, path in {'DATA_DIR': source, 'CAMPAIGNS_DIR': source / 'campaigns',
                       'USERS_FILE': source / 'users.json',
                       'SERVER_STATE_FILE': source / 'server_state.json'}.items():
        monkeypatch.setattr(storage, name, str(path))
    storage.set_live_campaign_id(CID)
    campaign = storage.load_json(storage.campaign_file(CID))
    database = Database('sqlite+pysqlite:///' + str(tmp_path / 'target.sqlite'))
    database.create_schema()
    env = SimpleNamespace(source=source, cid=CID, database=database,
                          make_store=lambda: JsonWorkflowStore(source / 'character_drafts'),
                          context=lambda: resolve_campaign_context(Principal.user(AUTHOR),
                              campaign_id=CID, campaign=campaign, live_campaign_id=CID))
    yield env
    database.dispose()


def import_current(env):
    plan = migration.build_plan(env.source)
    assert plan['import_allowed'], plan['blocking_conflicts']
    migration.import_store(env.source, env.database.url, expected_digest=plan['source_digest'])
    assert migration.verify_store(env.source, env.database.url)['verified']
    return plan


@pytest.mark.parametrize('runtime_export', [False, True])
def test_private_drafts_resume_after_json_sql_json_round_trip(migration_env, tmp_path, monkeypatch, runtime_export):
    env = migration_env
    workflow, drafts, files = setup_workflow(env)
    active = drafts.create(env.context(), partial(), request_key='active')
    active = drafts.save(env.context(), active.id, partial('Private saved choice'), expected_revision=0)
    versioned = drafts.create(
        env.context(), imported('Hero'), request_key='versioned', target_id='c' * 32
    )
    assert versioned.base_fingerprint.startswith('v2:')
    discarded = drafts.create(env.context(), partial('Discard me'), request_key='discarded')
    drafts.discard(env.context(), discarded.id, expected_revision=0)
    committed = drafts.create(env.context(), imported(), request_key='committed')
    published = workflow.publish(env.context(), committed.id, expected_revision=0, request_key='publish')
    before = {p: p.read_bytes() for p in env.source.rglob('*') if p.is_file()}
    plan = import_current(env)
    assert plan['entity_counts']['character_drafts'] == 4
    assert plan['entity_counts']['character_workflow_receipts'] == 6
    assert {p: p.read_bytes() for p in before} == before
    assert 'Private saved choice' not in json.dumps(plan)
    from core.persistence.workflow_store import SqlWorkflowStore
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'sql')
    monkeypatch.setattr(runtime, 'database', lambda: env.database)
    with SqlWorkflowStore(env.database).transaction(env.context()) as tx:
        assert tx.get_draft(active.id).inputs == active.inputs
    output = tmp_path / 'rollback'
    if runtime_export:
        report = exporter.export_runtime(env.source, env.database.url, output, quiesced=True)
    else:
        report = migration.export_store(env.source, env.database.url, output)
    assert report['verified']
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'json')
    monkeypatch.setattr(storage, 'DATA_DIR', str(output))
    monkeypatch.setattr(storage, 'USERS_FILE', str(output / 'users.json'))
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(output / 'campaigns'))
    env.make_store = lambda: JsonWorkflowStore(output / 'character_drafts')
    resumed = service(env)
    assert resumed.get(env.context(), active.id) == active
    assert resumed.get(env.context(), versioned.id) == versioned
    with pytest.raises(WorkflowError) as exc:
        resumed.get(env.context(), discarded.id)
    assert exc.value.status == 404
    with resumed.store.transaction(env.context()) as tx:
        assert tx.get_draft(discarded.id).inputs is None
    assert resumed.get(env.context(), committed.id).result['character_id'] == published.character_id
    assert {draft.id for draft in resumed.list(env.context())} == {active.id, versioned.id}
    reimport = Database('sqlite+pysqlite:///' + str(tmp_path / 'reimport.sqlite'))
    reimport.create_schema()
    try:
        rollback_plan = migration.build_plan(output)
        migration.import_store(output, reimport.url, expected_digest=rollback_plan['source_digest'])
        assert migration.verify_store(output, reimport.url)['verified']
    finally:
        reimport.dispose()


def test_historical_receipts_do_not_restore_grants(migration_env, tmp_path):
    from core import campaigns
    env = migration_env
    drafts = service(env)
    saved = drafts.create(env.context(), imported('Removed target'),
                          target_id='c' * 32, request_key='old')
    import app
    app._delete_character_document(str(env.source / 'campaigns' / CID / 'party_data' / 'hero.json'))
    campaigns.remove_member(CID, AUTHOR)
    plan = import_current(env)
    assert plan['entity_counts']['character_drafts'] == 0
    assert plan['entity_counts']['character_workflow_receipts'] == 1
    from core.persistence.models import CampaignMembership, CharacterWorkflowReceipt, Character
    with env.database.session() as session:
        assert session.get(CampaignMembership, (CID, AUTHOR)) is None
        assert session.get(Character, 'c' * 32) is None
        receipts = list(session.query(CharacterWorkflowReceipt))
        assert receipts[0].draft_id == saved.id and receipts[0].state == 'invalidated'
        assert receipts[0].target_id == 'c' * 32
    output = tmp_path / 'historical'
    assert exporter.export_runtime(env.source, env.database.url, output, quiesced=True)['verified']
    assert migration.build_plan(output)['entity_counts']['campaign_memberships'] == 1


def test_old_sources_remain_importable(migration_env):
    plan = import_current(migration_env)
    assert plan['entity_counts']['character_workflow_receipts'] == 0


@pytest.mark.parametrize('corruption', ['version', 'author', 'target', 'journal'])
def test_invalid_private_sources_are_not_silently_omitted(migration_env, corruption):
    env = migration_env
    draft = service(env).create(env.context(), partial(), request_key='draft')
    path = env.source / 'character_drafts' / CID / AUTHOR / (draft.id + '.json')
    document = storage.load_json(str(path))
    if corruption == 'version':
        document['inputs']['payload_version'] = 99
    elif corruption == 'author':
        document['author_id'] = '9' * 32
    elif corruption == 'target':
        document['target_id'] = '9' * 32
    else:
        storage.atomic_write_json(str(path.parent.parent / '_journal.json'), {'workflow_version': 1, 'operations': []})
    storage.atomic_write_json(str(path), document)
    plan = migration.build_plan(env.source)
    assert not plan['import_allowed']


def test_pending_workflow_blocks_export(migration_env, tmp_path, monkeypatch):
    env = migration_env
    workflow, drafts, files = setup_workflow(env)
    saved = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), saved.id, expected_revision=0, request_key='publish')
    plan = migration.build_plan(env.source)
    assert not plan['import_allowed']
    assert 'publication_repair_required' in {c['code'] for c in plan['blocking_conflicts']}


def test_sql_pending_workflow_blocks_runtime_export(migration_env, tmp_path):
    from core.persistence.models import CharacterWorkflowReceipt
    env = migration_env
    saved = service(env).create(env.context(), partial(), request_key='create')
    import_current(env)
    with env.database.transaction() as session:
        session.add(CharacterWorkflowReceipt(id='8' * 32, campaign_id=CID, author_id=AUTHOR,
            draft_id=saved.id, target_id='9' * 32, operation='publish', state='publishing',
            key_hash='a' * 64, request_digest='b' * 64, details={}))
    output = tmp_path / 'pending'
    with pytest.raises(WorkflowError) as exc:
        exporter.export_runtime(env.source, env.database.url, output, quiesced=True)
    assert exc.value.code == 'publication_repair_required' and not output.exists()


def test_runtime_export_uses_authoritative_draft_columns(migration_env, tmp_path):
    from core.persistence.models import Draft
    env = migration_env
    saved = service(env).create(env.context(), partial(), request_key='create')
    import_current(env)
    with env.database.transaction() as session:
        row = session.get(Draft, saved.id)
        row.revision = 3
        row.payload = {**row.payload, 'revision': 99, 'author_id': '9' * 32}
    output = tmp_path / 'current'
    assert exporter.export_runtime(env.source, env.database.url, output, quiesced=True)['verified']
    restored = storage.load_json(str(output / 'character_drafts' / CID / AUTHOR / (saved.id + '.json')))
    assert restored['revision'] == 3 and restored['author_id'] == AUTHOR


def test_runtime_export_refuses_sql_changes_during_staging(migration_env, tmp_path, monkeypatch):
    from core.persistence.models import Draft
    env = migration_env
    saved = service(env).create(env.context(), partial(), request_key='create')
    import_current(env)
    write = exporter._write_legacy_tree
    def racing_writer(*args):
        result = write(*args)
        with env.database.transaction() as session:
            row = session.get(Draft, saved.id)
            row.revision += 1
            row.payload = {**row.payload, 'revision': row.revision}
        return result
    monkeypatch.setattr(exporter, '_write_legacy_tree', racing_writer)
    output = tmp_path / 'raced'
    with pytest.raises(exporter.RuntimeExportError):
        exporter.export_runtime(env.source, env.database.url, output, quiesced=True)
    assert not output.exists()


def test_private_symlink_added_after_planning_blocks_import(migration_env, tmp_path, monkeypatch):
    env = migration_env
    service(env).create(env.context(), partial(), request_key='create')
    bundle = migration.inspect_source(env.source)
    link = env.source / 'character_drafts' / CID / 'unexpected-link'
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    with pytest.raises(migration.SourceDigestMismatch):
        migration._assert_source_snapshot_current(bundle)
