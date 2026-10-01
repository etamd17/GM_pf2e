"""One private lifecycle contract, exercised against real JSON and SQL stores."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path

import pytest
from sqlalchemy import delete

from core import storage
from core.character_workflows.types import DraftInput, WorkflowError
from core.persistence.models import CampaignMembership


def partial(text='Unfinished'):
    return DraftInput('pf2e_builder', 1, {'name': text, 'choices': ['x']},
                      {'step': 4, 'deity': 'Desna'}, {'name': text})


def service(env):
    import app
    from core.character_workflows.drafts import DraftService
    from core.character_workflows.systems import build_system_adapters
    from pf2e_pdf_import import build_from_pdf
    from systems.cosmere.pdf_import import build_from_pdf as cos_pdf
    return DraftService(env.make_store(), build_system_adapters(
        pf2e_build=app._build_new_pf2e_document, pf2e_validate=app._validate_new_character_feats,
        pf2e_merge=app._merge_pf2e_import, pf2e_pdf=build_from_pdf,
        cosmere_build=app._build_cosmere_document, cosmere_pdf=cos_pdf))


def test_author_private_draft_round_trip(workflow_env):
    env = workflow_env
    svc = service(env)
    saved = svc.create(env.context(), partial(), request_key='first-create')
    assert saved.revision == 0 and saved.inputs == partial()
    assert saved.author_id == env.users['owner'] and saved.target_id is None
    assert service(env).get(env.context(), saved.id) == saved
    assert [d.id for d in svc.list(env.context())] == [saved.id]
    for other in ('gm', 'editor', 'viewer', 'outsider'):
        with pytest.raises(WorkflowError) as exc:
            svc.get(env.context(other), saved.id)
        assert exc.value.status == 404
    assert svc.list(env.context('gm')) == []
    if env.mode == 'json':
        assert (env.root / env.cid / env.users['owner'] / (saved.id + '.json')).is_file()
        assert not list((env.path.parent.parent).rglob('*draft*'))


def test_revision_compare_and_swap(workflow_env):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='create')
    changed = svc.save(env.context(), draft.id, partial('Changed'), expected_revision=0)
    assert changed.revision == 1 and changed.created_at == draft.created_at
    with pytest.raises(WorkflowError, match='revision') as exc:
        svc.save(env.context(), draft.id, partial('Stale'), expected_revision=0)
    assert exc.value.status == 409
    assert svc.get(env.context(), draft.id).inputs.form['name'] == 'Changed'


@pytest.mark.parametrize('revision', [True, False, -1, '0', 0.0, None])
def test_revision_must_be_nonbool_nonnegative_integer(workflow_env, revision):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='create')
    with pytest.raises(WorkflowError) as exc:
        svc.save(env.context(), draft.id, partial(), expected_revision=revision)
    assert exc.value.status == 422


def test_input_version_system_size_and_quota(workflow_env):
    env = workflow_env
    svc = service(env)
    for value in (replace(partial(), payload_version=True), replace(partial(), payload_version=2),
                  replace(partial(), kind='cosmere_builder'), replace(partial(), ui=[]),
                  replace(partial(), form={'number': float('nan')})):
        with pytest.raises(WorkflowError) as exc:
            svc.create(env.context(), value, request_key='invalid')
        assert exc.value.status == 422
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), replace(partial(), form={'text': 'é' * 1_048_577}), request_key='oversize')
    assert exc.value.status == 413
    for index in range(20):
        svc.create(env.context(), partial(str(index)), request_key=str(index))
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), partial(), request_key='twenty-first')
    assert exc.value.status == 429


def test_quota_is_serialized_under_contention(workflow_env):
    env = workflow_env
    svc = service(env)
    for index in range(19):
        svc.create(env.context(), partial(), request_key=str(index))
    def create(index):
        try:
            return svc.create(env.context(), partial(), request_key='race-' + str(index)).id
        except WorkflowError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, [1, 2]))
    assert results.count(429) == 1
    assert len(svc.list(env.context())) == 20


def test_create_retry_uses_original_digest(workflow_env):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='same-key')
    saved = svc.save(env.context(), draft.id, partial('Changed'), expected_revision=0)
    retried = svc.create(env.context(), partial(), request_key='same-key')
    assert retried == saved
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), partial('Different request'), request_key='same-key')
    assert exc.value.status == 409
    with env.make_store().transaction(env.context()) as tx:
        receipts = tx.list_receipts()
        assert len(receipts) == 1
        assert 'same-key' not in json.dumps(receipts[0].metadata)
        assert len(receipts[0].key_hash) == 64


@pytest.mark.parametrize('operation', ['get', 'list', 'save', 'copy', 'discard', 'replay'])
def test_unknown_stored_version_is_never_reinterpreted_or_erased(workflow_env, operation):
    env, svc = workflow_env, service(workflow_env)
    draft = svc.create(env.context(), partial(), request_key='versioned')
    future = replace(draft, inputs=replace(draft.inputs, payload_version=2))
    with svc.store.transaction(env.context()) as tx:
        tx.put_draft(future)
    operations = {
        'get': lambda: svc.get(env.context(), draft.id),
        'list': lambda: svc.list(env.context()),
        'save': lambda: svc.save(env.context(), draft.id, partial('replacement'), expected_revision=0),
        'copy': lambda: svc.copy(env.context(), draft.id, partial(), request_key='copy'),
        'discard': lambda: svc.discard(env.context(), draft.id, expected_revision=0),
        'replay': lambda: svc.create(env.context(), partial(), request_key='versioned'),
    }
    with pytest.raises(WorkflowError) as exc:
        operations[operation]()
    assert exc.value.status == 422
    with svc.store.transaction(env.context()) as tx:
        assert tx.get_draft(draft.id) == future
        assert len(tx.list_receipts()) == 1


def test_discard_prevents_late_save(workflow_env):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial('Private payload'), request_key='create')
    svc.discard(env.context(), draft.id, expected_revision=0)
    assert svc.list(env.context()) == []
    with pytest.raises(WorkflowError) as exc:
        svc.save(env.context(), draft.id, partial(), expected_revision=0)
    assert exc.value.status in (404, 409)
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), partial('Private payload'), request_key='create')
    assert exc.value.status == 409
    with env.make_store().transaction(env.context()) as tx:
        assert tx.get_draft(draft.id).inputs is None
        assert any(r.operation == 'discard' for r in tx.list_receipts())


def test_cached_membership_admin_and_live_context_are_not_authority(workflow_env):
    env = workflow_env
    svc = service(env)
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context('outsider'), partial(), request_key='admin-no-membership')
    assert exc.value.status == 403
    draft = svc.create(env.context(), partial(), request_key='create')
    if env.mode == 'sql':
        with env.database.transaction() as session:
            session.execute(delete(CampaignMembership).where(
                CampaignMembership.campaign_id == env.cid,
                CampaignMembership.user_id == env.users['owner']))
    else:
        campaign = dict(env.campaign, members=[m for m in env.campaign['members']
                                               if m['user_id'] != env.users['owner']])
        storage.atomic_write_json(storage.campaign_file(env.cid), campaign)
    with pytest.raises(WorkflowError) as exc:
        svc.get(env.context(), draft.id)
    assert exc.value.status == 404
    storage.set_live_campaign_id(env.other)
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context('gm'), partial(), request_key='not-live')
    assert exc.value.status == 409


def test_copy_keeps_trusted_target_and_original_fingerprint(workflow_env):
    env = workflow_env
    svc = service(env)
    data = DraftInput('pf2e_import', 1, {}, {}, {'build': env.document['build']})
    original = svc.create(env.context(), data, request_key='create', target_id=env.chid)
    document = json.loads(env.path.read_text(encoding='utf-8'))
    document['build']['level'] = 5
    storage.atomic_write_json(str(env.path), document)
    forged = replace(data, form={'target_id': 'forged', 'base_fingerprint': 'forged'})
    copied = svc.copy(env.context(), original.id, forged, request_key='copy')
    assert copied.id != original.id and copied.target_id == env.chid
    assert copied.base_fingerprint == original.base_fingerprint
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), data, request_key='missing', target_id='missing')
    assert exc.value.status == 404


def test_prepare_import_uses_private_create_contract(workflow_env):
    env = workflow_env
    svc = service(env)
    raw = json.dumps({'build': env.document['build']}).encode()
    first = svc.prepare_import(env.context(), raw, 'hero.json', request_key='import')
    assert first.inputs.kind == 'pf2e_import' and first.target_id is None
    assert svc.prepare_import(env.context(), raw, 'hero.json', request_key='import').id == first.id


def test_deleted_account_cannot_read_or_create_drafts(workflow_env):
    from core.persistence.models import User
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='create')
    if env.mode == 'sql':
        with env.database.transaction() as session:
            session.execute(delete(User).where(User.id == env.users['owner']))
    else:
        users = json.loads(Path(storage.USERS_FILE).read_text(encoding='utf-8'))
        del users['users'][env.users['owner']]
        storage.atomic_write_json(storage.USERS_FILE, users)
    with pytest.raises(WorkflowError) as exc:
        svc.get(env.context(), draft.id)
    assert exc.value.status == 404
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), partial(), request_key='after-delete')
    assert exc.value.status == 403


def test_corrupt_json_account_store_returns_safe_unavailable(workflow_env):
    if workflow_env.mode != 'json':
        pytest.skip('JSON-specific account file boundary')
    env = workflow_env
    svc = service(env)
    storage.atomic_write_json(storage.USERS_FILE, {'users': []})
    with pytest.raises(WorkflowError) as exc:
        svc.create(env.context(), partial(), request_key='create')
    assert exc.value.status == 503
