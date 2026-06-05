#!/usr/bin/env python3
"""One-time migration: the legacy flat single-campaign layout -> multi-campaign.

Safe by construction: it COPIES the per-campaign data into campaigns/<id>/ and
LEAVES THE ORIGINALS IN PLACE as a backup. Pass --cleanup to remove the
originals only after you've confirmed the app works against the new layout.
Idempotent (no-op if a campaign already exists), supports --dry-run, validates
both ends, and removes the partial copy (originals untouched) on any failure.

Usage:
    DATA_DIR=/data python3 tools/migrate_to_campaigns.py --dry-run
    DATA_DIR=/data python3 tools/migrate_to_campaigns.py --created-by <admin_user_id>
    DATA_DIR=/data python3 tools/migrate_to_campaigns.py --cleanup   # after verifying
"""
import os
import sys
import json
import shutil
import argparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir)))
from core import storage

DATA_DIR = storage.DATA_DIR
BASE_DIR = storage.BASE_DIR

# (label, legacy_abs_path, new_path_func(cid))
DIR_ITEMS = [
    ('party_data',       os.path.join(DATA_DIR, 'party_data'),          storage.party_dir),
    ('saved_encounters', os.path.join(DATA_DIR, 'saved_encounters'),    storage.encounter_dir),
    ('campaign_assets',  os.path.join(DATA_DIR, 'campaign_assets'),     storage.campaign_assets_dir),
    ('journals',         os.path.join(DATA_DIR, 'journals'),            storage.journal_dir),
    ('scrapbooks',       os.path.join(DATA_DIR, 'scrapbooks'),          storage.scrapbook_dir),
    ('uploads/handouts', os.path.join(DATA_DIR, 'uploads', 'handouts'), storage.handouts_dir),
]
FILE_ITEMS = [
    ('loot_ledger.json',        os.path.join(DATA_DIR, 'loot_ledger.json'),        storage.loot_ledger_file),
    ('campaign_stats.json',     os.path.join(DATA_DIR, 'campaign_stats.json'),     storage.campaign_stats_file),
    ('session_highlights.json', os.path.join(DATA_DIR, 'session_highlights.json'), storage.session_highlights_file),
    ('pinned_generators.json',  os.path.join(DATA_DIR, 'pinned_generators.json'),  storage.pinned_generators_file),
    ('calendar.json',           os.path.join(DATA_DIR, 'calendar.json'),           storage.calendar_file),
    # story_threads.json currently lives at BASE_DIR (a known misplacement); fixed here.
    ('story_threads.json',      os.path.join(BASE_DIR, 'story_threads.json'),      storage.story_threads_file),
]
OLD_CAMPAIGN_FILE = os.path.join(DATA_DIR, 'campaign.json')
OLD_CAMPAIGN_AUDIO = os.path.join(DATA_DIR, 'campaign_audio')


def _all_legacy_json():
    paths = []
    if os.path.isfile(OLD_CAMPAIGN_FILE):
        paths.append(OLD_CAMPAIGN_FILE)
    for _, old, _ in FILE_ITEMS:
        if old.endswith('.json') and os.path.isfile(old):
            paths.append(old)
    for _, old, _ in DIR_ITEMS:
        if os.path.isdir(old):
            for root, _d, files in os.walk(old):
                paths += [os.path.join(root, fn) for fn in files if fn.endswith('.json')]
    return paths


def preflight(log):
    bad = []
    for p in _all_legacy_json():
        try:
            with open(p, encoding='utf-8') as f:
                json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            bad.append((p, str(e)))
    return bad


def _build_plan(cid):
    plan = []
    for label, old, newfn in DIR_ITEMS:
        if os.path.isdir(old):
            plan.append(('dir', label, old, newfn(cid)))
    for label, old, newfn in FILE_ITEMS:
        if os.path.isfile(old):
            plan.append(('file', label, old, newfn(cid)))
    audio_skip = None
    if os.environ.get('PF2E_AUDIO_DIR'):
        audio_skip = 'PF2E_AUDIO_DIR env override set'
    elif os.path.islink(OLD_CAMPAIGN_AUDIO):
        audio_skip = 'symlink (dev Foundry folder)'
    elif os.path.isdir(OLD_CAMPAIGN_AUDIO):
        plan.append(('dir', 'campaign_audio', OLD_CAMPAIGN_AUDIO, storage.campaign_audio_dir(cid)))
    return plan, audio_skip


