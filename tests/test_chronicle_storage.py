import os
import json
import tempfile

# Bind app's DATA_DIR to a throwaway BEFORE any `import app` (mirrors
# tests/test_cosmere_campaign_binding.py); harmless for the pure-storage tests.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='chron-data-'))
os.environ.setdefault('GM_PASSWORD', '')

import pytest
from core import storage


def test_chronicle_dir_is_under_campaign():
    cid = storage.new_id()
    assert storage.chronicle_dir(cid) == os.path.join(storage.campaign_dir(cid), 'chronicle')


def test_chronicle_dir_rejects_traversal_id():
    with pytest.raises(ValueError):
        storage.chronicle_dir('../escape')


import app as A  # top-level import; DATA_DIR already pinned above


def test_chronicle_dir_binds_campaign_branch():
    cid = storage.new_id()
    A._bind_campaign_paths(cid)
    assert A.CHRONICLE_DIR == storage.chronicle_dir(cid)


def test_chronicle_dir_binds_flat_fallback():
    A._bind_campaign_paths(None)          # legacy-open dev mode
    assert A.CHRONICLE_DIR == os.path.join(A.DATA_DIR, 'chronicle')


def _stage_content(chron_dir, h, session=1):
    """Build a fake, fully-rendered content tree under .staging/<h> and return it."""
    d = os.path.join(chron_dir, '.staging', h)
    os.makedirs(os.path.join(d, 'html'), exist_ok=True)
    with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'schema_version': 1, 'session_number': session, 'pages': []}, f)
    return d


@pytest.fixture
def chron(tmp_path, monkeypatch):
    cd = str(tmp_path / 'chronicle')
    os.makedirs(cd, exist_ok=True)
    monkeypatch.setattr(A, 'CHRONICLE_DIR', cd)
    return cd


def test_content_dir_is_none_before_first_publish(chron):
    assert A._chronicle_content_dir() is None
    assert A._chronicle_manifest() is None


def test_content_dir_is_none_when_current_target_has_no_manifest(chron):
    target = os.path.join(chron, 'content', 'nomanifest')
    os.makedirs(target, exist_ok=True)
    os.symlink(target, os.path.join(chron, 'current'))
    assert A._chronicle_content_dir() is None
    assert A._chronicle_manifest() is None


def test_content_dir_is_none_when_current_is_dangling_symlink(chron):
    os.symlink(os.path.join(chron, 'content', 'nonexistent'), os.path.join(chron, 'current'))
    assert A._chronicle_content_dir() is None
    assert A._chronicle_manifest() is None


def test_content_dir_is_none_when_chronicle_dir_is_none(monkeypatch):
    monkeypatch.setattr(A, 'CHRONICLE_DIR', None)
    assert A._chronicle_content_dir() is None
    assert A._chronicle_manifest() is None


def test_swap_publishes_and_current_resolves(chron):
    A._chronicle_swap(_stage_content(chron, 'hashA', session=3), 'hashA')
    assert A._chronicle_content_dir() == os.path.join(chron, 'content', 'hashA')
    assert os.path.realpath(os.path.join(chron, 'current')).endswith('hashA')
    assert A._chronicle_manifest()['session_number'] == 3
    # staging consumed by the move
    assert not os.path.exists(os.path.join(chron, '.staging', 'hashA'))


def test_second_swap_rotates_previous_and_prunes(chron):
    A._chronicle_swap(_stage_content(chron, 'h1', session=1), 'h1')
    A._chronicle_swap(_stage_content(chron, 'h2', session=2), 'h2')
    assert A._chronicle_manifest()['session_number'] == 2
    assert os.path.realpath(os.path.join(chron, 'previous')).endswith('h1')
    # both current + previous targets survive; nothing else lingers
    kept = set(os.listdir(os.path.join(chron, 'content')))
    assert kept == {'h1', 'h2'}
