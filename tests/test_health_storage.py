"""/health has to answer "is player data actually landing on the volume?"

DATA_DIR falls back to BASE_DIR silently, so a missing env var sends every
write into the container, where each deploy wipes it. The quieter failure is
worse: DATA_DIR=/data set correctly but the volume never mounted, so the app
writes to the container's own /data. Configured, distinct-from-repo and
writable are all true in that case -- nothing observable within one process
separates it from a healthy mount.

So the probe keeps evidence instead of inferring: a marker file counting boots.
More than one boot means the directory survived a restart. That is the only
field here that can actually catch an unmounted volume, and these tests hold it
to that, including the case where the directory is wiped between boots.

Note what the symptom looks like from the table: players lose their sheets on
deploy -- identical to the persistence-thread bug fixed earlier, and to the
save/reload race after it. Three different causes, one complaint. This endpoint
exists to tell them apart.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

import app as app_module


MARKER = app_module._STORAGE_MARKER_NAME


@pytest.fixture
def client():
    return app_module.app.test_client()


def _probe(d, **kw):
    return app_module._probe_storage(str(d), app_module.BASE_DIR, **kw)


# --- the evidence, which is the part that matters -------------------------

def test_a_first_boot_proves_nothing_either_way(tmp_path):
    """One boot is not evidence. Claiming persistence here would be a guess."""
    info = _probe(tmp_path)
    assert info['boots_observed'] == 1
    assert info['persistence_proven'] is False


def test_surviving_a_second_boot_is_the_proof(tmp_path):
    _probe(tmp_path)
    info = _probe(tmp_path)
    assert info['boots_observed'] == 2
    assert info['persistence_proven'] is True


def test_a_wiped_directory_never_accumulates_proof(tmp_path):
    """An unmounted volume: writable, distinct from the repo, and reset every
    deploy. The count is the only field that notices."""
    for _ in range(5):
        shutil.rmtree(tmp_path)          # what a redeploy does to container FS
        tmp_path.mkdir()
        info = _probe(tmp_path)
        assert info['writable'] is True
        assert info['boots_observed'] == 1
        assert info['persistence_proven'] is False


def test_age_is_measured_from_the_first_boot_not_this_one(tmp_path):
    marker = tmp_path / MARKER
    marker.write_text(json.dumps({'boots': 9, 'first_seen': 0, 'last_boot': 0}),
                      encoding='utf-8')
    info = _probe(tmp_path)
    assert info['boots_observed'] == 10
    assert info['age_days'] > 1000       # epoch 0 was a while ago


def test_a_corrupt_marker_is_not_fatal(tmp_path):
    """A half-written marker must not take the app down at import."""
    (tmp_path / MARKER).write_text('{not json', encoding='utf-8')
    info = _probe(tmp_path)
    assert info['boots_observed'] == 1
    assert info['writable'] is True


def test_an_unwritable_data_dir_is_reported_not_raised(tmp_path):
    """A mounted-but-read-only volume. os.makedirs on a path whose parent is a
    FILE cannot succeed on any platform, which keeps this portable."""
    blocker = tmp_path / 'blocker'
    blocker.write_text('not a directory', encoding='utf-8')
    info = _probe(blocker / 'data')
    assert info['writable'] is False
    assert info['persistence_proven'] is False


def test_read_only_probe_leaves_no_trace(tmp_path):
    """The pytest/reloader path. It must not create the marker."""
    info = _probe(tmp_path, write=False)
    assert not (tmp_path / MARKER).exists()
    assert info['boots_observed'] == 0


# --- the inferences, which are cheap but insufficient alone ---------------

def test_configured_reflects_the_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv('DATA_DIR', raising=False)
    assert _probe(tmp_path, write=False)['configured'] is False
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    assert _probe(tmp_path, write=False)['configured'] is True


def test_separate_from_repo_catches_the_silent_fallback(tmp_path):
    """DATA_DIR unset -> writes land in the checkout."""
    assert _probe(tmp_path, write=False)['separate_from_repo'] is True
    fallback = app_module._probe_storage(
        app_module.BASE_DIR, app_module.BASE_DIR, write=False)
    assert fallback['separate_from_repo'] is False


# --- the endpoint ---------------------------------------------------------

def test_health_reports_storage(client, monkeypatch):
    monkeypatch.setattr(app_module, '_is_gm', lambda: False)
    body = client.get('/health').get_json()
    assert body['status'] == 'healthy'
    for key in ('configured', 'separate_from_repo', 'writable',
                'boots_observed', 'persistence_proven'):
        assert key in body['storage'], key


def test_health_does_not_leak_paths_to_anonymous_callers(client, monkeypatch):
    """Railway polls this with no session, and so can anyone else. The findings
    are safe to publish; the filesystem layout is not."""
    monkeypatch.setattr(app_module, '_is_gm', lambda: False)
    storage = client.get('/health').get_json()['storage']
    assert 'data_dir' not in storage
    assert 'base_dir' not in storage
    blob = json.dumps(storage)
    assert app_module.DATA_DIR not in blob
    assert app_module.BASE_DIR not in blob


def test_a_gm_gets_the_actual_paths(client, monkeypatch):
    monkeypatch.setattr(app_module, '_is_gm', lambda: True)
    storage = client.get('/health').get_json()['storage']
    assert storage['data_dir'] == app_module.DATA_DIR
    assert storage['base_dir'] == app_module.BASE_DIR


def test_health_stays_free_of_disk_io(client, monkeypatch):
    """Railway polls this on a schedule; the probe is a boot-time cost, not a
    per-request one. Guarded because moving the probe into the route would look
    harmless and would put a write on every health check."""
    calls = []
    monkeypatch.setattr(app_module, '_probe_storage',
                        lambda *a, **k: calls.append(a) or {})
    client.get('/health')
    assert calls == []


def test_the_probe_did_not_write_into_the_repo_during_this_run():
    """The suite imports app.py with DATA_DIR unset, so an unguarded probe
    would drop its marker in the checkout -- the same way an earlier test
    dropped one and deleted a tracked file."""
    assert not os.path.exists(os.path.join(app_module.BASE_DIR, MARKER))
