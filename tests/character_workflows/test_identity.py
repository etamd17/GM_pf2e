"""A supplied character ID resolves one scoped, authoritative stored locator."""

import json

import pytest

from core import storage


def test_legacy_alias_rejects_same_id_under_another_name(identity_store):
    from core.character_workflows.identity import resolve_legacy_name
    from core.character_workflows.types import WorkflowError
    env = identity_store
    duplicate = {**env.document, 'build': {**env.document['build'], 'name': 'Different alias'}}
    storage.atomic_write_json(str(env.path.parent / 'duplicate.json'), duplicate)
    with pytest.raises(WorkflowError) as exc:
        resolve_legacy_name(env.cid, 'Hero')
    assert exc.value.status == 409
from core.persistence.models import CampaignMembership, Character


def test_exact_identity_keeps_existing_filename(identity_store):
    from core.character_workflows.identity import resolve_character, resolve_legacy_name
    env = identity_store
    record = resolve_character(env.cid, env.chid)
    assert record.character_id == env.chid and record.filename == 'old-name.json'
    assert record.owner_id == env.users['owner']
    assert resolve_legacy_name(env.cid, 'Hero').character_id == env.chid
    assert resolve_legacy_name(env.cid, 'Missing') is None


def test_json_does_not_trust_editor_fields(identity_store):
    from core.character_workflows.identity import resolve_character
    env = identity_store
    record = resolve_character(env.cid, env.chid)
    assert env.users['outsider'] not in record.editor_ids | record.viewer_ids
    assert record.editor_ids == (frozenset({env.users['editor']}) if env.mode == 'sql' else frozenset())
    assert record.viewer_ids == (frozenset({env.users['viewer']}) if env.mode == 'sql' else frozenset())
    assert env.users['outsider'] not in record.document.get('editor_user_ids', [])


@pytest.mark.parametrize('reference', ['../hero', '', ['c' * 32], 'd' * 32])
def test_cross_campaign_and_symlink_locators_are_rejected(identity_store, reference):
    from core.character_workflows.identity import resolve_character
    from core.character_workflows.types import WorkflowError
    with pytest.raises(WorkflowError):
        resolve_character(identity_store.other, reference)


def test_unsafe_symlink_is_not_followed(identity_store):
    from core.character_workflows.identity import resolve_character
    from core.character_workflows.types import WorkflowError
    env = identity_store
    target = env.path.parent.parent / 'outside.json'
    env.path.replace(target)
    try:
        env.path.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable on this platform')
    with pytest.raises(WorkflowError):
        resolve_character(env.cid, env.chid)


def test_ambiguous_id_or_name_fails_closed(identity_store):
    from core.character_workflows.identity import resolve_character, resolve_legacy_name
    from core.character_workflows.types import WorkflowError
    env = identity_store
    duplicate = env.path.with_name('duplicate.json')
    storage.atomic_write_json(str(duplicate), env.document)
    with pytest.raises(WorkflowError):
        resolve_character(env.cid, env.chid)
    with pytest.raises(WorkflowError):
        resolve_legacy_name(env.cid, 'Hero')


@pytest.mark.parametrize('field,value', [('campaign_id', 'b' * 32), ('system', 'cosmere')])
def test_document_scope_must_match_registry(identity_store, field, value):
    from core.character_workflows.identity import resolve_character
    from core.character_workflows.types import WorkflowError
    env = identity_store
    storage.atomic_write_json(str(env.path), {**env.document, field: value})
    with pytest.raises(WorkflowError):
        resolve_character(env.cid, env.chid)


def test_revoked_membership_removes_sql_grants(identity_store):
    from core.character_workflows.identity import resolve_character
    env = identity_store
    if env.mode == 'sql':
        with env.database.transaction() as session:
            session.delete(session.get(CampaignMembership, (env.cid, env.users['editor'])))
    else:
        env.campaign['members'] = [m for m in env.campaign['members'] if m['user_id'] != env.users['owner']]
        storage.atomic_write_json(storage.campaign_file(env.cid), env.campaign)
    record = resolve_character(env.cid, env.chid)
    assert env.users['editor'] not in record.editor_ids
    if env.mode == 'json':
        assert record.owner_id is None


def test_sql_locator_mismatch_is_not_rebound(identity_store):
    from core.character_workflows.identity import resolve_character
    from core.character_workflows.types import WorkflowError
    env = identity_store
    if env.mode != 'sql':
        return
    with env.database.transaction() as session:
        session.get(Character, env.chid).legacy_file = 'not-the-file.json'
    with pytest.raises(WorkflowError):
        resolve_character(env.cid, env.chid)
