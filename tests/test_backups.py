"""Guards for on-volume backup snapshots (core/backups.py).

Two properties matter for the Railway volume footprint: (1) the published
Chronicle export — large + regenerable from the GM's Obsidian vault — is
excluded from snapshots, and (2) old snapshots are pruned to a bounded count."""
from __future__ import annotations

import os
import zipfile

from core import backups, storage

CID = '0123456789abcdef0123456789abcdef'  # valid 32-hex campaign id


def test_snapshot_excludes_chronicle_export(tmp_path, monkeypatch):
    camps = tmp_path / 'campaigns'
    cdir = camps / CID
    (cdir / 'encounters').mkdir(parents=True)
    (cdir / 'campaign.json').write_text('{}')
    (cdir / 'encounters' / 'e1.json').write_text('{}')
    # A big, regenerable Chronicle export that should NOT be backed up.
    (cdir / 'chronicle' / 'content' / 'h1').mkdir(parents=True)
    (cdir / 'chronicle' / 'content' / 'h1' / 'big.bin').write_bytes(b'x' * 4096)

    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(camps))
    monkeypatch.setattr(backups, 'BACKUPS_DIR', str(tmp_path / 'backups'))

    path = backups.snapshot_campaign(CID)
    assert path and os.path.exists(path)
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert 'campaign.json' in names
    assert any(n.replace(os.sep, '/').startswith('encounters/') for n in names)
    assert not any('chronicle' in n for n in names), f"chronicle leaked into backup: {names}"


def test_prune_keeps_only_n_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(backups, 'BACKUPS_DIR', str(tmp_path / 'backups'))
    d = backups._campaign_backup_dir(CID)
    os.makedirs(d)
    for i in range(10):
        p = os.path.join(d, f'2026-{i:02d}.zip')
        open(p, 'w').close()
        os.utime(p, (1000 + i, 1000 + i))  # ascending mtime => i=9 newest
    # max_age huge so only the keep-count gates.
    backups.prune_campaign(CID, keep=7, max_age_days=10 ** 6)
    remaining = sorted(f for f in os.listdir(d) if f.endswith('.zip'))
    assert len(remaining) == 7
    # The three oldest (00,01,02) were dropped.
    assert '2026-00.zip' not in remaining
    assert '2026-09.zip' in remaining
