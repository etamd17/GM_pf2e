"""Publication contract: stable identity, authority, and latest live state."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from core import storage
from core.character_workflows.types import DraftInput, WorkflowError
from core.persistence.models import Character, CharacterAssignment
from test_drafts import service as draft_service


class DiskFiles:
    """Real atomic files; injected failure points are only at I/O boundaries."""

    def __init__(self):
        self.refreshed, self.notified = [], []

    def lock(self, record):
        return nullcontext()

    def path(self, record):
        return Path(storage.campaign_dir(record.campaign_id)) / record.storage / record.filename

    def read(self, record):
        path = self.path(record)
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def prepare(self, record, document):
        return deepcopy(document)

    def write(self, record, document):
        storage.atomic_write_json(str(self.path(record)), document)

    def refresh(self, record):
        self.refreshed.append(record.character_id)

    def notify(self, record):
        self.notified.append(record.character_id)


def setup_workflow(env):
    from core.character_workflows.publication import WorkflowService
    drafts = draft_service(env)
    files = DiskFiles()
    return WorkflowService(drafts, drafts.store, drafts.adapters, files), drafts, files


def imported(name='New Hero', **extras):
    return DraftInput('pf2e_import', 1, {}, {}, {
        'id': 'forged', 'owner_user_id': 'forged', 'campaign_id': 'forged',
        'build': {'name': name, 'class': 'Fighter', 'ancestry': 'Human', 'level': 2, **extras}})


@pytest.fixture
def cosmere_env(workflow_env):
    from core.persistence.models import Campaign
    env = workflow_env
    env.campaign['system'] = 'cosmere'
    storage.atomic_write_json(storage.campaign_file(env.cid), env.campaign)
    if env.mode == 'sql':
        with env.database.transaction() as session:
            session.get(Campaign, env.cid).system = 'cosmere'
    return env


def test_cosmere_publish_rename_retains_live_wallet_and_state(cosmere_env):
    from test_system_adapters import cos_payload, inputs
    env = cosmere_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), inputs('cosmere_builder', cos_payload()), request_key='create')
    result = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    path = Path(storage.cosmere_pc_dir(env.cid)) / (result.character_id + '.json')
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['owner_user_id'] == env.users['owner']
    update = cos_payload()
    update['build']['name'] = 'Renamed'
    update['build']['notes'] = 'FORGED NOTE'
    update['build']['session_notes'] = [{'date': 'forged', 'text': 'FORGED SESSION'}]
    draft = drafts.create(env.context(), inputs('cosmere_builder', update),
                          target_id=result.character_id, request_key='edit')
    document.update(wallet={'spheres': 5}, play_state={'health': 3})
    document['build']['notes'] = 'OWNER NOTE'
    document['build']['session_notes'] = [{'date': 'today', 'text': 'OWNER SESSION'}]
    storage.atomic_write_json(str(path), document)
    edited = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='edit-publish')
    assert edited.character_id == result.character_id
    current = json.loads(path.read_text(encoding='utf-8'))
    assert current['name'] == 'Renamed'
    assert current['wallet'] == {'spheres': 5} and current['play_state'] == {'health': 3}
    assert current['build']['notes'] == 'OWNER NOTE'
    assert current['build']['session_notes'] == [
        {'date': 'today', 'text': 'OWNER SESSION'}
    ]


def test_cosmere_editor_publish_preserves_latest_owner_private_fields(cosmere_env):
    from test_system_adapters import cos_payload, inputs
    env = cosmere_env
    if env.mode != 'sql':
        pytest.skip('JSON mode intentionally has no editor assignments')
    workflow, drafts, _files = setup_workflow(env)
    created_draft = drafts.create(
        env.context(), inputs('cosmere_builder', cos_payload()), request_key='create'
    )
    created = workflow.publish(
        env.context(), created_draft.id, expected_revision=0, request_key='publish'
    )
    path = Path(storage.cosmere_pc_dir(env.cid)) / (created.character_id + '.json')
    with env.database.transaction() as session:
        session.add(CharacterAssignment(
            campaign_id=env.cid,
            character_id=created.character_id,
            user_id=env.users['editor'],
            role='editor',
        ))

    update = cos_payload()
    update['build']['name'] = 'Editor Rename'
    update['build']['notes'] = 'FORGED NOTE'
    update['build']['session_notes'] = [{'date': 'forged', 'text': 'FORGED SESSION'}]
    editor_context = env.context('editor')
    draft = drafts.create(
        editor_context,
        inputs('cosmere_builder', update),
        target_id=created.character_id,
        request_key='editor-draft',
    )
    current = json.loads(path.read_text(encoding='utf-8'))
    current['build']['notes'] = 'LATEST OWNER NOTE'
    current['build']['session_notes'] = [
        {'date': 'today', 'text': 'LATEST OWNER SESSION'}
    ]
    storage.atomic_write_json(str(path), current)

    edited = workflow.publish(
        editor_context,
        draft.id,
        expected_revision=0,
        request_key='editor-publish',
    )

    assert edited.character_id == created.character_id
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['name'] == 'Editor Rename'
    assert saved['build']['notes'] == 'LATEST OWNER NOTE'
    assert saved['build']['session_notes'] == [
        {'date': 'today', 'text': 'LATEST OWNER SESSION'}
    ]


def _legacy_cosmere_fingerprint(document):
    projection = {key: document.get(key) for key in ('build', 'name', 'house_metal')}
    return sha256(json.dumps(
        projection, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')).hexdigest()


@pytest.mark.parametrize('stale', [False, True], ids=['unchanged', 'build-changed'])
def test_cosmere_legacy_update_draft_fingerprint_remains_compatible(cosmere_env, stale):
    from test_system_adapters import cos_payload, inputs
    env = cosmere_env
    workflow, drafts, _files = setup_workflow(env)
    created_draft = drafts.create(
        env.context(), inputs('cosmere_builder', cos_payload()), request_key='create'
    )
    created = workflow.publish(
        env.context(), created_draft.id, expected_revision=0, request_key='publish'
    )
    path = Path(storage.cosmere_pc_dir(env.cid)) / (created.character_id + '.json')
    current = json.loads(path.read_text(encoding='utf-8'))
    current['build']['notes'] = 'LEGACY OWNER NOTE'
    current['build']['session_notes'] = [
        {'date': 'today', 'text': 'LEGACY OWNER SESSION'}
    ]
    storage.atomic_write_json(str(path), current)

    update = cos_payload()
    update['build']['name'] = 'Legacy Draft Rename'
    draft = drafts.create(
        env.context(), inputs('cosmere_builder', update),
        target_id=created.character_id, request_key='legacy-draft',
    )
    legacy = _legacy_cosmere_fingerprint(current)
    with drafts.store.transaction(env.context()) as tx:
        tx.put_draft(replace(draft, base_fingerprint=legacy))

    if stale:
        current['build']['level'] = 2
        storage.atomic_write_json(str(path), current)
        with pytest.raises(WorkflowError) as exc:
            workflow.publish(
                env.context(), draft.id, expected_revision=0,
                request_key='legacy-publish',
            )
        assert exc.value.code == 'character_build_conflict'
        return

    published = workflow.publish(
        env.context(), draft.id, expected_revision=0,
        request_key='legacy-publish',
    )
    assert published.character_id == created.character_id
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['name'] == 'Legacy Draft Rename'
    assert saved['build']['notes'] == 'LEGACY OWNER NOTE'
    assert saved['build']['session_notes'] == [
        {'date': 'today', 'text': 'LEGACY OWNER SESSION'}
    ]


@pytest.mark.parametrize('author', ['owner', 'gm'])
def test_publish_owns_new_character_and_retries_once(workflow_env, author):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    ctx = env.context(author)
    draft = drafts.create(ctx, imported(), request_key='draft')
    first = workflow.publish(ctx, draft.id, expected_revision=0, request_key='publish')
    retry = workflow.publish(ctx, draft.id, expected_revision=0, request_key='publish')
    assert first == retry and first.created and first.character_id != 'forged'
    path = env.path.parent / (first.character_id + '.json')
    document = json.loads(path.read_text(encoding='utf-8'))
    assert document['id'] == first.character_id and document['campaign_id'] == env.cid
    assert document['owner_user_id'] == (env.users['owner'] if author == 'owner' else None)
    assert len(list(env.path.parent.glob('*.json'))) == 2
    assert files.notified == files.refreshed == [first.character_id]
    if env.mode == 'sql':
        with env.database.session() as session:
            record = session.get(Character, first.character_id)
            assert record.legacy_file == path.name
            assignments = list(session.scalars(select(CharacterAssignment).where(
                CharacterAssignment.character_id == first.character_id)))
            assert [a.user_id for a in assignments] == ([env.users['owner']] if author == 'owner' else [])
    assert drafts.get(ctx, draft.id).state == 'committed'


def test_publish_rechecks_target_and_force_authority(workflow_env):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    ctx = env.context()
    draft = drafts.create(ctx, imported('Hero'), request_key='draft', target_id=env.chid)
    with pytest.raises(WorkflowError) as exc:
        workflow.publish(ctx, draft.id, expected_revision=0, request_key='force', force=True)
    assert exc.value.status == 403 and files.notified == []
    if env.mode == 'sql':
        with env.database.transaction() as session:
            row = session.scalar(select(CharacterAssignment).where(
                CharacterAssignment.character_id == env.chid,
                CharacterAssignment.role == 'owner'))
            session.delete(row)
    else:
        document = dict(env.document, owner_user_id=None)
        storage.atomic_write_json(str(env.path), document)
    with pytest.raises(WorkflowError) as exc:
        workflow.publish(ctx, draft.id, expected_revision=0, request_key='revoked')
    assert exc.value.status in (403, 404)
    assert drafts.get(ctx, draft.id).state == 'active'


def test_stale_build_rejected_but_live_hp_preserved(workflow_env):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    ctx = env.context()
    draft = drafts.create(ctx, imported('Hero'), request_key='draft', target_id=env.chid)
    latest = deepcopy(env.document)
    latest['build']['current_hp'] = 3
    latest['build']['notes'] = 'A newer private note'
    storage.atomic_write_json(str(env.path), latest)
    result = workflow.publish(ctx, draft.id, expected_revision=0, request_key='publish')
    assert result.character_id == env.chid and not result.created
    current = json.loads(env.path.read_text(encoding='utf-8'))
    assert current['build']['current_hp'] == 3
    assert current['build']['notes'] == 'A newer private note'
    assert current['build']['level'] == 2
    second = drafts.create(ctx, imported('Hero', level=3), request_key='draft2', target_id=env.chid)
    current['build']['feats'] = [['Newer build change']]
    storage.atomic_write_json(str(env.path), current)
    with pytest.raises(WorkflowError) as exc:
        workflow.publish(ctx, second.id, expected_revision=0, request_key='stale')
    assert exc.value.code == 'character_build_conflict' and exc.value.status == 409
    assert drafts.get(ctx, second.id).state == 'active'


def test_name_conflict_and_system_specific_rename(workflow_env):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported('Hero'), request_key='same-name')
    with pytest.raises(WorkflowError) as exc:
        workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    assert exc.value.code == 'character_name_conflict'
    update = drafts.create(env.context(), imported('Renamed'), request_key='rename', target_id=env.chid)
    with pytest.raises(WorkflowError) as exc:
        workflow.publish(env.context(), update.id, expected_revision=0, request_key='rename')
    assert exc.value.code == 'rename_not_supported'


def test_failed_write_keeps_reservation_for_new_request_key(workflow_env, monkeypatch):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    def fail_before_write(*args):
        raise OSError('simulated disk failure before replace')
    with monkeypatch.context() as patch:
        patch.setattr(files, 'write', fail_before_write)
        with pytest.raises(WorkflowError) as exc:
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='first')
    assert exc.value.status == 503
    active = drafts.get(env.context(), draft.id)
    assert active.state == 'active'
    with drafts.store.transaction(env.context()) as tx:
        failed = next(r for r in tx.list_receipts() if r.operation == 'publish')
    reserved = failed.target_id
    assert reserved and not (env.path.parent / (reserved + '.json')).exists()
    result = workflow.publish(env.context(), draft.id, expected_revision=active.revision, request_key='second')
    assert result.character_id == reserved


def test_publish_uses_saved_snapshot_not_untrusted_request_metadata(workflow_env):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    original = draft.inputs
    # The service stores a copy: mutating the caller's DTO cannot change Finish.
    original.submission['build']['name'] = 'Not saved'
    result = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    doc = json.loads((env.path.parent / (result.character_id + '.json')).read_text(encoding='utf-8'))
    assert doc['build']['name'] == 'New Hero'
