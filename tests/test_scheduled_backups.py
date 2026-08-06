"""Automatic on-volume campaign snapshots (data-safety): a daily thread zips each
active campaign into DATA_DIR/backups/<cid>/<stamp>.zip and prunes old ones, so a
bad edit/corruption can be rolled back. Complements soft-delete (accidental
delete) — note these are on the same volume, so not a substitute for off-site.
"""
from __future__ import annotations

import os
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
    with open(os.path.join(S.party_dir(cid), 'pc.json'), 'w') as f:
        f.write('{"hp":42}')
    return cid


def test_run_backup_snapshots_and_records(tmpdata):
    cid = _mk()
    assert B.run_backup() == 1
    lp = B.latest_backup(cid)
    assert lp and os.path.isfile(lp)
    assert B.last_backup_at()
    assert any('pc.json' in n for n in zipfile.ZipFile(lp).namelist())   # real data captured


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
