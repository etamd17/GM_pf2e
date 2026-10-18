"""Per-campaign storage for D&D 5e: characters, the live encounter, saved
encounter plans. No Flask.

Layout (under the campaign directory, so it moves with export/import/trash)::

    campaigns/<cid>/dnd5e/characters/<pid>.json
    campaigns/<cid>/dnd5e/encounter.json          live tracker state
    campaigns/<cid>/dnd5e/plans/<id>.json         encounter-builder drafts

Concurrency: the app runs one gevent worker, but greenlets interleave at I/O,
so every read-modify-write of a character or the encounter happens under a
per-file lock via ``mutate_pc`` / ``mutate_encounter``. Lock ordering when both
are needed: ENCOUNTER first, then PC (``combat_service`` follows this).

Writes are atomic (``os.replace``). Live-tick writes (HP, slots, conditions)
skip fsync -- see core/storage.atomic_write_json for why.
"""
from __future__ import annotations

import copy
import os
import re
import threading
import uuid

from core import storage
from systems.dnd5e import combat as K

_ID_RE = re.compile(r'^[0-9a-f]{32}$')
_locks: dict = {}
_locks_guard = threading.Lock()
_enc_cache: dict = {}


def _lock(key: str) -> threading.RLock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


def valid_id(pid) -> bool:
    return isinstance(pid, str) and bool(_ID_RE.match(pid))


# --- characters ---------------------------------------------------------------

def pc_dir(cid: str) -> str:
    return storage.dnd5e_pc_dir(cid)


def pc_path(cid: str, pid: str) -> str:
    if not valid_id(pid):
        raise ValueError(f'bad character id {pid!r}')
    return os.path.join(pc_dir(cid), pid + '.json')


def load_pc(cid: str, pid: str):
    if not valid_id(pid):
        return None
    doc = storage.load_json(pc_path(cid, pid))
    if not isinstance(doc, dict) or doc.get('id') != pid:
        return None
    # Location is authoritative (a restored backup may carry another
    # campaign's id inside the file).
    doc['campaign_id'] = cid
    return doc


def list_pcs(cid: str) -> list:
    d = pc_dir(cid)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.json') and valid_id(fn[:-5]):
                doc = load_pc(cid, fn[:-5])
                if doc:
                    out.append(doc)
    out.sort(key=lambda x: (x.get('name') or '').lower())
    return out


def new_pc(cid: str, *, name: str, owner_user_id=None, edition='one') -> dict:
    return {
        'id': uuid.uuid4().hex, 'system': 'dnd5e', 'campaign_id': cid,
        'name': (name or 'New Character').strip()[:80] or 'New Character',
        'owner_user_id': owner_user_id, 'revision': 0,
        'build': {'edition': edition, 'sources': [], 'ability_method': 'standard',
                  'base_scores': {'str': 15, 'dex': 14, 'con': 13, 'int': 12, 'wis': 10, 'cha': 8},
                  'levels': [], 'choices': {}},
        'state': {'inventory': [], 'currency': {'cp': 0, 'sp': 0, 'ep': 0, 'gp': 0, 'pp': 0},
                  'slots_used': {}, 'pact_used': 0, 'resources_used': {}, 'hit_dice_used': {},
                  'notes': {}, 'inspiration': False},
        'details': {},
    }


def save_pc(cid: str, doc: dict, *, fsync: bool = True) -> dict:
    os.makedirs(pc_dir(cid), exist_ok=True)
    doc['system'] = 'dnd5e'
    doc['campaign_id'] = cid
    doc['revision'] = int(doc.get('revision') or 0) + 1
    storage.atomic_write_json(pc_path(cid, doc['id']), doc, indent=1, fsync=fsync)
    return doc


def pc_lock(cid: str, pid: str):
    return _lock(f'pc:{cid}:{pid}')


def mutate_pc(cid: str, pid: str, fn, *, fsync: bool = False):
    """Load -> fn(doc) -> save, atomically w.r.t. other mutators of this PC.
    ``fn`` may return a value (returned to the caller) and may raise to abort
    without writing."""
    with _lock(f'pc:{cid}:{pid}'):
        doc = load_pc(cid, pid)
        if doc is None:
            raise KeyError(pid)
        result = fn(doc)
        save_pc(cid, doc, fsync=fsync)
        return doc, result


def delete_pc(cid: str, pid: str) -> bool:
    with _lock(f'pc:{cid}:{pid}'):
        p = pc_path(cid, pid)
        if os.path.isfile(p):
            os.remove(p)
            return True
        return False


# --- live encounter ------------------------------------------------------------

def _enc_path(cid: str) -> str:
    return os.path.join(storage.dnd5e_dir(cid), 'encounter.json')


def load_encounter(cid: str) -> dict:
    with _lock(f'enc:{cid}'):
        enc = _enc_cache.get(cid)
        if enc is None:
            enc = storage.load_json(_enc_path(cid))
            if not isinstance(enc, dict) or 'combatants' not in enc:
                enc = K.new_encounter()
            _enc_cache[cid] = enc
        return copy.deepcopy(enc)


def mutate_encounter(cid: str, fn):
    """Locked read-modify-write of the live encounter; bumps ``revision``."""
    with _lock(f'enc:{cid}'):
        enc = _enc_cache.get(cid)
        if enc is None:
            enc = load_encounter(cid)
        work = copy.deepcopy(enc)
        result = fn(work)
        work['revision'] = int(work.get('revision') or 0) + 1
        os.makedirs(storage.dnd5e_dir(cid), exist_ok=True)
        storage.atomic_write_json(_enc_path(cid), work, indent=None, fsync=False)
        _enc_cache[cid] = work
        return copy.deepcopy(work), result


def encounter_lock(cid: str):
    return _lock(f'enc:{cid}')


def forget_cache(cid: str | None = None):
    """Drop cached encounters (tests; campaign deletion)."""
    if cid is None:
        _enc_cache.clear()
    else:
        _enc_cache.pop(cid, None)


# --- saved encounter plans (builder) ----------------------------------------

def _plans_dir(cid: str) -> str:
    return os.path.join(storage.dnd5e_dir(cid), 'plans')


def list_plans(cid: str) -> list:
    d = _plans_dir(cid)
    out = []
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.json') and valid_id(fn[:-5]):
                p = storage.load_json(os.path.join(d, fn))
                if isinstance(p, dict):
                    out.append(p)
    out.sort(key=lambda p: (p.get('name') or '').lower())
    return out


def save_plan(cid: str, plan: dict) -> dict:
    pid = plan.get('id') if valid_id(plan.get('id')) else uuid.uuid4().hex
    plan['id'] = pid
    os.makedirs(_plans_dir(cid), exist_ok=True)
    storage.atomic_write_json(os.path.join(_plans_dir(cid), pid + '.json'), plan, indent=1)
    return plan


def delete_plan(cid: str, pid: str) -> bool:
    if not valid_id(pid):
        return False
    p = os.path.join(_plans_dir(cid), pid + '.json')
    if os.path.isfile(p):
        os.remove(p)
        return True
    return False
