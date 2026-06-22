"""core/backups.py -- automatic on-volume campaign snapshots.

A daily background thread zips every active campaign into DATA_DIR/backups/<cid>/
<stamp>.zip and prunes old snapshots, so a bad bulk edit or corruption can be
rolled back to a recent point-in-time (complementing the soft-delete trash, which
covers accidental deletion). A GM can also trigger a snapshot on demand and
download the latest zip to pull it off-device.

NOTE: these snapshots live on the SAME Railway volume as the live data, so they
protect against bad edits/corruption but NOT volume loss. True off-site
durability needs object-storage credentials; when those are provided, an upload
step can hook into run_backup() (the snapshot path is returned for exactly that).
"""
import os
import time
import zipfile
import threading

from core import storage

BACKUPS_DIR = os.path.join(storage.DATA_DIR, 'backups')
KEEP_PER_CAMPAIGN = 7          # most-recent snapshots kept per campaign
MAX_AGE_DAYS = 30
INTERVAL_SECS = 24 * 3600      # one automatic snapshot per day


def _stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def _campaign_backup_dir(cid):
    return os.path.join(BACKUPS_DIR, storage._check_id(cid, 'campaign_id'))


def snapshot_campaign(cid, stamp=None):
    """Zip one campaign's data dir into its backup folder. Returns the zip path
    (or None if the campaign dir is missing)."""
    src = storage.campaign_dir(cid)
    if not os.path.isdir(src):
        return None
    out_dir = _campaign_backup_dir(cid)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, (stamp or _stamp()) + '.zip')
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, src))
    return path


def prune_campaign(cid, keep=KEEP_PER_CAMPAIGN, max_age_days=MAX_AGE_DAYS):
    """Keep the `keep` newest snapshots for a campaign and drop anything older
    than max_age_days."""
    out_dir = _campaign_backup_dir(cid)
    if not os.path.isdir(out_dir):
        return
    zips = sorted((os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith('.zip')),
                  key=os.path.getmtime, reverse=True)
    cutoff = time.time() - max_age_days * 86400
    for i, p in enumerate(zips):
        if i >= keep or os.path.getmtime(p) < cutoff:
            try:
                os.remove(p)
            except OSError:
                pass


def latest_backup(cid):
    """Path to a campaign's most-recent snapshot, or None."""
    out_dir = _campaign_backup_dir(cid)
    if not os.path.isdir(out_dir):
        return None
    zips = [os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.endswith('.zip')]
    return max(zips, key=os.path.getmtime) if zips else None


def last_backup_at():
    """Epoch seconds of the last completed run_backup(), or None."""
    return storage.load_server_state().get('last_backup_at')


def run_backup():
    """Snapshot every active (non-trashed) campaign + prune, and record the time.
    Returns the number of campaigns snapshotted."""
    stamp = _stamp()
    n = 0
    for cid in storage.list_campaign_ids():
        try:
            if snapshot_campaign(cid, stamp):
                n += 1
            prune_campaign(cid)
        except Exception as e:                  # one bad campaign shouldn't abort the rest
            print(f"[BACKUP] {cid}: {e}")
    st = storage.load_server_state()
    st['last_backup_at'] = int(time.time())
    storage.atomic_write_json(storage.SERVER_STATE_FILE, st)
    return n


# --- daily scheduler (lazy, gunicorn-safe: starts inside the worker) ----------
_thread_started = False
_thread_lock = threading.Lock()


def _loop():
    while True:
        try:
            if time.time() - (last_backup_at() or 0) >= INTERVAL_SECS:
                run_backup()
        except Exception as e:
            print(f"[BACKUP loop] {e}")
        time.sleep(3600)            # re-check hourly; the timestamp gate paces it to daily


def ensure_backup_thread():
    """Start the daily snapshot thread exactly once."""
    global _thread_started
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True
        threading.Thread(target=_loop, daemon=True, name='daily-backup').start()
