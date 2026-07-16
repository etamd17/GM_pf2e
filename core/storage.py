"""
core/storage.py -- campaign-scoped path resolution + atomic JSON I/O.

Multi-campaign data layout (under DATA_DIR -- the Railway volume in prod):

    DATA_DIR/
      users.json                       # accounts (managed by core.auth)
      server_state.json                # {live_campaign_id}
      campaigns/<campaign_id>/
        campaign.json                  # name, system, members, session#, ...
        party_data/*.json              # character envelopes (name-based files)
        saved_encounters/*.json        # + _autosave.json
        campaign_stats.json  loot_ledger.json  story_threads.json
        calendar.json  pinned_generators.json
        journals/  campaign_assets/  campaign_audio/
        uploads/handouts/
      systems/<system>/                # shipped read-only content (pf2e, cosmere)

Shared/system content is NOT per-campaign and keeps its current location:
    monster_data/ (bestiary), pf2e_database.db, compendium_data/, static/.

This module is intentionally standalone (it does not import app) so app.py can
import it without a circular dependency. It reads DATA_DIR/BASE_DIR from the
environment exactly the way app.py does.
"""
import os
import re
import json
import uuid
import shutil
import tempfile

# Mirror app.py's roots. BASE_DIR is the repo (this file lives in core/, so go up
# one level); DATA_DIR is the Railway volume in prod, the repo locally.
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)

CAMPAIGNS_DIR = os.path.join(DATA_DIR, 'campaigns')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
SERVER_STATE_FILE = os.path.join(DATA_DIR, 'server_state.json')
SYSTEMS_DIR = os.path.join(DATA_DIR, 'systems')

SCHEMA_VERSION = 1
SUPPORTED_SYSTEMS = ('pf2e', 'cosmere')

# IDs are uuid4 hex (dash-less, 32 chars). Validated on every path build so a
# campaign_id can never escape CAMPAIGNS_DIR (no traversal from user input).
_ID_RE = re.compile(r'^[0-9a-f]{32}$')


def new_id():
    """A fresh dash-less uuid4 hex id (campaign id, character id, etc.)."""
    return uuid.uuid4().hex


def _check_id(value, label='id'):
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"unsafe {label}: {value!r}")
    return value


def slugify(name, fallback='campaign'):
    """Human-readable slug for display/URLs (not a storage key)."""
    s = re.sub(r'[^a-z0-9]+', '-', (name or '').strip().lower()).strip('-')
    return s[:60] or fallback


# --------------------------------------------------------------------------
# Per-campaign paths (everything keyed by a validated campaign_id)
# --------------------------------------------------------------------------
def campaign_dir(cid):
    return os.path.join(CAMPAIGNS_DIR, _check_id(cid, 'campaign_id'))


def campaign_file(cid):            return os.path.join(campaign_dir(cid), 'campaign.json')
def party_dir(cid):                return os.path.join(campaign_dir(cid), 'party_data')
def encounter_dir(cid):            return os.path.join(campaign_dir(cid), 'saved_encounters')
def campaign_assets_dir(cid):      return os.path.join(campaign_dir(cid), 'campaign_assets')
def handouts_dir(cid):             return os.path.join(campaign_dir(cid), 'uploads', 'handouts')
def campaign_audio_dir(cid):       return os.path.join(campaign_dir(cid), 'campaign_audio')
def journal_dir(cid):              return os.path.join(campaign_dir(cid), 'journals')
def loot_ledger_file(cid):         return os.path.join(campaign_dir(cid), 'loot_ledger.json')
def campaign_stats_file(cid):      return os.path.join(campaign_dir(cid), 'campaign_stats.json')
def story_threads_file(cid):       return os.path.join(campaign_dir(cid), 'story_threads.json')
def pinned_generators_file(cid):   return os.path.join(campaign_dir(cid), 'pinned_generators.json')
def calendar_file(cid):            return os.path.join(campaign_dir(cid), 'calendar.json')
def handouts_file(cid):            return os.path.join(campaign_dir(cid), 'handouts.json')
def cosmere_adversaries_file(cid): return os.path.join(campaign_dir(cid), 'cosmere_adversaries.json')
def cosmere_pc_dir(cid):           return os.path.join(campaign_dir(cid), 'cosmere_pcs')
def homebrew_file(cid):            return os.path.join(campaign_dir(cid), 'homebrew.json')
def chronicle_dir(cid):            return os.path.join(campaign_dir(cid), 'chronicle')


