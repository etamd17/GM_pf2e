"""Atomic lifecycle records and independent receipt retention."""

from dataclasses import replace

import pytest
from sqlalchemy import delete

from core.persistence.models import CampaignMembership


def records(env):
    from core.character_workflows.types import DraftInput, DraftSnapshot, WorkflowReceipt
    draft = DraftSnapshot('d' * 32, env.cid, env.users['owner'], 'pf2e',
        DraftInput('pf2e_builder', 1, {'name': 'Private'}, {}, {}), 'active', 0,
        None, None, '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00', None)
    receipt = WorkflowReceipt('e' * 32, env.cid, env.users['owner'], 'create_draft',
        '1' * 64, '2' * 64, draft.id, None, 'committed', {})
    return draft, receipt


def test_transaction_commits_and_rolls_back_all_records(workflow_env):
    env = workflow_env
    store = env.make_store()
    draft, receipt = records(env)
    with pytest.raises(RuntimeError), store.transaction(env.context()) as tx:
        tx.put_draft(draft)
        tx.put_receipt(receipt)
        raise RuntimeError('abort before commit')
    with store.transaction(env.context()) as tx:
        assert tx.get_draft(draft.id) is None and tx.list_receipts() == []
    with store.transaction(env.context()) as tx:
        tx.put_draft(draft)
        tx.put_receipt(receipt)
    with env.make_store().transaction(env.context()) as tx:
        assert tx.get_draft(draft.id) == draft
        assert tx.get_receipt(receipt.author_id, receipt.operation, receipt.key_hash) == receipt


def test_receipt_survives_draft_and_membership_deletion(workflow_env):
    env = workflow_env
    store = env.make_store()
    draft, receipt = records(env)
    with store.transaction(env.context()) as tx:
        tx.put_draft(draft)
        tx.put_receipt(receipt)
    with store.transaction(env.context()) as tx:
        tx.delete_draft(draft.id)
    if env.mode == 'sql':
        with env.database.transaction() as session:
            session.execute(delete(CampaignMembership).where(
                CampaignMembership.campaign_id == env.cid,
                CampaignMembership.user_id == env.users['owner']))
    with store.transaction(env.context('gm')) as tx:
        assert tx.get_draft(draft.id) is None
        assert tx.list_receipts() == [receipt]


def test_transaction_rejects_cross_campaign_records(workflow_env):
    from core.character_workflows.types import WorkflowError
    env = workflow_env
    draft, receipt = records(env)
    for value in (draft, receipt):
        with pytest.raises(WorkflowError), env.make_store().transaction(env.context()) as tx:
            value = replace(value, campaign_id=env.other)
            if hasattr(value, 'inputs'):
                tx.put_draft(value)
            else:
                tx.put_receipt(value)


def test_json_journal_recovers_interrupted_multi_record_commit(workflow_env, monkeypatch):
    if workflow_env.mode != 'json':
        pytest.skip('JSON-specific durability boundary; SQL rollback covered separately')
    from core import storage
    from core.character_workflows.types import WorkflowError
    env = workflow_env
    draft, receipt = records(env)
    original = storage.atomic_write_json
    def fail_draft(path, data, **kwargs):
        if str(path).endswith(draft.id + '.json'):
            raise OSError('simulated interrupted write')
        return original(path, data, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'atomic_write_json', fail_draft)
        with pytest.raises(WorkflowError):
            with env.make_store().transaction(env.context()) as tx:
                tx.put_draft(draft)
                tx.put_receipt(receipt)
    with env.make_store().transaction(env.context()) as tx:
        assert tx.get_draft(draft.id) == draft
        assert tx.list_receipts() == [receipt]


def test_symlink_private_root_fails_closed(workflow_env, tmp_path):
    if workflow_env.mode != 'json':
        pytest.skip('JSON-specific filesystem boundary')
    from core.character_workflows.types import WorkflowError
    env = workflow_env
    outside = tmp_path / 'outside'
    outside.mkdir()
    env.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkflowError), env.make_store().transaction(env.context()):
        pass
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('journal', [
    {'workflow_version': 1}, {'workflow_version': 1, 'operations': [{}]},
    {'workflow_version': 1, 'operations': 'broken'},
])
def test_corrupt_json_journal_is_retained_for_repair(workflow_env, journal):
    if workflow_env.mode != 'json':
        pytest.skip('JSON-specific recovery format')
    from core import storage
    from core.character_workflows.types import WorkflowError
    env = workflow_env
    path = env.root / env.cid / '_journal.json'
    storage.atomic_write_json(str(path), journal)
    with pytest.raises(WorkflowError) as exc:
        with env.make_store().transaction(env.context()):
            pass
    assert exc.value.status == 503 and path.exists()


def test_receipt_operations_and_states_are_validated(workflow_env):
    from core.character_workflows.types import WorkflowError
    env = workflow_env
    _, receipt = records(env)
    for bad in (replace(receipt, operation='unknown'), replace(receipt, state='active')):
        with pytest.raises(WorkflowError), env.make_store().transaction(env.context()) as tx:
            tx.put_receipt(bad)
