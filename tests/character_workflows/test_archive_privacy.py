"""Portable campaigns exclude personal drafts; full-volume snapshots include them."""

import io
from pathlib import Path
import shutil
import zipfile

import pytest

from core import backups
from core.character_workflows.types import WorkflowError
from test_drafts import partial, service
from test_publication import imported, setup_workflow
from test_recovery import interrupt_completion
from test_migration import migration_env


def test_portable_archives_exclude_private_drafts(migration_env, tmp_path):
    env = migration_env
    draft = service(env).create(env.context(), partial('PRIVATE INPUT'), request_key='draft')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        assert backups.write_campaign_archive(archive, env.cid)
    with zipfile.ZipFile(buffer) as archive:
        assert all('character_drafts' not in name for name in archive.namelist())
        assert all(b'PRIVATE INPUT' not in archive.read(name) for name in archive.namelist())
    volume_copy = tmp_path / 'full-volume-snapshot'
    shutil.copytree(env.source, volume_copy)
    relative = Path('character_drafts') / env.cid / draft.author_id / (draft.id + '.json')
    assert (volume_copy / relative).read_bytes() == (env.source / relative).read_bytes()


def test_campaign_archive_refuses_pending_publication(migration_env, monkeypatch):
    env = migration_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with zipfile.ZipFile(io.BytesIO(), 'w') as archive, pytest.raises(WorkflowError):
        backups.write_campaign_archive(archive, env.cid)