def delete_campaign_dir(cid):
    """Permanently remove a campaign's entire data directory. `cid` is validated
    (no traversal) by campaign_dir(); a no-op if the directory is already gone."""
    d = campaign_dir(cid)
    if os.path.isdir(d):
        shutil.rmtree(d)


# --------------------------------------------------------------------------
# Soft delete: campaigns are moved to a trash dir (restorable) instead of being
# rmtree'd outright, so a misclick can't destroy a multi-month campaign.
# --------------------------------------------------------------------------
CAMPAIGNS_TRASH_DIR = os.path.join(DATA_DIR, 'campaigns_trash')


def campaign_trash_dir(cid):
    return os.path.join(CAMPAIGNS_TRASH_DIR, _check_id(cid, 'campaign_id'))


def trashed_campaign_file(cid):
    return os.path.join(campaign_trash_dir(cid), 'campaign.json')


def trash_campaign_dir(cid):
    """Move a campaign's data dir into the trash. Returns True if moved."""
    src = campaign_dir(cid)
    if not os.path.isdir(src):
        return False
    os.makedirs(CAMPAIGNS_TRASH_DIR, exist_ok=True)
    dst = campaign_trash_dir(cid)
    if os.path.isdir(dst):
        shutil.rmtree(dst)            # replace an older trashed copy of the same id
    shutil.move(src, dst)
    return True


def restore_campaign_dir(cid):
    """Move a campaign back from the trash. Returns True if restored (False if the
    trashed copy is missing or a live campaign already occupies that id)."""
    src = campaign_trash_dir(cid)
    dst = campaign_dir(cid)
    if not os.path.isdir(src) or os.path.isdir(dst):
        return False
    shutil.move(src, dst)
    return True


def purge_campaign_dir(cid):
    """Permanently remove a TRASHED campaign (no-op if it isn't in the trash)."""
    d = campaign_trash_dir(cid)
    if os.path.isdir(d):
        shutil.rmtree(d)


def list_trashed_campaign_ids():
    """Ids of campaigns currently in the trash."""
    if not os.path.isdir(CAMPAIGNS_TRASH_DIR):
        return []
    return [n for n in os.listdir(CAMPAIGNS_TRASH_DIR)
            if _ID_RE.match(n) and os.path.isfile(os.path.join(CAMPAIGNS_TRASH_DIR, n, 'campaign.json'))]


def trashed_dir_mtime(cid):
    """When the campaign was trashed (the trashed dir's mtime), or 0."""
    try:
        return os.path.getmtime(campaign_trash_dir(cid))
    except OSError:
        return 0


# The per-campaign subdirectories created for every campaign.
CAMPAIGN_SUBDIRS = (
    'party_data',
    'saved_encounters',
    'campaign_assets',
    os.path.join('uploads', 'handouts'),
    'campaign_audio',
    'journals',
    'cosmere_pcs',
)


def ensure_campaign_dirs(cid):
    """Create the campaign folder and all its subdirectories (idempotent)."""
    for sub in CAMPAIGN_SUBDIRS:
        os.makedirs(os.path.join(campaign_dir(cid), sub), exist_ok=True)


def list_campaign_ids():
    """Every campaign id currently on disk (dirs under campaigns/ named like an id)."""
    if not os.path.isdir(CAMPAIGNS_DIR):
        return []
    out = []
    for name in os.listdir(CAMPAIGNS_DIR):
        if _ID_RE.match(name) and os.path.isfile(os.path.join(CAMPAIGNS_DIR, name, 'campaign.json')):
            out.append(name)
    return out


# --------------------------------------------------------------------------
# Shared / system content (NOT per-campaign -- current locations preserved)
# --------------------------------------------------------------------------
def system_content_dir(system):
    return os.path.join(SYSTEMS_DIR, system)


