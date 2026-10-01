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
import json
import time
import zipfile
import threading

from sqlalchemy import select

from core import campaigns, storage
from core.persistence import campaign_runtime, ownership, runtime
from core.persistence.models import Campaign

BACKUPS_DIR = os.path.join(storage.DATA_DIR, 'backups')
KEEP_PER_CAMPAIGN = 7          # most-recent snapshots kept per campaign
MAX_AGE_DAYS = 30
INTERVAL_SECS = 24 * 3600      # one automatic snapshot per day
_CAMPAIGN_COMPLETION_KEY = 'backup_campaign_completed_at'

# Top-level campaign subdirs excluded from snapshots. The published Chronicle
# export (campaign_dir/chronicle) is a large, REGENERABLE artifact — the GM
# re-publishes it from their Obsidian vault — so zipping it into 7 daily
# snapshots would bloat the volume for no recovery value. Backups exist to
# protect live campaign data (PCs, encounters, config, threads), not derived
# output.
_BACKUP_EXCLUDE_DIRS = {'chronicle'}
_TRANSACTION_ARTIFACT_SUFFIXES = (
    '.character-batch-journal',
    '.batch-stage',
    '.batch-backup',
    '.tmp',
)

# The application replaces this with its live-campaign dispatch RLock during
# startup. Keeping a local default makes this module safe in standalone tests.
SNAPSHOT_LOCK = threading.RLock()


def is_transaction_artifact(path):
    """Return whether an archive member is internal, incomplete write state."""
    return os.path.basename(str(path)).endswith(_TRANSACTION_ARTIFACT_SUFFIXES)


def _stamp():
    return time.strftime('%Y%m%d-%H%M%S')


def _campaign_backup_dir(cid):
    return os.path.join(BACKUPS_DIR, storage._check_id(cid, 'campaign_id'))


def _active_campaign_ids():
    if runtime.sql_enabled():
        return tuple(campaign['id'] for campaign in campaigns.list_campaigns())
    return storage.list_campaign_ids()


def _write_archive_files(archive, src, *, include_chronicle, campaign=None,
                         character_document=None):
    if campaign is not None:
        archive.writestr('campaign.json', json.dumps(campaign, ensure_ascii=False,
                                                    allow_nan=False))
    for root, dirs, files in os.walk(src):
        if root == src and not include_chronicle:
            dirs[:] = [d for d in dirs if d not in _BACKUP_EXCLUDE_DIRS]
        relative_dir = os.path.relpath(root, src)
        for filename in files:
            full = os.path.join(root, filename)
            if is_transaction_artifact(full):
                continue
            if campaign is not None and root == src and filename == 'campaign.json':
                continue
            member = os.path.relpath(full, src)
            if (character_document is not None
                    and relative_dir in {'party_data', 'cosmere_pcs'}
                    and filename.endswith('.json')):
                with open(full, encoding='utf-8') as source:
                    document = json.load(source)
                document = character_document(document, relative_dir, filename)
                archive.writestr(member, json.dumps(document, ensure_ascii=False,
                                                     allow_nan=False))
            else:
                archive.write(full, member)


def _write_sql_archive(archive, cid, src, *, include_chronicle):
    with runtime.database().transaction() as session:
        campaign = session.scalar(select(Campaign).where(
            Campaign.id == cid,
        ).with_for_update())
        if campaign is None or campaign.trashed_at is not None:
            return False

        def character_document(document, folder, filename):
            if not isinstance(document, dict):
                raise ValueError('character document must be an object')
            character = ownership._resolve_identity(session, cid, document, folder, filename)
            # Preserve orphaned sheet data for recovery without granting any
            # of its untrusted file permissions to a restored character.
            if character is None:
                return ownership._no_grants(document)
            return ownership._overlay(session, character, document)

        # Reuse session-bound adapters so metadata and grants share a campaign
        # row lock with SQL membership/ownership writers, without nested
        # sessions or inconsistent reads from separate transactions.
        _write_archive_files(
            archive, src, include_chronicle=include_chronicle,
            campaign=campaign_runtime._document(session, campaign),
            character_document=character_document,
        )
        return True


