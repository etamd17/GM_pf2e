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


def test_swap_prune_survives_symlinked_ancestor(tmp_path, monkeypatch):
    """Bug 1 regression: when an ancestor of CHRONICLE_DIR is a symlink (e.g.
    macOS mktemp's /var -> /private/var), the orphan-prune compared an
    UNRESOLVED path against a `keep` set built from os.path.realpath(), so
    they never string-matched and the loop rmtree'd the just-published
    content out from under the live `current` symlink. Run two swaps and
    assert the just-published content survives after EACH one."""
    real = tmp_path / 'real'
    real.mkdir()
    link = tmp_path / 'link'
    os.symlink(str(real), str(link))
    chron_dir = os.path.join(str(link), 'chronicle')
    os.makedirs(chron_dir, exist_ok=True)
    monkeypatch.setattr(A, 'CHRONICLE_DIR', chron_dir)

    A._chronicle_swap(_stage_content(chron_dir, 'h1', session=1), 'h1')
    assert A._chronicle_manifest() is not None
    assert A._chronicle_manifest()['session_number'] == 1

    A._chronicle_swap(_stage_content(chron_dir, 'h2', session=2), 'h2')
    assert A._chronicle_manifest() is not None
    assert A._chronicle_manifest()['session_number'] == 2
    # the just-published content must not have been pruned as an "orphan"
    kept = set(os.listdir(os.path.join(chron_dir, 'content')))
    assert kept == {'h1', 'h2'}


def test_swap_same_hash_republish_does_not_dangle(chron):
    """Bug 2 regression: republishing under a hash that's already live used to
    unconditionally `shutil.rmtree(dest)` before re-moving the staged content
    in, leaving a transient window where `dest` (the live target `current`
    points at) doesn't exist on disk at all -- a reader in that window sees
    _chronicle_content_dir()/_chronicle_manifest() return None. Since
    production hashes are content-derived, a same-hash republish means the
    staged content is already identical to what's live, so the fix drops the
    redundant staging dir instead of destructively replacing the live one.
    (NOTE: this test stages *different* manifests under the same hash purely
    to make the "was the live dir ever destroyed" outcome observable; the
    correct/fixed behavior is that the ORIGINAL publish stays live -- so the
    session_number asserted below is 1, not the second staging dir's 2.)"""
    A._chronicle_swap(_stage_content(chron, 'hX', session=1), 'hX')
    assert A._chronicle_manifest()['session_number'] == 1

    A._chronicle_swap(_stage_content(chron, 'hX', session=2), 'hX')
    assert A._chronicle_content_dir() is not None
    assert A._chronicle_manifest() is not None
    assert A._chronicle_manifest()['session_number'] == 1
