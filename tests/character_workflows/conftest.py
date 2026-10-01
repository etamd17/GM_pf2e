"""Real, isolated JSON/SQLite stores for the character workflow contract."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import storage
from core.persistence import Database, runtime
from core.persistence.models import (
    Campaign, CampaignMembership, Character, CharacterAssignment, User,
)


@pytest.fixture(params=['json', 'sql'])
def identity_store(request, tmp_path, monkeypatch):
    cid, other, chid = 'a' * 32, 'b' * 32, 'c' * 32
    users = {role: str(index) * 32 for index, role in enumerate(
        ('gm', 'owner', 'editor', 'viewer', 'outsider'), 1)}
    monkeypatch.setenv('OWNERSHIP_BACKEND', request.param)
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    campaign = {'id': cid, 'system': 'pf2e', 'name': 'Table', 'members': [
        {'user_id': users[role], 'role': 'gm' if role == 'gm' else 'player'}
        for role in ('gm', 'owner', 'editor', 'viewer')]}
    storage.atomic_write_json(storage.campaign_file(cid), campaign)
    directory = Path(storage.party_dir(cid))
    directory.mkdir(parents=True)
    path = directory / 'old-name.json'
    document = {'id': chid, 'campaign_id': cid, 'system': 'pf2e',
                'owner_user_id': users['owner'],
                'editor_user_ids': [users['outsider']],
                'viewer_user_ids': [users['outsider']],
                'build': {'name': 'Hero', 'class': 'Fighter', 'ancestry': 'Human'}}
    storage.atomic_write_json(str(path), document)
    database = Database('sqlite+pysqlite:///' + str(tmp_path / 'test.sqlite'))
    if request.param == 'sql':
        database.create_schema()
        monkeypatch.setattr(runtime, 'database', lambda: database)
        with database.transaction() as session:
            session.add_all([User(id=uid, username=role, normalized_username=role,
                                 display_name=role, password_hash='test-only')
                             for role, uid in users.items()])
            session.flush()
            session.add(Campaign(id=cid, slug='table', name='Table', system='pf2e',
                                 created_by_user_id=users['gm']))
            session.flush()
            session.add_all([CampaignMembership(campaign_id=cid,
                user_id=m['user_id'], role=m['role']) for m in campaign['members']])
            session.add(Character(id=chid, campaign_id=cid, system='pf2e',
                                  display_name='Hero', legacy_storage='party_data',
                                  legacy_file=path.name, content_checksum='0' * 64))
            session.flush()
            session.add_all([CharacterAssignment(campaign_id=cid, character_id=chid,
                                                user_id=users[role], role=role)
                             for role in ('owner', 'editor', 'viewer')])
    else:
        def no_database():
            pytest.fail('default JSON resolution must not open SQL')
        monkeypatch.setattr(runtime, 'database', no_database)
    yield SimpleNamespace(mode=request.param, cid=cid, other=other, chid=chid,
                          users=users, path=path, document=document,
                          campaign=campaign, database=database)
    database.dispose()


@pytest.fixture
def workflow_env(identity_store, tmp_path, monkeypatch):
    """Use real account and live-slot files, not cached request authority."""
    from core.request_context import Principal, resolve_campaign_context
    env = identity_store
    monkeypatch.setattr(storage, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(storage, 'SERVER_STATE_FILE', str(tmp_path / 'server_state.json'))
    storage.atomic_write_json(storage.USERS_FILE, {'users': {
        uid: {'id': uid, 'username': role, 'password_hash': 'test-only',
              'is_admin': role == 'outsider'} for role, uid in env.users.items()}})
    storage.set_live_campaign_id(env.cid)
    env.root = tmp_path / 'character_drafts'
    env.context = lambda role='owner': resolve_campaign_context(
        Principal.user(env.users[role], is_admin=role == 'outsider'),
        campaign_id=env.cid, campaign=env.campaign, live_campaign_id=env.cid)
    # Imports live here so missing implementation is a test failure, not an
    # unrelated module-collection failure during the contract's first RED run.
    def make_store():
        from core.character_workflows.json_store import JsonWorkflowStore
        from core.persistence.workflow_store import SqlWorkflowStore
        return JsonWorkflowStore(env.root) if env.mode == 'json' else SqlWorkflowStore(env.database)
    env.make_store = make_store
    return env