# --------------------------------------------------------------------------
# Atomic JSON I/O (standalone; mirrors app._atomic_write_json semantics:
# temp file in the same dir -> fsync -> os.replace, so a crash can never leave
# a half-written file).
# --------------------------------------------------------------------------
def atomic_write_json(path, obj, indent=2):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


# --------------------------------------------------------------------------
# Schema constructors (the canonical shapes; see module docstring)
# --------------------------------------------------------------------------
def new_campaign(cid, name, system, created_by, *, slug=None, created_at=None, extra=None):
    """Build a campaign.json document. `extra` merges legacy intro fields
    (tagline, soundscapes, session_number, crest_image, ...) without clobbering
    the envelope keys."""
    if system not in SUPPORTED_SYSTEMS:
        raise ValueError(f"unknown system: {system!r}")
    doc = {
        'schema_version': SCHEMA_VERSION,
        'id': _check_id(cid, 'campaign_id'),
        'slug': slug or slugify(name),
        'name': name,
        'system': system,
        'created_by': created_by,
        'created_at': created_at,
        'session_number': 1,
        'members': [],            # [{user_id, role: 'gm'|'player', character_id?}]
        'system_config': {},
    }
    if extra:
        for k, v in extra.items():
            if k not in ('schema_version', 'id', 'system', 'created_by'):
                doc[k] = v
    return doc


def campaign_member(user_id, role, character_id=None):
    assert role in ('gm', 'player'), role
    m = {'user_id': user_id, 'role': role}
    if character_id is not None:
        m['character_id'] = character_id
    return m


def wrap_character(chid, cid, system, system_data, *, owner_user_id=None, play_state=None):
    """FLAT-ADDITIVE envelope: merge campaign/ownership metadata INTO the native
    character dict (which keeps its top-level `build`, `success`, etc.). This way
    every existing reader/writer of a party_data file -- the Character loader, the
    combat-state persistence, level-up, conditions -- keeps working unchanged,
    while new code reads the top-level envelope keys (id, owner_user_id, system).
    """
    doc = dict(system_data) if isinstance(system_data, dict) else {'data': system_data}
    doc.update({
        'schema_version': SCHEMA_VERSION,
        'id': chid,
        'campaign_id': _check_id(cid, 'campaign_id'),
        'owner_user_id': owner_user_id,
        'system': system,
    })
    return doc


def is_wrapped(doc):
    """True if a character doc already carries the envelope (migration idempotency)."""
    return isinstance(doc, dict) and doc.get('schema_version') == SCHEMA_VERSION and 'owner_user_id' in doc


def ensure_character_envelope(doc, cid, system='pf2e', *, existing=None, new_id=None):
    """Ensure `doc` carries the FLAT-ADDITIVE campaign envelope so a PF2e PC
    created or re-imported in-app flows through invite -> claim -> My-Characters.

    Identity precedence: if `existing` (the on-disk doc being overwritten) is
    already wrapped, REUSE its id / owner_user_id / campaign_id -- so re-importing
    a CLAIMED character keeps it claimed. Else if `doc` itself is already wrapped,
    keep that identity. Otherwise stamp a fresh, UNCLAIMED envelope
    (owner_user_id=None) with a new id under campaign `cid`. The new `doc`'s
    native content (build/success/...) always wins.
    """
    src = existing if is_wrapped(existing or {}) else (doc if is_wrapped(doc) else None)
    if src is not None:
        chid = src['id']
        owner = src.get('owner_user_id')
        ecid = src.get('campaign_id') or cid
    else:
        chid = new_id or uuid.uuid4().hex
        owner = None
        ecid = cid
    return wrap_character(chid, ecid, system, doc, owner_user_id=owner)


# --------------------------------------------------------------------------
# Server state (the single live-campaign slot; survives restarts/redeploys)
# --------------------------------------------------------------------------
def load_server_state():
    return load_json(SERVER_STATE_FILE, default={}) or {}


def get_live_campaign_id():
    return load_server_state().get('live_campaign_id')


def set_live_campaign_id(cid):
    state = load_server_state()
    state['live_campaign_id'] = _check_id(cid, 'campaign_id') if cid else None
    atomic_write_json(SERVER_STATE_FILE, state)
