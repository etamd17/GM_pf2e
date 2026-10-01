"""Prove PostgreSQL row-lock contention, not merely the process-local lock."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from core import campaigns, storage
from core.character_workflows.publication import WorkflowService
from core.character_workflows.types import DraftInput, WorkflowError
from core.persistence import runtime, campaign_runtime
from core.persistence.models import AuditEvent, Campaign, CampaignMembership, Character, CharacterAssignment, Draft, User
from core.persistence.workflow_store import SqlWorkflowStore, SqlWorkflowTransaction
from core.request_context import Principal, resolve_campaign_context
from tests.character_workflows.test_drafts import partial, service
from tests.persistence.test_postgresql_concurrency import postgres_database

pytestmark = pytest.mark.postgresql


class Files:
    def lock(self, record):
        return nullcontext()

    def path(self, record):
        return Path(storage.campaign_dir(record.campaign_id)) / record.storage / record.filename

    def read(self, record):
        path = self.path(record)
        return json.loads(path.read_text()) if path.exists() else None

    def prepare(self, record, document):
        return deepcopy(document)

    def write(self, record, document):
        storage.atomic_write_json(str(self.path(record)), document)

    def refresh(self, record):
        pass

    def notify(self, record):
        pass


@pytest.fixture
def workflow_pg(postgres_database, tmp_path, monkeypatch):
    database = postgres_database
    cid, gm, owner = 'a'*32, 'b'*32, 'c'*32
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'sql')
    monkeypatch.setattr(runtime, 'database', lambda:database)
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    monkeypatch.setattr(storage, 'SERVER_STATE_FILE', str(tmp_path / 'server_state.json'))
    # Production retains the process lock. Removing it here is deliberate:
    # independent SQL sessions must demonstrate the cross-process safety layer.
    monkeypatch.setattr(campaigns, '_CAMPAIGN_STORE_LOCK', nullcontext())
    campaign = {'id':cid,'name':'Race table','system':'pf2e','members':[
        {'user_id':gm,'role':'gm'},{'user_id':owner,'role':'player'}]}
    storage.atomic_write_json(storage.campaign_file(cid), campaign)
    storage.set_live_campaign_id(cid)
    with database.transaction() as db:
        db.add_all([User(id=uid,username=role,normalized_username=role,display_name=role,password_hash='test-only')
                    for uid,role in ((gm,'gm'),(owner,'owner'))])
        db.flush()
        db.add(Campaign(id=cid,slug='race',name='Race table',system='pf2e',created_by_user_id=gm))
        db.flush()
        db.add_all([CampaignMembership(campaign_id=cid,user_id=m['user_id'],role=m['role']) for m in campaign['members']])
    env = SimpleNamespace(database=database,cid=cid,gm=gm,owner=owner,
        make_store=lambda:SqlWorkflowStore(database))
    env.context = lambda:resolve_campaign_context(Principal.user(owner),campaign_id=cid,
        campaign=campaign,live_campaign_id=cid)
    env.drafts = service(env)
    env.workflow = WorkflowService(env.drafts,env.drafts.store,env.drafts.adapters,Files())
    return env


def race(monkeypatch, database, target, method_name, predicate, session_of, first, second):
    """Hold the winner's actual row lock until PostgreSQL reports the waiter."""
    original = getattr(target, method_name)
    local = threading.local()
    paused, release = threading.Event(), threading.Event()
    blocker = []

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        if getattr(local, 'slot', None) == 'first' and not paused.is_set() and predicate(args):
            blocker.append(session_of(args).scalar(text('SELECT pg_backend_pid()')))
            paused.set()
            assert release.wait(15), 'race release timed out'
        return result

    monkeypatch.setattr(target, method_name, wrapper)

    def run(slot, action):
        local.slot = slot
        try:
            return action()
        except WorkflowError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run, 'first', first)
        try:
            assert paused.wait(12), 'winner did not reach the selected transaction boundary'
            b = pool.submit(run, 'second', second)
            deadline = time.monotonic() + 10
            waiter = None
            with database.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                observer_pid = observer.scalar(text('SELECT pg_backend_pid()'))
                while time.monotonic() < deadline:
                    waiter = observer.scalar(text('SELECT pid FROM pg_stat_activity WHERE :blocker = ANY(pg_blocking_pids(pid)) LIMIT 1'), {'blocker':blocker[0]})
                    if waiter:
                        break
                assert waiter, 'second operation never contended on the PostgreSQL lock'
                assert len({waiter,blocker[0],observer_pid}) == 3
        finally:
            release.set()
        return a.result(timeout=15), b.result(timeout=15)


