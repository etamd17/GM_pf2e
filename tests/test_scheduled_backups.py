"""Automatic on-volume campaign snapshots (data-safety): a daily thread zips each
active campaign into DATA_DIR/backups/<cid>/<stamp>.zip and prunes old ones, so a
bad edit/corruption can be rolled back. Complements soft-delete (accidental
delete) — note these are on the same volume, so not a substitute for off-site.
"""
from __future__ import annotations

import os
import threading
import time
import zipfile

import pytest

import core.storage as S
import core.campaigns as C
import core.backups as B


@pytest.fixture
def tmpdata(tmp_path, monkeypatch):
    monkeypatch.setattr(S, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    monkeypatch.setattr(S, 'CAMPAIGNS_TRASH_DIR', str(tmp_path / 'trash'))
    monkeypatch.setattr(S, 'SERVER_STATE_FILE', str(tmp_path / 'server_state.json'))
    monkeypatch.setattr(B, 'BACKUPS_DIR', str(tmp_path / 'backups'))
    yield tmp_path


def _mk():
    cid = C.create_campaign('Saga', 'pf2e', 'u1')['id']
    with open(os.path.join(S.party_dir(cid), 'pc.json'), 'w', encoding='utf-8') as f:
        f.write('{"hp":42}')
    return cid


def test_run_backup_snapshots_and_records(tmpdata):
    cid = _mk()
    assert B.run_backup() == 1
    lp = B.latest_backup(cid)
    assert lp and os.path.isfile(lp)
    assert B.last_backup_at()
    assert S.load_server_state()[B._CAMPAIGN_COMPLETION_KEY][cid] > 0
    assert any('pc.json' in n for n in zipfile.ZipFile(lp).namelist())   # real data captured


def test_partial_run_retries_only_failed_campaigns(tmpdata, monkeypatch):
    healthy = _mk()
    blocked = C.create_campaign('Blocked', 'pf2e', 'u1')['id']
    with open(os.path.join(S.party_dir(blocked), 'pc.json'), 'w', encoding='utf-8') as f:
        f.write('{"hp":21}')

    now = int(time.time())
    monkeypatch.setattr(B.time, 'time', lambda: now)
    real_snapshot = B.snapshot_campaign
    attempts = []

    def fail_blocked_once(cid, stamp=None):
        attempts.append(cid)
        if cid == blocked and attempts.count(blocked) == 1:
            raise RuntimeError('blocked once')
        return real_snapshot(cid, stamp)

    monkeypatch.setattr(B, 'snapshot_campaign', fail_blocked_once)

    assert B.run_backup() == 1
    state = S.load_server_state()
    assert state.get('last_backup_at') is None
    assert state[B._CAMPAIGN_COMPLETION_KEY] == {healthy: now}
    assert B._automatic_backup_due_ids(now=now + 3600) == (blocked,)

    assert B.run_backup((blocked,)) == 1
    state = S.load_server_state()
    assert state['last_backup_at'] == now
    assert state[B._CAMPAIGN_COMPLETION_KEY] == {
        healthy: now,
        blocked: now,
    }
    assert attempts.count(healthy) == 1
    assert attempts.count(blocked) == 2
    assert len(os.listdir(B._campaign_backup_dir(healthy))) == 1
    assert len(os.listdir(B._campaign_backup_dir(blocked))) == 1


def test_backup_timestamp_cannot_overwrite_a_live_campaign_switch(
    tmpdata, monkeypatch
):
    cid = 'a' * 32
    activation_entered = threading.Event()
    allow_activation_write = threading.Event()
    backup_finished = threading.Event()
    errors = []
    original_write = S.atomic_write_json

    def delayed_write(path, document, *args, **kwargs):
        if (
            path == S.SERVER_STATE_FILE
            and document.get('live_campaign_id') == cid
            and not activation_entered.is_set()
        ):
            activation_entered.set()
            if not allow_activation_write.wait(timeout=10):
                raise TimeoutError('test did not release activation write')
        return original_write(path, document, *args, **kwargs)

    monkeypatch.setattr(S, 'atomic_write_json', delayed_write)

    def activate():
        try:
            S.set_live_campaign_id(cid)
        except Exception as exc:
            errors.append(exc)

    def back_up():
        try:
            B.run_backup()
        except Exception as exc:
            errors.append(exc)
        finally:
            backup_finished.set()

    activation = threading.Thread(target=activate)
    backup = threading.Thread(target=back_up)
    activation.start()
    assert activation_entered.wait(timeout=10)
    backup.start()
    try:
        # update_server_state holds one lock across load + atomic replace, so
        # backup cannot load a stale document while activation is paused.
        assert not backup_finished.wait(timeout=0.1)
    finally:
        allow_activation_write.set()
        activation.join(timeout=10)
        backup.join(timeout=10)

    assert not activation.is_alive()
    assert not backup.is_alive()
    assert errors == []
    state = S.load_server_state()
    assert state['live_campaign_id'] == cid
    assert state['last_backup_at'] > 0


def test_snapshot_excludes_incomplete_transaction_artifacts(tmpdata):
    cid = _mk()
    party_dir = S.party_dir(cid)
    artifacts = (
        'pc.json.batch-stage',
        'pc.json.batch-backup',
        'pc.json.tmp',
    )
    for name in artifacts:
        with open(os.path.join(party_dir, name), 'w', encoding='utf-8') as f:
            f.write('incomplete transaction state')

    path = B.snapshot_campaign(cid, stamp='clean')

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert 'party_data/pc.json' in names
    assert not any(name.endswith(artifacts) for name in names), names


def test_snapshot_blocks_unresolved_character_batch_journal(tmpdata):
    cid = _mk()
    journal = os.path.join(S.party_dir(cid), 'txn.character-batch-journal')
    with open(journal, 'w', encoding='utf-8') as f:
        f.write('{}')

    with pytest.raises(
        RuntimeError,
        match='snapshot blocked by an unresolved character batch',
    ):
        B.snapshot_campaign(cid, stamp='blocked')

    backup_dir = B._campaign_backup_dir(cid)
    assert not os.path.exists(os.path.join(backup_dir, 'blocked.zip'))
    assert not os.path.exists(os.path.join(backup_dir, 'blocked.zip.tmp'))


def test_prune_keeps_newest_n(tmpdata):
    import time
    cid = _mk()
    base = time.time()
    for i in range(10):
        p = B.snapshot_campaign(cid, stamp='s%02d' % i)
        t = base + i                       # distinct + recent mtimes -> deterministic "newest"
        os.utime(p, (t, t))
    B.prune_campaign(cid, keep=7, max_age_days=9999)
    left = sorted(f for f in os.listdir(B._campaign_backup_dir(cid)) if f.endswith('.zip'))
    assert len(left) == 7 and 's09.zip' in left and 's00.zip' not in left


def test_prune_drops_old(tmpdata):
    cid = _mk()
    p = B.snapshot_campaign(cid, stamp='old')
    old = os.path.getmtime(p) - 40 * 86400
    os.utime(p, (old, old))
    B.snapshot_campaign(cid, stamp='new')
    B.prune_campaign(cid, keep=99, max_age_days=30)
    left = [f for f in os.listdir(B._campaign_backup_dir(cid)) if f.endswith('.zip')]
    assert 'new.zip' in left and 'old.zip' not in left


def test_trashed_campaigns_not_snapshotted(tmpdata):
    cid = _mk()
    C.delete_campaign(cid)                 # soft-deleted -> out of list_campaign_ids
    assert B.run_backup() == 0


def test_backup_now_without_selected_campaign_scopes_to_gm_campaigns(monkeypatch):
    import app as application

    user = {'id': 'gm-1', 'username': 'gm', 'is_admin': False}
    gm_campaign = {
        'id': 'a' * 32,
        'members': [{'user_id': user['id'], 'role': 'gm'}],
    }
    player_campaign = {
        'id': 'b' * 32,
        'members': [{'user_id': user['id'], 'role': 'player'}],
    }
    calls = []

    monkeypatch.setattr(
        application._auth,
        'account_store_state',
        lambda: application._auth.ACCOUNT_STORE_READY,
    )
    monkeypatch.setattr(application._auth, 'current_user', lambda: user)
    monkeypatch.setattr(
        application._campaigns,
        'campaigns_for_user',
        lambda user_id: [gm_campaign, player_campaign],
    )
    monkeypatch.setattr(
        application._backups,
        'run_backup',
        lambda campaign_ids, **kwargs: calls.append((tuple(campaign_ids), kwargs)) or 1,
    )
    monkeypatch.setattr(application._backups, 'last_backup_at', lambda: 1234567890)

    response = application.app.test_client().post('/api/backup_now')

    assert response.status_code == 200
    assert response.get_json() == {
        'ok': True,
        'count': 1,
        'last_backup_at': 1234567890,
    }
    assert calls == [((gm_campaign['id'],), {'record_completion': False})]


def test_backup_now_rejects_authenticated_player_without_gm_campaign(monkeypatch):
    import app as application

    user = {'id': 'player-1', 'username': 'player', 'is_admin': False}
    player_campaign = {
        'id': 'b' * 32,
        'members': [{'user_id': user['id'], 'role': 'player'}],
    }
    calls = []

    monkeypatch.setattr(
        application._auth,
        'account_store_state',
        lambda: application._auth.ACCOUNT_STORE_READY,
    )
    monkeypatch.setattr(application._auth, 'current_user', lambda: user)
    monkeypatch.setattr(
        application._campaigns,
        'campaigns_for_user',
        lambda user_id: [player_campaign],
    )
    monkeypatch.setattr(
        application._backups,
        'run_backup',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    response = application.app.test_client().post('/api/backup_now')

    assert response.status_code == 403
    assert response.get_json() == {'ok': False, 'error': 'not authorized'}
    assert calls == []


def test_ensure_thread_idempotent():
    B.ensure_backup_thread()
    B.ensure_backup_thread()               # second call is a no-op (no crash, no dup)
    assert B._thread_started is True


def test_account_home_shows_backup_controls_for_gm():
    import app
    import flask
    with app.app.test_request_context('/me'):
        html = flask.render_template(
            'account_home.html',
            user={'id': 'u1', 'username': 'gm', 'is_admin': False},
            campaigns=[{'id': 'c' * 32, 'name': 'Saga', 'system': 'pf2e',
                        'members': [{'user_id': 'u1', 'role': 'gm'}]}],
            gm_campaign_ids=['c' * 32], characters=[],
            active_campaign_id=None, live_campaign_id=None, last_campaign=None,
            trashed_campaigns=[], trash_ttl_days=30, last_backup_at=1700000000)
    assert 'Last automatic backup' in html and 'backupNow' in html and '/api/backup_now' in html


def test_account_home_hides_backup_controls_for_non_gm():
    import app
    import flask
    with app.app.test_request_context('/me'):
        html = flask.render_template(
            'account_home.html',
            user={'id': 'p1', 'username': 'player', 'is_admin': False},
            campaigns=[], gm_campaign_ids=[], characters=[],
            active_campaign_id=None, live_campaign_id=None, last_campaign=None,
            trashed_campaigns=[], trash_ttl_days=30, last_backup_at=None)
    assert '/api/backup_now' not in html      # a player with no GM games sees no backup control