def migrate(created_by='admin', dry_run=False, cleanup=False, log=print):
    existing = storage.list_campaign_ids()
    if existing:
        log(f"[skip] already migrated -- campaign(s) present: {existing}")
        return existing[0]

    bad = preflight(log)
    if bad:
        for p, e in bad:
            log(f"[ABORT] unparseable JSON: {p}: {e}")
        raise SystemExit("pre-flight failed: malformed JSON; nothing migrated")

    old_campaign = storage.load_json(OLD_CAMPAIGN_FILE, default={}) or {}
    name = old_campaign.get('name') or 'Campaign'
    cid = storage.new_id()
    plan, audio_skip = _build_plan(cid)

    old_party = os.path.join(DATA_DIR, 'party_data')
    char_files = [f for f in os.listdir(old_party) if f.endswith('.json')] if os.path.isdir(old_party) else []

    log(f"[plan] campaign {cid}  name={name!r}  system=pf2e")
    for kind, label, _old, _new in plan:
        log(f"   copy {kind:4s} {label}")
    if audio_skip:
        log(f"   skip campaign_audio ({audio_skip})")
    log(f"   wrap {len(char_files)} character file(s); write campaign.json + server_state")
    if dry_run:
        log("[dry-run] no changes written")
        return cid

    storage.ensure_campaign_dirs(cid)
    try:
        for kind, label, old, new in plan:
            if kind == 'dir':
                if os.path.isdir(new):
                    shutil.rmtree(new)          # drop the empty dir ensure_campaign_dirs made
                shutil.copytree(old, new)
            else:
                os.makedirs(os.path.dirname(new), exist_ok=True)
                shutil.copy2(old, new)

        # wrap each copied character file in the campaign-aware envelope
        new_party = storage.party_dir(cid)
        for fn in (os.listdir(new_party) if os.path.isdir(new_party) else []):
            if not fn.endswith('.json'):
                continue
            p = os.path.join(new_party, fn)
            doc = storage.load_json(p)
            if doc is None or storage.is_wrapped(doc):
                continue
            env = storage.wrap_character(storage.new_id(), cid, 'pf2e', doc, owner_user_id=None)
            storage.atomic_write_json(p, env, indent=4)

        # campaign.json = envelope + legacy intro fields (name/tagline/soundscapes/...)
        extra = {k: v for k, v in old_campaign.items()
                 if k not in ('schema_version', 'id', 'system', 'created_by', 'members')}
        doc = storage.new_campaign(cid, name, 'pf2e', created_by, extra=extra)
        doc['members'] = [storage.campaign_member(created_by, 'gm')]
        storage.atomic_write_json(storage.campaign_file(cid), doc)

        storage.set_live_campaign_id(cid)
    except Exception as e:
        log(f"[ERROR] {e} -- removing partial campaign dir; originals untouched")
        shutil.rmtree(storage.campaign_dir(cid), ignore_errors=True)
        storage.set_live_campaign_id(None)
        raise

    errs = _postflight(cid, char_files)
    if errs:
        for e in errs:
            log(f"[POSTFLIGHT FAIL] {e}")
        shutil.rmtree(storage.campaign_dir(cid), ignore_errors=True)
        storage.set_live_campaign_id(None)
        raise SystemExit("post-flight validation failed; rolled back (originals intact)")

    log(f"[ok] migrated -> campaigns/{cid}  (originals preserved as backup)")
    if cleanup:
        _cleanup_originals(plan, log)
    return cid


def _postflight(cid, char_files):
    errs = []
    c = storage.load_json(storage.campaign_file(cid))
    if not c or c.get('id') != cid or c.get('system') != 'pf2e':
        errs.append('campaign.json missing/invalid')
    np = storage.party_dir(cid)
    new_chars = [f for f in os.listdir(np) if f.endswith('.json')] if os.path.isdir(np) else []
    if len(new_chars) != len(char_files):
        errs.append(f'character count mismatch: {len(new_chars)} != {len(char_files)}')
    for fn in new_chars:
        if not storage.is_wrapped(storage.load_json(os.path.join(np, fn)) or {}):
            errs.append(f'character not wrapped: {fn}')
    if storage.get_live_campaign_id() != cid:
        errs.append('server_state live_campaign_id not set')
    return errs


def _cleanup_originals(plan, log):
    for kind, label, old, _new in plan:
        try:
            if kind == 'dir' and os.path.isdir(old):
                shutil.rmtree(old)
            elif kind == 'file' and os.path.isfile(old):
                os.remove(old)
            log(f"   removed original {kind} {label}")
        except OSError as e:
            log(f"   [warn] could not remove {old}: {e}")
    if os.path.isfile(OLD_CAMPAIGN_FILE):
        try:
            os.remove(OLD_CAMPAIGN_FILE)
        except OSError:
            pass


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--created-by', default='admin', help='admin user id to record as campaign GM')
    ap.add_argument('--cleanup', action='store_true', help='remove originals after a verified migration')
    args = ap.parse_args()
    print(f"DATA_DIR={DATA_DIR}")
    result = migrate(created_by=args.created_by, dry_run=args.dry_run, cleanup=args.cleanup)
    print(f"campaign id: {result}")
