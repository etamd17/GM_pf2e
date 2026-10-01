"""Removal invalidates request keys before drafts can cascade or be retried."""

import os
from pathlib import Path

import pytest

from core import campaigns, storage
from core.character_workflows.types import WorkflowError
from test_drafts import partial, service
from test_publication import imported, setup_workflow
from test_recovery import interrupt_completion


def remove_target(env):
    if env.mode == 'sql':
        from core.persistence.ownership import delete_character
        delete_character(env.cid, env.chid, env.users['owner'],
                         delete_document=lambda: env.path.unlink())
    else:
        import app
        app._delete_character_document(str(env.path))


@pytest.mark.parametrize('operation', ['member', 'target'])
def test_receipts_survive_membership_and_character_deletion(workflow_env, operation):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    if operation == 'member':
        campaigns.remove_member(env.cid, env.users['owner'])
    else:
        remove_target(env)
    with svc.store.transaction(env.context()) as tx:
        assert tx.get_draft(draft.id) is None
        receipts = tx.list_receipts()
        create = next(r for r in receipts if r.operation == 'create_draft')
        assert create.state == 'invalidated' and create.draft_id == draft.id
        assert 'Unfinished' not in str(receipts)


@pytest.mark.parametrize('path', ['campaigns', 'transactional_service'])
def test_delayed_create_retry_after_rejoin_stays_revoked(workflow_env, path):
    env = workflow_env
    if path == 'transactional_service' and env.mode != 'sql':
        pytest.skip('independent SQL service path')
    svc = service(env)
    old = svc.create(env.context(), partial(), request_key='old-key')
    if path == 'campaigns':
        campaigns.remove_member(env.cid, env.users['owner'])
    else:
        from core.persistence.services import TransactionalStore
        TransactionalStore(env.database.session_factory).remove_membership(
            campaign_id=env.cid, user_id=env.users['owner'])
    campaigns.add_member(env.cid, env.users['owner'], 'player')
    with pytest.raises(WorkflowError) as exc:
        service(env).create(env.context(), partial(), request_key='old-key')
    assert exc.value.status == 404
    fresh = service(env).create(env.context(), partial(), request_key='new-key')
    assert fresh.id != old.id


@pytest.mark.parametrize('operation', ['member', 'target', 'trash'])
def test_pending_publication_blocks_destructive_removal(workflow_env, monkeypatch, operation):
    env = workflow_env
    monkeypatch.setattr(storage, 'CAMPAIGNS_TRASH_DIR', str(env.root.parent / 'trash'))
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with pytest.raises(WorkflowError) as exc:
        if operation == 'member':
            campaigns.remove_member(env.cid, env.users['owner'])
        elif operation == 'target':
            remove_target(env)
        else:
            campaigns.delete_campaign(env.cid)
    assert exc.value.code == 'publication_repair_required'
    assert campaigns.user_role(campaigns.get_campaign(env.cid), env.users['owner']) == 'player'
    assert env.path.exists()
    assert drafts.get(env.context(), draft.id).state == 'publishing'


def test_trash_restore_preserves_author_scope(workflow_env, monkeypatch):
    env = workflow_env
    monkeypatch.setattr(storage, 'CAMPAIGNS_TRASH_DIR', str(env.root.parent / 'trash'))
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='create')
    campaigns.delete_campaign(env.cid)
    with pytest.raises(WorkflowError):
        svc.get(env.context(), draft.id)
    campaigns.restore_campaign(env.cid)
    storage.set_live_campaign_id(env.cid)
    assert svc.get(env.context(), draft.id).inputs == partial()
    with pytest.raises(WorkflowError) as exc:
        svc.get(env.context('gm'), draft.id)
    assert exc.value.status == 404


@pytest.mark.parametrize('automatic', [False, True])
def test_json_expired_trash_purge_cleans_private_root(workflow_env, monkeypatch, automatic):
    env = workflow_env
    if env.mode != 'json':
        pytest.skip('SQL purge remains unsupported')
    monkeypatch.setattr(storage, 'CAMPAIGNS_TRASH_DIR', str(env.root.parent / 'trash'))
    svc = service(env)
    svc.create(env.context(), partial(), request_key='create')
    campaigns.delete_campaign(env.cid)
    if automatic:
        old = os.path.getmtime(storage.campaign_trash_dir(env.cid)) - 40 * 86400
        os.utime(storage.campaign_trash_dir(env.cid), (old, old))
        assert campaigns.purge_expired_trash() == 1
    else:
        campaigns.purge_campaign(env.cid)
    assert not (env.root / env.cid).exists()
    assert not Path(storage.campaign_trash_dir(env.cid)).exists()


def test_json_interrupted_removal_cannot_revive_drafts(workflow_env, monkeypatch):
    env = workflow_env
    if env.mode != 'json':
        pytest.skip('SQL invalidation and membership removal are one transaction')
    svc = service(env)
    svc.create(env.context(), partial(), request_key='old-key')
    with monkeypatch.context() as patch:
        def fail(doc):
            raise OSError('membership write interrupted')
        patch.setattr(campaigns, 'save_campaign', fail)
        with pytest.raises(OSError):
            campaigns.remove_member(env.cid, env.users['owner'])
    assert campaigns.user_role(campaigns.get_campaign(env.cid), env.users['owner']) == 'player'
    with pytest.raises(WorkflowError) as exc:
        service(env).create(env.context(), partial(), request_key='old-key')
    assert exc.value.status == 404


def test_last_gm_rejection_does_not_invalidate_drafts(workflow_env):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context('gm'), partial(), request_key='create')
    assert campaigns.remove_member(env.cid, env.users['gm']) is None
    assert svc.get(env.context('gm'), draft.id).inputs == partial()


def test_interrupted_invalidation_retains_recoverable_evidence(workflow_env, monkeypatch):
    env = workflow_env
    svc = service(env)
    draft = svc.create(env.context(), partial(), request_key='create')
    with monkeypatch.context() as patch:
        if env.mode == 'json':
            from core.character_workflows.json_store import JsonWorkflowTransaction
            def fail_apply(self, journal):
                raise OSError('process stopped after durable invalidation intent')
            patch.setattr(JsonWorkflowTransaction, '_apply', fail_apply)
            expected = WorkflowError
        else:
            from core.persistence.workflow_store import SqlWorkflowTransaction
            real_delete = SqlWorkflowTransaction.delete_draft
            def fail_delete(self, draft_id):
                real_delete(self, draft_id)
                raise OSError('transaction interrupted before membership delete')
            patch.setattr(SqlWorkflowTransaction, 'delete_draft', fail_delete)
            # The public SQL adapter deliberately hides backend exceptions.
            from core.persistence.runtime import StoreUnavailable
            expected = StoreUnavailable
        with pytest.raises(expected):
            campaigns.remove_member(env.cid, env.users['owner'])
    assert campaigns.user_role(campaigns.get_campaign(env.cid), env.users['owner']) == 'player'
    with env.make_store().transaction(env.context()) as tx:
        if env.mode == 'sql':
            assert tx.get_draft(draft.id).inputs == partial()
            assert tx.list_receipts()[0].state == 'committed'
        else:
            assert tx.get_draft(draft.id) is None
            assert tx.list_receipts()[0].state == 'invalidated'
            assert not (env.root / env.cid / '_journal.json').exists()