def write_campaign_archive(archive, cid, *, include_chronicle=False):
    """Write active campaign data to an open zip, returning whether it exists.

    SQL archives overlay current relational metadata and character grants; JSON
    and shadow archives preserve legacy bytes. Callers must discard the archive
    on error. The SQL row lock coordinates authority changes across processes;
    the existing snapshot lock coordinates local character-file writes.
    """
    with SNAPSHOT_LOCK:
        src = storage.campaign_dir(cid)
        if not os.path.isdir(src):
            return False
        from core.character_workflows.recovery import assert_no_pending_workflows
        assert_no_pending_workflows(cid)
        party_dir = storage.party_dir(cid)
        if os.path.isdir(party_dir) and any(
            name.endswith('.character-batch-journal')
            for name in os.listdir(party_dir)
        ):
            raise RuntimeError(
                'campaign snapshot blocked by an unresolved character batch'
            )
        if runtime.sql_enabled():
            return runtime.store_call(
                _write_sql_archive, archive, cid, src,
                include_chronicle=include_chronicle,
            )
        _write_archive_files(archive, src, include_chronicle=include_chronicle)
        return True


def snapshot_campaign(cid, stamp=None):
    """Zip one campaign's data dir into its backup folder. Returns the zip path
    (or None if the campaign dir is missing)."""
    with SNAPSHOT_LOCK:
        src = storage.campaign_dir(cid)
        if not os.path.isdir(src):
            return None
        out_dir = _campaign_backup_dir(cid)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, (stamp or _stamp()) + '.zip')
        temporary_path = path + '.tmp'
        try:
            with zipfile.ZipFile(temporary_path, 'w', zipfile.ZIP_DEFLATED) as z:
                archived = write_campaign_archive(z, cid)
            if not archived:
                os.remove(temporary_path)
                return None
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.remove(temporary_path)
            except OSError:
                pass
            raise
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


def _record_automatic_results(successful_ids, *, all_succeeded, completed_at):
    """Persist per-campaign success without racing another server-state writer."""
    with storage.SERVER_STATE_LOCK:
        state = storage.load_server_state()
        existing = state.get(_CAMPAIGN_COMPLETION_KEY)
        completed = dict(existing) if isinstance(existing, dict) else {}
        for cid in successful_ids:
            completed[cid] = completed_at
        updates = {_CAMPAIGN_COMPLETION_KEY: completed}
        if all_succeeded:
            updates['last_backup_at'] = completed_at
        storage.update_server_state(**updates)


def _automatic_backup_due_ids(now=None):
    """Return only campaigns without a successful automatic backup in 24h.

    The legacy global timestamp is the migration fallback for campaigns that do
    not yet have a per-campaign value. Once a partial run records its successes,
    hourly retries target only failures instead of consuming every healthy
    campaign's seven retained slots in a single day.
    """
    current_time = time.time() if now is None else now
    state = storage.load_server_state()
    global_completed = state.get('last_backup_at') or 0
    raw_completed = state.get(_CAMPAIGN_COMPLETION_KEY)
    completed = raw_completed if isinstance(raw_completed, dict) else {}
    due = []
    for cid in _active_campaign_ids():
        try:
            campaign_completed = float(completed.get(cid, global_completed) or 0)
        except (TypeError, ValueError):
            campaign_completed = 0
        if current_time - campaign_completed >= INTERVAL_SECS:
            due.append(cid)
    return tuple(due)


def run_backup(campaign_ids=None, *, record_completion=True):
    """Snapshot selected active campaigns and optionally advance the daily gate.

    ``campaign_ids=None`` is the scheduler's all-campaign run. HTTP callers pass
    only campaigns the principal may administer and leave ``record_completion``
    false so a partial/manual run cannot delay the next automatic full backup.
    """
    stamp = _stamp()
    n = 0
    all_succeeded = True
    successful_ids = []
    ids = _active_campaign_ids() if campaign_ids is None else tuple(campaign_ids)
    for cid in ids:
        try:
            if not snapshot_campaign(cid, stamp):
                all_succeeded = False
                continue
            n += 1
            prune_campaign(cid)
            successful_ids.append(cid)
        except Exception as e:                  # one bad campaign shouldn't abort the rest
            all_succeeded = False
            print(f"[BACKUP] {cid}: {e}")
    if record_completion:
        _record_automatic_results(
            successful_ids,
            all_succeeded=all_succeeded,
            completed_at=int(time.time()),
        )
    return n


# --- daily scheduler (lazy, gunicorn-safe: starts inside the worker) ----------
_thread_started = False
_thread_lock = threading.Lock()


def _loop():
    while True:
        try:
            due_ids = _automatic_backup_due_ids()
            if due_ids:
                run_backup(due_ids)
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