def imported():
    return DraftInput('pf2e_import',1,{}, {}, {'build':{
        'name':'Race hero','class':'Fighter','ancestry':'Human','level':1}})


def test_concurrent_quota_creation_uses_database_lock(workflow_pg, monkeypatch):
    env = workflow_pg
    for index in range(19):
        env.drafts.create(env.context(), partial(), request_key='seed-'+str(index))
    first, second = race(monkeypatch, env.database, SqlWorkflowTransaction, 'put_draft',
        lambda args:True, lambda args:args[0].session,
        lambda:env.drafts.create(env.context(),partial('winner'),request_key='first'),
        lambda:env.drafts.create(env.context(),partial('loser'),request_key='second'))
    assert first.inputs.form['name'] == 'winner'
    assert isinstance(second, WorkflowError) and second.status == 429
    assert len(env.drafts.list(env.context())) == 20


def test_stale_save_waits_then_rechecks_revision(workflow_pg, monkeypatch):
    env = workflow_pg
    draft = env.drafts.create(env.context(),partial(),request_key='create')
    first, second = race(monkeypatch, env.database, SqlWorkflowTransaction, 'put_draft',
        lambda args:True, lambda args:args[0].session,
        lambda:env.drafts.save(env.context(),draft.id,partial('winner'),expected_revision=0),
        lambda:env.drafts.save(env.context(),draft.id,partial('loser'),expected_revision=0))
    assert first.revision == 1
    assert isinstance(second, WorkflowError) and second.code == 'draft_revision_conflict'
    assert env.drafts.get(env.context(),draft.id).inputs.form['name'] == 'winner'


def test_simultaneous_publication_returns_one_id(workflow_pg, monkeypatch):
    env = workflow_pg
    draft = env.drafts.create(env.context(),imported(),request_key='create')
    publish = lambda:env.workflow.publish(env.context(),draft.id,expected_revision=0,request_key='publish')
    first, second = race(monkeypatch, env.database, SqlWorkflowTransaction, 'put_receipt',
        lambda args:args[1].operation == 'publish' and args[1].state == 'committed',
        lambda args:args[0].session, publish, publish)
    assert first == second and first.character_id
    assert len(list(Path(storage.party_dir(env.cid)).glob('*.json'))) == 1
    with env.database.session() as db:
        assert db.scalar(select(func.count()).select_from(Character)) == 1
        assert db.scalar(select(func.count()).select_from(CharacterAssignment)) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'character.workflow_published')) == 1


def test_publication_rechecks_revocation_after_database_wait(workflow_pg, monkeypatch):
    env = workflow_pg
    draft = env.drafts.create(env.context(),imported(),request_key='create')
    stale = env.context()
    first, second = race(monkeypatch, env.database, campaign_runtime, '_locked_campaign',
        lambda args:True, lambda args:args[0],
        lambda:campaign_runtime.remove_member(env.cid,env.owner,actor_user_id=env.gm),
        lambda:env.workflow.publish(stale,draft.id,expected_revision=0,request_key='publish'))
    assert first is not None
    assert isinstance(second, WorkflowError) and second.status in (403,404)
    assert not list(Path(storage.party_dir(env.cid)).glob('*.json'))
    with env.database.session() as db:
        assert db.scalar(select(func.count()).select_from(Draft)) == 0


def test_completed_publication_precedes_waiting_revocation(workflow_pg, monkeypatch):
    env = workflow_pg
    draft = env.drafts.create(env.context(),imported(),request_key='create')
    first, second = race(monkeypatch, env.database, SqlWorkflowTransaction, 'put_receipt',
        lambda args:args[1].operation == 'publish' and args[1].state == 'committed',
        lambda args:args[0].session,
        lambda:env.workflow.publish(env.context(),draft.id,expected_revision=0,request_key='publish'),
        lambda:campaign_runtime.remove_member(env.cid,env.owner,actor_user_id=env.gm))
    assert first.character_id and second is not None
    with env.database.session() as db:
        assert db.get(CampaignMembership,(env.cid,env.owner)) is None
        assert db.scalar(select(func.count()).select_from(Draft)) == 0
        assert db.scalar(select(func.count()).select_from(CharacterAssignment)) == 0
