from flask import Flask, render_template, request, redirect, url_for, send_file, send_from_directory, jsonify, session, Response
import sqlite3
import json
import math
import os
import glob
import uuid
import copy
import re
import urllib.parse
import markdown
import random
import time
import queue
import threading
import tempfile
from functools import wraps
from pathlib import Path
from werkzeug.exceptions import HTTPException

from class_matrix import ABP_TABLE, get_abp_bonus, CLASS_MATRIX, SUBCLASS_MATRIX, SPELL_SLOT_TABLES, PASSIVE_FEATURES, CLASS_FEATURES
from class_matrix import CLASS_PROGRESSION, SUBCLASS_PROGRESSION, get_class_proficiency_at_level, get_new_bumps_at_level, validate_skill_rank, ANCESTRY_SPEEDS, ANCESTRY_SENSES, ANCESTRY_SIZES, ANCESTRY_FEATURES, get_required_slots_at_level
from class_matrix import CLASS_AWARDED_FEATS, SUBCLASS_AWARDED_FEATS, HERITAGE_AWARDED_FEATS
from class_matrix import MONK_PATH_CONFIG
from class_matrix import SUBCLASS_DESCRIPTIONS
from class_matrix import SPELL_ACTIONS, get_action_cost, foundry_action_cost
from class_matrix import SKILL_FEAT_PREREQS, check_feat_prereqs, RANK_VALUES
from class_matrix import CLASS_LEVEL_FEATURES
from pf2e_generator import RobustPF2eGenerator

# ── Local .env loader (dependency-free) ──────────────────────────────────
# Loads KEY=VALUE lines from a .env beside this file into os.environ so the
# app picks up secrets (e.g. ANTHROPIC_API_KEY) no matter how it's launched
# — start.command, `python3 app.py`, gunicorn, or a dev preview. Real
# environment variables (e.g. Railway's dashboard vars) always win, so this
# never clobbers production config. python-dotenv is NOT required.
def _load_dotenv_file():
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
        if not os.path.isfile(env_path):
            return
        with open(env_path, 'r', encoding='utf-8') as fp:
            for raw in fp:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                # Strip surrounding quotes if the user added them.
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass

_load_dotenv_file()

app = Flask(__name__)
# Behind Railway's TLS-terminating proxy the app sees the proxy->app hop as plain
# HTTP. Trust one hop of X-Forwarded-Proto/Host/For so request.scheme/host reflect
# the real HTTPS origin the browser used -- otherwise request.host_url is http://...
# and the same-origin CSRF check, secure cookies, and external URLs all break.
from werkzeug.middleware.proxy_fix import ProxyFix as _ProxyFix
app.wsgi_app = _ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
def _stable_secret_key():
    """A secret key that SURVIVES restarts/deploys. A random key per boot (the old
    behavior) re-signed the session cookie on every Railway restart, silently
    invalidating every logged-in session -- the whole table got logged out (and
    dropped back to their last-remembered campaign) the moment a deploy landed
    mid-game. Prefer an explicit SECRET_KEY env var; otherwise persist a generated
    key on the data volume so it's stable across restarts."""
    env = os.environ.get('SECRET_KEY')
    if env:
        return env
    import secrets as _secrets
    _dd = os.environ.get('DATA_DIR') or os.path.dirname(os.path.abspath(__file__))
    keyfile = os.path.join(_dd, '.secret_key')
    try:
        if os.path.isfile(keyfile):
            with open(keyfile, 'r', encoding='utf-8') as f:
                k = (f.read() or '').strip()
            if k:
                return k
        k = _secrets.token_hex(32)
        os.makedirs(_dd, exist_ok=True)
        with open(keyfile, 'w', encoding='utf-8') as f:
            f.write(k)
        try:
            os.chmod(keyfile, 0o600)
        except OSError:
            pass
        return k
    except OSError:
        # Read-only FS (shouldn't happen on Railway's writable volume): fall back
        # to a per-boot random key -- no worse than the old behavior.
        return 'pf2e-gm-dashboard-' + str(uuid.uuid4())


app.secret_key = _stable_secret_key()
from datetime import timedelta as _timedelta
app.permanent_session_lifetime = _timedelta(days=60)  # "remember me" longevity for player/GM sessions
# Reject oversized uploads at the WSGI layer so a multi-GB POST can't OOM
# the dyno before our per-endpoint size checks run. Bumped high enough for
# a fat tarball push (vault_data) but well under Railway's worker memory.
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024


# Cache-bust static assets: append each file's mtime as `?v=` to every
# url_for('static', ...) link. Browsers cache CSS/JS aggressively, so without
# this a deploy that changes e.g. system.css can leave users staring at a stale
# stylesheet until a manual hard refresh. The mtime changes whenever the file is
# rewritten (including on each Railway deploy), so the version updates itself and
# we never have to hand-bump a number. Never let this break rendering.
@app.url_defaults
def _static_cache_bust(endpoint, values):
    if endpoint != 'static' or not values or not values.get('filename'):
        return
    try:
        fpath = os.path.join(app.static_folder, values['filename'])
        values['v'] = int(os.stat(fpath).st_mtime)
    except Exception:
        pass


def _atomic_write_json(path, obj, indent=2, fsync=True):
    """Write JSON to a temp file then atomically os.replace() it into place.

    A plain open(path, 'w') truncates immediately, so if the process is killed
    mid-write (Railway SIGKILLs the worker on every redeploy) the file can be
    left empty or half-written -- which is how a character sheet gets corrupted
    and you only find out next session. os.replace() is atomic on POSIX, so a
    reader (or a crash) never sees a partial file. The temp file shares the
    target's directory so the replace stays on one filesystem.

    fsync=True (default) forces the write to disk before the replace -- correct
    for durable saves (a built character you can't afford to lose). Pass
    fsync=False for high-frequency, low-stakes writes (live HP/condition ticks):
    os.fsync is a BLOCKING syscall that gevent can't yield around, so on the
    single gevent worker every fsync stalls *all* greenlets (and thus every
    player's SSE) for the disk-flush duration. os.replace stays atomic without
    it; we only trade durability against a hard power-loss, which for a
    re-derivable HP value is the right trade.
    """
    directory = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=directory, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(obj, f, indent=indent)
            f.flush()
            if fsync:
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --- GM ACCESS CONTROL ---
GM_PASSWORD = os.environ.get('GM_PASSWORD', '')  # Set in Railway env vars

# Session-cookie hardening. HttpOnly always; SameSite=Lax so the GM can
# still follow an emailed link and stay logged in, but cross-site POSTs
# don't carry the cookie. Secure flag is enabled in production (= when a
# GM_PASSWORD is configured, which is the Railway deploy signal); in
# local dev over http://localhost we leave it off so the session cookie
# can still round-trip.
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if GM_PASSWORD:
    app.config['SESSION_COOKIE_SECURE'] = True

# Account-based auth (multi-campaign). When any user account exists we authorize
# via the logged-in user's per-campaign role; with no accounts yet (tests /
# un-bootstrapped) we fall back to the legacy GM_PASSWORD behavior below.
from core import auth as _auth, campaigns as _campaigns, backups as _backups


def _account_mode():
    return _auth.any_users_exist()


def _active_campaign_id():
    """The campaign id THIS request should treat as active -- for the system,
    nav/chrome, landing redirects, and per-user PC authorization.

    Account mode: the user's own selection (this session), or on a fresh session
    the campaign they last activated -- ALWAYS scoped to a campaign they actually
    belong to, and NEVER the server-wide live slot. That decoupling is the fix
    for one table's live session (e.g. a Cosmere game holding the global slot)
    flipping another user -- or the same user on a new login -- onto the wrong
    system. Legacy mode (no accounts): the single global live slot, unchanged.
    """
    if _account_mode():
        u = _auth.current_user()
        if not u:
            return None
        # "Stop active campaign" parks the session with NO active table -- the
        # picker/lobby renders system-neutral and nothing bleeds from the last
        # table. We must NOT fall back to last_campaign_id here, or the stop
        # wouldn't stick. Cleared the moment any campaign is activated.
        if session.get('campaign_stopped'):
            return None
        cid = session.get('active_campaign_id') or u.get('last_campaign_id')
        if not cid:
            return None
        camp = _campaigns.get_campaign(cid)
        if camp and (u.get('is_admin') or _campaigns.user_role(camp, u['id'])):
            return cid
        return None   # stale / not-a-member -> don't adopt someone else's campaign
    return session.get('active_campaign_id') or _campaigns.get_live_campaign_id()


def _active_campaign_doc():
    return _campaigns.get_campaign(_active_campaign_id())


def _safe_then(target):
    """Return `target` only if it is a safe SAME-SITE path (single leading slash,
    no scheme, no protocol-relative //, no control chars) so the activate redirect
    can never become an open redirect. Otherwise None."""
    if not isinstance(target, str):
        return None
    t = target.strip()
    if not t or not t.startswith('/') or t.startswith('//') or t.startswith('/\\'):
        return None
    if any(c in t for c in '\r\n\t '):
        return None
    return t[:512]


def _pc_sheet_url(system, name, pid):
    """The deep-link to a character's own sheet, per system."""
    from urllib.parse import quote
    if (system or 'pf2e') == 'cosmere':
        return '/cosmere/pc/' + quote(str(pid or ''), safe='')
    return '/player/sheet/' + quote(str(name or ''), safe='')


def _my_pc_names(user_id):
    """PF2e PC names in the active campaign owned by user_id (account mode)."""
    cid = _active_campaign_id()
    if not cid:
        return []
    pdir = _storage.party_dir(cid)
    names = []
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if not fn.endswith('.json'):
                continue
            doc = _storage.load_json(os.path.join(pdir, fn))
            if _storage.is_wrapped(doc) and doc.get('owner_user_id') == user_id:
                names.append(_campaigns._character_name(doc))
    return names


def _set_active_campaign(cid):
    """Select a campaign for the current user: stash it in the session AND remember
    it on the account, so a later fresh login resumes the same table instead of
    inheriting the server-wide live slot's system. The single place that records
    a per-user campaign choice."""
    session['active_campaign_id'] = cid
    session.pop('campaign_stopped', None)   # picking a table un-parks the session
    try:
        u = _auth.current_user()
        if u:
            _auth.set_last_campaign(u['id'], cid)
    except Exception:
        pass


def _active_system():
    """The TTRPG system of the active campaign ('pf2e' | 'cosmere').

    Single source of truth for system-aware chrome: the nav, the home lobby, and
    any surface that must render one system's rules/stats without bleed. Defaults
    to 'pf2e' (legacy single-system installs + when no campaign is active)."""
    try:
        camp = _active_campaign_doc()
        system = (camp or {}).get('system')
        if system in _storage.SUPPORTED_SYSTEMS:
            return system
    except Exception:
        pass
    return 'pf2e'


def _cosmere_player_char_name():
    """The current player's own Cosmere character name in the active campaign --
    for the mobile nav's turn ping (matched against encounter_update.active_name).
    Returns '' for the GM, non-Cosmere campaigns, or an unclaimed player."""
    try:
        if not _account_mode() or _is_gm() or _active_system() != 'cosmere':
            return ''
        u = _auth.current_user()
        if not u:
            return ''
        for d in _list_cosmere_pcs():
            if d.get('owner_user_id') == u.get('id'):
                return d.get('name') or ''
    except Exception:
        pass
    return ''


def _active_system_ui():
    """The active system's UI descriptor -- its GM/player hub routes, nav brand,
    and nav link sets. Single source the redirects + nav read from, so there's no
    per-system branching in the app and a new system can't skip either hub."""
    try:
        return systems.get(_active_system()).ui
    except Exception:
        return systems.get(systems.DEFAULT_SYSTEM).ui


def _system_home(gm: bool) -> str:
    """The active system's GM-side or player-side landing route."""
    ui = _active_system_ui()
    return ui.gm_home if gm else ui.player_home


@app.template_global()
def qr_svg(data, scale=4):
    """Inline SVG QR code for a join link, so players can SCAN it off the GM's
    screen instead of hand-typing a long URL on a phone. Inline SVG = no network
    and no data-URI, so it renders offline at the table. Returns '' if segno
    isn't installed (the page still shows the link + copy button)."""
    try:
        import segno
        return segno.make(str(data), error='m').svg_inline(scale=scale, border=2,
                                                            dark='#12100b', light='#f5efe0')
    except Exception:
        return ''


def gm_required(f):
    """Decorator: GM of the active campaign (account mode) or the GM password
    (legacy). _is_gm() encodes both modes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _is_gm():
            return f(*args, **kwargs)
        if request.path.startswith('/api/'):
            return jsonify({"error": "GM access required"}), 403
        return redirect('/login' if _account_mode() else '/gm/login')
    return decorated

def _is_gm():
    """True if the caller is effectively the GM of the active campaign.

    Account mode: the logged-in user is a GM member of the active campaign (or a
    site admin). Legacy mode (no accounts): GM_PASSWORD unset = open local dev,
    otherwise the gm_authenticated session flag."""
    if _account_mode():
        u = _auth.current_user()
        if not u:
            return False
        return bool(u.get('is_admin')) or _campaigns.is_gm(_active_campaign_doc(), u['id'])
    return (not GM_PASSWORD) or session.get('gm_authenticated', False)

def require_pc_self_or_gm(f):
    """Decorator: only the GM or the character's owner may mutate a PC's sheet.
    Account mode checks owner_user_id on the character; legacy uses the
    player_name session. Open in legacy local dev (no GM_PASSWORD)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _is_gm():
            return f(*args, **kwargs)
        pc_name = kwargs.get('pc_name')
        if _account_mode():
            u = _auth.current_user()
            if u and pc_name and _user_owns_pc(u['id'], pc_name):
                return f(*args, **kwargs)
            return jsonify({"error": "forbidden — not your character"}), 403
        if not GM_PASSWORD:
            return f(*args, **kwargs)
        if pc_name and session.get('player_name') == pc_name:
            return f(*args, **kwargs)
        return jsonify({"error": "forbidden — not your character"}), 403
    return decorated


def _user_owns_pc(user_id, pc_name):
    """True if user_id owns the character shown as pc_name in the active campaign."""
    cid = _active_campaign_id()
    if not cid:
        return False
    pdir = _storage.party_dir(cid)
    if not os.path.isdir(pdir):
        return False
    for fn in os.listdir(pdir):
        if not fn.endswith('.json'):
            continue
        doc = _storage.load_json(os.path.join(pdir, fn))
        if _storage.is_wrapped(doc) and _campaigns._character_name(doc) == pc_name:
            return doc.get('owner_user_id') == user_id
    return False

# GM-only API prefixes — these are encounter/tracker/vault APIs that players shouldn't access
GM_API_PREFIXES = (
    '/api/add_combatant', '/api/add_party', '/api/remove_combatant', '/api/clear_encounter',
    '/api/adjust_hp/',  # Encounter tracker HP (not adjust_party_hp which is player-facing)
    '/api/multi_save_damage',  # GM AOE basic-save resolver (compute+log only)
    '/api/toggle_condition/', '/api/set_persistent_damage/', '/api/toggle_elite_weak/',
    '/api/update_initiative/', '/api/roll_npc_initiative', '/api/sort_initiative',
    '/api/cycle_turn/', '/api/delay_turn/', '/api/reenter_initiative/',
    '/api/save_encounter', '/api/load_encounter', '/api/delete_encounter', '/api/encounter_notes',
    '/api/save_stage',
    '/api/roll_all_initiative', '/api/reorder_initiative',
    # GM-only loot/check dispatch + party-wide daily prep. Players see
    # the resulting banners and loot deliveries but can't fire them.
    '/api/request_check', '/api/send_loot', '/api/daily_prep_all',
    # PC + monster authoring is GM-only — keeps a curious player from
    # importing a sheet that overwrites a sibling's JSON file, or
    # injecting a custom monster mid-fight.
    '/api/import_pathbuilder', '/api/save_new_character',
    '/api/import_monster', '/api/create_monster',
    '/api/monster_search', '/api/stage_encounter', '/api/party_stats',
    '/api/monster_statblock/', '/api/combatant_stats/',
    '/api/toggle_combatant_visibility/',
    # Hazard actions (Trigger/Disable/Reset) are GM-only. Players see
    # the resulting combat-log entry but can't fire the routine.
    '/api/hazard/',
    # /api/tracker_state returns raw combatant data (AC, HP, strikes, saves,
    # and visible_to_players). It's only consumed by templates/tracker.html
    # which is GM-only, so gate it here. /api/player_state is the sanitized
    # counterpart for player views.
    '/api/tracker_state',
    # /api/gm_secret_log holds GM-only secret rolls (stealth/perception/etc.)
    # — never fetched by player templates. Keep it GM-only. /api/gm_secret_roll
    # mints new ones; same constraint.
    '/api/gm_secret_log', '/api/gm_secret_roll',
    # /api/combat_log/clear and /api/clear_log are GM-only (wipe the combat log);
    # GET /api/combat_log itself is player-facing and stays open.
    '/api/combat_log/clear', '/api/clear_log',
    # Character library admin (delete a PC, upload a handout to the table).
    '/api/delete_character/', '/api/handout_upload',
    '/api/generate/',
    '/api/loot_ledger',
    '/api/session_timer/',
    '/api/set_combatant_tactics/',
)

@app.before_request
def check_gm_access():
    """Block GM-only API routes for non-GM callers (account or legacy mode)."""
    path = request.path
    if any(path.startswith(prefix) for prefix in GM_API_PREFIXES) and not _is_gm():
        return jsonify({"error": "GM access required"}), 403


# CSRF defense for the account/admin state-changing routes: reject cross-origin
# POSTs (with SameSite=Lax cookies as the backstop). A classic CSRF auto-submit
# from another site carries that site's Origin and is blocked; same-origin form
# posts carry a matching Origin/Referer. The game's existing fetch mutations are
# out of scope here -- they rely on SameSite + per-campaign authorization.
_CSRF_GUARD_PREFIXES = ('/setup', '/login', '/register', '/join', '/me/password',
                        '/campaigns/', '/campaign/', '/admin/')


@app.before_request
def _csrf_guard():
    if request.method != 'POST':
        return
    if not any(request.path.startswith(p) for p in _CSRF_GUARD_PREFIXES):
        return
    origin = request.headers.get('Origin') or request.headers.get('Referer') or ''
    if not origin:
        return  # no Origin/Referer -> rely on the SameSite=Lax cookie backstop
    # Compare HOSTS, not full URLs: scheme can legitimately differ behind a
    # TLS-terminating proxy (browser https, forwarded header may vary), but a
    # genuine cross-site CSRF post carries a different host.
    from urllib.parse import urlparse as _urlparse
    origin_host = _urlparse(origin).netloc
    if origin_host and origin_host != request.host:
        return ('Cross-origin request blocked.', 400)


# ═════════════════════════════════════════════════════════════════════
#  GZIP COMPRESSION
#  The tracker page is ~1.8 MB uncompressed (mostly the inlined monster
#  dropdown + script blocks) and player_sheet is ~1.2 MB. On a LAN for
#  4 concurrent players those payloads dominate page-load latency. Gzip
#  knocks them down by 79–89 %. We implement it inline rather than
#  adding flask-compress so there's no new dependency to install
#  mid-session.
#
#  Skip cases:
#    • SSE streams (/api/events) — must flush per-event, not buffer
#    • already-compressed bodies (images, pre-gzipped assets)
#    • small bodies (< 500 B) — gzip overhead isn't worth it
#    • direct-passthrough (Response with X-No-Compress marker)
# ═════════════════════════════════════════════════════════════════════
import gzip as _gzip
from io import BytesIO as _BytesIO

_GZIP_MIN_BYTES = 500
_GZIP_MIME_PREFIXES = (
    'text/', 'application/json', 'application/javascript',
    'application/xml', 'image/svg+xml',
)

@app.after_request
def _gzip_response(response):
    # DISABLED 2026-06-18. App-level Content-Encoding broke responses in
    # production: behind Railway's edge proxy (which already compresses), the
    # manual gzip + Content-Length double-encoded / mis-framed the body, so the
    # BROWSER could not decode JSON responses -- every large tracker mutation
    # (add/remove party or combatant returns the full encounter state) failed
    # client-side with "request failed" even though the server had already
    # applied the change (a manual refresh then showed it). It worked locally
    # (incl. a real browser against the dev server) because there is no second
    # compression layer there. Compression is handled at the edge; doing it in
    # the app too is redundant AND harmful. Left as a no-op pass-through.
    return response


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)  # Railway volume mount or local
MONSTER_DIR = os.path.join(DATA_DIR, 'monster_data')         # shared bestiary (system content, not per-campaign)
DB_PATH = os.path.join(BASE_DIR, 'pf2e_database.db')         # ships with repo, read-only
COMPENDIUM_DATA_DIR = os.path.join(BASE_DIR, 'compendium_data')

# --- Active-campaign path binding -------------------------------------------
# The app operates on ONE active campaign at a time (the live slot). Every
# per-campaign data path is (re)bound to that campaign by _bind_campaign_paths();
# load_campaign() re-binds + reloads when the active campaign changes. When no
# campaign has been migrated yet (server_state has no live id) we fall back to
# the legacy flat layout so the app keeps working unchanged pre-migration.
from core import storage as _storage
import systems  # system registry (pf2e, later cosmere); actor dispatch by envelope `system`

_AUDIO_OVERRIDE = os.environ.get('PF2E_AUDIO_DIR')  # dev: external Foundry audio folder
ACTIVE_CAMPAIGN_ID = None
PARTY_DIR = ENCOUNTER_DIR = CAMPAIGN_ASSETS_DIR = HANDOUTS_DIR = CAMPAIGN_AUDIO_DIR = None
CAMPAIGN_FILE = LOOT_LEDGER_FILE = CAMPAIGN_STATS_FILE = JOURNAL_DIR = None
SCRAPBOOK_FILE = SCRAPBOOK_DIR = PINNED_GENERATORS_FILE = CALENDAR_FILE = STORY_THREADS_FILE = None
HANDOUTS_FILE = COSMERE_ADVERSARIES_FILE = None


def _bind_campaign_paths(cid):
    """Point every per-campaign path global at campaign `cid`, or at the legacy
    flat layout when cid is None. Single source of truth for campaign data
    locations -- existing code keeps using PARTY_DIR/ENCOUNTER_DIR/etc unchanged."""
    global ACTIVE_CAMPAIGN_ID, PARTY_DIR, ENCOUNTER_DIR, CAMPAIGN_ASSETS_DIR, HANDOUTS_DIR
    global CAMPAIGN_AUDIO_DIR, CAMPAIGN_FILE, LOOT_LEDGER_FILE, CAMPAIGN_STATS_FILE, JOURNAL_DIR
    global SCRAPBOOK_FILE, SCRAPBOOK_DIR, PINNED_GENERATORS_FILE, CALENDAR_FILE, STORY_THREADS_FILE
    global COSMERE_PC_DIR, COSMERE_HOMEBREW_FILE, HANDOUTS_FILE, COSMERE_ADVERSARIES_FILE
    ACTIVE_CAMPAIGN_ID = cid
    if cid:
        PARTY_DIR = _storage.party_dir(cid)
        ENCOUNTER_DIR = _storage.encounter_dir(cid)
        CAMPAIGN_ASSETS_DIR = _storage.campaign_assets_dir(cid)
        HANDOUTS_DIR = _storage.handouts_dir(cid)
        CAMPAIGN_AUDIO_DIR = _AUDIO_OVERRIDE or _storage.campaign_audio_dir(cid)
        CAMPAIGN_FILE = _storage.campaign_file(cid)
        LOOT_LEDGER_FILE = _storage.loot_ledger_file(cid)
        CAMPAIGN_STATS_FILE = _storage.campaign_stats_file(cid)
        JOURNAL_DIR = _storage.journal_dir(cid)
        SCRAPBOOK_FILE = _storage.session_highlights_file(cid)
        SCRAPBOOK_DIR = _storage.scrapbook_dir(cid)
        PINNED_GENERATORS_FILE = _storage.pinned_generators_file(cid)
        CALENDAR_FILE = _storage.calendar_file(cid)
        STORY_THREADS_FILE = _storage.story_threads_file(cid)
        COSMERE_PC_DIR = _storage.cosmere_pc_dir(cid)
        COSMERE_HOMEBREW_FILE = _storage.homebrew_file(cid)
        HANDOUTS_FILE = _storage.handouts_file(cid)
        COSMERE_ADVERSARIES_FILE = _storage.cosmere_adversaries_file(cid)
        _storage.ensure_campaign_dirs(cid)
    else:
        PARTY_DIR = os.path.join(DATA_DIR, 'party_data')
        ENCOUNTER_DIR = os.path.join(DATA_DIR, 'saved_encounters')
        CAMPAIGN_ASSETS_DIR = os.path.join(DATA_DIR, 'campaign_assets')
        HANDOUTS_DIR = os.path.join(DATA_DIR, 'uploads', 'handouts')
        CAMPAIGN_AUDIO_DIR = _AUDIO_OVERRIDE or os.path.join(DATA_DIR, 'campaign_audio')
        CAMPAIGN_FILE = os.path.join(DATA_DIR, 'campaign.json')
        LOOT_LEDGER_FILE = os.path.join(DATA_DIR, 'loot_ledger.json')
        CAMPAIGN_STATS_FILE = os.path.join(DATA_DIR, 'campaign_stats.json')
        JOURNAL_DIR = os.path.join(DATA_DIR, 'journals')
        SCRAPBOOK_FILE = os.path.join(DATA_DIR, 'session_highlights.json')
        SCRAPBOOK_DIR = os.path.join(DATA_DIR, 'scrapbooks')
        PINNED_GENERATORS_FILE = os.path.join(DATA_DIR, 'pinned_generators.json')
        CALENDAR_FILE = os.path.join(DATA_DIR, 'calendar.json')
        STORY_THREADS_FILE = os.path.join(BASE_DIR, 'story_threads.json')
        COSMERE_PC_DIR = os.path.join(DATA_DIR, 'cosmere_pcs')
        COSMERE_HOMEBREW_FILE = os.path.join(DATA_DIR, 'homebrew.json')
        HANDOUTS_FILE = os.path.join(DATA_DIR, 'handouts.json')
        COSMERE_ADVERSARIES_FILE = os.path.join(DATA_DIR, 'cosmere_adversaries.json')


def load_campaign(cid):
    """Switch the active campaign: re-bind paths and reload all campaign-scoped
    in-memory state. `cid` may be None to fall back to the legacy flat layout."""
    _bind_campaign_paths(cid)
    load_libraries()
    _load_session_state()
    _load_session_highlights()
    _load_handouts()
    return ACTIVE_CAMPAIGN_ID


_bind_campaign_paths(_storage.get_live_campaign_id())

# Ensure shared + active-campaign data directories exist (fresh deployments).
os.makedirs(MONSTER_DIR, exist_ok=True)
for _dir in [PARTY_DIR, ENCOUNTER_DIR, CAMPAIGN_ASSETS_DIR, HANDOUTS_DIR, os.path.join(PARTY_DIR, 'portraits')]:
    os.makedirs(_dir, exist_ok=True)

MONSTER_LIBRARY = {}
PARTY_LIBRARY = {}
PENDING_INITIATIVES = {}
ACTIVE_ENCOUNTER = []
TURN_INDEX = 0
ROUND_NUMBER = 1
ENCOUNTER_NOTES = ''
COMBAT_LOGS = []
# Session timer: epoch timestamp (seconds) when the current encounter started.
# None means timer not running. Set by /api/session_timer/start, cleared on
# encounter clear. Broadcast via SSE so player views can show elapsed time.
SESSION_TIMER_START = None

# --- PARTY CHAT (Tier 4, feature 20) ---
# In-memory message list: [{sender, text, timestamp}]. Clears on server restart.
CHAT_MESSAGES = []
CHAT_LOCK = threading.Lock()
_CHAT_MAX = 200

# --- CAMPAIGN STATS (Tier 4, feature 30) ---
# CAMPAIGN_STATS_FILE / JOURNAL_DIR are bound to the active campaign in
# _bind_campaign_paths(); ensure the journals dir exists for either layout.
os.makedirs(JOURNAL_DIR, exist_ok=True)


# --- LOOT LEDGER (persistent party treasure log) ---
def _load_loot_ledger():
    """Load the persistent loot ledger from disk."""
    if os.path.exists(LOOT_LEDGER_FILE):
        try:
            with open(LOOT_LEDGER_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"entries": [], "session_counter": 0}


def _save_loot_ledger(ledger):
    """Persist the loot ledger to disk."""
    try:
        with open(LOOT_LEDGER_FILE, 'w', encoding='utf-8') as f:
            json.dump(ledger, f, indent=2)
    except IOError:
        pass


def _mutate_loot_ledger(fn):
    """Locked read-modify-write on the loot ledger so concurrent adds/deletes
    don't lose entries -- a load->append->save by one greenlet would otherwise be
    clobbered by another's. `fn(ledger)` mutates the loaded ledger dict in place."""
    with _path_lock(LOOT_LEDGER_FILE):
        ledger = _load_loot_ledger()
        fn(ledger)
        _save_loot_ledger(ledger)
    return ledger


# --- SESSION HIGHLIGHTS (Chunk 6: Session Complete scrapbook) ---
# A structured accumulator filled as play happens (the combat log is free-text
# and capped, so it can't be mined retroactively). Reset on session begin,
# persisted to the volume so a crash/reload mid-session doesn't lose the
# wrap-up. crits/fumbles/loot are PC-attributed; big_hits are party-level
# (this app applies monster damage GM-side with no attacker attribution);
# rp_moments are GM-authored before the scrapbook is pushed to players.
SESSION_HIGHLIGHTS = {
    'session_number': 1,
    'started_at': '',
    'crits': [],       # {pc, action, detail, round}
    'fumbles': [],     # {pc, action, detail, round}
    'big_hits': [],    # {target, amount, round}
    'loot': [],        # {pc, items:[{name,qty}], coins:{pp,gp,sp,cp}}
    'rp_moments': [],  # {text, scope}  scope = 'party' or a PC name
    'narrative': '',   # Claude-written, generated at wrap-up
    'mvp_votes': {},   # {voter_pc: choice_pc} — MVP poll on the scrapbook
    'mvp_winner': '',  # the PC the GM crowned MVP (set when a Hero Point is granted)
}
SESSION_HIGHLIGHTS_LOCK = threading.Lock()


def _mvp_tally():
    """Anonymous vote counts for the MVP poll: {choice_pc: count}. We only
    ever broadcast counts, never who-voted-for-whom, so the poll stays private."""
    counts = {}
    with SESSION_HIGHLIGHTS_LOCK:
        for choice in SESSION_HIGHLIGHTS['mvp_votes'].values():
            counts[choice] = counts.get(choice, 0) + 1
    return counts

# Reentrant lock for encounter/PC state mutations. Used by internal helpers
# (_combat_log, _broadcast_*, _get_tracker_state, _do_persist_*) so that
# multi-step reads/writes are consistent under threaded=True.
ENCOUNTER_LOCK = threading.RLock()

# --- PER-FILE READ-MODIFY-WRITE LOCKS ---
# In-memory combat state is serialized by ENCOUNTER_LOCK and persisted via a
# coalesced flush, so it has no disk read-modify-write race. But some state lives
# IN its JSON file and is mutated load->change->save (Cosmere PC play_state, the
# loot ledger, campaign.json). On the single gevent worker the disk READ yields,
# so a second greenlet can load the same file before the first saves, and the
# later save clobbers the earlier one (a lost update -- e.g. a player setting HP
# on their sheet while the GM sets injuries from the tracker). A per-file lock
# makes each such sequence atomic. threading.Lock is gevent-patched by the
# gunicorn gevent worker, so this cooperates correctly with greenlets.
_PATH_LOCKS = {}
_PATH_LOCKS_META = threading.Lock()


def _path_lock(path):
    """Return the lock guarding read-modify-write on `path` (lazily created, one
    per absolute path). Wrap a load->mutate->save sequence in `with _path_lock(p):`
    so concurrent writers serialize instead of clobbering each other."""
    key = os.path.abspath(str(path or ''))
    with _PATH_LOCKS_META:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.Lock()
    return lock


# --- DEBOUNCED PERSISTENCE ---
# Rather than writing JSON to disk on every mutation, mark dirty and let a
# background thread flush every few seconds. Writes become effectively free
# at the request path; autosave still survives restarts (with at most ~2s lag).
_PERSIST_DIRTY = False
_PC_PERSIST_DIRTY = set()
_PERSIST_INTERVAL_SEC = 2
_persist_thread_started = False

# --- CACHED TRACKER STATE ---
# _get_tracker_state() rebuilds the full encounter snapshot on every call;
# tracker clients hit it frequently. Cache for a short window and invalidate
# on mutation (via _broadcast_encounter_state).
_TRACKER_STATE_CACHE = None
_TRACKER_STATE_CACHE_TIME = 0.0
_TRACKER_STATE_TTL = 0.5

def _invalidate_tracker_cache():
    """Drop the cached tracker state. Called on any state mutation."""
    global _TRACKER_STATE_CACHE, _TRACKER_STATE_CACHE_TIME
    _TRACKER_STATE_CACHE = None
    _TRACKER_STATE_CACHE_TIME = 0.0

def _hidden_npc_names():
    """Snapshot of NPC names the GM has marked hidden.

    Used by both the SSE broadcast filter and the REST endpoints that feed
    player combat-log history. Kept here so there's one source of truth for
    "which names must not leak to players."
    """
    with ENCOUNTER_LOCK:
        return [
            c.name for c in ACTIVE_ENCOUNTER
            if not c.is_pc and not getattr(c, 'visible_to_players', True) and c.name
        ]

def _scrub_hidden_names(text, hidden_names=None):
    """Replace every hidden NPC name occurrence in ``text`` with '???'.

    Case-insensitive. Callers can pre-compute the ``hidden_names`` list to
    avoid re-locking ENCOUNTER_LOCK on every call (useful inside tight loops
    like scrubbing a whole combat-log array).
    """
    if hidden_names is None:
        hidden_names = _hidden_npc_names()
    if not hidden_names or not isinstance(text, str):
        return text
    scrubbed = text
    for name in hidden_names:
        try:
            scrubbed = re.sub(re.escape(name), '???', scrubbed, flags=re.IGNORECASE)
        except Exception:
            continue
    return scrubbed

def _scrub_log_entries_for_players(entries):
    """Return a copy of ``entries`` with hidden NPC names masked in every
    user-visible string field. Called before returning combat-log JSON to
    any endpoint that a player might hit."""
    if _is_gm():
        return entries
    hidden = _hidden_npc_names()
    if not hidden:
        return entries
    cleaned = []
    for e in entries:
        if not isinstance(e, dict):
            cleaned.append(e)
            continue
        copy_e = dict(e)
        for key in ('msg', 'detail', 'action', 'result', 'name'):
            if key in copy_e:
                copy_e[key] = _scrub_hidden_names(copy_e.get(key), hidden)
        cleaned.append(copy_e)
    return cleaned

def _combat_log(msg, log_type='action'):
    """Append a timestamped entry to the combat log and broadcast via SSE.

    Players must never see the literal name of an NPC the GM has hidden. The
    filter below replaces every hidden NPC's name with '???' in the message
    before it goes to player subscribers. GM subscribers still see the raw
    message. This is a best-effort substring scrub — it deliberately runs
    after ``entry`` is appended to ``COMBAT_LOGS`` (the GM-visible log) so
    the GM-side history is complete.
    """
    entry = {
        'id': str(uuid.uuid4())[:8],
        'time': time.strftime('%H:%M:%S'),
        'round': ROUND_NUMBER,
        'msg': msg,
        'type': log_type
    }
    # Snapshot hidden NPC names under the lock so the filter is deterministic
    # even if the encounter mutates between append and broadcast.
    with ENCOUNTER_LOCK:
        COMBAT_LOGS.append(entry)
        if len(COMBAT_LOGS) > 200:
            COMBAT_LOGS.pop(0)
        hidden_names = [
            c.name for c in ACTIVE_ENCOUNTER
            if not c.is_pc and not getattr(c, 'visible_to_players', True) and c.name
        ]

    def _player_filter(p):
        if not hidden_names:
            return p
        p['msg'] = _scrub_hidden_names(p.get('msg'), hidden_names)
        return p

    # Push to any SSE subscribers so clients can react without polling
    try:
        _bump_perf('combat_log_total')
        sse_broadcast('combat_log', entry, player_filter=_player_filter)
    except Exception:
        pass

def _persist_encounter_state():
    """Mark encounter state dirty. A background thread flushes to disk."""
    global _PERSIST_DIRTY
    _PERSIST_DIRTY = True

def _do_persist_encounter_state():
    """Actually write the active encounter to disk. Called by flush thread."""
    # Build a snapshot under lock so iteration is consistent, then release
    # before touching the filesystem (disk writes can be slow).
    with ENCOUNTER_LOCK:
        if not ACTIVE_ENCOUNTER:
            autosave_path = os.path.join(ENCOUNTER_DIR, '_autosave.json')
            try:
                if os.path.exists(autosave_path):
                    os.remove(autosave_path)
            except Exception:
                pass
            return
        encounter_data = {
            "round": ROUND_NUMBER,
            "turn_index": TURN_INDEX,
            "notes": ENCOUNTER_NOTES,
            "session_timer_start": SESSION_TIMER_START,
            "combatants": []
        }
        for c in ACTIVE_ENCOUNTER:
            entry = {
                'type': 'pc' if c.is_pc else 'monster',
                'path': c.name if c.is_pc else c.file_path,
                'instance_id': c.instance_id,
                'initiative': c.initiative,
                'current_hp': c.current_hp,
                'conditions': dict(c.conditions),
                'condition_expiry': dict(getattr(c, 'condition_expiry', {}) or {}),
                # Preserve the native shape per combatant type so restore
                # round-trips cleanly: PCs = list[dict], monsters = str.
                'persistent_damage': (
                    [e for e in getattr(c, 'persistent_damage', []) if isinstance(e, dict)]
                    if c.is_pc else
                    getattr(c, 'persistent_damage', '')
                ),
                'elite_weak': getattr(c, 'elite_weak', 0),
                'delaying': getattr(c, 'delaying', False),
                # Preserve hidden/visible state across autosave — otherwise a
                # crash or reload mid-session would reveal hidden NPCs to players.
                'visible_to_players': getattr(c, 'visible_to_players', True),
                # Boss-reveal title (Chunk 4d) — keep it across reloads.
                'epithet': getattr(c, 'epithet', ''),
                # GM creature tactics notes — per-combatant free-text.
                'tactics': getattr(c, 'tactics', ''),
            }
            encounter_data['combatants'].append(_augment_combatant_save(entry, c))
    try:
        os.makedirs(ENCOUNTER_DIR, exist_ok=True)
        _atomic_write_json(os.path.join(ENCOUNTER_DIR, '_autosave.json'), encounter_data, indent=2)
    except Exception as e:
        print(f"[ENCOUNTER PERSIST ERROR] {e}")

def _flush_pending_persistence():
    """Flush any dirty encounter/PC state to disk. Called by background thread and at exit."""
    global _PERSIST_DIRTY
    if _PERSIST_DIRTY:
        _PERSIST_DIRTY = False  # clear first so concurrent mutations re-mark dirty
        try:
            _do_persist_encounter_state()
        except Exception as e:
            print(f"[PERSIST FLUSH] encounter: {e}")
    # Snapshot-and-swap the PC dirty set to avoid skipping additions mid-flush
    if _PC_PERSIST_DIRTY:
        pcs = list(_PC_PERSIST_DIRTY)
        for name in pcs:
            _PC_PERSIST_DIRTY.discard(name)
        for name in pcs:
            try:
                _do_persist_pc_combat_state(name)
            except Exception as e:
                print(f"[PERSIST FLUSH] pc {name}: {e}")

def _persistence_flush_loop():
    """Background thread: periodically flush dirty state to disk."""
    while True:
        time.sleep(_PERSIST_INTERVAL_SEC)
        try:
            _flush_pending_persistence()
        except Exception as e:
            print(f"[PERSIST LOOP] {e}")

def _start_persistence_thread():
    """Start the background flush thread exactly once."""
    global _persist_thread_started
    if _persist_thread_started:
        return
    _persist_thread_started = True
    t = threading.Thread(target=_persistence_flush_loop, daemon=True, name='persistence-flush')
    t.start()
    import atexit as _atexit
    _atexit.register(_flush_pending_persistence)

# --- SERVER-SENT EVENTS (SSE) FOR REAL-TIME SYNC ---
# Each subscriber is a (queue.Queue, is_gm: bool) tuple. We tag the queue at
# connection time so sse_broadcast() can route GM-only and player-sanitized
# payloads without peeking at per-request Flask sessions (SSE connections
# outlive any one request).
_sse_subscribers = []
_sse_lock = threading.Lock()
_sse_last_cleanup = time.time()
# Event-replay ring buffer: every broadcast gets a monotonic id, and the last
# _SSE_BUFFER_MAX events are kept so a client that briefly dropped (tablet asleep
# / off wifi) can reconnect with Last-Event-ID and be replayed the events it
# missed — instead of silently showing stale HP/conditions until a manual reload.
_sse_event_seq = 0
_sse_buffer = []            # list of (id, gm_frame, player_frame_or_None)
_SSE_BUFFER_MAX = 256
_SSE_MAX_SUBSCRIBERS = 200  # Hard cap to prevent memory leaks. Sized for a full
# table across several devices, each tab holding multiple EventSource
# connections; broadcasts iterate this list so it stays bounded, but 50 was low
# enough that reload churn could evict a live connection (see subscribe logic).
_SSE_STALE_TIMEOUT = 120  # Seconds before a non-consuming queue is considered stale

# SSE keepalive thread — fires a real `keepalive` event every 25s so:
#   1. Edge proxies (Railway, Cloudflare, etc.) see bytes within their
#      idle-timeout window and don't drop the long-lived connection.
#   2. Player sheets reset their `_sheetMarkSseAlive` heartbeat marker,
#      so the connection-status pip in the upper-left correctly reads
#      "live" during an idle round of roleplay.
# The per-connection comment heartbeat already there (`: heartbeat`)
# keeps bytes flowing but doesn't fire JS event listeners, so the
# client's freshness watchdog wouldn't reset without this.
_SSE_KEEPALIVE_SECS = 25
_sse_keepalive_started = False
_sse_keepalive_lock = threading.Lock()
def _sse_keepalive_loop():
    while True:
        try:
            time.sleep(_SSE_KEEPALIVE_SECS)
            with _sse_lock:
                n = len(_sse_subscribers)
            if n > 0:
                # Bypass the player_filter logic — this is identical for
                # GM and players, no PII risk.
                msg = f"event: keepalive\ndata: {{\"t\":{int(time.time())}}}\n\n"
                with _sse_lock:
                    dead = []
                    for entry in _sse_subscribers:
                        try:
                            entry[0].put_nowait(msg)
                        except queue.Full:
                            dead.append(entry)
                    for e in dead:
                        if e in _sse_subscribers:
                            _sse_subscribers.remove(e)
        except Exception as e:
            print(f"[SSE keepalive] {e}")

def _ensure_sse_keepalive():
    global _sse_keepalive_started
    with _sse_keepalive_lock:
        if _sse_keepalive_started:
            return
        t = threading.Thread(target=_sse_keepalive_loop, name='sse-keepalive', daemon=True)
        t.start()
        _sse_keepalive_started = True


def sse_broadcast(event_type, data, *, player_filter=None):
    """Push an event to all connected SSE clients.

    Parameters
    ----------
    event_type : str
        SSE event name the client listens for.
    data : dict
        Payload the GM should see (full, unfiltered).
    player_filter : Optional[Callable[[dict], Optional[dict]]]
        If given, called with a deepcopy-safe dict for each player subscriber.
        Return a dict to send that filtered payload to players, or None/False
        to drop the message entirely for players (GMs still receive `data`).
        If omitted, all subscribers receive `data` unchanged.
    """
    global _sse_last_cleanup
    _bump_perf('sse_emit_total')
    gm_msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    # Pre-compute the player-facing payload once; all player subscribers share it.
    player_msg = None
    if player_filter is None:
        player_msg = gm_msg
    else:
        try:
            filtered = player_filter(copy.deepcopy(data))
        except Exception as e:
            print(f"[SSE FILTER] {event_type}: {e}")
            filtered = None
        if filtered is not None and filtered is not False:
            player_msg = f"event: {event_type}\ndata: {json.dumps(filtered)}\n\n"

    global _sse_event_seq
    with _sse_lock:
        # Stamp a monotonic id on the frame (so the browser tracks lastEventId)
        # and keep it in the replay buffer.
        _sse_event_seq += 1
        sid = _sse_event_seq
        idln = "id: %d\n" % sid
        gm_msg = idln + gm_msg
        player_msg = (idln + player_msg) if player_msg else None
        _sse_buffer.append((sid, gm_msg, player_msg))
        if len(_sse_buffer) > _SSE_BUFFER_MAX:
            del _sse_buffer[:-_SSE_BUFFER_MAX]
        dead = []
        for entry in _sse_subscribers:
            q, is_gm = entry
            msg = gm_msg if is_gm else player_msg
            if msg is None:
                continue  # Player filter dropped this message
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(entry)
        for entry in dead:
            _sse_subscribers.remove(entry)

        # Periodic stale subscriber cleanup (every 60 seconds)
        now = time.time()
        if now - _sse_last_cleanup > 60:
            _sse_last_cleanup = now
            # Remove queues that are nearly full (stale clients)
            stale = [entry for entry in _sse_subscribers if entry[0].qsize() > 40]
            for entry in stale:
                _sse_subscribers.remove(entry)
            if _sse_subscribers or stale:
                print(f"[SSE] Active: {len(_sse_subscribers)}, Cleaned: {len(stale)}")

def sse_subscriber_count():
    """Return the number of active SSE subscribers."""
    with _sse_lock:
        return len(_sse_subscribers)

# ---- SSE broadcast coalescing ----
# Every state-mutation endpoint calls _broadcast_pc_state. A single compound
# action (Shield Block → drain temp, drop HP, tick wounded, repaint shield)
# used to emit 3-4 SSE frames. We coalesce everything hit within a 50ms window
# into one broadcast — amortizes deepcopy + json.dumps + fanout, and keeps the
# client from flashing through intermediate states.
_PC_BROADCAST_PENDING = set()
_PC_BROADCAST_LOCK = threading.Lock()
_PC_BROADCAST_TIMER = None
_PC_BROADCAST_DELAY = 0.05

# Encounter-state broadcast coalescing — same pattern as the PC-state
# debouncer above. The GM's cycle_turn handler emits a chain of mutations
# (frightened tick, slowed tick, raise-shield expiry, persistent damage,
# round/turn advance) and would otherwise multi-broadcast within ~5ms.
# We collapse those into one frame so subscribers see one consistent state.
_ENC_BROADCAST_PENDING = False
_ENC_BROADCAST_LOCK = threading.Lock()
_ENC_BROADCAST_TIMER = None
_ENC_BROADCAST_DELAY = 0.05

# Simple counters used by /api/perf so the GM can spot broadcast storms at
# the table. Cheap to maintain (a single int increment per fire).
_PERF_COUNTERS = {
    'sse_emit_total': 0,
    'pc_broadcast_total': 0,
    'pc_broadcast_coalesced': 0,
    'enc_broadcast_total': 0,
    'enc_broadcast_coalesced': 0,
    'combat_log_total': 0,
}
_PERF_COUNTERS_LOCK = threading.Lock()

def _bump_perf(key, delta=1):
    with _PERF_COUNTERS_LOCK:
        _PERF_COUNTERS[key] = _PERF_COUNTERS.get(key, 0) + delta

def _flush_pc_broadcasts():
    global _PC_BROADCAST_TIMER
    with _PC_BROADCAST_LOCK:
        pending = list(_PC_BROADCAST_PENDING)
        _PC_BROADCAST_PENDING.clear()
        _PC_BROADCAST_TIMER = None
    for name in pending:
        try:
            _do_broadcast_pc_state(name)
        except Exception as e:
            print(f"[SSE FLUSH] pc {name}: {e}")

def _broadcast_pc_state(pc_name):
    """Queue a PC-state broadcast. Coalesced inside a {_PC_BROADCAST_DELAY}s
    window so multiple mutations in the same request collapse to one frame."""
    global _PC_BROADCAST_TIMER
    if pc_name not in PARTY_LIBRARY:
        return
    _bump_perf('pc_broadcast_total')
    with _PC_BROADCAST_LOCK:
        if pc_name in _PC_BROADCAST_PENDING:
            _bump_perf('pc_broadcast_coalesced')
        _PC_BROADCAST_PENDING.add(pc_name)
        if _PC_BROADCAST_TIMER is None:
            t = threading.Timer(_PC_BROADCAST_DELAY, _flush_pc_broadcasts)
            t.daemon = True
            _PC_BROADCAST_TIMER = t
            t.start()

def _pc_state_payload(pc_name):
    """Build the full pc_update SSE frame for a PC (HP / shield / temp HP /
    derived saves+skills+strikes / conditions / effects). Returns None if the PC
    is unknown. Shared by the SSE broadcast and the /api/pc_state refetch route
    so a reconnecting sheet can pull a COMPLETE fresh state, not just HP."""
    if pc_name not in PARTY_LIBRARY:
        return None
    # Snapshot under lock so HP/conditions read is consistent
    with ENCOUNTER_LOCK:
        pc = PARTY_LIBRARY[pc_name]
        pct = pc.current_hp / pc.hp if pc.hp > 0 else 0
        spell_summary = []
        for caster in getattr(pc, 'spell_casters', []):
            caster_data = {'name': caster.get('name', ''), 'tradition': caster.get('tradition', ''), 'levels': []}
            for lvl in caster.get('levels', []):
                caster_data['levels'].append({
                    'level': lvl.get('level', 0),
                    'label': lvl.get('label', ''),
                    'slots': lvl.get('slots', 0),
                    'spells': [{'name': s.get('name', '')} for s in lvl.get('spells', [])]
                })
            spell_summary.append(caster_data)
        payload = {
            'name': pc_name,
            'current_hp': pc.current_hp,
            'max_hp': pc.hp,
            'hp_pct': round(pct * 100),
            'temp_hp': int(getattr(pc, 'temp_hp', 0) or 0),
            'temp_hp_manual': int(getattr(pc, 'temp_hp_manual', 0) or 0),
            'shield': {
                'raised': bool(getattr(pc, 'shield_raised', False)),
                'hp': int(getattr(pc, 'shield_hp', 0) or 0),
                'max_hp': int(getattr(pc, 'shield_max_hp', 0) or 0),
                'hardness': int(getattr(pc, 'shield_hardness', 0) or 0),
                'bt': int(getattr(pc, 'shield_bt', 0) or 0),
                'broken': bool(getattr(pc, 'shield_broken', False)),
                'destroyed': bool(getattr(pc, 'shield_destroyed', False)),
            },
            'reaction_used': bool(getattr(pc, 'reaction_used', False)),
            'ac': int(getattr(pc, 'ac', 0) or 0),
            'shield_ac_bonus': int(getattr(pc, 'shield_ac_bonus', 0) or 0),
            'persistent_damage': list(getattr(pc, 'persistent_damage', []) or []),
            'exploration_activity': str(getattr(pc, 'exploration_activity', '') or ''),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'focus': getattr(pc, 'current_focus', 0),
            'hero_points': getattr(pc, 'hero_points', 1),
            'xp': int(getattr(pc, 'xp', 0) or 0),
            'ready_to_level': bool(getattr(pc, 'ready_to_level', False)),
            'spell_casters': spell_summary,
            # Read from the in-memory PC (hydrated at __init__ from the
            # build's expended_slots dict). Routes that mutate slots keep
            # pc.expended_slots in sync so this never has to hit disk.
            'expended_slots': dict(getattr(pc, 'expended_slots', {}) or {}),
            # Sheet-level active effects + the post-stacking effective
            # stats / breakdown. Lets the player sheet repaint its
            # Active Effects panel without a follow-up API call.
            'pc_active_effects': list(getattr(pc, 'pc_active_effects', []) or []),
        }
        try:
            eff = pc.compute_effective_stats()
            payload['effective'] = eff['effective']
            payload['effects_breakdown'] = eff['breakdown']
        except Exception as _e:
            # Compute is best-effort — never let a bad effect block a
            # state broadcast (HP / conditions are mission-critical).
            print(f"[BROADCAST] {pc_name}: effects compute failed: {_e}")
            payload['effective'] = {}
            payload['effects_breakdown'] = []
        # Derived combat numbers the player sheet repaints IN PLACE when a
        # condition / feature toggle / two-hand swap ripples through them.
        # These mirror exactly what the Jinja sheet renders (pc.fort, pc.skills,
        # pc.attacks, …) — NOT the post-active-effect `effective` values — so a
        # live painter and a hard reload agree to the digit. (Active-effect
        # buffs surface in the Active Effects breakdown, same as the server
        # render; folding them into saves here would diverge from a reload.)
        try:
            payload['derived'] = {
                'fort':       int(pc.fort or 0),
                'ref':        int(pc.ref or 0),
                'will':       int(pc.will or 0),
                'perception': int(pc.perception or 0),
                'skills': [
                    {'name': s.get('name', ''),
                     'total': s.get('total', '+0'),
                     'penalty': int(s.get('penalty', 0) or 0)}
                    for s in (pc.skills or [])
                ],
                'attacks': [
                    {'name': a.get('name', ''),
                     'damage': a.get('damage', ''),
                     'is_two_handed': bool(a.get('is_two_handed', False)),
                     'strikes': [
                         {'label': st.get('label', ''), 'mod': int(st.get('mod', 0) or 0)}
                         for st in (a.get('strikes') or [])
                     ]}
                    for a in (pc.attacks or [])
                ],
            }
        except Exception as _e:
            print(f"[BROADCAST] {pc_name}: derived compute failed: {_e}")
            payload['derived'] = {}
    return payload


def _do_broadcast_pc_state(pc_name):
    """Compute and emit the PC-state SSE frame."""
    payload = _pc_state_payload(pc_name)
    if payload is not None:
        sse_broadcast('pc_update', payload)


@app.route('/api/pc_state/<pc_name>')
def api_pc_state(pc_name):
    """The full pc_update-shaped state for one PC so the player sheet can do a
    COMPLETE refetch on SSE reconnect/wake (AC, saves, skills, strikes, shield,
    temp HP, conditions) instead of only patching HP+conditions after 45s."""
    payload = _pc_state_payload(pc_name)
    if payload is None:
        return jsonify({'error': 'unknown character'}), 404
    return jsonify(payload)


def _flush_enc_broadcast():
    global _ENC_BROADCAST_TIMER, _ENC_BROADCAST_PENDING
    with _ENC_BROADCAST_LOCK:
        if not _ENC_BROADCAST_PENDING:
            _ENC_BROADCAST_TIMER = None
            return
        _ENC_BROADCAST_PENDING = False
        _ENC_BROADCAST_TIMER = None
    try:
        _do_broadcast_encounter_state()
    except Exception as e:
        print(f"[SSE FLUSH] encounter: {e}")

def _broadcast_encounter_state():
    """Queue an encounter-state broadcast. Coalesced inside a
    {_ENC_BROADCAST_DELAY}s window — handlers like cycle_turn that mutate
    several combatants in sequence collapse into one frame.

    The tracker-state cache is invalidated synchronously here (not in the
    deferred flush) so a follow-up GET to /api/tracker_state sees the new
    state immediately even if the SSE frame is still pending.
    """
    global _ENC_BROADCAST_TIMER, _ENC_BROADCAST_PENDING
    _bump_perf('enc_broadcast_total')
    _invalidate_tracker_cache()
    with _ENC_BROADCAST_LOCK:
        if _ENC_BROADCAST_PENDING:
            _bump_perf('enc_broadcast_coalesced')
        _ENC_BROADCAST_PENDING = True
        if _ENC_BROADCAST_TIMER is None:
            t = threading.Timer(_ENC_BROADCAST_DELAY, _flush_enc_broadcast)
            t.daemon = True
            _ENC_BROADCAST_TIMER = t
            t.start()

def _do_broadcast_encounter_state():
    """Build and emit the encounter-state SSE frame (uncoalesced).

    GM subscribers receive the raw payload (every combatant's name, HP, and
    conditions). Player subscribers receive a filtered payload where any NPC
    with ``visible_to_players == False`` has its name replaced with '???',
    initiative hidden, and HP/condition data stripped. The ``active_name``
    field is also scrubbed when the active combatant is hidden so the turn
    banner doesn't leak mid-fight.
    """
    _invalidate_tracker_cache()
    with ENCOUNTER_LOCK:
        active_c = ACTIVE_ENCOUNTER[TURN_INDEX] if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
        active_name = active_c.name if active_c else None
        # `active_id` lets clients match the active combatant by
        # instance_id rather than by name. Two combatants sharing a
        # name (e.g. "Goblin", "Goblin") previously both got the
        # active-pulse on player view because the match was by name.
        active_id = active_c.instance_id if active_c else None
        combatants = []
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            entry = {
                # instance_id lets the map sidebar's drag-to-map drop the
                # token already linked to the encounter combatant, so HP
                # / conditions sync through /api/adjust_hp without a
                # second lookup. The player filter below treats this as
                # safe to expose (the same id is already in the player
                # state payload via the tracker).
                'instance_id': c.instance_id,
                'name': c.name,
                'is_pc': c.is_pc,
                'system': getattr(c, 'system', 'pf2e'),
                'ac': getattr(c, 'ac', None),
                'initiative': c.initiative,
                'is_active': (i == TURN_INDEX),
                # Include visibility so GM UI can render a hidden-eye indicator.
                # The player filter below strips this flag along with the rest
                # of the hidden combatant's data.
                'visible_to_players': getattr(c, 'visible_to_players', True),
            }
            if c.is_pc:
                pct = c.current_hp / c.hp if c.hp > 0 else 0
                entry['current_hp'] = c.current_hp
                entry['max_hp'] = c.hp
                entry['hp_pct'] = round(pct * 100)
                entry['reaction_used'] = bool(getattr(c, 'reaction_used', False))
                entry['hero_points'] = int(getattr(c, 'hero_points', 0) or 0)
                entry['persistent_damage_list'] = [
                    {'damage': e.get('damage', ''), 'type': e.get('type', ''), 'source': e.get('source', '')}
                    for e in (getattr(c, 'persistent_damage', []) or [])
                    if isinstance(e, dict)
                ]
            else:
                pct = c.current_hp / c.hp if c.hp > 0 else 0
                if c.current_hp == 0:
                    entry['hp_status'] = 'Dead'
                elif pct <= 0.5:
                    entry['hp_status'] = 'Wounded'
                else:
                    entry['hp_status'] = ''
                # Boss-reveal title (Chunk 4d). GM-only here; the player
                # filter masks hidden NPCs entirely, and a revealed NPC's
                # title is harmless to expose.
                entry['epithet'] = getattr(c, 'epithet', '')
            entry['conditions'] = {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False}
            entry['condition_expiry'] = dict(getattr(c, 'condition_expiry', {}) or {})
            entry['actions_used'] = int(getattr(c, 'actions_used', 0) or 0)
            entry['max_actions'] = int(getattr(c, 'max_actions', 3) or 3)
            # Monsters reuse the same reaction_used field as PCs.
            if 'reaction_used' not in entry:
                entry['reaction_used'] = bool(getattr(c, 'reaction_used', False))
            if entry.get('system') == 'cosmere' and hasattr(c, 'tracker_block'):
                entry['cosmere'] = c.tracker_block()
            combatants.append(entry)
        payload = {
            'encounter': combatants,
            'round': ROUND_NUMBER,
            'active_name': active_name,
            'active_id': active_id,
            'turn_index': TURN_INDEX,
            'session_timer_start': SESSION_TIMER_START,
            # Flag used by the player filter below — avoids re-reading globals
            # inside the filter, which runs after ENCOUNTER_LOCK is released.
            '_active_visible': getattr(active_c, 'visible_to_players', True) if active_c else True,
        }

    def _player_filter(p):
        # Strip any GM-only side-channel flags before the payload goes out.
        active_visible = p.pop('_active_visible', True)
        filtered_enc = []
        for entry in p.get('encounter', []):
            if entry.get('is_pc') or entry.get('visible_to_players', True):
                # PCs and visible NPCs: pass through unchanged. (PCs should
                # always be visible; the attribute is kept True at init.)
                filtered_enc.append(entry)
                continue
            # Hidden NPC — mask identity and all stat data. We keep is_active
            # so the player UI can still show "it's the enemy's turn" without
            # naming the creature, and we keep a stable index position so the
            # initiative order doesn't visibly collapse.
            filtered_enc.append({
                'name': '???',
                'is_pc': False,
                'initiative': None,
                'is_active': entry.get('is_active', False),
                'hp_status': '',
                'conditions': {},
                'hidden': True,
            })
        p['encounter'] = filtered_enc
        if not active_visible:
            p['active_name'] = '???'
            # active_id leaks the hidden combatant's instance_id —
            # strip it so a client can't probe for hidden creatures
            # by id correlation.
            p['active_id'] = None
        return p

    sse_broadcast('encounter_update', payload, player_filter=_player_filter)

COMPENDIUM_LIBRARY = {}
COMPENDIUM_RULES = {}

def _merge_rules(name, new_rules):
    """Register rules for a compendium entry by merging (not replacing).

    Some entries share names across different folders. The classic case is
    'Orc Warmask': there's a feats/ancestry entry (GrantItem rules — grants
    the equipment) and an equipment/ entry (ChoiceSet + FlatModifier — gives
    the actual +1 item bonus). A plain dict assignment let whichever loader
    ran last silently clobber the other, so the resolver never saw the
    ChoiceSet rule that drives the tradition-picker. Merging preserves both
    rule sets so `Character.__init__` applies the full picture.

    Dedup by serialized form: some files are loaded through more than one
    walker (e.g. the `equipment/` loader and an archetype loader both catch
    the same path), which without dedup would stack identical rules and
    double-apply item bonuses.
    """
    if not name:
        return
    key = name.lower()
    rules = list(new_rules) if new_rules else []
    existing = COMPENDIUM_RULES.get(key) or []
    seen = set()
    merged = []
    for r in list(existing) + rules:
        # Only dict rules need dedup; non-dicts are passed through verbatim.
        if isinstance(r, dict):
            try:
                sig = json.dumps(r, sort_keys=True, default=str)
            except Exception:
                sig = id(r)  # fall back to identity if unserializable
            if sig in seen:
                continue
            seen.add(sig)
        merged.append(r)
    COMPENDIUM_RULES[key] = merged

BUILDER_ANCESTRIES = {
    # Core ancestries (Player Core) — fallback data if no compendium loaded
    "Human": {"boosts": {"str": True, "dex": True, "con": True, "int": True, "wis": True, "cha": True}, "flaws": [], "hp": 8, "rarity": "common", "description": "Humans are diverse and adaptable, with a wide range of cultures."},
    "Elf": {"boosts": {"dex": True, "int": True}, "flaws": ["con"], "hp": 6, "rarity": "common", "description": "Elves are long-lived with a deep connection to magic and nature."},
    "Dwarf": {"boosts": {"con": True, "wis": True}, "flaws": ["cha"], "hp": 10, "rarity": "common", "description": "Dwarves are stout folk with a strong sense of duty and craftsmanship."},
    "Halfling": {"boosts": {"dex": True, "wis": True}, "flaws": ["str"], "hp": 6, "rarity": "common", "description": "Halflings are small, nimble folk known for their bravery and luck."},
    "Goblin": {"boosts": {"dex": True, "cha": True}, "flaws": ["wis"], "hp": 6, "rarity": "common", "description": "Goblins are energetic and creative, with a love of fire and songs."},
    "Gnome": {"boosts": {"con": True, "cha": True}, "flaws": ["str"], "hp": 8, "rarity": "common", "description": "Gnomes are fey-touched beings with vibrant appearances and curious natures."},
    "Orc": {"boosts": {"str": True, "con": True}, "flaws": [], "hp": 10, "rarity": "common", "description": "Orcs are strong and resilient, with a deep culture of honor and battle."},
    # Player Core 2 / APG ancestries
    "Leshy": {"boosts": {"con": True, "wis": True}, "flaws": ["int"], "hp": 8, "rarity": "common", "description": "Leshies are nature spirits given physical form from plant matter."},
    "Catfolk": {"boosts": {"dex": True, "cha": True}, "flaws": ["wis"], "hp": 8, "rarity": "common", "description": "Catfolk are agile humanoids with feline features and keen senses."},
    "Kobold": {"boosts": {"dex": True, "cha": True}, "flaws": ["con"], "hp": 6, "rarity": "common", "description": "Kobolds are small draconic humanoids who pride themselves on cunning."},
    "Tengu": {"boosts": {"dex": True, "con": True}, "flaws": [], "hp": 6, "rarity": "common", "description": "Tengu are avian humanoids who prize swordplay and storytelling."},
    "Ratfolk": {"boosts": {"dex": True, "int": True}, "flaws": ["str"], "hp": 6, "rarity": "common", "description": "Ratfolk are small, clever humanoids who thrive in tight-knit communities."},
    "Lizardfolk": {"boosts": {"str": True, "wis": True}, "flaws": ["int"], "hp": 8, "rarity": "common", "description": "Lizardfolk are cold-blooded reptilian humanoids at home in swamps and rivers."},
    "Kitsune": {"boosts": {"cha": True}, "flaws": [], "hp": 8, "rarity": "common", "description": "Kitsune are shapeshifting fox folk with ties to the First World."},
    "Android": {"boosts": {"dex": True, "int": True}, "flaws": ["cha"], "hp": 8, "rarity": "uncommon", "description": "Androids are synthetic humanoids with exceptional analytical abilities."},
    "Fetchling": {"boosts": {"dex": True, "cha": True}, "flaws": ["wis"], "hp": 8, "rarity": "uncommon", "description": "Fetchlings are shadowy humanoids from the Shadow Plane."},
    "Automaton": {"boosts": {"str": True, "con": True}, "flaws": [], "hp": 8, "rarity": "uncommon", "description": "Automatons are ancient constructs granted sentience."},
    "Fleshwarp": {"boosts": {"con": True}, "flaws": [], "hp": 10, "rarity": "uncommon", "description": "Fleshwarps are beings whose bodies have been transformed by powerful magic."},
    "Gnoll": {"boosts": {"str": True, "int": True}, "flaws": ["wis"], "hp": 8, "rarity": "uncommon", "description": "Gnolls are hyena-like humanoids with a deep sense of community."},
    "Grippli": {"boosts": {"dex": True, "wis": True}, "flaws": ["str"], "hp": 6, "rarity": "uncommon", "description": "Gripplis are small frog-like humanoids native to tropical forests."},
    "Poppet": {"boosts": {"con": True, "cha": True}, "flaws": ["dex"], "hp": 6, "rarity": "uncommon", "description": "Poppets are small constructs brought to life by magical means."},
}
BUILDER_BACKGROUNDS = {}
BUILDER_CLASSES = {}
BUILDER_FEATS = { 'class': [], 'skill': [], 'general': [], 'ancestry': [] }
BUILDER_SPELLS = []
BUILDER_WEAPONS = []

# A few rituals ship with empty descriptions in the Foundry compendium data
# (the raw per-spell JSON is blank too). Backfilled from Archives of Nethys —
# the official PF2e SRD (2e.aonprd.com) — so the sheet isn't blank for them.
_SPELL_DESC_OVERRIDES = {
    'transmigrate': (
        "You temporarily transmigrate slain characters out of a mindscape so they appear in the "
        "living world in solid bodies made of ectoplasm, existing for a short time in a hazy area "
        "between life and undeath. Cast over 4 hours with 2 secondary casters (primary check "
        "Occultism or Religion); the targets are encased in clay within a kiln. Range 20 feet; "
        "Duration 1 month. Critical Success: the targets reach a border realm between life and death "
        "and, on overcoming its challenge, manifest in the living world as ectoplasmic forms for up "
        "to 1 month, gaining a +1 status bonus to all skill checks for the first week. Success: as "
        "critical success but without the first-week bonus. Failure: the targets face the border-realm "
        "challenge with a -1 status penalty to Strikes, saving throws, and skill checks, which "
        "persists during the first week. Critical Failure: the ritual fails and each target takes 9d6 "
        "fire damage (DC 24 basic Fortitude) as the kiln breaks open; a new attempt requires waiting 24 hours."
    ),
    'mindscape shift': (
        "You attempt to transport targets from a mindscape they currently occupy into an adjacent, "
        "nearly identical mindscape. Unlike mindscape door, mindscape shift moves its targets entirely "
        "into the new mindscape rather than merely projecting their minds, and they appear in "
        "corresponding locations. Cast over 1 hour with 3 secondary casters and focusing diagrams and "
        "incense worth 28 gp (primary check Arcana or Occultism [expert], or Willowshore Lore); range "
        "touch, targeting you and the secondary casters. Critical Success: the targets are transported "
        "and you can leave a portal back to the previous mindscape that lasts up to 24 hours. Success: "
        "the targets are transported as intended. Failure: you fail to transport the targets. Critical "
        "Failure: you fail and mental feedback deals 9d6 mental damage to all ritual casters (DC 26 "
        "basic Will save). Heightened (8th): you can transport targets to any mindscape you have visited "
        "before or have detailed knowledge of."
    ),
    'open the wall of ghosts': (
        "You open a passage through the Wall of Ghosts; the ritual must be performed during a crescent "
        "or new moon. Cast over 1 day with 3 secondary casters and rice grains and incense worth 60 gp "
        "(primary check Occultism or Religion [expert]); range 40 feet, targeting the Wall of Ghosts; "
        "Duration 1 year. Critical Success: the ritual succeeds and grants you and the secondary casters "
        "a +2 status bonus to AC and saving throws while passing through the Wall of Ghosts. Success: the "
        "ritual succeeds. Failure: the ritual fails. Critical Failure: the ritual fails and two ghost "
        "commoners tear free and attack, their ghostly hand Strikes dealing mental damage."
    ),
}
BUILDER_ARMOR = []

# PF2e standard shield stats (Core Rulebook + Secrets of Magic basics).
# Keys are lowercased for lookup; BT is always max_hp // 2.
# Pathbuilder stores shields as entries in build['armor'] with prof='shield';
# the `name` string is what we match against this table.
SHIELD_TYPES = {
    # Core Rulebook
    "buckler":          {"ac_bonus": 1, "hardness": 3,  "max_hp": 6},
    "wooden shield":    {"ac_bonus": 2, "hardness": 3,  "max_hp": 12},
    "steel shield":     {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},
    "tower shield":     {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},  # +2 raise, +4 Take Cover
    # Advanced / specialty
    "spiked shield":    {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},
    "boss shield":      {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},
    "meteor shield":    {"ac_bonus": 2, "hardness": 6,  "max_hp": 24},
    "dart shield":      {"ac_bonus": 2, "hardness": 6,  "max_hp": 24},
    "reinforcing rune wooden":  {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},
    "reinforcing rune steel":   {"ac_bonus": 2, "hardness": 7,  "max_hp": 28},
    # Generic fallbacks if name doesn't match
    "shield":           {"ac_bonus": 2, "hardness": 5,  "max_hp": 20},
}

def _shield_stats_for(name: str):
    """Return (ac_bonus, hardness, max_hp, bt) for a shield by name, or None.
    Matches by normalized lowercase and substring — so 'Sturdy Shield (minor)'
    falls back to the generic steel-shield profile."""
    if not name:
        return None
    n = str(name).strip().lower()
    if n in SHIELD_TYPES:
        s = SHIELD_TYPES[n]
        return s['ac_bonus'], s['hardness'], s['max_hp'], s['max_hp'] // 2
    # Substring fallback: pick the longest matching key so 'spiked shield'
    # beats 'shield'.
    best = None
    for key in SHIELD_TYPES:
        if key in n and (best is None or len(key) > len(best)):
            best = key
    if best:
        s = SHIELD_TYPES[best]
        return s['ac_bonus'], s['hardness'], s['max_hp'], s['max_hp'] // 2
    return None

# PF2E standard weapon damage — used to correct DB entries that default to 1d4
PF2E_WEAPON_DAMAGE = {
    # Simple Melee
    "Club": "1d6 B", "Dagger": "1d4 P", "Gauntlet": "1d4 B", "Light Mace": "1d4 B",
    "Longspear": "1d8 P", "Mace": "1d6 B", "Morningstar": "1d6 B", "Sickle": "1d4 S",
    "Spear": "1d6 P", "Staff": "1d4 B", "Fist": "1d4 B",
    # Simple Ranged
    "Crossbow": "1d8 P", "Dart": "1d4 P", "Javelin": "1d6 P", "Sling": "1d6 B",
    "Blowgun": "1 P", "Hand Crossbow": "1d6 P", "Heavy Crossbow": "1d10 P",
    # Martial Melee
    "Bastard Sword": "1d8 S", "Battle Axe": "1d8 S", "Bo Staff": "1d8 B",
    "Falchion": "1d10 S", "Flail": "1d6 B", "Glaive": "1d8 S", "Greataxe": "1d12 S",
    "Greatclub": "1d10 B", "Greatsword": "1d12 S", "Guisarme": "1d10 S",
    "Halberd": "1d10 P", "Hatchet": "1d6 S", "Katana": "1d6 S", "Kukri": "1d6 S",
    "Lance": "1d8 P", "Light Hammer": "1d6 B", "Light Pick": "1d4 P",
    "Longsword": "1d8 S", "Main-Gauche": "1d4 P", "Maul": "1d12 B",
    "Pick": "1d6 P", "Ranseur": "1d10 P", "Rapier": "1d6 P",
    "Scimitar": "1d6 S", "Scythe": "1d10 S", "Shield Bash": "1d4 B",
    "Shield Boss": "1d6 B", "Shortsword": "1d6 P", "Starknife": "1d4 P",
    "Trident": "1d8 P", "War Flail": "1d10 B", "Warhammer": "1d8 B",
    "Whip": "1d4 S",
    # Martial Ranged
    "Composite Longbow": "1d8 P", "Composite Shortbow": "1d6 P",
    "Longbow": "1d8 P", "Shortbow": "1d6 P",
    # Advanced Melee
    "Aldori Dueling Sword": "1d8 S", "Dwarven Waraxe": "1d8 S",
    "Gnome Flickmace": "1d8 B", "Orc Necksplitter": "1d8 S",
    "Sawtooth Saber": "1d6 S", "Elven Curve Blade": "1d8 S",
    "Spiked Chain": "1d8 S", "Urumi": "1d6 S",
    "Karambit": "1d4 S", "Kama": "1d6 S", "Nunchaku": "1d6 B",
    "Sai": "1d4 P", "Shuriken": "1d4 P", "Wakizashi": "1d4 S",
    "Temple Sword": "1d8 S", "Khopesh": "1d8 S", "Katar": "1d4 P",
    # Martial Ranged
    "Alchemical Crossbow": "1d8 P",
}

# PF2E weapon categories
PF2E_WEAPON_CATEGORIES = {
    "Club": "simple", "Dagger": "simple", "Gauntlet": "simple", "Light Mace": "simple",
    "Longspear": "simple", "Mace": "simple", "Morningstar": "simple", "Sickle": "simple",
    "Spear": "simple", "Staff": "simple", "Fist": "simple",
    "Crossbow": "simple", "Dart": "simple", "Javelin": "simple", "Sling": "simple",
    "Blowgun": "simple", "Hand Crossbow": "simple", "Heavy Crossbow": "simple",
    "Bastard Sword": "martial", "Battle Axe": "martial", "Bo Staff": "martial",
    "Falchion": "martial", "Flail": "martial", "Glaive": "martial", "Greataxe": "martial",
    "Greatclub": "martial", "Greatsword": "martial", "Guisarme": "martial",
    "Halberd": "martial", "Hatchet": "martial", "Katana": "martial", "Kukri": "martial",
    "Lance": "martial", "Light Hammer": "martial", "Light Pick": "martial",
    "Longsword": "martial", "Main-Gauche": "martial", "Maul": "martial",
    "Pick": "martial", "Ranseur": "martial", "Rapier": "martial",
    "Scimitar": "martial", "Scythe": "martial", "Shield Bash": "martial",
    "Shield Boss": "martial", "Shortsword": "martial", "Starknife": "martial",
    "Trident": "martial", "War Flail": "martial", "Warhammer": "martial",
    "Whip": "martial", "Composite Longbow": "martial", "Composite Shortbow": "martial",
    "Longbow": "martial", "Shortbow": "martial",
    "Aldori Dueling Sword": "advanced", "Dwarven Waraxe": "advanced",
    "Gnome Flickmace": "advanced", "Orc Necksplitter": "advanced",
    "Sawtooth Saber": "advanced", "Elven Curve Blade": "advanced",
    "Spiked Chain": "advanced", "Urumi": "advanced", "Karambit": "advanced",
    "Kama": "martial", "Nunchaku": "martial", "Sai": "martial",
    "Shuriken": "martial", "Wakizashi": "martial", "Temple Sword": "martial",
    "Khopesh": "martial", "Katar": "martial",
}

pf2e_gen = RobustPF2eGenerator()

# --- SECURITY: Whitelisted generator types to prevent arbitrary method calls ---
VALID_GENERATOR_TYPES = {
    'npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter',
    'weather', 'trap', 'rumor', 'settlement', 'treasure_hoard', 'random_event',
    # New rich cards
    'faction', 'deity', 'villain', 'dungeon_room', 'travel_encounter',
    # Rapid-fire one-line generators (on-demand only, via the Rapid Fire bar)
    'rapid_name', 'rapid_tavern', 'rapid_shop', 'rapid_place', 'rapid_twist',
    'rapid_omen', 'rapid_bounty', 'rapid_loot', 'rapid_trinket', 'rapid_room',
}

# --- SECURITY: Allowed image extensions for vault image serving ---
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}  # .svg dropped — SVG can carry inline JS / event handlers; if you need to host one, store it as a static asset.

# --- CHARACTER FILE LOOKUP CACHE ---
_PC_FILE_CACHE = {}  # Maps character name -> filename (not full path)

RICH_CLASS_DATA = {
    "fighter": { "key_options": ["str", "dex"], "base_skills": ["athletics", "acrobatics"], "free_skills": 3, "subclass_label": "Combat Style", "subclasses": ["Two-Handed", "Dual-Wielding", "Sword & Board", "Archery"] },
    "wizard": { "key_options": ["int"], "base_skills": ["arcana"], "free_skills": 2, "spellcasting": "prepared", "traditions": ["arcane"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Arcane School", "subclasses": ["Abjuration", "Conjuration", "Divination", "Enchantment", "Evocation", "Illusion", "Necromancy", "Transmutation", "Universalist"] },
    "rogue": { "key_options": ["dex", "str", "cha", "int"], "base_skills": ["stealth"], "free_skills": 7, "subclass_label": "Rogue's Racket", "subclasses": ["Ruffian", "Scoundrel", "Thief", "Eldritch Trickster", "Mastermind"] },
    "cleric": { "key_options": ["wis"], "base_skills": ["religion"], "free_skills": 2, "spellcasting": "prepared", "traditions": ["divine"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Cleric Doctrine", "subclasses": ["Cloistered Cleric", "Warpriest"] },
    "druid": { "key_options": ["wis"], "base_skills": ["nature"], "free_skills": 2, "spellcasting": "prepared", "traditions": ["primal"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Druidic Order", "subclasses": ["Animal", "Leaf", "Storm", "Untamed"] },
    "kineticist": { "key_options": ["con"], "base_skills": ["nature"], "free_skills": 3, "subclass_label": "Elemental Gate", "subclasses": ["Single Gate", "Dual Gate"] },
    "bard": { "key_options": ["cha"], "base_skills": ["occultism", "performance"], "free_skills": 4, "spellcasting": "spontaneous", "traditions": ["occult"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Muse", "subclasses": ["Enigma", "Maestro", "Polymath", "Warrior"] },
    "sorcerer": { "key_options": ["cha"], "base_skills": [], "free_skills": 2, "spellcasting": "spontaneous", "traditions": ["arcane", "divine", "occult", "primal"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Bloodline", "subclasses": ["Aberrant", "Angelic", "Demonic", "Diabolic", "Draconic", "Elemental", "Fey", "Hag", "Imperial", "Nymph", "Undead"] },
    "barbarian": { "key_options": ["str"], "base_skills": ["athletics"], "free_skills": 3, "subclass_label": "Instinct", "subclasses": ["Animal", "Dragon", "Fury", "Giant", "Spirit", "Superstition"] },
    "champion": { "key_options": ["str", "dex"], "base_skills": ["religion"], "free_skills": 2, "subclass_label": "Cause", "subclasses": ["Justice", "Mercy", "Grandeur", "Paladin", "Redeemer", "Liberator", "Desecrator", "Tyrant", "Antipaladin"] },
    "monk": { "key_options": ["str", "dex"], "base_skills": ["athletics", "acrobatics"], "free_skills": 4 },
    "ranger": { "key_options": ["str", "dex"], "base_skills": ["nature", "survival"], "free_skills": 4, "subclass_label": "Hunter's Edge", "subclasses": ["Flurry", "Outwit", "Precision"] },
    "alchemist": { "key_options": ["int"], "base_skills": ["crafting"], "free_skills": 3, "subclass_label": "Research Field", "subclasses": ["Bomber", "Chirurgeon", "Mutagenist", "Toxicologist"] },
    "investigator": { "key_options": ["int"], "base_skills": ["society"], "free_skills": 4, "subclass_label": "Methodology", "subclasses": ["Alchemical Sciences", "Empiricism", "Interrogation", "Forensic Medicine"] },
    "swashbuckler": { "key_options": ["dex"], "base_skills": ["acrobatics"], "free_skills": 4, "subclass_label": "Style", "subclasses": ["Battledancer", "Braggart", "Fencer", "Gymnast", "Wit"] },
    "gunslinger": { "key_options": ["dex"], "base_skills": ["crafting"], "free_skills": 4, "subclass_label": "Way", "subclasses": ["Drifter", "Pistolero", "Sniper", "Vanguard", "Spellshot"] },
    "inventor": { "key_options": ["int"], "base_skills": ["crafting"], "free_skills": 3, "subclass_label": "Innovation", "subclasses": ["Armor", "Construct", "Weapon"] },
    "thaumaturge": { "key_options": ["cha"], "base_skills": [], "free_skills": 3, "subclass_label": "Implement", "subclasses": ["Amulet", "Bell", "Chalice", "Tome", "Wand", "Weapon"] },
    "witch": { "key_options": ["int"], "base_skills": [], "free_skills": 3, "spellcasting": "prepared", "traditions": ["arcane", "divine", "occult", "primal"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Patron", "subclasses": ["Curse", "Fate", "Fervor", "Night", "Rune", "Wild", "Winter"] },
    "oracle": { "key_options": ["cha"], "base_skills": ["religion"], "free_skills": 3, "spellcasting": "spontaneous", "traditions": ["divine"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Mystery", "subclasses": ["Ancestors", "Battle", "Bones", "Cosmos", "Flames", "Life", "Lore", "Tempest", "Time"] },
    "psychic": { "key_options": ["int", "cha"], "base_skills": ["occultism"], "free_skills": 3, "spellcasting": "spontaneous", "traditions": ["occult"], "starting_spells": {"cantrips": 3, "lvl1": 2}, "subclass_label": "Conscious Mind", "subclasses": ["Distant Grasp", "Infinite Eye", "Silent Whisper", "Tangent Strike", "Unbound Step"] },
    "magus": { "key_options": ["str", "dex"], "base_skills": ["arcana"], "free_skills": 2, "spellcasting": "bounded_prepared", "traditions": ["arcane"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Hybrid Study", "subclasses": ["Inexorable Iron", "Laughing Shadow", "Sparkling Targe", "Starlit Span", "Twisting Tree"] },
    "summoner": { "key_options": ["cha"], "base_skills": [], "free_skills": 3, "spellcasting": "bounded_spontaneous", "traditions": ["arcane", "divine", "occult", "primal"], "starting_spells": {"cantrips": 5, "lvl1": 2}, "subclass_label": "Eidolon", "subclasses": ["Beast", "Construct", "Demon", "Devotion", "Dragon", "Fey", "Plant", "Undead"] },
    "animist": { "key_options": ["wis"], "base_skills": ["nature", "religion"], "free_skills": 2, "spellcasting": "prepared", "traditions": ["divine", "primal"], "starting_spells": {"cantrips": 5, "lvl1": 2} },
    "exemplar": { "key_options": ["str", "dex"], "base_skills": ["athletics"], "free_skills": 3 },
    "commander": { "key_options": ["int"], "base_skills": ["society"], "free_skills": 4 },
    "guardian": { "key_options": ["str"], "base_skills": ["athletics"], "free_skills": 3 }
}

BUILDER_DATA = {
    "heritages": {
        "universal": [],
        "human": [{"name": "Versatile Heritage", "desc": "You meet the prerequisites for a general feat of your choice, and you gain that feat."}, {"name": "Half-Elf", "desc": "You have elven blood. You gain the elf trait and low-light vision."}, {"name": "Half-Orc", "desc": "You have orcish blood. You gain the orc trait and low-light vision."}, {"name": "Skilled Heritage", "desc": "You become trained in one skill of your choice. At 5th level, you become an expert in it."}, {"name": "Wintertouched", "desc": "You gain cold resistance equal to half your level (minimum 1)."}],
        "elf": [{"name": "Arctic Elf", "desc": "You gain cold resistance equal to half your level (minimum 1)."}, {"name": "Cavern Elf", "desc": "You gain darkvision."}, {"name": "Seer Elf", "desc": "You can cast detect magic as an innate arcane cantrip at will."}, {"name": "Whisper Elf", "desc": "You gain a +2 circumstance bonus to locate undetected creatures that you could hear within 30 feet."}, {"name": "Woodland Elf", "desc": "You can always Take Cover when you are in forest terrain, even without standard cover."}],
        "dwarf": [{"name": "Ancient-Blooded", "desc": "You gain the Call on Ancient Blood reaction to resist magical effects."}, {"name": "Death Warden", "desc": "If you roll a success on a saving throw against a necromancy effect, you get a critical success instead."}, {"name": "Forge", "desc": "You gain fire resistance equal to half your level (minimum 1)."}, {"name": "Rock", "desc": "You gain a +2 circumstance bonus to your Fortitude or Reflex DC against attempts to Shove or Trip you."}, {"name": "Strong-Blooded", "desc": "You gain poison resistance equal to half your level (minimum 1)."}],
        "halfling": [{"name": "Gutsy", "desc": "If you roll a success on a saving throw against an emotion effect, you get a critical success instead."}, {"name": "Hillock", "desc": "When you regain Hit Points overnight, add your level to the Hit Points regained."}, {"name": "Nomadic", "desc": "You gain two additional languages and become trained in a Lore skill."}, {"name": "Twilight", "desc": "You gain low-light vision."}, {"name": "Wildwood", "desc": "You ignore difficult terrain from non-magical foliage."}],
        "goblin": [{"name": "Charhide", "desc": "You gain fire resistance equal to half your level (minimum 1)."}, {"name": "Irongut", "desc": "You gain a +2 circumstance bonus against afflictions from food or drink."}, {"name": "Monkey", "desc": "You gain a climb speed of 10 feet."}, {"name": "Snow", "desc": "You gain cold resistance equal to half your level (minimum 1)."}, {"name": "Tailed", "desc": "You have a prehensile tail that can perform simple Interact actions."}],
        "gnome": [{"name": "Chameleon", "desc": "You gain a +2 circumstance bonus to Stealth checks when you are motionless."}, {"name": "Fey-Touched", "desc": "You can cast a single primal cantrip of your choice as an innate spell."}, {"name": "Sensate", "desc": "You gain imprecise scent with a range of 30 feet."}, {"name": "Umbral", "desc": "You gain darkvision."}, {"name": "Wellspring", "desc": "You can cast a single arcane, divine, or occult cantrip of your choice."}],
        "orc": [{"name": "Badlands", "desc": "You gain fire resistance equal to half your level (minimum 1)."}, {"name": "Deep", "desc": "You gain darkvision."}, {"name": "Hold-Scarred", "desc": "You gain 12 Hit Points from your ancestry instead of 10, and gain the Diehard feat."}, {"name": "Rainfall", "desc": "You gain a +2 circumstance bonus to saving throws against diseases."}, {"name": "Winter", "desc": "You gain cold resistance equal to half your level (minimum 1)."}],
        "leshy": [{"name": "Fungus", "desc": "You gain darkvision."}, {"name": "Gourd", "desc": "Your head is a gourd with a permanent light cantrip. You gain resistance to fire equal to half your level."}, {"name": "Leaf", "desc": "You gain a +2 circumstance bonus to Acrobatics checks to Maneuver in Flight."}, {"name": "Vine", "desc": "You can extend vines, gaining a climb speed of 10 feet."}, {"name": "Cactus", "desc": "You are covered in spines. Grappling creatures take 1d6 piercing damage."}],
        "catfolk": [{"name": "Clawed", "desc": "Your claws are sharp. You gain a claw unarmed attack that deals 1d6 slashing."}, {"name": "Hunting", "desc": "You gain a +2 circumstance bonus to Survival checks to Track."}, {"name": "Jungle", "desc": "You gain a climb speed of 10 feet."}, {"name": "Nine Lives", "desc": "You gain the Cat's Luck reaction."}, {"name": "Winter", "desc": "You gain cold resistance equal to half your level (minimum 1)."}],
        "kobold": [{"name": "Caveclimber", "desc": "You gain a climb speed of 10 feet."}, {"name": "Dragonscaled", "desc": "You gain resistance to the damage type of your draconic exemplar (5 + half your level)."}, {"name": "Spellscale", "desc": "You can cast a single arcane cantrip of your choice as an innate spell."}, {"name": "Strongjaw", "desc": "Your jaws are powerful. You gain a jaws unarmed attack that deals 1d6 piercing."}, {"name": "Venomtail", "desc": "You gain a tail attack that can deliver venom."}],
        "tengu": [{"name": "Dogtooth", "desc": "You gain a beak unarmed attack that deals 1d6 piercing damage."}, {"name": "Jinxed", "desc": "You can cast ill omen as an innate occult cantrip."}, {"name": "Mountainkeeper", "desc": "You gain a +2 circumstance bonus to Athletics checks to Climb."}, {"name": "Skyborn", "desc": "You gain a +2 circumstance bonus to Acrobatics checks to Maneuver in Flight."}, {"name": "Stormtossed", "desc": "You gain electricity resistance equal to half your level (minimum 1)."}],
        "ratfolk": [{"name": "Deep Rat", "desc": "You gain darkvision."}, {"name": "Desert Rat", "desc": "You gain fire resistance equal to half your level (minimum 1) and environmental heat protection."}, {"name": "Longsnout", "desc": "You gain imprecise scent with a range of 30 feet."}, {"name": "Sewer Rat", "desc": "You gain a +1 circumstance bonus to saving throws against diseases and poisons."}, {"name": "Shadow Rat", "desc": "You gain a +2 circumstance bonus to Stealth checks in dim light."}],
        "lizardfolk": [{"name": "Cliffscale", "desc": "You gain a climb speed of 15 feet."}, {"name": "Frilled", "desc": "You gain a frilled display Intimidation action."}, {"name": "Sandstrider", "desc": "You ignore difficult terrain from sand and gravel."}, {"name": "Unseen", "desc": "You can change your skin color. You gain a +2 circumstance bonus to Stealth checks in natural environments."}, {"name": "Wetlander", "desc": "You gain a swim speed of 15 feet."}],
        "kitsune": [{"name": "Celestial Envoy", "desc": "You can cast divine lance as a divine innate cantrip."}, {"name": "Dark Fields", "desc": "You gain darkvision."}, {"name": "Earthly Wilds", "desc": "You can cast know direction as a primal innate cantrip."}, {"name": "Frozen Wind", "desc": "You gain cold resistance equal to half your level (minimum 1)."}, {"name": "Foxfire", "desc": "You can produce foxfire, a magical flame."}],
        "android": [{"name": "Artisan", "desc": "You become trained in Crafting."}, {"name": "Impersonator", "desc": "You gain a +2 circumstance bonus to Deception checks to Impersonate a specific person."}, {"name": "Laborer", "desc": "You gain a +2 circumstance bonus to Athletics checks to Force Open and Shove."}, {"name": "Polyglot", "desc": "You gain two additional languages."}, {"name": "Warrior", "desc": "You become trained in all martial weapons."}],
        "fetchling": [{"name": "Bright", "desc": "Your body casts light. You gain the light cantrip as an innate occult spell."}, {"name": "Deep", "desc": "You gain darkvision."}, {"name": "Liminal", "desc": "You can Step into an extradimensional space adjacent to your position, ignoring difficult terrain."}, {"name": "Resolute", "desc": "You gain a +1 circumstance bonus to saving throws against emotion effects."}, {"name": "Wisp", "desc": "You gain a +2 circumstance bonus to Stealth checks in dim light or darkness."}],
        "automaton": [{"name": "Hunter", "desc": "You gain a +2 circumstance bonus to Survival checks to Track."}, {"name": "Mage", "desc": "You can cast a single arcane cantrip of your choice."}, {"name": "Sharpshooter", "desc": "You gain a +2 circumstance bonus to attacks of opportunity with ranged weapons."}, {"name": "Warrior", "desc": "You gain a +1 circumstance bonus to Athletics checks to Shove and Trip."}],
        "fleshwarp": [{"name": "Created", "desc": "You were intentionally crafted. You gain a +2 circumstance bonus to saving throws against transmutation effects."}, {"name": "Mutated", "desc": "You gain a claw or jaws unarmed attack dealing 1d6 damage."}, {"name": "Shapewrought", "desc": "You gain a +2 circumstance bonus to Deception checks to Impersonate."}, {"name": "Surgewise", "desc": "You gain a +2 circumstance bonus to Medicine checks."}],
        "gnoll": [{"name": "Great Gnoll", "desc": "You are Large size."}, {"name": "Sweetbreath", "desc": "You gain a +2 circumstance bonus to Diplomacy checks."}, {"name": "Witch", "desc": "You can cast an occult cantrip of your choice."}, {"name": "Ant", "desc": "You gain a +2 circumstance bonus to Athletics checks to Climb."}],
        "grippli": [{"name": "Poisonhide", "desc": "You secrete a mild toxin. Creatures that grapple you become sickened 1."}, {"name": "Snatcher", "desc": "Your tongue is prehensile and can grab small objects."}, {"name": "Stickytoe", "desc": "You gain a climb speed of 10 feet."}, {"name": "Windweb", "desc": "You gain a +2 circumstance bonus to Acrobatics checks to Balance."}],
        "poppet": [{"name": "Ghost", "desc": "You are partially translucent. You gain a +1 circumstance bonus to Stealth."}, {"name": "Stuffed", "desc": "You gain resistance to bludgeoning damage equal to half your level."}, {"name": "Toy", "desc": "You are Tiny instead of Small."}, {"name": "Windup", "desc": "You gain a +2 circumstance bonus to saving throws against effects that would make you fatigued."}]
    },
    "classes": copy.deepcopy(RICH_CLASS_DATA),
    "subclass_matrix": SUBCLASS_MATRIX
}

def safe_int(val, default=0):
    try: return int(float(val)) if val is not None else default
    except: return default

def safe_str(val, default=""):
    return str(val) if val is not None else default

def get_nested_val(data_dict, keys, default=0):
    if not isinstance(data_dict, dict): return default
    for k in keys:
        if k in data_dict:
            v = data_dict[k]
            if isinstance(v, dict) and 'value' in v:
                return v['value']
            if v is not None:
                return v
    return default

def clean_foundry_text(text):
    if not isinstance(text, str): return ""
    text = re.sub(r'@Localize\[.*?\]', '', text)
    text = re.sub(r'@\w+\[.*?\]\{(.*?)\}', r'\1', text)
    def extract_name(match): return match.group(1).split('.')[-1]
    text = re.sub(r'@\w+\[(.*?)\]', extract_name, text)
    return text.strip()

def _load_kineticist_impulse_data():
    """name (lowercased) -> {'actions': glyph/text, 'desc': cleaned full text} for
    every kineticist impulse, read straight from the committed Foundry compendium.

    Impulses are feats/actions, not spells, so they're absent from the master
    spell list and the name 'Elemental Blast' collides with a focus spell of the
    same name. Resolving impulse cost + description from the authoritative
    per-ability files (rather than the name-keyed spell path) fixes both. Returns
    {} if compendium_data is absent so the module still imports."""
    base = os.path.dirname(os.path.abspath(__file__))
    out = {}
    patterns = [
        os.path.join(base, 'compendium_data', 'actions', '**', 'kineticist', '**', '*.json'),
        os.path.join(base, 'compendium_data', 'feats', '**', 'kineticist', '**', '*.json'),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            try:
                with open(path, encoding='utf-8') as fp:
                    d = json.load(fp)
            except (OSError, ValueError):
                continue
            if not isinstance(d, dict) or not d.get('name'):
                continue
            system = d.get('system') or {}
            name = d['name'].strip().lower()
            cost = foundry_action_cost(system)
            d_obj = system.get('description')
            desc = clean_foundry_text(d_obj.get('value', '') if isinstance(d_obj, dict) else '')
            # The compendium often leads with the action-cost line ("1 or 2"
            # glyphs) + an <hr>; the cost renders as a separate badge, so drop it.
            m = re.match(r'^\s*<p>\s*<span[^>]*action-glyph.*?</p>\s*(?:<hr\s*/?>)?\s*', desc, re.I | re.S)
            if m:
                desc = desc[m.end():].lstrip()
            # On duplicates (e.g. class vs archetype) keep the richer description.
            if name not in out or len(desc) > len(out[name]['desc']):
                out[name] = {'actions': cost, 'desc': desc}
    return out


KINETICIST_IMPULSE_DATA = _load_kineticist_impulse_data()


def get_col(row, key, default=""):
    try: return row[key] if row[key] is not None else default
    except: return default

def safe_json_load(row, key, default):
    val = get_col(row, key, None)
    if not val: return default
    try: return json.loads(val)
    except: return default

def safe_load_json_file(file_path):
    """Safely load a JSON file with proper file handle management. Returns (data, error)."""
    try:
        with open(file_path, 'r', encoding='utf-8') as fp:
            return json.load(fp), None
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON: {e}"
    except OSError as e:
        return None, f"File error: {e}"
    except Exception as e:
        return None, f"Load error: {e}"

def extract_traits(raw_val):
    if not raw_val: return []
    if isinstance(raw_val, str):
        try:
            parsed = json.loads(raw_val)
            if isinstance(parsed, dict): return parsed.get('value', [])
            elif isinstance(parsed, list): return parsed
        except: pass
    elif isinstance(raw_val, dict): return raw_val.get('value', [])
    elif isinstance(raw_val, list): return raw_val
    return []

def get_rarity(sys_data, row, traits_list, default="common"):
    if isinstance(sys_data, dict):
        sys_traits = sys_data.get('traits', {})
        if isinstance(sys_traits, dict) and 'rarity' in sys_traits and sys_traits['rarity']:
            return str(sys_traits['rarity']).lower()
            
    traits_raw = get_col(row, 'traits', '{}')
    if isinstance(traits_raw, str) and traits_raw.startswith('{'):
        try:
            parsed = json.loads(traits_raw)
            if isinstance(parsed, dict) and 'rarity' in parsed and parsed['rarity']:
                return str(parsed['rarity']).lower()
        except: pass
        
    for r in ['common', 'uncommon', 'rare', 'unique']:
        if r in [str(t).lower() for t in traits_list]:
            return r
    return default.lower()

def _build_pc_file_cache():
    """Rebuild the name->filename mapping so we don't re-parse every JSON on every API call."""
    _PC_FILE_CACHE.clear()
    if not os.path.exists(PARTY_DIR): return
    for f in os.listdir(PARTY_DIR):
        if not f.endswith('.json'): continue
        try:
            with open(os.path.join(PARTY_DIR, f), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            if isinstance(data, list):
                for item in data:
                    name = (item.get('build') or item).get('name')
                    if name: _PC_FILE_CACHE[name] = f
            else:
                name = (data.get('build') or data).get('name')
                if name: _PC_FILE_CACHE[name] = f
        except: pass

def get_pc_file_path(pc_name):
    """Get the file path for a character by name using the cache. Falls back to safe-name."""
    if pc_name in _PC_FILE_CACHE:
        return os.path.join(PARTY_DIR, _PC_FILE_CACHE[pc_name])
    # Cache miss - rebuild and retry
    _build_pc_file_cache()
    if pc_name in _PC_FILE_CACHE:
        return os.path.join(PARTY_DIR, _PC_FILE_CACHE[pc_name])
    # Still not found - fall back to sanitized name
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', pc_name)
    return os.path.join(PARTY_DIR, f"{safe_name}.json")

def reload_single_character(file_path):
    """Reload just one character file into PARTY_LIBRARY instead of the entire compendium."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            for idx, char_data in enumerate(data):
                pc = make_actor(char_data, f"{os.path.basename(file_path)}[{idx}]")
                PARTY_LIBRARY[pc.name] = pc
        else:
            pc = make_actor(data, os.path.basename(file_path))
            PARTY_LIBRARY[pc.name] = pc
    except Exception as e:
        print(f"Reload Error for {file_path}: {e}")

def save_and_reload_character(pc_name, pc_json, file_path):
    """Save a character JSON to disk and reload just that character (not the whole compendium)."""
    try:
        _atomic_write_json(file_path, pc_json, indent=4)
        # Update the cache in case the name or file changed
        _PC_FILE_CACHE[pc_name] = os.path.basename(file_path)
        reload_single_character(file_path)
        return True, None
    except OSError as e:
        print(f"[SAVE ERROR] {pc_name}: {e}")
        return False, str(e)
    except Exception as e:
        print(f"[SAVE ERROR] {pc_name}: {e}")
        return False, str(e)

def _persist_pc_combat_state(pc_name):
    """Mark a PC's combat state dirty. Background thread flushes to disk."""
    if pc_name in PARTY_LIBRARY:
        _PC_PERSIST_DIRTY.add(pc_name)

def _do_persist_pc_combat_state(pc_name):
    """Actually write HP/conditions/focus to disk. Called by flush thread."""
    if pc_name not in PARTY_LIBRARY:
        return
    # Snapshot the values under lock, then do file I/O unlocked
    with ENCOUNTER_LOCK:
        pc = PARTY_LIBRARY[pc_name]
        current_hp = pc.current_hp
        current_focus = getattr(pc, 'current_focus', 0)
        hero_points = int(getattr(pc, 'hero_points', 1) or 0)
        temp_hp_manual = max(0, int(getattr(pc, 'temp_hp_manual', 0) or 0))
        conditions = {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False}
        # Shield durability and raised state — both persist-worthy now that the
        # sheet + encounter tracker both read them.
        shield_raised = bool(getattr(pc, 'shield_raised', False))
        shield_hp = int(getattr(pc, 'shield_hp', 0) or 0)
        # Reaction-budget bookkeeping (Phase 3) — survives a server restart
        # mid-combat so players don't get their reaction back unfairly.
        reaction_used = bool(getattr(pc, 'reaction_used', False))
        # Persistent damage dicts (Phase 5)
        persistent_damage = list(getattr(pc, 'persistent_damage', []) or [])
        # Exploration activity (Phase 10)
        exploration_activity = str(getattr(pc, 'exploration_activity', '') or '')
        # Sheet-level Active Effects (engine schema). Survives a server
        # restart mid-buff so Heroism doesn't vanish after a deploy.
        pc_active_effects = list(getattr(pc, 'pc_active_effects', []) or [])
    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        build['current_hp'] = current_hp
        build['current_focus'] = current_focus
        build['hero_points'] = hero_points
        build['temp_hp'] = temp_hp_manual
        build['conditions'] = conditions
        build['shield_raised'] = shield_raised
        build['shield_hp'] = shield_hp
        build['reaction_used'] = reaction_used
        build['persistent_damage'] = persistent_damage
        build['exploration_activity'] = exploration_activity
        build['pc_active_effects'] = pc_active_effects
        _atomic_write_json(file_path, pc_json, indent=2)
    except Exception as e:
        print(f"[PERSIST ERROR] {pc_name}: {e}")

def _flush_pc_dirty(pc_name):
    """Synchronously flush any debounced combat-state writes for this PC
    before a read-modify-write of its file. The debounced persistence thread
    holds HP / conditions / shield / persistent_damage / active_effects in
    memory; a route that reads the file, mutates the *build*, writes it back,
    and reload_single_character()s would otherwise resurrect the last-persisted
    combat state and clobber the live values. Mirrors the guard that has long
    lived inline in update_pc_condition."""
    try:
        if pc_name in _PC_PERSIST_DIRTY:
            _do_persist_pc_combat_state(pc_name)
            _PC_PERSIST_DIRTY.discard(pc_name)
    except Exception:
        pass

# ---- UNIFIED PC STATE MUTATION ----
# Historically every endpoint hand-rolled the four-step dance:
#   mutate pc → sync encounter tracker → persist → broadcast.
# Result: drift (focus wasn't broadcast, temp_hp wasn't tracker-synced, etc.)
# and bugs where one endpoint forgot a step. `apply_pc_delta` centralizes it.
#
# Fields mirrored on the encounter tracker so initiative HUD stays fresh:
_TRACKER_MIRRORED_FIELDS = (
    'current_hp', 'current_focus', 'hero_points', 'temp_hp', 'reaction_used',
    'persistent_damage',
)

def _sync_tracker_from_pc(pc_name, *, conditions=True):
    """Copy mirrored fields + conditions from the PARTY_LIBRARY PC onto the
    matching combatant in ACTIVE_ENCOUNTER. Caller holds ENCOUNTER_LOCK."""
    if pc_name not in PARTY_LIBRARY:
        return
    pc = PARTY_LIBRARY[pc_name]
    for c in ACTIVE_ENCOUNTER:
        if not (c.is_pc and c.name == pc_name):
            continue
        for f in _TRACKER_MIRRORED_FIELDS:
            if hasattr(pc, f):
                try:
                    setattr(c, f, getattr(pc, f))
                except Exception:
                    pass
        if conditions:
            # Only the fields the tracker already cares about — don't invent
            # new keys that nothing else reads.
            for cond_key in ('dying', 'wounded', 'doomed', 'frightened',
                             'sickened', 'stunned', 'slowed', 'enfeebled',
                             'clumsy', 'drained', 'stupefied'):
                if cond_key in pc.conditions:
                    c.conditions[cond_key] = pc.conditions[cond_key]
            for bool_key in ('prone', 'off_guard', 'concealed', 'hidden'):
                if bool_key in pc.conditions:
                    c.conditions[bool_key] = pc.conditions[bool_key]
        break

def apply_pc_delta(pc_name, mutator, *, sync_conditions=True,
                   persist=True, broadcast=True):
    """One-stop state mutation. `mutator(pc)` runs under ENCOUNTER_LOCK and
    can return a value which we propagate back to the caller.

    After the mutation: tracker sync → persistence dirty-mark → SSE broadcast
    (coalesced). Callers no longer need to remember any of these steps.

    Returns (result, pc) or (None, None) if the PC doesn't exist.
    """
    if pc_name not in PARTY_LIBRARY:
        return None, None
    pc_in_encounter = False
    with ENCOUNTER_LOCK:
        pc = PARTY_LIBRARY[pc_name]
        try:
            result = mutator(pc)
        except Exception as e:
            print(f"[apply_pc_delta] {pc_name}: {e}")
            raise
        _sync_tracker_from_pc(pc_name, conditions=sync_conditions)
        # If this PC is in the active encounter, the tracker / player_view
        # listen on encounter_update — fire that too so HP changes from a
        # player sheet propagate to the GM screen without waiting for the
        # 10-s polling fallback.
        pc_in_encounter = any(c.is_pc and c.name == pc_name for c in ACTIVE_ENCOUNTER)
    if persist:
        _persist_pc_combat_state(pc_name)
    if broadcast:
        _broadcast_pc_state(pc_name)
        if pc_in_encounter:
            _broadcast_encounter_state()
    return result, pc

# --- REQUEST VALIDATION HELPERS ---
def require_pc(pc_name):
    """Validate that a PC exists. Returns (pc, file_path, error_response).
    If error_response is not None, return it immediately from the route."""
    if not pc_name:
        return None, None, (jsonify({'success': False, 'error': 'No character name provided'}), 400)
    if pc_name not in PARTY_LIBRARY:
        _sync_party_from_disk()  # Try reloading in case it was just added
        if pc_name not in PARTY_LIBRARY:
            return None, None, (jsonify({'success': False, 'error': f'Character "{pc_name}" not found'}), 404)
    file_path = get_pc_file_path(pc_name)
    return PARTY_LIBRARY[pc_name], file_path, None

def require_pc_json(pc_name):
    """Validate PC exists and load its JSON for modification. Returns (pc_json, file_path, error_response)."""
    pc, file_path, err = require_pc(pc_name)
    if err:
        return None, None, err
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        return pc_json, file_path, None
    except Exception as e:
        return None, None, (jsonify({'success': False, 'error': f'Failed to load character: {e}'}), 500)

def require_combatant(instance_id):
    """Validate that a combatant exists in the active encounter. Returns (combatant, index, error_response)."""
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        if c.instance_id == instance_id:
            return c, i, None
    return None, None, (jsonify({'success': False, 'error': 'Combatant not found in encounter'}), 404)

def _cosmere_initiative_mode():
    """The active campaign's Cosmere initiative house-rule: 'phases' (rulebook
    fast/slow, default) or 'traditional' (rolled d20+Speed order)."""
    try:
        m = _load_campaign_config().get('cosmere_initiative', 'phases')
        return 'traditional' if m == 'traditional' else 'phases'
    except Exception:
        return 'phases'


def _cosmere_world():
    """The active campaign's Cosmere visual world-skin: 'stormlight' (default) or
    'mistborn'. Pure theming -- the ruleset is unchanged either way."""
    try:
        w = _load_campaign_config().get('cosmere_world', 'stormlight')
        return 'mistborn' if w == 'mistborn' else 'stormlight'
    except Exception:
        return 'stormlight'


def _sort_cosmere_phases():
    """Order a Cosmere encounter into the 4-phase queue (Ch.10): fast_pc ->
    fast_npc -> slow_pc -> slow_npc; within a phase, higher Speed first, then
    the initiative d20."""
    import systems.cosmere.combat as _cc
    items = [{
        '_c': c, 'is_pc': bool(getattr(c, 'is_pc', False)),
        'choice': getattr(c, 'speed_choice', 'slow'),
        'speed': int((getattr(c, 'attributes', {}) or {}).get('spd', 0) or 0),
        'tiebreak': int(getattr(c, 'initiative', 0) or 0),
    } for c in ACTIVE_ENCOUNTER]
    ACTIVE_ENCOUNTER[:] = [it['_c'] for it in _cc.order_combatants(items)]


def _sort_encounter():
    global ACTIVE_ENCOUNTER, TURN_INDEX
    active_id = None
    if ACTIVE_ENCOUNTER and 0 <= TURN_INDEX < len(ACTIVE_ENCOUNTER):
        active_id = ACTIVE_ENCOUNTER[TURN_INDEX].instance_id

    # A pure-Cosmere encounter resolves in the 4-phase fast/slow queue -- UNLESS
    # the campaign house-rule is 'traditional' (a rolled d20+Speed initiative
    # order). Any PF2e (or mixed) encounter always keeps the initiative sort.
    pure_cosmere = bool(ACTIVE_ENCOUNTER) and all(getattr(c, 'system', 'pf2e') == 'cosmere' for c in ACTIVE_ENCOUNTER)
    if pure_cosmere and _cosmere_initiative_mode() == 'phases':
        _sort_cosmere_phases()
    else:
        ACTIVE_ENCOUNTER.sort(key=lambda x: x.initiative, reverse=True)
    if active_id:
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            if c.instance_id == active_id:
                TURN_INDEX = i; break
    else: TURN_INDEX = 0

def calculate_encounter_xp(encounter, party_level):
    """Total XP value of the non-PC creatures in `encounter` against
    a party of the given level. Single source of truth lives in
    class_matrix.ENCOUNTER_XP_BY_DIFF."""
    from class_matrix import ENCOUNTER_XP_BY_DIFF
    total_xp = 0
    for c in encounter:
        if not c.is_pc:
            lvl_diff = max(-4, min(4, c.level - party_level))
            total_xp += ENCOUNTER_XP_BY_DIFF.get(lvl_diff, 160 if lvl_diff > 4 else 10)
    return total_xp

def get_difficulty_label(xp, party_size=None):
    """PF2E encounter difficulty (GM Core p.74). Thresholds scale with
    party size — a 5-player Severe is 150 XP, a 3-player Severe is 90.
    party_size defaults to the actual PARTY_LIBRARY count if not given."""
    from class_matrix import ENCOUNTER_DIFFICULTY
    if party_size is None:
        party_size = max(1, len(PARTY_LIBRARY) or 4)
    th = {t["name"]: t["base"] + t["per_extra"] * (party_size - 4)
          for t in ENCOUNTER_DIFFICULTY}
    if   xp < th["Trivial"]:   return "Trivial",    "text-gray-400"
    elif xp < th["Low"]:       return "Low",        "text-green-400"
    elif xp < th["Moderate"]:  return "Moderate",   "text-yellow-400"
    elif xp < th["Severe"]:    return "Severe",     "text-orange-500"
    elif xp < th["Extreme"]:   return "Extreme",    "text-red-600 font-bold"
    else:                      return "Impossible", "text-red-600 font-bold animate-pulse"

class Character:
    def __init__(self, data, file_path=""):
        self.file_path = file_path
        self.instance_id = ""
        self.is_pc = True
        self.initiative = 0
        self.elite_weak = 0
        self.delaying = False
        # PCs are always visible to players; only NPCs/monsters get hidden.
        # Kept on the class for uniform access in the broadcast filter.
        self.visible_to_players = True
        
        build = data.get('build') or data
        self._build_ref = build
        if not isinstance(build, dict): build = {}

        # Which game system this actor belongs to (flat-additive envelope key;
        # defaults to pf2e for legacy/unstamped PCs). Dispatched on at load time.
        self.system = str((data.get('system') if isinstance(data, dict) else None) or 'pf2e').lower()

        self.name = safe_str(build.get('name'), 'Unknown Hero')
        self.level = safe_int(build.get('level'), 1)
        # Advancement state (GM XP/level award). xp is the PF2e XP toward the
        # next level (1000 = ready); ready_to_level is set by the GM (milestone)
        # or on XP rollover, and cleared on level-up.
        self.xp = safe_int(build.get('xp'), 0)
        self.ready_to_level = bool(build.get('ready_to_level'))
        # Automatic Bonus Progression is a PF2e *variant* rule (CRB Gamemastery
        # Guide). Pathbuilder doesn't apply it, so vanilla PF2e rolls were
        # silently inflating by +1 AC at L5+, +1/+2/+3 attacks at L2/L10/L16,
        # +1 dmg die at L4+. Default OFF so the engine matches PB out of
        # the box; opt in per-PC by setting `"abp_enabled": true` in the
        # build dict (or globally with the GM_PF2E_ABP env var).
        self.abp_enabled = bool(build.get('abp_enabled', False)) or os.environ.get('GM_PF2E_ABP', '').lower() in ('1', 'true', 'yes')
        self.class_name = safe_str(build.get('class'), 'Unknown Class')
        self.subclass = safe_str(build.get('subclass'), '')
        self.ancestry = safe_str(build.get('ancestry'), 'Unknown Ancestry')
        
        # Strip Pathbuilder placeholder entries from `specials` so downstream
        # logic (subclass detection, domain detection, label rendering) doesn't
        # pick them up. Common placeholders: "Not Selected", "—", "None".
        _PLACEHOLDER_SPECIALS = {'not selected', '—', 'none', '', 'select one'}
        if isinstance(build.get('specials'), list):
            build['specials'] = [s for s in build['specials']
                                 if isinstance(s, str) and s.lower().strip() not in _PLACEHOLDER_SPECIALS]
        # Auto-detect subclass from Pathbuilder 'specials' array if not set
        if not self.subclass:
            specials = build.get('specials') or []
            all_subclasses = set()
            cls_lower = self.class_name.lower()
            if cls_lower in RICH_CLASS_DATA:
                for s in RICH_CLASS_DATA[cls_lower].get('subclasses', []):
                    all_subclasses.add(s if isinstance(s, str) else s.get('name', ''))
            for s in SUBCLASS_MATRIX:
                all_subclasses.add(s)
            for special in specials:
                # Exact match
                if special in all_subclasses:
                    self.subclass = special
                    break
                # Partial match: "Justice Cause" → "Justice", "Animal Instinct" → "Animal"
                for sub_name in all_subclasses:
                    if special.startswith(sub_name) or sub_name in special:
                        self.subclass = sub_name
                        break
                if self.subclass:
                    break
        
        self.heritage = safe_str(build.get('heritage'), '')
        self.background = safe_str(build.get('background'), '')
        
        # Size: Pathbuilder has sizeName="Medium" and size=2 (int). Prefer sizeName.
        raw_size = build.get('sizeName') or build.get('size', '')
        SIZE_MAP_INT = {0: 'Tiny', 1: 'Small', 2: 'Medium', 3: 'Large', 4: 'Huge', 5: 'Gargantuan'}
        SIZE_MAP_STR = {'tiny': 'Tiny', 'sm': 'Small', 'small': 'Small', 'med': 'Medium', 'medium': 'Medium', 
                        'lg': 'Large', 'large': 'Large', 'huge': 'Huge', 'grg': 'Gargantuan', 'gargantuan': 'Gargantuan'}
        if isinstance(raw_size, int):
            self.size = SIZE_MAP_INT.get(raw_size, 'Medium')
        elif isinstance(raw_size, str) and raw_size.strip():
            self.size = SIZE_MAP_STR.get(raw_size.lower().strip(), raw_size.title() if len(raw_size) > 2 else 'Medium')
        else:
            self.size = ANCESTRY_SIZES.get(self.ancestry.lower(), 'Medium')
        
        self.notes = safe_str(build.get('notes'), '')
        self.portrait = safe_str(build.get('portrait'), '')
        # Portrait focus point (percent 0-100) so the circular crop can be
        # re-framed without re-uploading. Defaults to dead-center.
        _pf = build.get('portrait_focus') or {}
        try:
            self.portrait_focus_x = float(_pf.get('x', 50)) if isinstance(_pf, dict) else 50.0
            self.portrait_focus_y = float(_pf.get('y', 50)) if isinstance(_pf, dict) else 50.0
        except (TypeError, ValueError):
            self.portrait_focus_x = 50.0
            self.portrait_focus_y = 50.0
        self.active_toggles = build.get('active_toggles') or []
        self.shield_raised = build.get('shield_raised', False)
        # Phase 11: Auto-populate shield stats from Pathbuilder equipment.
        # PB stores shields as entries in build['armor'] with prof='shield' —
        # the stats (hardness/HP/BT/AC bonus) are NOT carried over, so we
        # derive them from SHIELD_TYPES by name. Explicit shield_* fields on
        # the build always win (so GM edits via set_shield_stats survive).
        pb_shield_name = None
        for _a in (build.get('armor') or []):
            if isinstance(_a, dict) and _a.get('worn') and str(_a.get('prof', '')).lower() == 'shield':
                pb_shield_name = safe_str(_a.get('name'), '')
                break
        pb_stats = _shield_stats_for(pb_shield_name) if pb_shield_name else None
        self.shield_name = pb_shield_name or safe_str(build.get('shield_name'), '')
        if pb_stats:
            pb_ac, pb_hard, pb_mhp, pb_bt = pb_stats
        else:
            pb_ac, pb_hard, pb_mhp, pb_bt = 2, 5, 20, 10
        self.shield_ac_bonus = safe_int(build.get('shield_ac_bonus'), pb_ac)
        # Shield durability — exposed so the sheet can render the gauge and
        # the inline Shield-Block prompt on damage can decide whether it's
        # even usable (broken/destroyed shields can't block).
        self.shield_max_hp = safe_int(build.get('shield_max_hp'), pb_mhp)
        self.shield_hp = safe_int(build.get('shield_hp'), self.shield_max_hp)
        self.shield_hardness = safe_int(build.get('shield_hardness'), pb_hard)
        self.shield_bt = safe_int(build.get('shield_bt'), max(1, self.shield_max_hp // 2))
        self.shield_broken = self.shield_hp <= self.shield_bt
        self.shield_destroyed = self.shield_hp <= 0
        # Reaction budget — reset at the start of each of the PC's turns.
        # Persists across server restarts so mid-combat reloads don't hand
        # the player a free reaction back.
        self.reaction_used = bool(build.get('reaction_used', False))
        # Per-turn persistent damage list (Phase 5).
        # Each entry: {'damage': '1d6', 'type': 'fire', 'source': 'Shocking Grasp'}
        # Defensive: older builds may have stored this as a string (legacy
        # monster format) or even the literal "[]". list("[]") yields
        # ['[',']'] — we coerce those cases back to an empty list here.
        _pd_raw_build = build.get('persistent_damage') or []
        if isinstance(_pd_raw_build, list):
            self.persistent_damage = [e for e in _pd_raw_build if isinstance(e, dict)]
        elif isinstance(_pd_raw_build, str):
            s = _pd_raw_build.strip()
            if s and s not in ('[]', '{}'):
                # Parse a free-form "1d6 fire" into a single-entry list so
                # no in-flight damage is silently dropped on migration.
                _parts = s.split(None, 1)
                dmg = _parts[0]
                typ = _parts[1] if len(_parts) > 1 else ''
                self.persistent_damage = [{'damage': dmg, 'type': typ, 'source': ''}]
            else:
                self.persistent_damage = []
        else:
            self.persistent_damage = []
        # Phase 10: Exploration activity (PF2e Core p.479). Stored as a short
        # string key — the UI maps it to a label + tooltip. Empty string means
        # the PC is not doing anything in particular (default: "Walk").
        self.exploration_activity = str(build.get('exploration_activity') or '')
        # Signature spells: stored as either a flat list (legacy) or a
        # {rank: name} dict (new, post-rank-cap). Expose both forms so
        # templates can iterate the flat names while validators consult
        # the rank map.
        _raw_sig = build.get('signature_spells') or []
        if isinstance(_raw_sig, dict):
            self.signature_map = {int(k): v for k, v in _raw_sig.items() if v}
            self.signature_spells = list(self.signature_map.values())
        else:
            self.signature_map = {}
            self.signature_spells = list(_raw_sig)
        # Champion Divine Ally choice (L3): "Blade Ally" | "Steed Ally" | "Shield Ally"
        # Persisted on the build so it shows on the sheet + survives reloads.
        self.divine_ally = safe_str(build.get('divine_ally'), '')
        self.session_notes = build.get('session_notes') or []
        self.expended_slots = build.get('expended_slots') or {}
        
        self.raw_feats = build.get('feats') or []
        self.raw_spellCasters = build.get('spellCasters') or []
        # Stash the raw build dict so downstream property getters (spell_attack
        # for focus-only classes like Champion) can look up build.focus etc.
        self._build_ref = build
        self.monk_paths = build.get('monk_paths', {})
        self.half_boosts = build.get('half_boosts') or []
        
        self.abilities = build.get('abilities') or {}
        # Pathbuilder exports per-skill typed bonuses in a top-level `mods`
        # dict, e.g. {"Arcana": {"Item Bonus": 1}} for a Farlight Stone.
        # `self.mods` is about to be repurposed as the ability-modifier dict,
        # so stash the raw PB data here and fold it into self.rule_modifiers
        # after the rule engine is initialized. Without this, every skill
        # item/status/circumstance bonus encoded by Pathbuilder is dropped.
        _pb_skill_mods = build.get('mods') if isinstance(build.get('mods'), dict) else {}
        self.mods = {}
        self.ability_display = []
        
        # Detect format: Pathbuilder stores modifiers (0-7 range), our builder stores scores (8-24 range)
        # Use majority check: if 4+ of the 6 values are >= 8, treat as full scores
        raw_vals = [safe_int(v.get('value', 0) if isinstance(v, dict) else v, 0) for v in [self.abilities.get(k, 0) for k in ['str', 'dex', 'con', 'int', 'wis', 'cha']]]
        is_score_format = sum(1 for v in raw_vals if v >= 8) >= 4
        
        for k in ['str', 'dex', 'con', 'int', 'wis', 'cha']:
            v = self.abilities.get(k, 0)
            raw = safe_int(v.get('value', 0) if isinstance(v, dict) else v, 0)
            
            if is_score_format:
                # Full ability scores (10, 12, 14, etc.) — compute modifier
                mod = math.floor((raw - 10) / 2)
            else:
                # Pathbuilder format — raw value IS the modifier
                mod = raw
            
            self.mods[k] = mod
            
            display_mod = f"+{mod}" if mod >= 0 else str(mod)
            if k in self.half_boosts: display_mod += " (½)"
            self.ability_display.append({'label': k.upper(), 'mod': display_mod})

        # --- AUTOMATION: THE RULE ENGINE PARSER ---
        # proficiencies must be initialized before the rule engine since feats can modify them
        self.proficiencies = build.get('proficiencies') or {}

        # Normalize Pathbuilder camelCase proficiency keys to snake_case
        PB_KEY_MAP = {'classDC': 'class_dc', 'castingArcane': 'spell_attack', 'castingDivine': 'spell_attack',
                      'castingOccult': 'spell_attack', 'castingPrimal': 'spell_attack'}
        for pb_key, norm_key in PB_KEY_MAP.items():
            if pb_key in self.proficiencies:
                val = safe_int(self.proficiencies[pb_key])
                if val > 0:
                    self.proficiencies[norm_key] = max(self.proficiencies.get(norm_key, 0), val)
                    # Also set spell_dc from casting proficiency if not already set
                    if pb_key.startswith('casting') and val > 0:
                        self.proficiencies['spell_dc'] = max(self.proficiencies.get('spell_dc', 0), val)

        # Parse Pathbuilder lores array into proficiencies.
        #
        # Two bugs fixed here:
        #  (1) Empty lore names (e.g. a placeholder "Not Selected" slot that
        #      Pathbuilder exports as ["", 2]) used to become "lore:" and render
        #      as a ghost "Lore: Not Selected" Trained entry on the sheet.
        #  (2) `if key not in self.proficiencies` skipped overwrite when a prior
        #      source had already set the lore (typically to 0). The lores array
        #      is authoritative for lore ranks, so we now TAKE THE MAX — that
        #      way Pathbuilder's Expert-level lores aren't clobbered back down
        #      to Untrained by stale zeros.
        for lore_entry in (build.get('lores') or []):
            if isinstance(lore_entry, (list, tuple)) and len(lore_entry) >= 2:
                lore_name = str(lore_entry[0]).lower().strip()
                if not lore_name:
                    continue  # ignore empty / "Not Selected" placeholders
                lore_rank = safe_int(lore_entry[1], 0)
                if lore_rank <= 0:
                    continue  # untrained lores are implicit; skip to avoid noise
                key = f"lore:{lore_name}"
                existing = safe_int(self.proficiencies.get(key, 0))
                if lore_rank > existing:
                    self.proficiencies[key] = lore_rank
        
        # --- AUTO PROFICIENCY BUMPS ---
        # Apply class-based proficiency progression (saves, weapons, armor, perception, DCs)
        # This guarantees correct proficiency ranks regardless of Pathbuilder data quality
        cls_lower = self.class_name.lower()
        cls_data = CLASS_MATRIX.get(cls_lower, {})
        base_profs = cls_data.get('base_proficiencies', {})
        
        # Start with base proficiencies from CLASS_MATRIX (level 1 values)
        self._class_profs = dict(base_profs)
        
        # Apply CLASS_PROGRESSION bumps up to current level
        cumulative_bumps = get_class_proficiency_at_level(cls_lower, self.level, subclass=self.subclass)
        for key, val in cumulative_bumps.items():
            self._class_profs[key] = max(self._class_profs.get(key, 0), val)

        # Merge into self.proficiencies. For SAVE proficiencies (fort/ref/will)
        # we trust the Pathbuilder export verbatim — PB's number IS the correct
        # number per their tables, including any heritage/feat-granted bumps
        # we don't model. For other combat profs we still take max so feats
        # like Armor Proficiency can lift unmodeled values up.
        #
        # The PB export is the source of truth. Falling through to max(saved,
        # computed) bakes in stale class-progression values from the buggy era
        # (e.g., Champion L3 reflex=4 from when class_matrix had a phantom L3
        # bump). We honor PB's exported value when present and only fill in
        # from CLASS_PROGRESSION when PB didn't send one.
        TRUST_PB_PROF_KEYS = {'fortitude', 'reflex', 'will'}
        COMBAT_PROF_KEYS = {'fortitude', 'reflex', 'will', 'perception', 'ac',
                            'unarmored', 'light', 'medium', 'heavy',
                            'unarmed', 'simple', 'martial', 'advanced',
                            'class_dc', 'spell_attack', 'spell_dc'}
        # PB-imported PCs always have a `proficiencies` block; from-scratch
        # builds may not. Detect "PB-shaped" by presence of the camelCase
        # casting* keys or attributes block.
        is_pb_shaped = bool(build.get('attributes')) or any(
            k in (build.get('proficiencies') or {})
            for k in ('castingArcane', 'castingDivine', 'castingOccult', 'castingPrimal', 'classDC')
        )
        for key in COMBAT_PROF_KEYS:
            computed = self._class_profs.get(key, 0)
            current = safe_int(self.proficiencies.get(key, 0))
            if key in TRUST_PB_PROF_KEYS and is_pb_shaped and key in (build.get('proficiencies') or {}):
                # Trust PB's value verbatim for saves — don't bump above it.
                self.proficiencies[key] = current
            elif computed > 0:
                self.proficiencies[key] = max(current, computed)
        
        # Compute AC proficiency from best armor proficiency the character actually uses.
        #
        # IMPORTANT: read from the MERGED self.proficiencies (which already contains
        # Pathbuilder import + class progression), not from self._class_profs alone.
        # The class matrix only carries the *base* class proficiencies — e.g. cleric
        # has light/medium/heavy = 0, so a Warpriest who is trained in light armor
        # via their doctrine would otherwise end up with AC prof = 0 here, which
        # knocks level+2 off their AC. Same for Sorcerers with Bloodline armor, etc.
        # Pathbuilder-format fallback: PB never populates build['armor_name']
        # or build['armor_str_req']. The worn armor lives in build['armor']
        # as a list of {name, prof, worn, ...}. Derive both here so AC prof,
        # armor check penalty, and speed penalty all work for PB imports —
        # without this the AC cat stays "unarmored" (missing level+2 from
        # the worn armor's proficiency) and str_req stays 0 (making the
        # "meets str req" comparison always true).
        armor_name = build.get('armor_name', '') or ''
        armor_cat = 'unarmored'
        if not armor_name:
            for a in (build.get('armor') or []):
                if isinstance(a, dict) and a.get('worn') and a.get('prof', '').lower() != 'shield':
                    armor_name = safe_str(a.get('name'), '')
                    pf = str(a.get('prof', '')).lower()
                    if pf in ('light', 'medium', 'heavy'): armor_cat = pf
                    break
        if armor_name and armor_cat == 'unarmored':
            for a in BUILDER_ARMOR:
                if a.get('name', '').lower() == armor_name.lower():
                    cat = a.get('category', 'unarmored').lower()
                    if cat in ('light', 'medium', 'heavy'): armor_cat = cat
                    break
        self._derived_armor_name = armor_name
        self._derived_armor_cat = armor_cat
        cat_prof = safe_int(self.proficiencies.get(armor_cat, 0))
        class_cat_prof = self._class_profs.get(armor_cat, 0)
        self.proficiencies['ac'] = max(
            safe_int(self.proficiencies.get('ac', 0)),
            cat_prof,
            class_cat_prof,
        )
        
        self.rule_modifiers = {}
        self.senses = []

        # Pathbuilder ancestry/heritage capabilities ride in build['specials']
        # (filtered to a clean list higher up). Orcs land "Darkvision" here,
        # Strix get "Low-Light Vision", etc. The rule engine and feat-text
        # parser below cover senses granted by feats, but they MISS senses
        # that come from ancestry/heritage alone — which is exactly the
        # darkvision case for Go'el (Orc) and Kyle (Awakened Animal).
        # Without this pass, the map tool's "PC has darkvision" probe
        # (see add_map_token) returns false for ancestry-darkvision PCs,
        # so their tokens behave as if blind in dark ambient.
        _SPECIAL_SENSE_MAP = {
            'darkvision':           'Darkvision',
            'greater darkvision':   'Greater Darkvision',
            'low-light vision':     'Low-Light Vision',
            'low-light':            'Low-Light Vision',
            'echolocation':         'Echolocation',
            'scent':                'Scent',
            'tremorsense':          'Tremorsense',
            'thoughtsense':         'Thoughtsense',
            'wavesense':            'Wavesense',
        }
        for _sp in (build.get('specials') or []):
            if not isinstance(_sp, str):
                continue
            _label = _SPECIAL_SENSE_MAP.get(_sp.lower().strip())
            if _label and _label not in self.senses:
                self.senses.append(_label)

        def add_mod(sel, m_type, val):
            if sel not in self.rule_modifiers: self.rule_modifiers[sel] = {'circumstance': [], 'status': [], 'item': [], 'untyped': []}
            if m_type not in self.rule_modifiers[sel]: m_type = 'untyped'
            self.rule_modifiers[sel][m_type].append(val)
            
        def resolve_val(v):
            if isinstance(v, (int, float)): return int(v)
            if isinstance(v, str):
                v_low = v.lower().replace(' ', '')
                if v_low == '@actor.level': return self.level
                if 'floor(@actor.level/2)' in v_low: return max(1, math.floor(self.level / 2))
                # ternary(gte(@actor.level,N),T,F) — used by e.g. Armor Proficiency
                # to upgrade past level 13. Left narrow on purpose; extend as needed.
                _t = re.match(r'ternary\(gte\(@actor\.level,(-?\d+)\),(-?\d+),(-?\d+)\)', v_low)
                if _t:
                    return int(_t.group(2)) if self.level >= int(_t.group(1)) else int(_t.group(3))
                try: return int(v)
                except: return 0
            if isinstance(v, dict) and 'brackets' in v:
                for b in v['brackets']:
                    if b.get('start', 1) <= self.level <= b.get('end', 20):
                        return resolve_val(b.get('value', 0))
            return 0

        # Build the source list as (base_name, hint) tuples. Pathbuilder
        # encodes ChoiceSet selections in the equipment name via a colon
        # suffix — e.g. "Orc Warmask: Magic" means the user picked the
        # "Magic" choice (which maps to Arcana). Splitting here lets pass 1
        # honor that explicit choice instead of falling back to a heuristic.
        source_items = []
        def _add_src(n):
            if not n: return
            n_str = str(n).strip()
            if not n_str: return
            if ':' in n_str:
                base, _, after = n_str.partition(':')
                source_items.append((base.strip(), after.strip().lower()))
            else:
                source_items.append((n_str, None))

        for n in [self.ancestry, self.heritage, self.class_name, self.subclass, self.background]:
            _add_src(n)
        for f in self.raw_feats:
            if isinstance(f, list) and len(f) > 0: _add_src(f[0])
            elif isinstance(f, dict): _add_src(f.get('name', ''))
        for eq in (build.get('equipment') or []):
            if isinstance(eq, dict): _add_src(eq.get('name', ''))
            elif isinstance(eq, list) and len(eq) > 0: _add_src(eq[0])
        for w in (build.get('weapons') or []):
            if isinstance(w, dict): _add_src(w.get('name', ''))
        _add_src(build.get('armor_name', ''))

        # PASS 1 — ChoiceSet resolution, two sub-passes:
        #   A) Hinted sources first. If a source name carries a colon suffix
        #      (e.g. "Orc Warmask: Magic"), try to match that suffix against
        #      each choice's label — the last dotted segment of the label is
        #      what Pathbuilder displays ("PF2E.…OrcWarmask.Magic" → "Magic").
        #      This is how we route Orc Warmask's +1 to arcana specifically,
        #      not to whatever tradition the PC happens to be best at.
        #   B) Highest-trained fallback. For any flag still unresolved, pick
        #      the choice whose value matches the PC's highest proficiency
        #      rank — useful for older exports where Pathbuilder didn't put
        #      the choice in the name.
        item_choices = {}  # {'orc warmask': {'tradition': 'arcana'}}

        def _camel_flag(name):
            """Foundry's implicit flag when a ChoiceSet omits `flag`: take
            the item slug and camelCase it. "Armor Proficiency" →
            "armorProficiency" — matches what the sibling ActiveEffectLike
            references via `flags.pf2e.rulesSelections.armorProficiency`."""
            parts = re.split(r'[\s\-_]+', str(name).strip())
            if not parts: return ''
            return parts[0].lower() + ''.join(p[:1].upper() + p[1:].lower() for p in parts[1:] if p)

        def _iter_choicesets(base_name):
            """Yield (rule, flag) pairs for each ChoiceSet rule on base_name."""
            r_list = COMPENDIUM_RULES.get(base_name.lower()) or []
            implicit = _camel_flag(base_name)
            for rule in r_list:
                if not isinstance(rule, dict): continue
                if rule.get('key') != 'ChoiceSet': continue
                flag = rule.get('flag') or implicit
                if flag:
                    yield rule, str(flag)

        def _eval_predicate(pred, profs):
            """Evaluate a Foundry rule predicate against current proficiencies.

            Supports "defense:<cat>:rank:<N>" atoms (Foundry rank 0/1/2/3/4
            mapped to app rank 0/2/4/6/8) plus {"not": p}, {"nor": [...]},
            {"and": [...]}, {"or": [...]}, and bare lists (implicit AND).
            Unknown atoms return False — conservative so we don't
            accidentally auto-select a choice we don't understand."""
            if isinstance(pred, str):
                m = re.match(r'defense:(\w+):rank:(\d+)$', pred, re.IGNORECASE)
                if m:
                    cat, n = m.group(1).lower(), int(m.group(2))
                    return safe_int(profs.get(cat, 0)) == n * 2
                return False
            if isinstance(pred, dict):
                if 'not' in pred: return not _eval_predicate(pred['not'], profs)
                if 'nor' in pred: return not any(_eval_predicate(p, profs) for p in pred.get('nor') or [])
                if 'and' in pred: return all(_eval_predicate(p, profs) for p in pred.get('and') or [])
                if 'or'  in pred: return any(_eval_predicate(p, profs) for p in pred.get('or')  or [])
                return False
            if isinstance(pred, list):
                return all(_eval_predicate(p, profs) for p in pred)
            return False

        # Sub-pass A — hinted resolution.
        for base_name, hint in source_items:
            if not hint: continue
            src_lower = base_name.lower()
            for rule, flag in _iter_choicesets(base_name):
                if item_choices.get(src_lower, {}).get(flag): continue
                for c in rule.get('choices', []) or []:
                    if not isinstance(c, dict): continue
                    label = str(c.get('label', ''))
                    last_seg = label.rsplit('.', 1)[-1].strip().lower()
                    hint_l = hint.lower()
                    if hint_l == last_seg or (last_seg and last_seg in hint_l) or (hint_l and hint_l in last_seg):
                        val = c.get('value')
                        if val is not None:
                            item_choices.setdefault(src_lower, {})[flag] = str(val).lower()
                            break

        # Sub-pass A.5 — predicate-driven resolution. Some ChoiceSet rules
        # attach a `predicate` to each choice (e.g. Armor Proficiency's
        # "pick light if rank 0, medium if already light, heavy if already
        # medium") — the first choice whose predicate evaluates True is the
        # intended selection. Only fires when Sub-pass A didn't already
        # resolve the flag, so explicit Pathbuilder hints still win.
        for base_name, _ in source_items:
            src_lower = base_name.lower()
            for rule, flag in _iter_choicesets(base_name):
                if item_choices.get(src_lower, {}).get(flag): continue
                for c in rule.get('choices', []) or []:
                    if not isinstance(c, dict): continue
                    pred = c.get('predicate')
                    if pred is None: continue
                    if _eval_predicate(pred, self.proficiencies):
                        val = c.get('value')
                        if val is not None:
                            item_choices.setdefault(src_lower, {})[flag] = str(val).lower()
                            break

        # Sub-pass B — highest-trained fallback for anything still unresolved.
        for base_name, _ in source_items:
            src_lower = base_name.lower()
            for rule, flag in _iter_choicesets(base_name):
                if item_choices.get(src_lower, {}).get(flag): continue
                choice_values = []
                for c in rule.get('choices', []) or []:
                    if isinstance(c, dict) and c.get('value') is not None:
                        choice_values.append(str(c.get('value')).lower())
                    elif isinstance(c, str):
                        choice_values.append(c.lower())
                if not choice_values: continue
                best, best_rank = choice_values[0], -1
                for cv in choice_values:
                    r = safe_int(self.proficiencies.get(cv, 0))
                    if r > best_rank:
                        best, best_rank = cv, r
                item_choices.setdefault(src_lower, {})[flag] = best

        _tpl_sel_re = re.compile(r'\{item\|flags\.pf2e\.rulesSelections\.(\w+)\}', re.IGNORECASE)

        # PASS 2 — apply FlatModifier / Sense / ActiveEffectLike rules. We
        # iterate by base_name (stripping the ": suffix") so rules attached
        # to "Orc Warmask" are applied even when the only source entry is
        # the colon-variant equipment name "Orc Warmask: Magic". Dedup by
        # (base_name, id(rule)) so a rule doesn't fire twice if the same
        # base was reached via both feat and equipment entries.
        _seen_rule_firings = set()
        for src, _ in source_items:
            if not src: continue
            src_lower = str(src).lower()
            r_list = COMPENDIUM_RULES.get(src_lower) or []
            for rule in r_list:
                # Skip if we've already fired this exact rule object for
                # this base name — covers the case where both "Orc Warmask"
                # (from feats) and "Orc Warmask: Magic" (from equipment)
                # land on the same base lookup key.
                rule_sig = (src_lower, id(rule))
                if rule_sig in _seen_rule_firings:
                    continue
                _seen_rule_firings.add(rule_sig)
                try:
                    if not isinstance(rule, dict): continue
                    key = rule.get('key', '')
                    if key == 'FlatModifier':
                        selectors = rule.get('selector', [])
                        if isinstance(selectors, str): selectors = [selectors]
                        val = resolve_val(rule.get('value', 0))
                        m_type = rule.get('type', 'untyped').lower()
                        for s in selectors:
                            if not s: continue
                            s_str = str(s)
                            # Resolve {item|flags.pf2e.rulesSelections.X} using
                            # the selections we gathered in pass 1.
                            m = _tpl_sel_re.match(s_str)
                            if m:
                                flag = m.group(1)
                                resolved = item_choices.get(src_lower, {}).get(flag)
                                if resolved:
                                    add_mod(resolved, m_type, val)
                            else:
                                add_mod(s_str.lower(), m_type, val)
                    elif key == 'Sense':
                        s_type = rule.get('selector', rule.get('sense', {}).get('type', ''))
                        if s_type and s_type.title() not in self.senses: self.senses.append(s_type.title())
                    elif key == 'ActiveEffectLike' and rule.get('path') == 'system.attributes.speed.value':
                        val = resolve_val(rule.get('value', 0))
                        if rule.get('mode', 'add') == 'add': add_mod('speed', 'untyped', val)
                    elif key == 'ActiveEffectLike' and 'system.skills.' in str(rule.get('path', '')):
                        path = rule.get('path', '')
                        sk_match = re.search(r'system\.skills\.(\w+)\.rank', path)
                        if sk_match:
                            sk_name = sk_match.group(1).lower()
                            rank_val = resolve_val(rule.get('value', 1))
                            if rank_val <= 4:
                                pf2_rank = rank_val * 2
                            else:
                                pf2_rank = rank_val
                            mode = rule.get('mode', 'upgrade')
                            current = self.proficiencies.get(sk_name, 0)
                            if mode == 'upgrade':
                                self.proficiencies[sk_name] = max(current, pf2_rank)
                            elif mode == 'add':
                                self.proficiencies[sk_name] = current + pf2_rank
                    elif key == 'ActiveEffectLike' and 'system.proficiencies.defenses.' in str(rule.get('path', '')):
                        # e.g. Armor Proficiency feat:
                        #   path = "system.proficiencies.defenses.{item|flags.pf2e.rulesSelections.armorProficiency}.rank"
                        #   value = "ternary(gte(@actor.level,13),2,1)"
                        # The templated category is resolved via the ChoiceSet
                        # selection gathered in pass 1; literal paths
                        # (defenses.heavy.rank etc.) are also supported.
                        path = rule.get('path', '')
                        def_match = re.search(
                            r'system\.proficiencies\.defenses\.(?:\{item\|flags\.pf2e\.rulesSelections\.(\w+)\}|(\w+))\.rank',
                            path,
                        )
                        if def_match:
                            flag_name, literal = def_match.group(1), def_match.group(2)
                            if flag_name:
                                cat = item_choices.get(src_lower, {}).get(flag_name)
                            else:
                                cat = (literal or '').lower()
                            if cat:
                                rank_val = resolve_val(rule.get('value', 1))
                                pf2_rank = rank_val * 2 if rank_val <= 4 else rank_val
                                mode = rule.get('mode', 'upgrade')
                                current = safe_int(self.proficiencies.get(cat, 0))
                                if mode == 'upgrade':
                                    self.proficiencies[cat] = max(current, pf2_rank)
                                elif mode == 'add':
                                    self.proficiencies[cat] = current + pf2_rank
                                else:
                                    self.proficiencies[cat] = pf2_rank
                    elif key == 'ActiveEffectLike':
                        # Catch-all for other proficiency-upgrade rules that the
                        # earlier branches don't handle. Covers:
                        #   - system.saves.{fortitude,reflex,will}.rank
                        #     (Stalwart, Resolve, Juggernaut, etc.)
                        #   - system.attributes.{perception,classDC,ac}.rank
                        #   - system.attributes.spellDC.rank
                        # Without these, feats that upgrade saves/perception/AC
                        # proficiency silently fail to apply, and saves come out
                        # 2+ points low at level-ups.
                        path = str(rule.get('path', ''))
                        prof_key = None
                        # Saves — map to short keys used by _calc_save.
                        m = re.search(r'system\.saves\.(\w+)\.rank', path)
                        if m:
                            prof_key = m.group(1).lower()
                        else:
                            m = re.search(r'system\.attributes\.(\w+)\.rank', path)
                            if m:
                                attr = m.group(1).lower()
                                # classDC / spellDC / perception / ac are stored
                                # under those keys in self.proficiencies.
                                if attr in ('perception', 'classdc', 'class_dc',
                                            'spelldc', 'spell_dc', 'ac',
                                            'spellattack', 'spell_attack'):
                                    prof_key = {
                                        'classdc': 'class_dc',
                                        'spelldc': 'spell_dc',
                                        'spellattack': 'spell_attack',
                                    }.get(attr, attr)
                        if prof_key:
                            rank_val = resolve_val(rule.get('value', 1))
                            pf2_rank = rank_val * 2 if rank_val <= 4 else rank_val
                            mode = rule.get('mode', 'upgrade')
                            current = safe_int(self.proficiencies.get(prof_key, 0))
                            if mode == 'upgrade':
                                self.proficiencies[prof_key] = max(current, pf2_rank)
                            elif mode == 'add':
                                self.proficiencies[prof_key] = current + pf2_rank
                            else:
                                self.proficiencies[prof_key] = pf2_rank
                except Exception:
                    pass  # Don't let any single rule crash the entire character init

        # Apply Pathbuilder bonuses captured from build["mods"].
        #
        # PB exports several shapes in the wild — we need to accept them all:
        #   {"Arcana": {"Item Bonus": 1}}   ← per-skill typed bonuses
        #   {"Arcana": 1}                   ← bare numeric (assume untyped)
        #   {"AC": {"Status Bonus": 1}}     ← non-skill selectors (AC, Perception,
        #                                      Fortitude, "Attack Rolls", etc.)
        #   {"Perception": 1}               ← bare numeric on a non-skill selector
        #
        # Previously only the nested {skill: {type: val}} form was picked up,
        # and every bare-numeric / non-skill entry was dropped on the floor —
        # hence the "PB mods item bonuses dropped" report.
        _PB_MOD_TYPES = {
            'item bonus': 'item', 'item': 'item',
            'status bonus': 'status', 'status': 'status',
            'circumstance bonus': 'circumstance', 'circumstance': 'circumstance',
            'untyped': 'untyped', '': 'untyped',
        }
        # Normalize selector names so "Attack Rolls", "Spell DC", "Fortitude"
        # map onto keys we already use elsewhere (rule_modifiers / proficiencies).
        _PB_SELECTOR_ALIASES = {
            'attack rolls': 'attack', 'attack': 'attack',
            'saving throws': 'saving-throw', 'saving throw': 'saving-throw',
            'fortitude': 'fortitude', 'reflex': 'reflex', 'will': 'will',
            'perception': 'perception', 'ac': 'ac',
            'class dc': 'class_dc', 'spell dc': 'spell_dc',
            'spell attack': 'spell_attack', 'initiative': 'initiative',
        }
        def _coerce_pb_mod_type(raw):
            return _PB_MOD_TYPES.get(str(raw or '').strip().lower(), 'untyped')
        for _sk_name, _bonus in (_pb_skill_mods or {}).items():
            _sel = str(_sk_name).strip().lower()
            if not _sel: continue
            _sel = _PB_SELECTOR_ALIASES.get(_sel, _sel)
            if isinstance(_bonus, dict):
                for _btype, _amt in _bonus.items():
                    _val = safe_int(_amt, 0)
                    if _val:
                        add_mod(_sel, _coerce_pb_mod_type(_btype), _val)
            elif isinstance(_bonus, (int, float, str)):
                _val = safe_int(_bonus, 0)
                if _val:
                    add_mod(_sel, 'untyped', _val)

        self.feats = []
        self.immunities = []
        self.focus_max = safe_int((build.get('focus') or {}).get('pool'), 0)
        
        for feat in self.raw_feats:
            if isinstance(feat, list) and len(feat) > 1:
                f_name = safe_str(feat[0])
                f_level = 1
                f_type = ''
                
                # Pathbuilder format: [name, id, category, level, choice_label, choice_type, parent] (7 elements)
                # Our builder format: [name, type, level, description_string] (4 elements, feat[3] is long text)
                if len(feat) >= 4 and isinstance(feat[3], str) and len(feat[3]) > 15:
                    # Builder format — feat[3] is the full description text
                    f_desc = safe_str(feat[3])
                    f_level = safe_int(feat[2], 1)
                    f_type = safe_str(feat[1], '')
                else:
                    # Pathbuilder format — feat[3] is the level (int), feat[2] is category
                    if len(feat) >= 4: f_level = safe_int(feat[3], 1)
                    elif len(feat) >= 3: f_level = safe_int(feat[2], 1)
                    if len(feat) >= 3: f_type = safe_str(feat[2], '')
                    f_desc = COMPENDIUM_LIBRARY.get(f_name.lower(), "<em>Description not found in compendium.</em>")
                    
                self.feats.append({'name': f_name, 'desc': f_desc, 'level': f_level, 'type': f_type})
                
                lower_desc = f_desc.lower()
                if "focus point" in lower_desc and "maximum" in lower_desc: self.focus_max += 1
                if "darkvision" in lower_desc and "Darkvision" not in self.senses: self.senses.append("Darkvision")
                if "low-light vision" in lower_desc and "Low-Light vision" not in self.senses: self.senses.append("Low-Light Vision")

        self.current_focus = safe_int(build.get('current_focus'), self.focus_max)
        self.focus_points = self.focus_max  
        self.hero_points = safe_int(build.get('hero_points'), 1)
        
        self.deity = safe_str(build.get('deity'), 'None')
        self.sanctification = safe_str(build.get('sanctification'), 'Neutral')
        self.languages = build.get('languages') or ['Common']
        
        money = build.get('money') or {}
        self.pp = safe_int(money.get('pp'), 0)
        self.gp = safe_int(money.get('gp'), 15)
        self.sp = safe_int(money.get('sp'), 0)
        self.cp = safe_int(money.get('cp'), 0)

        w_raw = build.get('weapons')
        self._raw_weapons = w_raw if isinstance(w_raw, list) else []
        if not any(w.get('name') == 'Fist' for w in self._raw_weapons):
            self._raw_weapons.insert(0, {'name': 'Fist', 'attack_stat': 'str', 'damage': '1d4 B', 'traits': ['agile', 'finesse', 'nonlethal', 'unarmed']})

        # Pathbuilder weapons export `die` ('d8'), `damageType` ('S'), `prof`
        # ('martial'), and `display` ('Bastard Sword (2h)'). They DO NOT
        # export `damage`, `traits`, `is_two_handed`, or `prof_val` — the
        # fields the attacks() property needs. Without this enrichment,
        # every PB-imported weapon falls back to `1d4` damage, no traits,
        # and trained proficiency, regardless of the actual weapon. That's
        # what made Amadeus's Bastard Sword display as "1d4 + 4" instead
        # of "1d12 + 4" in the snapshot tests.
        for _w in self._raw_weapons:
            if not isinstance(_w, dict):
                continue
            # `traits` — look up by canonical name in BUILDER_WEAPONS so
            # finesse/agile/two-hand-d12/etc. all flow through.
            _name = str(_w.get('name') or '').strip().lower()
            _ref = next((bw for bw in BUILDER_WEAPONS
                         if str(bw.get('name', '')).strip().lower() == _name), None) if _name else None
            if not _w.get('traits') and _ref:
                _w['traits'] = _ref.get('traits') or []
            # `damage` — the base (one-handed) die. PB pre-bakes the
            # two-hand-d{N} swap into `die` when the PC happens to be wielding
            # the weapon 2h (Bastard Sword 2h → die='d12'), which DESTROYS the
            # 1h base. When the reference knows the real base AND the weapon has
            # a two-hand-d{N} trait, use the reference base so attacks() can
            # swap base ↔ 2h purely from is_two_handed (the grip toggle).
            # Otherwise both grips resolve to the baked die and the 1H/2H button
            # is a no-op. For non-two-hand weapons PB's `die` already is the base.
            _traits_l = [str(t).lower() for t in (_w.get('traits') or [])]
            _has_2h_die = any(t.startswith('two-hand-d') for t in _traits_l)
            if _has_2h_die and _ref and _ref.get('damage'):
                _w['damage'] = _ref['damage']
            elif not _w.get('damage'):
                _pb_die = str(_w.get('die') or '').strip()
                _pb_type = str(_w.get('damageType') or '').strip()
                if _pb_die:
                    _w['damage'] = f"1{_pb_die} {_pb_type}".strip()
                elif _ref and _ref.get('damage'):
                    _w['damage'] = _ref['damage']
            # 2-handed wielding flag: seed from PB's "(2h)" display every load
            # UNLESS the player has explicitly picked a grip via the toggle
            # (grip_user_set). A bare is_two_handed=False is ambiguous — it's
            # also the default written for a never-touched weapon — so gating on
            # grip_user_set keeps the import default ("(2h)" → two-handed) while
            # letting a real toggle stick. Re-forcing from display unconditionally
            # was what made the grip toggle appear to do nothing.
            if not _w.get('grip_user_set'):
                _disp = str(_w.get('display') or '').lower()
                _w['is_two_handed'] = ('(2h)' in _disp or 'two-hand' in _disp or 'two-handed' in _disp)
            # prof_val — PB stores the category in `prof`; map to the
            # PC's actual rank in that category. Without this, a Fighter
            # at L5 would still hit at trained rather than expert.
            if not _w.get('prof_val'):
                _pb_prof = str(_w.get('prof') or '').lower().strip()
                if _pb_prof in ('simple', 'martial', 'advanced', 'unarmed'):
                    _w['prof_val'] = safe_int(self.proficiencies.get(_pb_prof), 2)

        self.equipment = []
        for eq in (build.get('equipment') or []):
            if isinstance(eq, list) and len(eq) >= 2: 
                self.equipment.append({'name': safe_str(eq[0], 'Item'), 'qty': safe_int(eq[1], 1), 'bulk': safe_str(eq[2] if len(eq)>2 else '0')})
            elif isinstance(eq, dict): 
                self.equipment.append({'name': safe_str(eq.get('name'), 'Item'), 'qty': safe_int(eq.get('qty'), 1), 'bulk': safe_str(eq.get('bulk', '0'))})

        # Fall back to the PB-derived armor_name so bulk, AC item bonus,
        # and check/speed-penalty comparisons all operate on the worn
        # armor (not unarmored) for Pathbuilder-imported PCs.
        self.armor_name = safe_str(build.get('armor_name'), '') or getattr(self, '_derived_armor_name', '') or ''
        total_b = 0
        light_b = 0
        
        all_inventory = self.equipment + self._raw_weapons + ([{'bulk': str(build.get('armor_bulk', '0')), 'qty': 1}] if self.armor_name else [])
        
        for item in all_inventory:
            qty = safe_int(item.get('qty', 1), 1)
            b_str = str(item.get('bulk', '0')).upper()
            if b_str == 'L':
                light_b += qty
            else:
                total_b += safe_int(b_str) * qty
                
        self.total_bulk = total_b + math.floor(light_b / 10)
        self.light_bulk_remainder = light_b % 10
        
        self.encumbered_limit = 5 + self.mods.get('str', 0)
        self.max_bulk_limit = 10 + self.mods.get('str', 0)
        
        self.is_encumbered = self.total_bulk > self.encumbered_limit
        self.clumsy_penalty = 1 if self.is_encumbered else 0

        ac_data = build.get('acTotal') or {}
        self.ac_item = safe_int(build.get('ac_item'), safe_int(ac_data.get('acItemBonus'), 0))
        self.ac_dex_cap = safe_int(build.get('ac_dex_cap'), 99)
        self.armor_str_req = safe_int(build.get('armor_str_req'), 0)
        base_armor_penalty = abs(safe_int(build.get('armor_penalty'), abs(safe_int(ac_data.get('armorCheckPenalty'), 0))))
        base_speed_penalty = abs(safe_int(build.get('armor_speed_pen'), 0))
        self.armor_traits = build.get('armor_traits') or []

        # Pathbuilder doesn't export armor_str_req / armor_penalty /
        # armor_speed_pen — only the armor's name and prof category. Look
        # the worn armor up in BUILDER_ARMOR so that:
        #   - the str-req comparison below actually matters (was always
        #     True against 0, hiding check/speed penalties for underweight
        #     PCs in heavy armor)
        #   - ac_item, check penalty, and speed penalty are populated from
        #     the table when PB didn't provide them explicitly.
        if self.armor_name:
            _a_info = next((a for a in BUILDER_ARMOR if a.get('name', '').lower() == self.armor_name.lower()), None)
            if _a_info:
                # `str_req` in our armor table is the canonical PF2e value;
                # Pathbuilder routinely omits or zeros it. Trust the table
                # unless PB explicitly set a *non-zero* value.
                table_str_req = safe_int(_a_info.get('str_req'), 0)
                if self.armor_str_req == 0 and table_str_req > 0:
                    self.armor_str_req = table_str_req
                # For ac_item, penalty, and speed penalty: take the max of
                # what PB claimed and what the table says. Stale acTotal
                # values from PB (e.g. PC swapped armor in-game but acTotal
                # wasn't recomputed) used to silently win here.
                table_ac = safe_int(_a_info.get('ac'), 0)
                if table_ac > self.ac_item:
                    self.ac_item = table_ac
                table_penalty = abs(safe_int(_a_info.get('penalty'), 0))
                if table_penalty > base_armor_penalty:
                    base_armor_penalty = table_penalty
                table_speed_penalty = abs(safe_int(_a_info.get('speed_penalty'), 0))
                if table_speed_penalty > base_speed_penalty:
                    base_speed_penalty = table_speed_penalty
                if not self.armor_traits:
                    self.armor_traits = _a_info.get('traits') or []
                # ac_dex_cap follows the same Pathbuilder-omits-it pattern.
                # PB never exports this field, so it lands as 99 (the "no
                # cap" sentinel from line 2048). Without this fallback,
                # the AC formula at line 2690 took the full DEX mod even
                # in heavy/medium armor — Amadeus's chain mail AC came
                # out 1 too high (21 vs PB's own acTotal of 20). Only
                # tighten the cap; never widen it past what PB explicitly
                # set, in case the GM overrode the cap on a magical item.
                table_dex_cap = safe_int(_a_info.get('dex_cap'), 99)
                if self.ac_dex_cap >= 99 and 0 <= table_dex_cap < 99:
                    self.ac_dex_cap = table_dex_cap
        
        # BUILDER_ARMOR's str_req is stored as a *score* (e.g. chain mail
        # is 14). Pathbuilder-exported armor_str_req, when present, is
        # also a score. The comparison here is against the str *modifier*
        # — normalize by converting a score (anything >= 8) to a mod.
        # Without this, Chain Mail (str_req=14) vs Amadeus's str mod +4
        # evaluates as 4 >= 14 = False and penalties always apply, even
        # when the PC exceeds the actual Str 14 requirement.
        _str_req_mod = self.armor_str_req
        if _str_req_mod >= 8:
            _str_req_mod = math.floor((_str_req_mod - 10) / 2)
        if self.mods.get('str', 0) >= _str_req_mod:
            self.active_armor_penalty = 0
            self.active_speed_penalty = 0
            if 'noisy' in [str(t).lower() for t in self.armor_traits]:
                self.stealth_penalty = base_armor_penalty
            else:
                self.stealth_penalty = 0
        else:
            self.active_armor_penalty = base_armor_penalty
            self.active_speed_penalty = base_speed_penalty
            self.stealth_penalty = base_armor_penalty

        # --- AUTOMATION: THE CONDITION MATRIX ENGINE ---
        saved_conds = build.get('conditions', {})
        self.conditions = {
            'frightened': safe_int(saved_conds.get('frightened', 0)),
            'sickened': safe_int(saved_conds.get('sickened', 0)),
            'enfeebled': safe_int(saved_conds.get('enfeebled', 0)),
            'clumsy': safe_int(saved_conds.get('clumsy', 0)),
            'drained': safe_int(saved_conds.get('drained', 0)),
            'stupefied': safe_int(saved_conds.get('stupefied', 0)),
            'stunned': safe_int(saved_conds.get('stunned', 0)),
            'slowed': safe_int(saved_conds.get('slowed', 0)),
            'dying': safe_int(saved_conds.get('dying', 0)),
            'wounded': safe_int(saved_conds.get('wounded', 0)),
            'doomed': safe_int(saved_conds.get('doomed', 0)),
            'prone': saved_conds.get('prone', False),
            'off_guard': saved_conds.get('off_guard', False),
            'concealed': saved_conds.get('concealed', False),
            'hidden': saved_conds.get('hidden', False)
        }
        # Per-condition auto-expiry timer (rounds remaining). See Monster.__init__.
        self.condition_expiry = dict(build.get('condition_expiry', {}) or {})
        # Per-turn action economy for the GM-side combat HUD. PCs track their
        # own actions on the sheet, but a quick view in the tracker is useful
        # too. Reset to 0 / False at the start of each PC's turn (cycle_turn).
        self.actions_used = 0
        self.max_actions = 3

        attributes = build.get('attributes') or {}
        # Pathbuilder exports `ancestryhp` / `classhp` directly. When PB
        # provides a non-zero value, trust it: PB knows about Awakened Animal
        # variants, lineage HP swaps, etc. that the BUILDER tables don't
        # encode. The lookup below only fills in missing values for builds
        # that don't carry the attributes block (e.g. legacy non-PB imports).
        anc_hp = safe_int(attributes.get('ancestryhp'), 0)
        cls_hp = safe_int(attributes.get('classhp'), 0)

        if anc_hp <= 0:
            for anc_key in [self.ancestry, self.ancestry.lower(), self.ancestry.title()]:
                if anc_key in BUILDER_ANCESTRIES and BUILDER_ANCESTRIES[anc_key].get('hp'):
                    anc_hp = safe_int(BUILDER_ANCESTRIES[anc_key]['hp'], 0)
                    break
            if anc_hp <= 0:
                anc_hp = 8  # last-resort default

        if cls_hp <= 0:
            for cls_key in [self.class_name, self.class_name.lower(), self.class_name.title()]:
                if cls_key in BUILDER_CLASSES and BUILDER_CLASSES[cls_key].get('hp'):
                    cls_hp = safe_int(BUILDER_CLASSES[cls_key]['hp'], 0)
                    break
            if cls_hp <= 0:
                cls_hp = 8  # last-resort default
        bonus_hp = safe_int(attributes.get('bonushp'), 0)
        bonus_hp_per_level = safe_int(attributes.get('bonushpPerLevel'), 0)
        
        self._anc_hp = anc_hp
        self._cls_hp = cls_hp
        # Rule engine HP modifiers (e.g. Toughness adds @actor.level via FlatModifier)
        # But Pathbuilder already encodes Toughness as bonushpPerLevel=1, causing double-count.
        # Skip rule engine HP when bonushpPerLevel > 0 (PB already accounts for feat HP effects).
        hp_rule_mod = self.get_rule_mod('hp') if bonus_hp_per_level == 0 else 0
        self.hp = anc_hp + bonus_hp + ((cls_hp + self.mods.get('con', 0) + bonus_hp_per_level) * self.level) + hp_rule_mod
        # Note: Toughness HP bonus is handled by the rule engine via COMPENDIUM_RULES FlatModifier
        
        # Drained directly reduces Max HP
        drained_val = self.conditions.get('drained', 0)
        self.hp -= (drained_val * self.level)
        
        self.current_hp = safe_int(build.get('current_hp'), self.hp)
        if self.current_hp > self.hp: self.current_hp = self.hp # Cap it
        
        self.base_speed = safe_int(attributes.get('speed'), 25) + safe_int(attributes.get('speedBonus'), 0) + self.get_rule_mod('speed')
        if 'fleet' in [f['name'].lower() for f in self.feats]: self.base_speed += 5
        toggle_speed = self.toggle_effects_summary.get('speed', 0)
        self.active_speed = max(5, self.base_speed - self.active_speed_penalty - (10 if self.is_encumbered else 0) + toggle_speed)
        # Temp HP has two sources: a manual pool (granted by spells/items like
        # False Life, Heroism) which drains when the PC takes damage, and a
        # passive pool from class toggles (shield raised, rage, etc.) which
        # resets with the toggle. `temp_hp_manual` persists to disk; the
        # aggregate `temp_hp` is what the UI displays and what damage drains.
        self.temp_hp_manual = max(0, safe_int(build.get('temp_hp'), 0))
        self.temp_hp = self.temp_hp_manual + self.toggle_effects_summary.get('temp_hp', 0)
        
        # Tracker-compatibility aliases (tracker.html uses these on both PCs and Monsters)
        self.speed = self.active_speed
        self.strikes = []   # PCs use the 'attacks' property; strikes stays empty for template compat
        self.actions = []   # PCs don't have monster-style actions
        # NOTE: DO NOT reassign self.persistent_damage here. The Phase-5 loader
        # above (see "Per-turn persistent damage list") already set it to a
        # list[dict]. safe_str(list, '') would clobber that with the repr
        # string like "[{'damage': '1d6', ...}]", and list() of that repr
        # then produces a list of characters — blowing up the SSE payload
        # and the turn-reminder output. The tracker-JSON path stringifies
        # the list itself when it needs a display value.

        self.spell_casters = []
        if self.class_name.lower() in ['alchemist', 'inventor']:
            self.spell_casters.append({'name': 'Formula Book', 'type': 'Alchemical', 'levels': []})

        # First pass: detect whether Pathbuilder has already exported a
        # standalone "Cleric Font" caster block. If so, we skip the implicit
        # font-extra inference on the main "Cleric" block to avoid double-
        # counting the heal/harm slots.
        pb_provides_font_caster = any(
            'font' in (safe_str(c.get('name'), '').lower())
            for c in self.raw_spellCasters
        )

        for caster in self.raw_spellCasters:
            cast_type = safe_str(caster.get('castingType') or caster.get('spellcastingType'), 'Prepared')
            # Detect Bounded Spellcasting (archetype with 2 slots/rank cap).
            # Pathbuilder doesn't always set this explicitly, so we infer from
            # name/type strings.
            cast_type_lower = cast_type.lower()
            is_bounded = ('bounded' in cast_type_lower) or ('bounded' in safe_str(caster.get('name'), '').lower())
            if is_bounded and 'bounded' not in cast_type_lower:
                cast_type = ('Bounded ' + cast_type).strip()
            caster_name_lower = safe_str(caster.get('name'), '').lower()
            is_font_caster = 'font' in caster_name_lower
            c_info = {'name': safe_str(caster.get('name'), 'Spellcasting'), 'tradition': safe_str(caster.get('magicTradition'), 'Unknown'), 'type': cast_type, 'levels': []}
            slots_per_day = caster.get('perDay') or []

            # Cleric Divine Font — +4 heal/harm-only slots at highest cleric rank.
            # PF2e Player Core: 4 at L1, 5 at L5, 6 at L15. Honor explicit
            # `divineFont`/`font_kind`/`fontExtra` if set, otherwise derive
            # from class + sanctification.
            #
            # IMPORTANT: when Pathbuilder already gives us a separate
            # "* Font" caster block (Goel-style), do NOT also add inferred
            # font slots to the main caster — that would double-count.
            font_extra = 0
            font_kind = ''
            font_at_rank = 0
            cls_lower_local = self.class_name.lower()
            if (cls_lower_local == 'cleric' and 'prepared' in cast_type_lower
                    and not pb_provides_font_caster
                    and not is_font_caster):
                lvl = self.level
                font_extra = 6 if lvl >= 15 else (5 if lvl >= 5 else 4)
                # Sanctification → font kind (heal vs harm). Default to heal.
                sanc_lower = safe_str(build.get('sanctification'), 'Holy').lower()
                explicit_font = safe_str(caster.get('divineFont') or build.get('divineFont'), '').lower()
                if explicit_font in ('heal', 'harm'):
                    font_kind = explicit_font
                elif 'unholy' in sanc_lower:
                    font_kind = 'harm'
                else:
                    font_kind = 'heal'
                # Highest cleric spell rank — find the largest index in perDay > 0
                for i, n in enumerate(slots_per_day):
                    if safe_int(n) > 0 and i > font_at_rank:
                        font_at_rank = i
                # Allow per-PC override
                if isinstance(build.get('font_extra'), int):
                    font_extra = max(0, build['font_extra'])
            # If Pathbuilder gave us a standalone Font caster, mark it so the
            # UI renders it with the heal/harm-only badge + emerald slot pips.
            if is_font_caster and 'prepared' in cast_type_lower:
                # Determine kind from the name ("Harmful Font" vs "Healing Font")
                # or from sanctification.
                sanc_lower = safe_str(build.get('sanctification'), 'Holy').lower()
                if 'harm' in caster_name_lower or 'unholy' in sanc_lower:
                    c_info['_font_kind'] = 'harm'
                else:
                    c_info['_font_kind'] = 'heal'

            for lvl in range(11):
                max_slots = safe_int(slots_per_day[lvl]) if lvl < len(slots_per_day) else 0
                spells_at_lvl = []
                for s in (caster.get('spells') or []):
                    if safe_int(s.get('spellLevel')) == lvl:
                        for s_name in (s.get('list') or []):
                            entry = {'name': safe_str(s_name),
                                     'desc': COMPENDIUM_LIBRARY.get(safe_str(s_name).lower(), "<em>No description.</em>")}
                            # Innate caster uses_per_day map: {spell_name: N}
                            uses_map = caster.get('usesPerDay') or {}
                            if isinstance(uses_map, dict) and uses_map.get(safe_str(s_name)):
                                entry['uses_per_day'] = safe_int(uses_map[safe_str(s_name)], 1)
                            spells_at_lvl.append(entry)

                if spells_at_lvl or max_slots > 0:
                    # Paizo-style rank labels: "Cantrips", "1st-Rank Spells", etc.
                    if lvl == 0:
                        rank_label = 'Cantrips'
                    else:
                        _suf = 'th' if (10 <= lvl % 100 <= 20) else {1:'st', 2:'nd', 3:'rd'}.get(lvl % 10, 'th')
                        rank_label = f'{lvl}{_suf}-Rank Spells'
                    level_entry = {'level': lvl, 'label': rank_label, 'slots': max_slots, 'spells': spells_at_lvl}
                    # Attach font slots only at the cleric's highest cleric rank
                    if font_extra > 0 and lvl == font_at_rank:
                        level_entry['font_slots'] = font_extra
                        level_entry['font_kind'] = font_kind
                    # Standalone Pathbuilder Font caster: re-tag slots so the UI
                    # styles them as font (emerald, heal/harm-only) instead of
                    # generic prepared slots.
                    if is_font_caster and max_slots > 0 and c_info.get('_font_kind'):
                        level_entry['slots'] = 0
                        level_entry['font_slots'] = max_slots
                        level_entry['font_kind'] = c_info['_font_kind']
                    c_info['levels'].append(level_entry)
            # Strip the internal marker before appending.
            c_info.pop('_font_kind', None)
            if c_info['levels']: self.spell_casters.append(c_info)

        # Kineticist impulses — shown as spontaneous-style (no prep needed)
        if self.class_name.lower() == 'kineticist':
            k_impulses = [{'name': f['name'], 'desc': f['desc']} for f in self.feats 
                          if f.get('type', '').lower() in ['class feat', 'kineticist feat']]
            for cf in self.class_features:
                if cf['type'] in ['action', 'toggle'] and cf['name'] not in [i['name'] for i in k_impulses]:
                    k_impulses.append({'name': cf['name'], 'desc': cf['desc']})
            # Impulses are feats/actions, not spells: pull authoritative action
            # cost + full description from the Foundry compendium. Without this,
            # costs fall back to the stale name-keyed table (Scorching Column 2
            # vs the correct 3) or collide with a same-named focus spell
            # (Elemental Blast 2 vs the at-will blast's 1-or-2), and class-feature
            # impulses show only a short summary instead of the full rules text.
            for imp in k_impulses:
                info = KINETICIST_IMPULSE_DATA.get(imp['name'].strip().lower())
                if info:
                    imp['actions'] = info['actions']  # set (even '') so it isn't re-derived downstream
                    if len(info['desc']) > len(imp.get('desc') or ''):
                        imp['desc'] = info['desc']
            if k_impulses: self.spell_casters.append({'name': 'Kineticist Impulses', 'tradition': 'Primal', 'type': 'Impulse', 'levels': [{'level': 1, 'label': 'Impulses', 'slots': 0, 'spells': k_impulses}]})

        # Focus Spells — comprehensive detection for all classes
        # PF2E classes get focus spells from: class features, subclass grants, and feat selections
        focus_spells = []
        cls_lower = self.class_name.lower()
        
        # STEP 1: Class-granted focus spells (every member of the class gets these)
        CLASS_FOCUS_GRANTS = {
            'champion': ['Lay on Hands'], 'bard': ['Courageous Anthem'],
            'ranger': [], 'monk': [], 'cleric': [], 'druid': [],
            'sorcerer': [], 'oracle': [], 'witch': [], 'psychic': [],
            'magus': [], 'summoner': [], 'animist': [],
        }
        
        for spell_name in CLASS_FOCUS_GRANTS.get(cls_lower, []):
            focus_spells.append({'name': spell_name, 'desc': COMPENDIUM_LIBRARY.get(spell_name.lower(), f"<em>{spell_name} — class-granted focus spell.</em>")})

        # Post-process: add action costs to all spells across all casters
        
        # STEP 2: Subclass-granted focus spells (cause reactions, bloodline spells, etc.)
        if self.subclass:
            sub_data = SUBCLASS_MATRIX.get(self.subclass, {})
            fs_name = sub_data.get('focus_spell', '')
            if fs_name and fs_name not in [s['name'] for s in focus_spells]:
                focus_spells.append({'name': fs_name, 'desc': COMPENDIUM_LIBRARY.get(fs_name.lower(), f"<em>Focus spell from {self.subclass}.</em>")})
        
        # STEP 3: Pathbuilder focus data (if any is exported)
        pb_focus = build.get('focus', {})
        for fs_name in pb_focus.get('focusSpells', []):
            if fs_name and fs_name not in [s['name'] for s in focus_spells]:
                focus_spells.append({'name': safe_str(fs_name), 'desc': COMPENDIUM_LIBRARY.get(safe_str(fs_name).lower(), "<em>Focus spell.</em>")})
        
        # STEP 4: Detect from Pathbuilder 'specials' array — class features that grant focus
        specials = build.get('specials') or []
        specials_lower = [s.lower() for s in specials]
        
        # Domain-to-spell mapping (for clerics with Domain Initiate)
        DOMAIN_SPELLS = {
            'air': 'Pushing Gust', 'ambition': 'Blind Ambition', 'change': 'Adapt Self',
            'cities': 'Face in the Crowd', 'cold': 'Winter Bolt', 'confidence': 'Veil of Confidence',
            'creation': 'Splash of Art', 'darkness': 'Cloak of Shadow', 'death': "Death's Call",
            'decay': 'Withering Grasp', 'destruction': 'Cry of Destruction', 'dreams': 'Sweet Dream',
            'dust': 'Parch', 'duty': "Oathkeeper's Insignia", 'earth': 'Hurtling Stone',
            'family': 'Soothing Words', 'fate': 'Read Fate', 'fire': 'Fire Ray',
            'freedom': 'Unimpeded Stride', 'glyph': 'Redact', 'healing': "Healer's Blessing",
            'indulgence': 'Overstuff', 'knowledge': 'Scholarly Recollection',
            'lightning': 'Charged Javelin', 'luck': 'Bit of Luck', 'magic': 'Mystic Beacon',
            'might': 'Athletic Rush', 'moon': 'Moonbeam', 'nature': "Nature's Bounty",
            'nightmares': 'Waking Nightmare', 'pain': 'Savor the Sting', 'passion': 'Charming Touch',
            'perfection': 'Perfected Mind', 'plague': 'Divine Plagues', 'protection': "Protector's Sacrifice",
            'secrecy': 'Forced Quiet', 'shadow': 'Darkened Eyes', 'sorrow': 'Lament',
            'soul': 'Eject Soul', 'star': 'Zenith Star', 'sun': 'Dazzling Flash',
            'swarm': 'Swarmsense', 'time': 'Delay Consequence', 'travel': 'Agile Feet',
            'trickery': 'Sudden Shift', 'truth': 'Word of Truth', 'tyranny': 'Touch of Obedience',
            'undeath': 'Touch of Undeath', 'vigil': 'Object Memory', 'void': 'Hollow Heart',
            'water': 'Tidal Surge', 'wealth': 'Precious Metals', 'wyrmkin': 'Draconic Barrage',
            'zeal': 'Weapon Surge',
        }
        
        # Resolve choice feats: Domain Initiate → actual domain spell
        # Only for classes that take Domain Initiate (Cleric, Champion with Deity's Domain, etc.)
        has_domain_initiate = any(
            isinstance(f, list) and len(f) > 0 and safe_str(f[0]).lower() in ('domain initiate', "deity's domain", 'expanded domain initiate', 'advanced domain')
            for f in self.raw_feats
        ) or 'domain initiate' in specials_lower
        
        domain_found = None
        if has_domain_initiate:
            raw_feats = self.raw_feats
            for i, f in enumerate(raw_feats):
                if not isinstance(f, list) or len(f) < 3: continue
                fname = safe_str(f[0]).lower()
                ftype = safe_str(f[2] if len(f) > 2 else '').lower()
                
                # Method 1: feat with category "Domain" — the domain name IS the feat name
                if ftype == 'domain' and fname in DOMAIN_SPELLS:
                    domain_found = fname
                    break
                
                # Method 2: child choice of Domain Initiate
                if len(f) >= 6 and f[5] == 'childChoice' and isinstance(f[4], str) and 'domain' in f[4].lower():
                    if fname in DOMAIN_SPELLS:
                        domain_found = fname
                        break
                
                # Method 3: Domain Initiate's choice_label contains domain name
                if fname == 'domain initiate' and len(f) > 4 and isinstance(f[4], str):
                    for dname in DOMAIN_SPELLS:
                        if dname in f[4].lower():
                            domain_found = dname
                            break
                    if domain_found: break
            
            # Also check specials — but only exact domain name matches with "domain" suffix
            if not domain_found:
                for special in specials_lower:
                    if special.endswith(' domain'):
                        dname = special.replace(' domain', '')
                        if dname in DOMAIN_SPELLS:
                            domain_found = dname
                            break
                    # Also try exact match against domain names (for single-word specials like "zeal", "healing")
                    if not domain_found and special in DOMAIN_SPELLS:
                        # Only match if Domain Initiate is confirmed in feats/specials
                        domain_found = special
                        break
        
        # Replace "Domain Initiate" with the actual domain spell
        if has_domain_initiate:
            if domain_found and domain_found in DOMAIN_SPELLS:
                spell_name = DOMAIN_SPELLS[domain_found]
                focus_spells = [s for s in focus_spells if s['name'] != 'Domain Initiate']
                if spell_name not in [s['name'] for s in focus_spells]:
                    focus_spells.append({'name': spell_name, 'desc': COMPENDIUM_LIBRARY.get(spell_name.lower(), f"<em>{spell_name} — {domain_found.title()} domain focus spell.</em>")})
        
        # Map special names to the focus spells they grant
        SPECIAL_FOCUS_GRANTS = {
            'devotion spells': ['Lay on Hands'],  # Champion
            'ki spells': ['Ki Strike'],  # Monk
            'composition spells': ['Counter Performance', 'Courageous Anthem'],  # Bard
            'hex spells': [],  # Witch — patron-specific hex cantrip
            'conflux spells': [],  # Magus — from specific feat choices
            'link spells': ['Evolution Surge'],  # Summoner
            'revelation spells': [],  # Oracle — mystery-specific
            'bloodline spells': [],  # Sorcerer — bloodline-specific
            'warden spells': [],  # Ranger
            'wild shape': ['Wild Shape'],  # Druid
            'domain initiate': [],  # Cleric — domain-specific
        }
        
        for special in specials_lower:
            for key, spells in SPECIAL_FOCUS_GRANTS.items():
                if key in special:
                    for spell_name in spells:
                        if spell_name not in [s['name'] for s in focus_spells]:
                            focus_spells.append({'name': spell_name, 'desc': COMPENDIUM_LIBRARY.get(spell_name.lower(), f"<em>{spell_name}</em>")})
        
        # STEP 5: Check feat array for "Focus Spell" type entries (our builder format)
        for f in self.feats:
            if f.get('type', '').lower() == 'focus spell':
                if f['name'] not in [s['name'] for s in focus_spells]:
                    focus_spells.append({'name': f['name'], 'desc': f['desc']})
        
        # STEP 6: Scan feat names against comprehensive known focus spell list
        # These are feats whose names ARE focus spells — player chose them
        KNOWN_FOCUS_SPELLS = {
            # Champion
            'lay on hands', 'retributive strike', 'glimpse of redemption', 'liberating step',
            'touch of corruption', 'iron command', 'selfish shield', 'sun blade', 'light of revelation',
            'shield of faith', 'sacred form',
            # Monk ki/qi
            'ki strike', 'ki blast', 'ki rush', 'wholeness of body', 'ki cutting sight',
            'wronged monks wrath', 'qi center', 'unsheathing the sword-light',
            # Druid order
            'wild shape', 'wild morph', 'tempest surge', 'goodberry', 'heal animal',
            'stormwind flight', 'primal summons', 'storm retribution',
            # Bard compositions
            'inspire courage', 'courageous anthem', 'counter performance', 'inspire defense',
            'lingering composition', 'fortissimo composition', 'song of strength', 'dirge of doom',
            'triple time', 'allegro', 'soothing ballad', 'uplifting overture',
            'rallying anthem', 'symphony of the unfettered heart', 'song of the fallen',
            # Cleric domain
            'dazzling flash', 'fire ray', 'healer\'s blessing',
            'cry of destruction', 'athletic rush', 'splash of art', 'word of truth',
            # Sorcerer bloodline
            'angelic halo', 'tentacular limbs', 'glutton\'s jaw', 'diabolic edict',
            'dragon claws', 'elemental toss', 'faerie dust', 'jealous hex',
            'ancestral memories', 'nymph\'s token', 'undeath\'s blessing',
            # Oracle mystery
            'soul siphon', 'incendiary aura', 'life link', 'brain drain',
            'tempest touch', 'time skip', 'call to arms', 'spirit veil', 'spray of stars',
            # Magus conflux
            'shooting star', 'shielding strike', 'thunderous strike', 'spinning staff',
            'runic impression', 'cascade countermeasure', 'force fang', 'hasted assault',
            # Summoner link
            'evolution surge', 'extend boost', 'lifelink surge', 'eidolon\'s wrath',
            'unfetter eidolon',
            # Ranger warden
            'heal companion', 'enlarge companion', 'ranger\'s bramble', 'magic hide',
            'snare hopping',
            # Witch hex
            'evil eye', 'nudge fate', 'stoke the heart', 'shroud of night',
            'discern secrets', 'wilding word', 'clinging ice', 'patron\'s puppet',
            # Psychic
            'telekinetic rend', 'glimpse weakness', 'shatter mind',
            'redistribution of force', 'warp step',
            # Swashbuckler
            'derring-do',
            # Investigator
            'shared stratagem',
        }
        
        for f in self.feats:
            if f['name'].lower() in KNOWN_FOCUS_SPELLS and f['name'] not in [s['name'] for s in focus_spells]:
                focus_spells.append({'name': f['name'], 'desc': f['desc']})
        
        if focus_spells:
            # Calculate expected focus pool from feats/features
            computed_focus = min(3, max(1, len(focus_spells)))
            # Check Pathbuilder focusPoints field
            pb_fp = safe_int(build.get('focusPoints'), 0)
            # Take the best of: stored value, Pathbuilder value, computed value
            best_focus = max(self.focus_max, pb_fp, computed_focus)
            if best_focus > self.focus_max:
                self.focus_max = best_focus
                self.current_focus = max(self.current_focus, self.focus_max)
            
            # Only add focus caster if we don't already have one from spellCasters
            has_focus_caster = any('focus' in sc.get('type', '').lower() for sc in self.spell_casters)
            if not has_focus_caster:
                # Determine tradition based on class
                TRADITION_MAP = {
                    'champion': 'Divine', 'cleric': 'Divine', 'oracle': 'Divine',
                    'druid': 'Primal', 'ranger': 'Primal',
                    'wizard': 'Arcane', 'magus': 'Arcane', 'witch': 'Arcane',
                    'bard': 'Occult', 'psychic': 'Occult',
                    'monk': 'Divine', 'sorcerer': 'Arcane', 'summoner': 'Arcane',
                    'swashbuckler': 'None', 'investigator': 'None', 'thaumaturge': 'None',
                }
                tradition = TRADITION_MAP.get(cls_lower, 'Divine')
                if self.spell_casters:
                    tradition = self.spell_casters[0].get('tradition', tradition)
                
                # Focus spells use a *pool*, not slots. We pass slots=0 so the
                # spontaneous renderer doesn't draw a slot-checkbox row, and
                # set focus_pool: True for any future renderer that wants to
                # show pip readouts inline.
                self.spell_casters.append({
                    'name': 'Focus Spells',
                    'tradition': tradition,
                    'type': 'Focus',
                    'levels': [{'level': 1, 'label': 'Focus Spells', 'slots': 0,
                                'focus_pool': True, 'pool_max': self.focus_max,
                                'spells': focus_spells}]
                })

        # Post-process: add action costs to all spells across all casters
        for sc in self.spell_casters:
            for lvl in sc.get('levels', []):
                for sp in lvl.get('spells', []):
                    if 'actions' not in sp:
                        sp['actions'] = get_action_cost(sp['name'])

        # Guarantee: classes that can have focus spells ALWAYS get a Focus section
        # This ensures the "Add Spell" button is always available
        FOCUS_CLASSES = {'champion', 'cleric', 'druid', 'monk', 'bard', 'oracle', 'sorcerer', 
                         'witch', 'magus', 'ranger', 'summoner', 'psychic'}
        has_focus_caster = any('focus' in sc.get('type', '').lower() for sc in self.spell_casters)
        if cls_lower in FOCUS_CLASSES and not has_focus_caster:
            if self.focus_max == 0:
                self.focus_max = 1
                self.current_focus = 1
            TRADITION_MAP = {
                'champion': 'Divine', 'cleric': 'Divine', 'oracle': 'Divine',
                'druid': 'Primal', 'ranger': 'Primal',
                'wizard': 'Arcane', 'magus': 'Arcane', 'witch': 'Arcane',
                'bard': 'Occult', 'psychic': 'Occult',
                'monk': 'Divine', 'sorcerer': 'Arcane', 'summoner': 'Arcane',
            }
            tradition = TRADITION_MAP.get(cls_lower, 'Divine')
            if self.spell_casters:
                tradition = self.spell_casters[0].get('tradition', tradition)
            self.spell_casters.append({
                'name': 'Focus Spells',
                'tradition': tradition,
                'type': 'Focus',
                'levels': [{'level': 1, 'label': 'Focus Spells', 'slots': 0,
                            'focus_pool': True, 'pool_max': self.focus_max, 'spells': []}]
            })

        # Pets: merge Pathbuilder pets with custom pets
        self.pets = []
        custom_pets = build.get('pets_custom') or []
        pb_pets = build.get('pets') or []
        
        for pet in custom_pets:
            self.pets.append(pet)
        
        # Parse Pathbuilder pet format (different structure)
        for pet in pb_pets:
            if isinstance(pet, dict) and pet.get('name'):
                parsed = {
                    'name': pet.get('name', 'Companion'),
                    'type': pet.get('type', 'Animal Companion'),
                    'size': pet.get('size', 'Medium') if isinstance(pet.get('size'), str) else {0:'Tiny',1:'Small',2:'Medium',3:'Large'}.get(pet.get('size',2), 'Medium'),
                    'hp': safe_int(pet.get('hp'), safe_int(pet.get('maxHP'), 20)),
                    'ac': safe_int(pet.get('ac'), safe_int(pet.get('armorClass'), 16)),
                    'speed': safe_int(pet.get('speed'), 25),
                    'fort': safe_int(pet.get('fort'), safe_int(pet.get('fortitude'), 5)),
                    'ref': safe_int(pet.get('ref'), safe_int(pet.get('reflex'), 5)),
                    'will': safe_int(pet.get('will'), 3),
                    'perception': safe_int(pet.get('perception'), 5),
                    'attacks': [],
                    'abilities': pet.get('abilities', pet.get('special', '')),
                    'senses': pet.get('senses', ''),
                    'str_mod': safe_int(pet.get('str'), 2),
                    'dex_mod': safe_int(pet.get('dex'), 2),
                    'con_mod': safe_int(pet.get('con'), 2),
                    'int_mod': safe_int(pet.get('int'), -4),
                    'wis_mod': safe_int(pet.get('wis'), 1),
                    'cha_mod': safe_int(pet.get('cha'), 0),
                }
                # Parse attacks from various formats
                for atk in (pet.get('attacks') or pet.get('strikes') or []):
                    if isinstance(atk, dict):
                        parsed['attacks'].append({
                            'name': atk.get('name', 'Strike'),
                            'bonus': safe_int(atk.get('bonus'), safe_int(atk.get('hit'), 0)),
                            'damage': atk.get('damage', '1d6')
                        })
                # If Pathbuilder stores support benefit separately
                if pet.get('supportBenefit'):
                    parsed['abilities'] = (parsed['abilities'] or '') + '\nSupport Benefit: ' + pet['supportBenefit']
                
                # Only add if not already in custom_pets by name
                if parsed['name'] not in [p.get('name') for p in custom_pets]:
                    self.pets.append(parsed)
        
        self.active_effects = build.get('active_effects') or {}
        # Sheet-level Active Effects (the engine's instance records,
        # not the legacy `active_effects` dict above which is the
        # pre-engine condition-count map kept for back-compat with
        # the highest_buff calculator).
        self.pc_active_effects = list(build.get('pc_active_effects') or [])

    def get_rule_mod(self, selector):
        if selector not in self.rule_modifiers: return 0
        m = self.rule_modifiers[selector]
        return max(m['circumstance']+[0]) + max(m['status']+[0]) + max(m['item']+[0]) + sum(m['untyped'])
        
    def get_status_penalty(self, stat=None):
        base = max(self.conditions.get('frightened', 0), self.conditions.get('sickened', 0))
        if stat == 'str': base = max(base, self.conditions.get('enfeebled', 0))
        elif stat == 'dex': base = max(base, self.conditions.get('clumsy', 0), self.clumsy_penalty)
        elif stat == 'con': base = max(base, self.conditions.get('drained', 0))
        elif stat in ['int', 'wis', 'cha']: base = max(base, self.conditions.get('stupefied', 0))
        return base

    @property
    def status_penalty(self):
        """Base status penalty (frightened/sickened) for templates that access it as a property."""
        return max(self.conditions.get('frightened', 0), self.conditions.get('sickened', 0))

    @property
    def class_features(self):
        """Get class features from CLASS_FEATURES filtered by character level."""
        c_name = self.class_name.lower()
        features = CLASS_FEATURES.get(c_name, [])
        return [f for f in features if f.get('level', 1) <= self.level]
    
    @property
    def ancestry_features(self):
        """Get ancestry features from ANCESTRY_FEATURES."""
        a_name = self.ancestry.lower()
        return ANCESTRY_FEATURES.get(a_name, [])
    
    @property
    def toggle_effects_summary(self):
        """Calculate aggregate stat modifications from all active toggles."""
        effects = {}
        c_name = self.class_name.lower()
        all_features = CLASS_FEATURES.get(c_name, [])
        for f in all_features:
            if f['name'] in self.active_toggles and 'toggle_effects' in f:
                for stat, val in f['toggle_effects'].items():
                    if isinstance(val, (int, float)):
                        effects[stat] = effects.get(stat, 0) + val
                    elif val == 'level+con':
                        effects[stat] = self.level + self.mods.get('con', 0)
                    elif val == 'int':
                        effects[stat] = effects.get(stat, 0) + max(self.mods.get('int', 0), 1)
                    elif val == 'level':
                        effects[stat] = effects.get(stat, 0) + self.level
                    elif isinstance(val, bool):
                        effects[stat] = val
        return effects

    @property
    def highest_buff(self): return max([safe_int(v) for k, v in self.active_effects.items() if v] or [0])

    def _abp(self, bonus_type):
        """ABP bonus, gated by the per-PC opt-in flag. Returns 0 unless
        Automatic Bonus Progression is enabled for this character.

        ``devastating_attacks`` has a baseline of 1 die regardless of level
        when ABP is OFF — that's just "the weapon's normal damage die",
        which the attack code uses as a multiplier. When ABP is OFF we
        return 0 so the attack code's ``or 1`` fallback yields exactly 1
        die (vanilla weapon damage)."""
        if not self.abp_enabled:
            return 0
        return get_abp_bonus(self.level, bonus_type)

    @property
    def base_ac(self):
        """AC without condition penalties or buffs — used by tracker to detect debuffs."""
        prof_val = safe_int(self.proficiencies.get('ac'), 2)
        effective_dex = min(self.mods.get('dex', 0), self.ac_dex_cap)
        prof_bonus = prof_val + self.level if prof_val > 0 else 0
        abp_ac = self._abp('defense_potency')
        return 10 + self.ac_item + effective_dex + prof_bonus + abp_ac + self.get_rule_mod('ac')

    @property
    def ac(self):
        prof_val = safe_int(self.proficiencies.get('ac'), 2)
        effective_dex = min(self.mods.get('dex', 0), self.ac_dex_cap)
        prof_bonus = prof_val + self.level if prof_val > 0 else 0
        abp_ac = self._abp('defense_potency')
        base_ac = 10 + self.ac_item + effective_dex + prof_bonus + abp_ac
        circ_pen = 2 if (self.conditions.get('prone') or self.conditions.get('off_guard')) else 0
        shield_bonus = self.shield_ac_bonus if self.shield_raised else 0
        toggle_ac = self.toggle_effects_summary.get('ac', 0)
        return base_ac - self.get_status_penalty('dex') + self.highest_buff - circ_pen + self.get_rule_mod('ac') + toggle_ac + shield_bonus
    
    def _calc_save(self, stat_key, prof_key):
        prof_val = safe_int(self.proficiencies.get(prof_key), 2)
        base = self.mods.get(stat_key, 0) if prof_val == 0 else self.mods.get(stat_key, 0) + self.level + prof_val
        abp_save = self._abp('save_potency')
        return base + abp_save - self.get_status_penalty(stat_key) + self.highest_buff + self.get_rule_mod(prof_key) + self.get_rule_mod('saving-throw')

    @property
    def fort(self): return self._calc_save('con', 'fortitude')
    @property
    def ref(self): return self._calc_save('dex', 'reflex')
    @property
    def will(self): return self._calc_save('wis', 'will')

    def _save_breakdown(self, stat_key, prof_key, label):
        """Human-readable tooltip string for a save / perception-like roll."""
        prof_val = safe_int(self.proficiencies.get(prof_key), 2)
        prof_letter = {0:'U', 2:'T', 4:'E', 6:'M', 8:'L'}.get(prof_val, 'T')
        stat_mod = self.mods.get(stat_key, 0)
        sign = lambda v: f"+{v}" if v >= 0 else str(v)
        parts = [f"{stat_key.upper()} {sign(stat_mod)}"]
        if prof_val > 0:
            parts.append(f"{prof_letter} +{prof_val}")
            parts.append(f"Lvl +{self.level}")
        abp = self._abp('save_potency') if prof_key != 'perception' else self._abp('perception_potency')
        if abp:
            parts.append(f"ABP +{abp}")
        sp = self.get_status_penalty(stat_key)
        if sp:
            parts.append(f"status −{sp}")
        if self.highest_buff:
            parts.append(f"buff +{self.highest_buff}")
        rm = self.get_rule_mod(prof_key)
        if rm:
            parts.append(f"feat {sign(rm)}")
        return f"{label}: " + " · ".join(parts)

    @property
    def fort_breakdown(self): return self._save_breakdown('con', 'fortitude', 'Fortitude')
    @property
    def ref_breakdown(self): return self._save_breakdown('dex', 'reflex', 'Reflex')
    @property
    def will_breakdown(self): return self._save_breakdown('wis', 'will', 'Will')
    @property
    def perception_breakdown(self): return self._save_breakdown('wis', 'perception', 'Perception')

    @property
    def perception(self):
        prof_val = safe_int(self.proficiencies.get('perception'), 2)
        base = self.mods.get('wis', 0) if prof_val == 0 else self.mods.get('wis', 0) + self.level + prof_val
        abp_perc = self._abp('perception_potency')
        return base + abp_perc - self.get_status_penalty('wis') + self.highest_buff + self.get_rule_mod('perception')

    @property
    def initiative_mod(self):
        return self.perception + self.get_rule_mod('initiative')

    @property
    def class_dc(self):
        prof = safe_int(self.proficiencies.get('class_dc', 2))
        
        c_name = self.class_name.lower()
        key_options = BUILDER_DATA['classes'].get(c_name, {}).get("key_options", ["str"])
        subclass_info = SUBCLASS_MATRIX.get(self.subclass, {})
        if "key_ability" in subclass_info:
            key_options = [subclass_info["key_ability"]]
            
        key_mod = max([self.mods.get(stat, 0) for stat in key_options]) if key_options else 0
        return 10 + self.level + prof + key_mod - self.get_status_penalty()

    @property
    def spell_attack(self):
        c_name = self.class_name.lower()
        is_kineticist = (c_name == "kineticist")

        if not self.spell_casters and not is_kineticist: return 0

        # Use auto-computed proficiency from CLASS_PROGRESSION
        if is_kineticist:
            prof = safe_int(self.proficiencies.get('class_dc', 2))
        else:
            prof = safe_int(self.proficiencies.get('spell_attack', 0))
            if prof == 0:
                # Fallback for classes without spell_attack in CLASS_PROGRESSION (multiclass, etc.)
                c_type = (self.spell_casters[0].get("castingType") or self.spell_casters[0].get("spellcastingType") or "").lower() if self.spell_casters else ""
                if "alchemical" in c_type: return 0
                prof = 2  # Trained default

        # Casting ability comes from the spellCaster block, NOT the class's
        # martial key_options. e.g. Champion's class key is STR/DEX (used for
        # class DC), but their focus spells are CHA. Pathbuilder sets
        # `ability` per spellCaster — read that first; for focus-only classes
        # (Champion, Monk) Pathbuilder stores it under build.focus.{trad}.
        key_mod = None
        ability_used = 'cha'
        if self.raw_spellCasters:
            ab = (self.raw_spellCasters[0].get('ability') or '').lower()
            if ab in ('str', 'dex', 'con', 'int', 'wis', 'cha'):
                ability_used = ab
                key_mod = self.mods.get(ab, 0)
            else:
                # Tradition → ability fallback (per class casting ability conventions)
                trad = (self.raw_spellCasters[0].get('magicTradition') or '').lower()
                trad_default = {'arcane': 'int', 'divine': 'wis', 'occult': 'cha', 'primal': 'wis'}.get(trad)
                if trad_default and c_name == 'champion':
                    trad_default = 'cha'  # Champion divine focus uses CHA
                if trad_default:
                    ability_used = trad_default
                    key_mod = self.mods.get(trad_default, 0)
        if key_mod is None:
            # No spellCasters block — try Pathbuilder's `focus` map. Shape:
            #   focus: { divine: { cha: {...} }, primal: { wis: {...} } }
            # The first matching {tradition: {ability: {...}}} wins.
            focus_map = (self._build_ref or {}).get('focus') if hasattr(self, '_build_ref') else None
            if isinstance(focus_map, dict):
                for trad_key, ab_branch in focus_map.items():
                    if isinstance(ab_branch, dict):
                        for ab in ('str', 'dex', 'con', 'int', 'wis', 'cha'):
                            if ab in ab_branch:
                                ability_used = ab
                                key_mod = self.mods.get(ab, 0)
                                break
                    if key_mod is not None:
                        break
        if key_mod is None:
            # Class-specific defaults for focus-only casters with no PB hint.
            CLASS_FOCUS_ABILITY = {
                'champion': 'cha', 'monk':     'wis', 'ranger':   'wis',
                'sorcerer': 'cha', 'bard':     'cha', 'oracle':   'cha',
            }
            ab = CLASS_FOCUS_ABILITY.get(c_name)
            if ab:
                ability_used = ab
                key_mod = self.mods.get(ab, 0)
        if key_mod is None and is_kineticist:
            key_options = BUILDER_DATA["classes"].get(c_name, {}).get("key_options", ["con"])
            subclass_info = SUBCLASS_MATRIX.get(self.subclass, {})
            if "key_ability" in subclass_info:
                key_options = [subclass_info["key_ability"]]
            key_mod = max([self.mods.get(stat, 0) for stat in key_options]) if key_options else 0
            ability_used = key_options[0] if key_options else 'cha'
        if key_mod is None:
            key_mod = 0

        return self.level + prof + key_mod - self.get_status_penalty(ability_used)

    @property
    def spell_dc(self):
        attack = self.spell_attack
        return 10 + attack if attack > 0 else 0

    @property
    def cantrip_rank(self):
        """Cantrips auto-heighten to half your level, rounded up."""
        return max(1, math.ceil(self.level / 2))
    
    @property
    def hp_breakdown(self):
        """Returns a human-readable HP breakdown for the sheet."""
        anc_hp = self._anc_hp
        cls_hp = self._cls_hp
        con_mod = self.mods.get('con', 0)
        build = self._build_ref
        attrs = build.get('attributes', {})
        bonus_hp = safe_int(attrs.get('bonushp'), 0)
        bonus_per = safe_int(attrs.get('bonushpPerLevel'), 0)
        hp_rule_mod = self.get_rule_mod('hp') if bonus_per == 0 else 0
        drained = self.conditions.get('drained', 0) * self.level
        parts = [f"Ancestry {anc_hp}"]
        parts.append(f"({cls_hp} class + {con_mod} CON{f' + {bonus_per} bonus' if bonus_per else ''}) × {self.level} lvl = {(cls_hp + con_mod + bonus_per) * self.level}")
        if bonus_hp: parts.append(f"+{bonus_hp} flat bonus")
        if hp_rule_mod: parts.append(f"+{hp_rule_mod} feats")
        if drained: parts.append(f"-{drained} Drained")
        return " + ".join(parts) + f" = {self.hp}"

    @property
    def skills(self):
        res = []
        skill_map = { 'acrobatics': 'dex', 'arcana': 'int', 'athletics': 'str', 'crafting': 'int', 'deception': 'cha', 'diplomacy': 'cha', 'intimidation': 'cha', 'medicine': 'wis', 'nature': 'wis', 'occultism': 'int', 'performance': 'cha', 'religion': 'wis', 'society': 'int', 'stealth': 'dex', 'survival': 'wis', 'thievery': 'dex' }
        
        for skill, stat in skill_map.items():
            prof_val = safe_int(self.proficiencies.get(skill.lower()), 0)
            val = self.mods.get(stat, 0) if prof_val == 0 else self.mods.get(stat, 0) + self.level + prof_val
            
            if stat in ['str', 'dex']: val -= self.active_armor_penalty
            if skill == 'stealth': val -= self.stealth_penalty
            
            penalty = self.get_status_penalty(stat)
            total_mod = val - penalty + self.highest_buff + self.get_rule_mod(skill.lower())
            
            prof_letter = {0:'U', 2:'T', 4:'E', 6:'M', 8:'L'}.get(prof_val, 'U')
            # Human-readable breakdown string for roll-button tooltips — lets
            # players see *why* a skill is +X (which Demiplane is criticized
            # for hiding). Build out of parts so zero-value parts are omitted.
            stat_mod = self.mods.get(stat, 0)
            parts = []
            sign = lambda v: f"+{v}" if v >= 0 else str(v)
            parts.append(f"{stat.upper()} {sign(stat_mod)}")
            if prof_val > 0:
                parts.append(f"{prof_letter} +{prof_val}")
                parts.append(f"Lvl +{self.level}")
            if stat in ['str','dex'] and self.active_armor_penalty:
                parts.append(f"armor −{self.active_armor_penalty}")
            if skill == 'stealth' and self.stealth_penalty:
                parts.append(f"stealth −{self.stealth_penalty}")
            if penalty:
                parts.append(f"status −{penalty}")
            if self.highest_buff:
                parts.append(f"buff +{self.highest_buff}")
            rule_mod_val = self.get_rule_mod(skill.lower())
            if rule_mod_val:
                parts.append(f"feat {sign(rule_mod_val)}")
            breakdown = " · ".join(parts)
            res.append({'name': skill.title(), 'stat': stat.upper(), 'prof_val': prof_val, 'prof_letter': prof_letter, 'total': f"+{total_mod}" if total_mod >= 0 else str(total_mod), 'penalty': penalty, 'breakdown': breakdown})
            
        for skill, prof_val in self.proficiencies.items():
            if skill.startswith('lore:'):
                stat = 'int'
                val = self.mods.get(stat, 0) if prof_val == 0 else self.mods.get(stat, 0) + self.level + prof_val
                total_mod = val - self.get_status_penalty(stat) + self.highest_buff + self.get_rule_mod(skill.lower())
                prof_letter = {0:'U', 2:'T', 4:'E', 6:'M', 8:'L'}.get(prof_val, 'U')
                display_name = "Lore: " + skill.replace('lore:', '').strip().title()
                res.append({'name': display_name, 'stat': stat.upper(), 'prof_val': prof_val, 'prof_letter': prof_letter, 'total': f"+{total_mod}" if total_mod >= 0 else str(total_mod)})
                
        res.sort(key=lambda x: x['name'])
        return res

    @property
    def attacks(self):
        res = []
        abp_hit = self._abp('attack_potency')
        abp_dice = self._abp('devastating_attacks') or 1
        
        for w in self._raw_weapons:
            traits = w.get('traits', [])
            if isinstance(traits, str): traits = [traits]
            traits_lower = [str(t).lower() for t in (traits or [])]
            
            attack_stat = w.get('attack_stat', 'str')
            prof_val = safe_int(w.get('prof_val'), 2)
            is_two_handed = w.get('is_two_handed', False)
            
            if 'finesse' in traits_lower and attack_stat == 'str':
                if self.mods.get('dex', 0) > self.mods.get('str', 0):
                    attack_stat = 'dex'
            if 'ranged' in traits_lower and 'propulsive' not in traits_lower and 'thrown' not in traits_lower:
                attack_stat = 'dex'
                
            stat_mod = self.mods.get(attack_stat, 0)
            prof_bonus = (self.level + prof_val) if prof_val > 0 else 0
            
            circ_pen = 2 if self.conditions.get('prone') else 0
            total_hit = stat_mod + prof_bonus + abp_hit - self.get_status_penalty(attack_stat) + self.highest_buff + self.get_rule_mod('attack') - circ_pen
            
            map_penalty = -4 if 'agile' in traits_lower else -5
            second_hit = total_hit + map_penalty
            third_hit = total_hit + (map_penalty * 2)
            fmt = lambda v: f"+{v}" if v >= 0 else str(v)
            # MAP helper (user item #8): show the exact number to put on the d20
            # for the 2nd/3rd attack, plus the per-attack MAP delta (−4 agile /
            # −5 otherwise) so there's no mental math at the table.
            strikes = [
                {'label': fmt(total_hit),  'mod': total_hit,  'map': 0,                  'map_label': ''},
                {'label': fmt(second_hit), 'mod': second_hit, 'map': map_penalty,        'map_label': f"{map_penalty}{' agile' if 'agile' in traits_lower else ''}"},
                {'label': fmt(third_hit),  'mod': third_hit,  'map': map_penalty * 2,    'map_label': f"{map_penalty * 2}{' agile' if 'agile' in traits_lower else ''}"},
            ]
            
            base_dmg = safe_str(w.get('damage', '1d4'))
            die_match = re.search(r'd(\d+)', base_dmg)
            die_size = f"d{die_match.group(1)}" if die_match else "d4"
            type_match = re.search(r'[a-zA-Z]+$', base_dmg)
            dmg_type = type_match.group() if type_match else ""
            
            has_two_hand_trait = False
            for t in traits_lower:
                if t.startswith('two-hand-d'):
                    has_two_hand_trait = True
                    if is_two_handed: die_size = t.replace('two-hand-', '')
                    break

            dmg_mod = 0
            if 'ranged' not in traits_lower and 'finesse' not in traits_lower:
                dmg_mod = self.mods.get('str', 0)
            elif 'finesse' in traits_lower and 'ranged' not in traits_lower:
                if self.class_name.lower() == 'rogue' and self.subclass.lower() == 'thief':
                    dmg_mod = self.mods.get('dex', 0)
                else:
                    dmg_mod = self.mods.get('str', 0)
            elif 'propulsive' in traits_lower:
                str_mod = self.mods.get('str', 0)
                dmg_mod = math.floor(str_mod / 2) if str_mod > 0 else str_mod
            elif 'thrown' in traits_lower:
                dmg_mod = self.mods.get('str', 0)
            
            dmg_mod += self.get_rule_mod('damage')
            
            # AUTOMATION: Enfeebled drops melee STR damage
            if attack_stat == 'str':
                enfeebled = self.conditions.get('enfeebled', 0)
                if enfeebled > 0: dmg_mod -= enfeebled
            
            # AUTOMATION: Toggle effects (Rage +2 dmg, Overdrive +INT, Arcane Cascade +1, etc.)
            toggle_dmg = self.toggle_effects_summary.get('damage', 0)
            is_melee_or_thrown = 'ranged' not in traits_lower or 'thrown' in traits_lower
            if toggle_dmg and is_melee_or_thrown:
                dmg_mod += toggle_dmg
            
            dmg_tag = dmg_type
            if self.sanctification != 'Neutral' and 'unarmed' not in traits_lower:
                dmg_tag += f" ({self.sanctification.lower()})"
                
            dmg_str = f"{abp_dice}{die_size}"
            if dmg_mod > 0: dmg_str += f" + {dmg_mod}"
            elif dmg_mod < 0: dmg_str += f" - {abs(dmg_mod)}"
            dmg_str += f" {dmg_tag}".strip()
            
            crit_effects = []
            for t in traits_lower:
                if t.startswith('deadly'): crit_effects.append(t.title())
                if t.startswith('fatal'): crit_effects.append(t.title())
            
            # Attack breakdown tooltip — show why the hit bonus is what it is.
            sign = lambda v: f"+{v}" if v >= 0 else str(v)
            atk_parts = [f"{attack_stat.upper()} {sign(stat_mod)}"]
            if prof_val > 0:
                _prof_letter = {0:'U', 2:'T', 4:'E', 6:'M', 8:'L'}.get(prof_val, 'T')
                atk_parts.append(f"{_prof_letter} +{prof_val}")
                atk_parts.append(f"Lvl +{self.level}")
            if abp_hit:
                atk_parts.append(f"ABP +{abp_hit}")
            if self.get_status_penalty(attack_stat):
                atk_parts.append(f"status −{self.get_status_penalty(attack_stat)}")
            if self.highest_buff:
                atk_parts.append(f"buff +{self.highest_buff}")
            if circ_pen:
                atk_parts.append(f"prone −{circ_pen}")
            if self.get_rule_mod('attack'):
                atk_parts.append(f"feat {sign(self.get_rule_mod('attack'))}")
            atk_breakdown = " · ".join(atk_parts)
            res.append({
                'name': w.get('name'),
                'strikes': strikes,
                'damage': dmg_str,
                'traits': traits,
                'has_two_hand': has_two_hand_trait,
                'is_two_handed': is_two_handed,
                'crit_effects': " | ".join(crit_effects),
                'breakdown': atk_breakdown,
            })
        return res

    @property
    def as_dict(self):
        d = copy.deepcopy(self.__dict__)
        d['ac'] = self.ac
        d['fort'] = self.fort
        d['ref'] = self.ref
        d['will'] = self.will
        d['perception'] = self.perception
        d['initiative_mod'] = self.initiative_mod
        d['class_dc'] = self.class_dc
        d['spell_attack'] = self.spell_attack
        d['spell_dc'] = self.spell_dc
        d['skills'] = self.skills
        d['attacks'] = self.attacks
        d['total_bulk'] = round(self.total_bulk, 1)
        d['rule_modifiers'] = self.rule_modifiers
        # Sheet-level active effects (spell/feat/item buffs) carried
        # via the Active Effects engine. as_dict surfaces both the
        # raw list AND the effective stats post-application so the
        # sheet UI can show "AC 19 (effective 20 with Heroism)".
        d['pc_active_effects'] = list(getattr(self, 'pc_active_effects', []) or [])
        eff = self.compute_effective_stats()
        d['effective'] = eff['effective']
        d['effects_breakdown'] = eff['breakdown']
        return d

    def compute_effective_stats(self):
        """Apply pc_active_effects (and conditions, since conditions
        already feed through this engine) on top of the base @property
        computations. Returns {effective, breakdown}.

        Conditions are already partly handled in the ac / save
        properties (via get_status_penalty and circumstance pen).
        Active Effects compute strictly ADDITIVELY on top, so the
        base passed in here is the post-condition value. Double-
        counting Frightened would over-penalize; we feed an empty
        conditions dict to keep the engine focused on the
        active_effects-only delta."""
        from services import active_effects as _ae
        base = {
            'ac':         int(self.ac or 0),
            'fort':       int(self.fort or 0),
            'ref':        int(self.ref or 0),
            'will':       int(self.will or 0),
            'perception': int(self.perception or 0),
            'attack':     0,
            'damage':     0,
            'skills':     0,
            'dc':         0,
            'actions':    int(getattr(self, 'max_actions', 3) or 3),
        }
        return _ae.compute_token_stats(
            {},  # conditions already baked into base via @property paths
            list(getattr(self, 'pc_active_effects', []) or []),
            base,
        )


def _pf2e_make_actor(doc, file_path=''):
    """Actor factory bound into the pf2e registry entry (see systems/)."""
    return Character(doc, file_path)


# Bind the concrete PF2e actor class into the registry now that Character is
# defined. systems/ stays decoupled from app.py; the host binds at startup.
systems.get('pf2e').bind_actor_factory(_pf2e_make_actor)


def make_actor(doc, file_path=''):
    """Load a stored character envelope into the actor class for its `system`
    (defaulting to pf2e). The single dispatch seam for multi-system PCs."""
    return systems.actor_for_doc(doc, file_path)


class Monster:
    def __init__(self, data, file_path=""):
        self.file_path = file_path
        self.instance_id = ""
        self.is_pc = False
        self.initiative = 0
        self.persistent_damage = ""
        # GM-controlled visibility. When False, player SSE feed masks name,
        # HP, conditions, and scrubs the name from combat-log lines.
        self.visible_to_players = True
        # Dramatic title shown on the boss-reveal card (Chunk 4d), e.g.
        # "The Caged Wrath". Empty = no reveal card fires for this creature.
        self.epithet = ''
        # GM creature tactics notes — per-combatant free-text, persisted
        # with encounter save/load. Hidden from players.
        self.tactics = ''
        self.name = safe_str(data.get('name', 'Unknown Monster'))
        system = data.get('system') or {}
        if not isinstance(system, dict): system = {}
        
        self.level = safe_int(system.get('details', {}).get('level', {}).get('value'), 1)
        attributes = system.get('attributes') or {}
        if not isinstance(attributes, dict): attributes = {}
        
        self.hp = safe_int(attributes.get('hp', {}).get('max'), 10)
        self.current_hp = safe_int(attributes.get('hp', {}).get('value'), 10)
        self.base_ac = safe_int(attributes.get('ac', {}).get('value'), 10)
        self.speed = safe_int(attributes.get('speed', {}).get('value'), 25)
        
        perc_val = attributes.get('perception', {}).get('value')
        if perc_val is None: perc_val = system.get('perception', {}).get('mod')
        if perc_val is None: perc_val = system.get('perception', {}).get('value', 0)
        self.base_perception = safe_int(perc_val, 0)
        
        saves = system.get('saves', {})
        self.base_fort = safe_int(saves.get('fortitude', {}).get('value'), 0)
        self.base_ref = safe_int(saves.get('reflex', {}).get('value'), 0)
        self.base_will = safe_int(saves.get('will', {}).get('value'), 0)
        
        self.strikes = []
        self.actions = []
        
        # Parse resistances, weaknesses, immunities from Foundry VTT format
        self.immunities = []
        self.resistances = []
        self.weaknesses = []
        
        raw_imm = attributes.get('immunities', {})
        if isinstance(raw_imm, dict):
            self.immunities = [str(v) for v in raw_imm.get('value', [])]
            if raw_imm.get('custom'): self.immunities.append(str(raw_imm['custom']))
        elif isinstance(raw_imm, list):
            for item in raw_imm:
                if isinstance(item, dict): self.immunities.append(str(item.get('type', item.get('value', ''))))
                elif isinstance(item, str): self.immunities.append(item)
        
        raw_res = attributes.get('resistances', [])
        if isinstance(raw_res, list):
            for item in raw_res:
                if isinstance(item, dict):
                    rtype = str(item.get('type', 'unknown'))
                    rval = safe_int(item.get('value'), 0)
                    exceptions = item.get('exceptions', [])
                    exc_str = f" (except {', '.join(exceptions)})" if exceptions else ""
                    self.resistances.append(f"{rtype} {rval}{exc_str}")
                elif isinstance(item, str): self.resistances.append(item)
        
        raw_weak = attributes.get('weaknesses', [])
        if isinstance(raw_weak, list):
            for item in raw_weak:
                if isinstance(item, dict):
                    wtype = str(item.get('type', 'unknown'))
                    wval = safe_int(item.get('value'), 0)
                    self.weaknesses.append(f"{wtype} {wval}")
                elif isinstance(item, str): self.weaknesses.append(item)
        
        # Parse traits
        self.traits = []
        raw_traits = system.get('traits', {})
        if isinstance(raw_traits, dict):
            self.traits = [str(t) for t in raw_traits.get('value', [])]
        
        # An NPC's real Strikes are `melee` / `ranged` items (they carry the attack
        # bonus + damageRolls). A `weapon` item is inventory; for an NPC it has no
        # strike bonus/damage, so it used to parse to a phantom "+0 / Check Details"
        # that DUPLICATED the real Strike (every monster with both showed each
        # weapon twice). Parse the real strikes; fall back to a `weapon` item only
        # when no melee/ranged Strike already covers that name -- so a weapon-only
        # creature still gets a Strike, with no phantom duplicate.
        _weapon_fallback = []
        for item in (data.get('items') or []):
            item_type = item.get('type')
            name = item.get('name')
            if item_type in ('melee', 'ranged', 'weapon'):
                damage = "Check Details"
                system_data = item.get('system', {})
                damage_rolls = system_data.get('damageRolls', {})
                if isinstance(damage_rolls, dict) and damage_rolls:
                    parts = [f"{roll['damage']} {roll.get('damageType', '')}".strip() for k, roll in damage_rolls.items() if isinstance(roll, dict) and 'damage' in roll]
                    if parts: damage = ", ".join(parts)
                rec = {'name': name, 'bonus': safe_int(system_data.get('bonus', {}).get('value'), 0), 'damage': damage}
                (_weapon_fallback if item_type == 'weapon' else self.strikes).append(rec)
            elif item_type == 'action':
                self.actions.append({'name': name, 'description': clean_foundry_text(item.get('system', {}).get('description', {}).get('value', ''))})
        _real_strike_names = {s['name'] for s in self.strikes}
        self.strikes.extend(w for w in _weapon_fallback if w['name'] not in _real_strike_names)

        # Spellcasting (NPC): the spellcastingEntry carries the spell attack
        # (spelldc.value) + DC (spelldc.dc) so the GM can read a caster foe's
        # numbers straight off the tracker. Take the highest if several entries.
        self.spell_attack = 0
        self.spell_dc = 0
        for item in (data.get('items') or []):
            if item.get('type') == 'spellcastingEntry':
                sd = item.get('system', {}).get('spelldc', {})
                if isinstance(sd, dict):
                    self.spell_attack = max(self.spell_attack, safe_int(sd.get('value'), 0))
                    self.spell_dc = max(self.spell_dc, safe_int(sd.get('dc'), 0))

        self.conditions = { 'frightened': 0, 'sickened': 0, 'dying': 0, 'wounded': 0, 'doomed': 0, 'stunned': 0, 'slowed': 0, 'enfeebled': 0, 'clumsy': 0, 'drained': 0, 'stupefied': 0, 'prone': False, 'off_guard': False, 'concealed': False, 'hidden': False, 'undetected': False }

        # Auto-expiry: { condition_name: rounds_remaining }. Decremented at the
        # end of THIS combatant's turn; condition is cleared when it hits 0.
        # Lets the GM mark "frightened 1 for 2 rounds" without a manual cleanup
        # later. Conditions that already auto-tick by rule (frightened -1 each
        # turn) ignore expiry — whichever clears first wins.
        self.condition_expiry = {}
        # Per-turn action economy: tracker pips show how many of the monster's
        # 3 actions + reaction are still available. Reset on turn-start.
        self.actions_used = 0
        self.reaction_used = False
        self.max_actions = 3

        # Elite/Weak adjustment tracking
        self.elite_weak = 0  # 0=normal, 1=elite, -1=weak
        self.delaying = False
        self._original_hp = self.hp
        self._original_base_ac = self.base_ac
        self._original_base_perception = self.base_perception
        self._original_base_fort = self.base_fort
        self._original_base_ref = self.base_ref
        self._original_base_will = self.base_will
        self._original_strikes = [(s['name'], s['bonus']) for s in self.strikes]

    def _get_elite_hp_adjustment(self):
        """HP adjustment based on creature level per PF2E rules."""
        if self.level <= 1: return 10
        elif self.level <= 4: return 15
        elif self.level <= 19: return 20
        else: return 30

    def apply_elite_weak(self, mode):
        """Apply Elite (+1) or Weak (-1) adjustment, or reset to normal (0)."""
        # First reset to original values
        self.hp = self._original_hp
        self.current_hp = min(self.current_hp, self.hp)  # Don't exceed new max
        self.base_ac = self._original_base_ac
        self.base_perception = self._original_base_perception
        self.base_fort = self._original_base_fort
        self.base_ref = self._original_base_ref
        self.base_will = self._original_base_will
        for i, s in enumerate(self.strikes):
            if i < len(self._original_strikes):
                s['bonus'] = self._original_strikes[i][1]

        self.elite_weak = mode  # 0, 1, or -1
        if mode == 0: return  # Reset to normal, done
        
        adjustment = 2 * mode  # +2 for elite, -2 for weak
        hp_adj = self._get_elite_hp_adjustment() * mode
        
        self.hp = max(1, self._original_hp + hp_adj)
        self.current_hp = min(self.current_hp, self.hp)
        self.base_ac += adjustment
        self.base_perception += adjustment
        self.base_fort += adjustment
        self.base_ref += adjustment
        self.base_will += adjustment
        for s in self.strikes:
            s['bonus'] += adjustment

    @property
    def status_penalty(self): return max(self.conditions.get('frightened', 0), self.conditions.get('sickened', 0))
    @property
    def ac(self): return self.base_ac - self.status_penalty - (2 if (self.conditions.get('prone') or self.conditions.get('off_guard')) else 0)
    @property
    def fort(self): return self.base_fort - self.status_penalty
    @property
    def ref(self): return self.base_ref - self.status_penalty
    @property
    def will(self): return self.base_will - self.status_penalty
    @property
    def perception(self): return self.base_perception - self.status_penalty

def load_compendium():
    COMPENDIUM_LIBRARY.clear()
    COMPENDIUM_RULES.clear()
    BUILDER_ANCESTRIES.clear()
    BUILDER_BACKGROUNDS.clear()
    BUILDER_CLASSES.clear()
    BUILDER_FEATS['class'].clear(); BUILDER_FEATS['skill'].clear(); BUILDER_FEATS['general'].clear(); BUILDER_FEATS['ancestry'].clear()
    BUILDER_SPELLS.clear()
    BUILDER_WEAPONS.clear()
    BUILDER_ARMOR.clear()
    
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        tables = []
        try:
            for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                tables.append(row[0].lower())
        except: pass

        t_ancestry = next((t for t in ['ancestries', 'ancestry'] if t in tables), None)
        t_heritage = next((t for t in ['heritages', 'heritage'] if t in tables), None)
        t_bg = next((t for t in ['backgrounds', 'background'] if t in tables), None)
        t_class = next((t for t in ['classes', 'class'] if t in tables), None)
        t_feat = next((t for t in ['feats', 'feat'] if t in tables), None)
        t_spell = next((t for t in ['spells', 'spell'] if t in tables), None)
        
        equip_tables = [t for t in tables if t in ['equipment', 'items', 'item', 'weapons', 'weapon', 'armor']]
        
        if t_ancestry:
            try:
                for r in c.execute(f"SELECT * FROM {t_ancestry}"):
                    try:
                        cols = r.keys()
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

                        traits = extract_traits(get_col(r, 'traits', '[]'))
                        if not traits and isinstance(sys_data, dict):
                            traits = extract_traits(sys_data.get('traits', {}))

                        rarity = get_rarity(sys_data, r, traits)

                        boosts = safe_json_load(r, 'boosts', {})
                        flaws = safe_json_load(r, 'flaws', [])
                        hp = get_col(r, 'hp', 8)
                        
                        name = get_col(r, 'name', 'Unknown')
                        BUILDER_ANCESTRIES[name] = {'boosts': boosts, 'flaws': flaws, 'hp': hp, 'rarity': rarity, 'description': clean_foundry_text(desc)}
                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        _merge_rules(name, safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or [])
                    except: pass
            except: pass
            
        if t_heritage:
            known_ancestries = {a.lower(): a for a in BUILDER_ANCESTRIES.keys()}
            try:
                for r in c.execute(f"SELECT * FROM {t_heritage}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')
                                
                        anc_key = "universal"
                        if get_col(r, 'ancestry'):
                            anc_val = str(get_col(r, 'ancestry')).strip()
                            if anc_val.startswith('{'):
                                try:
                                    anc_dict = json.loads(anc_val)
                                    anc_key = str(anc_dict.get('slug') or anc_dict.get('name') or "universal").lower()
                                except:
                                    anc_key = "universal"
                            else:
                                anc_key = anc_val.lower()
                                
                        if anc_key == "universal" and isinstance(sys_data, dict):
                            ad = sys_data.get('ancestry', {})
                            if isinstance(ad, dict):
                                anc_key = str(ad.get('slug') or ad.get('name') or "universal").lower()
                                
                        resolved_key = "universal"
                        anc_key_clean = anc_key.lower().replace('-', ' ').replace('_', ' ')
                        for known in known_ancestries:
                            if known == anc_key_clean or known.replace('-', ' ') == anc_key_clean:
                                resolved_key = known
                                break
                        
                        traits = extract_traits(get_col(r, 'traits', '[]'))
                        if not traits and isinstance(sys_data, dict):
                            traits = extract_traits(sys_data.get('traits', {}))

                        if resolved_key == "universal":
                            for t in traits:
                                t_clean = str(t).lower().replace('-', ' ')
                                for known in known_ancestries:
                                    if known == t_clean or known.replace('-', ' ') == t_clean:
                                        resolved_key = known
                                        break
                                if resolved_key != "universal": break
                                    
                        anc_key = resolved_key
                        rarity = get_rarity(sys_data, r, traits)
                        
                        if anc_key not in BUILDER_DATA["heritages"]:
                            BUILDER_DATA["heritages"][anc_key] = []
                            
                        existing = [h['name'] for h in BUILDER_DATA["heritages"][anc_key]]
                        if name not in existing:
                            BUILDER_DATA["heritages"][anc_key].append({"name": name, "desc": clean_foundry_text(desc), "rarity": rarity})

                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        _merge_rules(name, safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or [])
                    except Exception as e: pass
            except: pass
        
        if t_bg:
            try:
                for r in c.execute(f"SELECT * FROM {t_bg}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

                        traits = extract_traits(get_col(r, 'traits', '[]'))
                        if not traits and isinstance(sys_data, dict):
                            traits = extract_traits(sys_data.get('traits', {}))

                        rarity = get_rarity(sys_data, r, traits)
                        t_lower = [str(t).lower() for t in traits]
                        
                        bg_cat = 'general'
                        if 'regional' in t_lower: bg_cat = 'regional'
                        elif rarity in ['uncommon', 'rare', 'unique']: bg_cat = 'campaign'

                        boosts = safe_json_load(r, 'boosts', {})
                        skills_raw = safe_json_load(r, 'skills', [])
                        skills = []
                        
                        if isinstance(skills_raw, dict): skills = skills_raw.get('value', [])
                        elif isinstance(skills_raw, list): skills = skills_raw

                        clean_desc = clean_foundry_text(desc).lower()
                        bg_feat = ""
                        
                        if clean_desc:
                            match_str = clean_desc.replace('<strong>', '').replace('</strong>', '').replace('<b>', '').replace('</b>', '')
                            feat_match = re.search(r'gain the ([\w\s\']+) (?:skill )?feat', match_str)
                            if feat_match:
                                bg_feat = feat_match.group(1).title().strip()
                        
                        # Also check rule_elements for GrantItem feat grants
                        if not bg_feat:
                            rules_raw = safe_json_load(r, 'rule_elements', [])
                            if isinstance(rules_raw, list):
                                for rule in rules_raw:
                                    if isinstance(rule, dict) and rule.get('key') == 'GrantItem':
                                        uuid_str = str(rule.get('uuid', ''))
                                        if 'feats-srd' in uuid_str or 'feat' in uuid_str.lower():
                                            feat_name = uuid_str.split('.')[-1] if '.' in uuid_str else ''
                                            if feat_name and not feat_name.startswith('{'):
                                                bg_feat = feat_name
                                                break
                                
                            if not skills:
                                for sk in ['acrobatics', 'arcana', 'athletics', 'crafting', 'deception', 'diplomacy', 'intimidation', 'medicine', 'nature', 'occultism', 'performance', 'religion', 'society', 'stealth', 'survival', 'thievery']:
                                    if f"trained in {sk}" in match_str or f"trained in the {sk}" in match_str:
                                        skills.append(sk)
                                lore_matches = re.findall(r'trained in (?:the )?([\w\s]+) lore', match_str)
                                for lm in lore_matches:
                                    skills.append(f"lore: {lm.strip()}")
                                
                        clean_skills = [str(s).lower().strip() if not str(s).lower().strip().startswith('lore:') else 'lore: ' + str(s).lower().replace('lore', '').strip() for s in skills]

                        BUILDER_BACKGROUNDS[name] = {'boosts': boosts, 'skills': clean_skills, 'feat': bg_feat, 'description': clean_foundry_text(desc), 'category': bg_cat}
                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        _merge_rules(name, safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or [])
                    except Exception as e: pass
            except: pass
        
        if t_class:
            try:
                for r in c.execute(f"SELECT * FROM {t_class}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

                        traits = extract_traits(get_col(r, 'traits', '[]'))
                        if not traits and isinstance(sys_data, dict):
                            traits = extract_traits(sys_data.get('traits', {}))
                            
                        rarity = get_rarity(sys_data, r, traits)
                        
                        core_classes = ['alchemist', 'barbarian', 'bard', 'champion', 'cleric', 'druid', 'fighter', 'monk', 'ranger', 'rogue', 'sorcerer', 'wizard']
                        c_lower = name.lower()
                        if c_lower in core_classes: c_cat = 'core'
                        elif 'archetype' in c_lower or 'class archetype' in [str(t).lower() for t in traits]: c_cat = 'class_archetype'
                        else: c_cat = 'expanded'

                        key_ab = safe_json_load(r, 'key_ability', [])
                        hp = get_col(r, 'hp', 8)
                        
                        BUILDER_CLASSES[name] = {'keyAbility': key_ab, 'hp': hp, 'rarity': rarity, 'category': c_cat, 'description': clean_foundry_text(desc)}
                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        _merge_rules(name, safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or [])
                        
                        if c_lower not in BUILDER_DATA['classes']:
                            BUILDER_DATA['classes'][c_lower] = {
                                "key_options": key_ab if key_ab else ["str"],
                                "base_skills": [],
                                "free_skills": 3,
                                "spellcasting": None,
                                "subclasses": []
                            }
                    except: pass
            except: pass
        
        if t_feat:
            try:
                for r in c.execute(f"SELECT * FROM {t_feat}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

                        cat = get_col(r, 'category', 'general')
                        lvl = get_col(r, 'level', 1)
                        
                        traits_raw = get_col(r, 'traits', '[]')
                        traits = extract_traits(traits_raw)
                        if not traits and isinstance(sys_data, dict):
                            traits = extract_traits(sys_data.get('traits', {}))

                        prereq_raw = ""
                        prereq_parsed = {"stats": {}, "skills": {}}
                        
                        prereq_match = re.search(r'(?:<strong>)?Prerequisites(?:</strong>)?\s*(?:</[a-z]+>)?\s*(.*?)</p>', desc, re.IGNORECASE)
                        if prereq_match:
                            prereq_raw = prereq_match.group(1)
                            prereq_raw = re.sub(r'@\w+\[.*?\]\{(.*?)\}', r'\1', prereq_raw)
                            
                            s_lower = prereq_raw.lower()
                            stat_map = {"strength": "str", "dexterity": "dex", "constitution": "con", "intelligence": "int", "wisdom": "wis", "charisma": "cha"}
                            for full_stat, short_stat in stat_map.items():
                                match = re.search(fr'{full_stat}\s*(?:score\s*of\s*)?(\d+)', s_lower)
                                if match:
                                    score = int(match.group(1))
                                    prereq_parsed["stats"][short_stat] = math.floor((score - 10) / 2) if score >= 10 else score
                                match_mod = re.search(fr'{full_stat}\s*\+(\d+)', s_lower)
                                if match_mod:
                                    prereq_parsed["stats"][short_stat] = int(match_mod.group(1))
                                    
                            rank_map = {"trained": 2, "expert": 4, "master": 6, "legendary": 8}
                            skill_names = ['acrobatics', 'arcana', 'athletics', 'crafting', 'deception', 'diplomacy', 'intimidation', 'medicine', 'nature', 'occultism', 'performance', 'religion', 'society', 'stealth', 'survival', 'thievery']
                            for rank_str, rank_val in rank_map.items():
                                for sk in skill_names:
                                    if re.search(fr'{rank_str}\s*(?:in)?\s*{sk}', s_lower):
                                        prereq_parsed["skills"][sk] = max(prereq_parsed["skills"].get(sk, 0), rank_val)
                        
                        if cat in BUILDER_FEATS: 
                            BUILDER_FEATS[cat].append({'name': name, 'level': lvl, 'traits': traits, 'prerequisites_raw': prereq_raw, 'prereqs_parsed': prereq_parsed, 'description': clean_foundry_text(desc)})
                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        # Load rules from rule_elements column (direct) or system.rules (Foundry format)
                        feat_rules = safe_json_load(r, 'rule_elements', [])
                        if not feat_rules and isinstance(sys_data, dict):
                            feat_rules = sys_data.get('rules') or []
                        _merge_rules(name, feat_rules)
                    except: pass
            except: pass
        
        if t_spell:
            try:
                spell_map = {}  # name -> best entry (prefer ones with traditions)
                for r in c.execute(f"SELECT * FROM {t_spell}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        # Skip Foundry folder-label rows ingested as fake spells
                        # ("Cantrip", "Rank 1".."Rank 10", "Rituals", "Spells",
                        # "Focus") — empty descriptions, no action cost, garbage
                        # in spell pickers.
                        _jl = name.strip().lower()
                        if _jl in ('cantrip', 'focus', 'rituals', 'spells') or re.match(r'^rank \d+$', _jl):
                            continue
                        sys_data = safe_json_load(r, 'system', {})

                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')
                        # Backfill the handful of rituals with empty Foundry descriptions (AoN SRD).
                        if not (desc or '').strip() and _jl in _SPELL_DESC_OVERRIDES:
                            desc = _SPELL_DESC_OVERRIDES[_jl]

                        lvl = get_col(r, 'level', 1)
                        
                        traditions_raw = get_col(r, 'traditions', '[]')
                        traditions = extract_traits(traditions_raw)
                        if not traditions and isinstance(sys_data, dict):
                            traditions = extract_traits(sys_data.get('traits', {}).get('traditions', []))

                        clean_desc = clean_foundry_text(desc)
                        entry = {'name': name, 'level': lvl, 'traditions': traditions, 'description': clean_desc}
                        
                        # Keep the version with more data (traditions populated, longer description)
                        if name not in spell_map:
                            spell_map[name] = entry
                        else:
                            existing = spell_map[name]
                            if len(traditions) > len(existing['traditions']):
                                spell_map[name] = entry
                            elif not existing['description'] and clean_desc:
                                spell_map[name] = entry
                        
                        if clean_desc:
                            COMPENDIUM_LIBRARY[name.lower()] = clean_desc
                    except: pass
                
                BUILDER_SPELLS.extend(spell_map.values())
            except: pass
        
        for t_equip in equip_tables:
            try:
                for r in c.execute(f"SELECT * FROM {t_equip}"):
                    try:
                        cols = r.keys()
                        name = get_col(r, 'name', 'Unknown')
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

                        COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                        _merge_rules(name, safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or [])
                        
                        item_type = get_col(r, 'type', '').lower()
                        if not item_type and 'type' in cols: item_type = str(r['type']).lower()
                        if not item_type and isinstance(sys_data, dict): item_type = sys_data.get('type', '').lower()
                        
                        # ARMOR EXTRACTION
                        if item_type == 'armor' or 'armor' in t_equip.lower():
                            # Read from direct DB columns first, fall back to sys_data
                            ac = safe_int(get_col(r, 'ac_bonus', 0))
                            if ac == 0: ac = safe_int(get_nested_val(sys_data, ['acBonus', 'armor', 'ac']))
                            dex = safe_int(get_col(r, 'dex_cap', 99))
                            if dex == 99: dex = safe_int(get_nested_val(sys_data, ['dexCap', 'dex']))
                            pen = safe_int(get_col(r, 'check_penalty', 0))
                            if pen == 0: pen = safe_int(get_nested_val(sys_data, ['checkPenalty', 'penalty']))
                            spd = safe_int(get_nested_val(sys_data, ['speedPenalty', 'speed']))
                            s_req = safe_int(get_nested_val(sys_data, ['strength', 'str']))
                            b_val = str(get_nested_val(sys_data, ['bulk'], '0'))
                            item_level = safe_int(get_col(r, 'level', 0))
                            traits = extract_traits(get_col(r, 'traits', '[]'))
                            if not traits and isinstance(sys_data, dict): traits = extract_traits(sys_data.get('traits', {}))
                            item_desc = clean_foundry_text(desc) if desc else ''
                            
                            # Determine armor category from traits
                            armor_cat = 'unarmored'
                            traits_lower = [t.lower() for t in traits]
                            if 'heavy' in traits_lower or ac >= 5: armor_cat = 'heavy'
                            elif 'medium' in traits_lower or ac >= 3: armor_cat = 'medium'
                            elif 'light' in traits_lower or ac >= 1: armor_cat = 'light'
                            
                            # Estimate speed penalty and str req from AC if not in data
                            if spd == 0 and ac >= 5: spd = -10
                            elif spd == 0 and ac >= 3: spd = -5
                            if s_req == 0 and ac >= 5: s_req = 16
                            elif s_req == 0 and ac >= 3: s_req = 14
                            elif s_req == 0 and ac >= 2: s_req = 12
                            
                            if not any(a['name'] == name for a in BUILDER_ARMOR):
                                BUILDER_ARMOR.append({
                                    'name': name, 'ac': ac, 'dex_cap': dex, 'penalty': pen,
                                    'speed_penalty': spd, 'str_req': s_req, 'bulk': b_val,
                                    'traits': traits, 'level': item_level, 'category': armor_cat,
                                    'description': item_desc[:500]
                                })

                        # WEAPON EXTRACTION
                        elif item_type == 'weapon' or 'weapon' in t_equip.lower(): 
                            dmg = get_col(r, 'damage_die', '')
                            if not dmg and isinstance(sys_data, dict):
                                dmg_dict = sys_data.get('damage', {})
                                if isinstance(dmg_dict, dict) and 'die' in dmg_dict:
                                    dice_count = dmg_dict.get('dice', 1)
                                    die_size = dmg_dict.get('die', 'd4')
                                    dmg_type = dmg_dict.get('damageType', '')
                                    dmg_letter = dmg_type[0].upper() if isinstance(dmg_type, str) and dmg_type else ''
                                    dmg = f"{dice_count}{die_size} {dmg_letter}".strip()
                            if not dmg: dmg = '1d4'
                            
                            traits_raw = get_col(r, 'traits', '[]')
                            traits = extract_traits(traits_raw)
                            if not traits and isinstance(sys_data, dict):
                                traits = extract_traits(sys_data.get('traits', {}))
                            
                            item_level = safe_int(get_col(r, 'level', 0))
                            item_desc = clean_foundry_text(desc) if desc else ''
                            
                            # Determine weapon category from traits
                            weapon_cat = 'simple'
                            traits_lower = [t.lower() for t in traits]
                            if 'advanced' in traits_lower: weapon_cat = 'advanced'
                            elif 'martial' in traits_lower: weapon_cat = 'martial'
                            
                            if not any(w['name'] == name for w in BUILDER_WEAPONS):
                                BUILDER_WEAPONS.append({
                                    'name': name, 'damage': dmg, 'traits': traits,
                                    'level': item_level, 'category': weapon_cat,
                                    'description': item_desc[:500]
                                })
                    except: pass
            except: pass
                
        conn.close()

    # --- RAW COMPENDIUM DATA JSON SCRAPER ---
    if os.path.exists(COMPENDIUM_DATA_DIR):
        p_anc = os.path.join(COMPENDIUM_DATA_DIR, 'ancestries')
        if os.path.exists(p_anc):
            for root, _, files in os.walk(p_anc):
                for f in files:
                    if f.endswith('.json'):
                        data, err = safe_load_json_file(os.path.join(root, f))
                        if err or not data:
                            continue
                        try:
                            name = data.get('name')
                            if name and name not in BUILDER_ANCESTRIES:
                                sys = data.get('system', {})
                                desc = sys.get('description', {}).get('value', '')
                                traits = extract_traits(sys.get('traits', {}))
                                rarity = get_rarity(sys, {}, traits)
                                BUILDER_ANCESTRIES[name] = {'boosts': sys.get('boosts', {}), 'flaws': sys.get('flaws', []), 'hp': sys.get('hp', 8), 'rarity': rarity, 'description': clean_foundry_text(desc)}
                                _merge_rules(name, sys.get('rules') or [])
                        except: pass

        p_her = os.path.join(COMPENDIUM_DATA_DIR, 'heritages')
        if os.path.exists(p_her):
            known_ancestries = {a.lower(): a for a in BUILDER_ANCESTRIES.keys()}
            for root, _, files in os.walk(p_her):
                for f in files:
                    if f.endswith('.json'):
                        data, err = safe_load_json_file(os.path.join(root, f))
                        if err or not data:
                            continue
                        try:
                            name = data.get('name')
                            sys = data.get('system', {})
                            desc = sys.get('description', {}).get('value', '')
                            
                            folder_name = os.path.basename(root).lower()
                            anc_key = "universal"
                            
                            if isinstance(sys.get('ancestry'), dict):
                                anc_key = str(sys['ancestry'].get('slug') or sys['ancestry'].get('name') or folder_name).lower()
                            else:
                                anc_key = folder_name
                                
                            resolved_key = "universal"
                            anc_key_clean = anc_key.replace('-', ' ').replace('_', ' ')
                            for known in known_ancestries:
                                if known == anc_key_clean or known.replace('-', ' ') == anc_key_clean:
                                    resolved_key = known
                                    break
                                
                            traits = extract_traits(sys.get('traits', {}))
                            rarity = get_rarity(sys, {}, traits)
                            
                            anc_key = resolved_key
                                
                            if anc_key not in BUILDER_DATA["heritages"]:
                                BUILDER_DATA["heritages"][anc_key] = []
                                
                            existing = [h['name'] for h in BUILDER_DATA["heritages"][anc_key]]
                            if name not in existing:
                                BUILDER_DATA["heritages"][anc_key].append({"name": name, "desc": clean_foundry_text(desc), "rarity": rarity})
                            _merge_rules(name, sys.get('rules') or [])
                        except: pass

        p_bg = os.path.join(COMPENDIUM_DATA_DIR, 'backgrounds')
        if os.path.exists(p_bg):
            for root, _, files in os.walk(p_bg):
                for f in files:
                    if f.endswith('.json'):
                        data, err = safe_load_json_file(os.path.join(root, f))
                        if err or not data:
                            continue
                        try:
                            name = data.get('name')
                            if name and name not in BUILDER_BACKGROUNDS:
                                sys = data.get('system', {})
                                desc = sys.get('description', {}).get('value', '')
                                
                                traits = extract_traits(sys.get('traits', {}))
                                rarity = get_rarity(sys, {}, traits)
                                t_lower = [str(t).lower() for t in traits]
                                
                                bg_cat = 'general'
                                if 'regional' in t_lower: bg_cat = 'regional'
                                elif rarity in ['uncommon', 'rare', 'unique']: bg_cat = 'campaign'
                                
                                BUILDER_BACKGROUNDS[name] = {'boosts': sys.get('boosts', {}), 'skills': sys.get('skills', {}).get('value', []), 'feat': '', 'description': clean_foundry_text(desc), 'category': bg_cat}
                                _merge_rules(name, sys.get('rules') or [])
                        except: pass

        p_cls = os.path.join(COMPENDIUM_DATA_DIR, 'classes')
        if os.path.exists(p_cls):
            for root, _, files in os.walk(p_cls):
                for f in files:
                    if f.endswith('.json'):
                        data, err = safe_load_json_file(os.path.join(root, f))
                        if err or not data:
                            continue
                        try:
                            name = data.get('name')
                            if name and name not in BUILDER_CLASSES:
                                sys = data.get('system', {})
                                desc = sys.get('description', {}).get('value', '')
                                
                                traits = extract_traits(sys.get('traits', {}))
                                rarity = get_rarity(sys, {}, traits)
                                
                                core_classes = ['alchemist', 'barbarian', 'bard', 'champion', 'cleric', 'druid', 'fighter', 'monk', 'ranger', 'rogue', 'sorcerer', 'wizard']
                                c_lower = name.lower()
                                if c_lower in core_classes: c_cat = 'core'
                                elif 'archetype' in c_lower or 'class archetype' in [str(t).lower() for t in traits]: c_cat = 'class_archetype'
                                else: c_cat = 'expanded'
                                
                                BUILDER_CLASSES[name] = {'keyAbility': sys.get('key_ability', []), 'hp': sys.get('hp', 8), 'rarity': rarity, 'category': c_cat, 'description': clean_foundry_text(desc)}
                                _merge_rules(name, sys.get('rules') or [])
                                
                                if c_lower not in BUILDER_DATA['classes']:
                                    BUILDER_DATA['classes'][c_lower] = {
                                        "key_options": sys.get('key_ability', []) if sys.get('key_ability', []) else ["str"],
                                        "base_skills": [],
                                        "free_skills": 3,
                                        "spellcasting": None,
                                        "subclasses": []
                                    }
                        except: pass

        for folder in ['equipment', 'weapons', 'items', 'armor']:
            p_eq = os.path.join(COMPENDIUM_DATA_DIR, folder)
            if os.path.exists(p_eq):
                for root, _, files in os.walk(p_eq):
                    for f in files:
                        if f.endswith('.json'):
                            data, err = safe_load_json_file(os.path.join(root, f))
                            if err or not data:
                                continue
                            try:
                                name = data.get('name')
                                if not name: continue
                                sys = data.get('system', {})
                                desc = sys.get('description', {}).get('value', '')
                                COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                                _merge_rules(name, sys.get('rules') or [])
                                
                                item_type = data.get('type', '').lower()
                                if item_type == 'weapon' or 'weapon' in folder.lower():
                                    dmg_dict = sys.get('damage', {})
                                    dice_count = dmg_dict.get('dice', 1)
                                    die_size = dmg_dict.get('die', 'd4')
                                    dmg_type = dmg_dict.get('damageType', '')
                                    dmg_letter = dmg_type[0].upper() if isinstance(dmg_type, str) and dmg_type else ''
                                    dmg = f"{dice_count}{die_size} {dmg_letter}".strip()
                                    
                                    traits = extract_traits(sys.get('traits', {}))
                                    if not any(w['name'] == name for w in BUILDER_WEAPONS):
                                        BUILDER_WEAPONS.append({'name': name, 'damage': dmg, 'traits': traits})
                                        
                                elif item_type == 'armor' or 'armor' in folder.lower():
                                    ac = safe_int(get_nested_val(sys, ['acBonus', 'armor', 'ac']))
                                    dex = safe_int(get_nested_val(sys, ['dexCap', 'dex']))
                                    pen = safe_int(get_nested_val(sys, ['checkPenalty', 'penalty']))
                                    spd = safe_int(get_nested_val(sys, ['speedPenalty', 'speed']))
                                    s_req = safe_int(get_nested_val(sys, ['strength', 'str']))
                                    b_val = str(get_nested_val(sys, ['bulk'], '0'))
                                    traits = extract_traits(sys.get('traits', {}))
                                    if not any(a['name'] == name for a in BUILDER_ARMOR): 
                                        BUILDER_ARMOR.append({'name': name, 'ac': ac, 'dex_cap': dex, 'penalty': pen, 'speed_penalty': spd, 'str_req': s_req, 'bulk': b_val, 'traits': traits})
                            except: pass

        for folder in ['classfeatures', 'class-features', 'feats']:
            p_cf = os.path.join(COMPENDIUM_DATA_DIR, folder)
            if os.path.exists(p_cf):
                for root, _, files in os.walk(p_cf):
                    for f in files:
                        if f.endswith('.json'):
                            data, err = safe_load_json_file(os.path.join(root, f))
                            if err or not data:
                                continue
                            try:
                                name = data.get('name')
                                sys = data.get('system', {})
                                desc = sys.get('description', {}).get('value', '')
                                if name and desc:
                                    COMPENDIUM_LIBRARY[name.lower()] = clean_foundry_text(desc)
                                    _merge_rules(name, sys.get('rules') or [])
                            except: pass

    for c_key, c_data in BUILDER_DATA['classes'].items():
        if 'subclasses' in c_data:
            updated_subs = []
            for sub in c_data['subclasses']:
                s_name = sub if isinstance(sub, str) else sub.get('name', 'Unknown')
                desc = COMPENDIUM_LIBRARY.get(s_name.lower(), '')
                if not desc:
                    lbl = c_data.get('subclass_label', '').lower()
                    desc = COMPENDIUM_LIBRARY.get(f"{s_name.lower()} {lbl}", '')
                if not desc:
                    desc = f"<p>Specialization for {c_key.capitalize()}.</p>"
                updated_subs.append({"name": s_name, "desc": desc})
            c_data['subclasses'] = updated_subs

def load_libraries():
    load_compendium()
    
    # --- POST-LOAD CORRECTION: Fix weapon damage from known table ---
    for w in BUILDER_WEAPONS:
        if w['damage'] == '1d4' or w['damage'] == '1d4 ':
            correct = PF2E_WEAPON_DAMAGE.get(w['name'])
            if correct:
                w['damage'] = correct
        if not w.get('category') or w['category'] == 'simple':
            cat = PF2E_WEAPON_CATEGORIES.get(w['name'])
            if cat:
                w['category'] = cat
    
    MONSTER_LIBRARY.clear()
    # Load monsters from all available directories:
    # 1. DATA_DIR/monster_data (persistent volume on Railway — user-added monsters)
    # 2. BASE_DIR/monster_data (repo-bundled bestiaries — always present)
    monster_dirs = [MONSTER_DIR]
    repo_monster_dir = os.path.join(BASE_DIR, 'monster_data')
    if repo_monster_dir != MONSTER_DIR and os.path.exists(repo_monster_dir):
        monster_dirs.append(repo_monster_dir)
    for mdir in monster_dirs:
        if not os.path.exists(mdir):
            continue
        for root, dirs, files in os.walk(mdir):
            for file in files:
                if file.endswith('.json') and not file.startswith('_'):
                    file_path = os.path.join(root, file)
                    data, err = safe_load_json_file(file_path)
                    if err:
                        print(f"[LOAD ERROR] Monster {file}: {err}")
                        continue
                    try:
                        if isinstance(data, dict) and ('system' in data or data.get('type') == 'npc'):
                            rel_path = os.path.relpath(file_path, mdir)
                            if rel_path not in MONSTER_LIBRARY:  # Don't overwrite user-added monsters
                                MONSTER_LIBRARY[rel_path] = Monster(data, rel_path)
                    except Exception as e:
                        print(f"[LOAD ERROR] Monster {file}: {e}")
    print(f"[STARTUP] Loaded {len(MONSTER_LIBRARY)} monsters from {len(monster_dirs)} director{'ies' if len(monster_dirs) > 1 else 'y'}")
    
    PARTY_LIBRARY.clear()
    if not os.path.exists(PARTY_DIR): os.makedirs(PARTY_DIR) 
    for file in os.listdir(PARTY_DIR):
        if file.endswith('.json'):
            file_path = os.path.join(PARTY_DIR, file)
            data, err = safe_load_json_file(file_path)
            if err:
                print(f"[LOAD ERROR] Character {file}: {err}")
                continue
            try:
                if isinstance(data, list):
                    for idx, char_data in enumerate(data):
                        pc = make_actor(char_data, f"{file}[{idx}]")
                        PARTY_LIBRARY[pc.name] = pc
                else:
                    pc = make_actor(data, file)
                    PARTY_LIBRARY[pc.name] = pc
            except Exception as e: 
                print(f"[LOAD ERROR] Character {file}: {e}")
    _build_pc_file_cache()
    
    # --- AUTO-RESTORE ENCOUNTER FROM AUTOSAVE ---
    _restore_encounter_autosave()

def _restore_encounter_autosave():
    """Restore the active encounter from autosave file on startup."""
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER, ENCOUNTER_NOTES, SESSION_TIMER_START
    autosave_path = os.path.join(ENCOUNTER_DIR, '_autosave.json')
    if not os.path.exists(autosave_path):
        return
    try:
        with open(autosave_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        combatants = raw.get('combatants', [])
        ROUND_NUMBER = raw.get('round', 1)
        TURN_INDEX = raw.get('turn_index', 0)
        ENCOUNTER_NOTES = raw.get('notes', '')
        SESSION_TIMER_START = raw.get('session_timer_start', None)
        ACTIVE_ENCOUNTER.clear()
        for item in combatants:
            new_c = None
            # Cosmere combatants rebuild from their source id (bestiary _id or
            # campaign PC id), not the PF2e MONSTER/PARTY libraries.
            if item.get('system') == 'cosmere':
                cos = _restore_cosmere_combatant(item)
                if cos is not None:
                    ACTIVE_ENCOUNTER.append(cos)
                continue
            if item.get('type') == 'monster' and item.get('path') in MONSTER_LIBRARY:
                new_c = copy.deepcopy(MONSTER_LIBRARY[item['path']])
            elif item.get('type') == 'pc' and item.get('path') in PARTY_LIBRARY:
                new_c = copy.deepcopy(PARTY_LIBRARY[item['path']])
            if new_c:
                new_c.instance_id = item.get('instance_id', str(uuid.uuid4()))
                new_c.initiative = item.get('initiative', 0)
                if 'current_hp' in item: new_c.current_hp = item['current_hp']
                if 'conditions' in item: new_c.conditions = item['conditions']
                if 'condition_expiry' in item:
                    new_c.condition_expiry = dict(item.get('condition_expiry') or {})
                if 'persistent_damage' in item:
                    _pd_in = item['persistent_damage']
                    # Heal corrupt stale values: historical saves may contain the
                    # literal string "[]" (which becomes ['[',']'] if later fed
                    # through list()). PCs use list-of-dicts; monsters use the
                    # legacy string format.
                    if new_c.is_pc:
                        if isinstance(_pd_in, list):
                            new_c.persistent_damage = [e for e in _pd_in if isinstance(e, dict)]
                        else:
                            new_c.persistent_damage = []
                    else:
                        if isinstance(_pd_in, str):
                            s = _pd_in.strip()
                            new_c.persistent_damage = '' if s in ('[]', '{}') else _pd_in
                        else:
                            new_c.persistent_damage = _pd_in or ''
                if 'delaying' in item: new_c.delaying = item['delaying']
                if 'elite_weak' in item and hasattr(new_c, 'apply_elite_weak'):
                    new_c.apply_elite_weak(item['elite_weak'])
                # Restore hidden/visible state — PCs are always visible; for
                # NPCs we honor the saved flag (default True for old saves).
                if 'visible_to_players' in item:
                    new_c.visible_to_players = bool(item['visible_to_players']) if not new_c.is_pc else True
                if 'epithet' in item and not new_c.is_pc:
                    new_c.epithet = str(item['epithet'] or '')
                # GM creature tactics notes.
                if 'tactics' in item:
                    new_c.tactics = str(item['tactics'] or '')
                ACTIVE_ENCOUNTER.append(new_c)
        if TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = 0
        if ACTIVE_ENCOUNTER:
            print(f"[ENCOUNTER] Restored autosave: {len(ACTIVE_ENCOUNTER)} combatants, Round {ROUND_NUMBER}")
    except Exception as e:
        print(f"[ENCOUNTER] Failed to restore autosave: {e}")

load_libraries()

@app.route('/health')
def health_check():
    """Health check endpoint for Railway/container orchestration."""
    return jsonify({
        'status': 'healthy',
        'party_count': len(PARTY_LIBRARY),
        'monster_count': len(MONSTER_LIBRARY),
        'encounter_active': len(ACTIVE_ENCOUNTER),
        'sse_connections': sse_subscriber_count(),
    })

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(Exception)
def handle_uncaught(e):
    """Turn any uncaught exception on an /api/ route into a JSON error.

    Without this, an unhandled error returns Flask's HTML 500 page, which makes
    the client's fetch().then(r => r.json()) throw on parse -- so the action
    fails silently (the player/GM clicks and nothing happens, no error). For
    /api/ paths we always return JSON; non-API routes keep normal HTML pages.
    The 404 handler above still wins for 404s (it is more specific).
    """
    code = e.code if isinstance(e, HTTPException) else 500
    if request.path.startswith('/api/'):
        if isinstance(e, HTTPException):
            # Intentional, developer-set message (e.g. "GM access required").
            msg = e.description or e.name
        else:
            # NEVER surface raw exception text to the client: a FileNotFoundError
            # etc. carries absolute server paths. Log it server-side, return a
            # generic message.
            app.logger.exception('Unhandled error on %s', request.path)
            msg = 'Internal server error'
        return jsonify(success=False, error=msg), code
    if isinstance(e, HTTPException):
        return e
    app.logger.exception('Unhandled error on %s', request.path)
    return 'Internal Server Error', 500

@app.route('/api/perf')
def perf_metrics():
    """Lightweight perf snapshot for at-the-table debugging. Returns SSE
    subscriber count + lifetime broadcast counters + a coalesced-rate hint
    so the GM can spot a broadcast storm if the table feels sluggish."""
    with _PERF_COUNTERS_LOCK:
        snap = dict(_PERF_COUNTERS)
    pc_total = max(1, snap.get('pc_broadcast_total', 0))
    enc_total = max(1, snap.get('enc_broadcast_total', 0))
    snap['sse_connections'] = sse_subscriber_count()
    snap['pc_coalesce_rate'] = round(snap.get('pc_broadcast_coalesced', 0) / pc_total, 3)
    snap['enc_coalesce_rate'] = round(snap.get('enc_broadcast_coalesced', 0) / enc_total, 3)
    return jsonify(snap)

CAMPAIGN_DEFAULT = {
    'name': 'Untitled Campaign',
    'tagline': '',
    'intro': '',
    'session_number': 1,
    'next_session_at': '',
    'last_recap': '',
    # Optional CSS-url for the homepage hero background. Drop a portrait /
    # poster-shaped image under static/portraits/ or a public CDN URL and
    # reference it as `/portraits/shades-of-blood.jpg` etc.
    'hero_image': '',
    # Campaign crest — a square emblem (distinct from the wide hero splash)
    # shown centered on the session-start curtain and, later, in the app
    # corner. Uploaded via /api/campaign/crest.
    'crest_image': '',
    # Vault folder the session-recap note picker reads from (newest-first).
    'sessions_folder': 'Sessions',
    # Soundscape -> audio-file mapping (paths relative to CAMPAIGN_AUDIO_DIR).
    # The GM assigns these in the hub; the audio engine plays them on demand.
    'soundscapes': {'tavern': '', 'dungeon': '', 'combat': ''},
    # Current scene mood (Chunk 5). Drives a subtle full-screen tint on every
    # screen. One of: calm | mystery | tension | combat | dread. Persisted so
    # a mid-session reload restores the table's mood.
    'scene_mood': 'calm',
    # Per-campaign module enable-list. Each entry is the filename (no
    # extension) of a file under static/js/modules/. The map / tracker
    # / sheet pages load enabled modules in order. See
    # static/js/modules.js for the host-side hook registry, and
    # static/js/modules/README.md for the drop-in conventions.
    'modules_enabled': [],
    # Cosmere combat initiative house-rule: 'phases' = the rulebook 4-phase
    # fast/slow queue (default); 'traditional' = a rolled d20+Speed initiative
    # order, for tables that don't run fast/slow turns.
    'cosmere_initiative': 'phases',
    # Cosmere VISUAL world-skin: 'stormlight' (storm-slate + cyan + Cormorant
    # headings, default) or 'mistborn' (ash-charcoal + pewter + Playfair). Pure
    # theming, selectable per campaign -- the whole Cosmere side re-skins, but
    # characters still use the (Stormlight) Cosmere ruleset underneath.
    'cosmere_world': 'stormlight',
    # How the party advances: 'milestone' (GM marks the party ready to level, no
    # XP math; default) or 'xp' (track XP per PC, auto-award the encounter total,
    # roll over at 1000). XP mode is PF2e-only; Cosmere always uses milestone.
    'advancement_mode': 'milestone',
    # Table safety tools. 'lines' = hard "we don't go there" topics; 'veils' =
    # "happens off-screen, don't dwell"; 'notes' = free-text content agreement.
    # The X-Card (a one-tap, anonymous "pause the game" signal any member can
    # fire) reads no state here -- it just broadcasts safety_xcard. Shown on
    # every screen via templates/_sse_hub.html.
    'safety': {'lines': [], 'veils': [], 'notes': ''},
}

MODULES_DIR = os.path.join(BASE_DIR, 'static', 'js', 'modules')

def _list_module_files():
    """Catalog `.js` files under static/js/modules/. Returns
    [{id, filename, mtime, size}]. README + dot-files are skipped."""
    out = []
    if not os.path.exists(MODULES_DIR):
        return out
    for f in sorted(os.listdir(MODULES_DIR)):
        if not f.endswith('.js') or f.startswith('.') or f.startswith('_'):
            continue
        path = os.path.join(MODULES_DIR, f)
        try:
            st = os.stat(path)
        except OSError:
            continue
        out.append({
            'id': f[:-3],            # filename without `.js`
            'filename': f,
            'mtime': st.st_mtime,
            'size': st.st_size,
        })
    return out

def _load_campaign_config():
    """Read campaign.json, falling back to defaults. Always returns the full schema."""
    cfg = dict(CAMPAIGN_DEFAULT)
    if os.path.exists(CAMPAIGN_FILE):
        data, err = safe_load_json_file(CAMPAIGN_FILE)
        if data and isinstance(data, dict):
            for k in CAMPAIGN_DEFAULT:
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
    return cfg

def _save_campaign_config(updates):
    """Merge `updates` (CAMPAIGN_DEFAULT config keys) into campaign.json and persist.

    Reads and rewrites the FULL existing file so the multi-campaign doc's own
    keys -- id / slug / system / members / system_config -- survive a config
    write (campaign.json is the same file the campaign doc lives in). Returns
    the merged config view."""
    # Locked load-merge-save: a recap save racing a session-number bump (or two
    # GMs/devices) would otherwise read-modify-write the same file and lose one
    # update -- and this file IS the campaign doc (id/system/members live here).
    with _path_lock(CAMPAIGN_FILE):
        full = {}
        if os.path.exists(CAMPAIGN_FILE):
            data, _err = safe_load_json_file(CAMPAIGN_FILE)
            if isinstance(data, dict):
                full = data
        for k in CAMPAIGN_DEFAULT:
            if k in updates and updates[k] is not None:
                full[k] = updates[k]
        if 'session_number' in full:
            try:
                full['session_number'] = max(1, int(full['session_number']))
            except (TypeError, ValueError):
                full['session_number'] = 1
        try:
            with open(CAMPAIGN_FILE, 'w', encoding='utf-8') as fp:
                json.dump(full, fp, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[CAMPAIGN] Failed to write {CAMPAIGN_FILE}: {e}")
    return _load_campaign_config()


# ── GM advancement: XP award + milestone "ready to level" ──────────────────
def _advancement_mode():
    return (_load_campaign_config().get('advancement_mode') or 'milestone')


def _pf2e_set_pc_advancement(name, *, add_xp=None, ready=None):
    """Update a PF2e PC's advancement state on disk (xp / ready_to_level),
    reload it into PARTY_LIBRARY, and broadcast so the sheet badge repaints.
    XP rolls the ready flag on at 1000. Returns a small summary or None."""
    fp = get_pc_file_path(name)
    if not fp or not os.path.exists(fp):
        return None
    with _path_lock(fp):
        doc = _storage.load_json(fp)
        if not isinstance(doc, dict):
            return None
        build = doc.get('build')
        if not isinstance(build, dict):
            build = doc.setdefault('build', {})
        if add_xp is not None:
            build['xp'] = max(0, int(build.get('xp', 0) or 0) + int(add_xp))
            if build['xp'] >= 1000:
                build['ready_to_level'] = True
        if ready is not None:
            build['ready_to_level'] = bool(ready)
        _atomic_write_json(fp, doc, indent=2)
        summary = {'name': name, 'xp': int(build.get('xp', 0) or 0),
                   'ready': bool(build.get('ready_to_level'))}
    try:
        reload_single_character(fp)
    except Exception:
        pass
    try:
        _broadcast_pc_state(name)
    except Exception:
        pass
    return summary


def _pf2e_award_xp(amount):
    return [s for s in (_pf2e_set_pc_advancement(n, add_xp=amount)
                        for n in list(PARTY_LIBRARY)) if s]


def _mark_party_ready(ready=True):
    """Milestone: flag every PC in the active campaign ready (or not) to level.
    Works for both systems; the sheet badge links to the right level-up flow."""
    out = []
    if _active_system() == 'cosmere':
        for d in _list_cosmere_pcs():
            pid = d.get('id')
            if not pid:
                continue
            with _path_lock(_cosmere_pc_path(pid)):
                doc = _load_cosmere_pc(pid) or d
                doc['ready_to_level'] = bool(ready)
                _save_cosmere_pc(doc, fsync=False)
            try:
                sse_broadcast('cosmere_player_state',
                              {'pid': pid, 'name': doc.get('name'),
                               'play_state': doc.get('play_state') or {}, 'ready_to_level': bool(ready)})
            except Exception:
                pass
            out.append({'name': doc.get('name'), 'ready': bool(ready)})
    else:
        for n in list(PARTY_LIBRARY):
            s = _pf2e_set_pc_advancement(n, ready=ready)
            if s:
                out.append(s)
    return out


@app.route('/api/gm/advancement_mode', methods=['POST'])
@gm_required
def api_set_advancement_mode():
    mode = ((request.get_json(silent=True) or request.form).get('mode') or '').strip()
    if mode not in ('milestone', 'xp'):
        return jsonify({'ok': False, 'error': 'mode must be milestone or xp'}), 400
    _save_campaign_config({'advancement_mode': mode})
    return jsonify({'ok': True, 'mode': mode})


@app.route('/api/gm/award_xp', methods=['POST'])
@gm_required
def api_award_xp():
    """Award XP to every PF2e PC (a manual amount, or the live encounter's
    computed XP). PF2e-only; Cosmere advances by milestone."""
    if _active_system() != 'pf2e':
        return jsonify({'ok': False, 'error': 'XP award is PF2e-only'}), 400
    data = request.get_json(silent=True) or request.form
    if str(data.get('from_encounter')) in ('1', 'true', 'True', 'on'):
        plv = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc]
                  or [p.level for p in PARTY_LIBRARY.values()] or [1])
        amount = calculate_encounter_xp(ACTIVE_ENCOUNTER, plv)
    else:
        try:
            amount = int(data.get('amount') or 0)
        except (TypeError, ValueError):
            amount = 0
    if amount <= 0:
        return jsonify({'ok': False, 'error': 'no XP to award'}), 400
    party = _pf2e_award_xp(amount)
    _combat_log(f"Awarded {amount} XP to the party.", 'success')
    return jsonify({'ok': True, 'amount': amount, 'party': party})


@app.route('/api/gm/mark_ready', methods=['POST'])
@gm_required
def api_mark_ready():
    """Milestone: mark the whole party ready to level (or clear it)."""
    data = request.get_json(silent=True) or request.form
    ready = str(data.get('ready', '1')) not in ('0', 'false', 'False', '')
    party = _mark_party_ready(ready)
    _combat_log('Party marked ready to level.' if ready else 'Cleared ready-to-level.', 'success')
    return jsonify({'ok': True, 'ready': ready, 'party': party})


_VALID_MOODS = ('calm', 'mystery', 'tension', 'combat', 'dread')


@app.context_processor
def _inject_campaign_chrome():
    """Expose campaign chrome to every template so shared overlays render
    without each route passing them: the nav-bar crest (base.html) and the
    current scene mood (the _scene_mood overlay applies it on load). Cheap:
    one small JSON read per render."""
    try:
        cfg = _load_campaign_config()
        mood = cfg.get('scene_mood', 'calm')
        if mood not in _VALID_MOODS:
            mood = 'calm'
        return {'nav_crest': cfg.get('crest_image', ''), 'scene_mood': mood, 'is_gm': _is_gm(), 'player_name': session.get('player_name', '')}
    except Exception:
        return {'nav_crest': '', 'scene_mood': 'calm', 'is_gm': _is_gm(), 'player_name': session.get('player_name', '')}


# ══════════════════════════════════════════════════════════════════════════
# SESSION-START CURTAIN — "Previously on..." recap from Obsidian + broadcast
# ══════════════════════════════════════════════════════════════════════════

def _session_notes(folder=None):
    """Deprecated: the in-site Obsidian vault was removed. Session recaps are
    entered manually now. Kept as a no-op so the session-start curtain still
    renders cleanly with nothing to pull."""
    return []


def _read_session_note_raw(rel_path):
    """Deprecated: the in-site Obsidian vault was removed."""
    raise FileNotFoundError("vault removed")


def _extract_recap_section(text):
    """No-AI fallback: pull a recap from a note. Prefers a '## Recap' /
    '## Summary' heading section; otherwise the first substantial paragraph."""
    if not text:
        return ''
    import re as _re
    lines = text.splitlines()
    # Look for a Recap/Summary/Previously heading and grab until the next heading.
    for i, ln in enumerate(lines):
        if _re.match(r'^#{1,6}\s*(recap|summary|previously|what happened)', ln.strip(), _re.I):
            section = []
            for nxt in lines[i + 1:]:
                if _re.match(r'^#{1,6}\s', nxt):
                    break
                section.append(nxt)
            blurb = '\n'.join(section).strip()
            if blurb:
                return blurb[:1200]
    # Fallback: first paragraph of >80 chars (skip headings/frontmatter cruft).
    paras = [p.strip() for p in _re.split(r'\n\s*\n', text) if p.strip()]
    for p in paras:
        if p.startswith('#') or p.startswith('---'):
            continue
        if len(p) >= 80:
            return p[:1200]
    return (paras[0][:1200] if paras else '')


def _generate_recap_via_claude(note_text):
    """Summarize a session note into one evocative 'Previously on...' paragraph
    via the Anthropic API. Returns (text, reason): text is the recap on success
    (reason None); on failure text is None and reason is a short human-readable
    diagnostic the caller can surface. Uses urllib — no new dependency."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None, 'no_key'
    note_text = (note_text or '').strip()
    if not note_text:
        return None, 'empty_note'
    # Cap the input so we never ship a huge note to the API.
    note_text = note_text[:12000]
    campaign = _load_campaign_config()
    prompt = (
        f"You are the narrator of a \"Previously on...\" recap for the tabletop campaign "
        f"\"{campaign.get('name', 'the campaign')}\", read aloud to the players at the start of a session. "
        f"Below are the GM's raw notes from the most recent session.\n\n"
        f"Write a flowing, story-format recap — 2 to 3 short paragraphs of vivid narrative prose, "
        f"told in a dramatic narrator voice like the cold-open recap of a TV show. Tell it as a "
        f"STORY: what the party did, the choices they made, who they met, what they discovered or "
        f"lost, the tension and the stakes, and the unresolved threads they carry into tonight. "
        f"Use the characters' names. Keep the momentum and mood high.\n\n"
        f"IMPORTANT — these are game-prep notes, so IGNORE all the mechanical and logistical "
        f"scaffolding and never mention it: room or area numbers, map/grid coordinates and "
        f"directions, encounter or stat-block labels, monster stat lines, dice rolls, DCs, "
        f"initiative, HP/damage numbers, XP, gold and itemized loot, page or section references, "
        f"and any GM shorthand or planning notes for scenes that did not actually happen. "
        f"Translate events into the fiction — e.g. not \"cleared room 4, 30 XP,\" but \"fought "
        f"through the flooded gallery and left its guardians broken behind them.\" "
        f"No headings, no bullet points, no preamble or sign-off — just the recap prose.\n\n"
        f"--- SESSION NOTES ---\n{note_text}\n--- END NOTES ---\n\n"
        f"Write only the recap."
    )
    import urllib.request as _urlreq
    import urllib.error as _urlerr

    def _call(model):
        payload = json.dumps({
            'model': model,
            'max_tokens': 800,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        req = _urlreq.Request(
            'https://api.anthropic.com/v1/messages',
            data=payload,
            headers={
                'content-type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST',
        )
        try:
            with _urlreq.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            blocks = data.get('content') or []
            text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
            return (text, None) if text else (None, 'empty_response')
        except _urlerr.HTTPError as e:
            try:
                err_body = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                err_body = ''
            print(f"[SESSION] Claude recap HTTP {e.code} (model={model}): {err_body}")
            return None, f'api_http_{e.code}: {err_body[:200]}'
        except (_urlerr.URLError, TimeoutError) as e:
            print(f"[SESSION] Claude recap network error: {e}")
            return None, f'network: {getattr(e, "reason", e)}'
        except (ValueError, KeyError) as e:
            print(f"[SESSION] Claude recap parse error: {e}")
            return None, f'parse_error: {e}'

    SAFE_MODEL = 'claude-3-5-haiku-latest'
    model = (os.environ.get('ANTHROPIC_RECAP_MODEL') or SAFE_MODEL).strip()
    text, reason = _call(model)
    # Self-heal: a misconfigured ANTHROPIC_RECAP_MODEL (404 model_not_found)
    # shouldn't kill AI recaps — retry once with the known-good default so the
    # GM still gets a written recap instead of silently falling back to extract.
    if text is None and model != SAFE_MODEL and reason and reason.startswith('api_http_404'):
        print(f"[SESSION] model '{model}' not found — retrying with {SAFE_MODEL}")
        text, reason2 = _call(SAFE_MODEL)
        if text:
            return text, None
        reason = f'{reason} (fallback model also failed: {reason2})'
    return text, reason


@app.route('/api/session/models')
@gm_required
def api_session_models():
    """Diagnostic: ask the Anthropic API which models THIS key can use, so the
    GM can set ANTHROPIC_RECAP_MODEL to a value that actually resolves instead
    of guessing. Also a quick key-validity check (401 = bad key)."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return jsonify({'success': False, 'key_present': False,
                        'error': 'No ANTHROPIC_API_KEY reached the server.'}), 200
    import urllib.request as _urlreq
    import urllib.error as _urlerr
    req = _urlreq.Request(
        'https://api.anthropic.com/v1/models?limit=100',
        headers={'x-api-key': api_key, 'anthropic-version': '2023-06-01'},
        method='GET',
    )
    try:
        with _urlreq.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        models = [{'id': m.get('id'), 'name': m.get('display_name', '')}
                  for m in (data.get('data') or []) if m.get('id')]
        current = (os.environ.get('ANTHROPIC_RECAP_MODEL') or 'claude-3-5-haiku-latest').strip()
        return jsonify({'success': True, 'key_present': True, 'current_model': current, 'models': models})
    except _urlerr.HTTPError as e:
        try:
            body = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            body = ''
        msg = 'API rejected the key (401 — wrong/revoked/no credit).' if e.code == 401 else f'Anthropic API HTTP {e.code}: {body[:160]}'
        return jsonify({'success': False, 'key_present': True, 'http': e.code, 'error': msg}), 200
    except (_urlerr.URLError, TimeoutError) as e:
        return jsonify({'success': False, 'key_present': True, 'error': f'Could not reach the API: {getattr(e, "reason", e)}'}), 200


def _anthropic_complete(prompt, max_tokens=800):
    """POST a single user message to the Anthropic API; return (text, reason).
    Mirrors the recap generator's call + 404 self-heal so flavor AI features
    share one code path. Uses urllib — no new dependency."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None, 'no_key'
    import urllib.request as _urlreq
    import urllib.error as _urlerr

    def _call(model):
        payload = json.dumps({
            'model': model,
            'max_tokens': max_tokens,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        req = _urlreq.Request(
            'https://api.anthropic.com/v1/messages', data=payload,
            headers={'content-type': 'application/json', 'x-api-key': api_key,
                     'anthropic-version': '2023-06-01'}, method='POST')
        try:
            with _urlreq.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            blocks = data.get('content') or []
            t = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
            return (t, None) if t else (None, 'empty_response')
        except _urlerr.HTTPError as e:
            try:
                body = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                body = ''
            return None, f'api_http_{e.code}: {body[:200]}'
        except (_urlerr.URLError, TimeoutError) as e:
            return None, f'network: {getattr(e, "reason", e)}'
        except (ValueError, KeyError) as e:
            return None, f'parse_error: {e}'

    SAFE_MODEL = 'claude-3-5-haiku-latest'
    model = (os.environ.get('ANTHROPIC_RECAP_MODEL') or SAFE_MODEL).strip()
    text, reason = _call(model)
    if text is None and model != SAFE_MODEL and reason and reason.startswith('api_http_404'):
        text, reason2 = _call(SAFE_MODEL)
        if text:
            return text, None
        reason = f'{reason} (fallback model also failed: {reason2})'
    return text, reason


# ══════════════════════════════════════════════════════════════════════════
# SESSION COMPLETE — scrapbook (Chunk 6)
# Auto-mined highlights + GM-authored RP moments + a Claude narrative, pushed
# to every screen at session end and saved on the volume to revisit.
# ══════════════════════════════════════════════════════════════════════════
# SCRAPBOOK_FILE / SCRAPBOOK_DIR are bound to the active campaign in _bind_campaign_paths().


def _persist_session_highlights():
    try:
        with SESSION_HIGHLIGHTS_LOCK:
            snap = copy.deepcopy(SESSION_HIGHLIGHTS)
        with open(SCRAPBOOK_FILE, 'w', encoding='utf-8') as f:
            json.dump(snap, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[SCRAPBOOK] persist failed: {e}")


def _load_session_highlights():
    """Restore the in-flight accumulator on startup so a mid-session reload
    doesn't lose the wrap-up. A fresh session resets it via /api/session/begin."""
    try:
        if not os.path.exists(SCRAPBOOK_FILE):
            return
        data, err = safe_load_json_file(SCRAPBOOK_FILE)
        if data and isinstance(data, dict):
            with SESSION_HIGHLIGHTS_LOCK:
                for k in SESSION_HIGHLIGHTS:
                    if k in data:
                        SESSION_HIGHLIGHTS[k] = data[k]
    except Exception as e:
        print(f"[SCRAPBOOK] load failed: {e}")


def _reset_session_highlights(session_number):
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['session_number'] = int(session_number or 1)
        SESSION_HIGHLIGHTS['started_at'] = time.strftime('%Y-%m-%d %H:%M')
        SESSION_HIGHLIGHTS['crits'] = []
        SESSION_HIGHLIGHTS['fumbles'] = []
        SESSION_HIGHLIGHTS['big_hits'] = []
        SESSION_HIGHLIGHTS['loot'] = []
        SESSION_HIGHLIGHTS['rp_moments'] = []
        SESSION_HIGHLIGHTS['narrative'] = ''
        SESSION_HIGHLIGHTS['mvp_votes'] = {}
        SESSION_HIGHLIGHTS['mvp_winner'] = ''
    _persist_session_highlights()


def _party_level():
    """Best guess at the party level for the campaign log: the max PC level."""
    try:
        levels = [int(getattr(p, 'level', 1) or 1) for p in PARTY_LIBRARY.values()]
        return max(levels) if levels else 1
    except Exception:
        return 1


def _active_party_names():
    """The player-character roster for the active campaign's system -- PF2e's
    PARTY_LIBRARY or the Cosmere PC store. The session scrapbook's highlights,
    MVP poll, and per-PC cards follow whichever system is live, so the same
    capture hooks + assembly work for both."""
    if _active_system() == 'cosmere':
        names = []
        for d in _list_cosmere_pcs():
            nm = d.get('name') or (d.get('build') or {}).get('name')
            if nm:
                names.append(nm)
        return names
    return list(PARTY_LIBRARY.keys())


def _scrapbook_party_level(system=None, roster=None):
    """Average party level for the scrapbook header, per active system."""
    system = system or _active_system()
    if system == 'cosmere':
        levels = []
        for d in _list_cosmere_pcs():
            try:
                levels.append(int((d.get('build') or {}).get('level', 1)))
            except (TypeError, ValueError):
                pass
        return round(sum(levels) / len(levels)) if levels else 1
    return _party_level()


def _record_crit_fumble(name, action, detail, degree):
    """Hook from /api/log_roll (PF2e) + /api/cosmere/roll (Cosmere nat-20/nat-1).
    Records a crit / nat-1 for a PARTY PC only — NPC and GM rolls are ignored.
    Trusts the degree when present; otherwise sniffs the marker text the sheets
    stamp on the roll detail."""
    if not name or name not in _active_party_names():
        return
    blob = (str(detail or '') + ' ' + str(degree or '')).upper()
    is_crit = (degree == 'crit_success') or ('[CRIT 20]' in blob) or ('NAT 20' in blob) or ('CRIT SUCCESS' in blob)
    is_fumble = (degree == 'crit_failure') or ('[NAT 1]' in blob) or ('CRIT FAIL' in blob) or bool(re.search(r'\bNAT 1\b', blob))
    if not (is_crit or is_fumble):
        return
    rec = {'pc': name, 'action': str(action or '')[:60], 'detail': str(detail or '')[:160], 'round': ROUND_NUMBER}
    with SESSION_HIGHLIGHTS_LOCK:
        bucket = SESSION_HIGHLIGHTS['crits'] if is_crit else SESSION_HIGHLIGHTS['fumbles']
        bucket.append(rec)
        SESSION_HIGHLIGHTS['crits'] = SESSION_HIGHLIGHTS['crits'][-120:]
        SESSION_HIGHLIGHTS['fumbles'] = SESSION_HIGHLIGHTS['fumbles'][-120:]
    _persist_session_highlights()


def _record_loot(target, items, coins):
    """Hook from /api/send_loot. Records loot per recipient PC."""
    if not target:
        return
    norm_items = []
    for it in (items or []):
        if isinstance(it, dict) and (it.get('name') or '').strip():
            norm_items.append({'name': str(it['name']).strip()[:80], 'qty': int(it.get('qty', 1) or 1)})
    coins = coins or {}
    norm_coins = {k: int(coins.get(k, 0) or 0) for k in ('pp', 'gp', 'sp', 'cp')}
    if not norm_items and not any(norm_coins.values()):
        return
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['loot'].append({'pc': target, 'items': norm_items, 'coins': norm_coins})
        SESSION_HIGHLIGHTS['loot'] = SESSION_HIGHLIGHTS['loot'][-120:]
    _persist_session_highlights()


def _record_big_hit(target, amount, is_pc):
    """Hook from /api/adjust_hp. Records the biggest blows STRUCK (damage dealt
    to non-PCs). This app applies monster damage GM-side without attacker
    attribution, so big hits are a party-level highlight, not per-PC."""
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return
    if amount <= 0 or is_pc:
        return
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['big_hits'].append({'target': str(target or '?')[:60], 'amount': amount, 'round': ROUND_NUMBER})
        # Keep only the heaviest dozen so the file stays small.
        SESSION_HIGHLIGHTS['big_hits'] = sorted(SESSION_HIGHLIGHTS['big_hits'], key=lambda h: h['amount'], reverse=True)[:12]
    _persist_session_highlights()


def _build_session_log_text():
    """Flatten the recent combat log into plain lines for the narrator."""
    with ENCOUNTER_LOCK:
        logs = list(COMBAT_LOGS)
    lines = []
    for e in logs:
        if not isinstance(e, dict):
            continue
        rnd = e.get('round', '?')
        if 'msg' in e:
            lines.append(f"[R{rnd}] {e.get('msg', '')}")
        elif 'name' in e:
            lines.append(f"[R{rnd}] {e.get('name', '')}: {e.get('action', '')} -> {e.get('result', '')} ({e.get('detail', '')})")
    return '\n'.join(lines)[-9000:]


def _generate_scrapbook_narrative():
    """Write a short dramatic recap of THIS session from the combat log via the
    Anthropic API. Returns (text, reason)."""
    log_text = _build_session_log_text()
    if not log_text.strip():
        return None, 'empty_log'
    cfg = _load_campaign_config()
    prompt = (
        f"You are the narrator wrapping up tonight's session of the tabletop campaign "
        f"\"{cfg.get('name', 'the campaign')}\". Below is the session's combat / event log.\n\n"
        f"Write a short, vivid recap of what happened this session — 2 short paragraphs of "
        f"dramatic narrator prose, like the closing montage of a TV episode. Tell it as a STORY: "
        f"the battles fought, the turning points, the triumphs and the narrow escapes, and the "
        f"mood the party leaves on. Use the characters' names where they appear.\n\n"
        f"IMPORTANT — the log is mechanical, so IGNORE and never mention dice rolls, degrees of "
        f"success, DCs, initiative, HP/damage numbers, instance ids, or stat-block labels. "
        f"Translate it into the fiction. No headings, no bullet points, no preamble or sign-off — "
        f"just the recap prose.\n\n"
        f"--- SESSION LOG ---\n{log_text}\n--- END LOG ---\n\nWrite only the recap."
    )
    return _anthropic_complete(prompt, max_tokens=700)


def _assemble_scrapbook():
    """Build the full scrapbook payload: party-wide totals + per-PC cards.
    System-aware: the roster is the active system's party and loot totals are
    spheres (Cosmere) or coins (PF2e)."""
    with SESSION_HIGHLIGHTS_LOCK:
        h = copy.deepcopy(SESSION_HIGHLIGHTS)
    cfg = _load_campaign_config()
    system = _active_system()
    roster = _active_party_names()
    biggest = max(h['big_hits'], key=lambda x: x['amount']) if h['big_hits'] else None
    total_coins = {'pp': 0, 'gp': 0, 'sp': 0, 'cp': 0}
    total_spheres = {'chip': 0, 'mark': 0, 'broam': 0}
    for l in h['loot']:
        for k in total_coins:
            total_coins[k] += int((l.get('coins') or {}).get(k, 0) or 0)
        for k in total_spheres:
            total_spheres[k] += int((l.get('spheres') or {}).get(k, 0) or 0)
    party = {
        'crit_count': len(h['crits']),
        'fumble_count': len(h['fumbles']),
        'biggest_hit': biggest,
        'total_coins': total_coins,
        'total_spheres': total_spheres,
        'loot_count': sum(len(l.get('items', [])) for l in h['loot']),
        'rp_moments': [m['text'] for m in h['rp_moments'] if m.get('scope') == 'party'],
    }
    players = {}
    for pc in roster:
        players[pc] = {
            'crits':   [c for c in h['crits'] if c['pc'] == pc],
            'fumbles': [f for f in h['fumbles'] if f['pc'] == pc],
            'loot':    [l for l in h['loot'] if l['pc'] == pc],
            'rp_moments': [m['text'] for m in h['rp_moments'] if m.get('scope') == pc],
        }
    # MVP poll: anonymous counts only (party member -> votes), plus the roster
    # of who can win, so the overlay can render the vote buttons + tally.
    mvp_counts = {}
    for choice in h.get('mvp_votes', {}).values():
        mvp_counts[choice] = mvp_counts.get(choice, 0) + 1
    return {
        'session_number': h.get('session_number', cfg.get('session_number', 1)),
        'campaign_name': cfg.get('name', 'The Campaign'),
        'crest_image': cfg.get('crest_image', ''),
        'narrative': h.get('narrative', ''),
        'started_at': h.get('started_at', ''),
        'party_level': _scrapbook_party_level(system, roster),
        'mvp_winner': h.get('mvp_winner', ''),
        'system': system,
        'party': party,
        'players': players,
        'mvp': {'tally': mvp_counts, 'total': sum(mvp_counts.values()),
                'candidates': roster},
    }


@app.route('/api/session/scrapbook/draft')
@gm_required
def api_scrapbook_draft():
    """The GM review draft: assembled scrapbook + the raw RP moments (with
    scope) so the editor can list / remove them."""
    with SESSION_HIGHLIGHTS_LOCK:
        rps = list(SESSION_HIGHLIGHTS['rp_moments'])
    return jsonify({'success': True, 'scrapbook': _assemble_scrapbook(),
                    'rp_moments': rps, 'party_members': _active_party_names()})


@app.route('/api/session/scrapbook/vote', methods=['POST'])
def api_scrapbook_vote():
    """Cast / change an MVP vote. Players vote as their own character (pinned
    to the session); the GM may pass an explicit voter. Broadcasts only the
    anonymous tally so the open scrapbook updates live without revealing who
    voted for whom. One vote per voter (re-voting overwrites)."""
    data = request.get_json(silent=True) or {}
    choice = str(data.get('choice', '') or '').strip()
    if choice not in _active_party_names():
        return jsonify({'success': False, 'error': 'unknown choice'}), 400
    if _is_gm():
        voter = (str(data.get('voter') or '').strip() or 'GM')
    else:
        voter = session.get('player_name') or ''
        if not voter:
            return jsonify({'success': False, 'error': 'join as a character to vote'}), 403
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['mvp_votes'][voter] = choice
    _persist_session_highlights()
    tally = _mvp_tally()
    sse_broadcast('mvp_vote', {'tally': tally, 'total': sum(tally.values())})
    return jsonify({'success': True, 'your_vote': choice, 'tally': tally})


@app.route('/api/session/scrapbook/grant_hero/<pc_name>', methods=['POST'])
@gm_required
def api_scrapbook_grant_hero(pc_name):
    """GM names the session MVP. PF2e also grants a Hero Point (caps at 3, via
    apply_pc_delta which persists + broadcasts pc_update so the winner's sheet
    lights up); Cosmere has no Hero Points, so it just crowns the winner in the
    session record."""
    if pc_name not in _active_party_names():
        return jsonify({'success': False, 'error': 'unknown PC'}), 404
    hero_points = None
    if _active_system() != 'cosmere':
        def _mut(pc):
            before = int(getattr(pc, 'hero_points', 0) or 0)
            if before < 3:
                pc.hero_points = before + 1
            return True
        try:
            _, pc = apply_pc_delta(pc_name, _mut)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
        hero_points = pc.hero_points
        _combat_log(f"{pc_name} named session MVP — +1 Hero Point (now {hero_points})", 'system')
    else:
        _combat_log(f"{pc_name} named session MVP", 'system')
    # Crown the MVP in the session record so the campaign timeline shows it.
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['mvp_winner'] = pc_name
    _persist_session_highlights()
    try:
        _save_scrapbook_record(_assemble_scrapbook())
    except Exception:
        pass
    return jsonify({'success': True, 'pc': pc_name, 'hero_points': hero_points})


@app.route('/api/session/scrapbook/rp_moment', methods=['POST'])
@gm_required
def api_scrapbook_add_rp():
    """GM adds a role-play moment before pushing the scrapbook. scope is
    'party' (shows in the shared section) or a PC name (their card)."""
    data = request.get_json(silent=True) or {}
    text = str(data.get('text', '') or '').strip()[:400]
    scope = str(data.get('scope', 'party') or 'party').strip()
    if not text:
        return jsonify({'success': False, 'error': 'empty moment'}), 400
    if scope != 'party' and scope not in _active_party_names():
        scope = 'party'
    with SESSION_HIGHLIGHTS_LOCK:
        SESSION_HIGHLIGHTS['rp_moments'].append({'text': text, 'scope': scope})
        rps = list(SESSION_HIGHLIGHTS['rp_moments'])
    _persist_session_highlights()
    return jsonify({'success': True, 'rp_moments': rps})


@app.route('/api/session/scrapbook/rp_moment/remove', methods=['POST'])
@gm_required
def api_scrapbook_remove_rp():
    data = request.get_json(silent=True) or {}
    with SESSION_HIGHLIGHTS_LOCK:
        try:
            i = int(data.get('index'))
            if 0 <= i < len(SESSION_HIGHLIGHTS['rp_moments']):
                SESSION_HIGHLIGHTS['rp_moments'].pop(i)
        except (TypeError, ValueError):
            pass
        rps = list(SESSION_HIGHLIGHTS['rp_moments'])
    _persist_session_highlights()
    return jsonify({'success': True, 'rp_moments': rps})


@app.route('/api/session/scrapbook/narrative', methods=['POST'])
@gm_required
def api_scrapbook_narrative():
    """Set the scrapbook narrative. {text} sets it manually; otherwise it's
    generated from the session log via Claude."""
    data = request.get_json(silent=True) or {}
    manual = data.get('text')
    if manual is not None:
        text = str(manual or '').strip()[:4000]
        with SESSION_HIGHLIGHTS_LOCK:
            SESSION_HIGHLIGHTS['narrative'] = text
        _persist_session_highlights()
        return jsonify({'success': True, 'narrative': text, 'source': 'manual'})
    text, reason = _generate_scrapbook_narrative()
    if text:
        with SESSION_HIGHLIGHTS_LOCK:
            SESSION_HIGHLIGHTS['narrative'] = text
        _persist_session_highlights()
        return jsonify({'success': True, 'narrative': text, 'source': 'ai'})
    notes = {
        'no_key': 'No ANTHROPIC_API_KEY set — type the recap yourself, or set the key on Railway.',
        'empty_log': 'The combat log is empty, so there is nothing to summarize yet.',
        'empty_response': 'The model returned nothing — try again.',
    }
    note = notes.get(reason, f'Could not generate the narrative ({reason}).')
    return jsonify({'success': False, 'reason': reason, 'note': note,
                    'key_present': bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())}), 200


def _save_scrapbook_record(sb):
    """Write a session scrapbook to the volume (campaign log). Returns the
    session number used as the filename stem."""
    n = sb.get('session_number', 1)
    try:
        os.makedirs(SCRAPBOOK_DIR, exist_ok=True)
        with open(os.path.join(SCRAPBOOK_DIR, f'session_{n}.json'), 'w', encoding='utf-8') as f:
            json.dump(sb, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[SCRAPBOOK] save failed: {e}")
    return n


@app.route('/api/session/scrapbook/push', methods=['POST'])
@gm_required
def api_scrapbook_push():
    """Finalize: save the scrapbook to the volume, optionally feed the next
    'Previously on...', and broadcast it to every screen."""
    sb = _assemble_scrapbook()
    _save_scrapbook_record(sb)
    data = request.get_json(silent=True) or {}
    if data.get('feed_recap') and sb.get('narrative'):
        _save_campaign_config({'last_recap': sb['narrative']})
    sse_broadcast('session_scrapbook', sb)
    return jsonify({'success': True, 'scrapbook': sb})


@app.route('/api/session/scrapbook/list')
@gm_required
def api_scrapbook_list():
    out = []
    try:
        if os.path.isdir(SCRAPBOOK_DIR):
            for fn in sorted(os.listdir(SCRAPBOOK_DIR)):
                if fn.endswith('.json'):
                    out.append(fn[:-5])
    except Exception:
        pass
    return jsonify({'success': True, 'scrapbooks': out})


@app.route('/api/session/scrapbook/saved/<name>')
@gm_required
def api_scrapbook_saved(name):
    name = re.sub(r'[^A-Za-z0-9_-]', '', name or '')[:60]
    p = os.path.join(SCRAPBOOK_DIR, name + '.json')
    if not name or not os.path.exists(p):
        return jsonify({'success': False, 'error': 'not found'}), 404
    data, err = safe_load_json_file(p)
    if not data:
        return jsonify({'success': False, 'error': err or 'unreadable'}), 404
    return jsonify({'success': True, 'scrapbook': data})


@app.route('/api/session/timeline')
@gm_required
def api_session_timeline():
    """Campaign continuity: a rich, newest-first list of saved sessions for
    the timeline view — number, date, party level, MVP, and a narrative
    teaser. The full scrapbook for any entry loads via /scrapbook/saved."""
    out = []
    try:
        if os.path.isdir(SCRAPBOOK_DIR):
            for fn in os.listdir(SCRAPBOOK_DIR):
                if not fn.endswith('.json'):
                    continue
                data, _err = safe_load_json_file(os.path.join(SCRAPBOOK_DIR, fn))
                if not isinstance(data, dict):
                    continue
                narr = (data.get('narrative') or '').strip().replace('\n', ' ')
                teaser = (narr[:220] + '…') if len(narr) > 220 else narr
                party = data.get('party') or {}
                out.append({
                    'name': fn[:-5],
                    'session_number': data.get('session_number', 0),
                    'date': data.get('started_at', ''),
                    'party_level': data.get('party_level'),
                    'mvp_winner': data.get('mvp_winner', ''),
                    'teaser': teaser,
                    'crit_count': party.get('crit_count', 0),
                    'fumble_count': party.get('fumble_count', 0),
                })
    except Exception as e:
        print(f"[TIMELINE] {e}")
    out.sort(key=lambda s: s.get('session_number', 0), reverse=True)
    return jsonify({'success': True, 'sessions': out})


# Restore any in-flight highlights on import (crash/reload recovery).
_load_session_highlights()


@app.route('/api/session/notes')
@gm_required
def api_session_notes():
    """List the sessions-folder notes (newest-first) for the recap picker."""
    return jsonify({'success': True, 'notes': _session_notes(), 'folder': _load_campaign_config().get('sessions_folder', 'Sessions')})


@app.route('/api/session/recap/generate', methods=['POST'])
@gm_required
def api_session_recap_generate():
    """Generate a 'Previously on...' blurb from a chosen session note.
    Tries the Claude API (if ANTHROPIC_API_KEY set); falls back to extracting
    a recap section from the note. Does NOT save — the GM reviews/edits first."""
    data = request.json or {}
    rel = (data.get('note') or '').strip()
    if not rel:
        return jsonify({'success': False, 'error': 'note path required'}), 400
    try:
        body = _read_session_note_raw(rel)
    except (FileNotFoundError, ValueError) as e:
        return jsonify({'success': False, 'error': f'could not read note: {e}'}), 404
    ai_text, reason = _generate_recap_via_claude(body)
    if ai_text:
        return jsonify({'success': True, 'recap': ai_text, 'source': 'ai'})
    extracted = _extract_recap_section(body)
    key_present = bool(os.environ.get('ANTHROPIC_API_KEY', '').strip())
    # Human-readable explanation of why we fell back, so the GM can fix it.
    if reason == 'no_key':
        note = 'No ANTHROPIC_API_KEY reached the server. On Railway, add it under Variables and redeploy; locally, put it in .env.'
    elif reason and reason.startswith('api_http_401'):
        note = 'API rejected the key (401). The key is wrong, revoked, or has no credit — check console.anthropic.com.'
    elif reason and reason.startswith('api_http_404'):
        note = 'API model not found (404). Set ANTHROPIC_RECAP_MODEL to a valid id (e.g. claude-3-5-haiku-latest).'
    elif reason and reason.startswith('api_http_'):
        note = 'Anthropic API error: ' + reason.replace('api_http_', 'HTTP ')
    elif reason and reason.startswith('network'):
        note = 'Could not reach the Anthropic API (network/egress). Showing a section from the note instead.'
    else:
        note = 'Pulled a section from the note (' + (reason or 'fallback') + '). Edit & save.'
    return jsonify({
        'success': True,
        'recap': extracted,
        'source': 'extract',
        'reason': reason,
        'key_present': key_present,
        'note': note,
    })


@app.route('/api/session/recap', methods=['POST'])
@gm_required
def api_session_recap_save():
    """Persist the (possibly GM-edited) recap text to the campaign config."""
    data = request.json or {}
    text = (data.get('recap') or '').strip()
    cfg = _save_campaign_config({'last_recap': text})
    return jsonify({'success': True, 'last_recap': cfg.get('last_recap', '')})


@app.route('/api/session/begin', methods=['POST'])
@gm_required
def api_session_begin():
    """Broadcast the session-start curtain to every connected screen.
    Optionally bumps the session number. Carries the campaign name, crest,
    and saved recap so clients can render the curtain without a refetch."""
    data = request.json or {}
    cfg = _load_campaign_config()
    if data.get('bump_session'):
        cfg = _save_campaign_config({'session_number': int(cfg.get('session_number', 1)) + 1})
    # Fresh session → wipe the highlights accumulator so the wrap-up scrapbook
    # only reflects tonight (Chunk 6).
    _reset_session_highlights(cfg.get('session_number', 1))
    # Campaign stats hook (Tier 4, feature 30)
    try:
        _bump_campaign_stat('sessions_started')
    except Exception:
        pass
    payload = {
        'campaign_name': cfg.get('name', 'The Campaign'),
        'crest_image': cfg.get('crest_image', ''),
        'recap': cfg.get('last_recap', ''),
        'session_number': cfg.get('session_number', 1),
    }
    sse_broadcast('session_start', payload)
    return jsonify({'success': True, **payload})


@app.route('/api/session/dismiss', methods=['POST'])
@gm_required
def api_session_dismiss():
    """Part the curtain on every screen at once (GM clicks Enter)."""
    sse_broadcast('session_dismiss', {'t': int(time.time())})
    return jsonify({'success': True})


@app.route('/api/session/mood', methods=['GET', 'POST'])
@gm_required
def api_session_mood():
    """Get or set the scene mood (Chunk 5). POST {mood} persists it and
    broadcasts scene_mood to every screen, which applies a subtle tint.
    Visuals only — no audio coupling (soundscapes are controlled separately)."""
    if request.method == 'GET':
        return jsonify({'mood': _load_campaign_config().get('scene_mood', 'calm')})
    data = request.get_json(silent=True) or {}
    mood = str(data.get('mood', '') or '').strip().lower()
    if mood not in _VALID_MOODS:
        return jsonify({'success': False, 'error': f'Unknown mood (use one of: {", ".join(_VALID_MOODS)})'}), 400
    _save_campaign_config({'scene_mood': mood})
    sse_broadcast('scene_mood', {'mood': mood, 't': int(time.time())})
    return jsonify({'success': True, 'mood': mood})


# ── Table safety tools (X-Card + Lines & Veils) ────────────────────────────
def _safety_record():
    s = _load_campaign_config().get('safety') or {}
    return {'lines': list(s.get('lines') or []),
            'veils': list(s.get('veils') or []),
            'notes': str(s.get('notes') or '')}


@app.route('/api/safety', methods=['GET', 'POST'])
def api_safety():
    """GET the table's lines/veils/content notes (everyone at the table should
    see them). POST saves them -- GM only. The X-Card itself is a separate,
    member-callable signal (/api/safety/xcard)."""
    if request.method == 'GET':
        rec = _safety_record()
        rec['can_edit'] = bool(_is_gm())
        return jsonify(rec)
    if not _is_gm():
        return jsonify({'error': 'GM only'}), 403
    data = request.get_json(silent=True) or {}
    def _clean_list(v):
        items = v if isinstance(v, list) else str(v or '').split('\n')
        return [str(x).strip() for x in items if str(x).strip()][:50]
    safety = {'lines': _clean_list(data.get('lines')),
              'veils': _clean_list(data.get('veils')),
              'notes': str(data.get('notes') or '').strip()[:2000]}
    _save_campaign_config({'safety': safety})
    return jsonify({'success': True, **safety})


@app.route('/api/safety/xcard', methods=['POST'])
def api_safety_xcard():
    """Anyone at the table taps the X-Card: broadcast an ANONYMOUS 'pause the
    game' signal to every screen. We deliberately record no identity -- the
    point of an X-Card is that it carries no attribution or explanation."""
    if _account_mode() and not _auth.current_user():
        return jsonify({'error': 'login required'}), 401
    sse_broadcast('safety_xcard', {'t': int(time.time())})
    return jsonify({'success': True})


@app.route('/api/session/roll_initiative', methods=['POST'])
@gm_required
def api_session_roll_initiative():
    """Broadcast a cinematic ROLL FOR INITIATIVE flourish to every screen.

    Pure flavor — the mechanical NPC initiative roll lives at
    /api/roll_npc_initiative. This just fires the SSE that every page's
    existing /api/events socket listens for (no new connection), and the
    GM device answers with the combat drum via window.pf2eAudio.drum()."""
    sse_broadcast('roll_initiative', {'t': int(time.time())})
    return jsonify({'success': True})

@app.route('/')
def index():
    """The public front door.

    Account mode (production): a logged-out visitor gets the TTRPG splash
    (Enter World -> login); a logged-in user goes to /me, where they pick from
    the campaigns they run or have a character in. Legacy mode (no accounts yet)
    keeps the old single-campaign intro lobby.
    """
    if _account_mode():
        if _auth.current_user():
            return redirect('/me')
        return render_template('splash.html')
    # ── Legacy single-campaign mode (pre-accounts) ──
    if _active_system() != systems.DEFAULT_SYSTEM:
        return redirect(_system_home(_is_gm()))
    _sync_party_from_disk()
    is_gm = _is_gm()
    state = {}  # vault removed; the campaign-intro state row is config-driven now
    return render_template(
        'campaign_intro.html',
        campaign=_load_campaign_config(),
        party=list(PARTY_LIBRARY.values()),
        current_player=session.get('player_name'),
        is_gm=is_gm,
        vault_state=state,
    )

@app.route('/api/campaign', methods=['GET', 'POST'])
def api_campaign():
    """GET is public (intro screen reads it); POST is GM-only (edit form)."""
    if request.method == 'GET':
        return jsonify(_load_campaign_config())
    if not _is_gm():
        return jsonify({'success': False, 'error': 'GM authentication required'}), 403
    data = request.json or {}
    cfg = _save_campaign_config(data)
    return jsonify({'success': True, 'campaign': cfg})

@app.route('/api/join_campaign', methods=['POST'])
def api_join_campaign():
    """Player picks a PC tile on the intro screen → session.player_name is set
    so the rest of the app (map auth, combat-log filter, my-char bar) knows
    who they are. Validates against PARTY_LIBRARY so a stale tile from a
    cached page can't poison the session with a bogus name."""
    data = request.json or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'No character selected'}), 400
    if name not in PARTY_LIBRARY:
        return jsonify({'success': False, 'error': 'Unknown character'}), 404
    session['player_name'] = name
    return jsonify({'success': True, 'name': name})

@app.route('/api/leave_campaign', methods=['POST'])
def api_leave_campaign():
    """Clear the joined-as state so the next visit shows the picker again."""
    session.pop('player_name', None)
    return jsonify({'success': True})

@app.route('/party')
def party_view():
    _sync_party_from_disk()
    return render_template('party_view.html', party=list(PARTY_LIBRARY.values()))

@app.route('/gm/login', methods=['GET', 'POST'])
def gm_login():
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == GM_PASSWORD:
            session['gm_authenticated'] = True
            # Reject open-redirects: `next` must be a relative same-origin
            # path (starts with `/` and not `//`), otherwise an attacker
            # could craft /gm/login?next=https://evil.com/phish and land
            # the freshly-authed GM on a phishing page.
            nxt = request.args.get('next', '/gm') or '/gm'
            if not nxt.startswith('/') or nxt.startswith('//'):
                nxt = '/gm'
            return redirect(nxt)
        return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
            <title>GM Login</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Cinzel:wght@600&display=swap" rel="stylesheet">
            <style>body{font-family:'Inter',system-ui,sans-serif;background:#0d0d12;color:#e8e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
            .box{background:#24242e;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:40px;max-width:340px;width:100%;text-align:center;}
            h1{font-family:'Cinzel',serif;color:#ef4444;font-size:16px;margin-bottom:8px;}
            p{color:#8080a0;font-size:13px;}
            input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);background:#0d0d12;color:#e8e8f0;font-size:14px;margin:16px 0;box-sizing:border-box;font-family:'Inter',sans-serif;}
            input:focus{outline:none;border-color:rgba(94,173,173,0.3);}
            button{width:100%;padding:10px;border-radius:6px;border:none;background:#3A7878;color:#A8DEDE;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.2s;}
            button:hover{background:#4A9696;}
            </style></head>
            <body><div class="box"><h1>Wrong Password</h1><p>Try again.</p>
            <form method="POST"><input type="password" name="password" placeholder="GM Password" autofocus>
            <button type="submit">Sign In</button></form></div></body></html>'''
    return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
        <title>GM Login</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Cinzel:wght@600&display=swap" rel="stylesheet">
        <style>body{font-family:'Inter',system-ui,sans-serif;background:#0d0d12;color:#e8e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
        .box{background:#24242e;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:40px;max-width:340px;width:100%;text-align:center;}
        h1{font-family:'Cinzel',serif;color:#7DC4C4;font-size:18px;margin-bottom:4px;}
        p{color:#8080a0;font-size:13px;margin-bottom:20px;}
        input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);background:#0d0d12;color:#e8e8f0;font-size:14px;margin-bottom:16px;box-sizing:border-box;font-family:'Inter',sans-serif;}
        input:focus{outline:none;border-color:rgba(94,173,173,0.3);}
        button{width:100%;padding:10px;border-radius:6px;border:none;background:#3A7878;color:#A8DEDE;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.2s;}
        button:hover{background:#4A9696;}
        </style></head>
        <body><div class="box"><h1>GM Access</h1><p>This area is restricted to the Game Master.</p>
        <form method="POST"><input type="password" name="password" placeholder="GM Password" autofocus>
        <button type="submit">Sign In</button></form></div></body></html>'''

@app.route('/gm/logout')
def gm_logout():
    session.pop('gm_authenticated', None)
    return redirect('/player')


# ── Account auth + campaign selection (multi-campaign) ──────────────────────
SETUP_TOKEN = os.environ.get('SETUP_TOKEN', '')


def _safe_next(default):
    nxt = request.args.get('next', default) or default
    return default if (not nxt.startswith('/') or nxt.startswith('//')) else nxt


def _auto_migrate_legacy(admin_user_id):
    """First-run convenience: if a legacy flat game exists and nothing is migrated
    yet, copy it into Campaign #1 (owned by the new admin) and go live. Safe -- the
    migration copies and preserves the originals as backup."""
    try:
        if _campaigns.list_campaigns():
            return
        legacy_party = os.path.join(DATA_DIR, 'party_data')
        if not (os.path.isdir(legacy_party) and any(f.endswith('.json') for f in os.listdir(legacy_party))):
            return
        from tools.migrate_to_campaigns import migrate as _run_migration
        new_cid = _run_migration(created_by=admin_user_id)
        if new_cid:
            _set_active_campaign(new_cid)
            load_campaign(new_cid)
    except Exception as e:
        app.logger.warning(f"setup auto-migration skipped: {e}")


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    """One-time bootstrap of the first (admin/GM) account. Self-disables once any
    account exists; gated by the SETUP_TOKEN env var when one is set."""
    if _auth.any_users_exist():
        return redirect('/login')
    if request.method == 'POST':
        if SETUP_TOKEN and request.form.get('setup_token', '') != SETUP_TOKEN:
            return render_template('setup.html', error='Wrong setup token.', need_token=True), 403
        try:
            u = _auth.create_user(request.form.get('username', ''), request.form.get('password', ''),
                                  display_name=request.form.get('display_name'), is_admin=True)
        except ValueError as e:
            return render_template('setup.html', error=str(e), need_token=bool(SETUP_TOKEN)), 400
        _auth.login_user(u, remember=True)
        _auto_migrate_legacy(u['id'])
        return redirect('/me')
    return render_template('setup.html', error=None, need_token=bool(SETUP_TOKEN))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if not _auth.any_users_exist():
        return redirect('/setup')
    if request.method == 'POST':
        u = _auth.verify_credentials(request.form.get('username', ''), request.form.get('password', ''))
        if not u:
            return render_template('login.html', error='Wrong username or password.'), 401
        _auth.login_user(u, remember=True)
        return redirect(_safe_next('/me'))
    return render_template('login.html', error=None)


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Open self-registration: anyone can create an account, then create + run
    their own campaigns. The very first account is still the admin via /setup."""
    if not _auth.any_users_exist():
        return redirect('/setup')          # first account must be the admin
    if _auth.current_user():
        return redirect('/me')             # already signed in
    if request.method == 'POST':
        try:
            u = _auth.create_user(
                request.form.get('username', ''), request.form.get('password', ''),
                display_name=request.form.get('display_name'))
        except ValueError as e:
            return render_template('register.html', error=str(e)), 400
        _auth.login_user(u, remember=True)
        return redirect('/me')
    return render_template('register.html', error=None)


@app.route('/logout')
def logout():
    _auth.logout_user()
    return redirect('/login')


@app.route('/me')
@_auth.login_required
def account_home():
    """My Campaigns + My Characters for the logged-in user."""
    return _me_render()


@app.route('/campaign/<cid>/activate', methods=['POST'])
@_auth.login_required
def activate_campaign(cid):
    """Set the session's active campaign; a GM/admin also takes the live slot."""
    u = _auth.current_user()
    camp = _campaigns.get_campaign(cid)
    is_member = bool(_campaigns.user_role(camp, u['id'])) or u.get('is_admin')
    if not camp or not is_member:
        return jsonify({'error': 'not a member of that campaign'}), 403
    _set_active_campaign(cid)
    gm = _campaigns.is_gm(camp, u['id']) or u.get('is_admin')
    if gm:
        _storage.set_live_campaign_id(cid)
        load_campaign(cid)
    # A validated same-site `then` target (e.g. deep-link straight to the
    # player's own sheet from My Characters) wins over the default hub landing.
    then = _safe_then(request.form.get('then') or request.args.get('then'))
    if then:
        return redirect(then)
    # System-aware landing, registry-driven: every system declares a GM home and
    # a player home, so this works for any system with no per-system branching.
    ui = systems.get(camp.get('system') or systems.DEFAULT_SYSTEM).ui
    return redirect(ui.gm_home if gm else ui.player_home)


@app.route('/campaign/stop', methods=['POST'])
@_auth.login_required
def stop_active_campaign():
    """Park the session with NO active campaign so the GM can switch systems
    cleanly -- the lobby renders system-neutral and nothing from the last table
    bleeds into the next pick. If this user's table currently holds the
    server-wide live slot, release it too (so stale global state for one system
    can't leak into a different-system campaign); re-binding happens on the next
    activate. Does NOT yank the live slot out from under another GM's session."""
    u = _auth.current_user()
    prev = session.get('active_campaign_id') or (u.get('last_campaign_id') if u else None)
    session['campaign_stopped'] = True
    session.pop('active_campaign_id', None)
    try:
        if prev and _storage.get_live_campaign_id() == prev:
            camp = _campaigns.get_campaign(prev)
            if camp and (_campaigns.is_gm(camp, u['id']) or u.get('is_admin')):
                _storage.set_live_campaign_id(None)
                load_campaign(None)
    except Exception:
        pass
    if _is_ajax():
        return jsonify({'ok': True})
    return redirect('/me')


@app.route('/campaign/<cid>/invites')
@_auth.login_required
def campaign_invites(cid):
    """GM/admin: per-character join links for players to claim their PCs."""
    u = _auth.current_user()
    camp = _campaigns.get_campaign(cid)
    if not camp or not (_campaigns.is_gm(camp, u['id']) or u.get('is_admin')):
        return jsonify({'error': 'GM only'}), 403
    rows = []
    pdir = _storage.party_dir(cid)
    for fn in (sorted(os.listdir(pdir)) if os.path.isdir(pdir) else []):
        if not fn.endswith('.json'):
            continue
        doc = _storage.load_json(os.path.join(pdir, fn))
        if not _storage.is_wrapped(doc):
            continue
        claimed = bool(doc.get('owner_user_id'))
        code = None
        if not claimed:
            inv = _auth.active_invite_for_character(cid, doc.get('id'))
            code = inv['code'] if inv else _auth.create_invite(cid, 'player', character_id=doc.get('id'), created_by=u['id'])
        rows.append({'name': _campaigns._character_name(doc), 'claimed': claimed, 'code': code, 'kind': 'pf2e'})
    # Cosmere PCs (campaign-scoped store) get join links the same way, and a
    # GM-built PC can be HANDED OFF (released) so a player can claim it.
    cdir = _storage.cosmere_pc_dir(cid)
    for fn in (sorted(os.listdir(cdir)) if os.path.isdir(cdir) else []):
        if not fn.endswith('.json'):
            continue
        doc = _storage.load_json(os.path.join(cdir, fn))
        if not isinstance(doc, dict) or not doc.get('id'):
            continue
        owner_id = doc.get('owner_user_id')
        claimed = bool(owner_id)
        owner_name = ''
        if claimed:
            ow = _auth.get_user(owner_id)
            owner_name = (ow.get('display_name') or ow.get('username')) if ow else 'a player'
        code = None
        if not claimed:
            inv = _auth.active_invite_for_character(cid, doc.get('id'))
            code = inv['code'] if inv else _auth.create_invite(cid, 'player', character_id=doc.get('id'), created_by=u['id'])
        rows.append({'name': doc.get('name') or (doc.get('build') or {}).get('name') or '?',
                     'claimed': claimed, 'code': code, 'kind': 'cosmere',
                     'id': doc['id'], 'owner': owner_name})
    # Member roster (name + role) for management.
    members = []
    for m in camp.get('members', []):
        mu = _auth.get_user(m.get('user_id'))
        members.append({
            'user_id': m.get('user_id'),
            'name': (mu.get('display_name') or mu.get('username')) if mu else '(unknown user)',
            'role': m.get('role'),
            'is_self': m.get('user_id') == u['id'],
        })
    # Open (character-less) invite codes the GM minted -- generic player / co-GM.
    open_invites = [{'code': inv['code'], 'role': inv.get('role', 'player')}
                    for inv in _auth.list_active_invites(cid) if not inv.get('character_id')]
    return render_template('campaign_invites.html', campaign=camp, rows=rows,
                           members=members, gm_total=_campaigns.gm_count(camp),
                           open_invites=open_invites)


def _require_campaign_gm(cid):
    """(user, campaign) when the caller is the campaign's GM or a site admin,
    else (user, None). Central gate for the campaign-management routes."""
    u = _auth.current_user()
    camp = _campaigns.get_campaign(cid)
    if not camp or not (_campaigns.is_gm(camp, u['id']) or u.get('is_admin')):
        return u, None
    return u, camp


@app.route('/campaign/<cid>/invite', methods=['POST'])
@_auth.login_required
def campaign_mint_invite(cid):
    """GM mints a generic join code -- a build-your-own player, or a co-GM."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    role = 'gm' if request.form.get('role') == 'gm' else 'player'
    _auth.create_invite(cid, role, created_by=u['id'])
    return redirect('/campaign/%s/invites' % cid)


@app.route('/campaign/<cid>/invite/<code>/revoke', methods=['POST'])
@_auth.login_required
def campaign_revoke_invite(cid, code):
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    inv = _auth.get_invite(code)
    if inv and inv.get('campaign_id') == cid:
        _auth.revoke_invite(code)
    return redirect('/campaign/%s/invites' % cid)


@app.route('/campaign/<cid>/members/<uid>/remove', methods=['POST'])
@_auth.login_required
def campaign_remove_member(cid, uid):
    """GM removes a player from the campaign (refuses to strand the last GM)."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    _campaigns.remove_member(cid, uid)
    return redirect('/campaign/%s/invites' % cid)


@app.route('/campaign/<cid>/members/<uid>/role', methods=['POST'])
@_auth.login_required
def campaign_set_role(cid, uid):
    """GM promotes a player to co-GM, or demotes a co-GM (never the last GM)."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    role = 'gm' if request.form.get('role') == 'gm' else 'player'
    _campaigns.set_member_role(cid, uid, role)
    return redirect('/campaign/%s/invites' % cid)


@app.route('/campaign/<cid>/system', methods=['POST'])
@_auth.login_required
def campaign_set_system(cid):
    """The campaign's own GM (or a site admin) repairs a mis-stamped game system --
    e.g. a Pathfinder game saved as Cosmere that routes the whole table to the
    Cosmere side. Mirrors /admin/campaigns/<cid>/system but scoped to the GM's own
    campaign so they never need site-admin access. Rebinds the live globals when
    this campaign holds the live slot, so the fix takes effect immediately."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    new_system = (request.form.get('system') or '').strip().lower()
    if new_system not in _storage.SUPPORTED_SYSTEMS:
        return redirect('/campaign/%s/invites?system_error=1' % cid)
    old = camp.get('system') or 'pf2e'
    if old != new_system:
        camp['system'] = new_system
        _campaigns.save_campaign(camp)
        if _storage.get_live_campaign_id() == cid:
            load_campaign(cid)   # rebind globals so live in-memory state matches the new system
    return redirect('/campaign/%s/invites?system_set=%s' % (cid, new_system))


@app.route('/campaign/<cid>/delete', methods=['POST'])
@_auth.login_required
def campaign_delete(cid):
    """Permanently delete a campaign and ALL its data. GM/owner or site admin,
    guarded by typing the campaign name back as confirmation."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    if (request.form.get('confirm_name') or '').strip() != (camp.get('name') or '').strip():
        return redirect('/campaign/%s/invites?delete_error=1' % cid)
    _campaigns.delete_campaign(cid)   # soft delete -> trash (restorable ~30 days)
    if session.get('active_campaign_id') == cid:
        session.pop('active_campaign_id', None)
    # The active/live campaign may have just been removed -> rebind the globals
    # (load_campaign(None) falls back to the legacy flat layout).
    load_campaign(_storage.get_live_campaign_id())
    return redirect('/me?trashed=1')


@app.route('/campaign/<cid>/restore', methods=['POST'])
@_auth.login_required
def campaign_restore(cid):
    """Restore a soft-deleted campaign from the trash. The GM (of the trashed
    campaign) or a site admin only."""
    u = _auth.current_user()
    doc = _campaigns.get_trashed_campaign(cid)
    if not doc or not (u.get('is_admin') or _campaigns.is_gm(doc, u['id'])):
        return jsonify({'error': 'not authorized'}), 403
    _campaigns.restore_campaign(cid)
    return redirect('/me?restored=1')


@app.route('/campaign/<cid>/purge', methods=['POST'])
@_auth.login_required
def campaign_purge(cid):
    """Permanently delete a trashed campaign (irreversible). GM/admin + name
    confirmation, same guard as the original delete."""
    u = _auth.current_user()
    doc = _campaigns.get_trashed_campaign(cid)
    if not doc or not (u.get('is_admin') or _campaigns.is_gm(doc, u['id'])):
        return jsonify({'error': 'not authorized'}), 403
    if (request.form.get('confirm_name') or '').strip() != (doc.get('name') or '').strip():
        return redirect('/me?purge_error=1')
    _campaigns.purge_campaign(cid)
    return redirect('/me?purged=1')


@app.route('/api/backup_now', methods=['POST'])
@_auth.login_required
def backup_now():
    """Trigger an immediate on-volume snapshot of every active campaign. Any GM
    (or a site admin) may run it; it's their data and the op only writes backups."""
    u = _auth.current_user()
    if not (u.get('is_admin') or _campaigns.campaigns_for_user(u['id'])):
        return jsonify({'ok': False, 'error': 'not authorized'}), 403
    n = _backups.run_backup()
    return jsonify({'ok': True, 'count': n, 'last_backup_at': _backups.last_backup_at()})


@app.route('/campaign/<cid>/backup/latest')
@_auth.login_required
def campaign_backup_latest(cid):
    """Download the most-recent automatic snapshot of a campaign (to pull a copy
    off-device). GM/owner or admin."""
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    path = _backups.latest_backup(cid)
    if not path or not os.path.isfile(path):
        return jsonify({'error': 'no snapshot yet'}), 404
    safe = re.sub(r'[^a-zA-Z0-9_-]+', '-', (camp.get('slug') or camp.get('name') or 'campaign')).strip('-') or 'campaign'
    return send_file(path, mimetype='application/zip', as_attachment=True,
                     download_name='%s-snapshot.zip' % safe)


@app.route('/campaign/<cid>/export')
@_auth.login_required
def campaign_export(cid):
    """Download a .zip backup of the ENTIRE campaign tree (party PCs, encounters,
    loot, threads, journals, handouts, campaign.json). GM/owner or site admin."""
    import io, zipfile
    u, camp = _require_campaign_gm(cid)
    if not camp:
        return jsonify({'error': 'GM only'}), 403
    cdir = _storage.campaign_dir(cid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(cdir):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, cdir))
    buf.seek(0)
    safe = re.sub(r'[^a-zA-Z0-9_-]+', '-', (camp.get('slug') or camp.get('name') or 'campaign')).strip('-') or 'campaign'
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name='%s-backup.zip' % safe)


@app.route('/campaign/import', methods=['POST'])
@_auth.login_required
def campaign_import():
    """Restore a campaign from a .zip export into a NEW campaign (non-destructive
    -- never overwrites an existing game), owned by the importer as GM."""
    import io, zipfile
    u = _auth.current_user()
    f = request.files.get('backup')
    if not f:
        return jsonify({'ok': False, 'error': 'no file uploaded'}), 400
    try:
        zf = zipfile.ZipFile(io.BytesIO(f.read()))
    except zipfile.BadZipFile:
        return jsonify({'ok': False, 'error': 'not a valid .zip'}), 400
    if 'campaign.json' not in zf.namelist():
        return jsonify({'ok': False, 'error': 'not a campaign backup (missing campaign.json)'}), 400
    new_cid = _storage.new_id()
    _storage.ensure_campaign_dirs(new_cid)
    dest = _storage.campaign_dir(new_cid)
    for n in zf.namelist():
        if n.endswith('/'):
            continue
        target = os.path.normpath(os.path.join(dest, n))
        # zip-slip guard: every extracted path must stay inside the campaign dir
        if target != dest and not target.startswith(dest + os.sep):
            return jsonify({'ok': False, 'error': 'unsafe path in archive'}), 400
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'wb') as out:
            out.write(zf.read(n))
    # Re-key the campaign doc to the new id + the importer as GM owner.
    doc = _storage.load_json(_storage.campaign_file(new_cid)) or {}
    doc['id'] = new_cid
    doc['created_by'] = u['id']
    doc['members'] = [_storage.campaign_member(u['id'], 'gm')]
    doc['name'] = (doc.get('name') or 'Campaign') + ' (restored)'
    _campaigns.save_campaign(doc)
    # Re-stamp character envelopes to the new campaign id.
    for pdir in (_storage.party_dir(new_cid), _storage.cosmere_pc_dir(new_cid)):
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith('.json'):
                continue
            p = os.path.join(pdir, fn)
            cd = _storage.load_json(p)
            if isinstance(cd, dict) and 'campaign_id' in cd:
                cd['campaign_id'] = new_cid
                _atomic_write_json(p, cd, indent=2)
    return jsonify({'ok': True, 'id': new_cid, 'name': doc['name']})


def _claim_by_id(cid, character_id, user_id):
    pdir = _storage.party_dir(cid)
    for fn in (os.listdir(pdir) if os.path.isdir(pdir) else []):
        if fn.endswith('.json'):
            doc = _storage.load_json(os.path.join(pdir, fn))
            if _storage.is_wrapped(doc) and doc.get('id') == character_id:
                _campaigns.claim_character(cid, fn, user_id)
                return True
    # Cosmere PCs live in the campaign's cosmere_pcs/ store (not flat-wrapped):
    # stamp owner_user_id directly so the player hub + 'My Characters' find it.
    cdir = _storage.cosmere_pc_dir(cid)
    for fn in (os.listdir(cdir) if os.path.isdir(cdir) else []):
        if fn.endswith('.json'):
            p = os.path.join(cdir, fn)
            doc = _storage.load_json(p)
            if isinstance(doc, dict) and doc.get('id') == character_id:
                doc['owner_user_id'] = user_id
                doc.setdefault('campaign_id', cid)
                _atomic_write_json(p, doc, indent=2)
                return True
    return False


@app.route('/join', methods=['GET', 'POST'])
def join():
    """Invite-code claim: create/sign-in an account, join the campaign, claim a PC."""
    code = (request.values.get('code') or '').strip()
    inv = _auth.get_invite(code) if code else None
    camp_name = (_campaigns.get_campaign(inv['campaign_id']) or {}).get('name') if inv else None
    if request.method == 'POST':
        if not inv:
            return render_template('join.html', error='Invalid or expired invite code.', code=code,
                                   invite=None, logged_in=bool(_auth.current_user())), 400
        u = _auth.current_user()
        if not u:
            try:
                u = _auth.create_user(request.form.get('username', ''), request.form.get('password', ''),
                                      display_name=request.form.get('display_name'))
            except ValueError as e:
                return render_template('join.html', error=str(e), code=code, invite=inv,
                                       campaign_name=camp_name, logged_in=False), 400
            _auth.login_user(u, remember=True)
        _auth.consume_invite(code)
        _campaigns.add_member(inv['campaign_id'], u['id'], inv['role'], character_id=inv.get('character_id'))
        if inv.get('character_id'):
            _claim_by_id(inv['campaign_id'], inv['character_id'], u['id'])
        _set_active_campaign(inv['campaign_id'])
        return redirect('/me')
    return render_template('join.html', error=None, code=code, invite=inv,
                           campaign_name=camp_name, logged_in=bool(_auth.current_user()))


def _me_characters(user_id):
    """Enriched 'My Characters' cards for /me -- level + class/order + a couple
    stats + a per-system accent, so the dashboard reads like a launcher rather
    than a flat list. Falls back gracefully if a doc can't be parsed."""
    cards = []
    _live = _storage.get_live_campaign_id()
    for ch in _campaigns.characters_for_user(user_id):
        card = dict(ch)
        cid = ch.get('campaign_id')
        card['sheet_url'] = _pc_sheet_url(ch.get('system'), ch.get('name'), ch.get('id'))
        card['is_live'] = (cid == _live)
        try:
            if ch.get('system') == 'cosmere':
                import systems.cosmere.build as _cb
                import systems.cosmere.radiant as _rad
                doc = _storage.load_json(os.path.join(_storage.cosmere_pc_dir(cid), ch['file']))
                b = _cb.CosmereBuild((doc or {}).get('build') or {}, homebrew=_cosmere_homebrew_store(cid))
                o = _rad.order(b.radiant_order)
                d = b.defenses()
                card.update(
                    subtitle='Level %d · %s' % (b.level, o['name'] if o else (b.path or 'Cosmere').title()),
                    accent=_rad.order_color(b.radiant_order) if b.is_radiant else _rad.DEFAULT_ACCENT,
                    stats=[('Phy', d['phy']), ('Cog', d['cog']), ('Spi', d['spi'])],
                )
            else:
                doc = _storage.load_json(os.path.join(_storage.party_dir(cid), ch['file']))
                b = (doc or {}).get('build') or {}
                sub = 'Level %s %s %s' % (b.get('level') or 1, b.get('ancestry') or '', b.get('class') or '')
                card.update(subtitle=' '.join(sub.split()) or 'Pathfinder 2e', accent='#e0b65a', stats=[])
        except Exception:
            card.setdefault('subtitle', (ch.get('system') or 'pf2e').upper())
            card.setdefault('accent', '#e0b65a')
            card.setdefault('stats', [])
        cards.append(card)
    return cards


def _me_render(**extra):
    u = _auth.current_user()
    try:
        _campaigns.purge_expired_trash()    # self-cleaning trash (no cron needed)
        _backups.ensure_backup_thread()     # lazily start the daily-snapshot thread
    except Exception:
        pass
    camps = _campaigns.campaigns_for_user(u['id'])
    trashed = _campaigns.trashed_for_user(u['id'])
    gm_ids = [c['id'] for c in camps if _campaigns.is_gm(c, u['id']) or u.get('is_admin')]
    live_id = _storage.get_live_campaign_id()
    # The campaign to offer as "Resume last session" -- the user's last-activated
    # table, but only if they're still a member of it.
    last = None
    last_id = u.get('last_campaign_id')
    if last_id:
        lc = _campaigns.get_campaign(last_id)
        if lc and (u.get('is_admin') or _campaigns.user_role(lc, u['id'])):
            last = {'id': lc['id'], 'name': lc.get('name'), 'system': lc.get('system') or 'pf2e',
                    'is_gm': _campaigns.is_gm(lc, u['id']) or bool(u.get('is_admin'))}
    ctx = dict(user=u, campaigns=camps, gm_campaign_ids=gm_ids,
               characters=_me_characters(u['id']),
               active_campaign_id=_active_campaign_id(),
               live_campaign_id=live_id, last_campaign=last,
               trashed_campaigns=trashed, trash_ttl_days=_campaigns.TRASH_TTL_DAYS,
               last_backup_at=_backups.last_backup_at())
    ctx.update(extra)
    return render_template('account_home.html', **ctx)


@app.route('/api/my_campaigns')
@_auth.login_required
def api_my_campaigns():
    """The logged-in user's campaigns (id/name/system/role/live/active) -- feeds
    the in-nav campaign switcher dropdown."""
    u = _auth.current_user()
    live = _storage.get_live_campaign_id()
    active = _active_campaign_id()
    out = []
    for c in _campaigns.campaigns_for_user(u['id']):
        gm = _campaigns.is_gm(c, u['id']) or bool(u.get('is_admin'))
        out.append({'id': c['id'], 'name': c.get('name'), 'system': c.get('system') or 'pf2e',
                    'role': 'GM' if gm else 'Player',
                    'is_live': c['id'] == live, 'is_active': c['id'] == active})
    return jsonify({'campaigns': out, 'active': active})


@app.context_processor
def _inject_account_ctx():
    """Expose the logged-in account user, the active campaign, and its system to
    every template -- so the nav switcher + all system-aware chrome render the
    right TTRPG (no PF2e/Cosmere bleed) off one source of truth."""
    u = None
    try:
        if _account_mode():
            u = _auth.current_user()
    except Exception:
        u = None
    return {
        'account_user': u,
        'active_campaign': (_active_campaign_doc() if u else None),
        'active_system': _active_system(),
        'cosmere_world': _cosmere_world(),
        'cosmere_player_char': _cosmere_player_char_name(),
        'system_ui': _active_system_ui(),
        'advancement_mode': (_advancement_mode() if u else 'milestone'),
    }


@app.route('/campaigns/new', methods=['GET', 'POST'])
@_auth.login_required
def new_campaign():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        system = request.form.get('system') or 'pf2e'
        if not name:
            return render_template('new_campaign.html', error='Please name the campaign.'), 400
        if system not in _storage.SUPPORTED_SYSTEMS:
            system = 'pf2e'
        camp = _campaigns.create_campaign(name, system, _auth.current_user()['id'])
        _set_active_campaign(camp['id'])
        return redirect('/me')
    return render_template('new_campaign.html', error=None)


@app.route('/me/password', methods=['POST'])
@_auth.login_required
def change_my_password():
    u = _auth.current_user()
    if not _auth.verify_credentials(u['username'], request.form.get('current_password', '')):
        return _me_render(pw_error='Current password is incorrect.'), 400
    try:
        _auth.set_password(u['id'], request.form.get('new_password', ''))
    except ValueError as e:
        return _me_render(pw_error=str(e)), 400
    return _me_render(pw_msg='Password updated.')


@app.route('/admin/users')
@_auth.login_required
def admin_users():
    if not _auth.current_user().get('is_admin'):
        return jsonify({'error': 'admin only'}), 403
    return render_template('admin_users.html', users=_auth.list_users(),
                           notice=session.pop('_pw_reset_notice', None))


@app.route('/admin/users/<uid>/reset', methods=['POST'])
@_auth.login_required
def admin_reset_password(uid):
    if not _auth.current_user().get('is_admin'):
        return jsonify({'error': 'admin only'}), 403
    target = _auth.get_user(uid)
    if target:
        import secrets as _secrets
        temp = _secrets.token_urlsafe(6)
        _auth.set_password(uid, temp)
        session['_pw_reset_notice'] = {'username': target['username'], 'temp': temp}
    return redirect('/admin/users')


@app.route('/admin/campaigns')
@_auth.login_required
def admin_campaigns():
    """Admin: see every campaign's stored game system (and which holds the live
    slot), and repair a mis-stamped one. A campaign saved as 'cosmere' routes the
    whole app to the Cosmere side; this is where you flip it back to Pathfinder."""
    if not _auth.current_user().get('is_admin'):
        return jsonify({'error': 'admin only'}), 403
    live = _storage.get_live_campaign_id()
    rows = []
    for c in _campaigns.list_campaigns():
        owner = _auth.get_user(c.get('created_by'))
        rows.append({
            'id': c['id'],
            'name': c.get('name') or '(unnamed)',
            'system': (c.get('system') or 'pf2e'),
            'members': len(c.get('members', [])),
            'owner': (owner or {}).get('display_name') or (owner or {}).get('username') or '—',
            'is_live': c['id'] == live,
        })
    rows.sort(key=lambda r: r['name'].lower())
    return render_template('admin_campaigns.html', campaigns=rows,
                           systems=list(_storage.SUPPORTED_SYSTEMS),
                           notice=session.pop('_campaign_notice', None))


@app.route('/admin/campaigns/<cid>/system', methods=['POST'])
@_auth.login_required
def admin_set_campaign_system(cid):
    """Admin: correct a campaign's stored game system (e.g. a Pathfinder game
    saved as Cosmere). Rebinds the in-memory state if it's the live campaign."""
    if not _auth.current_user().get('is_admin'):
        return jsonify({'error': 'admin only'}), 403
    new_system = (request.form.get('system') or '').strip().lower()
    camp = _campaigns.get_campaign(cid)
    if camp and new_system in _storage.SUPPORTED_SYSTEMS:
        old = camp.get('system') or 'pf2e'
        if old != new_system:
            camp['system'] = new_system
            _campaigns.save_campaign(camp)
            if _storage.get_live_campaign_id() == cid:
                load_campaign(cid)   # rebind globals so live state matches the new system
        session['_campaign_notice'] = {'name': camp.get('name') or cid, 'old': old, 'new': new_system}
    return redirect('/admin/campaigns')


@app.route('/gm')
@gm_required
def gm_hub():
    """GM Dashboard hub — links to all GM tools.

    Renders templates/gm_hub.html. Pulls campaign metadata so the hub can
    surface session number, next-session date, and tagline at the top —
    same context the campaign-intro lobby shows the players, but here it
    serves as a "what state is the table in" reminder for the GM.

    Also renders an excerpt of `Now Playing.md` from the Obsidian vault if
    available — turns the hub into a real session-prep dashboard rather
    than just a navigation index.

    System-aware: this PF2e command center is PF2e's GM home; any system whose GM
    home is elsewhere is redirected there (registry-driven), so there's no
    cross-system bleed (campaign settings / invites live on /me +
    /campaign/<id>/invites for both systems).
    """
    gm_home = _active_system_ui().gm_home
    if gm_home != '/gm':
        return redirect(gm_home)
    now_playing = None  # vault removed; manual session summary wired in separately
    return render_template(
        'gm_hub.html',
        party_count=len(PARTY_LIBRARY),
        monster_count=len(MONSTER_LIBRARY),
        encounter_count=len(ACTIVE_ENCOUNTER),
        campaign=_load_campaign_config(),
        now_playing=now_playing,
    )


def _load_story_threads():
    """Load story-thread beats for the /gm/threads diagram from
    story_threads.json (repo root). The GM's Cowork regenerates this file from
    the Obsidian vault and hands it over; it's dropped in here. Returns a list
    of beat dicts (empty on any error)."""
    try:
        with open(STORY_THREADS_FILE, encoding='utf-8') as f:
            return json.load(f).get('beats') or []
    except (OSError, ValueError):
        return []


@app.route('/gm/threads')
@gm_required
def gm_threads():
    """Branching story-thread diagram — a read-only view of how plot beats
    connect (open/resolved, NPCs, locations, session order). Data-driven; the
    site no longer reads the Obsidian vault directly."""
    return render_template('threads.html', beats=_load_story_threads())


def _save_story_threads(beats):
    path = STORY_THREADS_FILE
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'beats': beats}, f, indent=2, ensure_ascii=False)
        f.write('\n')


# ═══════════════════════════════════════════════════════════════════════
#  LOOT LEDGER — persistent party treasury and award log
# ═══════════════════════════════════════════════════════════════════════

@app.route('/gm/loot')
@gm_required
def loot_ledger_view():
    """Persistent loot ledger page -- wealth tracking and award history."""
    return render_template('loot_ledger.html')


@app.route('/api/loot_ledger')
@gm_required
def api_loot_ledger():
    """Return full loot ledger plus current party wealth summary."""
    ledger = _load_loot_ledger()
    party_wealth = []
    for name, pc in PARTY_LIBRARY.items():
        total_gp = (int(getattr(pc, 'pp', 0) or 0) * 10 +
                    int(getattr(pc, 'gp', 0) or 0) +
                    int(getattr(pc, 'sp', 0) or 0) * 0.1 +
                    int(getattr(pc, 'cp', 0) or 0) * 0.01)
        party_wealth.append({
            'name': name,
            'level': pc.level,
            'pp': int(getattr(pc, 'pp', 0) or 0),
            'gp': int(getattr(pc, 'gp', 0) or 0),
            'sp': int(getattr(pc, 'sp', 0) or 0),
            'cp': int(getattr(pc, 'cp', 0) or 0),
            'total_gp': round(total_gp, 1),
        })
    # Wealth-by-level reference from PF2E guidelines
    from pf2e_generator import PF2EGenerator
    gen = PF2EGenerator()
    avg_level = max(1, round(sum(pc.level for pc in PARTY_LIBRARY.values()) / max(len(PARTY_LIBRARY), 1)))
    expected = gen._wealth_by_level.get(min(max(avg_level, 1), 20), 175)
    return jsonify({
        'entries': ledger.get('entries', []),
        'party_wealth': party_wealth,
        'expected_wealth_per_pc': expected,
        'party_level': avg_level,
    })


@app.route('/api/loot_ledger/add', methods=['POST'])
@gm_required
def api_loot_ledger_add():
    """Manually add a ledger entry (for retroactive logging)."""
    data = request.get_json(silent=True) or {}
    recipient = (data.get('recipient') or '').strip()
    items = data.get('items') or []
    coins = data.get('coins') or {}
    note = (data.get('note') or '').strip()
    if not recipient:
        return jsonify({"success": False, "error": "recipient required"}), 400
    from datetime import datetime as _dt_ledger
    entry = {
        'id': str(uuid.uuid4()),
        'timestamp': _dt_ledger.now().isoformat(),
        'recipient': recipient,
        'items': [{'name': it.get('name', ''), 'qty': int(it.get('qty', 1) or 1)}
                  for it in items if isinstance(it, dict) and it.get('name')],
        'coins': {k: int(v or 0) for k, v in coins.items() if k in ('pp', 'gp', 'sp', 'cp')},
        'note': note,
    }
    _mutate_loot_ledger(lambda l: l['entries'].append(entry))
    return jsonify({"success": True})


@app.route('/api/loot_ledger/delete', methods=['POST'])
@gm_required
def api_loot_ledger_delete():
    """Delete a ledger entry by ID."""
    entry_id = (request.get_json(silent=True) or {}).get('id', '')
    if not entry_id:
        return jsonify({"success": False}), 400
    _mutate_loot_ledger(lambda l: l.update(entries=[e for e in l['entries'] if e.get('id') != entry_id]))
    return jsonify({"success": True})


# ── Cosmere loot ledger ───────────────────────────────────────────────────
# The Rosharan sibling of the PF2e ledger: spheres & spoils instead of coins.
# Reuses the same campaign-scoped storage (a Cosmere campaign keeps its own
# loot_ledger.json), just with Cosmere-shaped entries (spheres, gem colour).
_COSMERE_GEMS = ('diamond', 'garnet', 'ruby', 'sapphire', 'smokestone', 'emerald')
_SPHERE_DENOMS = (('broam', 20), ('mark', 5), ('chip', 1))   # value in clearchips


def _sphere_value(spheres):
    """A sphere stash's worth in clearchips (chip 1 / mark 5 / broam 20)."""
    s = spheres or {}
    return sum(int(s.get(k, 0) or 0) * v for k, v in _SPHERE_DENOMS)


@app.route('/cosmere/loot')
@gm_required
def cosmere_loot_view():
    """Cosmere loot ledger -- spheres & spoils awarded to the party. The Rosharan
    sibling of /gm/loot; redirects out in non-Cosmere mode like the other tools."""
    if _active_system() != 'cosmere':
        return redirect(_active_system_ui().gm_home)
    return render_template('cosmere_loot.html')


@app.route('/api/cosmere/loot')
@gm_required
def api_cosmere_loot():
    """Ledger entries + the Cosmere party roster + the running spheres total."""
    if _active_system() != 'cosmere':
        return jsonify({'error': 'cosmere only'}), 400
    ledger = _load_loot_ledger()
    entries = ledger.get('entries', [])
    party = []
    for d in _list_cosmere_pcs():
        b = d.get('build') or {}
        party.append({'name': d.get('name') or b.get('name') or 'Unnamed',
                      'level': b.get('level', 1)})
    return jsonify({'entries': entries, 'party': party, 'gems': list(_COSMERE_GEMS),
                    'total_chips': sum(_sphere_value(e.get('spheres')) for e in entries)})


def _credit_cosmere_loot(doc, items, spheres):
    """Accumulate awarded spheres + items onto a Cosmere PC doc's `wallet` so the
    award shows on the player's sheet (not just the ledger). Spheres sum by
    denomination; items merge by name. Returns the updated wallet."""
    w = doc.get('wallet') if isinstance(doc.get('wallet'), dict) else {}
    sp = dict(w.get('spheres') or {})
    for k in ('chip', 'mark', 'broam'):
        sp[k] = int(sp.get(k, 0) or 0) + int((spheres or {}).get(k, 0) or 0)
    goods = [dict(g) for g in (w.get('items') or []) if isinstance(g, dict)]
    for it in (items or []):
        name = str(it.get('name', '')).strip()
        if not name:
            continue
        qty = int(it.get('qty', 1) or 1)
        for g in goods:
            if g.get('name') == name[:80]:
                g['qty'] = int(g.get('qty', 0) or 0) + qty
                break
        else:
            goods.append({'name': name[:80], 'qty': qty})
    doc['wallet'] = {'spheres': sp, 'items': goods}
    return doc['wallet']


@app.route('/api/cosmere/loot/add', methods=['POST'])
@gm_required
def api_cosmere_loot_add():
    """Record a loot award (items + spheres) to a PC or the whole party."""
    if _active_system() != 'cosmere':
        return jsonify({'success': False, 'error': 'cosmere only'}), 400
    data = request.get_json(silent=True) or {}
    recipient = (data.get('recipient') or '').strip()
    if not recipient:
        return jsonify({'success': False, 'error': 'recipient required'}), 400
    items = data.get('items') or []
    spheres = data.get('spheres') or {}
    gem = data.get('gem') if data.get('gem') in _COSMERE_GEMS else 'diamond'
    norm_items = [{'name': str(it.get('name', '')).strip()[:80], 'qty': int(it.get('qty', 1) or 1)}
                  for it in items if isinstance(it, dict) and (it.get('name') or '').strip()]
    norm_spheres = {k: int(spheres.get(k, 0) or 0) for k in ('chip', 'mark', 'broam')}
    from datetime import datetime as _dt_loot
    entry = {
        'id': str(uuid.uuid4()),
        'timestamp': _dt_loot.now().isoformat(),
        'recipient': recipient[:60],
        'items': norm_items,
        'spheres': norm_spheres,
        'gem': gem,
        'note': (data.get('note') or '').strip()[:280],
    }
    _mutate_loot_ledger(lambda l: l['entries'].append(entry))
    # Credit a NAMED PC's sheet wallet (so player-visible wealth tracks the
    # ledger). A whole-party award (recipient not matching a PC, e.g. 'Party')
    # stays ledger-only. Mirrors the PF2e send_loot sheet write.
    for d in _list_cosmere_pcs():
        if (d.get('name') or (d.get('build') or {}).get('name') or '') == recipient:
            pid = d.get('id')
            wallet = None
            with _path_lock(_cosmere_pc_path(pid)):
                doc = _load_cosmere_pc(pid) or d
                wallet = _credit_cosmere_loot(doc, norm_items, norm_spheres)
                _save_cosmere_pc(doc, fsync=False)
            try:
                sse_broadcast('cosmere_loot', {'pid': pid, 'name': recipient,
                                               'wallet': wallet, 'items': norm_items,
                                               'spheres': norm_spheres})
            except Exception:
                pass
            break
    # Also feed the session scrapbook's loot highlights -- the ledger is the
    # permanent wealth record; this is tonight's haul for the Session Complete recap.
    try:
        with SESSION_HIGHLIGHTS_LOCK:
            SESSION_HIGHLIGHTS['loot'].append({'pc': recipient[:60], 'items': norm_items, 'spheres': norm_spheres})
            SESSION_HIGHLIGHTS['loot'] = SESSION_HIGHLIGHTS['loot'][-120:]
        _persist_session_highlights()
    except Exception:
        pass
    return jsonify({'success': True})


@app.route('/api/cosmere/loot/delete', methods=['POST'])
@gm_required
def api_cosmere_loot_delete():
    """Remove a loot-ledger entry by id."""
    entry_id = (request.get_json(silent=True) or {}).get('id', '')
    if not entry_id:
        return jsonify({'success': False}), 400
    _mutate_loot_ledger(lambda l: l.update(entries=[e for e in l['entries'] if e.get('id') != entry_id]))
    return jsonify({'success': True})


def _extract_beats_via_claude(session_notes, existing_beats):
    """Send session-note markdown + existing beats to Claude and get back a
    merged beats array. Returns (beats_list, error_reason)."""
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None, 'no_key'
    if not session_notes.strip():
        return None, 'empty_notes'

    existing_json = json.dumps(existing_beats, indent=2) if existing_beats else '[]'
    campaign = _load_campaign_config()

    prompt = (
        f"You are a story-thread analyst for the tabletop RPG campaign "
        f"\"{campaign.get('name', 'the campaign')}\".\n\n"
        f"Below are the GM's Obsidian session notes (one or more sessions), "
        f"followed by the EXISTING story-thread beats (JSON). Your job:\n\n"
        f"1. Read the session notes and identify every distinct plot thread / "
        f"story beat: a quest given, a mystery discovered, a confrontation, "
        f"a relationship formed, an NPC encounter, a location explored, etc.\n"
        f"2. For each beat produce a JSON object with these fields:\n"
        f"   - id: short kebab-case slug (e.g. \"kobold-warrens\")\n"
        f"   - title: short human-readable title\n"
        f"   - status: \"open\" if unresolved/ongoing, \"resolved\" if wrapped up\n"
        f"   - session: integer session number (derive from headings or context)\n"
        f"   - branches_to: list of beat IDs this thread leads into\n"
        f"   - npcs: list of NPC names involved\n"
        f"   - locations: list of location names\n"
        f"   - summary: one-sentence summary of what happened\n"
        f"3. MERGE with the existing beats:\n"
        f"   - If a beat from the existing list is clearly continued or concluded "
        f"in the new notes, UPDATE its status (open -> resolved if wrapped up) "
        f"and add branches_to links to new beats.\n"
        f"   - Keep existing beats that aren't mentioned (they may still be open).\n"
        f"   - Do NOT duplicate beats that already exist.\n"
        f"   - Connect related beats with branches_to links to show the story flow.\n"
        f"4. Return ONLY a JSON array of ALL beats (existing updated + new), "
        f"no commentary, no markdown fences, just the raw JSON array.\n\n"
        f"IMPORTANT: Ignore mechanical/logistical content (stat blocks, DCs, "
        f"dice rolls, XP, loot lists, grid coordinates). Focus on the NARRATIVE: "
        f"what happened in the story, who the party met, what choices they made, "
        f"what mysteries remain.\n\n"
        f"--- EXISTING BEATS ---\n{existing_json}\n--- END EXISTING ---\n\n"
        f"--- SESSION NOTES ---\n{session_notes[:30000]}\n--- END NOTES ---\n\n"
        f"Return the merged JSON array of beats."
    )

    import urllib.request as _urlreq
    import urllib.error as _urlerr

    SAFE_MODEL = 'claude-3-5-haiku-latest'
    model = (os.environ.get('ANTHROPIC_RECAP_MODEL') or SAFE_MODEL).strip()

    payload = json.dumps({
        'model': model,
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode('utf-8')
    req = _urlreq.Request(
        'https://api.anthropic.com/v1/messages',
        data=payload,
        headers={
            'content-type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        },
        method='POST',
    )
    try:
        with _urlreq.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        blocks = data.get('content') or []
        text = ''.join(b.get('text', '') for b in blocks if b.get('type') == 'text').strip()
        if not text:
            return None, 'empty_response'
        # Strip markdown fences if the model wraps it
        if text.startswith('```'):
            text = text.split('\n', 1)[-1]
            if text.endswith('```'):
                text = text[:-3]
            text = text.strip()
        beats = json.loads(text)
        if not isinstance(beats, list):
            return None, 'bad_format'
        return beats, None
    except _urlerr.HTTPError as e:
        err = ''
        try:
            err = e.read().decode('utf-8', 'replace')[:300]
        except Exception:
            pass
        print(f"[THREADS] Claude extract HTTP {e.code}: {err}")
        return None, f'api_http_{e.code}'
    except (_urlerr.URLError, TimeoutError) as e:
        print(f"[THREADS] Claude extract network error: {e}")
        return None, 'network_error'
    except (ValueError, KeyError) as e:
        print(f"[THREADS] Claude extract parse error: {e}")
        return None, f'parse_error: {e}'


@app.route('/api/threads/upload', methods=['POST'])
@gm_required
def api_threads_upload():
    """Accept one or more Obsidian .md files, extract story beats via Claude,
    merge with existing threads, and save."""
    files = request.files.getlist('files')
    if not files or not any(f.filename for f in files):
        return jsonify(success=False, error='No files uploaded.'), 400

    combined = []
    for f in files:
        if not f.filename or not f.filename.lower().endswith('.md'):
            continue
        try:
            text = f.read().decode('utf-8', errors='replace')
        except Exception:
            continue
        if text.strip():
            combined.append(f"## File: {f.filename}\n\n{text}")

    if not combined:
        return jsonify(success=False, error='No valid .md files found.'), 400

    session_notes = '\n\n---\n\n'.join(combined)
    existing = _load_story_threads()

    beats, err = _extract_beats_via_claude(session_notes, existing)
    if beats is None:
        return jsonify(success=False, error=f'AI extraction failed: {err}'), 502

    _save_story_threads(beats)
    return jsonify(success=True, beats=beats, count=len(beats))


@app.route('/api/session/export', methods=['POST'])
@gm_required
def api_session_export():
    """Build a session recap (party state + active encounter + combat log) and
    return it as text. The GM downloads it as a .txt and pastes it into Claude /
    Obsidian. No vault write — the site no longer hosts an Obsidian vault."""
    from datetime import datetime as _dt
    data = request.get_json(silent=True) or {}
    title = (data.get('title') or '').strip() or f"Session - {_dt.now().strftime('%Y-%m-%d')}"
    include_log = bool(data.get('include_log', True))
    campaign = _load_campaign_config() or {}
    md = [f"# {title}", ""]
    if campaign.get('name'):
        md.append(f"_Campaign: {campaign['name']}_")
    if campaign.get('session_number'):
        md.append(f"_Session number: {campaign['session_number']}_")
    if _active_system() == 'cosmere':
        # Cosmere party table — read from the Cosmere PC store (PARTY_LIBRARY is
        # PF2e-only and empty here), with Injuries instead of Hero points.
        md += ["", "## Party State", "", "| PC | Path | Health | Injuries | Conditions |",
               "|----|------|--------|----------|------------|"]
        for row in _cosmere_status_party(_list_cosmere_pcs()):
            conds = [k for k, v in (row.get('conditions') or {}).items() if v]
            md.append(f"| {row['name']} | {row.get('class_name', '')} | "
                      f"{row['current_hp']}/{row['max_hp']} | {row.get('injuries', 0)} | "
                      f"{', '.join(conds) if conds else '—'} |")
    else:
        md += ["", "## Party State", "", "| PC | Class | HP | Hero | Conditions |",
               "|----|-------|----|------|------------|"]
        for name, pc in sorted(PARTY_LIBRARY.items()):
            cls = (getattr(pc, 'class_name', '') or '').strip()
            conds = []
            try:
                for k, v in (pc.conditions or {}).items():
                    if v and v != 0 and v is not False:
                        conds.append(f"{k}{(' ' + str(v)) if isinstance(v, int) and v > 0 else ''}")
            except Exception:
                pass
            md.append(f"| {name} | {cls} | {getattr(pc, 'current_hp', 0)}/{getattr(pc, 'hp', 0)} | "
                      f"{getattr(pc, 'hero_points', 0)} | {', '.join(conds) if conds else '—'} |")
    md.append("")
    if ACTIVE_ENCOUNTER:
        md += ["## Active Encounter at Export", ""]
        for c in ACTIVE_ENCOUNTER:
            md.append(f"- **{c.name}** ({'PC' if c.is_pc else 'Enemy'}, init {c.initiative}) — HP {c.current_hp}/{c.hp}")
        md.append("")
    if include_log and COMBAT_LOGS:
        md += ["## Combat Log", ""]
        for e in COMBAT_LOGS:
            md.append(f"- `{e.get('time', '')}` R{e.get('round', '')} {e.get('type', '')} — {e.get('msg', '')}")
        md.append("")
    md += ["## GM Notes", "", "_Paste this into Claude / Obsidian and flesh out the prose recap._", ""]
    body = "\n".join(md)
    return jsonify({"success": True, "markdown": body, "byte_count": len(body.encode("utf-8")), "title": title})

@app.route('/tracker')
def tracker_view():
    sorted_monsters = sorted(MONSTER_LIBRARY.values(), key=lambda m: m.name)
    sorted_party = sorted(PARTY_LIBRARY.values(), key=lambda p: p.name)
    saved_encounters = [f.replace('.json', '') for f in os.listdir(ENCOUNTER_DIR) if f.endswith('.json')] if os.path.exists(ENCOUNTER_DIR) else []
    party_level = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc] or [p.level for p in PARTY_LIBRARY.values()] or [1])
    encounter_xp = calculate_encounter_xp(ACTIVE_ENCOUNTER, party_level)
    diff_label, diff_color = get_difficulty_label(encounter_xp)
    initial_state = _get_tracker_state()
    # In Cosmere mode, feed the tool strip Cosmere adversaries + party so a GM
    # can build a Stormlight encounter from the tracker (the PF2e monster search
    # + party picker are gated off in the template). PF2e mode is unchanged.
    cosmere_adversaries, cosmere_pcs = [], []
    if _active_system() == 'cosmere':
        _seen_adv = set()
        for d in systems.cosmere.adversary_docs():
            s = d.get('system', {})
            cosmere_adversaries.append({'id': d.get('_id'), 'name': d.get('name', 'Unknown'),
                                        'tier': s.get('tier'), 'role': s.get('role'), 'custom': False})
            _seen_adv.add(d.get('_id'))
        # GM-authored homebrew adversaries — so the GM's saved custom enemies are
        # re-addable from the tracker's adversary picker, not just creatable as a
        # one-off. _cosmere_doc_by_id already resolves these for add-to-tracker.
        for d in _load_cosmere_custom_adversaries():
            if not isinstance(d, dict) or d.get('_id') in _seen_adv:
                continue
            s = d.get('system', {}) if isinstance(d.get('system'), dict) else {}
            cosmere_adversaries.append({'id': d.get('_id'), 'name': d.get('name', 'Unknown'),
                                        'tier': s.get('tier'), 'role': s.get('role') or 'Homebrew', 'custom': True})
        cosmere_adversaries.sort(key=lambda a: ((a['tier'] or 0), (a['name'] or '').lower()))
        cosmere_pcs = [{'id': d['id'], 'name': d.get('name', '?')} for d in _list_cosmere_pcs()]
    return render_template('tracker.html', monsters=sorted_monsters, party=sorted_party, initial_state=initial_state, turn_index=TURN_INDEX, round_number=ROUND_NUMBER, saved_encounters=sorted(saved_encounters), encounter_xp=encounter_xp, diff_label=diff_label, diff_color=diff_color, party_level=party_level, turn_reminders=TURN_REMINDERS, cosmere_adversaries=cosmere_adversaries, cosmere_pcs=cosmere_pcs,
        cosmere_initiative=_cosmere_initiative_mode())

def _is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or request.content_type == 'application/json'

def _tracker_json_response(extra=None):
    """Return full tracker state as JSON for AJAX calls. `extra`, if given, is
    merged into a SHALLOW COPY of the state (so the cached state dict isn't
    polluted) — e.g. the per-action `applied` HP delta a damage/heal reports
    back so the client toast can show the net amount that actually landed."""
    state = _get_tracker_state()
    if extra:
        state = dict(state)
        state.update(extra)
    return jsonify(state)


def _find_active_combatant(instance_id):
    """The combatant with this instance_id in the live encounter, or None."""
    return next((c for c in ACTIVE_ENCOUNTER if c.instance_id == instance_id), None)


def require_live_combatant(fn):
    """Guard per-combatant actions against a STALE tracker tab. If the targeted
    instance_id is no longer in the live encounter (the encounter was cleared,
    reloaded, or the campaign switched out from under an open tab), fail loudly
    with 409 + {"stale": true} instead of the old silent fall-through to a 200
    with unchanged state -- which read as "nothing happened" (HP wouldn't move,
    no error). The client surfaces the message and re-syncs from /api/tracker_state."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        iid = kwargs.get('instance_id')
        if iid is not None and _find_active_combatant(iid) is None:
            return jsonify({'error': 'This combatant is no longer in the live encounter — reloading the tracker.',
                            'stale': True}), 409
        return fn(*args, **kwargs)
    return wrapper

def _get_tracker_state():
    """Build the full tracker state dict.
    Cached for _TRACKER_STATE_TTL seconds; invalidated by _broadcast_encounter_state."""
    global _TRACKER_STATE_CACHE, _TRACKER_STATE_CACHE_TIME
    now = time.time()
    cached = _TRACKER_STATE_CACHE
    if cached is not None and (now - _TRACKER_STATE_CACHE_TIME) < _TRACKER_STATE_TTL:
        return cached
    with ENCOUNTER_LOCK:
        active_name = ACTIVE_ENCOUNTER[TURN_INDEX].name if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
        combatants = []
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            entry = {
                'instance_id': c.instance_id, 'name': c.name, 'is_pc': c.is_pc,
                'system': getattr(c, 'system', 'pf2e'),
                'initiative': c.initiative, 'is_active': (i == TURN_INDEX),
                'level': c.level, 'ac': c.ac, 'current_hp': c.current_hp, 'max_hp': c.hp,
                'fort': c.fort, 'ref': c.ref, 'will': c.will,
                'perception': c.perception, 'speed': getattr(c, 'active_speed', getattr(c, 'speed', 25)),
                'conditions': {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False},
                'condition_expiry': dict(getattr(c, 'condition_expiry', {}) or {}),
                'actions_used': int(getattr(c, 'actions_used', 0) or 0),
                'max_actions': int(getattr(c, 'max_actions', 3) or 3),
                # Render the tracker-display string. PCs store a list[dict];
                # monsters keep the legacy "1d6 fire" string.
                'persistent_damage': (
                    ', '.join(
                        f"{e.get('damage','?')} {e.get('type','')}".strip()
                        for e in getattr(c, 'persistent_damage', [])
                        if isinstance(e, dict)
                    )
                    if c.is_pc else
                    getattr(c, 'persistent_damage', '')
                ),
                'elite_weak': getattr(c, 'elite_weak', 0),
                'delaying': getattr(c, 'delaying', False),
                'base_ac': getattr(c, 'base_ac', c.ac),
                # GM-side flag: when False the player SSE feed masks this
                # combatant. The tracker UI renders an eye icon off this.
                'visible_to_players': getattr(c, 'visible_to_players', True),
                # Boss-reveal title (Chunk 4d) — the tracker renders an
                # epithet input off this so the GM can edit it inline.
                'epithet': getattr(c, 'epithet', ''),
                # GM creature tactics notes — per-combatant free-text.
                'tactics': getattr(c, 'tactics', ''),
                # Hazard-specific fields the tracker uses to render the
                # Trigger / Disable buttons + display routine text. Always
                # emitted so the client can branch on `is_hazard`.
                'is_hazard': getattr(c, 'is_hazard', False),
                'hazard_type': getattr(c, 'hazard_type', ''),
                'stealth_dc': getattr(c, 'stealth_dc', 0),
                'disable_dc': getattr(c, 'disable_dc', 0),
                'trigger': getattr(c, 'trigger', ''),
                'routine': getattr(c, 'routine', ''),
                'disabled': getattr(c, 'disabled', False),
                'triggered': getattr(c, 'triggered', False),
            }
            hp_pct = (c.current_hp / c.hp * 100) if c.hp > 0 else 0
            entry['hp_pct'] = round(hp_pct)
            if c.is_pc:
                entry['strikes'] = [{'name': a['name'], 'hit': a['strikes'][0]['label'] if a.get('strikes') else '+?', 'damage': a['damage']} for a in getattr(c, 'attacks', [])]
                entry['feats'] = [{'name': f['name'], 'desc': f.get('desc', '')} for f in getattr(c, 'feats', [])]
                # Spell casters: name, tradition, type, and per-rank known
                # spell list. The active-combatant card on the tracker uses
                # this so the GM can see the PC's spell options at a glance.
                # Trim the levels payload to just what the card needs (name +
                # action cost) to keep the tracker_state response lean.
                _sc = []
                for sc in getattr(c, 'spell_casters', []) or []:
                    _levels = []
                    for lvl in (sc.get('levels') or []):
                        _spells = [{'name': sp.get('name', ''), 'actions': sp.get('actions', '')}
                                   for sp in (lvl.get('spells') or []) if sp.get('name')]
                        if _spells or lvl.get('slots'):
                            _levels.append({
                                'level': lvl.get('level'),
                                'label': lvl.get('label', f"Rank {lvl.get('level','?')}"),
                                'slots': lvl.get('slots', 0),
                                'spells': _spells,
                            })
                    _sc.append({
                        'name': sc.get('name', ''),
                        'tradition': sc.get('tradition', ''),
                        'type': sc.get('type', ''),
                        'levels': _levels,
                    })
                entry['spell_casters'] = _sc
                entry['spell_attack'] = getattr(c, 'spell_attack', 0)
                entry['spell_dc'] = getattr(c, 'spell_dc', 0)
                entry['focus_pool'] = getattr(c, 'focus_max', 0)
                entry['focus_current'] = getattr(c, 'current_focus', 0)
                entry['reaction_used'] = bool(getattr(c, 'reaction_used', False))
                entry['hero_points'] = int(getattr(c, 'hero_points', 0) or 0)
                # Raw structured persistent-damage entries — the active panel
                # uses this to render per-entry roll/remove buttons. The
                # `persistent_damage` field above remains the joined string
                # used by the small initiative row.
                entry['persistent_damage_list'] = [
                    {'damage': e.get('damage', ''), 'type': e.get('type', ''), 'source': e.get('source', '')}
                    for e in (getattr(c, 'persistent_damage', []) or [])
                    if isinstance(e, dict)
                ]
            else:
                entry['strikes'] = [{'name': s.get('name', ''), 'hit': (lambda b: f"+{b}" if b >= 0 else str(b))(s.get('bonus', s.get('mod', 0))), 'damage': s.get('damage', '')} for s in getattr(c, 'strikes', [])]
                entry['actions'] = [{'name': a['name'], 'description': a.get('description', '')} for a in getattr(c, 'actions', [])]
                entry['immunities'] = getattr(c, 'immunities', [])
                entry['resistances'] = getattr(c, 'resistances', [])
                entry['weaknesses'] = getattr(c, 'weaknesses', [])
                entry['traits'] = getattr(c, 'traits', [])
                entry['reaction_used'] = bool(getattr(c, 'reaction_used', False))
                # Spell attack / DC for caster foes, so the GM can quick-reference
                # them in the inspector the same way they can for PCs.
                entry['spell_attack'] = int(getattr(c, 'spell_attack', 0) or 0)
                entry['spell_dc'] = int(getattr(c, 'spell_dc', 0) or 0)
            # Cosmere combatants carry an extra stat block (defenses/deflect/
            # resources); the tracker UI branches on `system` to render it.
            if entry['system'] == 'cosmere' and hasattr(c, 'tracker_block'):
                entry['cosmere'] = c.tracker_block()
            combatants.append(entry)
        party_level = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc] or [p.level for p in PARTY_LIBRARY.values()] or [1])
        encounter_xp = calculate_encounter_xp(ACTIVE_ENCOUNTER, party_level)
        diff_label, diff_color = get_difficulty_label(encounter_xp)
        from class_matrix import ENCOUNTER_DIFFICULTY
        party_size = max(1, sum(1 for c in ACTIVE_ENCOUNTER if c.is_pc) or len(PARTY_LIBRARY) or 4)
        xp_thresholds = {t["name"]: t["base"] + t["per_extra"] * (party_size - 4) for t in ENCOUNTER_DIFFICULTY}
        result = {
            'combatants': combatants, 'round': ROUND_NUMBER, 'turn_index': TURN_INDEX,
            'active_name': active_name, 'encounter_xp': encounter_xp,
            'diff_label': diff_label, 'diff_color': diff_color, 'party_level': party_level,
            'party_size': party_size, 'xp_thresholds': xp_thresholds,
            'encounter_notes': ENCOUNTER_NOTES,
            'session_timer_start': SESSION_TIMER_START,
        }
    _TRACKER_STATE_CACHE = result
    _TRACKER_STATE_CACHE_TIME = now
    return result

@app.route('/api/tracker_state')
def api_tracker_state():
    """GET endpoint for full tracker state (AJAX polling fallback)."""
    return _tracker_json_response()

# ── Cosmere RPG (Phase 3): bestiary browsing, sheet view, tracker add ──────
def _load_cosmere_custom_adversaries():
    """Per-campaign GM-authored Cosmere adversaries (homebrew). Stored as full
    Foundry-shaped docs so CosmereActor + the tracker treat them like canon."""
    data = _storage.load_json(COSMERE_ADVERSARIES_FILE, []) if COSMERE_ADVERSARIES_FILE else []
    return data if isinstance(data, list) else []


def _save_cosmere_custom_adversary(doc):
    advs = _load_cosmere_custom_adversaries()
    advs.append(doc)
    if COSMERE_ADVERSARIES_FILE:
        _atomic_write_json(COSMERE_ADVERSARIES_FILE, advs, indent=2)


def _build_cosmere_adversary_doc(data):
    """Build a Foundry-shaped Cosmere adversary doc from a GM quick form, using
    overrides so the GM's final defenses / health / deflect / strike are used
    verbatim (CosmereActor honors `useOverride`)."""
    def _gi(k, dflt):
        try:
            return int(data.get(k, dflt) if data.get(k) not in (None, '') else dflt)
        except (TypeError, ValueError):
            return dflt
    name = (str(data.get('name') or 'Custom Adversary').strip()) or 'Custom Adversary'
    health = max(1, _gi('health', 12))
    atk_mod = _gi('atk_mod', 5)
    return {
        '_id': uuid.uuid4().hex,
        'name': name[:80], 'type': 'adversary',
        'system': {
            'level': {'value': _gi('level', 1)},
            'tier': _gi('tier', 1), 'role': str(data.get('role') or '').strip()[:40],
            'size': str(data.get('size') or 'medium').strip()[:20],
            'attributes': {k: {'value': 0} for k in ('str', 'spd', 'int', 'wil', 'awa', 'pre')},
            'defenses': {
                'phy': {'useOverride': True, 'override': _gi('phy', 11)},
                'cog': {'useOverride': True, 'override': _gi('cog', 11)},
                'spi': {'useOverride': True, 'override': _gi('spi', 11)},
            },
            'resources': {
                'hea': {'value': health, 'max': {'useOverride': True, 'override': health}},
                'foc': {'value': _gi('focus', 0), 'max': {'useOverride': True, 'override': _gi('focus', 0)}},
                'inv': {'value': 0, 'max': {}},
            },
            'deflect': {'natural': max(0, _gi('deflect', 0)),
                        'types': {'impact': True, 'keen': True, 'energy': True,
                                  'spirit': False, 'vital': False}},
            'skills': {'hwp': {'attribute': 'str', 'rank': 0,
                               'mod': {'useOverride': True, 'override': atk_mod}, 'unlocked': True}},
        },
        'items': [{
            'type': 'weapon', 'name': (str(data.get('atk_name') or 'Strike').strip() or 'Strike')[:60],
            'system': {'damage': {'formula': (str(data.get('atk_dmg') or '1d6').strip() or '1d6')[:40],
                                  'type': (str(data.get('atk_type') or 'impact').strip() or 'impact'),
                                  'skill': 'hwp'}},
        }],
    }


def _cosmere_doc_by_id(actor_id):
    """An ingested Cosmere adversary doc by its Foundry _id (or None) -- across
    the base system bestiary, the ingested modules, AND per-campaign GM-authored
    homebrew adversaries (so a custom adversary resolves for both add-to-tracker
    and encounter restore)."""
    for d in systems.cosmere.adversary_docs():
        if d.get('_id') == actor_id:
            return d
    for d in _load_cosmere_custom_adversaries():
        if isinstance(d, dict) and d.get('_id') == actor_id:
            return d
    return None


def _cosmere_combatant(actor_id):
    """A fresh CosmereActor for the tracker (independent mutable combat state) --
    a bestiary adversary by Foundry _id, or a campaign Cosmere PC by id. A PC's
    actor doc carries type='character' so is_pc (the fast/slow PC phase) is set."""
    doc = _cosmere_doc_by_id(actor_id)
    if doc:
        actor = systems.cosmere.CosmereActor(doc)
        actor.restore_id = actor_id        # so the encounter autosave can rehydrate it
        return actor
    pc = _load_cosmere_pc(actor_id)
    if pc:
        import systems.cosmere.build as _cb
        actor = systems.cosmere.CosmereActor(
            _cb.CosmereBuild(pc.get('build') or {}, homebrew=_cosmere_homebrew_store()).to_actor_doc())
        actor.name = pc.get('name') or actor.name
        actor.restore_id = actor_id        # so the encounter autosave can rehydrate it
        return actor
    return None


def _augment_combatant_save(entry, c):
    """Stamp the game system (+ Cosmere rehydrate keys) onto a serialized
    combatant so the encounter autosave / saved encounter round-trips across
    systems. PF2e combatants get only `system` (back-compatible)."""
    entry['system'] = getattr(c, 'system', 'pf2e')
    if entry['system'] == 'cosmere':
        entry['cosmere_id'] = getattr(c, 'restore_id', None)
        entry['injuries'] = int(getattr(c, 'injuries', 0) or 0)
        entry['injury_log'] = [dict(r) for r in (getattr(c, 'injury_log', []) or []) if isinstance(r, dict)]
        entry['speed_choice'] = getattr(c, 'speed_choice', None)
    return entry


def _restore_cosmere_combatant(item):
    """Rebuild a Cosmere combatant from a serialized encounter entry, overlaying
    its live combat state (HP / injuries / conditions / fast-slow / initiative /
    visibility). Returns None if it can't be rebuilt (unknown id, or a malformed
    PC build that throws) -- a single bad combatant must never abort the whole
    restore loop and wipe the live fight."""
    try:
        new_c = _cosmere_combatant(item.get('cosmere_id'))
    except Exception as e:
        print(f"[ENCOUNTER] skipped un-rebuildable cosmere combatant "
              f"{item.get('cosmere_id')!r}: {e}")
        return None
    if new_c is None:
        return None
    new_c.instance_id = item.get('instance_id', str(uuid.uuid4()))
    new_c.initiative = item.get('initiative', 0)
    if 'current_hp' in item:
        new_c.current_hp = item['current_hp']
    if isinstance(item.get('conditions'), dict):
        new_c.conditions = dict(item['conditions'])
    if isinstance(item.get('condition_expiry'), dict):
        new_c.condition_expiry = dict(item['condition_expiry'])
    try:
        new_c.injuries = max(0, int(item.get('injuries', getattr(new_c, 'injuries', 0)) or 0))
    except (TypeError, ValueError):
        pass
    if isinstance(item.get('injury_log'), list):
        new_c.injury_log = [dict(r) for r in item['injury_log'] if isinstance(r, dict)]
    if item.get('speed_choice'):
        new_c.speed_choice = item['speed_choice']
    if 'delaying' in item:
        new_c.delaying = bool(item['delaying'])
    # Match the fast(2)/slow(3) action ceiling cycle_turn would set, so the pip
    # widget is right immediately on reload (not only after the next turn).
    new_c.max_actions = 2 if getattr(new_c, 'speed_choice', None) == 'fast' else 3
    if 'visible_to_players' in item:
        new_c.visible_to_players = bool(item['visible_to_players'])
    if item.get('epithet'):
        new_c.epithet = str(item['epithet'])
    if 'tactics' in item:
        new_c.tactics = str(item['tactics'] or '')
    return new_c


@app.route('/cosmere/bestiary')
def cosmere_bestiary():
    """Browse the ingested Cosmere adversaries (read-only reference)."""
    advs = []
    for d in systems.cosmere.adversary_docs():
        s = d.get('system', {})
        advs.append({
            'id': d.get('_id'), 'name': d.get('name', 'Unknown'),
            'tier': s.get('tier'), 'role': s.get('role'), 'size': s.get('size'),
        })
    advs.sort(key=lambda a: ((a['tier'] or 0), (a['name'] or '').lower()))
    return render_template('cosmere_bestiary.html', adversaries=advs)


@app.route('/cosmere/sheet/<actor_id>')
def cosmere_sheet(actor_id):
    """Render one Cosmere actor's character sheet."""
    doc = _cosmere_doc_by_id(actor_id)
    if not doc:
        return ('Unknown Cosmere actor', 404)
    actor = systems.cosmere.CosmereActor(doc)
    return render_template(
        'cosmere_sheet.html', a=actor.to_summary(), actor_id=actor_id,
        actions=actor.actions, strikes=actor.strikes, traits=actor.traits,
        skill_names=systems.cosmere.SKILL_NAMES,
        attr_names=systems.cosmere.ATTR_NAMES,
        defense_names=systems.cosmere.DEFENSE_NAMES,
    )


# ── Cosmere PCs: builder + leveler + saved-character store ─────────────────
# Built Cosmere PCs live in their own flat store (DATA_DIR/cosmere_pcs/), NOT
# the active PF2e campaign's party_data -- so they never enter the PF2e
# PARTY_LIBRARY (whose routes assume the PF2e Character shape). A real Cosmere
# campaign binding is a later phase.
# COSMERE_PC_DIR is bound per-campaign by _bind_campaign_paths() (campaign-scoped
# when a campaign is live, legacy flat DATA_DIR/cosmere_pcs otherwise) -- do NOT
# reassign it here; this module loads after the import-time bind and would clobber it.


def _cosmere_pc_path(pid):
    if not re.match(r'^[0-9a-f]{32}$', pid or ''):
        return None
    return os.path.join(COSMERE_PC_DIR, pid + '.json')


def _load_cosmere_pc(pid):
    p = _cosmere_pc_path(pid)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _delete_cosmere_pc(pid):
    """Remove a Cosmere PC file from the active campaign's store (no-op if gone)."""
    p = _cosmere_pc_path(pid)
    if p and os.path.isfile(p):
        os.remove(p)
        return True
    return False


def _list_cosmere_pcs():
    out = []
    if os.path.isdir(COSMERE_PC_DIR):
        for fn in sorted(os.listdir(COSMERE_PC_DIR)):
            if fn.endswith('.json'):
                d = _load_cosmere_pc(fn[:-5])
                if d:
                    out.append(d)
    return out


def _save_cosmere_pc(doc, fsync=True):
    os.makedirs(COSMERE_PC_DIR, exist_ok=True)
    pid = doc.get('id') or uuid.uuid4().hex
    doc['id'] = pid
    # Bind to the active campaign + owner so 'My Characters' and character->system
    # inference work (the PC file already lives under the campaign's cosmere_pcs/).
    if ACTIVE_CAMPAIGN_ID and not doc.get('campaign_id'):
        doc['campaign_id'] = ACTIVE_CAMPAIGN_ID
    try:
        # Default owner = the saver, but only when the caller didn't decide
        # ownership explicitly (the builder sets the key -- None for a GM-built
        # PC left assignable, a user id for a player's own -- so this won't fire
        # for it; it's a fallback for any other caller).
        if _account_mode() and 'owner_user_id' not in doc:
            u = _auth.current_user()
            if u:
                doc['owner_user_id'] = u['id']
    except Exception:
        pass
    _atomic_write_json(_cosmere_pc_path(pid), doc, indent=2, fsync=fsync)
    return pid


# --- Cosmere homebrew (per-campaign content shelf) ------------------------
def _load_homebrew_raw(cid=None):
    """The raw homebrew store ({type: [entry]}) for `cid` (active campaign if
    None); empty dict when absent."""
    path = _storage.homebrew_file(cid) if cid else COSMERE_HOMEBREW_FILE
    if path and os.path.isfile(path):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _cosmere_homebrew_store(cid=None):
    """Normalized homebrew store for the build engine + builder pickers."""
    import systems.cosmere.homebrew as _hb
    return _hb.normalize_store(_load_homebrew_raw(cid))


def _save_homebrew_raw(store):
    _atomic_write_json(COSMERE_HOMEBREW_FILE, store, indent=2)


def _cosmere_cultures():
    """All cultures for the builder -- base system + the ingested handbook
    (Iriali, Kharbranthian, Listener, Natan, Reshi, Shin, Wayfarer, ...)."""
    names = {d.get('name', '') for pack in ('cultures', 'handbook-cultures')
             for d in systems.cosmere.load_pack(pack) if d.get('name')}
    return sorted(n for n in names if n)


def _talent_prereq_summary(pr):
    """A short human-readable summary of a talent's Foundry prerequisites."""
    if not isinstance(pr, dict) or not pr:
        return ''
    parts = []
    for grp in pr.values():
        if not isinstance(grp, dict):
            continue
        t = grp.get('type')
        if t == 'skill':
            parts.append('%s rank %s' % (systems.cosmere.SKILL_NAMES.get(grp.get('skill'), grp.get('skill')), grp.get('rank')))
        elif t == 'talent':
            parts.append(' or '.join(x.get('label', '?') for x in (grp.get('talents') or [])))
        elif t == 'attribute':
            parts.append('%s %s' % (grp.get('attribute'), grp.get('value')))
        elif t == 'goal':
            parts.append('a completed goal')
        elif t == 'connection':
            parts.append('a connection')
        elif t:
            parts.append(str(t))
    return ' + '.join(p for p in parts if p)


def _cosmere_path_talents():
    """{path_id: [{id, name, key, prereq}]} for the builder's talent picker
    (key talents first; each carries a prerequisite summary)."""
    import systems.cosmere.origins as _o
    import systems.cosmere.talents as _ct
    from systems.cosmere.radiant_talents import _effect as _talent_effect   # readable one-line summary
    # The real key talent per path is the one named in PATH_INFO (handbook trees
    # have many prereq-less roots, so "no prerequisite" is NOT the discriminator).
    key_names = {p: (info.get('key_talent') or '').strip().lower()
                 for p, info in _o.PATH_INFO.items()}
    out, seen = {}, set()
    # Base system talents first, then the fuller handbook trees; a name clash
    # per path keeps the BASE talent (so origins.PATH_INFO key-talent ids +
    # talents.py prereqs still match), and the handbook adds the rest.
    for pack in ('heroic-paths', 'handbook-heroic-paths'):
        for d in systems.cosmere.load_pack(pack):
            if d.get('type') != 'talent':
                continue
            s = d.get('system', {})
            p = _ct.norm_path(s.get('path'))     # champion (a Leader specialty) -> leader
            if not p:
                continue
            name = d.get('name', '')
            dkey = (p, name.strip().lower())
            if not name or dkey in seen:
                continue
            seen.add(dkey)
            # Resolve prereqs from the talent-tree graph (authoritative); this
            # corrects the null / swapped / stale-label item-level prerequisites.
            out.setdefault(p, []).append({
                'id': d.get('_id'), 'name': name,
                'key': name.strip().lower() == key_names.get(p, '\x00'),
                'specialty': _ct.talent_specialty().get(d.get('_id'), ''),   # '' = core path talent
                'prereq': _talent_prereq_summary(_ct.resolved_prereqs(d.get('_id'))),
                'effect': _talent_effect(s.get('description')),   # readable text for the inspector panel
            })
    for p in out:
        # Key talents first, then group by specialty (core '' first), then name.
        out[p].sort(key=lambda t: (not t['key'], t['specialty'] or '', t['name'].lower()))
    return out


def _cosmere_starting_kits():
    """The rulebook starting kits, with armor/weapons resolved to catalog items
    (id+name) so the builder can add them; equipment/marks/bonus are notes."""
    import systems.cosmere.origins as _o
    import systems.cosmere.items as _it
    out = []
    for key, k in _o.STARTING_KITS.items():
        items = []
        if k['armor']:
            a = _it.by_name(k['armor'])
            if a:
                items.append({'id': a['id'], 'name': a['name']})
        for wn in k['weapons']:
            w = _it.by_name(wn)
            if w:
                items.append({'id': w['id'], 'name': w['name']})
        out.append({'key': key, 'name': k['name'], 'items': items,
                    'equipment': k['equipment'], 'marks': k['marks'], 'bonus': k['bonus']})
    return out


def _cosmere_builder_context(build, hb_store=None):
    import systems.cosmere.build as _cb
    import systems.cosmere.radiant as _rad
    import systems.cosmere.radiant_talents as _rt
    import systems.cosmere.infected as _inf
    if hb_store is None:
        hb_store = _cosmere_homebrew_store()
    return dict(
        build=build.to_dict(),
        infected_arts=_inf.load_arts(),     # the Infected Arts catalog (disease cost + abilities)
        catalog=systems.cosmere.items.catalog(),
        fabrials=systems.cosmere.items.fabrials(),
        skill_names=build.eff_skill_names(), skill_attr=build.eff_skill_attr(),
        surge_skills=list(build.eff_surge_skills()),
        attr_names=systems.cosmere.ATTR_NAMES,
        attr_desc=systems.cosmere.ATTR_DESC, skill_desc=systems.cosmere.SKILL_DESC,
        culture_info=systems.cosmere.origins.CULTURE_INFO,
        ancestry_info=systems.cosmere.origins.ANCESTRY_INFO,
        paths=list(systems.cosmere.PATHS), cultures=_cosmere_cultures(),
        path_talents=_cosmere_path_talents(),
        path_trees=systems.cosmere.talents.tree_graphs(),   # positioned DAG per path (visual tree)
        radiant_orders=_rad.RADIANT_ORDERS, radiant_variants=_rad.RADIANT_VARIANTS,
        surges=_rad.SURGES, first_ideal=_rad.FIRST_IDEAL,
        radiant_ideals=_rad.ORDER_IDEALS, ideal_personal=list(_rad.IDEAL_PERSONAL),
        fourth_ideal_level=_rad.FOURTH_IDEAL_LEVEL,
        path_info=systems.cosmere.origins.PATH_INFO,
        singer_forms=systems.cosmere.origins.SINGER_FORMS,
        singer_change_form=systems.cosmere.origins.SINGER_CHANGE_FORM,
        starting_kits=_cosmere_starting_kits(),
        radiant_surge_talents=_rt.SURGE_TALENTS, radiant_order_talents=_rt.ORDER_TALENTS,
        radiant_surge_powers=_rt.SURGE_POWERS,
        radiant_trees=_rt.radiant_tree_graphs(),    # positioned Radiant DAGs (visual tree)
        homebrew=hb_store,            # {type: [entry]} -- merged into the pickers client-side
        budgets=dict(
            attr_points=build.attr_points_available(),
            skill_ranks=build.skill_ranks_available(),
            max_skill_rank=_cb.max_skill_rank(build.level),
            talents=build.talents_available(),
            expertises=build.expertises_available(),
        ),
    )


@app.route('/cosmere/gm')
@gm_required
def cosmere_gm_hub():
    """Cosmere GM dashboard -- the command center for a Stormlight campaign.

    The Cosmere sibling of the PF2e /gm hub: a header (campaign + live counts)
    over a tile grid of the Cosmere GM tools (roster, builder, bestiary, the
    deflect/injury/Plot-Die tracker, story threads, session tools). System-aware
    and registry-driven -- it redirects out if the active campaign isn't Cosmere,
    mirroring how /gm redirects a Cosmere GM here, so there's no cross-system
    bleed.
    """
    if _active_system() != 'cosmere':
        return redirect(_active_system_ui().gm_home)
    adv_count = len(systems.cosmere.adversary_docs())
    camp = _active_campaign_doc() or {}
    cfg = _load_campaign_config()
    return render_template(
        'cosmere_gm.html',
        campaign_name=camp.get('name') or 'Cosmere Campaign',
        char_count=len(_list_cosmere_pcs()),
        adv_count=adv_count,
        encounter_count=len(ACTIVE_ENCOUNTER),
        active_cid=ACTIVE_CAMPAIGN_ID,
        last_recap=cfg.get('last_recap', ''),
        session_number=cfg.get('session_number', 1),
    )


@app.route('/cosmere/gmscreen')
@gm_required
def cosmere_gmscreen():
    """Cosmere GM Screen -- the rules reference at a glance (Stormlight Ch.9/10/13):
    turn order, damage & Deflect, the injury death-spiral, conditions, the Plot
    Die, surges & Stormlight, the Radiant orders, rest, and the stat math. Every
    table is sourced from the Cosmere engine modules (combat / radiant / build)
    so it can't drift from the rules the app actually runs. System-aware:
    redirects out in non-Cosmere mode, like the dashboard.
    """
    if _active_system() != 'cosmere':
        return redirect(_active_system_ui().gm_home)
    import systems.cosmere as _cos
    import systems.cosmere.combat as _cmb
    import systems.cosmere.radiant as _rad
    import systems.cosmere.build as _cb

    conditions = [
        {'key': k, 'name': k.capitalize(), 'desc': _cos.CONDITION_INFO.get(k, ''),
         'stacks': k in _cos._VALUED}
        for k in _cos._CONDITION_KEYS
    ]
    damage_types = [{'type': t, 'deflectable': t in _cmb.DEFLECTABLE}
                    for t in _cmb.DAMAGE_TYPES]
    injury_rows = [
        ('16 or higher', 'Flesh Wound', _cmb.INJURY_DURATION['flesh_wound']),
        ('6 to 15',      'Shallow',     _cmb.INJURY_DURATION['shallow']),
        ('1 to 5',       'Vicious',     _cmb.INJURY_DURATION['vicious']),
        ('-5 to 0',      'Permanent',   _cmb.INJURY_DURATION['permanent']),
        ('-6 or lower',  'Death',       _cmb.INJURY_DURATION['death']),
    ]
    surges = [dict(code=c, **v) for c, v in _rad.SURGES.items()]
    orders = [dict(key=k, color=_rad.order_color(k),
                   surge_names=[_rad.surge_name(s) for s in v['surges']], **v)
              for k, v in _rad.RADIANT_ORDERS.items()]
    skills = [{'name': _cos.SKILL_NAMES[c], 'attr': _cos.ATTR_NAMES[_cos.SKILL_ATTR[c]]}
              for c in sorted(_cos.BASIC_SKILLS, key=lambda c: _cos.SKILL_NAMES[c])]
    return render_template(
        'cosmere_gmscreen.html',
        conditions=conditions, damage_types=damage_types,
        injury_rows=injury_rows, injury_d8=sorted(_cmb.INJURY_EFFECTS_D8.items()),
        plot_spend=_cmb.PLOT_DIE_SPEND,
        surges=surges, surge_scaling=sorted(_rad.SURGE_SCALING.items()),
        stormlight=_rad.STORMLIGHT_ACTIONS, first_ideal=_rad.FIRST_IDEAL,
        orders=orders, skills=skills,
        skill_rank_by_tier=[_cb.max_skill_rank(lv) for lv in (1, 6, 11, 16, 21)],
    )


@app.route('/cosmere/generator')
@gm_required
def cosmere_generator():
    """Cosmere GM generators -- Rosharan names, NPCs, highstorms/weather, spheres,
    loot & fabrials, locations, plot hooks, rumors, and scene dressing. Mirrors
    the PF2e /generator page (initial cards + per-card reroll via the API below).
    System-aware: redirects out in non-Cosmere mode, like the dashboard."""
    if _active_system() != 'cosmere':
        return redirect(_active_system_ui().gm_home)
    import systems.cosmere.generator as _gen
    cards = [{'type': t, 'label': lbl, 'html': fn()}
             for t, (lbl, fn) in _gen.GENERATORS.items()]
    return render_template('cosmere_generator.html', cards=cards)


@app.route('/api/cosmere/generate/<gtype>', methods=['POST'])
@gm_required
def api_cosmere_generate(gtype):
    """Reroll a single Cosmere generator card -> {html}."""
    import systems.cosmere.generator as _gen
    if gtype not in _gen.GENERATORS:
        return jsonify({'error': 'unknown generator'}), 400
    return jsonify({'html': _gen.generate(gtype)})


@app.route('/cosmere/pcs')
def cosmere_pcs():
    import systems.cosmere.build as _cb
    import systems.cosmere.radiant as _rad
    cards = []
    _hb_store = _cosmere_homebrew_store()
    for d in _list_cosmere_pcs():
        b = _cb.CosmereBuild(d.get('build'), homebrew=_hb_store)
        o = b.order()
        cards.append({
            'id': d['id'], 'name': d.get('name', 'Unknown'), 'level': b.level, 'tier': b.tier,
            'path': b.path, 'defenses': b.defenses(), 'health': b.health_max(),
            'deflect': b.deflect_value(), 'investiture': b.investiture_max(),
            'is_radiant': b.is_radiant,
            'order': o['name'] if o else '', 'spren': b.spren_name or (o['spren'] if o else ''),
            'surges': [_rad.surge_name(s) for s in o['surges']] if (o and b.first_ideal_sworn) else [],
            'accent': _rad.order_color(b.radiant_order) if b.is_radiant else _rad.DEFAULT_ACCENT,
        })
    cards.sort(key=lambda c: c['name'].lower())
    return render_template('cosmere_pcs.html', pcs=cards,
                           is_gm=_is_gm(), active_cid=ACTIVE_CAMPAIGN_ID)


@app.route('/cosmere/gm/vitals')
@gm_required
def cosmere_gm_vitals():
    """GM-only at-a-glance board of every Cosmere PC's LIVE vitals (Health /
    Focus / Investiture + conditions + injuries), updating in real time from the
    cosmere_player_state SSE as players adjust their sheets."""
    party = _cosmere_status_party(_list_cosmere_pcs())
    party.sort(key=lambda r: r['name'].lower())
    return render_template('cosmere_gm_vitals.html', party=party)


@app.route('/cosmere/pc/import_pdf', methods=['POST'])
@_auth.login_required
def cosmere_import_pdf():
    """Create Cosmere PC(s) from uploaded filled character-sheet PDF(s). Parses
    the AcroForm fields, maps them to a CosmereBuild (computing stat_bonuses so
    the sheet's authoritative totals are reproduced exactly), and saves a PC into
    the active Cosmere campaign. A GM leaves them UNCLAIMED (players claim via a
    join link); a player importing their own owns it."""
    if _active_system() != 'cosmere':
        return jsonify({'ok': False, 'error': 'Switch to a Cosmere campaign first, then import.'}), 400
    files = request.files.getlist('file') or request.files.getlist('files')
    if not files and 'file' in request.files:
        files = [request.files['file']]
    files = [f for f in files if f and f.filename]
    if not files:
        return jsonify({'ok': False, 'error': 'No PDF uploaded.'}), 400
    import systems.cosmere.build as _cb
    from systems.cosmere.pdf_import import build_from_pdf
    created, errors = [], []
    for f in files:
        try:
            data = f.read()
            if not data or not data[:5].startswith(b'%PDF'):
                errors.append({'file': f.filename, 'error': 'Not a PDF file.'})
                continue
            build, play_state, extras = build_from_pdf(data, homebrew=_cosmere_homebrew_store())
            # Normalize through CosmereBuild so the stored build round-trips (and
            # the in-app builder/leveler can later edit it). to_dict carries the
            # stat_bonuses overrides.
            cb = _cb.CosmereBuild(build, homebrew=_cosmere_homebrew_store())
            doc = {
                'id': uuid.uuid4().hex, 'system': 'cosmere',
                'name': cb.name or 'Imported Hero',
                'owner_user_id': (None if _is_gm() else session.get('user_id')),
                'build': cb.to_dict(),
                'play_state': play_state,
                'imported_from': 'pdf',
                'import_extras': {k: extras.get(k) for k in ('weapons', 'equipment', 'spheres')},
            }
            _save_cosmere_pc(doc)
            created.append({'id': doc['id'], 'name': doc['name'],
                            'url': url_for('cosmere_pc_sheet', pid=doc['id'])})
        except Exception as e:
            print(f'[PDF IMPORT] {getattr(f, "filename", "?")}: {e}')
            errors.append({'file': getattr(f, 'filename', '?'), 'error': str(e)[:200]})
    return jsonify({'ok': bool(created), 'created': created, 'errors': errors})


@app.route('/cosmere/builder', methods=['GET', 'POST'])
def cosmere_builder():
    import systems.cosmere.build as _cb
    _hb_store = _cosmere_homebrew_store()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        build = _cb.CosmereBuild(data.get('build') or data, homebrew=_hb_store)
        issues = build.validate()
        # Rules enforcement: a build that OVER-applies the rules (over a cap or
        # budget, OR a talent without its prerequisite) cannot be saved by a
        # player. The GM may override with force=true (their builder shows the
        # toggle). Soft guidance (under-spend, missing key talent) never blocks --
        # only hard_violations() do.
        hard = build.hard_violations()
        if hard and not (bool(data.get('force')) and _is_gm()):
            return jsonify({'ok': False, 'blocked': True, 'hard': hard, 'issues': issues,
                            'error': 'This build exceeds the rules. Fix the flagged limits to save.'}), 400
        existing = _load_cosmere_pc(data.get('id')) if data.get('id') else None
        if existing is not None:
            owner = existing.get('owner_user_id')                  # preserve owner on edit / level-up
        else:
            # A GM building the party leaves PCs UNCLAIMED (assignable via a join
            # link); a player building their own character owns it immediately.
            owner = None if _is_gm() else session.get('user_id')
        doc = {
            'id': (existing or {}).get('id') or uuid.uuid4().hex,
            'system': 'cosmere', 'name': build.name,
            'owner_user_id': owner,
            'build': build.to_dict(),
        }
        if existing and existing.get('campaign_id'):
            doc['campaign_id'] = existing['campaign_id']
        # Carry forward live state the builder rebuild would otherwise drop:
        # the GM-awarded wallet (Tier 3) and the player's play_state (HP /
        # conditions / focus). A level-up resets HP to max on next sheet load
        # anyway, but silently wiping awarded spheres/items is data loss.
        if existing and isinstance(existing.get('wallet'), dict):
            doc['wallet'] = existing['wallet']
        if existing and isinstance(existing.get('play_state'), dict):
            doc['play_state'] = existing['play_state']
        # Cosmetic Mistborn "house metal" (theming) — purely visual, preserved
        # across rebuilds. Accept it from the POST or carry the existing value.
        import systems.cosmere.lore as _lore
        _hm = (data.get('house_metal') or (existing or {}).get('house_metal') or '').lower()
        if _hm in _lore.METALS:
            doc['house_metal'] = _hm
        _save_cosmere_pc(doc)
        return jsonify({'ok': True, 'id': doc['id'], 'issues': issues,
                        'url': url_for('cosmere_pc_sheet', pid=doc['id'])})
    # GET — new build, or edit/level an existing one
    existing = _load_cosmere_pc(request.args.get('pc')) if request.args.get('pc') else None
    build = (_cb.CosmereBuild((existing or {}).get('build'), homebrew=_hb_store)
             if existing else _cb.CosmereBuild(homebrew=_hb_store))
    if request.args.get('levelup') and existing:
        build.level += 1     # pre-bump for the leveler flow; the player allocates, then saves
    ctx = _cosmere_builder_context(build, _hb_store)
    return render_template('cosmere_builder.html', pid=(existing or {}).get('id', ''), **ctx)


@app.route('/cosmere/builder/preview', methods=['POST'])
def cosmere_builder_preview():
    """Live 'character so far' for the walkthrough builder: the ENGINE's derived
    stats + budgets + guided validation for a partial build, so the wizard panel
    never drifts from the rules. Read-only."""
    import systems.cosmere.build as _cb
    data = request.get_json(silent=True) or {}
    b = _cb.CosmereBuild(data.get('build') or data, homebrew=_cosmere_homebrew_store())
    return jsonify({
        'defenses': b.defenses(),
        'health': b.health_max(), 'focus': b.focus_max(),
        'investiture': b.investiture_max(), 'deflect': b.deflect_value(),
        'derived': b.derived_stats(),          # move / senses / lift / recovery die (Ch.3)
        'tier': b.tier, 'level': b.level, 'is_radiant': b.is_radiant,
        'budgets': {
            'attr': [b.attr_points_spent(), b.attr_points_available()],
            'skills': [b.skill_ranks_spent(), b.skill_ranks_available()],
            'talents': [len(b.talents), b.talents_available()],
            'expertises': [len(b.expertises), b.expertises_available()],
        },
        'issues': b.validate(),
        'hard': b.hard_violations(),          # over-cap / over-budget breaks that block a player's save
        'is_gm': _is_gm(),                     # the GM's builder may override the block
        'homebrew': sorted(set(b.homebrew_sources)),
        'homebrew_dangling': b.homebrew_dangling,
        'infected': [a.get('name') for a in b.infected_records()],
    })


@app.route('/cosmere/homebrew')
@gm_required
def cosmere_homebrew():
    """Per-campaign homebrew shelf: author custom talents / items / ancestries /
    cultures / heroic + Radiant paths / surges in the SAME schema as canon. Each
    entry carries structured stat bonuses (applied by the engine) plus free-form
    notes, and shows in the builder pickers with a 'Homebrew' badge. GM-only,
    Cosmere mode (redirects out otherwise, like the dashboard / GM screen)."""
    if _active_system() != 'cosmere':
        return redirect(_active_system_ui().gm_home)
    import systems.cosmere.homebrew as _hb
    import systems.cosmere.radiant as _rad
    store = _cosmere_homebrew_store()
    return render_template(
        'cosmere_homebrew.html',
        store=store, types=list(_hb.TYPES), type_labels=_hb.TYPE_LABELS,
        targets=_hb.effect_targets(),
        counts={t: len(store.get(t, [])) for t in _hb.TYPES},
        skill_names=systems.cosmere.SKILL_NAMES, skill_attr=systems.cosmere.SKILL_ATTR,
        paths=list(systems.cosmere.PATHS),
        surge_names=_rad.SURGES,
        order_keys=list(_rad.RADIANT_ORDERS),
        active_cid=ACTIVE_CAMPAIGN_ID,
    )


@app.route('/cosmere/homebrew/save', methods=['POST'])
@gm_required
def cosmere_homebrew_save():
    """Upsert a homebrew entry into the active campaign's shelf (by id)."""
    if _active_system() != 'cosmere':
        return jsonify({'ok': False, 'error': 'not a Cosmere campaign'}), 400
    import systems.cosmere.homebrew as _hb
    data = request.get_json(silent=True) or {}
    entry = _hb.normalize(data.get('entry') or data)
    t = entry['type']
    raw = _load_homebrew_raw()
    bucket = [e for e in (raw.get(t) or []) if isinstance(e, dict) and e.get('id') != entry['id']]
    bucket.append(entry)
    raw[t] = bucket
    _save_homebrew_raw(raw)
    return jsonify({'ok': True, 'id': entry['id'], 'entry': entry})


@app.route('/cosmere/homebrew/<eid>/delete', methods=['POST'])
@gm_required
def cosmere_homebrew_delete(eid):
    """Remove a homebrew entry by id (searches every type bucket)."""
    if _active_system() != 'cosmere':
        return jsonify({'ok': False}), 400
    import systems.cosmere.homebrew as _hb
    raw = _load_homebrew_raw()
    removed = False
    for t in _hb.TYPES:
        b = raw.get(t)
        if isinstance(b, list):
            nb = [e for e in b if not (isinstance(e, dict) and e.get('id') == eid)]
            removed = removed or len(nb) != len(b)
            raw[t] = nb
    _save_homebrew_raw(raw)
    return jsonify({'ok': removed})


@app.route('/cosmere/pc/<pid>')
def cosmere_pc_sheet(pid):
    import systems.cosmere.build as _cb
    doc = _load_cosmere_pc(pid)
    if not doc:
        return ('Unknown Cosmere character', 404)
    build = _cb.CosmereBuild(doc.get('build'), homebrew=_cosmere_homebrew_store())
    actor = systems.cosmere.CosmereActor(build.to_actor_doc())
    _u = _auth.current_user()
    # The owner (or the GM) gets the INTERACTIVE sheet: tap-to-roll skills/strikes
    # + live Health/Focus/Investiture steppers. Everyone else sees it read-only.
    interactive = (bool(_u) and doc.get('owner_user_id') == _u.get('id')) or _is_gm()
    can_delete = interactive
    ps = doc.get('play_state') if isinstance(doc.get('play_state'), dict) else {}
    def _ps(key, default):
        try:
            return int(ps[key])
        except (KeyError, TypeError, ValueError):
            return int(default)
    cur = {
        'health': _ps('health', actor.health_max),
        'focus': _ps('focus', actor.focus_max),
        'investiture': _ps('investiture', actor.investiture_max),
        'injuries': _ps('injuries', 0),
        'enhanced': bool(ps.get('enhanced')),         # the Stormlight Enhance toggle
        'conditions': ps.get('conditions') if isinstance(ps.get('conditions'), dict) else {},
        'shardblade': bool(ps.get('shardblade')),      # spren summoned as a Shardblade (3rd Ideal)
        'shardplate': bool(ps.get('shardplate')),      # living Shardplate donned (4th Ideal)
        'squire': str(ps.get('squire') or ''),         # Take Squire: who they've taken under their wing
        'forsaken': bool(ps.get('forsaken')),          # oaths forsaken — spren withdrawn (GM-toggleable)
        'goals': ps.get('goals') if isinstance(ps.get('goals'), list) else [],  # Ch.8 goals + rewards
    }
    # Radiant (Phase 2): the 3 Stormlight actions + the order's castable surge powers.
    import systems.cosmere.radiant_talents as _rt
    _order = build.order()
    radiant_powers = [dict(_rt.SURGE_POWERS[c], code=c)
                      for c in (_order['surges'] if _order else ()) if c in _rt.SURGE_POWERS]
    # Ideal payoffs (RAW): the Third Ideal lets a Radiant summon their spren as a
    # Shardblade (2d8 spirit, deadly — bypasses Deflect); Take Squire and Wound
    # Regeneration are talents whose effects surface as sheet actions once taken.
    _tnames = [(t.get('name') or '') for t in (build.to_dict().get('talents') or [])]
    shardblade = None
    if build.is_radiant and build.ideals_sworn >= 3:
        shardblade = {'name': 'Shardblade', 'damage': '2d8', 'type': 'spirit',
                      'mod': (actor.skills.get('hwp') or {}).get('mod', 0)}
    # Living Shardplate (RAW: Fourth Ideal+) — Deflect 5 against EVERY damage type
    # (even spirit/vital, which normally bypass Deflect). Don/doff toggle.
    shardplate = {'deflect': 5} if (build.is_radiant and build.ideals_sworn >= 4) else None
    has_take_squire = any(n.startswith('Take Squire') for n in _tnames)
    has_wound_regen = any(n == 'Wound Regeneration' for n in _tnames)
    radiant_variant = systems.cosmere.radiant.variants(build.radiant_order).get(build.radiant_variant)  # {name,desc} or None
    # Equipped Fabrials (Ch.7) + their live charge counts (default = full).
    _fab_state = ps.get('fabrials') if isinstance(ps.get('fabrials'), dict) else {}
    fabrials = []
    for _fid in (build.to_dict().get('fabrials') or []):
        _f = systems.cosmere.items.fabrial(_fid)
        if _f:
            try:
                _cur = int(_fab_state.get(_fid, _f['charges']))
            except (TypeError, ValueError):
                _cur = _f['charges']
            fabrials.append(dict(_f, current=max(0, min(_f['charges'], _cur))))
    # Fabrial Workshop (Ch.7 "Inventing Unique Fabrials"): the crafting rules +
    # the skill mods the in-app rolls need, plus any fabrials this PC has crafted.
    import systems.cosmere.fabrial_crafting as _fc
    crafting = {
        'effects': _fc.EFFECTS, 'tier_cost': _fc.TIER_COST, 'trap_dc': _fc.TRAP_DC,
        'bands': _fc.CRAFT_BANDS, 'general_up': _fc.GENERAL_UPGRADES,
        'general_dn': _fc.GENERAL_DRAWBACKS, 'advanced': _fc.ADVANCED_FEATURES,
        'lore_mod': (actor.skills.get('lor') or {}).get('mod', 0),
        'crafting_mod': (actor.skills.get('cra') or {}).get('mod', 0),
    }
    crafted = []
    for _c in (ps.get('crafted_fabrials') or []):
        if isinstance(_c, dict) and _c.get('name'):
            mx = int(_c.get('charges') or 0)
            crafted.append(dict(_c, charges=mx, current=max(0, min(mx, int(_c.get('current', mx))))))
    # Per-character name crest + secondary accent (theming PR-B). In a Mistborn
    # campaign a chosen cosmetic "house metal" wins; otherwise the Radiant order
    # (the character's actual mechanic) drives the crest + its accent color.
    import systems.cosmere.lore as _lore
    _world = _cosmere_world()
    _hm = (doc.get('house_metal') or '').lower()
    if _world == 'mistborn' and _hm in _lore.METALS:
        crest_glyph, crest_color = 'cg-metal-' + _hm, _lore.metal_tint(_hm)
    elif build.radiant_order:
        crest_glyph, crest_color = 'cg-order-' + build.radiant_order, systems.cosmere.radiant.order_color(build.radiant_order)
    else:
        crest_glyph, crest_color = '', ''
    return render_template(
        'cosmere_sheet.html', a=actor.to_summary(), actor_id=pid, can_delete=can_delete,
        crest_glyph=crest_glyph, crest_color=crest_color, house_metal=_hm,
        ideal_states=build.ideal_states(), derived=build.derived_stats(),
        interactive=interactive, cur=cur, tier=actor.tier,
        ready_to_level=bool(doc.get('ready_to_level')),
        stormlight_actions=systems.cosmere.radiant.STORMLIGHT_ACTIONS,
        radiant_powers=radiant_powers,
        conditions=systems.cosmere.CONDITION_INFO,
        actions=actor.actions, strikes=actor.strikes, traits=actor.traits,
        skill_names=build.eff_skill_names(),
        attr_names=systems.cosmere.ATTR_NAMES,
        defense_names=systems.cosmere.DEFENSE_NAMES,
        pc=True, build=build.to_dict(), inventory=build.inventory.resolved(),
        wallet=doc.get('wallet') if isinstance(doc.get('wallet'), dict) else None,
        infected=build.infected_records(),           # selected Infected Arts (cost + abilities)
        warnings=build.validate(), edit_url=url_for('cosmere_builder', pc=pid),
        radiant=build.order(),                       # canon OR homebrew order
        first_ideal=systems.cosmere.radiant.FIRST_IDEAL,
        surge_names=build.eff_surge_names(),          # canon + homebrew surge names
        singer_form=systems.cosmere.origins.singer_form(build.singer_form),
        shardblade=shardblade, shardplate=shardplate,
        has_take_squire=has_take_squire, has_wound_regen=has_wound_regen,
        radiant_variant=radiant_variant, fabrials=fabrials,
        crafting=crafting, crafted=crafted,
    )


def _cosmere_player_card(doc):
    """A rich, at-a-glance hero card for the Cosmere player hub: the stat block
    (defenses / deflect / health / focus / investiture), the six attributes, and
    the Radiant summary (order / spren / ideals / surges). Mirrors the stat-block
    cards used elsewhere; the full sheet carries skills + abilities."""
    import systems.cosmere.build as _cb
    import systems.cosmere.radiant as _rad
    b = _cb.CosmereBuild(doc.get('build'), homebrew=_cosmere_homebrew_store())
    o = b.order()
    return {
        'id': doc['id'], 'name': doc.get('name', 'Unknown'),
        'ancestry': b.ancestry, 'culture': b.culture, 'path': b.path,
        'level': b.level, 'tier': b.tier,
        'attributes': b.eff_attributes(),
        'defenses': b.defenses(), 'deflect': b.deflect_value(),
        'health': b.health_max(), 'focus': b.focus_max(), 'investiture': b.investiture_max(),
        'is_radiant': b.is_radiant,
        'order': o['name'] if o else '', 'spren': b.spren_name or (o['spren'] if o else ''),
        'ideals': b.ideals_sworn, 'first_ideal': b.first_ideal_sworn,
        'surges': [_rad.surge_name(s) for s in o['surges']] if (o and b.first_ideal_sworn) else [],
        'accent': _rad.order_color(b.radiant_order) if b.is_radiant else _rad.DEFAULT_ACCENT,
    }


@app.route('/cosmere/player')
def cosmere_player_hub():
    """A Cosmere-native player landing: the player's own character(s) in the
    active campaign, with quick links to the full sheet, level-up, live combat,
    and notes. The global dice + Plot Die widget rides along from base.html. No
    PF2e bleed -- this is the Cosmere sibling of /player."""
    u = _auth.current_user() if _account_mode() else None
    all_pcs = _list_cosmere_pcs()
    mine = [d for d in all_pcs if u and d.get('owner_user_id') == u.get('id')] if u else []
    # Fall back to the campaign membership's assigned character if ownership
    # wasn't stamped (e.g. a GM-built PC handed off without a claim).
    if u and not mine:
        camp = _active_campaign_doc()
        cid_char = None
        for m in (camp or {}).get('members', []):
            if m.get('user_id') == u.get('id'):
                cid_char = m.get('character_id')
                break
        if cid_char:
            mine = [d for d in all_pcs if d.get('id') == cid_char]
    cards = [_cosmere_player_card(d) for d in mine]
    # Reference iconography (theming PR-C): the ten Radiant orders (with colors +
    # surges) for the Stormlight skin, and the 16-metal Allomantic table for the
    # Mistborn skin. The template shows whichever matches the campaign's world.
    import systems.cosmere.radiant as _rad
    import systems.cosmere.lore as _lore
    _all_orders = dict(_rad.RADIANT_ORDERS); _all_orders['bondsmiths'] = _rad.BONDSMITHS
    order_ref = [{
        'key': k, 'name': o['name'],
        'color': _rad.ORDER_COLORS.get(k, '#cbb46a'),
        'surges': ' + '.join(_rad.surge_name(s) for s in o['surges']),
    } for k, o in _all_orders.items()]
    return render_template(
        'cosmere_player.html', cards=cards, has_pc=bool(cards),
        roster_count=len(all_pcs), is_gm=_is_gm(),
        order_ref=order_ref, metal_families=_lore.metals_by_family(),
        attr_names=systems.cosmere.ATTR_NAMES, defense_names=systems.cosmere.DEFENSE_NAMES,
    )


@app.route('/cosmere/combat')
def cosmere_combat_view():
    """A player-facing combat view: the whole turn order, the round, and who's
    up -- the Cosmere sibling of /player's encounter view. Live via the
    encounter_update SSE; data comes from /api/cosmere/combat_state (which masks
    hidden adversaries for players). The nav's 'Combat' tab points here."""
    u = _auth.current_user() if _account_mode() else None
    my_name = ''
    if u:
        for d in _list_cosmere_pcs():
            if d.get('owner_user_id') == u.get('id'):
                my_name = d.get('name') or ''
                break
    return render_template('cosmere_combat.html', my_name=my_name,
                           cond_info=systems.cosmere.CONDITION_INFO)


@app.route('/api/cosmere/combat_state')
def api_cosmere_combat_state():
    """Player-safe snapshot of the encounter order for the Cosmere combat view.
    Hidden adversaries are masked for non-GM viewers (name '???', no stats); the
    active banner is scrubbed when the active combatant is hidden. PC health is
    shown (the party already sees each other); adversary exact HP is withheld --
    players get tier/role + conditions, not a number to meta-game against."""
    gm = _is_gm()
    with ENCOUNTER_LOCK:
        active_c = ACTIVE_ENCOUNTER[TURN_INDEX] if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
        active_visible = getattr(active_c, 'visible_to_players', True) if active_c else True
        order = []
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            if not (gm or getattr(c, 'visible_to_players', True)):
                order.append({'name': '???', 'is_pc': False, 'is_active': (i == TURN_INDEX), 'hidden': True})
                continue
            is_pc = bool(getattr(c, 'is_pc', False))
            cb = c.tracker_block() if (getattr(c, 'system', 'pf2e') == 'cosmere' and hasattr(c, 'tracker_block')) else None
            order.append({
                'name': c.name,
                'is_pc': is_pc,
                'is_active': (i == TURN_INDEX),
                'initiative': int(getattr(c, 'initiative', 0) or 0),
                'speed_choice': getattr(c, 'speed_choice', 'slow'),
                'max_actions': int(getattr(c, 'max_actions', 3) or 3),
                'injuries': int(getattr(c, 'injuries', 0) or 0),
                'conditions': [k for k, v in (getattr(c, 'conditions', {}) or {}).items() if v],
                'health': (cb['health'] if (cb and is_pc) else None),   # PCs only
                'tier': getattr(c, 'tier', None),
                'role': getattr(c, 'role', None),
            })
    return jsonify({
        'ok': True,
        'in_encounter': bool(order),
        'round': ROUND_NUMBER,
        'mode': _cosmere_initiative_mode(),
        'active_name': (active_c.name if active_c else '') if (gm or active_visible) else '???',
        'order': order,
    })


# ── Session notes: a per-owner, per-campaign free-form scratchpad ──────────
# System-agnostic: a place for any player (or the GM) to jot down whatever they
# want during a session. Stored next to the campaign's journals.
def _session_notes_dir():
    return os.path.join(os.path.dirname(JOURNAL_DIR), 'session_notes')


def _notes_owner():
    """A safe storage key for the current player/GM."""
    raw = session.get('user_id') or session.get('player_name') or 'gm'
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(raw))[:64] or 'gm'


def _notes_path(owner):
    return os.path.join(_session_notes_dir(), owner + '.json')


def _load_notes_text(owner):
    p = _notes_path(owner)
    if os.path.isfile(p):
        try:
            with open(p, encoding='utf-8') as f:
                return json.load(f).get('text', '')
        except Exception:
            pass
    return ''


def _save_notes_text(owner, text):
    os.makedirs(_session_notes_dir(), exist_ok=True)
    _atomic_write_json(_notes_path(owner), {'text': (text or '')[:20000]}, indent=2)


@app.route('/notes')
def session_notes_page():
    return render_template('notes.html', notes=_load_notes_text(_notes_owner()))


@app.route('/api/notes', methods=['GET', 'POST'])
def api_notes():
    owner = _notes_owner()
    if request.method == 'POST':
        text = (request.get_json(silent=True) or {}).get('text', '')
        _save_notes_text(owner, text if isinstance(text, str) else '')
        return jsonify({'ok': True})
    return jsonify({'text': _load_notes_text(owner)})


@app.route('/cosmere/pc/<pid>/notes', methods=['POST'])
def cosmere_pc_notes(pid):
    doc = _load_cosmere_pc(pid)
    if not doc:
        return ('Unknown Cosmere character', 404)
    text = (request.get_json(silent=True) or {}).get('text', '')
    b = doc.get('build') or {}
    b['notes'] = (text if isinstance(text, str) else '')[:20000]
    doc['build'] = b
    _save_cosmere_pc(doc)
    return jsonify({'ok': True})


def _cosmere_can_act_on(doc):
    """The PC's owner, or the GM, may roll/spend on it."""
    u = _auth.current_user()
    return _is_gm() or (bool(u) and doc.get('owner_user_id') == u.get('id'))


def _sync_cosmere_combatant_state(name, ps):
    """Mirror a Cosmere PC's saved play_state onto its LIVE tracker combatant so
    the GM screen reflects player-side changes. The combatant in ACTIVE_ENCOUNTER
    is a separate object from the saved doc (loaded when added to the encounter),
    so without this the GM tracker shows stale HP/injuries/conditions. Matches by
    name (PC names are unique in a party). Returns True if a combatant was hit."""
    if not name:
        return False
    hit = False
    for c in ACTIVE_ENCOUNTER:
        if getattr(c, 'system', 'pf2e') == 'cosmere' and getattr(c, 'is_pc', False) and c.name == name:
            if 'health' in ps:
                try: c.current_hp = max(0, int(ps['health']))
                except (TypeError, ValueError): pass
            if 'injuries' in ps:
                try: c.injuries = max(0, int(ps['injuries']))
                except (TypeError, ValueError): pass
            if isinstance(ps.get('conditions'), dict):
                c.conditions = {k: v for k, v in ps['conditions'].items() if v}
            hit = True
    return hit


def _sync_cosmere_pc_from_combatant(c):
    """Reverse of _sync_cosmere_combatant_state: write a live Cosmere PC
    combatant's health / injuries / conditions back to its saved doc's play_state
    and broadcast cosmere_player_state, so the player's OWN sheet repaints in place
    when the GM changes them from the tracker (e.g. dealing damage). No-op for
    non-PC or non-Cosmere combatants. Matches by name (unique in a party)."""
    if not getattr(c, 'is_pc', False) or getattr(c, 'system', 'pf2e') != 'cosmere':
        return
    name = getattr(c, 'name', None)
    if not name:
        return
    # Find the PC id by name (cheap scan), then do the write under its file lock
    # so it can't clobber a concurrent player-side save of the SAME doc.
    pid = None
    for d in _list_cosmere_pcs():
        if (d.get('name') or (d.get('build') or {}).get('name')) == name:
            pid = d.get('id')
            break
    if not pid:
        return
    ps = None
    with _path_lock(_cosmere_pc_path(pid)):
        d = _load_cosmere_pc(pid)               # re-read inside the lock
        if not d:
            return
        ps = dict(d.get('play_state') or {})
        ps['health'] = max(0, int(getattr(c, 'current_hp', ps.get('health', 0)) or 0))
        ps['injuries'] = max(0, int(getattr(c, 'injuries', ps.get('injuries', 0)) or 0))
        conds = getattr(c, 'conditions', None)
        if isinstance(conds, dict):
            ps['conditions'] = {k: v for k, v in conds.items() if v}
        d['play_state'] = ps
        _save_cosmere_pc(d, fsync=False)
    try:
        sse_broadcast('cosmere_player_state', {'pid': pid, 'name': name, 'play_state': ps})
    except Exception:
        pass


@app.route('/cosmere/pc/<pid>/state', methods=['POST'])
def cosmere_pc_state(pid):
    """Persist a Cosmere PC's live resource state (current health / focus /
    investiture / injuries / conditions) from the player's own sheet, mirror it
    onto the live tracker combatant, and broadcast so the GM sees it instantly.
    Owner or GM only."""
    doc = _load_cosmere_pc(pid)
    if not doc:
        return jsonify({'ok': False, 'error': 'unknown character'}), 404
    if not _cosmere_can_act_on(doc):
        return jsonify({'ok': False, 'error': 'not your character'}), 403
    data = request.get_json(silent=True) or {}
    # Lock the load->mutate->save so a player's sheet save and the GM's tracker
    # write-back (_sync_cosmere_pc_from_combatant) to the SAME doc can't clobber
    # each other's fields (one sets health, the other injuries -> both must stick).
    with _path_lock(_cosmere_pc_path(pid)):
        doc = _load_cosmere_pc(pid) or doc       # re-read inside the lock for a fresh base
        ps = dict(doc.get('play_state') or {})
        for k in ('health', 'focus', 'investiture', 'injuries'):
            if k in data:
                try:
                    ps[k] = max(0, int(data[k]))
                except (TypeError, ValueError):
                    pass
        if 'enhanced' in data:
            ps['enhanced'] = bool(data['enhanced'])
        if 'shardblade' in data:
            ps['shardblade'] = bool(data['shardblade'])   # Shardblade summoned / dismissed
        if 'shardplate' in data:
            ps['shardplate'] = bool(data['shardplate'])   # Shardplate donned / doffed
        if 'squire' in data:
            ps['squire'] = str(data['squire'] or '')[:80]  # Take Squire roster
        if 'forsaken' in data:
            ps['forsaken'] = bool(data['forsaken'])        # oaths forsaken — spren withdrawn
        if isinstance(data.get('fabrials'), dict):         # per-fabrial charge counts {id: charges}
            clean = {}
            for k, v in data['fabrials'].items():
                try:
                    clean[str(k)] = max(0, int(v))
                except (TypeError, ValueError):
                    continue
            ps['fabrials'] = clean
        if isinstance(data.get('crafted_fabrials'), list):   # workshop-crafted unique fabrials
            cf = []
            for c in data['crafted_fabrials'][:40]:
                if not isinstance(c, dict) or not c.get('name'):
                    continue
                try:
                    mx = max(0, int(c.get('charges') or 0))
                    cf.append({'key': str(c.get('key', ''))[:40], 'name': str(c.get('name'))[:80],
                               'tier': int(c.get('tier') or 0), 'charges': mx,
                               'current': max(0, min(mx, int(c.get('current', mx)))),
                               'effect': str(c.get('effect', ''))[:400],
                               'upgrades': [str(u)[:160] for u in (c.get('upgrades') or [])][:6],
                               'drawbacks': [str(x)[:160] for x in (c.get('drawbacks') or [])][:6]})
                except (TypeError, ValueError):
                    continue
            ps['crafted_fabrials'] = cf
        if isinstance(data.get('conditions'), dict):
            ps['conditions'] = data['conditions']
        if isinstance(data.get('goals'), list):            # Ch.8 goals: 3 milestones -> reward
            goals = []
            for g in data['goals'][:12]:
                if not isinstance(g, dict) or not str(g.get('text', '')).strip():
                    continue
                ms = g.get('milestones')
                ms = [bool(x) for x in ms[:3]] if isinstance(ms, list) else []
                ms += [False] * (3 - len(ms))
                rw = g.get('reward') if isinstance(g.get('reward'), dict) else {}
                goals.append({
                    'id': str(g.get('id', ''))[:40],
                    'text': str(g.get('text'))[:240],
                    'milestones': ms[:3],
                    'concluded': bool(g.get('concluded')),
                    'reward': {'category': str(rw.get('category', ''))[:40],
                               'text': str(rw.get('text', ''))[:240]},
                })
            ps['goals'] = goals
        doc['play_state'] = ps
        _save_cosmere_pc(doc, fsync=False)
    # In-memory combatant mirror + SSE happen AFTER releasing the file lock (they
    # touch ACTIVE_ENCOUNTER, not the file). The tracker still repaints instantly.
    if _sync_cosmere_combatant_state(doc.get('name'), ps):
        _broadcast_encounter_state()
    try:
        sse_broadcast('cosmere_player_state', {'pid': pid, 'name': doc.get('name'), 'play_state': ps})
    except Exception:
        pass
    return jsonify({'ok': True, 'play_state': ps})


def _cosmere_apply_rest(doc, mode):
    """Apply a Cosmere rest (Ch.9) to a PC doc and return the new play_state.
    Long rest: health + focus to max, Exhausted reduced by 1, short-lived
    conditions cleared (an ongoing Affliction is kept for the GM to resolve).
    Short rest: heal a recovery-die roll (capped at max) and refill focus;
    conditions untouched. Investiture + injury count are left to the GM (RAW:
    Investiture refills only via Stormlight; most injuries heal over days)."""
    import systems.cosmere.build as _cb
    import systems.cosmere.combat as _cc
    b = _cb.CosmereBuild(doc.get('build') or {}, homebrew=_cosmere_homebrew_store())
    ps = dict(doc.get('play_state') or {})
    hmax, fmax = b.health_max(), b.focus_max()
    conds = dict(ps.get('conditions') or {})
    if mode == 'long':
        ps['health'] = hmax
        ps['focus'] = fmax
        ex = conds.get('exhausted')
        if isinstance(ex, bool):
            conds.pop('exhausted', None)
        elif isinstance(ex, (int, float)):
            nv = int(ex) - 1
            conds['exhausted'] = nv if nv > 0 else None
            if not conds['exhausted']:
                conds.pop('exhausted', None)
        # Clear short-lived conditions; keep Affliction (and the exhausted we
        # just reduced) so the GM still sees lingering effects.
        for k in list(conds):
            if k not in ('afflicted', 'exhausted'):
                conds.pop(k, None)
        ps['conditions'] = conds
    else:  # short rest
        wil = int((b.eff_attributes() or {}).get('wil', 0) or 0)
        heal = _cc.roll_recovery(wil)
        ps['health'] = min(hmax, int(ps.get('health', hmax) or 0) + heal)
        ps['focus'] = fmax
    return ps


@app.route('/api/cosmere/rest', methods=['POST'])
@gm_required
def cosmere_rest():
    """GM: run a Cosmere short/long rest across the campaign's PCs (or one PC by
    `pid`), writing each PC's recovered play_state through to disk and pushing it
    live to their sheet + the tracker."""
    if _active_system() != 'cosmere':
        return jsonify({'ok': False, 'error': 'not a Cosmere campaign'}), 400
    data = request.get_json(silent=True) or request.form
    mode = (data.get('mode') or 'long').strip().lower()
    if mode not in ('short', 'long'):
        mode = 'long'
    pid = data.get('pid')
    docs = _list_cosmere_pcs()
    if pid:
        docs = [d for d in docs if d.get('id') == pid]
    rested = []
    for d in docs:
        pidd = d.get('id')
        if not pidd:
            continue
        with _path_lock(_cosmere_pc_path(pidd)):
            doc = _load_cosmere_pc(pidd) or d
            ps = _cosmere_apply_rest(doc, mode)
            doc['play_state'] = ps
            _save_cosmere_pc(doc, fsync=False)
        if _sync_cosmere_combatant_state(doc.get('name'), ps):
            _broadcast_encounter_state()
        try:
            sse_broadcast('cosmere_player_state', {'pid': pidd, 'name': doc.get('name'), 'play_state': ps})
        except Exception:
            pass
        rested.append({'pid': pidd, 'name': doc.get('name'), 'play_state': ps})
    _combat_log(f"Cosmere {mode} rest — {len(rested)} character(s) recovered.", 'success')
    if _is_ajax():
        return jsonify({'ok': True, 'mode': mode, 'rested': rested})
    return redirect(url_for('status_board'))


@app.route('/cosmere/pc/<pid>/rest', methods=['POST'])
def cosmere_pc_rest(pid):
    """A PLAYER rests their OWN character from the sheet (owner or GM): short or
    long rest per the rulebook (Ch.9), via the shared _cosmere_apply_rest. Mirrors
    the GM campaign-wide rest but scoped to one PC + self-serviceable."""
    doc = _load_cosmere_pc(pid)
    if not doc:
        return jsonify({'ok': False, 'error': 'unknown character'}), 404
    if not _cosmere_can_act_on(doc):
        return jsonify({'ok': False, 'error': 'not your character'}), 403
    mode = ((request.get_json(silent=True) or request.form or {}).get('mode') or 'long').strip().lower()
    if mode not in ('short', 'long'):
        mode = 'long'
    with _path_lock(_cosmere_pc_path(pid)):
        doc = _load_cosmere_pc(pid) or doc
        ps = _cosmere_apply_rest(doc, mode)
        doc['play_state'] = ps
        _save_cosmere_pc(doc, fsync=False)
    if _sync_cosmere_combatant_state(doc.get('name'), ps):
        _broadcast_encounter_state()
    try:
        sse_broadcast('cosmere_player_state', {'pid': pid, 'name': doc.get('name'), 'play_state': ps})
    except Exception:
        pass
    _combat_log(f"{doc.get('name', 'A hero')} took a {mode} rest.", 'info')
    return jsonify({'ok': True, 'mode': mode, 'play_state': ps})


@app.route('/api/cosmere/roll', methods=['POST'])
def api_cosmere_roll():
    """Log a roll made from a Cosmere player sheet (skill test / strike) and
    broadcast it to the GM + table via the shared combat log + SSE. The dice are
    rolled client-side (d20 + mod, optional advantage + plot die); the server
    attributes the roll to the OWNED character so a player can't spoof another."""
    data = request.get_json(silent=True) or {}
    doc = _load_cosmere_pc(data.get('pid'))
    if not doc:
        return jsonify({'ok': False, 'error': 'unknown character'}), 404
    if not _cosmere_can_act_on(doc):
        return jsonify({'ok': False, 'error': 'not your character'}), 403
    # Roll visibility (player-chosen on the sheet): 'group' = the whole table,
    # 'gm' = whispered to the GM only, 'private' = never sent (the client skips
    # posting entirely, so a 'private' that reaches here is a no-op safeguard).
    vis = str(data.get('visibility', 'group')).lower()
    if vis not in ('group', 'gm', 'private'):
        vis = 'group'
    if vis == 'private':
        return jsonify({'ok': True})
    from datetime import datetime
    detail = str(data.get('detail', ''))[:200]
    if vis == 'gm':
        detail = ('[GM only] ' + detail)[:200]   # so the GM's feed shows it was a whisper
    entry = {
        'id': str(uuid.uuid4()),
        'name': doc.get('name') or 'Cosmere Hero',
        'action': str(data.get('action', 'Test'))[:80],
        'result': str(data.get('result', ''))[:48],
        'detail': detail,
        'degree': None,                       # Cosmere is meet-or-beat: no PF2e degree banner
        'time': datetime.now().strftime('%H:%M:%S'),
        'round': ROUND_NUMBER,
    }
    COMBAT_LOGS.append(entry)
    if len(COMBAT_LOGS) > 200:
        COMBAT_LOGS.pop(0)
    try:
        _bump_campaign_stat('total_rolls')
    except Exception:
        pass
    try:
        payload = {k: entry[k] for k in ('name', 'action', 'result', 'detail', 'degree', 'time')}
        # 'gm' whispers: GM subscribers get the payload; the player filter returns
        # None so it's dropped for every player connection.
        pf = (lambda _d: None) if vis == 'gm' else None
        sse_broadcast('player_roll', payload, player_filter=pf)
    except Exception:
        pass
    # Feed the session scrapbook: a nat-20 (Opportunity) / nat-1 (Complication)
    # is Cosmere's crit / fumble. The sheet stamps "(nat 20)" / "(nat 1)" into the
    # detail, which _record_crit_fumble sniffs (gated to the active party roster).
    try:
        _record_crit_fumble(entry['name'], entry['action'], entry['detail'], None)
    except Exception:
        pass
    return jsonify({'ok': True})


def _my_cosmere_combatant(pid):
    """(combatant, pc_doc) for the player's OWN Cosmere PC in the active encounter
    -- the combatant is matched by name (PC names are unique in a party). Returns
    (None, doc) if not in combat, (None, None) if the PC is unknown / not theirs."""
    doc = _load_cosmere_pc(pid)
    if not doc or not _cosmere_can_act_on(doc):
        return None, None
    name = doc.get('name')
    for c in ACTIVE_ENCOUNTER:
        if getattr(c, 'system', 'pf2e') == 'cosmere' and getattr(c, 'is_pc', False) and c.name == name:
            return c, doc
    return None, doc


@app.route('/api/cosmere/my_combat')
def api_cosmere_my_combat():
    """The player's own-character combat state for the sheet's combat strip:
    whether they're in the encounter, the round, whose turn it is, and their
    fast/slow election + initiative under the active house-rule."""
    c, doc = _my_cosmere_combatant(request.args.get('pid'))
    if doc is None:
        return jsonify({'ok': False}), 403
    if not c:
        return jsonify({'ok': True, 'in_encounter': False})
    active = ACTIVE_ENCOUNTER[TURN_INDEX] if (ACTIVE_ENCOUNTER and 0 <= TURN_INDEX < len(ACTIVE_ENCOUNTER)) else None
    return jsonify({
        'ok': True, 'in_encounter': True, 'round': ROUND_NUMBER,
        'mode': _cosmere_initiative_mode(),
        'active_name': active.name if active else '',
        'is_my_turn': bool(active and active.instance_id == c.instance_id),
        'speed_choice': getattr(c, 'speed_choice', 'slow'),
        'max_actions': getattr(c, 'max_actions', 3),
        'initiative': int(getattr(c, 'initiative', 0) or 0),
    })


@app.route('/api/cosmere/my_speed', methods=['POST'])
def api_cosmere_my_speed():
    """A player elects Fast (2 actions, acts early) or Slow (3 actions, acts late)
    for their OWN character (phases mode). Re-sorts + broadcasts."""
    data = request.get_json(silent=True) or {}
    c, doc = _my_cosmere_combatant(data.get('pid'))
    if doc is None:
        return jsonify({'ok': False}), 403
    if not c:
        return jsonify({'ok': False, 'error': 'not in combat'}), 400
    choice = (data.get('choice') or '').lower()
    if choice not in ('fast', 'slow'):
        return jsonify({'ok': False, 'error': 'choice must be fast or slow'}), 400
    c.speed_choice = choice
    c.max_actions = 2 if choice == 'fast' else 3
    _sort_encounter()
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({'ok': True, 'speed_choice': choice, 'max_actions': c.max_actions})


@app.route('/api/cosmere/my_initiative', methods=['POST'])
def api_cosmere_my_initiative():
    """A player rolls their OWN initiative (d20+Speed) for the traditional
    house-rule. Re-sorts + broadcasts + logs."""
    data = request.get_json(silent=True) or {}
    c, doc = _my_cosmere_combatant(data.get('pid'))
    if doc is None:
        return jsonify({'ok': False}), 403
    if not c:
        return jsonify({'ok': False, 'error': 'not in combat'}), 400
    d20 = random.randint(1, 20)
    spd = _cosmere_init_bonus(c)
    c.initiative = d20 + spd
    _combat_log(f"{c.name} rolled Initiative (Speed): {d20} + {spd} = {c.initiative}", 'action')
    _sort_encounter()
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({'ok': True, 'initiative': c.initiative, 'detail': f'd20({d20}) + {spd}'})


@app.route('/api/cosmere/combatant/<instance_id>/condition', methods=['POST'])
@gm_required
def api_cosmere_combatant_condition(instance_id):
    """GM applies / removes a Cosmere condition on a tracker combatant. For a
    player's PC it also writes the condition through to their saved play_state and
    pushes it to their open sheet (the `cosmere_player_state` listener), so a
    GM-applied condition shows up live on the player's character sheet."""
    data = request.get_json(silent=True) or {}
    cond = (data.get('condition') or '').strip().lower()
    action = (data.get('action') or 'toggle').lower()
    if cond not in systems.cosmere.CONDITION_INFO:
        return jsonify({'ok': False, 'error': 'unknown condition'}), 400
    target = next((c for c in ACTIVE_ENCOUNTER if c.instance_id == instance_id
                   and getattr(c, 'system', 'pf2e') == 'cosmere'), None)
    if target is None:
        return jsonify({'ok': False, 'error': 'not a Cosmere combatant'}), 404
    if not isinstance(getattr(target, 'conditions', None), dict):
        target.conditions = {}
    on = bool(target.conditions.get(cond))
    on = True if action == 'add' else (False if action == 'remove' else not on)
    if on:
        target.conditions[cond] = True
    else:
        target.conditions.pop(cond, None)
    _combat_log(f"{target.name} {'gained' if on else 'lost'} {cond.capitalize()}", 'condition')
    if on:
        try: _bump_campaign_stat('conditions_applied')
        except Exception: pass
    # If the combatant is a player's PC, persist the new set + push to their sheet.
    if getattr(target, 'is_pc', False):
        for d in _list_cosmere_pcs():
            if d.get('name') == target.name:
                ps = dict(d.get('play_state') or {})
                ps['conditions'] = dict(target.conditions)
                d['play_state'] = ps
                _save_cosmere_pc(d, fsync=False)
                try:
                    sse_broadcast('cosmere_player_state',
                                  {'pid': d.get('id'), 'name': d.get('name'), 'play_state': ps})
                except Exception:
                    pass
                break
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax():
        return _tracker_json_response()
    return jsonify({'ok': True, 'conditions': dict(target.conditions)})


@app.route('/cosmere/pc/<pid>/release', methods=['POST'])
def cosmere_pc_release(pid):
    """Hand a Cosmere character off to a player: clear its ownership so the
    invites page mints a fresh join code for it. GM of the active campaign only
    (the PC is implicitly scoped -- COSMERE_PC_DIR is the live campaign's store)."""
    if not _is_gm():
        return jsonify({'error': 'GM only'}), 403
    doc = _load_cosmere_pc(pid)
    if not doc:
        return ('Unknown Cosmere character', 404)
    doc['owner_user_id'] = None
    _save_cosmere_pc(doc)
    cid = ACTIVE_CAMPAIGN_ID or doc.get('campaign_id')
    if request.is_json:
        return jsonify({'ok': True})
    return redirect('/campaign/%s/invites' % cid if cid else '/cosmere/pcs')


@app.route('/cosmere/pc/<pid>/delete', methods=['POST'])
def cosmere_pc_delete(pid):
    """Delete a Cosmere character -- its owner, or the active campaign's GM/admin."""
    doc = _load_cosmere_pc(pid)
    if not doc:
        return ('Unknown Cosmere character', 404)
    u = _auth.current_user()
    owner = bool(u) and doc.get('owner_user_id') == u.get('id')
    if not (owner or _is_gm()):
        return jsonify({'error': 'not allowed'}), 403
    _delete_cosmere_pc(pid)
    if request.is_json:
        return jsonify({'ok': True})
    return redirect('/cosmere/pcs')


@app.route('/api/plot_die', methods=['POST'])
def api_plot_die():
    """Roll the Cosmere Plot Die — a d6 side-channel when the stakes are raised."""
    import systems.cosmere.combat as _cc
    r = _cc.roll_plot_die_full()
    who = session.get('player_name') or ('GM' if _is_gm() else 'Someone')
    sev = 'critical' if r['type'] == 'complication' else 'success'
    try:
        _combat_log(f"{who} rolled the Plot Die — {r['label']}.", sev)
    except Exception:
        pass
    return jsonify(r)


@app.route('/api/add_combatant', methods=['POST'])
def add_combatant():
    c_type = request.form.get('type') or (request.json or {}).get('type')
    path = request.form.get('path') or (request.json or {}).get('path')
    if c_type == 'monster' and path in MONSTER_LIBRARY:
        new_c = copy.deepcopy(MONSTER_LIBRARY[path])
        new_c.instance_id = str(uuid.uuid4())
        ACTIVE_ENCOUNTER.append(new_c)
    elif c_type == 'pc' and path in PARTY_LIBRARY:
        new_c = copy.deepcopy(PARTY_LIBRARY[path])
        new_c.instance_id = str(uuid.uuid4())
        ACTIVE_ENCOUNTER.append(new_c)
    elif c_type == 'cosmere' and path:
        new_c = _cosmere_combatant(path)
        if new_c is not None:
            new_c.instance_id = str(uuid.uuid4())
            ACTIVE_ENCOUNTER.append(new_c)
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/add_party', methods=['POST'])
def add_party():
    for pc_name, pc_data in PARTY_LIBRARY.items():
        new_c = copy.deepcopy(pc_data)
        new_c.instance_id = str(uuid.uuid4())
        ACTIVE_ENCOUNTER.append(new_c)
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/cosmere/add_party', methods=['POST'])
def add_cosmere_party():
    """Add every Cosmere PC in the active campaign to the encounter."""
    for d in _list_cosmere_pcs():
        new_c = _cosmere_combatant(d.get('id'))
        if new_c is not None:
            new_c.instance_id = str(uuid.uuid4())
            ACTIVE_ENCOUNTER.append(new_c)
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/cosmere/add_custom_adversary', methods=['POST'])
@gm_required
def add_cosmere_custom_adversary():
    """GM: stat a custom Cosmere adversary from a quick form, persist it to the
    campaign's homebrew adversary store (reusable + survives a restart), and add
    it to the live encounter. The Cosmere sibling of /api/add_custom_monster."""
    if _active_system() != 'cosmere':
        return jsonify({'success': False, 'error': 'cosmere only'}), 400
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    doc = _build_cosmere_adversary_doc(data)
    _save_cosmere_custom_adversary(doc)
    new_c = _cosmere_combatant(doc['_id'])
    if new_c is None:
        return jsonify({'success': False, 'error': 'failed to build adversary'}), 500
    new_c.instance_id = str(uuid.uuid4())
    ACTIVE_ENCOUNTER.append(new_c)
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({'success': True, 'name': doc['name'], 'id': doc['_id'],
                    'instance_id': new_c.instance_id})

@app.route('/api/remove_combatant/<instance_id>', methods=['POST'])
def remove_combatant(instance_id):
    global ACTIVE_ENCOUNTER, TURN_INDEX
    ACTIVE_ENCOUNTER = [c for c in ACTIVE_ENCOUNTER if c.instance_id != instance_id]
    if len(ACTIVE_ENCOUNTER) > 0 and TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = len(ACTIVE_ENCOUNTER) - 1
    elif len(ACTIVE_ENCOUNTER) == 0: TURN_INDEX = 0
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/toggle_combatant_visibility/<instance_id>', methods=['POST'])
@require_live_combatant
def toggle_combatant_visibility(instance_id):
    """GM-only: flip whether a combatant is visible to player SSE feeds.

    Also syncs the matching map token (if one exists) so the map and the
    tracker agree on who the players can see. Returns JSON with the new
    state for optimistic-UI callers on the tracker page.
    """
    data = request.get_json(silent=True) or {}
    target = None
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                target = c
                break
        if target is None:
            return jsonify({'success': False, 'error': 'Combatant not found'}), 404
        # Accept an explicit 'visible' boolean, or flip the current state.
        prev_vis = getattr(target, 'visible_to_players', True)
        if 'visible' in data:
            new_vis = bool(data['visible'])
        else:
            new_vis = not getattr(target, 'visible_to_players', True)
        # PCs are always visible. Silently no-op rather than error so a
        # mis-click on a PC row doesn't confuse the GM mid-fight.
        if target.is_pc:
            new_vis = True
        target.visible_to_players = new_vis
        # Auto boss-reveal (Chunk 4d): a hidden→visible flip on a non-PC that
        # carries an epithet fires the cinematic name card for everyone.
        # Snapshot the fields under the lock; broadcast after we release it.
        reveal_name = target.name
        reveal_epithet = (getattr(target, 'epithet', '') or '').strip()
        reveal_level = getattr(target, 'level', None)
        did_reveal = (not prev_vis) and new_vis and (not target.is_pc) and bool(reveal_epithet)
    _persist_encounter_state()
    _broadcast_encounter_state()
    _combat_log(
        f"{target.name} is now {'visible to' if new_vis else 'hidden from'} players",
        'system'
    )
    if did_reveal:
        _broadcast_boss_reveal(reveal_name, reveal_epithet, reveal_level)
    if _is_ajax() or request.is_json:
        return jsonify({'success': True, 'instance_id': instance_id, 'visible_to_players': new_vis})
    return redirect(url_for('tracker_view'))


def _broadcast_boss_reveal(name, epithet, level=None):
    """Fire the cinematic boss-reveal card (Chunk 4d) to every screen.

    Sent unfiltered on purpose: the creature is being revealed at this very
    moment, so exposing its name + title to players is the whole point."""
    sse_broadcast('boss_reveal', {
        'name': name or '???',
        'epithet': epithet or '',
        'level': level,
        't': int(time.time()),
    })


@app.route('/api/set_combatant_epithet/<instance_id>', methods=['POST'])
@gm_required
def set_combatant_epithet(instance_id):
    """GM-only: set/clear the boss-reveal title on a combatant. Persisted so
    it survives autosave + save/load; re-broadcasts encounter state so any
    other GM screen picks up the edit."""
    data = request.get_json(silent=True) or {}
    epithet = str(data.get('epithet', '') or '').strip()[:80]
    target = None
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                target = c
                break
        if target is None:
            return jsonify({'success': False, 'error': 'Combatant not found'}), 404
        if target.is_pc:
            return jsonify({'success': False, 'error': 'PCs have no epithet'}), 400
        target.epithet = epithet
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({'success': True, 'instance_id': instance_id, 'epithet': epithet})


@app.route('/api/session/boss_reveal/<instance_id>', methods=['POST'])
@gm_required
def api_session_boss_reveal(instance_id):
    """GM-only manual trigger: reveal a combatant and fire its name card on
    every screen. Marks the creature visible (so its name unmasks in the
    feeds) and broadcasts the cinematic card even if it was already visible
    — handy for re-showing the card or revealing a creature with no epithet
    (the card then shows just the name)."""
    target = None
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                target = c
                break
        if target is None:
            return jsonify({'success': False, 'error': 'Combatant not found'}), 404
        if not target.is_pc:
            target.visible_to_players = True
        reveal_name = target.name
        reveal_epithet = (getattr(target, 'epithet', '') or '').strip()
        reveal_level = getattr(target, 'level', None)
    _persist_encounter_state()
    _broadcast_encounter_state()
    _broadcast_boss_reveal(reveal_name, reveal_epithet, reveal_level)
    return jsonify({'success': True, 'instance_id': instance_id})

@app.route('/api/session_timer/<action>', methods=['POST'])
@gm_required
def session_timer(action):
    """Start, stop, or reset the encounter session timer. Broadcasts via SSE
    so every client (GM + players) starts ticking in sync."""
    global SESSION_TIMER_START
    if action == 'start':
        SESSION_TIMER_START = int(time.time())
    elif action == 'stop':
        SESSION_TIMER_START = None
    elif action == 'reset':
        SESSION_TIMER_START = int(time.time())
    else:
        return jsonify({'success': False, 'error': 'action must be start|stop|reset'}), 400
    _persist_encounter_state()
    _broadcast_encounter_state()
    sse_broadcast('session_timer', {'start': SESSION_TIMER_START})
    return jsonify({'success': True, 'session_timer_start': SESSION_TIMER_START})


@app.route('/api/set_combatant_tactics/<instance_id>', methods=['POST'])
@gm_required
@require_live_combatant
def set_combatant_tactics(instance_id):
    """GM-only: set/update the per-creature tactics note. Persisted with
    encounter save/load. Hidden from players (GM view only)."""
    data = request.get_json(silent=True) or {}
    tactics_text = str(data.get('tactics', '') or '')[:2000]
    target = None
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                target = c
                break
        if target is None:
            return jsonify({'success': False, 'error': 'Combatant not found'}), 404
        target.tactics = tactics_text
    _persist_encounter_state()
    return jsonify({'success': True, 'instance_id': instance_id, 'tactics': tactics_text})


@app.route('/api/clear_encounter', methods=['POST'])
def clear_encounter():
    global TURN_INDEX, ROUND_NUMBER, ENCOUNTER_NOTES, SESSION_TIMER_START
    if ACTIVE_ENCOUNTER:
        names = [c.name for c in ACTIVE_ENCOUNTER]
        _combat_log(f"Encounter ended ({', '.join(names)})", 'system')
    ACTIVE_ENCOUNTER.clear(); TURN_INDEX = 0; ROUND_NUMBER = 1; ENCOUNTER_NOTES = ''
    SESSION_TIMER_START = None
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/combat_log')
def get_combat_log():
    """Return combat log entries.

    Players get a scrubbed copy where any hidden NPC's name is replaced
    with '???' across every user-visible field. GMs see the raw log.
    """
    entries = _scrub_log_entries_for_players(COMBAT_LOGS)
    return jsonify({"log": entries, "count": len(entries)})

@app.route('/api/combat_log/clear', methods=['POST'])
def clear_combat_log():
    """Clear the combat log."""
    COMBAT_LOGS.clear()
    return jsonify({"success": True})


# ──────────────────────────────────────────────────────────
# GM SECRET ROLLS
# ──────────────────────────────────────────────────────────
GM_SECRET_LOG = []  # Rolls only the GM can see

@app.route('/api/gm_secret_roll', methods=['POST'])
def gm_secret_roll():
    """Roll dice secretly — result only visible to GM, not broadcast to players."""
    data = request.json or {}
    dice_count = max(1, min(20, int(data.get('dice_count', 1))))
    dice_sides = max(2, min(100, int(data.get('dice_sides', 20))))
    modifier = int(data.get('modifier', 0))
    label = data.get('label', 'Secret Roll')

    rolls = [random.randint(1, dice_sides) for _ in range(dice_count)]
    total = sum(rolls) + modifier

    detail = f"{dice_count}d{dice_sides}"
    if modifier > 0: detail += f"+{modifier}"
    elif modifier < 0: detail += str(modifier)
    detail += f" = [{', '.join(str(r) for r in rolls)}]"
    if modifier: detail += f" + {modifier}"
    detail += f" = {total}"

    entry = {
        'id': str(uuid.uuid4())[:8],
        'time': time.strftime('%H:%M:%S'),
        'round': ROUND_NUMBER,
        'label': label,
        'detail': detail,
        'total': total,
        'rolls': rolls,
        'type': 'secret'
    }
    GM_SECRET_LOG.append(entry)
    if len(GM_SECRET_LOG) > 100: GM_SECRET_LOG.pop(0)

    # Only broadcast to GM via SSE (with secret flag — player views filter these out)
    sse_broadcast('gm_secret_roll', entry)

    return jsonify({"success": True, "roll": entry})

@app.route('/api/gm_secret_log')
def get_gm_secret_log():
    """Return the GM secret roll log."""
    return jsonify({"log": GM_SECRET_LOG})

@app.route('/api/gm_secret_log/clear', methods=['POST'])
def clear_gm_secret_log():
    GM_SECRET_LOG.clear()
    return jsonify({"success": True})


# ──────────────────────────────────────────────────────────
# RECALL KNOWLEDGE
# ──────────────────────────────────────────────────────────
# PF2e Recall Knowledge: creature identification DCs by level
# DC = level-based DC from Core Rulebook / GM Core
RK_DC_BY_LEVEL = {
    -1: 13, 0: 14, 1: 15, 2: 16, 3: 18, 4: 19, 5: 20, 6: 22, 7: 23, 8: 24,
    9: 26, 10: 27, 11: 28, 12: 30, 13: 31, 14: 32, 15: 34, 16: 35, 17: 36,
    18: 38, 19: 39, 20: 40, 21: 42, 22: 44, 23: 46, 24: 48, 25: 50,
}

# Rarity DC adjustments
RK_RARITY_ADJ = {'common': 0, 'uncommon': 2, 'rare': 5, 'unique': 10}

# Trait → relevant skill mapping for Recall Knowledge
RK_TRAIT_SKILLS = {
    'aberration': 'occultism', 'animal': 'nature', 'astral': 'occultism',
    'beast': 'arcana', 'celestial': 'religion', 'construct': 'arcana',
    'dragon': 'arcana', 'dream': 'occultism', 'elemental': 'arcana',
    'ethereal': 'occultism', 'fey': 'nature', 'fiend': 'religion',
    'fungus': 'nature', 'giant': 'nature', 'humanoid': 'society',
    'monitor': 'religion', 'ooze': 'occultism', 'plant': 'nature',
    'spirit': 'occultism', 'undead': 'religion',
}

def _get_rk_skill(combatant):
    """Determine the appropriate Recall Knowledge skill based on creature traits."""
    traits = [t.lower() for t in getattr(combatant, 'traits', [])]
    for trait in traits:
        if trait in RK_TRAIT_SKILLS:
            return RK_TRAIT_SKILLS[trait], trait
    # Default fallback
    return 'nature', 'creature'

def _get_rk_dc(combatant):
    """Calculate Recall Knowledge DC for a creature."""
    level = getattr(combatant, 'level', 0)
    base_dc = RK_DC_BY_LEVEL.get(level, 14 + level)

    # Adjust for rarity
    traits = [t.lower() for t in getattr(combatant, 'traits', [])]
    rarity_adj = 0
    for rarity in ['unique', 'rare', 'uncommon']:
        if rarity in traits:
            rarity_adj = RK_RARITY_ADJ[rarity]
            break

    return base_dc + rarity_adj

def _get_rk_info_tiers(combatant):
    """Build tiered information for Recall Knowledge results."""
    info = {'success': [], 'critical': []}

    # SUCCESS tier: basic info a scholar would know
    name = combatant.name
    traits = getattr(combatant, 'traits', [])
    if traits:
        info['success'].append(f"Creature traits: {', '.join(traits)}")

    # Immunities, resistances, weaknesses — the most tactically useful info
    immunities = getattr(combatant, 'immunities', [])
    if immunities:
        info['success'].append(f"Immunities: {', '.join(immunities)}")

    weaknesses = getattr(combatant, 'weaknesses', [])
    if weaknesses:
        info['success'].append(f"Weaknesses: {', '.join(weaknesses)}")

    resistances = getattr(combatant, 'resistances', [])
    if resistances:
        info['success'].append(f"Resistances: {', '.join(resistances)}")

    # Highest save
    saves = {'Fortitude': getattr(combatant, 'fort', 0), 'Reflex': getattr(combatant, 'ref', 0), 'Will': getattr(combatant, 'will', 0)}
    best_save = max(saves, key=saves.get)
    worst_save = min(saves, key=saves.get)
    info['success'].append(f"Strongest save: {best_save}")

    # CRITICAL SUCCESS tier: detailed info
    info['critical'].append(f"Weakest save: {worst_save} (+{saves[worst_save]})")
    info['critical'].append(f"AC: {combatant.ac}")

    # Special abilities
    actions = getattr(combatant, 'actions', [])
    if actions:
        ability_names = [a['name'] for a in actions[:3]]
        info['critical'].append(f"Notable abilities: {', '.join(ability_names)}")

    strikes = getattr(combatant, 'strikes', [])
    if strikes:
        best_strike = max(strikes, key=lambda s: s.get('bonus', 0))
        info['critical'].append(f"Best attack: {best_strike['name']} +{best_strike['bonus']} ({best_strike['damage']})")

    return info

@app.route('/api/recall_knowledge/<instance_id>', methods=['POST'])
def recall_knowledge(instance_id):
    """Perform a Recall Knowledge check against a creature in the encounter."""
    data = request.json or {}
    pc_name = data.get('pc_name', '')
    skill_override = data.get('skill', '')  # Optional skill override

    # A player can only roll Recall Knowledge as their own PC. Without this
    # check, Kyle could POST {pc_name: "Amadeus"} and the combat log would
    # show Amadeus's roll using Amadeus's bonuses — both a spoofing vector
    # and a fairness issue. GM bypasses (rolls NPCs / others freely).
    if GM_PASSWORD and not _is_gm():
        if not pc_name or session.get('player_name') != pc_name:
            return jsonify({"error": "forbidden — you can only roll for your own character"}), 403

    # Find the target creature
    target = None
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and not c.is_pc:
            target = c
            break

    if not target:
        return jsonify({"error": "Target creature not found"}), 404

    # A hidden NPC must not be a valid Recall Knowledge target for a player —
    # that would both reveal its name in the combat log and let players
    # enumerate instance_ids to brute-force hidden creatures. GM sessions
    # bypass this check (useful for admin/debug).
    if not getattr(target, 'visible_to_players', True) and not _is_gm():
        return jsonify({"error": "Target creature not found"}), 404

    # Find the PC
    pc = PARTY_LIBRARY.get(pc_name)
    if not pc:
        # Try to find in encounter
        for c in ACTIVE_ENCOUNTER:
            if c.is_pc and c.name == pc_name:
                pc = c
                break

    if not pc:
        return jsonify({"error": "PC not found"}), 404

    # Determine skill and DC
    suggested_skill, creature_type = _get_rk_skill(target)
    skill_name = skill_override if skill_override else suggested_skill
    dc = _get_rk_dc(target)

    # Get PC's skill modifier
    skill_mod = 0
    pc_skills = getattr(pc, 'skills', [])
    if isinstance(pc_skills, list):
        for sk in pc_skills:
            if isinstance(sk, dict) and sk.get('name', '').lower() == skill_name.lower():
                skill_mod = sk.get('total', 0)
                break
    elif isinstance(pc_skills, dict):
        skill_mod = pc_skills.get(skill_name, 0)

    # Roll d20 + skill modifier
    d20 = random.randint(1, 20)
    total = d20 + skill_mod

    # Determine degree of success
    if d20 == 20: total += 10  # Nat 20 upgrades
    if d20 == 1: total -= 10   # Nat 1 downgrades

    diff = total - dc
    if diff >= 10:
        degree = 'critical_success'
    elif diff >= 0:
        degree = 'success'
    elif diff > -10:
        degree = 'failure'
    else:
        degree = 'critical_failure'

    # Get info tiers
    info_tiers = _get_rk_info_tiers(target)
    revealed = []
    if degree == 'critical_success':
        revealed = info_tiers['success'] + info_tiers['critical']
    elif degree == 'success':
        revealed = info_tiers['success']
    elif degree == 'critical_failure':
        revealed = ["You recall incorrect information about this creature! (GM: provide false info)"]
    # failure = no info

    degree_labels = {
        'critical_success': 'Critical Success',
        'success': 'Success',
        'failure': 'Failure',
        'critical_failure': 'Critical Failure'
    }

    result = {
        'pc_name': pc_name,
        'target': target.name,
        'skill': skill_name.title(),
        'creature_type': creature_type,
        'd20': d20,
        'modifier': skill_mod,
        'total': d20 + skill_mod,  # Show raw total before nat 20/1 adjustment
        'dc': dc,
        'degree': degree,
        'degree_label': degree_labels[degree],
        'revealed_info': revealed,
        'suggested_skill': suggested_skill.title(),
    }

    # Log it as a secret GM roll (players see the degree but not the DC)
    _combat_log(f"📖 {pc_name} Recall Knowledge ({skill_name.title()}) vs {target.name}: {degree_labels[degree]} (d20={d20}, +{skill_mod}={d20+skill_mod} vs DC {dc})", 'action')

    # Broadcast as GM-only info
    sse_broadcast('recall_knowledge', result)

    return jsonify({"success": True, "result": result})

@app.route('/api/recall_knowledge_info/<instance_id>')
def recall_knowledge_info(instance_id):
    """Get Recall Knowledge metadata for a creature (skill, DC) without rolling.

    Hidden NPCs return 404 to non-GM callers. This stops players from
    probing instance_ids to pull DC/level/traits on creatures they shouldn't
    even know exist.
    """
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and not c.is_pc:
            if not getattr(c, 'visible_to_players', True) and not _is_gm():
                return jsonify({"error": "Target creature not found"}), 404
            skill, creature_type = _get_rk_skill(c)
            dc = _get_rk_dc(c)
            return jsonify({
                'name': c.name,
                'skill': skill.title(),
                'creature_type': creature_type,
                'dc': dc,
                'level': c.level,
                'traits': getattr(c, 'traits', []),
            })
    return jsonify({"error": "Creature not found"}), 404


# ──────────────────────────────────────────────────────────
# PLAYER HANDOUTS
# ──────────────────────────────────────────────────────────
HANDOUTS = []  # [{id, title, content, image_url, recipients: ['all' or pc_names], time, from_gm}]


def _load_handouts():
    """Load the campaign's persisted handout records (text/title/recipients).
    The uploaded images already live on the volume; this keeps the records that
    point at them from vanishing on a single-worker restart/redeploy."""
    global HANDOUTS
    data = _storage.load_json(HANDOUTS_FILE, []) if HANDOUTS_FILE else []
    HANDOUTS = data if isinstance(data, list) else []


def _save_handouts():
    if not HANDOUTS_FILE:
        return
    try:
        _atomic_write_json(HANDOUTS_FILE, HANDOUTS, indent=2)
    except Exception as e:
        print(f"[HANDOUTS] save failed: {e}")


_load_handouts()


@app.route('/api/handouts', methods=['GET'])
def get_handouts():
    """Get handouts. Players only see handouts addressed to them or 'all'."""
    player_name = request.args.get('player', '').strip()
    if not player_name:
        # GM sees all handouts
        return jsonify({"handouts": HANDOUTS})
    # Player: filter to their handouts
    visible = [h for h in HANDOUTS if 'all' in h.get('recipients', []) or player_name in h.get('recipients', [])]
    return jsonify({"handouts": visible})

@app.route('/api/handouts', methods=['POST'])
@gm_required
def create_handout():
    """GM creates a handout to push to players."""
    data = request.json or {}
    title = data.get('title', 'Handout').strip()
    content = data.get('content', '').strip()  # Text/HTML content
    image_url = data.get('image_url', '').strip()  # Optional image URL
    recipients = data.get('recipients', ['all'])  # ['all'] or ['PlayerName1', 'PlayerName2']

    if not title and not content and not image_url:
        return jsonify({"error": "Handout must have title, content, or image"}), 400

    handout = {
        'id': str(uuid.uuid4())[:8],
        'title': title,
        'content': content,
        'image_url': image_url,
        'recipients': recipients,
        'time': time.strftime('%H:%M:%S'),
        'from_gm': True,
    }
    HANDOUTS.append(handout)
    if len(HANDOUTS) > 50: HANDOUTS.pop(0)
    _save_handouts()

    # Broadcast to all clients (players filter client-side)
    sse_broadcast('handout', handout)

    return jsonify({"success": True, "handout": handout})

@app.route('/api/handouts/<handout_id>', methods=['DELETE'])
@gm_required
def delete_handout(handout_id):
    """GM deletes a handout."""
    global HANDOUTS
    HANDOUTS = [h for h in HANDOUTS if h['id'] != handout_id]
    _save_handouts()
    sse_broadcast('handout_deleted', {'id': handout_id})
    return jsonify({"success": True})

@app.route('/api/handout_upload', methods=['POST'])
def upload_handout_image():
    """Upload an image for a handout."""
    if 'image' not in request.files:
        return jsonify({"error": "No image file"}), 400

    f = request.files['image']
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    # Save to the persistent data volume (HANDOUTS_DIR = <DATA_DIR>/uploads/handouts),
    # NOT the app's static folder — on Railway the app filesystem is ephemeral, so
    # static-folder uploads vanish on the next deploy. Served via /handouts/<file>.
    upload_dir = HANDOUTS_DIR
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp'):
        return jsonify({"error": "Invalid image format"}), 400

    # Cap per-file size. The global MAX_CONTENT_LENGTH is 64 MB — far larger
    # than any handout needs — so measure this file (seek to end, no read into
    # memory) and reject oversized uploads before writing to disk.
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > 10 * 1024 * 1024:
        return jsonify({"error": "Image too large (max 10 MB)"}), 400

    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    filepath = os.path.join(upload_dir, filename)
    f.save(filepath)

    url = f"/handouts/{filename}"
    return jsonify({"success": True, "url": url})


def _parse_damage_type_value(entry_str):
    """Parse a resistance/weakness string like 'fire 5' or 'slashing 10 (except adamantine)' into (type, value, exceptions)."""
    entry_str = entry_str.strip().lower()
    exceptions = []
    # Extract exceptions like (except adamantine)
    exc_match = re.search(r'\(except\s+(.+?)\)', entry_str)
    if exc_match:
        exceptions = [e.strip() for e in exc_match.group(1).split(',')]
        entry_str = entry_str[:exc_match.start()].strip()
    # Split into type and value
    parts = entry_str.rsplit(' ', 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0], int(parts[1]), exceptions
    return entry_str, 0, exceptions

def _calculate_damage_with_wri(amount, damage_type, combatant):
    """Apply immunities, resistances, and weaknesses to damage. Returns (final_amount, adjustments_log)."""
    if not damage_type or damage_type == 'untyped':
        return amount, []

    dtype = damage_type.lower().strip()
    adjustments = []

    # Check immunities first — immune means 0 damage of that type
    immunities = [i.lower().strip() for i in getattr(combatant, 'immunities', [])]
    if dtype in immunities:
        adjustments.append(f"IMMUNE to {damage_type}")
        return 0, adjustments
    # Check for broader immunity categories (e.g., "all damage" for objects, "physical" for certain creatures)
    physical_types = {'bludgeoning', 'piercing', 'slashing'}
    energy_types = {'fire', 'cold', 'electricity', 'acid', 'sonic', 'force', 'vitality', 'void', 'spirit'}
    if 'physical' in immunities and dtype in physical_types:
        adjustments.append(f"IMMUNE to physical ({damage_type})")
        return 0, adjustments

    final = amount

    # Apply resistances (subtract from damage, minimum 0)
    for r_str in getattr(combatant, 'resistances', []):
        rtype, rval, exceptions = _parse_damage_type_value(r_str)
        if rtype == dtype or (rtype == 'physical' and dtype in physical_types) or (rtype == 'all' and dtype):
            # Check if any exception applies (e.g., "except adamantine" — but we don't track material on incoming damage, so exceptions don't apply here)
            final = max(0, final - rval)
            adjustments.append(f"Resist {damage_type} {rval}")
            break  # Only one resistance applies per damage type

    # Apply weaknesses (add to damage)
    for w_str in getattr(combatant, 'weaknesses', []):
        wtype, wval, _ = _parse_damage_type_value(w_str)
        if wtype == dtype or (wtype == 'physical' and dtype in physical_types):
            final = final + wval
            adjustments.append(f"Weak {damage_type} +{wval}")
            break  # Only one weakness applies per damage type

    return final, adjustments

# Standard PF2e damage types for the UI
PF2E_DAMAGE_TYPES = [
    'untyped', 'bludgeoning', 'piercing', 'slashing',
    'fire', 'cold', 'electricity', 'acid', 'sonic',
    'vitality', 'void', 'force', 'spirit',
    'mental', 'poison', 'bleed',
]


def _apply_cosmere_injury_effect(c, effect):
    """Apply an injury's d8 effect (Ch.9) as a tracked condition on the combatant
    so it surfaces on the sheet. Exhausted parses its magnitude and stacks (a
    valued condition); slowed/disoriented/surprised set the boolean. 'Can only
    use one hand' has no condition mapping -- it lives in the injury_log only."""
    e = (effect or '').lower()
    conds = c.conditions
    if 'exhausted' in e:
        import re as _re
        m = _re.search(r'(\d+)', e)
        mag = int(m.group(1)) if m else 1
        cur = conds.get('exhausted', 0)
        cur = int(cur) if (isinstance(cur, (int, float)) and not isinstance(cur, bool)) else (1 if cur else 0)
        conds['exhausted'] = cur + mag
    elif 'slowed' in e:
        conds['slowed'] = True
    elif 'disoriented' in e:
        conds['disoriented'] = True
    elif 'surprised' in e:
        conds['surprised'] = True


def _cosmere_adjust_hp(c, amount, action, damage_type):
    """Damage/heal a Cosmere combatant via the Cosmere combat engine: Deflect
    reduces impact/keen/energy only, and reaching 0 health triggers an injury
    roll (the death-spiral, Ch.9) instead of the PF2e dying/wounded model."""
    import systems.cosmere.combat as _cc
    amount = max(0, int(amount or 0))
    if getattr(c, 'conditions', None) is None:
        c.conditions = {}
    if action == 'heal':
        c.current_hp = min(int(getattr(c, 'max_hp', c.current_hp) or c.current_hp), c.current_hp + amount)
        if c.current_hp > 0:
            c.conditions.pop('unconscious', None)
        try:
            _bump_campaign_stat('total_healing', amount)
        except Exception:
            pass
        _combat_log(f"{c.name} recovers {amount} health.", 'success')
        return
    deflect = int((getattr(c, 'deflect', {}) or {}).get('value', 0) or 0)
    # Map any non-Cosmere damage type -- the UI quick/Enter path and the route
    # both default to 'untyped', and the GM may pick a PF2e type -- to a
    # deflectable physical hit ('impact'), the rulebook default for a blow.
    # Only the explicit bypassing Cosmere types (spirit/vital) skip Deflect.
    dtype = (damage_type or '').strip().lower()
    if dtype not in _cc.DAMAGE_TYPES:
        dtype = 'impact'
    at_zero_before = c.current_hp <= 0
    new_hp, taken, hit_zero = _cc.apply_damage(c.current_hp, amount, dtype, deflect)
    c.current_hp = new_hp
    try:
        _bump_campaign_stat('total_damage_dealt', taken)
        _record_big_hit(c.name, taken, c.is_pc)
    except Exception:
        pass
    note = f"{c.name} takes {taken} {dtype} damage"
    if deflect and dtype in _cc.DEFLECTABLE:
        note += f" (Deflect {deflect})"
    # An injury occurs on being reduced to 0, or on taking damage while at 0 (Ch.9).
    if hit_zero or (at_zero_before and taken > 0):
        inj = int(getattr(c, 'injuries', 0) or 0)
        roll = _cc.roll_injury(deflect=deflect, existing_injuries=inj)
        c.injuries = inj + 1
        c.conditions['unconscious'] = True
        # Persist the STRUCTURED injury (severity/duration/effect) so the GM can
        # track the death spiral over a long fight, not just an integer count.
        if not isinstance(getattr(c, 'injury_log', None), list):
            c.injury_log = []
        eff = roll.get('effect', '')
        c.injury_log.append({'n': c.injuries, 'total': roll['total'],
                             'severity': roll['severity'],
                             'duration': roll.get('duration', ''), 'effect': eff})
        if roll['severity'] == 'death':
            note += f" — Unconscious. INJURY ROLL {roll['total']}: DEATH"
        else:
            # Auto-apply the d8 effect as a tracked condition so it shows on the
            # sheet instead of only scrolling past in the log.
            if eff:
                _apply_cosmere_injury_effect(c, eff)
            note += (f" — Unconscious, injury #{c.injuries} (roll {roll['total']} = "
                     f"{roll['severity'].replace('_', ' ')}" + (f"; {eff})" if eff else ")"))
    _combat_log(note, 'critical')


@app.route('/api/adjust_hp/<instance_id>', methods=['POST'])
@require_live_combatant
def adjust_hp(instance_id):
    old_hp = None
    action = None
    amount = 0
    try:
        amount = int(request.form.get('amount', 0))
        action = request.form.get('action')
        damage_type = request.form.get('damage_type', 'untyped').strip()
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                old_hp = c.current_hp
                # Cosmere combatants use the Cosmere combat engine (Deflect +
                # injuries), not the PF2e W/R/I + dying/wounded path below.
                if getattr(c, 'system', 'pf2e') == 'cosmere':
                    _cosmere_adjust_hp(c, amount, action, damage_type)
                    _persist_encounter_state()
                    _broadcast_encounter_state()
                    # Reflect the new HP/injuries onto the player's own Cosmere
                    # sheet (it repaints from cosmere_player_state) so the tracker
                    # and the sheet stay in sync, not just the GM's tracker view.
                    _sync_cosmere_pc_from_combatant(c)
                    break
                effective = amount
                wri_notes = []
                if action == 'damage':
                    was_above_zero = c.current_hp > 0
                    # Session-scrapbook hook (Chunk 6): track the biggest blow
                    # struck (damage dealt to a non-PC foe).
                    try:
                        _record_big_hit(c.name, amount, c.is_pc)
                    except Exception:
                        pass
                    # Campaign stats hook (Tier 4, feature 30)
                    try:
                        _bump_campaign_stat('total_damage_dealt', amount)
                    except Exception:
                        pass
                    # PCs route through the same temp-HP-aware damage
                    # path the player sheet uses. Without this the GM's
                    # tracker "−damage" button silently bypassed the
                    # PC's manual temp HP pool + any toggle THP — a +10
                    # temp HP buff would protect against player-side
                    # damage but evaporate under GM-side damage. Same
                    # in-memory PC object, so we keep wri (PCs don't
                    # carry W/R/I tables today, but a future per-PC
                    # ABP rune that grants resistance would land here).
                    if c.is_pc and c.name in PARTY_LIBRARY:
                        # Drain toggle THP (passive — re-grants), then
                        # manual THP, then real HP. Same shape as
                        # adjust_party_hp's mutator so the two paths
                        # stay in lockstep.
                        def _mutate(pc):
                            remaining = amount
                            toggle_thp = 0
                            try:
                                toggle_thp = int(pc.toggle_effects_summary.get('temp_hp', 0) or 0)
                            except Exception:
                                toggle_thp = 0
                            if toggle_thp > 0 and remaining > 0:
                                remaining = max(0, remaining - toggle_thp)
                            manual_thp = int(getattr(pc, 'temp_hp_manual', 0) or 0)
                            if manual_thp > 0 and remaining > 0:
                                used = min(manual_thp, remaining)
                                pc.temp_hp_manual = manual_thp - used
                                remaining -= used
                            pc.temp_hp = int(getattr(pc, 'temp_hp_manual', 0) or 0) + toggle_thp
                            pc.current_hp = max(0, pc.current_hp - remaining)
                            return remaining   # hp_actually_lost
                        hp_before = c.current_hp
                        try:
                            hp_lost, _pc = apply_pc_delta(c.name, _mutate, persist=False, broadcast=False)
                        except Exception:
                            hp_lost = 0
                            _pc = PARTY_LIBRARY.get(c.name)
                        if _pc is not None:
                            c.current_hp = _pc.current_hp
                            try:
                                c.temp_hp = int(getattr(_pc, 'temp_hp', 0) or 0)
                                c.temp_hp_manual = int(getattr(_pc, 'temp_hp_manual', 0) or 0)
                            except Exception:
                                pass
                        effective = hp_before - (c.current_hp if _pc else hp_before)
                        # Dying / wounded post-conditions still computed
                        # by apply_pc_delta's mutator since we set
                        # pc.current_hp inside it. But we also need to
                        # bump dying here for consistency with old log
                        # behavior — apply_pc_delta doesn't add dying.
                        if c.current_hp == 0:
                            if was_above_zero:
                                c.conditions['dying'] = 1 + c.conditions.get('wounded', 0)
                            else:
                                c.conditions['dying'] = c.conditions.get('dying', 0) + 1
                            doomed = int(c.conditions.get('doomed', 0) or 0)
                            max_dying = max(1, 4 - doomed)
                            if c.conditions['dying'] >= max_dying:
                                c.conditions['dying'] = max_dying
                            _combat_log(f"{c.name} is Dying {c.conditions['dying']}{' — DEAD' if c.conditions['dying'] >= max_dying else ''}!", 'critical')
                            # Mirror back to PARTY_LIBRARY
                            PARTY_LIBRARY[c.name].conditions['dying'] = c.conditions['dying']
                    else:
                        # Non-PC: apply W/R/I resist/weakness, then drain HP.
                        if damage_type and damage_type != 'untyped':
                            effective, wri_notes = _calculate_damage_with_wri(amount, damage_type, c)
                        c.current_hp = max(0, c.current_hp - effective)
                        if c.current_hp == 0:
                            c.conditions['dying'] = 1 + c.conditions.get('wounded', 0) if was_above_zero else c.conditions.get('dying', 0) + 1
                            doomed = int(c.conditions.get('doomed', 0) or 0)
                            max_dying = max(1, 4 - doomed)
                            if c.conditions['dying'] >= max_dying:
                                c.conditions['dying'] = max_dying
                            _combat_log(f"{c.name} is Dying {c.conditions['dying']}{' — DEAD' if c.conditions['dying'] >= max_dying else ''}!", 'critical')

                    # Combat log with the same shape as before
                    type_label = f" {damage_type}" if damage_type and damage_type != 'untyped' else ''
                    if wri_notes:
                        adj_detail = ' | '.join(wri_notes)
                        _combat_log(f"{c.name} took {effective}{type_label} damage ({old_hp}→{c.current_hp}) [{adj_detail}, raw {amount}]", 'damage')
                    else:
                        _combat_log(f"{c.name} took {effective}{type_label} damage ({old_hp}→{c.current_hp})", 'damage')
                elif action == 'heal':
                    was_dying = c.conditions.get('dying', 0) > 0
                    c.current_hp = min(c.hp, c.current_hp + amount)
                    _combat_log(f"{c.name} healed {amount} HP ({old_hp}→{c.current_hp})", 'heal')
                    if c.current_hp > 0 and was_dying:
                        c.conditions['dying'] = 0; c.conditions['wounded'] = c.conditions.get('wounded', 0) + 1
                        _combat_log(f"{c.name} recovered from Dying! (Wounded {c.conditions['wounded']})", 'critical')
                    # Campaign stats hook (Tier 4, feature 30)
                    try:
                        _bump_campaign_stat('total_healing', amount)
                    except Exception:
                        pass
                if c.is_pc and c.name in PARTY_LIBRARY:
                    PARTY_LIBRARY[c.name].current_hp = c.current_hp
                    PARTY_LIBRARY[c.name].conditions['dying'] = c.conditions['dying']
                    PARTY_LIBRARY[c.name].conditions['wounded'] = c.conditions['wounded']
                    _broadcast_pc_state(c.name)
                    _persist_pc_combat_state(c.name)
                # Reaction hint: any "when damaged" effects on the
                # target surface to the player + GM. Only fires on the
                # damage action (heal is silent).
                if action == 'damage':
                    _emit_reaction_triggers(
                        pc_name=c.name if c.is_pc else None,
                        instance_id=c.instance_id,
                        event='on_damaged',
                        damage_amount=int(effective),
                    )
                _persist_encounter_state()
                _broadcast_encounter_state()
                break
    except ValueError: pass
    if _is_ajax():
        # Report the ACTUAL hp-pool change (after Deflect / resistances /
        # weaknesses / temp HP / clamping) so the client toast says the net
        # damage that landed, not the raw amount typed.
        extra = None
        _c = _find_active_combatant(instance_id)
        if _c is not None and old_hp is not None and action in ('damage', 'heal'):
            net = (old_hp - _c.current_hp) if action == 'damage' else (_c.current_hp - old_hp)
            extra = {'applied': {'instance_id': instance_id, 'action': action,
                                 'net': int(max(0, net)), 'raw': int(amount or 0)}}
        return _tracker_json_response(extra)
    return redirect(url_for('tracker_view'))


# ── Basic-save AOE resolver ────────────────────────────────────────────────
_BASIC_SAVE_MULT = {'crit_success': 0.0, 'success': 0.5, 'failure': 1.0, 'crit_failure': 2.0}
_SAVE_ATTR = {'fortitude': 'fort', 'fort': 'fort', 'reflex': 'ref', 'ref': 'ref', 'will': 'will'}


def _degree_of_success(total, dc, d20=None):
    """PF2e four-step degree for a check `total` vs `dc`, applying the natural-20
    bumps-up-one-step / natural-1 bumps-down-one-step rule when the raw `d20` is
    given. Returns 'crit_failure' | 'failure' | 'success' | 'crit_success'."""
    if total >= dc + 10:
        step = 3
    elif total >= dc:
        step = 2
    elif total <= dc - 10:
        step = 0
    else:
        step = 1
    if d20 == 20:
        step = min(3, step + 1)
    elif d20 == 1:
        step = max(0, step - 1)
    return ('crit_failure', 'failure', 'success', 'crit_success')[step]


@app.route('/api/multi_save_damage', methods=['POST'])
def multi_save_damage():
    """Resolve a basic-save area effect against several combatants at once: roll
    each target's save (or use a GM-provided d20), compute the PF2e degree, and
    report the post-save damage (crit-success 0 / success half / failure full /
    crit-failure double). COMPUTES + LOGS only -- the client then applies each
    `effective` through /api/adjust_hp, so weakness/resistance, temp HP, and the
    dying path all stay in the one tested damage path (no duplication)."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not ids:
        return jsonify({'error': 'no targets selected'}), 400
    save = str(data.get('save') or 'reflex').strip().lower()
    attr = _SAVE_ATTR.get(save, 'ref')
    save_label = {'fort': 'Fortitude', 'ref': 'Reflex', 'will': 'Will'}[attr]
    try:
        dc = int(data.get('dc') or 0)
        damage = max(0, int(data.get('damage') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'dc and damage must be numbers'}), 400
    rolls = data.get('rolls') or {}
    dtype = str(data.get('damage_type') or 'untyped').strip()
    by_id = {c.instance_id: c for c in ACTIVE_ENCOUNTER}
    results = []
    for iid in ids:
        c = by_id.get(iid)
        if c is None:
            continue   # stale id -> skip; the tab re-syncs on the next broadcast
        bonus = int(getattr(c, attr, 0) or 0)
        try:
            d20 = int(rolls[iid]) if iid in rolls else random.randint(1, 20)
        except (TypeError, ValueError, KeyError):
            d20 = random.randint(1, 20)
        d20 = max(1, min(20, d20))
        total = d20 + bonus
        degree = _degree_of_success(total, dc, d20)
        mult = _BASIC_SAVE_MULT[degree]
        effective = int(math.floor(damage * mult))
        results.append({'instance_id': iid, 'name': c.name, 'd20': d20, 'bonus': bonus,
                        'total': total, 'degree': degree, 'multiplier': mult, 'effective': effective})
        tstr = (' ' + dtype) if dtype and dtype != 'untyped' else ''
        _combat_log(f"{c.name}: {save_label} save {total} vs DC {dc} — "
                    f"{degree.replace('_', ' ')} ({effective} of {damage}{tstr})", 'roll')
    return jsonify({'results': results})


@app.route('/api/adjust_party_hp/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def adjust_party_hp(pc_name):
    try:
        amount = int(request.form.get('amount', 0))
        action = request.form.get('action')
        if pc_name not in PARTY_LIBRARY:
            return redirect(url_for('party_view'))

        def _mutate(pc):
            if action == 'damage':
                was_above_zero = pc.current_hp > 0
                remaining = amount
                # PF2e: temporary HP absorbs damage before real HP.
                # Drain the toggle (passive — re-grants from the toggle),
                # then the manual pool, then real HP.
                toggle_thp = 0
                try:
                    toggle_thp = int(pc.toggle_effects_summary.get('temp_hp', 0) or 0)
                except Exception:
                    toggle_thp = 0
                if toggle_thp > 0 and remaining > 0:
                    remaining = max(0, remaining - toggle_thp)
                manual_thp = int(getattr(pc, 'temp_hp_manual', 0) or 0)
                if manual_thp > 0 and remaining > 0:
                    used = min(manual_thp, remaining)
                    pc.temp_hp_manual = manual_thp - used
                    remaining -= used
                pc.temp_hp = int(getattr(pc, 'temp_hp_manual', 0) or 0) + toggle_thp
                pc.current_hp = max(0, pc.current_hp - remaining)
                if pc.current_hp == 0 and was_above_zero:
                    pc.conditions['dying'] = 1 + pc.conditions.get('wounded', 0)
                elif pc.current_hp == 0 and not was_above_zero:
                    pc.conditions['dying'] = pc.conditions.get('dying', 0) + 1
                # PF2e: dying death threshold is 4 - doomed.
                doomed = int(pc.conditions.get('doomed', 0) or 0)
                max_dying = max(1, 4 - doomed)
                if pc.conditions.get('dying', 0) >= max_dying:
                    pc.conditions['dying'] = max_dying
            elif action == 'heal':
                was_dying = pc.conditions.get('dying', 0) > 0
                pc.current_hp = min(pc.hp, pc.current_hp + amount)
                if pc.current_hp > 0 and was_dying:
                    pc.conditions['dying'] = 0
                    pc.conditions['wounded'] = pc.conditions.get('wounded', 0) + 1
            return True

        _, pc = apply_pc_delta(pc_name, _mutate)
        # Reaction hint: damage path only — heal doesn't trigger
        # "when struck" effects (Heal action arguably could, but PF2e
        # core rules don't generally chain reactions off positive HP
        # deltas).
        if action == 'damage' and amount > 0:
            _emit_reaction_triggers(
                pc_name=pc_name,
                instance_id=getattr(pc, 'instance_id', None) or None,
                event='on_damaged',
                damage_amount=int(amount),
            )
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            doomed = int(pc.conditions.get('doomed', 0) or 0)
            return jsonify({
                "success": True, "current_hp": pc.current_hp,
                "temp_hp": int(getattr(pc, 'temp_hp', 0) or 0),
                "temp_hp_manual": int(getattr(pc, 'temp_hp_manual', 0) or 0),
                "conditions": {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
                "dying": pc.conditions.get('dying', 0),
                "wounded": pc.conditions.get('wounded', 0),
                "doomed": doomed,
                "dead": pc.conditions.get('dying', 0) >= max(1, 4 - doomed)
            })
    except ValueError: pass
    return redirect(url_for('party_view'))

@app.route('/api/adjust_temp_hp/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def adjust_temp_hp(pc_name):
    """Set / add to / clear a PC's manual temporary HP pool.

    action='set'   -> PF2e stacking rule: take max(current, amount)
    action='add'   -> add to the current pool (for stacking effects the
                      player explicitly wants to combine — we let the player
                      decide rather than silently swallowing the smaller value)
    action='clear' -> zero the pool (effect expired, dawn, etc.)
    """
    try:
        # Accept both JSON body (fetch) and form body (legacy callers).
        payload = request.get_json(silent=True) or {}
        amount = max(0, int(payload.get('amount', request.form.get('amount', 0)) or 0))
        action = (payload.get('action') or request.form.get('action') or 'set').lower()
        if pc_name not in PARTY_LIBRARY:
            return jsonify({"success": False, "error": "Unknown PC"}), 404
        if action not in ('set', 'add', 'clear'):
            return jsonify({"success": False, "error": "Bad action"}), 400

        def _mutate(pc):
            current = int(getattr(pc, 'temp_hp_manual', 0) or 0)
            if action == 'set':
                pc.temp_hp_manual = max(current, amount)
            elif action == 'add':
                pc.temp_hp_manual = current + amount
            elif action == 'clear':
                pc.temp_hp_manual = 0
            try:
                pc.temp_hp = pc.temp_hp_manual + pc.toggle_effects_summary.get('temp_hp', 0)
            except Exception:
                pc.temp_hp = pc.temp_hp_manual
            return True

        _, pc = apply_pc_delta(pc_name, _mutate)
        return jsonify({
            "success": True,
            "temp_hp": int(pc.temp_hp),
            "temp_hp_manual": int(pc.temp_hp_manual),
        })
    except ValueError:
        return jsonify({"success": False, "error": "Bad amount"}), 400

@app.route('/api/adjust_focus/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def adjust_focus(pc_name):
    try:
        action = request.form.get('action')
        if pc_name not in PARTY_LIBRARY:
            return jsonify({"success": False})
        def _mutate(pc):
            if action == 'increase' and pc.current_focus < pc.focus_max:
                pc.current_focus += 1
            elif action == 'decrease' and pc.current_focus > 0:
                pc.current_focus -= 1
            return True
        _, pc = apply_pc_delta(pc_name, _mutate)
        return jsonify({"success": True, "current_focus": pc.current_focus})
    except ValueError: pass
    return jsonify({"success": False})

@app.route('/api/refocus/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def refocus(pc_name):
    """PF2e Refocus activity: spend 10 minutes performing class-appropriate
    deeds to restore 1 Focus Point (capped at focus_max, max 3 in pool)."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    def _mutate(pc):
        if pc.focus_max <= 0:
            raise ValueError("no focus pool")
        if pc.current_focus >= pc.focus_max:
            raise ValueError("focus pool already full")
        pc.current_focus = min(pc.focus_max, pc.current_focus + 1)
        return True
    try:
        _, pc = apply_pc_delta(pc_name, _mutate)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    return jsonify({"success": True, "current_focus": pc.current_focus,
                    "focus_max": pc.focus_max,
                    "message": f"{pc_name} refocused (10 min). +1 Focus Point."})

@app.route('/api/adjust_hero/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def adjust_hero(pc_name):
    try:
        action = request.form.get('action')
        if pc_name not in PARTY_LIBRARY:
            return jsonify({"success": False})
        def _mutate(pc):
            if action == 'increase' and pc.hero_points < 3:
                pc.hero_points += 1
            elif action == 'decrease' and pc.hero_points > 0:
                pc.hero_points -= 1
            return True
        _, pc = apply_pc_delta(pc_name, _mutate)
        return jsonify({"success": True, "current_hero": pc.hero_points})
    except ValueError: pass
    return jsonify({"success": False})

# =============================================================================
# DAILY PREPARATIONS
# =============================================================================
@app.route('/api/daily_prep/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def daily_preparations(pc_name):
    """Daily preparations: reset spell slots, focus points, conditions, optionally heal to full."""
    pc, file_path, err = require_pc(pc_name)
    if err: return err
    
    pc_json, file_path, err = require_pc_json(pc_name)
    if err: return err
    build = pc_json.get('build', pc_json)
    
    data = request.json or {}
    heal_full = data.get('heal_full', True)
    
    # Reset expended spell slots, prepared lists, and per-slot cast tracking
    build['expended_slots'] = {}
    build['prepared_spells'] = {}
    build['cast_prep'] = {}

    # Restore focus to max
    build['current_focus'] = pc.focus_max
    
    # Clear combat conditions that don't persist overnight
    conditions_to_clear = ['frightened', 'sickened', 'stunned', 'slowed', 'dying', 'off_guard', 'concealed', 'hidden', 'prone']
    if 'conditions' not in build: build['conditions'] = {}
    for cond in conditions_to_clear:
        build['conditions'][cond] = False if cond in ['off_guard', 'concealed', 'hidden', 'prone'] else 0

    # Conditions that *tick down* rather than clear: PF2e CRB.
    #   Wounded & Doomed: reduce by 1 per long rest.
    #   Drained: reduce by 1 per long rest.
    for tick_cond in ('wounded', 'doomed', 'drained'):
        cur = safe_int(build['conditions'].get(tick_cond, 0), 0)
        if cur > 0:
            build['conditions'][tick_cond] = cur - 1

    # Clear persistent damage (list or legacy string)
    build['persistent_damage'] = []

    # Reset reaction + lower shield (fresh day, shield not actively raised)
    build['reaction_used'] = False
    build['shield_raised'] = False

    # Temp HP pool resets overnight in PF2e.
    build['temp_hp'] = 0

    # Reset hero points to 1
    build['hero_points'] = 1

    # Heal to full HP if requested
    if heal_full:
        build.pop('current_hp', None)  # Removing it makes Character.__init__ default to max

    save_and_reload_character(pc_name, pc_json, file_path)
    _broadcast_pc_state(pc_name)
    
    return jsonify({"success": True, "message": f"{pc_name} completed daily preparations."})

@app.route('/api/daily_prep_all', methods=['POST'])
def daily_preparations_all():
    """Daily preparations for all party members at once."""
    data = request.json or {}
    heal_full = data.get('heal_full', True)
    results = []
    for pc_name in list(PARTY_LIBRARY.keys()):
        try:
            pc_json, file_path, err = require_pc_json(pc_name)
            if err: continue
            build = pc_json.get('build', pc_json)
            build['expended_slots'] = {}
            build['prepared_spells'] = {}
            build['cast_prep'] = {}
            pc = PARTY_LIBRARY[pc_name]
            build['current_focus'] = pc.focus_max
            if 'conditions' not in build: build['conditions'] = {}
            for cond in ['frightened', 'sickened', 'stunned', 'slowed', 'dying', 'off_guard', 'concealed', 'hidden', 'prone']:
                build['conditions'][cond] = False if cond in ['off_guard', 'concealed', 'hidden', 'prone'] else 0
            for tick_cond in ('wounded', 'doomed', 'drained'):
                cur = safe_int(build['conditions'].get(tick_cond, 0), 0)
                if cur > 0:
                    build['conditions'][tick_cond] = cur - 1
            build['persistent_damage'] = []
            build['reaction_used'] = False
            build['shield_raised'] = False
            build['temp_hp'] = 0
            build['hero_points'] = 1
            if heal_full:
                build.pop('current_hp', None)
            save_and_reload_character(pc_name, pc_json, file_path)
            _broadcast_pc_state(pc_name)
            results.append(pc_name)
        except Exception as e:
            print(f"[DAILY PREP] Error for {pc_name}: {e}")
    return jsonify({"success": True, "prepared": results})

# =============================================================================
# SHIELD BLOCK SYSTEM
# =============================================================================
@app.route('/api/shield_block/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def shield_block(pc_name):
    """Use Shield Block reaction: reduce damage by hardness, shield takes remaining."""
    pc, _, err = require_pc(pc_name)
    if err: return err

    data = request.json or {}
    damage = safe_int(data.get('damage', 0))

    # Eligibility values — read for the response, but the actual atomic
    # check + mutate happens inside `_mutate(pc)` under ENCOUNTER_LOCK so
    # two near-simultaneous clicks can't both consume the reaction.
    shield_max_hp = int(getattr(pc, 'shield_max_hp', 0) or 0)
    shield_hardness = int(getattr(pc, 'shield_hardness', 0) or 0)
    shield_bt = int(getattr(pc, 'shield_bt', 0) or (shield_max_hp // 2))
    if shield_max_hp <= 0:
        return jsonify({"success": False, "error": "No shield equipped"})

    state = {'error': None}

    def _mutate(pc):
        # Re-read all eligibility fields under the lock — closes the TOCTOU
        # window where two reactions could both pass the pre-check.
        cur_shield_hp = int(getattr(pc, 'shield_hp', 0) or 0)
        cur_bt = int(getattr(pc, 'shield_bt', 0) or (shield_max_hp // 2))
        if cur_shield_hp <= 0:
            state['error'] = "Shield is destroyed"
            return False
        if getattr(pc, 'reaction_used', False):
            state['error'] = "Reaction already spent this round"
            return False
        # PF2e: a broken shield CAN still Shield Block — the leftover damage
        # often destroys it. We allow the action and surface a warning client-
        # side instead of refusing it server-side.
        was_broken = cur_shield_hp <= cur_bt

        blocked = min(damage, shield_hardness)
        leftover = max(0, damage - shield_hardness)
        new_shield_hp = max(0, cur_shield_hp - leftover)
        pc.shield_hp = new_shield_hp
        pc.shield_broken = new_shield_hp <= cur_bt
        pc.shield_destroyed = new_shield_hp <= 0
        pc.reaction_used = True

        # Full damage pipeline for the leftover that hits the PC. Single
        # temp-HP pool: drain the toggle component first (effect sources),
        # then the manual pool, then real HP. Matches PF2e's "any temp HP
        # absorbs damage before real HP".
        was_above_zero = pc.current_hp > 0
        remaining = leftover
        toggle_thp = 0
        try:
            toggle_thp = int(pc.toggle_effects_summary.get('temp_hp', 0) or 0)
        except Exception:
            toggle_thp = 0
        manual_thp = int(getattr(pc, 'temp_hp_manual', 0) or 0)
        if toggle_thp > 0 and remaining > 0:
            # Toggle sources are passive — they absorb damage but the
            # toggle itself isn't drained (it'll re-grant the same THP
            # next tick from toggle_effects_summary).
            remaining = max(0, remaining - toggle_thp)
        if manual_thp > 0 and remaining > 0:
            used = min(manual_thp, remaining)
            pc.temp_hp_manual = manual_thp - used
            remaining -= used
        pc.temp_hp = int(getattr(pc, 'temp_hp_manual', 0) or 0) + toggle_thp
        pc.current_hp = max(0, pc.current_hp - remaining)
        if pc.current_hp == 0 and was_above_zero:
            pc.conditions['dying'] = 1 + pc.conditions.get('wounded', 0)
        elif pc.current_hp == 0 and not was_above_zero:
            pc.conditions['dying'] = pc.conditions.get('dying', 0) + 1
        # PF2e: dying threshold is 4 - doomed; clamp accordingly.
        doomed = int(pc.conditions.get('doomed', 0) or 0)
        max_dying = max(1, 4 - doomed)
        if pc.conditions.get('dying', 0) >= max_dying:
            pc.conditions['dying'] = max_dying

        state['blocked'] = blocked
        state['leftover'] = leftover
        state['shield_hp'] = new_shield_hp
        state['shield_broken'] = pc.shield_broken
        state['shield_destroyed'] = pc.shield_destroyed
        state['was_broken'] = was_broken
        return True

    ok, pc = apply_pc_delta(pc_name, _mutate)
    if not ok:
        return jsonify({"success": False, "error": state.get('error') or "Shield Block unavailable"})

    status = "destroyed" if state['shield_destroyed'] else "broken" if state['shield_broken'] else "intact"
    warn = " [shield was already broken — extra fragile]" if state['was_broken'] else ""
    _combat_log(f"{pc_name}: Shield Block! Blocked {state['blocked']} dmg (Hardness {shield_hardness}). Shield took {state['leftover']} ({status}){warn}. {state['leftover']} dmg to HP.", 'action')

    return jsonify({
        "success": True,
        "blocked": state['blocked'],
        "damage_to_char": state['leftover'],
        "damage_to_shield": state['leftover'],
        "shield_hp": state['shield_hp'],
        "shield_max_hp": shield_max_hp,
        "shield_hardness": shield_hardness,
        "shield_bt": shield_bt,
        "shield_broken": state['shield_broken'],
        "shield_destroyed": state['shield_destroyed'],
        "shield_was_broken": state['was_broken'],
        "current_hp": pc.current_hp,
        "temp_hp": int(getattr(pc, 'temp_hp', 0) or 0),
        "reaction_used": bool(getattr(pc, 'reaction_used', False)),
        "dying": pc.conditions.get('dying', 0),
        "wounded": pc.conditions.get('wounded', 0),
        "doomed": pc.conditions.get('doomed', 0),
        "dead": pc.conditions.get('dying', 0) >= max(1, 4 - int(pc.conditions.get('doomed', 0) or 0)),
        "conditions": {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
    })

@app.route('/api/repair_shield/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def repair_shield(pc_name):
    """Repair a shield (Crafting check during daily prep or Repair action)."""
    pc_json, file_path, err = require_pc_json(pc_name)
    if err: return err
    build = pc_json.get('build', pc_json)
    
    data = request.json or {}
    amount = safe_int(data.get('amount'), 0)
    full_repair = data.get('full_repair', False)
    
    shield_max_hp = safe_int(build.get('shield_max_hp'), 20)
    shield_hp = safe_int(build.get('shield_hp'), shield_max_hp)
    
    if full_repair:
        build['shield_hp'] = shield_max_hp
    else:
        build['shield_hp'] = min(shield_max_hp, shield_hp + amount)

    save_and_reload_character(pc_name, pc_json, file_path)
    # Mirror onto in-memory PC so the next pc_update payload picks it up,
    # then broadcast so party_view + GM screen repaint the shield gauge.
    if pc_name in PARTY_LIBRARY:
        PARTY_LIBRARY[pc_name].shield_hp = build['shield_hp']
        PARTY_LIBRARY[pc_name].shield_broken = build['shield_hp'] <= safe_int(build.get('shield_bt'), shield_max_hp // 2)
        PARTY_LIBRARY[pc_name].shield_destroyed = build['shield_hp'] <= 0
    _broadcast_pc_state(pc_name)
    return jsonify({"success": True, "shield_hp": build['shield_hp'], "shield_max_hp": shield_max_hp})

@app.route('/api/set_shield_stats/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def set_shield_stats(pc_name):
    """Set shield stats (hardness, HP, BT) when equipping a new shield."""
    pc_json, file_path, err = require_pc_json(pc_name)
    if err: return err
    build = pc_json.get('build', pc_json)
    
    data = request.json or {}
    build['shield_hardness'] = safe_int(data.get('hardness'), 5)
    build['shield_max_hp'] = safe_int(data.get('max_hp'), 20)
    build['shield_hp'] = safe_int(data.get('hp'), build['shield_max_hp'])
    build['shield_bt'] = safe_int(data.get('bt'), build['shield_max_hp'] // 2)
    build['shield_ac_bonus'] = safe_int(data.get('ac_bonus'), 2)

    save_and_reload_character(pc_name, pc_json, file_path)
    # save_and_reload_character rebuilds the Character — mirror is automatic.
    _broadcast_pc_state(pc_name)
    return jsonify({"success": True})


# NEW: FRONTEND CONDITION SYNC
@app.route('/api/update_pc_condition/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def update_pc_condition(pc_name):
    data = request.json
    cond = data.get('condition')
    val = data.get('value')
    delta = data.get('delta')
    toggle = data.get('toggle')
    
    file_path = get_pc_file_path(pc_name)
    if os.path.exists(file_path):
        # Before reading disk, flush any dirty in-memory combat state so we
        # don't clobber unflushed writes (persistent_damage, shield_hp,
        # reaction_used, exploration_activity, current_hp). Without this the
        # read-modify-write cycle here reverts changes that haven't made it
        # through the debounced persistence thread yet.
        _flush_pc_dirty(pc_name)
        with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        if 'conditions' not in build: build['conditions'] = {}

        if toggle:
            # Boolean conditions (prone, off_guard)
            current = build['conditions'].get(cond, False)
            build['conditions'][cond] = not current
        elif delta is not None:
            # Incremental (frightened ±1, sickened ±1, etc.)
            current = safe_int(build['conditions'].get(cond, 0))
            new_val = max(0, min(4, current + int(delta)))
            build['conditions'][cond] = new_val
        elif val is not None:
            # Absolute value (legacy/GM usage)
            if isinstance(val, int): val = max(0, min(4, val))
            build['conditions'][cond] = val

        save_and_reload_character(pc_name, pc_json, file_path)
        # Mirror the change onto the live encounter row so end-of-turn
        # auto-decrement (frightened, slowed, stunned) actually sees the
        # condition the player just applied. Without this the tracker row
        # and PARTY_LIBRARY drift — the player sees frightened 2 on the
        # sheet but the GM clicks "Next Turn" and nothing decrements.
        try:
            if ACTIVE_ENCOUNTER:
                for _c in ACTIVE_ENCOUNTER:
                    if _c.is_pc and _c.name == pc_name:
                        if not isinstance(_c.conditions, dict):
                            _c.conditions = {}
                        # Copy the freshly-saved condition map onto the tracker
                        # row. Using build['conditions'] (not the in-memory PC)
                        # avoids a window where save_and_reload hasn't rebuilt
                        # the PC yet.
                        _c.conditions = dict(build.get('conditions') or {})
                        break
        except Exception: pass
        _broadcast_pc_state(pc_name)
        # Echo the canonical (filtered) condition map back so the caller can
        # paint the Conditions Matrix / Quick-Conditions rail immediately,
        # without waiting on the coalesced pc_update frame.
        return jsonify({"success": True,
                        "conditions": {k: v for k, v in build['conditions'].items()
                                       if v and v != 0 and v is not False}})
    return jsonify({"success": False})

@app.route('/api/toggle_condition/<instance_id>', methods=['POST'])
@require_live_combatant
def toggle_condition(instance_id):
    condition = request.form.get('condition')
    action = request.form.get('action')
    # Optional rounds value sets a GM-defined auto-expiry timer that ticks
    # at the end of this combatant's turn. Only honored on add/increase;
    # decrease/toggle leaves any existing timer alone unless the condition
    # value reaches 0 (in which case the timer is cleared as a side effect).
    try:
        rounds = int(request.form.get('rounds', '') or 0)
    except ValueError:
        rounds = 0
    for combatant in ACTIVE_ENCOUNTER:
        if combatant.instance_id == instance_id:
            if condition in ['frightened', 'sickened', 'dying', 'wounded', 'doomed', 'stunned', 'slowed', 'enfeebled', 'clumsy', 'drained', 'stupefied']:
                current = combatant.conditions.get(condition, 0)
                if action in ['increase', 'add']:
                    combatant.conditions[condition] = current + 1
                    if rounds > 0:
                        if not hasattr(combatant, 'condition_expiry') or combatant.condition_expiry is None:
                            combatant.condition_expiry = {}
                        combatant.condition_expiry[condition] = rounds
                elif action == 'decrease' and current > 0:
                    combatant.conditions[condition] = current - 1
                    if condition == 'dying' and combatant.conditions[condition] == 0: combatant.conditions['wounded'] = combatant.conditions.get('wounded', 0) + 1
                # Clear any expiry if the condition is now 0
                if combatant.conditions.get(condition, 0) == 0:
                    _exp = getattr(combatant, 'condition_expiry', None)
                    if isinstance(_exp, dict) and condition in _exp:
                        del _exp[condition]
            elif condition in ['prone', 'off_guard', 'concealed', 'hidden', 'undetected']:
                if action == 'toggle': combatant.conditions[condition] = not combatant.conditions[condition]
                elif action == 'add':
                    combatant.conditions[condition] = True
                    if rounds > 0:
                        if not hasattr(combatant, 'condition_expiry') or combatant.condition_expiry is None:
                            combatant.condition_expiry = {}
                        combatant.condition_expiry[condition] = rounds
                if combatant.conditions.get(condition, False) is False:
                    _exp = getattr(combatant, 'condition_expiry', None)
                    if isinstance(_exp, dict) and condition in _exp:
                        del _exp[condition]
            if combatant.is_pc and combatant.name in PARTY_LIBRARY: 
                PARTY_LIBRARY[combatant.name].conditions[condition] = combatant.conditions[condition]
                _broadcast_pc_state(combatant.name)
                _persist_pc_combat_state(combatant.name)
            new_val = combatant.conditions.get(condition, 0)
            if isinstance(new_val, bool):
                _combat_log(f"{combatant.name} {'gained' if new_val else 'lost'} {condition.replace('_','-').title()}", 'condition')
                if new_val:
                    try:
                        _bump_campaign_stat('conditions_applied')
                    except Exception:
                        pass
            else:
                _combat_log(f"{combatant.name}: {condition.title()} -> {new_val}", 'condition')
                if new_val > 0 and action in ('increase', 'add'):
                    try:
                        _bump_campaign_stat('conditions_applied')
                    except Exception:
                        pass
            _persist_encounter_state()
            _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/use_action/<instance_id>', methods=['POST'])
@gm_required
def use_action(instance_id):
    """Toggle a combatant's action-economy pip. Body: {slot: 'action'|'reaction',
    delta: +1|-1}. Used by the tracker action-economy widget so the GM can
    track how many of a monster's 3 actions + reaction it has spent this turn.
    Auto-reset at the start of that combatant's next turn by cycle_turn."""
    data = request.get_json(silent=True) or {}
    slot = data.get('slot', 'action')
    try:
        delta = int(data.get('delta', 1))
    except (TypeError, ValueError):
        delta = 1
    with ENCOUNTER_LOCK:
        target = next((c for c in ACTIVE_ENCOUNTER if c.instance_id == instance_id), None)
        if not target:
            return jsonify({"success": False, "error": "Combatant not found"}), 404
        if slot == 'reaction':
            cur = bool(getattr(target, 'reaction_used', False))
            target.reaction_used = not cur if delta == 0 else (delta > 0)
            if target.is_pc and target.name in PARTY_LIBRARY:
                PARTY_LIBRARY[target.name].reaction_used = bool(target.reaction_used)
        else:
            max_a = int(getattr(target, 'max_actions', 3) or 3)
            cur = int(getattr(target, 'actions_used', 0) or 0)
            target.actions_used = max(0, min(max_a, cur + delta))
        actions_used = int(getattr(target, 'actions_used', 0) or 0)
        max_actions = int(getattr(target, 'max_actions', 3) or 3)
        reaction_used = bool(getattr(target, 'reaction_used', False))
        is_pc = target.is_pc
        target_name = target.name
    if is_pc and target_name in PARTY_LIBRARY:
        _broadcast_pc_state(target_name)
    _broadcast_encounter_state()
    return jsonify({
        "success": True,
        "actions_used": actions_used,
        "max_actions": max_actions,
        "reaction_used": reaction_used,
    })

@app.route('/api/recovery_check/<instance_id>', methods=['POST'])
@gm_required
def recovery_check(instance_id):
    """PF2e Remaster recovery check: flat check vs DC 10 + current dying value.
    The site never forces a roll — players can roll physical dice and POST
    {"d20": <result>}; we apply the degree-of-success math. POST without a d20
    rolls server-side. Crit success/fail bumps dying by 2; nat 1 / nat 20
    shift degree by one step. Dying 0 clears unconscious and adds wounded."""
    import random as _r
    data = request.get_json(silent=True) or {}
    try:
        d20 = int(data.get('d20')) if data.get('d20') not in (None, '', 0) else _r.randint(1, 20)
    except (TypeError, ValueError):
        d20 = _r.randint(1, 20)
    d20 = max(1, min(20, d20))
    with ENCOUNTER_LOCK:
        target = next((c for c in ACTIVE_ENCOUNTER if c.instance_id == instance_id), None)
        if not target:
            return jsonify({"success": False, "error": "Combatant not found"}), 404
        dying = int(target.conditions.get('dying', 0) or 0)
        if dying <= 0:
            return jsonify({"success": False, "error": "Not dying"}), 400
        dc = 10 + dying
        # Degree-of-success ladder, with nat 1 / nat 20 stepping by one
        if d20 >= dc + 10: degree = 'crit_success'
        elif d20 >= dc:    degree = 'success'
        elif d20 <= dc - 10: degree = 'crit_failure'
        else:              degree = 'failure'
        # Step by 1 for natural 1/20
        order = ['crit_failure', 'failure', 'success', 'crit_success']
        if d20 == 20: degree = order[min(len(order) - 1, order.index(degree) + 1)]
        elif d20 == 1: degree = order[max(0, order.index(degree) - 1)]
        delta = {'crit_success': -2, 'success': -1, 'failure': 1, 'crit_failure': 2}[degree]
        new_dying = max(0, dying + delta)
        doomed = int(target.conditions.get('doomed', 0) or 0)
        death_threshold = max(1, 4 - doomed)
        died = new_dying >= death_threshold
        if died:
            new_dying = death_threshold
        target.conditions['dying'] = new_dying
        if new_dying == 0 and dying > 0:
            target.conditions['wounded'] = int(target.conditions.get('wounded', 0) or 0) + 1
        is_pc = target.is_pc
        target_name = target.name
        new_wounded = target.conditions['wounded']
    if is_pc and target_name in PARTY_LIBRARY:
        PARTY_LIBRARY[target_name].conditions['dying'] = new_dying
        PARTY_LIBRARY[target_name].conditions['wounded'] = new_wounded
        _broadcast_pc_state(target_name)
        _persist_pc_combat_state(target_name)
    label = degree.replace('_', ' ').title()
    msg = f"{target_name}: Recovery DC {dc} → rolled {d20} → {label} ({'died' if died else f'Dying {new_dying}'})"
    _combat_log(msg, 'condition', degree=degree)
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({
        "success": True,
        "d20": d20,
        "dc": dc,
        "degree": degree,
        "delta": delta,
        "dying": new_dying,
        "died": died,
        "wounded": new_wounded,
    })

@app.route('/api/set_persistent_damage/<instance_id>', methods=['POST'])
@require_live_combatant
def set_persistent_damage(instance_id):
    pd_val = request.form.get('persistent_damage', '') or (request.json or {}).get('persistent_damage', '')
    # Hold ENCOUNTER_LOCK across the iterate-and-mutate so the autosave
    # snapshot (which also takes the lock) can't see a torn write, and
    # parallel AOE damage POSTs can't race each other on the same combatant.
    rejected_pc = False
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                # PCs store persistent_damage as a list-of-dicts; this string-
                # form endpoint would corrupt that into `list("1d6 fire")` →
                # ['1','d','6',...]. PC PD goes through /api/persistent_damage/
                # <name>/{add,remove,flat_check} instead — bail with a 409
                # so a misrouted client hears about it instead of silently
                # writing junk.
                if c.is_pc:
                    rejected_pc = True
                    break
                c.persistent_damage = pd_val
                break
    if rejected_pc:
        return jsonify({"success": False, "error": "PCs use /api/persistent_damage/<name>/... — this endpoint is monster-only"}), 409
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/toggle_elite_weak/<instance_id>', methods=['POST'])
@require_live_combatant
def toggle_elite_weak(instance_id):
    mode = request.form.get('mode', 'normal') or (request.json or {}).get('mode', 'normal')
    mode_val = {'elite': 1, 'weak': -1, 'normal': 0}.get(mode, 0)
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id and not c.is_pc and hasattr(c, 'apply_elite_weak'):
                c.apply_elite_weak(mode_val)
                break
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/update_initiative/<instance_id>', methods=['POST'])
@require_live_combatant
def update_initiative(instance_id):
    try: init_val = int(request.form.get('initiative', 0) or (request.json or {}).get('initiative', 0))
    except ValueError: init_val = 0
    with ENCOUNTER_LOCK:
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id: c.initiative = init_val; break
    _sort_encounter(); _persist_encounter_state(); _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

def _cosmere_init_bonus(c):
    """A Cosmere combatant's initiative bonus = the Speed attribute (the Dex
    analog) for the 'traditional' rolled-initiative house-rule."""
    return int((getattr(c, 'attributes', {}) or {}).get('spd', 0) or 0)


@app.route('/api/roll_npc_initiative', methods=['POST'])
def roll_npc_initiative():
    for c in ACTIVE_ENCOUNTER:
        if c.is_pc:
            continue
        if getattr(c, 'system', 'pf2e') == 'cosmere':
            c.initiative = random.randint(1, 20) + _cosmere_init_bonus(c)
        else:
            c.initiative = random.randint(1, 20) + getattr(c, 'perception', 0)
    _sort_encounter(); _persist_encounter_state(); _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/roll_all_initiative', methods=['POST'])
def roll_all_initiative():
    """Roll initiative for all combatants. PCs use perception by default, NPCs use perception.
    Supports skill override (stealth, deception, etc.) and secret GM rolls."""
    data = request.json or {}
    skill_overrides = data.get('overrides', {})  # {instance_id: "stealth"} or {instance_id: "perception"}
    secret_roll = data.get('secret', False)  # If true, don't broadcast PC rolls
    results = []
    
    for c in ACTIVE_ENCOUNTER:
        override_skill = skill_overrides.get(c.instance_id, '').lower()

        d20 = random.randint(1, 20)

        # Cosmere combatants roll d20 + Speed (the traditional-initiative house-rule).
        if getattr(c, 'system', 'pf2e') == 'cosmere':
            spd = _cosmere_init_bonus(c)
            c.initiative = d20 + spd
            if not (secret_roll and c.is_pc):
                _combat_log(f"{c.name} rolled Initiative (Speed): {d20} + {spd} = {c.initiative}", 'action')
            results.append({'name': c.name, 'initiative': c.initiative})
            continue

        if c.is_pc:
            # PC initiative: perception by default, or use skill override
            if override_skill and override_skill != 'perception':
                # Use a skill check instead of perception
                skill_map = {'acrobatics':'dex', 'arcana':'int', 'athletics':'str', 'crafting':'int',
                             'deception':'cha', 'diplomacy':'cha', 'intimidation':'cha', 'medicine':'wis',
                             'nature':'wis', 'occultism':'int', 'performance':'cha', 'religion':'wis',
                             'society':'int', 'stealth':'dex', 'survival':'wis', 'thievery':'dex'}
                stat = skill_map.get(override_skill, 'wis')
                prof_val = safe_int(c.proficiencies.get(override_skill, 0))
                mod = c.mods.get(stat, 0)
                skill_bonus = mod + (c.level + prof_val if prof_val > 0 else 0)
                c.initiative = d20 + skill_bonus
                used_skill = override_skill.title()
            else:
                c.initiative = d20 + c.perception
                used_skill = "Perception"
            
            if not secret_roll:
                _combat_log(f"{c.name} rolled Initiative ({used_skill}): {d20} + {c.initiative - d20} = {c.initiative}", 'action')
            else:
                _combat_log(f"{c.name} rolled Initiative (secret)", 'action')
        else:
            # NPC initiative: always perception
            perc = getattr(c, 'perception', 0) if hasattr(c, 'perception') else getattr(c, 'base_perception', 0)
            c.initiative = d20 + perc
            _combat_log(f"{c.name} rolled Initiative: {d20} + {perc} = {c.initiative}", 'action')
        
        results.append({'name': c.name, 'instance_id': c.instance_id, 'initiative': c.initiative, 
                         'roll': d20, 'is_pc': c.is_pc, 'secret': secret_roll and c.is_pc})
    
    _sort_encounter()
    _persist_encounter_state()
    _broadcast_encounter_state()
    
    if request.is_json:
        return jsonify({"success": True, "results": results})
    return redirect(url_for('tracker_view'))

@app.route('/api/sort_initiative', methods=['POST'])
def sort_initiative():
    _sort_encounter(); _persist_encounter_state(); _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/cosmere/speed/<instance_id>', methods=['POST'])
def cosmere_speed_choice(instance_id):
    """Set a Cosmere combatant's fast/slow election (2 vs 3 actions); re-sorts
    the 4-phase queue."""
    choice = (request.form.get('choice') or (request.get_json(silent=True) or {}).get('choice') or '').lower()
    if choice not in ('fast', 'slow'):
        return jsonify({'error': 'choice must be fast or slow'}), 400
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and getattr(c, 'system', 'pf2e') == 'cosmere':
            c.speed_choice = choice
            c.max_actions = 2 if choice == 'fast' else 3
            _sort_encounter()
            _persist_encounter_state(); _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/cosmere/initiative_mode', methods=['POST'])
@gm_required
def api_cosmere_initiative_mode():
    """Switch the Cosmere initiative house-rule for this campaign: 'phases'
    (rulebook fast/slow) <-> 'traditional' (rolled d20+Speed order). Re-sorts the
    active encounter under the new rule and broadcasts it."""
    mode = (request.get_json(silent=True) or request.form or {}).get('mode')
    mode = 'traditional' if mode == 'traditional' else 'phases'
    _save_campaign_config({'cosmere_initiative': mode})
    _sort_encounter()
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({'ok': True, 'mode': mode})

@app.route('/api/cosmere/world', methods=['POST'])
@gm_required
def api_cosmere_world():
    """Switch this campaign's Cosmere visual world-skin: 'stormlight' <-> 'mistborn'.
    Pure theming (re-skins the whole Cosmere side); the ruleset is unchanged."""
    w = (request.get_json(silent=True) or request.form or {}).get('world')
    w = 'mistborn' if w == 'mistborn' else 'stormlight'
    _save_campaign_config({'cosmere_world': w})
    return jsonify({'ok': True, 'world': w})

@app.route('/api/reorder_initiative', methods=['POST'])
def reorder_initiative():
    """Reorder encounter list based on drag-and-drop order."""
    global ACTIVE_ENCOUNTER, TURN_INDEX
    data = request.json
    order = data.get('order', [])
    if not order or len(order) != len(ACTIVE_ENCOUNTER):
        return jsonify({"error": "Invalid order"}), 400
    
    # Find which combatant was active before reorder
    active_id = ACTIVE_ENCOUNTER[TURN_INDEX].instance_id if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
    
    # Build new order from instance_ids
    id_map = {c.instance_id: c for c in ACTIVE_ENCOUNTER}
    new_order = [id_map[iid] for iid in order if iid in id_map]
    if len(new_order) == len(ACTIVE_ENCOUNTER):
        ACTIVE_ENCOUNTER = new_order
        # Preserve active turn
        if active_id:
            for i, c in enumerate(ACTIVE_ENCOUNTER):
                if c.instance_id == active_id:
                    TURN_INDEX = i
                    break
    _broadcast_encounter_state()
    _persist_encounter_state()
    return jsonify({"success": True})

@app.route('/api/cycle_turn/<direction>', methods=['POST'])
def cycle_turn(direction):
    global TURN_INDEX, ACTIVE_ENCOUNTER, ROUND_NUMBER, TURN_REMINDERS
    if not ACTIVE_ENCOUNTER: return redirect(url_for('tracker_view'))
    if direction == 'next':
        # === END OF CURRENT TURN: auto-tick conditions (PF2E Remaster) ===
        current_c = ACTIVE_ENCOUNTER[TURN_INDEX]

        # GM-defined auto-expiry timers (decremented at end of THIS combatant's
        # turn). Independent of the per-condition tick rules below; whichever
        # clears the condition first wins.
        _expiry = getattr(current_c, 'condition_expiry', None)
        if isinstance(_expiry, dict) and _expiry:
            for _cond in list(_expiry.keys()):
                _expiry[_cond] = int(_expiry[_cond]) - 1
                if _expiry[_cond] <= 0:
                    if _cond in current_c.conditions:
                        if isinstance(current_c.conditions[_cond], bool):
                            current_c.conditions[_cond] = False
                        else:
                            current_c.conditions[_cond] = 0
                    del _expiry[_cond]
                    _combat_log(f"{current_c.name}: {_cond.replace('_','-').title()} expired", 'condition')
                    if current_c.is_pc and current_c.name in PARTY_LIBRARY:
                        try:
                            PARTY_LIBRARY[current_c.name].conditions[_cond] = current_c.conditions[_cond]
                        except Exception:
                            pass

        # PF2e Remaster integer condition-ticking. Cosmere conditions are
        # BOOLEANS (and follow their own refresh model), so running this on a
        # Cosmere combatant would mangle e.g. slowed=True into the int 0. Skip
        # the PF2e tick entirely for Cosmere combatants.
        if getattr(current_c, 'system', 'pf2e') != 'cosmere':
            # Frightened decreases by 1 at end of turn (PF2E Core p.619)
            if current_c.conditions.get('frightened', 0) > 0:
                current_c.conditions['frightened'] -= 1
                _combat_log(f"{current_c.name}: Frightened reduced to {current_c.conditions['frightened']}", 'condition')
                if current_c.is_pc and current_c.name in PARTY_LIBRARY: PARTY_LIBRARY[current_c.name].conditions['frightened'] = current_c.conditions['frightened']

            # Stupefied doesn't auto-reduce, but tracked here for completeness (requires Remove Curse / rest)
            # Enfeebled doesn't auto-reduce (requires specific recovery)
            # Clumsy doesn't auto-reduce (requires specific recovery)
            # Drained doesn't auto-reduce (requires long rest, reducing by 1 per long rest)
            # Sickened doesn't auto-reduce (must retch / Fortitude save)
            # Slowed: reduces by 1 at end of turn if caused by a non-permanent source (PF2E Core)
            if current_c.conditions.get('slowed', 0) > 0:
                # Only auto-reduce slowed if it has a duration (most slowed effects are 1 round)
                # Tracked via a flag — if no flag, assume it's round-based and auto-decrement
                if not getattr(current_c, '_slowed_persistent', False):
                    current_c.conditions['slowed'] -= 1
                    _combat_log(f"{current_c.name}: Slowed reduced to {current_c.conditions['slowed']}", 'condition')
                    if current_c.is_pc and current_c.name in PARTY_LIBRARY: PARTY_LIBRARY[current_c.name].conditions['slowed'] = current_c.conditions['slowed']

        # Sync conditions to PC file if applicable
        if current_c.is_pc and current_c.name in PARTY_LIBRARY:
            _persist_pc_combat_state(current_c.name)
        
        # Advance turn index, skipping delaying combatants
        old_index = TURN_INDEX
        for _ in range(len(ACTIVE_ENCOUNTER)):
            TURN_INDEX = (TURN_INDEX + 1) % len(ACTIVE_ENCOUNTER)
            if TURN_INDEX <= old_index:
                ROUND_NUMBER += 1
                try:
                    _bump_campaign_stat('total_combat_rounds')
                except Exception:
                    pass
            if not getattr(ACTIVE_ENCOUNTER[TURN_INDEX], 'delaying', False):
                break
            old_index = TURN_INDEX

        # Expire any token-active-effects whose round-based duration
        # elapsed this turn. Shield (1 round), Inspire Courage (1
        # round), Haste (when single-cast), etc. fall off automatically.
        # Minute-based effects (Bless, Bane, Heroism, Mage Armor) are
        # also tracked in rounds since 1 min = 10 rounds, so they expire
        # the same way. Logs each expiry to the combat log so the GM
        # sees what fell off.
        try:
            _expire_token_effects_for_round()
        except Exception as _ex:
            print('[EFFECTS] expire error:', _ex)

        # === START OF NEW TURN: auto-apply start-of-turn mechanics ===
        new_c = ACTIVE_ENCOUNTER[TURN_INDEX]

        # PC turn refresh: (1) get your reaction back, (2) Raise a Shield
        # expires (PF2e CRB: "until the start of your next turn"). We do this
        # before applying new start-of-turn effects so conditions that hit
        # you at turn-start can't accidentally be blocked by a stale shield.
        if new_c.is_pc and new_c.name in PARTY_LIBRARY:
            _pc = PARTY_LIBRARY[new_c.name]
            _pc.reaction_used = False
            if getattr(_pc, 'shield_raised', False):
                _pc.shield_raised = False
                _combat_log(f"{_pc.name}: Raise a Shield expired (start of turn)", 'condition')
            _persist_pc_combat_state(new_c.name)
            _broadcast_pc_state(new_c.name)
        # Action economy reset.
        if getattr(new_c, 'system', 'pf2e') == 'cosmere':
            # Cosmere fast/slow ceiling, derived from the elected speed EACH turn
            # so it survives turn advance (fast = 2 actions, slow = 3). No PF2e
            # slowed/stunned integer math — Cosmere conditions are booleans.
            new_c.max_actions = 2 if getattr(new_c, 'speed_choice', None) == 'fast' else 3
            new_c.actions_used = 0
            if not new_c.is_pc:
                new_c.reaction_used = False
        else:
            # PF2e: Slowed lowers the action ceiling, Stunned then spends from
            # what's left (PF2E Core p.448). Pip widget reads max_actions/
            # actions_used directly, so both must reflect the condition math
            # BEFORE the turn renders.
            slowed_val = new_c.conditions.get('slowed', 0)
            new_c.max_actions = max(0, min(4, 3 - int(slowed_val or 0)))
            new_c.actions_used = 0
            if not new_c.is_pc:
                new_c.reaction_used = False

            # Stunned: lose actions (cap at the remaining max), then decrement
            # stunned by the number lost. We pre-fill actions_used so the pip
            # widget shows the stunned cost already spent — otherwise the GM
            # would see 3/3 actions on a stunned combatant's turn and have to
            # remember to click them off manually.
            stunned_val = new_c.conditions.get('stunned', 0)
            if stunned_val > 0:
                actions_lost = min(stunned_val, new_c.max_actions)
                new_c.conditions['stunned'] = max(0, stunned_val - actions_lost)
                new_c.actions_used = actions_lost
                _combat_log(f"{new_c.name}: Lost {actions_lost} action(s) to Stunned. Stunned reduced to {new_c.conditions['stunned']}", 'condition')
                if new_c.is_pc and new_c.name in PARTY_LIBRARY: PARTY_LIBRARY[new_c.name].conditions['stunned'] = new_c.conditions['stunned']
        
        # Persistent damage (start of turn, PF2e Core p.451).
        # Two representations coexist:
        #   - Monsters / old tracker rows: `persistent_damage` is a dice string
        #     like "2d6 fire". We still auto-roll for the GM's convenience.
        #   - PCs: `persistent_damage` is a list of dicts (see Character.__init__).
        #     The player asked to NOT auto-roll; we just surface a reminder and
        #     let them click "Take N damage" + "Roll DC 15 Flat Check" on the sheet.
        pd = getattr(new_c, 'persistent_damage', '')
        if pd and not (new_c.is_pc and new_c.name in PARTY_LIBRARY):
            # Monster path: keep the old auto-roll behavior.
            if isinstance(pd, str):
                import re as _re
                pd_match = _re.search(r'(\d+)d(\d+)(?:\s*\+\s*(\d+))?', pd)
                if pd_match:
                    pd_qty = int(pd_match.group(1))
                    pd_sides = int(pd_match.group(2))
                    pd_bonus = int(pd_match.group(3)) if pd_match.group(3) else 0
                    pd_total = sum(random.randint(1, pd_sides) for _ in range(pd_qty)) + pd_bonus
                    old_hp = new_c.current_hp
                    new_c.current_hp = max(0, new_c.current_hp - pd_total)
                    _combat_log(f"{new_c.name}: Persistent {pd} dealt {pd_total} ({old_hp}→{new_c.current_hp})", 'damage')
                    flat_roll = random.randint(1, 20)
                    if flat_roll >= 15:
                        new_c.persistent_damage = ''
                        _combat_log(f"{new_c.name}: Flat check {flat_roll} >= 15 — persistent damage ends!", 'heal')
                    else:
                        _combat_log(f"{new_c.name}: Flat check {flat_roll} < 15 — persistent damage continues", 'damage')
        elif new_c.is_pc and new_c.name in PARTY_LIBRARY:
            # PC path: just log that it's pending. The reminder (and buttons on
            # the sheet) drive resolution — the player rolls the DC 15 flat
            # check themselves.
            _pc = PARTY_LIBRARY[new_c.name]
            pd_list = list(getattr(_pc, 'persistent_damage', []) or [])
            if pd_list:
                parts = [f"{e.get('damage','?')} {e.get('type','')}".strip() for e in pd_list]
                _combat_log(f"{_pc.name}: Persistent damage pending — {', '.join(parts)} (player rolls)", 'condition')
        
        _generate_turn_reminders()
        
    elif direction == 'prev':
        old_index = TURN_INDEX
        for _ in range(len(ACTIVE_ENCOUNTER)):
            TURN_INDEX = (TURN_INDEX - 1) % len(ACTIVE_ENCOUNTER)
            if TURN_INDEX >= old_index and ROUND_NUMBER > 1: ROUND_NUMBER -= 1
            if not getattr(ACTIVE_ENCOUNTER[TURN_INDEX], 'delaying', False):
                break
            old_index = TURN_INDEX
        _generate_turn_reminders()
    current_name = ACTIVE_ENCOUNTER[TURN_INDEX].name if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else '?'
    _combat_log(f"Round {ROUND_NUMBER}: {current_name}'s turn", 'turn')
    _persist_encounter_state()
    _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

TURN_REMINDERS = []  # List of reminder dicts for active combatant

def _generate_turn_reminders():
    """Generate start-of-turn reminders for the active combatant."""
    global TURN_REMINDERS
    TURN_REMINDERS = []
    if not ACTIVE_ENCOUNTER or TURN_INDEX >= len(ACTIVE_ENCOUNTER): return
    c = ACTIVE_ENCOUNTER[TURN_INDEX]
    
    # Persistent damage (happens at start of turn).
    # PCs now store this as a list of entries; render one reminder per entry
    # so each damage type is called out individually.
    _pd_raw = getattr(c, 'persistent_damage', '')
    if c.is_pc and c.name in PARTY_LIBRARY:
        _pd_raw = list(getattr(PARTY_LIBRARY[c.name], 'persistent_damage', []) or [])
    if isinstance(_pd_raw, list):
        for entry in _pd_raw:
            dmg = entry.get('damage', '?')
            ptype = entry.get('type', '')
            src = entry.get('source', '')
            title = f'Persistent {dmg}{" " + ptype if ptype else ""}'.strip()
            detail = 'Roll that damage, apply it, then roll a DC 15 flat check to end.'
            if src:
                detail = f'From {src}. ' + detail
            TURN_REMINDERS.append({
                'type': 'danger', 'icon': '🔥',
                'title': title,
                'detail': detail,
                'action': 'roll_pd'
            })
    elif _pd_raw:
        TURN_REMINDERS.append({
            'type': 'danger', 'icon': '🔥',
            'title': f'Persistent Damage: {_pd_raw}',
            'detail': 'Roll damage, apply it, then roll a DC 15 flat check to end.',
            'action': 'roll_pd'
        })
    
    # Dying (recovery check at start of turn)
    dying_val = c.conditions.get('dying', 0)
    if dying_val > 0:
        TURN_REMINDERS.append({
            'type': 'danger', 'icon': '💀',
            'title': f'Dying {dying_val} — Recovery Check',
            'detail': f'DC {10 + dying_val} flat check. Crit Success: dying -2. Success: dying -1. Failure: dying +1. Crit Fail: dying +2. Dies at Dying 4.',
            'action': None
        })
    
    # Sickened (can retch as a free action at start of turn — actually an action, but remind)
    sick_val = c.conditions.get('sickened', 0)
    if sick_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '🤢',
            'title': f'Sickened {sick_val}',
            'detail': f'−{sick_val} status penalty to all checks, DCs, saves, attacks. Can spend an action to retch (Fortitude save vs DC) to reduce.',
            'action': None
        })
    
    # Frightened (will tick down at END of this turn)
    fright_val = c.conditions.get('frightened', 0)
    if fright_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '😱',
            'title': f'Frightened {fright_val}',
            'detail': f'−{fright_val} status penalty to all checks, DCs, saves, attacks. Will decrease to {fright_val - 1} at end of turn.',
            'action': None
        })
    
    # Stunned (loses actions)
    stunned_val = c.conditions.get('stunned', 0)
    if stunned_val > 0:
        TURN_REMINDERS.append({
            'type': 'danger', 'icon': '⚡',
            'title': f'Stunned {stunned_val}',
            'detail': f'Lose {stunned_val} action(s) this turn. Stunned decreases by the number of actions lost.',
            'action': None
        })
    
    # Slowed (fewer actions)
    slowed_val = c.conditions.get('slowed', 0)
    if slowed_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '🐌',
            'title': f'Slowed {slowed_val}',
            'detail': f'Lose {slowed_val} action(s) this turn (start with {3 - slowed_val} actions instead of 3).',
            'action': None
        })
    
    # Prone
    if c.conditions.get('prone'):
        TURN_REMINDERS.append({
            'type': 'info', 'icon': '🔻',
            'title': 'Prone',
            'detail': 'Off-Guard (−2 AC). −2 to attack rolls. Must spend an action to Stand. Only movement is Crawl.',
            'action': None
        })
    
    # Off-Guard
    if c.conditions.get('off_guard') and not c.conditions.get('prone'):
        TURN_REMINDERS.append({
            'type': 'info', 'icon': '🛡',
            'title': 'Off-Guard',
            'detail': '−2 circumstance penalty to AC. Vulnerable to Sneak Attack.',
            'action': None
        })
    
    # Drained
    drained_val = c.conditions.get('drained', 0)
    if drained_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '💧',
            'title': f'Drained {drained_val}',
            'detail': f'−{drained_val} to Con-based checks. Max HP reduced by {drained_val} × level. Decreases by 1 per full night rest.',
            'action': None
        })

    # Enfeebled
    enf_val = c.conditions.get('enfeebled', 0)
    if enf_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '💪',
            'title': f'Enfeebled {enf_val}',
            'detail': f'−{enf_val} status penalty to Str-based rolls and DCs (attacks, Athletics, damage).',
            'action': None
        })

    # Clumsy
    clumsy_val = c.conditions.get('clumsy', 0)
    if clumsy_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '🦶',
            'title': f'Clumsy {clumsy_val}',
            'detail': f'−{clumsy_val} status penalty to Dex-based checks and DCs (AC, Reflex, Acrobatics, Stealth, Thievery).',
            'action': None
        })

    # Stupefied
    stup_val = c.conditions.get('stupefied', 0)
    if stup_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '🧠',
            'title': f'Stupefied {stup_val}',
            'detail': f'−{stup_val} penalty to mental checks, spell attacks, DCs. DC 5 + {stup_val} flat check or spell is lost when casting.',
            'action': None
        })

    # Doomed
    doomed_val = c.conditions.get('doomed', 0)
    if doomed_val > 0:
        TURN_REMINDERS.append({
            'type': 'danger', 'icon': '☠',
            'title': f'Doomed {doomed_val}',
            'detail': f'Max Dying value reduced to {4 - doomed_val}. Dies at Dying {4 - doomed_val} instead of 4.',
            'action': None
        })

    # Wounded
    wounded_val = c.conditions.get('wounded', 0)
    if wounded_val > 0:
        TURN_REMINDERS.append({
            'type': 'warning', 'icon': '🩹',
            'title': f'Wounded {wounded_val}',
            'detail': f'If you gain the dying condition, increase dying value by {wounded_val}. Removed when healed to max HP.',
            'action': None
        })

@app.route('/api/delay_turn/<instance_id>', methods=['POST'])
def delay_turn(instance_id):
    """Mark a combatant as delaying — they'll be skipped in turn order."""
    global TURN_INDEX, ROUND_NUMBER
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        if c.instance_id == instance_id:
            c.delaying = True
            # If it's currently their turn, advance to next
            if i == TURN_INDEX:
                # End-of-turn condition ticking still applies
                if c.conditions.get('frightened', 0) > 0:
                    c.conditions['frightened'] -= 1
                    if c.is_pc and c.name in PARTY_LIBRARY: PARTY_LIBRARY[c.name].conditions['frightened'] = c.conditions['frightened']
                # Move to next non-delaying combatant
                old_index = TURN_INDEX
                for _ in range(len(ACTIVE_ENCOUNTER)):
                    TURN_INDEX = (TURN_INDEX + 1) % len(ACTIVE_ENCOUNTER)
                    if TURN_INDEX <= old_index: ROUND_NUMBER += 1
                    if not getattr(ACTIVE_ENCOUNTER[TURN_INDEX], 'delaying', False):
                        break
                    old_index = TURN_INDEX
                _generate_turn_reminders()
            _persist_encounter_state()
            _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/reenter_initiative/<instance_id>', methods=['POST'])
def reenter_initiative(instance_id):
    """Re-enter a delaying combatant just before the current active combatant."""
    global TURN_INDEX
    delay_idx = None
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        if c.instance_id == instance_id and getattr(c, 'delaying', False):
            delay_idx = i
            break
    if delay_idx is None:
        if _is_ajax(): return _tracker_json_response()
        return redirect(url_for('tracker_view'))

    combatant = ACTIVE_ENCOUNTER.pop(delay_idx)
    combatant.delaying = False

    if delay_idx < TURN_INDEX:
        TURN_INDEX -= 1

    if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER):
        current_active = ACTIVE_ENCOUNTER[TURN_INDEX]
        combatant.initiative = current_active.initiative

    ACTIVE_ENCOUNTER.insert(TURN_INDEX, combatant)
    _generate_turn_reminders()
    _persist_encounter_state()
    _broadcast_encounter_state()

    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))


# ---------------------------------------------------------------------------
# Hazards (#12)
# ---------------------------------------------------------------------------
# Hazards live in ACTIVE_ENCOUNTER alongside monsters and PCs but expose two
# extra GM actions: "Trigger" (fire the routine, log it, mark triggered) and
# "Disable" (mark inert — Thievery / Disable Device success). Both are
# idempotent fire-and-forget POSTs from the tracker buttons.
def _find_hazard_combatant(instance_id):
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and getattr(c, 'is_hazard', False):
            return c
    return None

@app.route('/api/hazard/<instance_id>/trigger', methods=['POST'])
@gm_required
def hazard_trigger(instance_id):
    h = _find_hazard_combatant(instance_id)
    if not h:
        return jsonify({'success': False, 'error': 'hazard not found'}), 404
    h.triggered = True
    routine = (h.routine or '').strip() or '(GM describes effect)'
    _combat_log(f"⚠ Hazard triggered — {h.name}: {routine}", 'critical')
    _broadcast_encounter_state()
    return jsonify({'success': True})

@app.route('/api/hazard/<instance_id>/disable', methods=['POST'])
@gm_required
def hazard_disable(instance_id):
    h = _find_hazard_combatant(instance_id)
    if not h:
        return jsonify({'success': False, 'error': 'hazard not found'}), 404
    h.disabled = True
    _combat_log(f"Hazard disabled — {h.name}", 'system')
    _broadcast_encounter_state()
    return jsonify({'success': True})

@app.route('/api/hazard/<instance_id>/reset', methods=['POST'])
@gm_required
def hazard_reset(instance_id):
    """Re-arm a hazard (clears triggered + disabled). Useful for
    multi-fire complex hazards or for replaying an encounter."""
    h = _find_hazard_combatant(instance_id)
    if not h:
        return jsonify({'success': False, 'error': 'hazard not found'}), 404
    h.triggered = False
    h.disabled = False
    _combat_log(f"Hazard reset — {h.name}", 'system')
    _broadcast_encounter_state()
    return jsonify({'success': True})


def _sanitize_encounter_name(name: str) -> str:
    """Strip everything but alnum/space/dash/underscore from a user-supplied
    encounter name so it can be safely used as a filename. Returns '' if
    nothing usable remains so the caller can 400 instead of writing to
    a bare `.json` path."""
    if not name:
        return ''
    cleaned = ''.join(ch for ch in name if ch.isalnum() or ch in ' -_').strip()
    return cleaned[:120]  # cap so a giant name can't blow the filename limit


@app.route('/api/save_encounter', methods=['POST'])
def save_encounter():
    raw_name = request.form.get('encounter_name')
    name = _sanitize_encounter_name(raw_name or '')
    if not name:
        return jsonify({"success": False, "error": "encounter_name required (alphanumerics, space, '-' or '_')"}), 400
    if name and ACTIVE_ENCOUNTER:
        if not os.path.exists(ENCOUNTER_DIR): os.makedirs(ENCOUNTER_DIR)
        encounter_data = {
            "round": ROUND_NUMBER,
            "turn_index": TURN_INDEX,
            "notes": request.form.get('encounter_notes', ENCOUNTER_NOTES),
            "session_timer_start": SESSION_TIMER_START,
            "combatants": []
        }
        for c in ACTIVE_ENCOUNTER:
            entry = {
                'type': 'pc' if c.is_pc else 'monster',
                'path': c.name if c.is_pc else c.file_path,
                'instance_id': c.instance_id,
                'initiative': c.initiative,
                'current_hp': c.current_hp,
                'conditions': c.conditions,
                # Match the autosave field set so a manual "save Round 3, resume
                # next session" doesn't silently drop temp-condition timers or a
                # delayed combatant's delay (the autosave persists both; this
                # path used to omit them).
                'condition_expiry': dict(getattr(c, 'condition_expiry', {}) or {}),
                'delaying': getattr(c, 'delaying', False),
                'persistent_damage': getattr(c, 'persistent_damage', ''),
                'elite_weak': getattr(c, 'elite_weak', 0),
                # Persist hidden/visible state so saved encounters reload with
                # the same player visibility as when they were saved.
                'visible_to_players': getattr(c, 'visible_to_players', True),
                # Boss-reveal title (Chunk 4d) — saved with the encounter.
                'epithet': getattr(c, 'epithet', ''),
                # GM creature tactics notes.
                'tactics': getattr(c, 'tactics', ''),
            }
            encounter_data['combatants'].append(_augment_combatant_save(entry, c))
        with open(os.path.join(ENCOUNTER_DIR, f"{name}.json"), 'w', encoding='utf-8') as f:
            json.dump(encounter_data, f, indent=2)
    return redirect(url_for('tracker_view'))

@app.route('/api/load_encounter', methods=['POST'])
def load_encounter():
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER, ENCOUNTER_NOTES, SESSION_TIMER_START
    name = _sanitize_encounter_name(request.form.get('encounter_name') or '')
    if not name:
        return jsonify({"success": False, "error": "encounter_name required"}), 400
    enc_path = os.path.join(ENCOUNTER_DIR, f"{name}.json")
    if not os.path.exists(enc_path):
        return jsonify({"success": False, "error": f"encounter '{name}' not found"}), 404
    # Parse JSON BEFORE wiping the active state — a malformed save shouldn't
    # leave the GM staring at an empty tracker mid-session.
    try:
        with open(enc_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({"success": False, "error": f"encounter file is unreadable or corrupt: {e}"}), 500
    if name and os.path.exists(enc_path):
        ACTIVE_ENCOUNTER.clear(); TURN_INDEX = 0; ROUND_NUMBER = 1; ENCOUNTER_NOTES = ''

        # Support both old format (list) and new format (dict with metadata)
        if isinstance(raw, list):
            combatants = raw
        elif isinstance(raw, dict):
            combatants = raw.get('combatants', [])
            if not isinstance(combatants, list):
                combatants = []
            ROUND_NUMBER = raw.get('round', 1)
            TURN_INDEX = raw.get('turn_index', 0)
            ENCOUNTER_NOTES = raw.get('notes', '')
            SESSION_TIMER_START = raw.get('session_timer_start', None)
        else:
            combatants = []

        for item in combatants:
            new_c = None
            # Cosmere combatants rebuild from their source id, not PF2e libraries.
            if item.get('system') == 'cosmere':
                cos = _restore_cosmere_combatant(item)
                if cos is not None:
                    ACTIVE_ENCOUNTER.append(cos)
                continue
            if item.get('type') == 'monster' and item.get('path') in MONSTER_LIBRARY:
                new_c = copy.deepcopy(MONSTER_LIBRARY[item['path']])
            elif item.get('type') == 'pc' and item.get('path') in PARTY_LIBRARY:
                new_c = copy.deepcopy(PARTY_LIBRARY[item['path']])

            if new_c:
                new_c.instance_id = item.get('instance_id', str(uuid.uuid4()))
                new_c.initiative = item.get('initiative', 0)
                if 'current_hp' in item: new_c.current_hp = item['current_hp']
                if 'conditions' in item: new_c.conditions = item['conditions']
                if 'condition_expiry' in item:
                    new_c.condition_expiry = dict(item.get('condition_expiry') or {})
                if 'persistent_damage' in item:
                    _pd_in = item['persistent_damage']
                    # Heal corrupt stale values: historical saves may contain the
                    # literal string "[]" (which becomes ['[',']'] if later fed
                    # through list()). PCs use list-of-dicts; monsters use the
                    # legacy string format.
                    if new_c.is_pc:
                        if isinstance(_pd_in, list):
                            new_c.persistent_damage = [e for e in _pd_in if isinstance(e, dict)]
                        else:
                            new_c.persistent_damage = []
                    else:
                        if isinstance(_pd_in, str):
                            s = _pd_in.strip()
                            new_c.persistent_damage = '' if s in ('[]', '{}') else _pd_in
                        else:
                            new_c.persistent_damage = _pd_in or ''
                if 'delaying' in item: new_c.delaying = bool(item['delaying'])
                if 'elite_weak' in item and hasattr(new_c, 'apply_elite_weak'):
                    new_c.apply_elite_weak(item['elite_weak'])
                # Restore hidden/visible state from saved encounter files.
                if 'visible_to_players' in item:
                    new_c.visible_to_players = bool(item['visible_to_players']) if not new_c.is_pc else True
                if 'epithet' in item and not new_c.is_pc:
                    new_c.epithet = str(item['epithet'] or '')
                # GM creature tactics notes.
                if 'tactics' in item:
                    new_c.tactics = str(item['tactics'] or '')
                ACTIVE_ENCOUNTER.append(new_c)

        # Validate turn index
        if TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = 0
    return redirect(url_for('tracker_view'))

@app.route('/api/list_encounters', methods=['GET'])
def list_encounters():
    """Enumerate saved encounters in ENCOUNTER_DIR. Used by the builder's
    Load dropdown and the tracker's Save/Load drawer. Filters out the
    autosave file so it doesn't clutter the list."""
    if not os.path.exists(ENCOUNTER_DIR):
        return jsonify({"encounters": []})
    items = []
    for fname in sorted(os.listdir(ENCOUNTER_DIR)):
        if not fname.endswith('.json'):
            continue
        if fname.startswith('_'):
            continue
        name = fname[:-5]
        try:
            full = os.path.join(ENCOUNTER_DIR, fname)
            mtime = os.path.getmtime(full)
            with open(full, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            kind = 'stage' if isinstance(raw, dict) and raw.get('format') == 'stage' else 'encounter'
            count = (
                len(raw.get('monsters', [])) if kind == 'stage'
                else len(raw.get('combatants', []) if isinstance(raw, dict) else raw)
            )
            # Snapshot of the map that was active when this encounter was
            # saved (added in 998bb3a9-era). Surface its name + id so
            # the GM's "load" UI can show which battlefield each encounter
            # boots into.
            bound = None
            if isinstance(raw, dict) and isinstance(raw.get('map'), dict):
                m = raw['map']
                bound = {
                    'id': m.get('id'),
                    'name': m.get('name'),
                    'image': m.get('image'),
                }
            notes = raw.get('notes', '') if isinstance(raw, dict) else ''
        except Exception:
            mtime, kind, count, bound, notes = 0, 'encounter', 0, None, ''
        items.append({"name": name, "kind": kind, "count": count, "mtime": mtime, "map": bound, "notes": notes})
    items.sort(key=lambda it: -it.get('mtime', 0))
    return jsonify({"encounters": items})


@app.route('/api/save_stage', methods=['POST'])
def save_stage():
    """Save the encounter builder's staged monster list to ENCOUNTER_DIR.
    Body: {name: str, monsters: [{path, count, elite_weak, is_hazard, ...}]}.
    Stored under a dict shape with format='stage' so the loader can tell
    it apart from full-state save_encounter output (which carries hydrated
    combatant data and a map snapshot)."""
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    monsters = data.get('monsters') or []
    if not name:
        return jsonify({"success": False, "error": "Name required"}), 400
    # Strip path traversal; only keep filename-safe characters
    safe = ''.join(ch for ch in name if ch.isalnum() or ch in (' ', '-', '_')).strip()
    if not safe:
        return jsonify({"success": False, "error": "Invalid name"}), 400
    if not os.path.exists(ENCOUNTER_DIR):
        os.makedirs(ENCOUNTER_DIR)
    from datetime import datetime as _dt
    payload = {
        "format": "stage",
        "saved_at": _dt.now().isoformat(timespec='seconds'),
        "monsters": monsters,
    }
    with open(os.path.join(ENCOUNTER_DIR, f"{safe}.json"), 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    return jsonify({"success": True, "name": safe})


@app.route('/api/load_stage/<name>', methods=['GET'])
def load_stage(name):
    """Return the saved monster list for a stage so the builder can repopulate.
    Also tolerates the older live-encounter save format (combatants[] with
    type/path) by projecting it back to a stage list."""
    safe = ''.join(ch for ch in name if ch.isalnum() or ch in (' ', '-', '_')).strip()
    fpath = os.path.join(ENCOUNTER_DIR, f"{safe}.json")
    if not os.path.exists(fpath):
        return jsonify({"success": False, "error": "Not found"}), 404
    with open(fpath, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    if isinstance(raw, dict) and raw.get('format') == 'stage':
        return jsonify({"success": True, "monsters": raw.get('monsters', [])})
    # Legacy: project full save into a stage list. Group by path and count duplicates.
    src = raw.get('combatants', []) if isinstance(raw, dict) else raw
    monsters = []
    seen = {}
    for c in src:
        if c.get('type') != 'monster':
            continue
        key = (c.get('path'), c.get('elite_weak', 0))
        if key in seen:
            monsters[seen[key]]['count'] += 1
        else:
            seen[key] = len(monsters)
            monsters.append({
                'path': c.get('path'),
                'count': 1,
                'elite_weak': c.get('elite_weak', 0),
                'is_hazard': bool(c.get('is_hazard', False)),
            })
    return jsonify({"success": True, "monsters": monsters})


@app.route('/api/delete_encounter', methods=['POST'])
@gm_required
def delete_encounter():
    """Delete a saved encounter file."""
    name = request.form.get('encounter_name') or (request.json or {}).get('encounter_name')
    if not name:
        return jsonify({'success': False, 'error': 'No encounter name provided'}), 400
    
    # Sanitize filename to prevent directory traversal
    safe_name = os.path.basename(name)
    enc_path = os.path.join(ENCOUNTER_DIR, f"{safe_name}.json")
    
    if os.path.exists(enc_path):
        os.remove(enc_path)
        return jsonify({'success': True, 'deleted': safe_name})
    else:
        return jsonify({'success': False, 'error': 'Encounter not found'}), 404


@app.route('/api/encounter_notes', methods=['GET', 'POST'])
def encounter_notes_api():
    """Get or update the encounter notes for the active encounter."""
    global ENCOUNTER_NOTES
    if request.method == 'POST':
        ENCOUNTER_NOTES = (request.json or {}).get('notes', '')
        _persist_encounter_state()
        return jsonify({"success": True})
    return jsonify({"notes": ENCOUNTER_NOTES})


@app.route('/gmscreen')
@gm_required
def gm_screen():
    # System guard: a bookmarked/typed /gmscreen must not drop a Cosmere table
    # onto the PF2e GM screen. Send them to the Cosmere equivalent.
    if _active_system() == 'cosmere':
        return redirect(url_for('cosmere_gmscreen'))
    return render_template('gmscreen.html')


from services import active_effects as effects_service


@app.route('/encounter_builder')
@gm_required
def encounter_builder():
    sorted_party = sorted(PARTY_LIBRARY.values(), key=lambda p: p.name)
    party_level = max([p.level for p in PARTY_LIBRARY.values()]) if PARTY_LIBRARY else 1
    # Default party size from PARTY_LIBRARY; the template surfaces a numeric
    # override input so the GM can model "what if a guest joins" or "down to
    # 3 PCs tonight" without changing the canonical roster.
    party_size = max(1, len(PARTY_LIBRARY) or 4)
    # Compute a coarse "is there a healer in the party?" flag for the
    # difficulty-spike warning. We trust class identity + a skim of known
    # spell names. Misses corner cases (e.g. a Wizard with Heal scrolls)
    # but catches the 90% — Cleric / Druid / Bard / Champion / Witch /
    # Oracle, or any caster with Heal / Soothe / Lay on Hands prepped.
    HEALER_CLASSES = {'cleric', 'druid', 'bard', 'champion', 'witch', 'oracle'}
    HEAL_SPELLS = {'heal', 'soothe', 'lay on hands', 'goodberry', 'breath of life'}
    party_has_healer = False
    for p in sorted_party:
        cls = (getattr(p, 'class_name', '') or '').lower()
        if any(h in cls for h in HEALER_CLASSES):
            party_has_healer = True
            break
        # Skim spells_known on each spellcaster; structure is a list of dicts
        # with 'levels' → list of {'spells': [{'name': ...}]}.
        for sc in getattr(p, 'spell_casters', []) or []:
            for lvl in (sc.get('levels') or []):
                for sp in (lvl.get('spells') or []):
                    if (sp.get('name') or '').strip().lower() in HEAL_SPELLS:
                        party_has_healer = True
                        break
                if party_has_healer: break
            if party_has_healer: break
        if party_has_healer: break
    # Single source of truth for encounter math (lives in class_matrix).
    # Pass the constants down so the template's recalcXP() can scale per
    # party size instead of hardcoding the 4-player numbers.
    from class_matrix import ENCOUNTER_XP_BY_DIFF, ENCOUNTER_DIFFICULTY
    return render_template('encounter_builder.html',
                           party=sorted_party,
                           party_level=party_level,
                           party_size=party_size,
                           party_has_healer=party_has_healer,
                           xp_by_diff=ENCOUNTER_XP_BY_DIFF,
                           difficulty_tiers=ENCOUNTER_DIFFICULTY)

@app.route('/api/monster_search')
def api_monster_search():
    """Search monster library for the encounter builder.

    Accepts:
        q       — optional substring match on monster name.
        min_lvl — optional inclusive lower bound on level.
        max_lvl — optional inclusive upper bound on level.
        trait   — optional trait substring (e.g. 'undead', 'caster').

    When level or trait filters are provided we allow an empty `q` so the GM
    can browse "show me every Level 3–5 undead" without typing a name. Pair
    with the builder's live preview — we now surface primary-strike info so
    the GM doesn't need to open the full statblock to gauge threat.
    """
    query = request.args.get('q', '').strip().lower()
    trait_q = request.args.get('trait', '').strip().lower()
    try:
        min_lvl = int(request.args.get('min_lvl', '')) if request.args.get('min_lvl') else None
    except (TypeError, ValueError):
        min_lvl = None
    try:
        max_lvl = int(request.args.get('max_lvl', '')) if request.args.get('max_lvl') else None
    except (TypeError, ValueError):
        max_lvl = None

    has_any_filter = bool(query) or trait_q or (min_lvl is not None) or (max_lvl is not None)
    if not has_any_filter:
        return jsonify({"results": []})
    # Name-only searches still require 2 chars to keep the API cheap,
    # but filter-only browsing is allowed (no query-length gate).
    if query and not trait_q and min_lvl is None and max_lvl is None and len(query) < 2:
        return jsonify({"results": []})

    results = []
    for path, m in MONSTER_LIBRARY.items():
        if query and query not in m.name.lower():
            continue
        if min_lvl is not None and m.level < min_lvl:
            continue
        if max_lvl is not None and m.level > max_lvl:
            continue
        if trait_q:
            traits = [str(t).lower() for t in (getattr(m, 'traits', []) or [])]
            if not any(trait_q in t for t in traits):
                continue
        # Pull the strongest strike (best attack bonus) for a quick preview —
        # on the Monster class these live in `strikes`, each {'name','bonus','damage'}.
        best_atk = None
        best_dmg = None
        best_name = None
        try:
            for s in (getattr(m, 'strikes', []) or []):
                if not isinstance(s, dict):
                    continue
                bonus = safe_int(s.get('bonus'), 0) if 'bonus' in s else None
                if bonus is None:
                    continue
                if best_atk is None or bonus > best_atk:
                    best_atk = bonus
                    best_dmg = s.get('damage')
                    best_name = s.get('name')
        except Exception:
            pass
        results.append({
            'name': m.name, 'level': m.level, 'path': path,
            'hp': m.hp, 'ac': m.base_ac,
            'perception': getattr(m, 'perception', None),
            'immunities': m.immunities, 'resistances': m.resistances, 'weaknesses': m.weaknesses,
            'traits': [str(t) for t in (getattr(m, 'traits', []) or [])][:6],
            'best_attack': best_atk,
            'best_damage': best_dmg,
            'best_strike_name': best_name,
        })
    results.sort(key=lambda r: (r['level'], r['name']))
    return jsonify({"results": results[:40]})

def _make_hazard_combatant(entry):
    """Construct a hazard "combatant" from an encounter-builder entry.

    Hazards aren't in MONSTER_LIBRARY — they're created inline from the
    builder form. The tracker still iterates ACTIVE_ENCOUNTER like a
    flat list so we duck-type a Monster-shaped object: same fields the
    tracker reads (instance_id, name, level, is_pc=False, hp, ac, saves,
    initiative, conditions...) plus hazard-specific fields the tracker
    UI gates on (is_hazard, hazard_type, stealth_dc, disable_dc,
    trigger, routine, disabled).

    Simple hazards default to hp=0 — they fire once, no HP bar. Complex
    hazards may take damage; the GM enters HP via the tracker.
    """
    name = (entry.get('name') or 'Hazard').strip()
    level = int(entry.get('level') or 1)
    hazard_type = entry.get('hazard_type') or 'simple'
    h = type('Hazard', (), {})()
    h.instance_id = str(uuid.uuid4())
    h.name = name
    h.level = level
    h.is_pc = False
    h.is_hazard = True
    h.hazard_type = 'complex' if hazard_type == 'complex' else 'simple'
    h.stealth_dc = int(entry.get('stealth_dc') or 0)
    h.disable_dc = int(entry.get('disable_dc') or 0)
    h.trigger = (entry.get('trigger') or '').strip()
    h.routine = (entry.get('routine') or '').strip()
    h.disabled = False
    h.triggered = False
    # Combatant-shaped fields. Hazards have no AC/saves by default;
    # complex hazards with HP can be set via the tracker if needed.
    h.hp = int(entry.get('hp') or 0)
    h.current_hp = h.hp
    h.ac = 0
    h.base_ac = 0
    h.fort = 0
    h.ref = 0
    h.will = 0
    h.base_fort = 0
    h.base_ref = 0
    h.base_will = 0
    h.perception = 0
    h.base_perception = 0
    h.speed = 0
    h.initiative = 0
    h.elite_weak = 0
    h.delaying = False
    h.visible_to_players = True
    h.conditions = {}
    h.persistent_damage = ''
    h.immunities = []
    h.resistances = []
    h.weaknesses = []
    h.traits = ['hazard', h.hazard_type]
    h.strikes = []
    h.actions = []
    h.notes = ''
    return h


@app.route('/api/monster_details')
def api_monster_details():
    """Return enough monster metadata for the encounter-builder Load Stage
    flow to repopulate a row (name, level, immunities, resistances,
    weaknesses). Lookup by path against MONSTER_LIBRARY."""
    path = request.args.get('path', '').strip()
    if not path or path not in MONSTER_LIBRARY:
        return jsonify({"error": "Not found"}), 404
    m = MONSTER_LIBRARY[path]
    return jsonify({
        "name": m.name,
        "level": getattr(m, 'level', 0),
        "ac": getattr(m, 'ac', 0),
        "hp": getattr(m, 'hp', 0),
        "perception": getattr(m, 'perception', 0),
        "immunities": list(getattr(m, 'immunities', []) or []),
        "resistances": list(getattr(m, 'resistances', []) or []),
        "weaknesses": list(getattr(m, 'weaknesses', []) or []),
        "traits": list(getattr(m, 'traits', []) or []),
    })


@app.route('/api/stage_encounter', methods=['POST'])
def api_stage_encounter():
    """Load a staged encounter directly into the active tracker."""
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER
    data = request.json
    monsters = data.get('monsters', [])
    add_party = data.get('add_party', False)
    clear_first = data.get('clear_first', True)

    if clear_first:
        ACTIVE_ENCOUNTER.clear()
        TURN_INDEX = 0
        ROUND_NUMBER = 1

    # Add monsters + hazards. Hazards arrive with is_hazard=True and a
    # path of '__hazard__<name>'; they're constructed inline rather than
    # looked up in MONSTER_LIBRARY.
    for entry in monsters:
        path = entry.get('path')
        count = entry.get('count', 1)
        elite_weak = entry.get('elite_weak', 0)
        if entry.get('is_hazard') or (isinstance(path, str) and path.startswith('__hazard__')):
            for i in range(count):
                h = _make_hazard_combatant(entry)
                if count > 1:
                    h.name = f"{h.name} {i+1}"
                ACTIVE_ENCOUNTER.append(h)
            continue
        for i in range(count):
            if path in MONSTER_LIBRARY:
                new_c = copy.deepcopy(MONSTER_LIBRARY[path])
                new_c.instance_id = str(uuid.uuid4())
                if count > 1:
                    new_c.name = f"{new_c.name} {i+1}"
                if elite_weak != 0:
                    new_c.apply_elite_weak(elite_weak)
                ACTIVE_ENCOUNTER.append(new_c)

    # Add party if requested
    if add_party:
        for pc_name, pc in PARTY_LIBRARY.items():
            new_c = copy.deepcopy(pc)
            new_c.instance_id = str(uuid.uuid4())
            ACTIVE_ENCOUNTER.append(new_c)

    return jsonify({"success": True, "combatant_count": len(ACTIVE_ENCOUNTER)})

@app.route('/api/party_stats')
def api_party_stats():
    """Return passive stats for all PCs for the GM tracker panel."""
    stats = []
    for name, pc in sorted(PARTY_LIBRARY.items()):
        stats.append({
            'name': pc.name, 'level': pc.level, 'ac': pc.ac,
            'perception': pc.perception, 'fort': pc.fort, 'ref': pc.ref, 'will': pc.will,
            'hp': pc.hp, 'current_hp': pc.current_hp, 'speed': pc.active_speed
        })
    return jsonify({"party": stats})

@app.route('/api/monster_statblock/<instance_id>')
def api_monster_statblock(instance_id):
    """Return full monster stat block for the popup modal."""
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and not c.is_pc:
            return jsonify({
                'name': c.name, 'level': c.level,
                'hp': c.hp, 'current_hp': c.current_hp,
                'ac': c.ac, 'base_ac': c.base_ac,
                'fort': c.fort, 'ref': c.ref, 'will': c.will,
                'perception': c.perception, 'speed': c.speed,
                'immunities': getattr(c, 'immunities', []),
                'resistances': getattr(c, 'resistances', []),
                'weaknesses': getattr(c, 'weaknesses', []),
                'traits': getattr(c, 'traits', []),
                'strikes': c.strikes,
                'actions': [{'name': a['name'], 'description': a.get('description', '')} for a in c.actions],
                'conditions': {k: v for k, v in c.conditions.items() if v},
                'persistent_damage': getattr(c, 'persistent_damage', ''),
                'elite_weak': getattr(c, 'elite_weak', 0)
            })
    return jsonify({"error": "Not found"}), 404

@app.route('/generator')
@gm_required
def dm_generator():
    # System guard: the PF2e (Golarion) generators don't belong to a Cosmere
    # table; redirect to the Rosharan generators.
    if _active_system() == 'cosmere':
        return redirect(url_for('cosmere_generator'))
    # Honor explicit ?biome / ?level overrides so a refresh-after-tweak
    # restores the GM's chosen context. Default biome was previously
    # hardcoded "City", which silently overrode every initial card on
    # page load — the GM had to click reroll-all to get the biome they
    # actually picked.
    default_level = max([p.level for p in PARTY_LIBRARY.values()]) if PARTY_LIBRARY else 1
    party_level = request.args.get('level', type=int) or default_level
    biome = request.args.get('biome', 'City')
    gen_types = ['npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter', 'weather', 'trap', 'rumor', 'settlement', 'treasure_hoard', 'random_event', 'faction', 'deity', 'villain', 'dungeon_room', 'travel_encounter']
    data = {}
    for k in gen_types:
        try:
            data[k] = getattr(pf2e_gen, f'get_{k}')(level=party_level, biome=biome)
        except AttributeError:
            data[k] = f"<em>Generator '{k}' not available.</em>"
    return render_template('generator.html', data=data, current_level=party_level, current_biome=biome)

@app.route('/api/generate/<element_type>', methods=['POST'])
def api_generate(element_type):
    if element_type not in VALID_GENERATOR_TYPES:
        return jsonify({'error': 'Invalid generator type'}), 400
    data = request.get_json()
    return jsonify({'html': getattr(pf2e_gen, f'get_{element_type}')(int(data.get('level', 1)), data.get('biome', 'City'))})

# --- Pinned generators --- (PINNED_GENERATORS_FILE bound in _bind_campaign_paths)

def _load_pinned_generators():
    if os.path.exists(PINNED_GENERATORS_FILE):
        try:
            with open(PINNED_GENERATORS_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []

def _save_pinned_generators(pins):
    os.makedirs(os.path.dirname(PINNED_GENERATORS_FILE) or '.', exist_ok=True)
    with open(PINNED_GENERATORS_FILE, 'w') as f:
        json.dump(pins, f, indent=2)

@app.route('/api/generator/pins', methods=['GET'])
@gm_required
def api_list_pinned_generators():
    return jsonify({'pins': _load_pinned_generators()})

@app.route('/api/generator/pin', methods=['POST'])
@gm_required
def api_pin_generator():
    from datetime import datetime as _dt
    data = request.get_json()
    if not data or 'type' not in data or 'content' not in data:
        return jsonify({'error': 'Missing type or content'}), 400
    pin_type = data['type']
    if pin_type not in VALID_GENERATOR_TYPES:
        return jsonify({'error': 'Invalid generator type'}), 400
    pins = _load_pinned_generators()
    new_pin = {
        'id': str(uuid.uuid4()),
        'type': pin_type,
        'content': data['content'],
        'pinned_at': _dt.utcnow().isoformat() + 'Z',
    }
    pins.insert(0, new_pin)
    _save_pinned_generators(pins)
    return jsonify({'pin': new_pin})

@app.route('/api/generator/pin/<pin_id>', methods=['DELETE'])
@gm_required
def api_unpin_generator(pin_id):
    pins = _load_pinned_generators()
    pins = [p for p in pins if p.get('id') != pin_id]
    _save_pinned_generators(pins)
    return jsonify({'ok': True})

@app.route('/m')
@app.route('/mobile')
def mobile_combat():
    """Mobile-optimized combat view for players on phones."""
    pname = session.get('player_name', '')
    if not pname:
        return redirect('/player')
    pc = PARTY_LIBRARY.get(pname)
    if not pc:
        return redirect('/player')

    # Active conditions (non-zero / non-False only)
    conditions = {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False}

    # Attack data for quick-roll buttons
    attacks = []
    for a in getattr(pc, 'attacks', []):
        strikes = a.get('strikes', [])
        attacks.append({
            'name': a.get('name', 'Strike'),
            'strikes': strikes,
            'damage': a.get('damage', ''),
            'traits': a.get('traits', []),
        })

    # Saves + perception + AC
    saves = {
        'fortitude': pc.fort,
        'reflex': pc.ref,
        'will': pc.will,
    }
    perception = pc.perception

    return render_template('mobile_combat.html',
        pc=pc, player_name=pname,
        conditions=conditions,
        attacks=attacks, saves=saves,
        perception=perception)

@app.route('/player')
def player_view():
    # System guard: a Cosmere player who lands on /player (bookmark / stale link)
    # goes to their Cosmere hub instead of the empty PF2e party picker.
    if _active_system() == 'cosmere':
        return redirect(url_for('cosmere_player_hub'))
    # Owner-aware landing (account mode): a player who owns exactly one PC in this
    # campaign goes straight to their sheet instead of a 4-tile party picker --
    # parity with the Cosmere player hub.
    if _account_mode():
        _u = _auth.current_user()
        if _u:
            mine = _my_pc_names(_u['id'])
            if len(mine) == 1:
                from urllib.parse import quote
                return redirect('/player/sheet/' + quote(mine[0]))
    # Sync from disk to catch any characters added outside this process
    _sync_party_from_disk()
    # Pass the campaign config so the hub can render the same hero band
    # (splash, gilt title, session pill) the homepage uses — the player
    # arriving here from the campaign_intro shouldn't visually leave the
    # brand world. Falls back to defaults if no campaign.json exists.
    return render_template(
        'player_view.html',
        party=list(PARTY_LIBRARY.values()),
        campaign=_load_campaign_config(),
        current_player=session.get('player_name'),
    )

_PARTY_DIR_MTIME_CACHE = {}  # filename -> last-seen mtime
_PARTY_DIR_LISTING_MTIME = 0  # mtime of PARTY_DIR itself, to skip listdir

def _sync_party_from_disk():
    """Ensure PARTY_LIBRARY matches what's on disk. Adds missing characters,
    removes deleted ones.

    Optimized for the steady-state case where 5 concurrent clients hit the
    landing page during a single live session: a per-file mtime cache lets
    us skip the JSON parse when nothing on disk has changed. The directory's
    own mtime gates the listdir so the common 'no churn' case is a single
    stat() call.
    """
    global _PARTY_DIR_LISTING_MTIME
    if not os.path.exists(PARTY_DIR): return

    try:
        dir_mtime = os.stat(PARTY_DIR).st_mtime
    except OSError as e:
        print(f"[SYNC ERROR] Failed to stat party directory: {e}")
        return

    # Fast path: directory mtime unchanged AND we've cached at least one file
    # already → nothing was added/removed/touched, library is current.
    if _PARTY_DIR_MTIME_CACHE and dir_mtime == _PARTY_DIR_LISTING_MTIME:
        return

    try:
        disk_files = {f for f in os.listdir(PARTY_DIR) if f.endswith('.json')}
    except OSError as e:
        print(f"[SYNC ERROR] Failed to list party directory: {e}")
        return

    disk_names = set()
    load_errors = []

    for f in disk_files:
        file_path = os.path.join(PARTY_DIR, f)
        try:
            file_mtime = os.stat(file_path).st_mtime
        except OSError:
            file_mtime = 0
        prev_mtime = _PARTY_DIR_MTIME_CACHE.get(f)

        # Per-file fast path: if mtime hasn't changed AND we already have the
        # PC loaded, just collect its name(s) for the disk_names set so the
        # delete-pass below doesn't drop it. We can't extract the name without
        # opening the file, so probe PARTY_LIBRARY by file_path first.
        if prev_mtime == file_mtime:
            for name, pc in PARTY_LIBRARY.items():
                if getattr(pc, 'file_path', None) == f:
                    disk_names.add(name)
            continue

        data, err = safe_load_json_file(file_path)
        if err:
            load_errors.append(f"[SYNC ERROR] {f}: {err}")
            continue

        try:
            if isinstance(data, list):
                for item in data:
                    name = (item.get('build') or item).get('name')
                    if name:
                        disk_names.add(name)
                        if name not in PARTY_LIBRARY:
                            PARTY_LIBRARY[name] = Character(item, f)
            else:
                name = (data.get('build') or data).get('name')
                if name:
                    disk_names.add(name)
                    if name not in PARTY_LIBRARY:
                        PARTY_LIBRARY[name] = Character(data, f)
            _PARTY_DIR_MTIME_CACHE[f] = file_mtime
        except Exception as e:
            load_errors.append(f"[SYNC ERROR] Failed to load character from {f}: {e}")

    # Log any errors encountered
    for err in load_errors:
        print(err)

    # Remove characters from memory that were deleted from disk
    for name in list(PARTY_LIBRARY.keys()):
        if name not in disk_names:
            del PARTY_LIBRARY[name]
    # Drop mtime entries for files that no longer exist
    for cached in list(_PARTY_DIR_MTIME_CACHE.keys()):
        if cached not in disk_files:
            _PARTY_DIR_MTIME_CACHE.pop(cached, None)

    _PARTY_DIR_LISTING_MTIME = dir_mtime
    _build_pc_file_cache()

@app.route('/player/sheet/<pc_name>')
def player_sheet(pc_name):
    if pc_name in PARTY_LIBRARY:
        # Claim this character for the browser session so rolls broadcast
        # under the PC's name instead of the generic "Player" fallback in
        # /api/log_roll. Mirrors /api/register_player's validation (only a
        # real party member, never "GM"/NPC). GMs keep their own identity —
        # they roll as whoever they pick in the tracker.
        if not _is_gm():
            session['player_name'] = pc_name
        return render_template('player_sheet.html', pc=PARTY_LIBRARY[pc_name], weapons_json=json.dumps(BUILDER_WEAPONS), builder_armor=BUILDER_ARMOR, armor_json=json.dumps(BUILDER_ARMOR), spells_json=json.dumps([{'name': s['name'], 'level': s['level'], 'traditions': s['traditions']} for s in BUILDER_SPELLS]), party_names=list(PARTY_LIBRARY.keys()))
    return redirect(url_for('player_view'))

@app.route('/api/player_state')
def player_state():
    """Polling fallback for the player encounter viewer.

    When a GM marks an NPC hidden (via the tracker's Hide button), this
    endpoint replaces its name with '???' and strips HP/conditions. The
    active_name banner is also scrubbed when the active combatant is hidden.
    GM sessions get the raw data so this endpoint is usable for admin views.
    """
    gm_view = _is_gm()
    state = []
    active_c = ACTIVE_ENCOUNTER[TURN_INDEX] if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
    active_name = active_c.name if active_c else None
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        # Legacy skip: creatures with hidden/undetected conditions never
        # appeared in the old player state either. Keep for back-compat.
        if not c.is_pc and (c.conditions.get('hidden') or c.conditions.get('undetected')): continue
        is_hidden = (not c.is_pc and not getattr(c, 'visible_to_players', True))
        if is_hidden and not gm_view:
            # Emit a placeholder row so players know *something* is in the
            # order without learning its identity.
            state.append({
                'name': '???',
                'initiative': None,
                'is_pc': False,
                'is_active': (i == TURN_INDEX),
                'conditions': {},
                'hp_status': '',
                'hp_color': '',
                'hidden': True,
            })
            continue
        safe_c = { 'name': c.name, 'initiative': c.initiative, 'is_pc': c.is_pc, 'is_active': (i == TURN_INDEX), 'conditions': {k: v for k, v in c.conditions.items() if v} }
        if c.is_pc:
            pct = c.current_hp / c.hp if c.hp > 0 else 0
            if c.current_hp == 0: status, color = "Unconscious", "text-red-600"
            elif pct <= 0.25: status, color = "Critical", "text-red-400"
            elif pct <= 0.5: status, color = "Bloodied", "text-orange-400"
            else: status, color = "Healthy", "text-green-400"
            safe_c['hp_status'], safe_c['hp_color'] = status, color
            safe_c['current_hp'] = c.current_hp
            safe_c['max_hp'] = c.hp
            safe_c['hp_pct'] = round(pct * 100)
        else:
            pct = c.current_hp / c.hp if c.hp > 0 else 0
            if c.current_hp == 0: safe_c['hp_status'] = "Dead"
            elif pct <= 0.5: safe_c['hp_status'] = "Wounded"
            else: safe_c['hp_status'] = ""
            safe_c['hp_color'] = "text-red-400" if c.current_hp == 0 else "text-orange-400" if pct <= 0.5 else ""
        state.append(safe_c)
    # Mask active_name if the active combatant is a hidden NPC (turn banner).
    if active_c and not gm_view and not active_c.is_pc and not getattr(active_c, 'visible_to_players', True):
        active_name = '???'
    return jsonify({'encounter': state, 'round': ROUND_NUMBER, 'active_name': active_name})

@app.route('/api/gm_party_state')
def gm_party_state():
    """Full party state for GM party view — includes spell slots, conditions, HP, attacks."""
    party = []
    for pc_name, pc in PARTY_LIBRARY.items():
        pct = (pc.current_hp / pc.hp * 100) if pc.hp > 0 else 0
        # Get expended slots from disk
        expended_slots = {}
        try:
            fp = get_pc_file_path(pc_name)
            if fp and os.path.exists(fp):
                with open(fp, 'r', encoding='utf-8') as f:
                    build = json.load(f).get('build', {})
                    expended_slots = build.get('expended_slots', {})
        except Exception:
            pass
        # Build spell data
        spell_casters = []
        for ci, caster in enumerate(getattr(pc, 'spell_casters', [])):
            cdata = {'name': caster.get('name', ''), 'tradition': caster.get('tradition', ''), 'levels': []}
            for lvl in caster.get('levels', []):
                slots = lvl.get('slots', 0)
                spells_in_level = [{'name': s.get('name', '')} for s in lvl.get('spells', [])]
                # Count expended
                expended_count = sum(1 for si in range(max(slots, len(spells_in_level))) if expended_slots.get(f"{ci}-{lvl.get('level',0)}-{si}"))
                cdata['levels'].append({
                    'level': lvl.get('level', 0), 'label': lvl.get('label', ''),
                    'slots': slots, 'expended': expended_count,
                    'spells': spells_in_level
                })
            spell_casters.append(cdata)
        pc_data = {
            'name': pc_name, 'class_name': pc.class_name, 'ancestry': pc.ancestry,
            'subclass': getattr(pc, 'subclass', ''), 'level': pc.level,
            'current_hp': pc.current_hp, 'max_hp': pc.hp, 'hp_pct': round(pct),
            'ac': pc.ac, 'fort': pc.fort, 'ref': pc.ref, 'will': pc.will,
            'perception': pc.perception, 'speed': getattr(pc, 'active_speed', getattr(pc, 'speed', 25)),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'focus': getattr(pc, 'current_focus', 0), 'focus_max': getattr(pc, 'focus_max', 0),
            'hero_points': getattr(pc, 'hero_points', 1),
            'spell_casters': spell_casters,
            'portrait': getattr(pc, 'portrait', None),
            'portrait_focus': {
                'x': getattr(pc, 'portrait_focus_x', 50.0),
                'y': getattr(pc, 'portrait_focus_y', 50.0),
            },
            'attacks': [{'name': a['name'], 'hit': a['strikes'][0]['label'] if a.get('strikes') else '+?', 'damage': a['damage']} for a in getattr(pc, 'attacks', [])],
            'mods': getattr(pc, 'mods', {}),
            # Phase 10: GM party view shows each PC's current exploration activity.
            'exploration_activity': str(getattr(pc, 'exploration_activity', '') or ''),
            # Phase 11: shield details for the GM party card.
            'shield_name': getattr(pc, 'shield_name', '') or '',
            'shield_hp': int(getattr(pc, 'shield_hp', 0) or 0),
            'shield_max_hp': int(getattr(pc, 'shield_max_hp', 0) or 0),
            'shield_hardness': int(getattr(pc, 'shield_hardness', 0) or 0),
            'shield_broken': bool(getattr(pc, 'shield_broken', False)),
            'shield_destroyed': bool(getattr(pc, 'shield_destroyed', False)),
        }
        party.append(pc_data)
    return jsonify({'party': party})

@app.route('/api/events')
def sse_stream():
    """Server-Sent Events stream for real-time updates to player sheets."""
    # Lazily start the keepalive broadcaster on the first SSE connection.
    # Doing it here (instead of at module import) means the thread only
    # exists when actually needed and avoids subtle issues with gunicorn
    # forking workers — the thread starts inside the worker that owns it.
    _ensure_sse_keepalive()
    # Resolve GM status from the live Flask session at connect time. SSE
    # connections are long-lived; the session state captured here is what
    # sse_broadcast() uses to decide whether this subscriber gets raw GM
    # data or the player-sanitized view.
    is_gm = _is_gm()
    # On reconnect the client tells us the last event it saw (the hub passes it as
    # ?last_event_id=, native EventSource as the Last-Event-ID header); we replay
    # any events it missed from the ring buffer so the sheet never shows stale data.
    try:
        last_seen = int(request.args.get('last_event_id') or request.headers.get('Last-Event-ID') or 0)
    except (TypeError, ValueError):
        last_seen = 0

    def generate():
        q = queue.Queue(maxsize=50)
        entry = (q, is_gm)
        with _sse_lock:
            # Enforce max subscriber cap. Reap dead subscribers first: a full
            # queue means that client stopped draining (closed/asleep tab), so we
            # evict those zombies before ever dropping a live connection. Only if
            # we're still at the cap after reaping do we drop the oldest.
            if len(_sse_subscribers) >= _SSE_MAX_SUBSCRIBERS:
                _sse_subscribers[:] = [s for s in _sse_subscribers if not s[0].full()]
            if len(_sse_subscribers) >= _SSE_MAX_SUBSCRIBERS:
                _sse_subscribers.pop(0)
            _sse_subscribers.append(entry)
            # Snapshot the events to replay: everything after last_seen up to the
            # id at subscribe time. Events newer than this arrive live via the
            # queue (we subscribed first), so there's no gap and no duplicate.
            start_id = _sse_event_seq
            replay = ([(i, gm, pl) for (i, gm, pl) in _sse_buffer if last_seen < i <= start_id]
                      if last_seen else [])
        try:
            yield "event: connected\ndata: {}\n\n"
            for (i, gm_frame, pl_frame) in replay:
                frame = gm_frame if is_gm else pl_frame
                if frame is not None:
                    yield frame
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield msg
                except queue.Empty:
                    yield ": heartbeat\n\n"  # Keep connection alive
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if entry in _sse_subscribers:
                    _sse_subscribers.remove(entry)

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive'
    })

@app.route('/api/party_list')
def party_list():
    """Simple list of party member names for vault export."""
    return jsonify({"party": [{"name": pc.name} for pc in PARTY_LIBRARY.values()]})

@app.route('/api/combatant_stats/<instance_id>')
def combatant_stats(instance_id):
    """Return full stat block for a combatant in the encounter (for GM popup)."""
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id:
            data = {
                'name': c.name, 'level': c.level, 'is_pc': c.is_pc,
                'ac': c.ac, 'fort': c.fort, 'ref': c.ref, 'will': c.will,
                'perception': c.perception, 'speed': getattr(c, 'active_speed', getattr(c, 'speed', 25)),
                'current_hp': c.current_hp, 'max_hp': c.hp,
                'conditions': {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False},
            }
            if c.is_pc:
                data['class_name'] = c.class_name
                data['ancestry'] = c.ancestry
                data['subclass'] = getattr(c, 'subclass', '')
                data['abilities'] = c.mods
                data['attacks'] = [{'name': a['name'], 'strikes': a.get('strikes', []), 'damage': a['damage']} for a in c.attacks]
                data['skills'] = c.skills
                data['spell_casters'] = c.spell_casters
            else:
                data['attacks'] = [{'name': s.get('name', ''), 'hit': (lambda b: f"+{b}" if b >= 0 else str(b))(s.get('bonus', s.get('mod', 0))), 'damage': s.get('damage', '')} for s in c.strikes]
                data['actions'] = [{'name': a['name'], 'description': a.get('description', '')} for a in c.actions]
                data['immunities'] = getattr(c, 'immunities', [])
                data['resistances'] = getattr(c, 'resistances', [])
                data['weaknesses'] = getattr(c, 'weaknesses', [])
                data['traits'] = getattr(c, 'traits', [])
                data['elite_weak'] = getattr(c, 'elite_weak', 0)
            return jsonify(data)
    
    # Not in encounter — check party library
    for name, pc in PARTY_LIBRARY.items():
        if name == instance_id:
            return jsonify({
                'name': pc.name, 'level': pc.level, 'is_pc': True,
                'class_name': pc.class_name, 'ancestry': pc.ancestry, 'subclass': getattr(pc, 'subclass', ''),
                'ac': pc.ac, 'fort': pc.fort, 'ref': pc.ref, 'will': pc.will,
                'perception': pc.perception, 'speed': pc.active_speed,
                'current_hp': pc.current_hp, 'max_hp': pc.hp,
                'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
                'abilities': pc.mods,
                'attacks': [{'name': a['name'], 'strikes': a.get('strikes', []), 'damage': a['damage']} for a in pc.attacks],
                'skills': pc.skills,
                'spell_casters': pc.spell_casters,
            })
    
    return jsonify({"error": "Combatant not found"}), 404

@app.route('/api/log_roll', methods=['POST'])
def log_roll():
    data = request.json
    from datetime import datetime
    # Phase 7: clients may supply a precomputed PF2e degree of success
    # ('crit_success'|'success'|'failure'|'crit_failure') for known-DC rolls.
    # We just forward it through to the log + broadcast so GM toasts + combat
    # log can render a degree banner without re-deriving the math server-side.
    degree = data.get('degree')
    if degree not in ('crit_success', 'success', 'failure', 'crit_failure'):
        degree = None
    # Pin the actor name to the caller's session for non-GM callers — without
    # this a player could POST {"name": "GM"} and inject fake rolls into the
    # combat log. The GM can roll as anyone (NPCs etc.).
    if _is_gm():
        actor_name = data.get('name', 'Player')
    else:
        actor_name = session.get('player_name') or 'Player'
    log_entry = {
        'id': str(uuid.uuid4()),
        'name': actor_name,
        'action': data.get('action', 'Action'),
        'result': data.get('result', ''),
        'detail': data.get('detail', ''),
        'degree': degree,
        'time': datetime.now().strftime('%H:%M:%S'),
        'round': ROUND_NUMBER
    }
    COMBAT_LOGS.append(log_entry)
    if len(COMBAT_LOGS) > 200: COMBAT_LOGS.pop(0)

    # Session-scrapbook hook (Chunk 6): tally crits / nat-1s for party PCs.
    try:
        _record_crit_fumble(actor_name, log_entry['action'], log_entry['detail'], degree)
    except Exception:
        pass

    # Campaign stats hook (Tier 4, feature 30)
    try:
        _bump_campaign_stat('total_rolls')
        if degree == 'crit_success':
            _bump_campaign_stat('total_crits')
        elif degree == 'crit_failure':
            _bump_campaign_stat('total_fumbles')
    except Exception:
        pass

    # Broadcast to all connected clients so everyone sees each other's rolls.
    # Hidden-NPC names in the detail/action/result fields (e.g. "vs GhoulPriest")
    # get scrubbed for player subscribers via the player_filter callback.
    broadcast_payload = {
        'name': log_entry['name'],
        'action': log_entry['action'],
        'result': log_entry['result'],
        'detail': log_entry['detail'],
        'degree': log_entry['degree'],
        'time': log_entry['time']
    }

    def _roll_player_filter(p):
        hidden = _hidden_npc_names()
        if not hidden:
            return p
        for key in ('name', 'action', 'result', 'detail'):
            if key in p:
                p[key] = _scrub_hidden_names(p.get(key), hidden)
        return p

    sse_broadcast('player_roll', broadcast_payload, player_filter=_roll_player_filter)

    return jsonify({"success": True})

@app.route('/api/log_spell_cast', methods=['POST'])
def log_spell_cast():
    """Slice 2 (tableview_3d): emit a 'player_cast' SSE frame so the 3D renderer
    can play a cast animation + suppress the inferred melee attack. Additive."""
    data = request.form if request.form else (request.json or {})
    if _is_gm():
        actor_name = data.get('name', 'Player')
    else:
        actor_name = session.get('player_name') or 'Player'
    spell_name = data.get('spell_name', '')
    try:
        level = int(data.get('level')) if data.get('level') not in (None, '') else None
    except (TypeError, ValueError):
        level = None
    payload = {
        'name': actor_name,
        'spell_name': spell_name,
        'level': level,
        'result': data.get('result', ''),
        'detail': data.get('detail', ''),
        't': int(time.time()),
    }

    def _cast_player_filter(p):
        hidden = _hidden_npc_names()
        if not hidden:
            return p
        for key in ('name', 'spell_name', 'detail'):
            if key in p:
                p[key] = _scrub_hidden_names(p.get(key), hidden)
        return p

    sse_broadcast('player_cast', payload, player_filter=_cast_player_filter)
    return jsonify({"success": True})

@app.route('/api/get_logs')
def get_logs():
    last_id = request.args.get('last_id')
    if not last_id:
        return jsonify({'logs': _scrub_log_entries_for_players(COMBAT_LOGS[-5:])})
    idx = next((i for i, log in enumerate(COMBAT_LOGS) if log['id'] == last_id), -1)
    if idx != -1:
        return jsonify({'logs': _scrub_log_entries_for_players(COMBAT_LOGS[idx+1:])})
    return jsonify({'logs': _scrub_log_entries_for_players(COMBAT_LOGS[-5:])})

@app.route('/api/get_full_log')
def get_full_log():
    """Return the complete combat log for the history panel.

    Hidden-NPC names are scrubbed for player sessions.
    """
    return jsonify({'logs': _scrub_log_entries_for_players(list(reversed(COMBAT_LOGS)))})

@app.route('/api/clear_log', methods=['POST'])
def clear_log():
    COMBAT_LOGS.clear()
    return jsonify({"success": True})

# --- COMPENDIUM DB CONNECTION POOL ---
# With threaded=True, each Flask worker thread gets its own sqlite3 connection.
# We keep it open across requests (thread-local) so we skip the connect/close
# overhead on every compendium lookup.
import sqlite3 as _sqlite3
_compendium_tls = threading.local()

def _get_compendium_db():
    conn = getattr(_compendium_tls, 'conn', None)
    if conn is not None:
        return conn
    db_path = os.path.join(BASE_DIR, 'pf2e_database.db')
    if not os.path.exists(db_path):
        return None
    conn = _sqlite3.connect(db_path)
    conn.row_factory = _sqlite3.Row
    _compendium_tls.conn = conn
    return conn

# ── PF2e Conditions (fixed reference set) ──────────────────────────────
# Not in the compendium DB; kept in-memory for quick-search and GM screen.
CONDITION_REFERENCE = {
    'blinded': {'desc': 'All terrain is difficult terrain. You fail Perception checks requiring sight. Immune to visual effects. -4 status penalty to Perception checks. Attacks have the flat-footed condition against you.'},
    'broken': {'desc': 'Cannot be used for its normal function until repaired. An item is broken when damage reduces its HP below its BT.'},
    'clumsy': {'desc': 'Penalty to Dex-based checks and DCs, including AC, Reflex saves, and Dex-based attack rolls.'},
    'concealed': {'desc': 'DC 5 flat check to target with attacks, spells, or other effects.'},
    'confused': {'desc': 'You are flat-footed, cannot Delay or Ready, and must use all actions to Strike or cast offensive cantrips at the nearest creature.'},
    'controlled': {'desc': 'Another creature decides your actions. You gain no actions of your own.'},
    'dazzled': {'desc': 'DC 5 flat check to target concealed creatures. All creatures are concealed to you.'},
    'deafened': {'desc': 'You automatically critically fail Perception checks requiring hearing. -2 status penalty to Perception and initiative. If you Cast a Spell with verbal component, DC 5 flat check or the spell is lost.'},
    'doomed': {'desc': 'Your dying value threshold is reduced. You die at dying 4 minus your doomed value.'},
    'drained': {'desc': 'Penalty to Constitution-based checks. Lose HP equal to level times drained value. Cannot recover these HP until drained is reduced.'},
    'dying': {'desc': 'You are unconscious. You must make recovery checks at the start of each turn. At dying 4 (or lower if doomed), you die.'},
    'encumbered': {'desc': '10-foot penalty to all Speeds and clumsy 1.'},
    'enfeebled': {'desc': 'Penalty to Strength-based checks and DCs, including melee attack rolls, damage rolls, and Athletics checks.'},
    'fascinated': {'desc': 'Cannot use hostile actions. Penalty to Perception and skill checks. Cannot voluntarily move closer to the source.'},
    'fatigued': {'desc': '-1 status penalty to AC and saving throws. Cannot reduce below fatigued. Recover after a full night rest.'},
    'flat-footed': {'desc': '-2 circumstance penalty to AC.'},
    'fleeing': {'desc': 'Must spend actions to move away from the source of fleeing. Cannot Delay or Ready.'},
    'frightened': {'desc': 'Status penalty to all checks and DCs. Decreases by 1 at end of each turn.'},
    'grabbed': {'desc': 'You are immobilized and flat-footed. If you attempt a manipulate action, you must succeed at a DC 5 flat check or the action is lost.'},
    'hidden': {'desc': 'Creatures that cannot perceive you are flat-footed to you. DC 11 flat check when targeting you.'},
    'immobilized': {'desc': 'Cannot use any action with the move trait.'},
    'invisible': {'desc': 'You cannot be seen. You are undetected to everyone. DC 11 flat check to target. You are not flat-footed to anyone.'},
    'observed': {'desc': 'You are clearly visible. No special benefits or penalties.'},
    'off-guard': {'desc': '-2 circumstance penalty to AC. (Remaster name for flat-footed)'},
    'paralyzed': {'desc': 'You are flat-footed and cannot act. Your body is immobile.'},
    'petrified': {'desc': 'Turned to stone. You cannot act or sense anything. Immune to damage but not destruction.'},
    'prone': {'desc': 'Flat-footed and -2 circumstance penalty to attack rolls. Must Stand (1 action) to remove. Melee attacks gain +2 vs you; ranged attacks take -2.'},
    'quickened': {'desc': 'Gain 1 extra action each turn. Many effects limit what the extra action can be used for.'},
    'restrained': {'desc': 'You are immobilized and flat-footed. You cannot use actions with the attack or manipulate trait except to attempt to Escape.'},
    'sickened': {'desc': 'Status penalty to all checks and DCs. You cannot willingly ingest anything. Can retch as an action to attempt a Fortitude save to reduce.'},
    'slowed': {'desc': 'Lose actions at the start of your turn equal to slowed value.'},
    'stunned': {'desc': 'Lose actions. Stunned value is reduced by actions lost. Similar to slowed but value decrements.'},
    'stupefied': {'desc': 'Penalty to Intelligence, Wisdom, and Charisma-based checks. When casting a spell, DC 5+stupefied flat check or the spell is lost.'},
    'unconscious': {'desc': 'You are flat-footed, cannot act or sense anything normally. You fall prone and drop held items.'},
    'undetected': {'desc': 'Creature does not know your location. DC 11 flat check to target your space. If target is wrong, attack is lost.'},
    'unfriendly': {'desc': 'The creature does not wish you well. Will not help willingly.'},
    'unnoticed': {'desc': 'Creature is unaware of your presence entirely.'},
    'wounded': {'desc': 'When you regain consciousness, increase dying by your wounded value. Wounded increases by 1 each time you gain dying. Resets on full rest.'},
}

@app.route('/api/condition_info/<name>')
def condition_info(name):
    """Return the mechanical description for a single PF2e condition."""
    key = name.lower().replace(' ', '-').replace('_', '-')
    data = CONDITION_REFERENCE.get(key)
    if not data:
        return jsonify({"error": "Unknown condition"}), 404
    return jsonify({"name": key.replace('-', ' ').title(), "desc": data['desc']})

@app.route('/api/compendium_search')
def compendium_search():
    """Search the PF2E compendium database across feats, spells, equipment, and conditions."""
    query = request.args.get('q', '').strip()
    category = request.args.get('cat', 'all')  # all, feats, spells, equipment, conditions
    if not query or len(query) < 2:
        return jsonify({"results": []})

    results = []

    # Conditions: in-memory lookup (no DB needed)
    if category in ('all', 'conditions'):
        q_lower = query.lower()
        for cname, cdata in CONDITION_REFERENCE.items():
            if q_lower in cname:
                results.append({
                    'type': 'condition',
                    'name': cname.replace('-', ' ').title(),
                    'level': None,
                    'meta': 'Condition',
                    'desc': cdata['desc']
                })

    conn = _get_compendium_db()
    if conn is None and category != 'conditions':
        return jsonify({"results": results, "error": "Database not found"})
    if conn is None:
        return jsonify({"results": results})
    c = conn.cursor()
    search_term = f"%{query}%"

    try:
        if category in ('all', 'spells'):
            c.execute("SELECT name, level, traditions, description FROM spells WHERE name LIKE ? ORDER BY level, name LIMIT 15", (search_term,))
            for row in c.fetchall():
                desc = (row['description'] or '')[:300]
                # Strip HTML tags for preview
                desc = re.sub(r'<[^>]+>', '', desc).strip()
                results.append({
                    'type': 'spell',
                    'name': row['name'],
                    'level': row['level'],
                    'meta': row['traditions'] or '',
                    'desc': desc
                })

        if category in ('all', 'feats'):
            c.execute("SELECT name, category, level, traits, description FROM feats WHERE name LIKE ? AND level IS NOT NULL ORDER BY level, name LIMIT 15", (search_term,))
            for row in c.fetchall():
                desc = (row['description'] or '')[:300]
                desc = re.sub(r'<[^>]+>', '', desc).strip()
                results.append({
                    'type': 'feat',
                    'name': row['name'],
                    'level': row['level'],
                    'meta': row['category'] or '',
                    'desc': desc
                })

        if category in ('all', 'equipment'):
            c.execute("SELECT name, type, level, traits, description FROM equipment WHERE name LIKE ? AND type NOT IN ('effect', 'consumable') ORDER BY level, name LIMIT 15", (search_term,))
            for row in c.fetchall():
                desc = (row['description'] or '')[:300]
                desc = re.sub(r'<[^>]+>', '', desc).strip()
                results.append({
                    'type': 'item',
                    'name': row['name'],
                    'level': row['level'],
                    'meta': row['type'] or '',
                    'desc': desc
                })
    except Exception as e:
        return jsonify({"results": [], "error": str(e)})
    finally:
        c.close()

    # Sort: exact name matches first, then by level
    q_lower = query.lower()
    results.sort(key=lambda r: (0 if r['name'].lower() == q_lower else 1 if r['name'].lower().startswith(q_lower) else 2, r['level'] or 0))
    return jsonify({"results": results[:30]})

@app.route('/api/compendium_detail')
def compendium_detail():
    """Get full description for a compendium entry."""
    name = request.args.get('name', '').strip()
    entry_type = request.args.get('type', '')
    if not name:
        return jsonify({"error": "No name provided"}), 400

    # Conditions are in-memory, no DB query needed
    if entry_type == 'condition':
        key = name.lower().replace(' ', '-')
        cdata = CONDITION_REFERENCE.get(key)
        if cdata:
            return jsonify({'name': name, 'type': 'condition', 'description': cdata['desc'], 'meta': 'Condition'})
        return jsonify({"error": "Not found"}), 404

    conn = _get_compendium_db()
    if conn is None:
        return jsonify({"error": "Database not found"}), 404
    c = conn.cursor()

    try:
        if entry_type == 'spell':
            c.execute("SELECT * FROM spells WHERE name = ?", (name,))
        elif entry_type == 'feat':
            c.execute("SELECT * FROM feats WHERE name = ?", (name,))
        elif entry_type == 'item':
            c.execute("SELECT * FROM equipment WHERE name = ?", (name,))
        else:
            return jsonify({"error": "Invalid type"}), 400

        row = c.fetchone()
        if row:
            return jsonify(dict(row))
        return jsonify({"error": "Not found"}), 404
    finally:
        c.close()

@app.route('/api/long_rest/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def long_rest(pc_name):
    if pc_name in PARTY_LIBRARY:
        pc = PARTY_LIBRARY[pc_name]

        # PF2E Rest Rules (Core Rulebook p.480 "Resting"):
        # - HP regained = Con modifier (minimum 1) × level, capped at max HP.
        #   You do NOT wake with full HP. Treat Wounds / other healing is
        #   applied separately during rest; this endpoint models only the
        #   base rest-recovery rule.
        # - Wounded: clears entirely after full night's rest
        # - Drained: reduces by 1 (not cleared)
        # - Doomed: does NOT change from rest (only specific effects remove it)
        # - Fatigued: clears after rest (explicit rule)
        # - All other short-duration conditions (stunned, slowed, stupefied,
        #   enfeebled, clumsy, frightened, sickened) expire after 8 hours —
        #   their typical durations are rounds/minutes, not days.
        # - Dying clears (you woke up, you aren't dying anymore)
        # - Focus Points refill to max (daily preparations).
        # - Hero Points are NOT restored by rest — per PF2e they reset at
        #   the start of each session (GM awards 1); they are intentionally
        #   left untouched here.

        # HP recovery: max(1, con_mod) * level, capped at max HP.
        try:
            con_mod = int(pc.mods.get('con', 0))
        except Exception:
            con_mod = 0
        hp_per_level = max(1, con_mod)
        hp_before = pc.current_hp
        hp_regained = hp_per_level * pc.level
        pc.current_hp = min(pc.hp, pc.current_hp + hp_regained)
        hp_actually_regained = pc.current_hp - hp_before

        pc.current_focus = pc.focus_max
        # Temp HP always fades after a rest — clear the manual pool so it
        # doesn't linger across days. Toggle-based temp HP refreshes with
        # the toggle (no action needed here).
        pc.temp_hp_manual = 0
        try:
            pc.temp_hp = pc.toggle_effects_summary.get('temp_hp', 0)
        except Exception:
            pc.temp_hp = 0
        drained_val = max(0, pc.conditions.get('drained', 0) - 1)
        doomed_val = pc.conditions.get('doomed', 0)  # Preserved

        pc.conditions = {
            'frightened': 0, 'sickened': 0, 'dying': 0, 'wounded': 0,
            'doomed': doomed_val, 'drained': drained_val,
            'fatigued': 0,
            'stunned': 0, 'slowed': 0, 'stupefied': 0,
            'enfeebled': 0, 'clumsy': 0,
            'prone': False, 'off_guard': False, 'concealed': False,
            'hidden': False, 'undetected': False
        }
        
        # Clear server-side spell slot tracking
        pc._spell_slots_refreshed = True
        # Snapshot post-rest values before disk roundtrip — save_and_reload_character
        # rebuilds Character from the saved JSON, so we MUST persist the new
        # HP/focus/temp_hp/conditions in the build dict here, otherwise the
        # in-memory mutations get stomped by the reload and the player wakes
        # up at their pre-rest HP.
        file_path = get_pc_file_path(pc_name)
        if file_path and os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
            build = pc_json.get('build', pc_json)
            build['expended_slots'] = {}
            build['prepared_spells'] = {}
            build['cast_prep'] = {}
            build['current_hp'] = pc.current_hp
            build['current_focus'] = pc.current_focus
            build['temp_hp'] = pc.temp_hp_manual
            build['conditions'] = dict(pc.conditions)
            build['reaction_used'] = bool(getattr(pc, 'reaction_used', False))
            build['shield_raised'] = bool(getattr(pc, 'shield_raised', False))
            save_and_reload_character(pc_name, pc_json, file_path)
            # Re-fetch the rebuilt PC reference for the broadcast below.
            pc = PARTY_LIBRARY[pc_name]

        # Sync to tracker
        in_encounter = False
        for c in ACTIVE_ENCOUNTER:
            if c.is_pc and c.name == pc_name:
                c.current_hp = pc.current_hp
                c.current_focus = pc.focus_max
                c.conditions = dict(pc.conditions)
                in_encounter = True

        # Broadcast so other players' party_view + GM tracker see the rest.
        _broadcast_pc_state(pc_name)
        if in_encounter:
            _broadcast_encounter_state()

        result = {"success": True, "restored": {
            "hp": pc.current_hp,
            "hp_max": pc.hp,
            "hp_regained": hp_actually_regained,
            "hp_per_level": hp_per_level,
            "con_mod": con_mod,
            "focus": pc.focus_max,
            "drained": drained_val, "doomed": doomed_val,
            "conditions_cleared": True
        }}
        return jsonify(result)
    return jsonify({"success": False})


# ════════════════════════════════════════════════════════════════════════
# Player → GM private channels (Wave 2 UX additions, 2026-04-24)
#
# All four of these endpoints accept a small JSON payload from a player
# sheet and broadcast it as a GM-only SSE event using the existing
# `player_filter=lambda d: None` pattern. Players never see each other's
# whispers or journal entries; the GM screen subscribes to the new event
# names and surfaces them in its existing inbox / log UI.
# ════════════════════════════════════════════════════════════════════════

# In-memory session log of healing events + session journal. Persisted
# to disk so a server bounce mid-session doesn't lose state. All writes
# guarded by a single lock since these are append-mostly + small.
#
# Path: prefer a writable data dir if RAILWAY mounts a volume there,
# otherwise fall back to the project dir. Failure to load OR save is
# non-fatal — the in-memory dicts still work, the disk just won't
# persist across restarts.
SESSION_STATE_LOCK = threading.Lock()
SESSION_HEALING_LOG = []  # list of dicts: {ts, healer, target, amount, source}
SESSION_JOURNAL = {}      # pc_name -> list of {ts, text}

def _resolve_session_state_path():
    candidates = [
        os.environ.get('SESSION_STATE_PATH'),
        os.path.join(os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', ''), 'session_state.json') if os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') else None,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'session_state.json'),
    ]
    for c in candidates:
        if not c: continue
        try:
            d = os.path.dirname(c) or '.'
            os.makedirs(d, exist_ok=True)
            # Probe writability without leaving a file behind.
            test = os.path.join(d, '.session_state_probe')
            with open(test, 'w') as f: f.write('ok')
            os.remove(test)
            return c
        except Exception:
            continue
    return None  # disk persistence disabled

_SESSION_STATE_PATH = _resolve_session_state_path()

def _save_session_state():
    """Persist healing log + journal to disk. Called after any mutation
    (which are infrequent — a few per session). Cheap: ~1KB JSON.
    Silent no-op if the path probe failed at boot."""
    if not _SESSION_STATE_PATH:
        return
    try:
        with SESSION_STATE_LOCK:
            payload = {
                'healing_log': SESSION_HEALING_LOG[-500:],
                'journal': {k: v[-100:] for k, v in SESSION_JOURNAL.items()},
            }
        with open(_SESSION_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"[SESSION_STATE] save failed: {e}")

def _load_session_state():
    """Restore healing log + journal on boot. Idempotent — safe to
    call multiple times; later calls just overwrite in-memory state."""
    global SESSION_HEALING_LOG, SESSION_JOURNAL
    if not _SESSION_STATE_PATH:
        return
    try:
        if not os.path.exists(_SESSION_STATE_PATH):
            return
        with open(_SESSION_STATE_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        with SESSION_STATE_LOCK:
            SESSION_HEALING_LOG[:] = payload.get('healing_log', []) or []
            SESSION_JOURNAL.clear()
            SESSION_JOURNAL.update(payload.get('journal', {}) or {})
    except Exception as e:
        print(f"[SESSION_STATE] load failed: {e}")
_load_session_state()

@app.route('/api/whisper/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def player_whisper(pc_name):
    """Player-only → GM-only message. Players never see other players'
    whispers; the GM screen surfaces them in its inbox. The URL pc_name
    is the *sender*; @require_pc_self_or_gm blocks Kyle from whispering
    AS Amadeus."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({"success": False, "error": "empty"}), 400
    text = text[:1000]  # hard cap
    import time as _t
    payload = {
        'pc_name': pc_name,
        'text': text,
        'ts': _t.time(),
    }
    sse_broadcast('whisper', payload, player_filter=lambda d: None)
    return jsonify({"success": True})


@app.route('/api/treat_wounds/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def treat_wounds_dispatch(pc_name):
    """Player rolled Treat Wounds — broadcast a GM-visible record so the
    GM applies the healing (and we keep a session log of total healing
    time + amounts for the optional 'how much table time on healing'
    summary)."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    data = request.get_json(silent=True) or {}
    target = (data.get('target') or '').strip()
    roll_total = data.get('roll_total')
    healing = data.get('healing')
    proficiency = (data.get('proficiency') or 'Trained').strip()
    dc = data.get('dc')
    success = data.get('success')  # crit_success / success / failure / crit_failure
    if not target or healing is None:
        return jsonify({"success": False, "error": "missing target or healing"}), 400
    import time as _t
    payload = {
        'healer': pc_name,
        'target': target,
        'roll_total': roll_total,
        'dc': dc,
        'success': success,
        'healing': healing,
        'proficiency': proficiency,
        'ts': _t.time(),
    }
    with SESSION_STATE_LOCK:
        SESSION_HEALING_LOG.append(payload)
        # Cap log length so a long-running server doesn't grow unboundedly.
        if len(SESSION_HEALING_LOG) > 500:
            del SESSION_HEALING_LOG[:len(SESSION_HEALING_LOG) - 500]
    _save_session_state()
    sse_broadcast('treat_wounds', payload, player_filter=lambda d: None)
    return jsonify({"success": True})


@app.route('/api/healing_log')
def healing_log_get():
    """GM-side fetch of the in-memory healing log."""
    return jsonify({"log": SESSION_HEALING_LOG})


@app.route('/api/session_journal/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def session_journal_add(pc_name):
    """End-of-session prompt — player jots a one-line summary, GM gets
    a private SSE event so they can paste it into their Obsidian vault.
    Also appended to in-memory log so the GM screen can render a 'this
    session's recap' panel even on hot-reload."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({"success": False, "error": "empty"}), 400
    text = text[:2000]
    import time as _t
    ts = _t.time()
    with SESSION_STATE_LOCK:
        SESSION_JOURNAL.setdefault(pc_name, []).append({'ts': ts, 'text': text})
    _save_session_state()
    sse_broadcast('journal_entry', {
        'pc_name': pc_name, 'text': text, 'ts': ts,
    }, player_filter=lambda d: None)
    return jsonify({"success": True})


@app.route('/api/session_journal')
def session_journal_get():
    """GM-side: get all journal entries this session, grouped by PC."""
    return jsonify({"journal": SESSION_JOURNAL})


# ════════════════════════════════════════════════════════════════════════
# PF2e spell-cast validation (Wave 3 — spell rules audit fix).
#
# Replaces the older "auto-expend at base rank" flow. Enforces RAW:
#   - Spontaneous: chosen slot rank must be >= spell's base rank, and
#     that specific slot must be unexpended. Cantrips and focus spells
#     are exempt (no slots).
#   - Prepared: spell must currently be PREPARED at the chosen slot
#     (i.e. the daily-prep stored mapping has this spell at that rank).
#     Cantrips exempt.
# Mutations to expended_slots are guarded by a per-PC lock so two clicks
# don't both grab the "first free" slot.
# ════════════════════════════════════════════════════════════════════════
_PC_SPELL_LOCKS = {}  # pc_name -> threading.Lock
_PC_SPELL_LOCKS_GUARD = threading.Lock()
def _pc_spell_lock(pc_name):
    with _PC_SPELL_LOCKS_GUARD:
        L = _PC_SPELL_LOCKS.get(pc_name)
        if L is None:
            L = threading.Lock()
            _PC_SPELL_LOCKS[pc_name] = L
        return L

@app.route('/api/cast_spell/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def cast_spell(pc_name):
    """Validate + record a spell cast. Returns success/failure with a
    short reason string. Client uses the response to decide whether to
    fire the actual roll-toast / GM broadcast."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    data = request.get_json(silent=True) or {}
    try:
        caster_idx = int(data.get('caster_idx', 0))
        slot_rank = int(data.get('slot_rank', 0))
        spell_rank = int(data.get('spell_rank', 0))  # base rank of the spell
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "bad caster/rank"}), 400
    spell_name = (data.get('spell_name') or '').strip()
    cast_type = (data.get('cast_type') or '').strip().lower()  # spontaneous/prepared/focus/cantrip/innate

    # Cantrips and focus / innate / impulse spells skip slot validation.
    if cast_type in ('cantrip', 'focus', 'innate', 'impulse', 'alchemical') or spell_rank <= 0:
        return jsonify({"success": True, "expended": False, "reason": "no slot needed"})

    pc = PARTY_LIBRARY[pc_name]
    # Find the caster
    casters = pc.spell_casters or []
    if caster_idx < 0 or caster_idx >= len(casters):
        return jsonify({"success": False, "error": "invalid caster index"}), 400
    caster = casters[caster_idx]
    real_type = (caster.get('type') or '').strip().lower()

    # Slot rank must be >= spell rank (PF2e RAW: no down-casting).
    if slot_rank < spell_rank:
        return jsonify({
            "success": False,
            "error": f"Slot rank {slot_rank} is lower than spell rank {spell_rank}. PF2e: a spell cannot be cast in a slot lower than its base rank."
        }), 400

    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "error": "no save file"}), 500

    with _pc_spell_lock(pc_name):
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        expended = build.get('expended_slots') or {}
        prepared = build.get('prepared_spells') or {}
        key = f"c{caster_idx}_l{slot_rank}"

        # Determine slot count for this caster + rank (regular + font slots)
        slot_count = 0
        font_extra = 0
        font_kind = ''
        for lvl in (caster.get('levels') or []):
            if int(lvl.get('level', -1)) == slot_rank:
                slot_count = int(lvl.get('slots', 0))
                font_extra = int(lvl.get('font_slots', 0) or 0)
                font_kind = (lvl.get('font_kind', '') or '').lower()
                break
        total_slots = slot_count + font_extra
        if total_slots <= 0:
            return jsonify({"success": False, "error": f"You have no rank-{slot_rank} slots."}), 400

        # PREPARED — the spell must be at this exact slot in the daily prep.
        if 'prepared' in real_type and 'spontaneous' not in real_type:
            prep_for_caster = prepared.get(str(caster_idx)) or prepared.get(caster_idx) or {}
            slots_at_rank = prep_for_caster.get(str(slot_rank)) or prep_for_caster.get(slot_rank) or []
            if not isinstance(slots_at_rank, list):
                slots_at_rank = []
            target_idx = -1
            for i, prepped_name in enumerate(slots_at_rank):
                if prepped_name and prepped_name.strip().lower() == spell_name.lower():
                    # Already cast?
                    cast_prep = build.get('cast_prep') or {}
                    cast_key = f"{caster_idx}_{slot_rank}_{i}"
                    if cast_prep.get(cast_key):
                        continue
                    target_idx = i
                    break
            if target_idx < 0:
                return jsonify({
                    "success": False,
                    "error": f"\"{spell_name}\" is not prepared in a rank-{slot_rank} slot. Prepare it first (open Spellbook → drag to a rank-{slot_rank} slot)."
                }), 400
            # Mark this prepared slot as cast.
            cast_prep = build.get('cast_prep') or {}
            cast_prep[f"{caster_idx}_{slot_rank}_{target_idx}"] = True
            build['cast_prep'] = cast_prep
            save_and_reload_character(pc_name, pc_json, file_path)
            return jsonify({"success": True, "expended": True, "slot_rank": slot_rank,
                            "slot_idx": target_idx, "type": "prepared"})

        # SPONTANEOUS — must know the spell at slot_rank OR have it signed.
        # Per PF2e: "you must know a spell at the specific rank that you want
        # to cast it" — except signature spells, which auto-heighten.
        # We check the repertoire (caster.levels[].spells) at slot_rank.
        sig_map = {}
        _raw_sig = build.get('signature_spells') or []
        if isinstance(_raw_sig, dict):
            sig_map = {int(k): v for k, v in _raw_sig.items() if v}
        sig_names = set(_raw_sig) if isinstance(_raw_sig, list) else set(sig_map.values())
        is_signature = spell_name in sig_names
        if not is_signature and slot_rank != spell_rank:
            # Not signature → must be known at slot_rank
            known_at_rank = False
            for lvl in (caster.get('levels') or []):
                if int(lvl.get('level', -1)) == slot_rank:
                    for sp in (lvl.get('spells') or []):
                        if (sp.get('name') or '').strip().lower() == spell_name.lower():
                            known_at_rank = True
                            break
                    break
            if not known_at_rank:
                return jsonify({
                    "success": False,
                    "error": f'"{spell_name}" is not in your repertoire at rank {slot_rank}. Mark it as a Signature spell or learn it at that rank to heighten.'
                }), 400
        free_idx = -1
        slot_list = expended.get(key) or []
        for i in range(total_slots):
            if i >= len(slot_list) or not slot_list[i]:
                free_idx = i
                break
        if free_idx < 0:
            return jsonify({
                "success": False,
                "error": f"No rank-{slot_rank} slots left today. Pick a higher rank or a cantrip / focus spell."
            }), 400
        # Pad and mark.
        while len(slot_list) <= free_idx:
            slot_list.append(False)
        slot_list[free_idx] = True
        expended[key] = slot_list
        build['expended_slots'] = expended
        save_and_reload_character(pc_name, pc_json, file_path)
        return jsonify({"success": True, "expended": True, "slot_rank": slot_rank,
                        "slot_idx": free_idx, "type": "spontaneous", "signature": is_signature})


# GM → player check request (Wave 2 #14). GM screen posts which skill
# (and optional DC) to broadcast — every player's sheet pops a small
# banner with a "Roll +N" button using their own modifier.
@app.route('/api/request_check', methods=['POST'])
def request_check_from_players():
    data = request.get_json(silent=True) or {}
    skill = (data.get('skill') or '').strip()
    if not skill:
        return jsonify({"success": False, "error": "missing skill"}), 400
    dc = data.get('dc')
    targets = data.get('targets') or []  # empty list = everyone
    secret = bool(data.get('secret', False))
    payload = {
        'skill': skill,
        'dc': dc,
        'targets': targets,
        'secret': secret,
    }
    sse_broadcast('check_request', payload)
    return jsonify({"success": True})


# Level-up validator (Wave 2 #29) — checks the build for incomplete
# choices that PF2e requires at the player's current level. Returns a
# list of issue strings; the level-up drawer renders these as a checklist
# so the player isn't surprised mid-session.
@app.route('/api/levelup_validate/<pc_name>')
def levelup_validate(pc_name):
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown player"}), 404
    pc = PARTY_LIBRARY[pc_name]
    issues = []
    L = pc.level
    # Skill increase: every odd level from 3 onward (PF2e core).
    expected_skill_increases = max(0, (L - 1) // 2)  # L3=1, L5=2, L7=3...
    actual_increases = sum(1 for v in (pc.proficiencies or {}).values() if isinstance(v, int) and v >= 4)
    # The check above is a heuristic; many builds have starting trained
    # skills, so flag only when we are clearly under the boost count.
    # General feat: every even level from 3 onward.
    # Class feat: every even level.
    # Ancestry feat: 1, 5, 9, 13, 17.
    # Ability boosts: every 5 levels (L5/L10/L15/L20).
    feats = pc.feats or []
    class_feat_levels = sorted({safe_int(f.get('level'), 0) for f in feats if 'class' in (f.get('type','').lower() or 'class')})
    # The build engine usually fills these via Pathbuilder. Surface only
    # generic warnings if the count looks short for the level.
    if L >= 5:
        boosts_hit = bool(getattr(pc, 'level_5_boosts_applied', None))
        if not boosts_hit and L >= 5:
            # Heuristic: flag if no L5 boost recorded in build.
            try:
                build = pc._build_ref or {}
                applied = (build.get('attributes', {}) or {}).get('boosts_applied', [])
                if not any(str(b).startswith('5') for b in (applied or [])):
                    issues.append(f"Level 5 ability boosts: pick four +1s (or +2 to a stat below 18).")
            except Exception:
                pass
    # Spell prep for prepared casters: surface if prepared_spells looks empty.
    if pc.spell_casters:
        for ci, c in enumerate(pc.spell_casters):
            if 'prepared' in (c.get('type','').lower()):
                build = pc._build_ref or {}
                prep = (build.get('prepared_spells') or {}).get(str(ci), {})
                if not prep:
                    issues.append(f"Prepared caster #{ci+1} ({c.get('name','?')}) has no prepared spells.")
    return jsonify({"success": True, "issues": issues, "level": L})


# GM → player loot dispatch (Wave 2 #26). The GM-side caller specifies
# which player to send to; we broadcast a GM event AND emit a
# player-targeted event the player sheet listens for.
@app.route('/api/send_loot', methods=['POST'])
def send_loot_to_player():
    data = request.get_json(silent=True) or {}
    target = (data.get('target') or '').strip()
    items = data.get('items') or []  # list of {name, qty?, bulk?, note?}
    coins = data.get('coins') or {}  # {pp, gp, sp, cp}
    if not target or target not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "unknown target"}), 404
    if not isinstance(items, list):
        items = []
    pc = PARTY_LIBRARY[target]
    # Apply coins to the PC's wallet directly so the player just sees
    # their wallet update without an extra "accept" step.
    pc.pp = int(getattr(pc, 'pp', 0) or 0) + int(coins.get('pp', 0) or 0)
    pc.gp = int(getattr(pc, 'gp', 0) or 0) + int(coins.get('gp', 0) or 0)
    pc.sp = int(getattr(pc, 'sp', 0) or 0) + int(coins.get('sp', 0) or 0)
    pc.cp = int(getattr(pc, 'cp', 0) or 0) + int(coins.get('cp', 0) or 0)
    # Append items to the PC's inventory (build['equipment'] for persistence).
    file_path = get_pc_file_path(target)
    if file_path and os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        equipment = build.get('equipment') or []
        for it in items:
            if not isinstance(it, dict): continue
            nm = (it.get('name') or '').strip()
            if not nm: continue
            qty = int(it.get('qty', 1) or 1)
            equipment.append([nm, qty])
        build['equipment'] = equipment
        # Persist coins
        build['pp'] = pc.pp; build['gp'] = pc.gp; build['sp'] = pc.sp; build['cp'] = pc.cp
        save_and_reload_character(target, pc_json, file_path)
    # Session-scrapbook hook (Chunk 6): record loot per recipient.
    try:
        _record_loot(target, items, coins)
    except Exception:
        pass
    # Persistent loot ledger: append a timestamped entry for wealth tracking.
    try:
        from datetime import datetime as _dt_loot
        entry = {
            'id': str(uuid.uuid4()),
            'timestamp': _dt_loot.now().isoformat(),
            'recipient': target,
            'items': [{'name': it.get('name', ''), 'qty': int(it.get('qty', 1) or 1)}
                      for it in items if isinstance(it, dict) and it.get('name')],
            'coins': {k: int(v or 0) for k, v in coins.items() if k in ('pp', 'gp', 'sp', 'cp')},
            'note': (data.get('note') or '').strip(),
        }
        _mutate_loot_ledger(lambda l: l['entries'].append(entry))
    except Exception:
        pass
    # Tell the target player's sheet to refresh (and surface a toast).
    sse_broadcast('loot_received', {
        'target': target, 'items': items, 'coins': coins,
    })
    return jsonify({"success": True})


@app.route('/api/equip_armor/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def equip_armor(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    a_name = data.get('name', '')
    a_info = next((a for a in BUILDER_ARMOR if a['name'] == a_name), None)
    
    if a_info:
        build['armor_name'] = a_info['name']
        build['ac_item'] = a_info['ac']
        build['ac_dex_cap'] = a_info['dex_cap']
        build['armor_penalty'] = a_info['penalty']
        build['armor_speed_pen'] = a_info['speed_penalty']
        build['armor_str_req'] = a_info['str_req']
        build['armor_bulk'] = a_info['bulk']
        build['armor_traits'] = a_info['traits']
    else:
        build['armor_name'] = ''
        build['ac_item'] = 0
        build['ac_dex_cap'] = 99
        build['armor_penalty'] = 0
        build['armor_speed_pen'] = 0
        build['armor_str_req'] = 0
        build['armor_bulk'] = '0'
        build['armor_traits'] = []

    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/update_sheet/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def update_sheet(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    if data.get('type') == 'armor':
        build['ac_item'] = int(data.get('ac_item', 0))
        build['ac_dex_cap'] = int(data.get('ac_dex_cap', 99))
        build['armor_penalty'] = int(data.get('armor_penalty', 0))
        build['stealth_penalty'] = int(data.get('stealth_penalty', 0))

    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/update_wealth/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def update_wealth(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    if 'money' not in build: build['money'] = {}
    build['money']['pp'] = int(data.get('pp', 0))
    build['money']['gp'] = int(data.get('gp', 0))
    build['money']['sp'] = int(data.get('sp', 0))
    build['money']['cp'] = int(data.get('cp', 0))
    
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/spell_slots/<pc_name>', methods=['GET', 'POST'])
@require_pc_self_or_gm
def spell_slots(pc_name):
    """Server-side spell slot persistence. GET returns current state, POST saves it.
    Stores: expended_slots, prepared_spells, cast_prep (all server-side)."""
    file_path = get_pc_file_path(pc_name)
    if not os.path.exists(file_path):
        return jsonify({"error": "Character not found"}), 404

    if request.method == 'GET':
        with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        return jsonify({
            "success": True,
            "expended_slots": build.get('expended_slots', {}),
            "prepared_spells": build.get('prepared_spells', {}),
            "cast_prep": build.get('cast_prep', {})
        })

    # POST — save slot state (accepts any combination of the three keys).
    # Guarded by the per-PC spell lock so a click on a slot checkbox can't
    # race with a /api/cast_spell write and clobber it (or vice versa).
    data = request.json
    with _pc_spell_lock(pc_name):
        with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        if 'expended_slots' in data:
            build['expended_slots'] = data['expended_slots']
        if 'prepared_spells' in data:
            build['prepared_spells'] = data['prepared_spells']
        if 'cast_prep' in data:
            build['cast_prep'] = data['cast_prep']
        save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/add_item/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def add_item(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    if 'equipment' not in build or build['equipment'] is None: build['equipment'] = []
    
    item_name = data.get('name', 'Unknown Item')
    item_qty = int(data.get('qty', 1))
    found = False
    for eq in build['equipment']:
        if isinstance(eq, list) and len(eq) >= 2 and eq[0].lower() == item_name.lower():
            eq[1] = int(eq[1]) + item_qty; found = True; break
        elif isinstance(eq, dict) and eq.get('name', '').lower() == item_name.lower():
            eq['qty'] = int(eq.get('qty', 0)) + item_qty; found = True; break
            
    if not found: build['equipment'].append([item_name, item_qty])
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/remove_item/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def remove_item(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    item_name = data.get('name', '')
    if 'equipment' in build and isinstance(build['equipment'], list):
        new_eq = []
        for eq in build['equipment']:
            if isinstance(eq, list) and eq[0] == item_name: continue
            elif isinstance(eq, dict) and eq.get('name') == item_name: continue
            new_eq.append(eq)
        build['equipment'] = new_eq
        
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

# ─── CONSUMABLE QUANTITY ADJUSTMENT ──────────────────────────────────
@app.route('/api/adjust_consumable/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def adjust_consumable(pc_name):
    data = request.json or {}
    item_name = data.get('name', '').strip()
    delta = int(data.get('delta', 0))
    if not item_name or delta == 0:
        return jsonify({"success": False, "error": "Missing name or delta"}), 400
    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return jsonify({"success": False, "error": "PC not found"}), 404
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    equipment = build.get('equipment') or []
    new_qty = None
    for eq in equipment:
        if isinstance(eq, list) and len(eq) >= 2 and eq[0] == item_name:
            eq[1] = max(0, int(eq[1]) + delta)
            new_qty = eq[1]
            break
        elif isinstance(eq, dict) and eq.get('name') == item_name:
            eq['qty'] = max(0, int(eq.get('qty', 0)) + delta)
            new_qty = eq['qty']
            break
    if new_qty is None:
        return jsonify({"success": False, "error": "Item not found"}), 404
    if new_qty <= 0:
        build['equipment'] = [
            eq for eq in equipment
            if not ((isinstance(eq, list) and eq[0] == item_name) or
                    (isinstance(eq, dict) and eq.get('name') == item_name))
        ]
    save_and_reload_character(pc_name, pc_json, file_path)
    try:
        verb = 'used' if delta < 0 else 'gained'
        _combat_log(f"{pc_name} {verb} {abs(delta)}x {item_name} (now {new_qty})", 'system')
    except Exception:
        pass
    return jsonify({"success": True, "item": item_name, "qty": new_qty})


# ─── HERO POINT NOMINATIONS ─────────────────────────────────────────
@app.route('/api/hero_nomination', methods=['POST'])
def hero_nomination():
    data = request.json or {}
    nominator = data.get('nominator', '').strip()
    nominee = data.get('nominee', '').strip()
    reason = data.get('reason', '').strip() or 'No reason given'
    if not nominator or not nominee:
        return jsonify({"success": False, "error": "Missing nominator or nominee"}), 400
    if nominee not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Nominee not in party"}), 404
    payload = {
        "nominator": nominator,
        "nominee": nominee,
        "reason": reason,
        "ts": int(time.time()),
        "id": str(uuid.uuid4())[:8]
    }
    try:
        sse_broadcast('hero_nomination', payload)
        _combat_log(f"{nominator} nominated {nominee} for a Hero Point: {reason}", 'system')
    except Exception:
        pass
    return jsonify({"success": True})


@app.route('/api/approve_hero_nomination', methods=['POST'])
@gm_required
def approve_hero_nomination():
    data = request.json or {}
    nominee = data.get('nominee', '').strip()
    if not nominee or nominee not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Invalid nominee"}), 400
    def _mutate(pc):
        if pc.hero_points < 3:
            pc.hero_points += 1
        return True
    try:
        _, pc = apply_pc_delta(nominee, _mutate)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    _combat_log(f"{nominee} awarded a Hero Point via nomination (now {pc.hero_points})", 'system')
    return jsonify({"success": True, "nominee": nominee, "hero_points": pc.hero_points})


@app.route('/api/add_weapon/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def add_weapon(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    if 'weapons' not in build or build['weapons'] is None: build['weapons'] = []
    
    w_name = data.get('name', 'Custom Weapon')
    w_dmg = data.get('damage', '1d4')
    w_traits = data.get('traits', [])
    
    w_name_clean = w_name.lower().strip()
    for bw in BUILDER_WEAPONS:
        if bw['name'].lower().strip() == w_name_clean:
            w_dmg = bw.get('damage', '1d4')
            w_traits = bw.get('traits', [])
            break
    
    # Fallback: consult hardcoded PF2E weapon table if DB gave default 1d4
    if w_dmg == '1d4' and w_name in PF2E_WEAPON_DAMAGE:
        w_dmg = PF2E_WEAPON_DAMAGE[w_name]
    
    # Auto-detect weapon category for proficiency
    w_cat = PF2E_WEAPON_CATEGORIES.get(w_name, 'simple')
    prof_map = {'simple': 'simple', 'martial': 'martial', 'advanced': 'advanced'}
    auto_prof = safe_int(build.get('proficiencies', {}).get(prof_map.get(w_cat, 'simple'), 2))

    build['weapons'].append({
        'name': w_name, 
        'attack_stat': data.get('attack_stat', 'str'), 
        'prof_val': data.get('prof_val', auto_prof), 
        'damage': w_dmg, 
        'traits': w_traits,
        'is_two_handed': False
    })
    
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/toggle_two_hand/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def toggle_two_hand(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    _flush_pc_dirty(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)

    w_name = data.get('name', '')
    if 'weapons' in build and isinstance(build['weapons'], list):
        for w in build['weapons']:
            if w.get('name') == w_name:
                # Flip the grip the player currently SEES, then mark it
                # user-chosen so import stops re-seeding it from the "(2h)"
                # display. Until grip_user_set is true the displayed grip comes
                # from that display (the import seed), NOT a possibly-stale
                # is_two_handed=False — so mirror that here, else the first tap
                # would just re-assert the displayed grip and take two taps to
                # actually switch.
                if w.get('grip_user_set'):
                    cur = bool(w.get('is_two_handed'))
                else:
                    _disp = str(w.get('display') or '').lower()
                    cur = ('(2h)' in _disp or 'two-hand' in _disp or 'two-handed' in _disp)
                w['is_two_handed'] = not cur
                w['grip_user_set'] = True
                break

    save_and_reload_character(pc_name, pc_json, file_path)
    # Two-handed grip changes the weapon's damage die; broadcast so the strike
    # cards repaint their damage (and the 2H pill) in place, no reload.
    _broadcast_pc_state(pc_name)
    return jsonify({"success": True})

@app.route('/api/delete_weapon/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def delete_weapon(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    if 'weapons' in build and isinstance(build['weapons'], list): 
        build['weapons'] = [w for w in build['weapons'] if w.get('name') != data.get('name')]
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/save_notes/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def save_notes(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    pc_json.get('build', pc_json)['notes'] = data.get('notes', '')
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/player/builder')
def player_builder():
    # Filter weapons/armor to level 0-1 items for starting gear
    starting_weapons = [w for w in BUILDER_WEAPONS if w.get('level', 0) <= 1 and w.get('category') in ('simple', 'martial', None)]
    starting_armor = [a for a in BUILDER_ARMOR if a.get('level', 0) <= 1]
    return render_template('player_builder.html',
        ancestries=BUILDER_ANCESTRIES,
        backgrounds=BUILDER_BACKGROUNDS,
        classes=BUILDER_CLASSES,
        spells=BUILDER_SPELLS,
        feats=BUILDER_FEATS,
        builder_data=BUILDER_DATA,
        subclass_descriptions=SUBCLASS_DESCRIPTIONS,
        weapons=starting_weapons,
        armor=starting_armor
    )

@app.route('/api/toggle_feature/<pc_name>/<feature_name>', methods=['POST'])
@require_pc_self_or_gm
def toggle_feature(pc_name, feature_name):
    """Toggle a class feature on/off (like Rage, Panache, etc.)."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    pc = PARTY_LIBRARY[pc_name]
    file_path = get_pc_file_path(pc_name)
    if not file_path:
        return jsonify({"error": "File not found"}), 404

    # Persist any in-flight combat state before the read-modify-write so the
    # reload below doesn't revert live HP / conditions / shield.
    _flush_pc_dirty(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)

    toggles = build.get('active_toggles') or []
    if feature_name in toggles:
        toggles.remove(feature_name)
        active = False
    else:
        toggles.append(feature_name)
        active = True
    
    build['active_toggles'] = toggles
    save_and_reload_character(pc_name, pc_json, file_path)

    pc = PARTY_LIBRARY[pc_name]
    effects = pc.toggle_effects_summary
    # Broadcast so the sheet repaints AC / strike damage in place — the toggle
    # (Rage, Arcane Cascade, …) ripples into derived numbers the player sheet
    # now paints from the pc_update `derived` block instead of hard-reloading.
    _broadcast_pc_state(pc_name)

    return jsonify({"success": True, "active": active, "feature": feature_name, "effects": effects})

@app.route('/api/set_reaction/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def set_reaction(pc_name):
    """Mark / clear a PC's per-round reaction. Auto-reset at the start of the
    PC's turn by cycle_turn; this endpoint is the manual toggle for players
    (Attack of Opportunity, Shield Warden, etc.) or a GM override."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Unknown PC"}), 404
    data = request.get_json(silent=True) or {}
    used = bool(data.get('used', True))
    def _mutate(pc):
        pc.reaction_used = used
        return True
    _, pc = apply_pc_delta(pc_name, _mutate)
    return jsonify({"success": True, "reaction_used": bool(pc.reaction_used)})

# Phase 10: PF2e exploration activities (Core p.479). The canonical list lives
# here so the player sheet dropdown and GM banner agree on keys + labels.
# GMs only need to SEE what each PC is doing; no mechanical effects are applied.
EXPLORATION_ACTIVITIES = [
    ('',                  'None (Walking)',      'No special activity — just traveling.'),
    ('search',            'Search',              'Look for hidden things. Half speed. Perception vs Stealth DC.'),
    ('detect_magic',      'Detect Magic',        'Cast Detect Magic periodically while moving.'),
    ('follow_expert',     'Follow the Expert',   'Gain +2/+3/+4 circumstance to match a trained ally.'),
    ('scout',             'Scout',               '+1 circumstance bonus to party initiative.'),
    ('avoid_notice',      'Avoid Notice',        'Stealth for initiative; gain Undetected status on entry.'),
    ('hustle',            'Hustle',              'Double speed for (CON mod × 10) minutes.'),
    ('defend',            'Defend',              'Walk with shield raised. Starts combat with shield raised.'),
    ('repeat_spell',      'Repeat a Spell',      'Cast the same cantrip each round of exploration.'),
    ('track',             'Track',               'Follow tracks using Survival. Half speed.'),
    ('investigate',       'Investigate',         'Use Recall Knowledge repeatedly while moving.'),
    ('cover_tracks',      'Cover Tracks',        'Hide party trail. Half speed. Survival to obscure.'),
    ('sense_direction',   'Sense Direction',     'Survival check to avoid getting lost.'),
    ('decipher_writing',  'Decipher Writing',    'Work through a written puzzle/inscription.'),
    ('treat_wounds',      'Treat Wounds',        'Medicine check to heal a target (10 min).'),
    ('other',             'Other (custom)',      'A custom activity — note it in the dropdown label.'),
]

@app.route('/api/exploration_activities')
def api_exploration_activities():
    """Return the canonical list of exploration activities + labels + tooltips.
    Cached indefinitely on the client side — the list is effectively constant."""
    return jsonify({
        'activities': [{'key': k, 'label': lbl, 'tooltip': tip} for (k, lbl, tip) in EXPLORATION_ACTIVITIES]
    })

@app.route('/api/set_exploration_activity/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def set_exploration_activity(pc_name):
    """Set a PC's current exploration activity. Broadcasts via pc_update so
    the party view / GM screen can render a per-PC banner without polling."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Unknown PC"}), 404
    data = request.get_json(silent=True) or request.form or {}
    key = (data.get('activity') or '').strip()
    valid_keys = {k for (k, _, _) in EXPLORATION_ACTIVITIES}
    if key not in valid_keys:
        return jsonify({"success": False, "error": f"Unknown activity '{key}'"}), 400
    def _mutate(pc):
        pc.exploration_activity = key
        return True
    _, pc = apply_pc_delta(pc_name, _mutate, sync_conditions=False)
    # Surface on the combat log so GM sees the change in the roll feed too.
    label = next((lbl for (k, lbl, _) in EXPLORATION_ACTIVITIES if k == key), key)
    _combat_log(f"{pc_name}: Exploration → {label}", 'system')
    return jsonify({"success": True, "exploration_activity": pc.exploration_activity, "label": label})


@app.route('/api/persistent_damage/<pc_name>/add', methods=['POST'])
@require_pc_self_or_gm
def persistent_damage_add(pc_name):
    """Add a persistent-damage entry to a PC. Body: {damage, type, source}.

    We intentionally do NOT auto-roll damage or the DC 15 flat check — the
    player drives those from the sheet (per user request). This endpoint
    just records the condition; the UI + turn reminder do the reminding.
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Unknown PC"}), 404
    data = request.get_json(silent=True) or request.form or {}
    damage = (data.get('damage') or '').strip()
    if not damage:
        return jsonify({"success": False, "error": "Missing damage expression"}), 400
    ptype = (data.get('type') or '').strip()
    source = (data.get('source') or '').strip()
    entry = {'damage': damage, 'type': ptype, 'source': source}
    def _mutate(pc):
        _pd = getattr(pc, 'persistent_damage', []) or []
        lst = list(_pd) if isinstance(_pd, list) else []
        lst.append(entry)
        pc.persistent_damage = lst
        return True
    _, pc = apply_pc_delta(pc_name, _mutate)
    # Regenerate reminders if this PC is currently up.
    try:
        if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER):
            cur = ACTIVE_ENCOUNTER[TURN_INDEX]
            if cur.is_pc and cur.name == pc_name:
                _generate_turn_reminders()
    except Exception: pass
    return jsonify({"success": True, "persistent_damage": list(pc.persistent_damage)})


@app.route('/api/persistent_damage/<pc_name>/remove/<int:idx>', methods=['POST'])
@require_pc_self_or_gm
def persistent_damage_remove(pc_name, idx):
    """Remove one persistent-damage entry by index (e.g. when the player
    rolls the DC 15 flat check and succeeds, or the GM confirms the source
    is gone)."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Unknown PC"}), 404
    def _mutate(pc):
        _pd = getattr(pc, 'persistent_damage', []) or []
        lst = list(_pd) if isinstance(_pd, list) else []
        if 0 <= idx < len(lst):
            lst.pop(idx)
        pc.persistent_damage = lst
        return True
    _, pc = apply_pc_delta(pc_name, _mutate)
    try:
        if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER):
            cur = ACTIVE_ENCOUNTER[TURN_INDEX]
            if cur.is_pc and cur.name == pc_name:
                _generate_turn_reminders()
    except Exception: pass
    return jsonify({"success": True, "persistent_damage": list(pc.persistent_damage)})


@app.route('/api/persistent_damage/<pc_name>/flat_check/<int:idx>', methods=['POST'])
@require_pc_self_or_gm
def persistent_damage_flat_check(pc_name, idx):
    """Roll the DC 15 flat check for a specific persistent-damage entry.
    On success, the entry is removed. We roll here so the result shows up
    in the combat log consistently — the player clicks the button, but the
    RNG is server-side so everyone sees the same outcome.
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"success": False, "error": "Unknown PC"}), 404
    pc = PARTY_LIBRARY[pc_name]
    lst = list(getattr(pc, 'persistent_damage', []) or [])
    if not (0 <= idx < len(lst)):
        return jsonify({"success": False, "error": "Bad index"}), 400
    entry = lst[idx]
    roll = random.randint(1, 20)
    removed = roll >= 15
    label = f"{entry.get('damage','?')} {entry.get('type','')}".strip()
    if removed:
        def _mutate(pc):
            cur = list(getattr(pc, 'persistent_damage', []) or [])
            if 0 <= idx < len(cur):
                cur.pop(idx)
            pc.persistent_damage = cur
            return True
        apply_pc_delta(pc_name, _mutate)
        _combat_log(f"{pc_name}: Flat check {roll} vs DC 15 — persistent {label} ENDS", 'heal')
    else:
        _combat_log(f"{pc_name}: Flat check {roll} vs DC 15 — persistent {label} continues", 'damage')
    try:
        if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER):
            cur = ACTIVE_ENCOUNTER[TURN_INDEX]
            if cur.is_pc and cur.name == pc_name:
                _generate_turn_reminders()
    except Exception: pass
    return jsonify({
        "success": True,
        "roll": roll,
        "dc": 15,
        "passed": removed,
        "persistent_damage": list(PARTY_LIBRARY[pc_name].persistent_damage),
    })


@app.route('/api/toggle_shield/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def toggle_shield(pc_name):
    """Raise / lower a shield. PF2e: Raise a Shield is a 1-action activity
    that gives you the shield's circumstance bonus to AC until the start of
    your next turn (auto-dropped by the turn advancer).

    Broken/destroyed shields can't be raised — the +AC bonus is lost until
    the shield is repaired.
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    pc = PARTY_LIBRARY[pc_name]
    if getattr(pc, 'shield_destroyed', False):
        return jsonify({"success": False, "error": "Shield is destroyed — can't raise"}), 400
    # Broken shields CAN still be raised (they just can't Block) — so we allow it.

    def _mutate(pc):
        pc.shield_raised = not bool(getattr(pc, 'shield_raised', False))
        return True
    _, pc = apply_pc_delta(pc_name, _mutate)
    return jsonify({
        "success": True,
        "shield_raised": pc.shield_raised,
        "shield_broken": bool(getattr(pc, 'shield_broken', False)),
        "shield_hp": int(getattr(pc, 'shield_hp', 0) or 0),
        "shield_max_hp": int(getattr(pc, 'shield_max_hp', 0) or 0),
        "ac": pc.ac,
    })

@app.route('/api/learn_spell/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def learn_spell(pc_name):
    """Add a spell to a character's spellbook/repertoire."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    data = request.json
    spell_name = data.get('name', '')
    spell_level = safe_int(data.get('level'), 0)
    caster_idx = safe_int(data.get('caster_idx'), 0)
    
    if not spell_name:
        return jsonify({"error": "No spell name provided"}), 400
    
    file_path = get_pc_file_path(pc_name)
    if not file_path:
        return jsonify({"error": "File not found"}), 404
    
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    spell_casters = build.get('spellCasters', [])
    if caster_idx >= len(spell_casters):
        return jsonify({"error": "Invalid caster index"}), 400

    caster = spell_casters[caster_idx]

    # Tradition gate: can't learn an arcane spell into a divine list, etc.
    # Allow override via `force=True` (e.g. multiclass dedications can grant
    # cross-tradition spells).
    caster_trad = (caster.get('magicTradition') or '').strip().lower()
    spell_trads = data.get('traditions') or data.get('spell_traditions') or []
    spell_trads = [str(t).strip().lower() for t in spell_trads if t]
    force = bool(data.get('force', False))
    if caster_trad and spell_trads and caster_trad not in spell_trads and not force:
        return jsonify({
            "success": False,
            "error": f'"{spell_name}" is not on the {caster_trad} list (only: {", ".join(sorted(set(spell_trads)))}). Pass force=true to override.',
            "tradition_mismatch": True
        }), 400

    spells = caster.get('spells', [])

    # Find or create the level array
    lvl_entry = next((s for s in spells if s.get('spellLevel') == spell_level), None)
    if not lvl_entry:
        lvl_entry = {"spellLevel": spell_level, "list": []}
        spells.append(lvl_entry)

    if spell_name not in lvl_entry['list']:
        lvl_entry['list'].append(spell_name)

    caster['spells'] = spells
    save_and_reload_character(pc_name, pc_json, file_path)

    return jsonify({"success": True, "spell": spell_name, "level": spell_level})

@app.route('/api/forget_spell/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def forget_spell(pc_name):
    """Remove a spell from a character's spellbook/repertoire.

    Strips the spell from the caster's `spells[].list` at the given level.
    Also clears any prepared-slot instances so a stale reference doesn't
    leave a ghost "Cast" button pointing at a spell the character no longer
    knows.
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404

    data = request.json or {}
    spell_name = data.get('name', '')
    spell_level = safe_int(data.get('level'), 0)
    caster_idx = safe_int(data.get('caster_idx'), 0)

    if not spell_name:
        return jsonify({"error": "No spell name provided"}), 400

    file_path = get_pc_file_path(pc_name)
    if not file_path:
        return jsonify({"error": "File not found"}), 404

    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)

    spell_casters = build.get('spellCasters', [])
    if caster_idx >= len(spell_casters):
        return jsonify({"error": "Invalid caster index"}), 400

    caster = spell_casters[caster_idx]
    removed = False
    for lvl_entry in caster.get('spells', []) or []:
        if lvl_entry.get('spellLevel') == spell_level and spell_name in (lvl_entry.get('list') or []):
            lvl_entry['list'] = [s for s in lvl_entry['list'] if s != spell_name]
            removed = True

    # Scrub any prepared slots that reference this spell — the prepared UI
    # lives in build.prepared_spells[caster_idx][level] = [name|null, ...]
    prepped = build.get('prepared_spells') or {}
    caster_prep = prepped.get(str(caster_idx)) or prepped.get(caster_idx) or {}
    for lvl_key, slot_arr in list(caster_prep.items()):
        if isinstance(slot_arr, list):
            caster_prep[lvl_key] = [None if s == spell_name else s for s in slot_arr]
    if caster_prep:
        # Normalize key to str so reloads match what we wrote
        prepped[str(caster_idx)] = caster_prep
        build['prepared_spells'] = prepped

    # Signature spells (spontaneous casters) — drop the entry if present.
    # Handle both legacy flat list and new {rank: name} dict.
    sigs = build.get('signature_spells') or []
    if isinstance(sigs, dict):
        build['signature_spells'] = {k: v for k, v in sigs.items() if v != spell_name}
    elif spell_name in sigs:
        build['signature_spells'] = [s for s in sigs if s != spell_name]

    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True, "removed": removed, "spell": spell_name, "level": spell_level})

@app.route('/api/set_signature_spells/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def set_signature_spells(pc_name):
    """Set signature spells for spontaneous casters.

    PF2e RAW: a spontaneous caster gets 1 signature spell per spell rank they
    can cast (not counting cantrips). Each chosen spell must be in the
    repertoire at its base rank. We accept either a flat list (legacy) or a
    `{rank: name}` map (new) and normalize both.
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404

    data = request.json or {}
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    pc = PARTY_LIBRARY[pc_name]

    # Find spontaneous caster — first one wins. If none, reject.
    spont = None
    spont_idx = -1
    for i, sc in enumerate(pc.spell_casters or []):
        if 'spontaneous' in (sc.get('type', '').lower()):
            spont = sc
            spont_idx = i
            break
    if spont is None:
        return jsonify({"success": False, "error": "Character has no spontaneous caster"}), 400

    # Build {rank: spell_names_known_at_that_rank} from caster levels
    known_at_rank = {}
    max_rank = 0
    for lvl in spont.get('levels', []):
        r = int(lvl.get('level', 0))
        if r <= 0:
            continue
        if int(lvl.get('slots', 0)) > 0:
            max_rank = max(max_rank, r)
        known_at_rank.setdefault(r, set())
        for sp in (lvl.get('spells') or []):
            known_at_rank[r].add(sp.get('name'))

    incoming = data.get('signature_spells', [])
    sig_map = {}  # rank -> name
    if isinstance(incoming, dict):
        for k, v in incoming.items():
            try:
                rk = int(k)
            except (TypeError, ValueError):
                continue
            if v:
                sig_map[rk] = str(v)
    elif isinstance(incoming, list):
        # Legacy: figure out the rank for each name (lowest rank known).
        for name in incoming:
            for r in sorted(known_at_rank.keys()):
                if name in known_at_rank.get(r, set()):
                    if r not in sig_map:
                        sig_map[r] = name
                    break

    # Enforce: chosen spell must be known at exactly that rank, one per rank,
    # and rank must be one the PC can cast.
    cleaned = {}
    for r, name in sig_map.items():
        if r > max_rank:
            return jsonify({"success": False,
                            "error": f"Signature rank {r} exceeds your max castable rank {max_rank}."}), 400
        if name not in known_at_rank.get(r, set()):
            return jsonify({"success": False,
                            "error": f'"{name}" is not in your repertoire at rank {r}.'}), 400
        cleaned[str(r)] = name

    build['signature_spells'] = cleaned
    # Keep a flat list around for legacy UI bindings.
    build['signature_spells_flat'] = list(cleaned.values())
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True, "signature_spells": cleaned, "max_rank": max_rank})

@app.route('/api/set_focus_spells/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def set_focus_spells(pc_name):
    """Manually add/set focus spells for a character."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    action = data.get('action', 'add')
    spell_name = data.get('name', '').strip()
    
    if action == 'add' and spell_name:
        # Add as a Focus Spell feat
        feats = build.get('feats') or []
        if not any(f[0] == spell_name for f in feats if isinstance(f, list)):
            feats.append([spell_name, None, 'Focus Spell', 1, '', 'manualAdd', None])
            build['feats'] = feats
        # Ensure focus pool exists
        if not build.get('focus') or not build['focus'].get('pool'):
            build['focus'] = {'pool': 1}
    elif action == 'remove' and spell_name:
        feats = build.get('feats') or []
        build['feats'] = [f for f in feats if not (isinstance(f, list) and f[0] == spell_name and len(f) > 2 and f[2] == 'Focus Spell')]
    elif action == 'set_pool':
        pool = safe_int(data.get('pool'), 1)
        build['focus'] = {'pool': pool}
        build['current_focus'] = pool
    
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/delete_character/<pc_name>', methods=['POST'])
def delete_character(pc_name):
    """Delete a character from the party library."""
    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Character not found"}), 404
    
    os.remove(file_path)
    if pc_name in PARTY_LIBRARY:
        del PARTY_LIBRARY[pc_name]
    
    portraits_dir = os.path.join(PARTY_DIR, 'portraits')
    if os.path.exists(portraits_dir):
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', pc_name)
        for f in os.listdir(portraits_dir):
            if f.startswith(safe_name + '.'):
                os.remove(os.path.join(portraits_dir, f))
    
    return jsonify({"success": True})

@app.route('/api/add_pet/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def add_pet(pc_name):
    """Add a pet/companion to a character."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    pets = build.get('pets_custom') or []
    pets.append(data)
    build['pets_custom'] = pets
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/remove_pet/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def remove_pet(pc_name):
    """Remove a pet by name."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    pet_name = data.get('name', '')
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    pets = build.get('pets_custom') or []
    build['pets_custom'] = [p for p in pets if p.get('name') != pet_name]
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/send_initiative/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def send_initiative(pc_name):
    """Player rolls initiative and sends it to the GM encounter tracker."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    roll_total = safe_int(data.get('total'), 0)
    PENDING_INITIATIVES[pc_name] = {'total': roll_total, 'time': time.time()}
    
    return jsonify({"success": True, "initiative": roll_total})

@app.route('/api/save_session_note/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def save_session_note(pc_name):
    """Save a dated session note entry."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    note_text = data.get('text', '').strip()
    if not note_text:
        return jsonify({"error": "Empty note"}), 400
    
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    session_notes = build.get('session_notes') or []
    import datetime
    session_notes.append({
        'date': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
        'text': note_text
    })
    build['session_notes'] = session_notes
    save_and_reload_character(pc_name, pc_json, file_path)
    
    return jsonify({"success": True, "count": len(session_notes)})

@app.route('/api/delete_session_note/<pc_name>/<int:note_idx>', methods=['POST'])
@require_pc_self_or_gm
def delete_session_note(pc_name, note_idx):
    """Delete a session note by index."""
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    notes = build.get('session_notes') or []
    if 0 <= note_idx < len(notes):
        notes.pop(note_idx)
        build['session_notes'] = notes
        save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/sync_spell_slots/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def sync_spell_slots(pc_name):
    """Sync expended spell slot state to the character JSON for GM visibility."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    build['expended_slots'] = data.get('expended_slots', {})
    save_and_reload_character(pc_name, pc_json, file_path)
    _broadcast_pc_state(pc_name)
    return jsonify({"success": True})

@app.route('/api/upload_portrait/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def upload_portrait(pc_name):
    """Upload a character portrait image.

    Accepts an optional focus point (focus_x, focus_y as 0–100 floats) so the
    player can frame which part of their uploaded image sits inside the
    circular crop. Stored as pc.portrait_focus = {'x': 50, 'y': 50} and
    rendered via CSS object-position on all portrait sites.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    # Validate image type
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return jsonify({"error": "Invalid image type"}), 400

    # Optional focus point — percent (0-100). Default to dead-center.
    def _clamp_focus(v):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return 50.0
        return max(0.0, min(100.0, n))
    focus_x = _clamp_focus(request.form.get('focus_x', 50))
    focus_y = _clamp_focus(request.form.get('focus_y', 50))

    portraits_dir = os.path.join(PARTY_DIR, 'portraits')
    if not os.path.exists(portraits_dir):
        os.makedirs(portraits_dir)

    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', pc_name)
    filename = f"{safe_name}.{ext}"

    # Remove old portrait if exists
    for old in os.listdir(portraits_dir):
        if old.startswith(safe_name + '.'):
            os.remove(os.path.join(portraits_dir, old))

    file.save(os.path.join(portraits_dir, filename))

    # Update the character JSON
    file_path = get_pc_file_path(pc_name)
    if file_path and os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        build['portrait'] = filename
        build['portrait_focus'] = {'x': focus_x, 'y': focus_y}
        save_and_reload_character(pc_name, pc_json, file_path)

    return jsonify({"success": True, "filename": filename, "focus": {'x': focus_x, 'y': focus_y}})


@app.route('/api/update_portrait_focus/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def update_portrait_focus(pc_name):
    """Update only the focus point for an already-uploaded portrait.
    Cheaper than a re-upload when the player just wants to recentre."""
    def _clamp_focus(v):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return 50.0
        return max(0.0, min(100.0, n))
    # Accept either form or JSON body so callers can do either.
    payload = request.get_json(silent=True) or {}
    focus_x = _clamp_focus(request.form.get('focus_x', payload.get('focus_x', 50)))
    focus_y = _clamp_focus(request.form.get('focus_y', payload.get('focus_y', 50)))
    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Character not found"}), 404
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    build['portrait_focus'] = {'x': focus_x, 'y': focus_y}
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True, "focus": {'x': focus_x, 'y': focus_y}})


@app.route('/portraits/<filename>')
def serve_portrait(filename):
    """Serve portrait images from party_data/portraits/.

    max_age=3600 keeps the browser from refetching on every page nav
    while still letting a freshly-uploaded portrait appear within an
    hour. Combined with the cache-busting query string the frontend
    adds on upload (?v=<mtime>), re-uploads show immediately too."""
    portraits_dir = os.path.join(PARTY_DIR, 'portraits')
    return send_from_directory(portraits_dir, filename, max_age=3600)


@app.route('/campaign_assets/<filename>')
def serve_campaign_asset(filename):
    """Serve campaign-level images (hero/splash backgrounds) from
    CAMPAIGN_ASSETS_DIR. Public — the campaign intro page is the
    players' login surface and references this URL."""
    return send_from_directory(CAMPAIGN_ASSETS_DIR, filename, max_age=3600)


@app.route('/handouts/<filename>')
def serve_handout_image(filename):
    """Serve GM-uploaded handout images from HANDOUTS_DIR (the Railway volume
    in production, so they survive redeploys). Public — players need to view
    handouts the GM pushed to them. send_from_directory guards against path
    traversal; the <filename> converter already rejects slashes."""
    return send_from_directory(HANDOUTS_DIR, filename, max_age=3600)


_AUDIO_EXTS = {'.ogg', '.mp3', '.wav', '.m4a', '.aac', '.opus', '.flac'}

def _audio_root():
    """Resolve the campaign audio dir fresh (symlink may appear post-start)."""
    try:
        return os.path.realpath(CAMPAIGN_AUDIO_DIR)
    except OSError:
        return CAMPAIGN_AUDIO_DIR


@app.route('/campaign_audio/<path:filename>')
def serve_campaign_audio(filename):
    """Serve soundscape audio from CAMPAIGN_AUDIO_DIR. GM-device-only feature,
    but the route is open (no secret URLs) — files are non-sensitive ambience.
    send_from_directory handles HTTP range requests so large .ogg tracks stream."""
    root = _audio_root()
    if not os.path.isdir(root):
        return ('audio directory not available', 404)
    return send_from_directory(root, filename, max_age=3600, conditional=True)


@app.route('/api/audio/list')
@gm_required
def api_audio_list():
    """List playable audio files under CAMPAIGN_AUDIO_DIR (recursive), grouped
    by their top-level subfolder (Ambience / Loop / SFX / ...)."""
    root = _audio_root()
    if not os.path.isdir(root):
        return jsonify({'success': False, 'available': False, 'error': 'No campaign audio directory found.', 'files': []})
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if os.path.splitext(n)[1].lower() not in _AUDIO_EXTS:
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            top = rel.split('/', 1)[0] if '/' in rel else ''
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            files.append({'path': rel, 'name': n, 'group': top, 'size': size})
    files.sort(key=lambda f: (f['group'].lower(), f['name'].lower()))
    return jsonify({'success': True, 'available': True, 'files': files,
                    'soundscapes': _load_campaign_config().get('soundscapes', {})})


@app.route('/api/audio/soundscapes', methods=['GET', 'POST'])
def api_audio_soundscapes():
    """GET the soundscape->file mapping (used by the audio engine on every GM
    page); POST (GM-only) to save the GM's assignments."""
    if request.method == 'GET':
        return jsonify({'success': True, 'soundscapes': _load_campaign_config().get('soundscapes', {})})
    if not _is_gm():
        return jsonify({'success': False, 'error': 'GM authentication required'}), 403
    data = request.json or {}
    incoming = data.get('soundscapes') or {}
    # Whitelist the three known scenes; values are relative paths (or '').
    current = dict(_load_campaign_config().get('soundscapes') or {})
    for scene in ('tavern', 'dungeon', 'combat'):
        if scene in incoming:
            current[scene] = str(incoming[scene] or '')
    cfg = _save_campaign_config({'soundscapes': current})
    return jsonify({'success': True, 'soundscapes': cfg.get('soundscapes', {})})


@app.route('/api/audio/storage')
@gm_required
def api_audio_storage():
    """Report where soundscape audio is stored and whether it's durable — so
    the GM can confirm, before a session, that uploads land on the Railway
    persistent volume (and survive redeploys) rather than ephemeral disk."""
    root = CAMPAIGN_AUDIO_DIR
    # "Persistent" = a volume was explicitly configured via env (DATA_DIR on
    # Railway, or PF2E_AUDIO_DIR), not the local default that DATA_DIR falls
    # back to. On Railway without DATA_DIR set, uploads would vanish on deploy.
    persistent = bool(os.environ.get('DATA_DIR') or os.environ.get('PF2E_AUDIO_DIR'))
    writable = False
    try:
        os.makedirs(root, exist_ok=True)
        probe = os.path.join(root, '.write_probe')
        with open(probe, 'w') as fp:
            fp.write('ok')
        os.remove(probe)
        writable = True
    except OSError:
        writable = False
    count = 0
    try:
        rr = os.path.realpath(root)
        if os.path.isdir(rr):
            for _d, _s, names in os.walk(rr):
                count += sum(1 for n in names if os.path.splitext(n)[1].lower() in _AUDIO_EXTS)
    except OSError:
        pass
    return jsonify({
        'success': True,
        'dir': root,
        'persistent': persistent,
        'writable': writable,
        'data_dir': DATA_DIR,
        'file_count': count,
    })


@app.route('/api/audio/upload', methods=['POST'])
@gm_required
def api_audio_upload():
    """Upload one soundscape track to CAMPAIGN_AUDIO_DIR (the Railway volume in
    production). One file per request keeps each POST under MAX_CONTENT_LENGTH.
    Returns the saved relative path so the GM can map it to a scene."""
    f = request.files.get('audio')
    if not f or not f.filename:
        return jsonify({'success': False, 'error': 'audio field required'}), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _AUDIO_EXTS:
        return jsonify({'success': False, 'error': f"unsupported type '{ext}' — ogg / mp3 / wav / m4a / opus / flac only"}), 400
    f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)
    if size > 48 * 1024 * 1024:
        return jsonify({'success': False, 'error': f'file too large ({size // (1024*1024)} MB); max 48 MB per file'}), 413
    # Sanitize to a safe flat filename (strip directories + odd chars).
    base = os.path.basename(f.filename).replace('\\', '/').split('/')[-1]
    safe = re.sub(r'[^A-Za-z0-9._ -]', '_', base).strip() or ('track' + ext)
    root = CAMPAIGN_AUDIO_DIR  # write target (not realpath — we own this dir)
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as e:
        return jsonify({'success': False, 'error': f'could not create audio dir: {e}'}), 500
    dest = os.path.join(root, safe)
    # Avoid clobbering: if a same-named file exists, suffix with a counter.
    if os.path.exists(dest):
        stem, x = os.path.splitext(safe)
        i = 2
        while os.path.exists(os.path.join(root, f'{stem}-{i}{x}')):
            i += 1
        safe = f'{stem}-{i}{x}'
        dest = os.path.join(root, safe)
    f.save(dest)
    return jsonify({'success': True, 'path': safe, 'name': safe, 'byte_count': size})


@app.route('/api/audio/delete', methods=['POST'])
@gm_required
def api_audio_delete():
    """Delete an uploaded track (path relative to CAMPAIGN_AUDIO_DIR). Path-safe."""
    data = request.json or {}
    rel = (data.get('path') or '').strip().lstrip('/')
    if not rel:
        return jsonify({'success': False, 'error': 'path required'}), 400
    root = os.path.realpath(CAMPAIGN_AUDIO_DIR)
    target = os.path.realpath(os.path.join(root, rel))
    if not target.startswith(root + os.sep) and target != root:
        return jsonify({'success': False, 'error': 'path escapes audio dir'}), 400
    try:
        if os.path.isfile(target):
            os.remove(target)
        return jsonify({'success': True})
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/campaign/hero_image', methods=['POST'])
@gm_required
def api_campaign_hero_image():
    """Upload (or remove) the campaign splash background. Stored under
    /campaign_assets/ on the Railway volume so it survives redeploys.
    On successful upload, the campaign config's `hero_image` field is
    updated to the public URL automatically — no manual URL paste."""
    # Remove mode: empty file + ?action=remove clears the existing image.
    if request.form.get('action') == 'remove' or request.args.get('action') == 'remove':
        cfg = _load_campaign_config()
        old = (cfg.get('hero_image') or '').strip()
        # Only delete files we own (under /campaign_assets/) to avoid
        # nuking a remote URL the GM pointed at manually.
        if old.startswith('/campaign_assets/'):
            old_path = os.path.join(CAMPAIGN_ASSETS_DIR, os.path.basename(old))
            try:
                if os.path.exists(old_path):
                    os.remove(old_path)
            except OSError:
                pass
        _save_campaign_config({'hero_image': ''})
        return jsonify({"success": True, "hero_image": ""})

    f = request.files.get('image')
    if not f or not f.filename:
        return jsonify({"success": False, "error": "image field required"}), 400

    ext = (os.path.splitext(f.filename)[1] or '').lower().lstrip('.')
    if ext not in {'png', 'jpg', 'jpeg', 'webp', 'gif'}:
        return jsonify({"success": False, "error": f"unsupported extension '{ext}' — png / jpg / webp / gif only"}), 400

    # Size guard so an accidental 60-MB drop doesn't fill the volume
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > 15 * 1024 * 1024:
        return jsonify({"success": False, "error": f"file too large ({size // (1024*1024)} MB); max 15 MB"}), 413

    os.makedirs(CAMPAIGN_ASSETS_DIR, exist_ok=True)

    # Stamp filename with mtime so the browser cache busts naturally on
    # re-upload (URL changes → fresh fetch). Save the NEW file first; only
    # delete prior splashes after the new one is durably on disk and the
    # config points at it. Reverse order would leave the GM with no splash
    # if f.save() fails (disk hiccup / killed mid-write).
    stamp = int(time.time())
    filename = f"hero_image_{stamp}.{ext}"
    new_path = os.path.join(CAMPAIGN_ASSETS_DIR, filename)
    f.save(new_path)

    public_url = f"/campaign_assets/{filename}"
    _save_campaign_config({'hero_image': public_url})

    # Now that the new splash is the source of truth, garbage-collect any
    # older hero_image_* files so the volume doesn't grow forever as the
    # GM iterates on the art.
    for old in os.listdir(CAMPAIGN_ASSETS_DIR):
        if old.startswith('hero_image_') and old != filename:
            try:
                os.remove(os.path.join(CAMPAIGN_ASSETS_DIR, old))
            except OSError:
                pass

    return jsonify({"success": True, "hero_image": public_url, "byte_count": size})


@app.route('/api/campaign/crest', methods=['POST'])
@gm_required
def api_campaign_crest():
    """Upload (or remove) the campaign crest — a square emblem shown centered
    on the session-start curtain and in the app corner. Mirrors the
    hero_image uploader but writes crest_* files and updates `crest_image`."""
    if request.form.get('action') == 'remove' or request.args.get('action') == 'remove':
        cfg = _load_campaign_config()
        old = (cfg.get('crest_image') or '').strip()
        if old.startswith('/campaign_assets/'):
            old_path = os.path.join(CAMPAIGN_ASSETS_DIR, os.path.basename(old))
            try:
                if os.path.exists(old_path):
                    os.remove(old_path)
            except OSError:
                pass
        _save_campaign_config({'crest_image': ''})
        return jsonify({"success": True, "crest_image": ""})

    f = request.files.get('image')
    if not f or not f.filename:
        return jsonify({"success": False, "error": "image field required"}), 400

    ext = (os.path.splitext(f.filename)[1] or '').lower().lstrip('.')
    if ext not in {'png', 'jpg', 'jpeg', 'webp', 'gif', 'svg'}:
        return jsonify({"success": False, "error": f"unsupported extension '{ext}' — png / jpg / webp / gif / svg only"}), 400

    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > 8 * 1024 * 1024:
        return jsonify({"success": False, "error": f"file too large ({size // (1024*1024)} MB); max 8 MB"}), 413

    os.makedirs(CAMPAIGN_ASSETS_DIR, exist_ok=True)
    stamp = int(time.time())
    filename = f"crest_{stamp}.{ext}"
    new_path = os.path.join(CAMPAIGN_ASSETS_DIR, filename)
    f.save(new_path)

    public_url = f"/campaign_assets/{filename}"
    _save_campaign_config({'crest_image': public_url})

    # GC older crests so the volume doesn't grow as the GM iterates.
    for old in os.listdir(CAMPAIGN_ASSETS_DIR):
        if old.startswith('crest_') and old != filename:
            try:
                os.remove(os.path.join(CAMPAIGN_ASSETS_DIR, old))
            except OSError:
                pass

    return jsonify({"success": True, "crest_image": public_url, "byte_count": size})


@app.route('/api/modules')
@gm_required
def api_modules_list():
    """Catalog every .js file in static/js/modules/ plus the per-campaign
    enabled set. Front-end uses this to render the enable/disable UI."""
    files = _list_module_files()
    cfg = _load_campaign_config()
    enabled = set(cfg.get('modules_enabled') or [])
    for f in files:
        f['enabled'] = f['id'] in enabled
    return jsonify({'modules': files})


@app.route('/api/modules/toggle', methods=['POST'])
@gm_required
def api_modules_toggle():
    """Toggle a module's enabled state in the campaign config. The
    per-page <script> loader reads this list at next page render."""
    data = request.json or {}
    module_id = (data.get('id') or '').strip()
    if not module_id:
        return jsonify({'success': False, 'error': 'id required'}), 400
    # Verify the module file actually exists so the enabled list never
    # references a missing file (which would 404 the script tag).
    if not any(m['id'] == module_id for m in _list_module_files()):
        return jsonify({'success': False, 'error': 'module file not found'}), 404
    enable = bool(data.get('enabled'))
    cfg = _load_campaign_config()
    current = list(cfg.get('modules_enabled') or [])
    if enable and module_id not in current:
        current.append(module_id)
    elif (not enable) and module_id in current:
        current = [m for m in current if m != module_id]
    _save_campaign_config({'modules_enabled': current})
    return jsonify({'success': True, 'modules_enabled': current})


def _enabled_module_files():
    """Return the (id, filename) tuples for modules currently enabled in
    the campaign config, in order. Filters out any enabled entry whose
    file has since been deleted from disk."""
    cfg = _load_campaign_config()
    enabled = cfg.get('modules_enabled') or []
    files = {m['id']: m['filename'] for m in _list_module_files()}
    return [(mid, files[mid]) for mid in enabled if mid in files]


@app.route('/api/export_character/<pc_name>')
def export_character(pc_name):
    """Download a character's JSON file."""
    file_path = get_pc_file_path(pc_name)
    if file_path and os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    return jsonify({"error": "Character not found"}), 404

@app.route('/api/import_pathbuilder', methods=['POST'])
def import_pathbuilder():
    """Import a Pathbuilder 2e JSON export. If character already exists, smart-merges
    to update abilities/feats/spells/proficiencies from Pathbuilder while preserving
    HP, conditions, notes, custom weapons, pets, shield stats, expended slots, and session data."""
    try:
        # Accept a Pathbuilder JSON export (file or body) OR an official Paizo
        # fillable PF2e character-sheet PDF, which we convert to a Pathbuilder
        # build (Character then re-derives the sheet's exact stats).
        if 'file' in request.files:
            file = request.files['file']
            raw_bytes = file.read()
            fname = (getattr(file, 'filename', '') or '').lower()
            if fname.endswith('.pdf') or raw_bytes[:5] == b'%PDF-':
                import pf2e_pdf_import
                _pdf_build, _pdf_play = pf2e_pdf_import.build_from_pdf(raw_bytes, character_factory=Character)
                pc_json = {"success": True, "build": _pdf_build}
            else:
                pc_json = json.loads(raw_bytes.decode('utf-8'))
        elif request.json:
            pc_json = request.json
        else:
            return jsonify({"error": "No data provided"}), 400
        
        # Pathbuilder wraps in {"success": true, "build": {...}} 
        new_build = pc_json.get('build', pc_json)
        
        # Validate required fields
        name = new_build.get('name', '').strip()
        if not name:
            return jsonify({"error": "Character has no name"}), 400
        if not new_build.get('class'):
            return jsonify({"error": "Character has no class"}), 400
        if not new_build.get('ancestry'):
            return jsonify({"error": "Character has no ancestry"}), 400
        
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
        file_path = os.path.join(PARTY_DIR, f"{safe_name}.json")
        
        merged = False
        if name in PARTY_LIBRARY and os.path.exists(file_path):
            # --- SMART MERGE: Character exists, preserve runtime state ---
            with open(file_path, 'r', encoding='utf-8') as f:
                existing_json = json.load(f)
            existing_build = existing_json.get('build', existing_json)
            
            # Fields to IMPORT from Pathbuilder (game rules data)
            PB_IMPORT_KEYS = [
                'name', 'class', 'dualClass', 'level', 'xp', 'ancestry', 'heritage',
                'background', 'alignment', 'gender', 'age', 'deity', 'size', 'sizeName',
                'keyability', 'languages', 'rituals', 'resistances', 'inventorMods',
                'abilities', 'attributes', 'proficiencies', 'mods', 'feats', 'specials',
                'lores', 'specificProficiencies', 'armor', 'spellCasters', 'focusPoints',
                'focus', 'formula', 'acTotal', 'pets', 'familiars',
            ]
            
            # Fields to PRESERVE from existing (runtime/custom data)
            PRESERVE_KEYS = [
                'current_hp', 'conditions', 'current_focus', 'hero_points',
                'notes', 'session_notes', 'portrait', 'portrait_focus', 'active_toggles',
                'shield_raised', 'shield_hp', 'shield_max_hp', 'shield_hardness', 'shield_bt', 'shield_ac_bonus',
                'expended_slots', 'signature_spells', 'active_effects',
                'weapons',  # Preserve custom weapons added in-app
                'pets_custom',  # Preserve custom pets
                'level_history', 'monk_paths', 'half_boosts',
                'persistent_damage',
            ]
            
            # Start with existing build as base
            merged_build = dict(existing_build)
            
            # Overlay Pathbuilder data for rules fields
            for key in PB_IMPORT_KEYS:
                if key in new_build:
                    merged_build[key] = new_build[key]
            
            # Merge weapons: keep custom weapons (those with no PB equivalent), add PB weapons
            existing_weapons = existing_build.get('weapons') or []
            pb_weapons = new_build.get('weapons') or []
            # Custom weapons = those that don't match any PB weapon name
            pb_weapon_names = {(w.get('name','') if isinstance(w, dict) else '').lower() for w in pb_weapons}
            custom_weapons = [w for w in existing_weapons if isinstance(w, dict) and w.get('name','').lower() not in pb_weapon_names and w.get('name','') != 'Fist']
            merged_build['weapons'] = pb_weapons + custom_weapons
            
            # Merge equipment: Pathbuilder's equipment list takes precedence, but append custom items
            pb_equipment = new_build.get('equipment') or []
            existing_equipment = existing_build.get('equipment') or []
            pb_eq_names = set()
            for eq in pb_equipment:
                if isinstance(eq, list) and len(eq) >= 1: pb_eq_names.add(str(eq[0]).lower())
                elif isinstance(eq, dict): pb_eq_names.add(str(eq.get('name','')).lower())
            custom_eq = []
            for eq in existing_equipment:
                eq_name = ''
                if isinstance(eq, list) and len(eq) >= 1: eq_name = str(eq[0]).lower()
                elif isinstance(eq, dict): eq_name = str(eq.get('name','')).lower()
                if eq_name and eq_name not in pb_eq_names:
                    custom_eq.append(eq)
            merged_build['equipment'] = pb_equipment + custom_eq
            
            # Restore preserved fields from existing
            for key in PRESERVE_KEYS:
                if key in existing_build and key not in ['weapons']:
                    merged_build[key] = existing_build[key]
            
            # Cap current_hp to new max (level might have changed)
            # Don't set current_hp if it wasn't previously saved (let Character.__init__ default to max)
            
            final_json = {"success": True, "build": merged_build}
            merged = True
        else:
            # --- FRESH IMPORT: No existing character ---
            if 'build' not in pc_json:
                final_json = {"success": True, "build": new_build}
            else:
                final_json = pc_json
        
        # In account/campaign mode, carry the campaign envelope so the PC stays
        # invitable/claimable -- and on a re-import (merge) PRESERVE the existing
        # id/owner so a claimed PC is not un-claimed. Legacy mode is unchanged.
        cid = _active_campaign_id()
        if cid:
            final_json = _storage.ensure_character_envelope(
                final_json, cid, existing=(existing_json if merged else None))

        # Save to disk
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(final_json, f, indent=2)

        # Reload into library
        try:
            PARTY_LIBRARY[name] = Character(final_json, file_path)
            _build_pc_file_cache()
        except Exception as e:
            return jsonify({"error": f"Character loaded but had parse issues: {str(e)}", "success": True, "name": name})
        
        action = "merged" if merged else "imported"
        return jsonify({"success": True, "name": name, "level": new_build.get('level', 1), "class": new_build.get('class', 'Unknown'), "action": action})
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON format"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/save_new_character', methods=['POST'])
def save_new_character():
    data = request.json
    char_name = data.get('name', 'Unknown')
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', char_name)
    
    abilities = data.get('abilities', {"str": 0, "dex": 0, "con": 0, "int": 0, "wis": 0, "cha": 0})
    class_name = data.get('class_name', '')
    subclass_name = data.get('subclass', '')
    ancestry_name = data.get('ancestry', '')
    heritage_name = data.get('heritage', '')
    
    cls_data = CLASS_MATRIX.get(class_name.lower(), {})
    base_profs = copy.deepcopy(cls_data.get("base_proficiencies", {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "will": 2}))
    
    focus_spell = None
    granted_spells = []
    tradition = data.get('spellCasters', [{}])[0].get('magicTradition', 'Unknown') if data.get('spellCasters') else 'Unknown'

    if subclass_name in SUBCLASS_MATRIX:
        overrides = SUBCLASS_MATRIX[subclass_name]
        if "armor" in overrides:
            for k, v in overrides["armor"].items(): base_profs[k.lower()] = max(base_profs.get(k.lower(), 0), v)
        if "weapons" in overrides:
            for k, v in overrides["weapons"].items(): base_profs[k.lower()] = max(base_profs.get(k.lower(), 0), v)
        if "skills" in overrides:
            for sk in overrides["skills"]:
                base_profs[sk.lower()] = max(base_profs.get(sk.lower(), 0), 2)
        if "tradition" in overrides:
            tradition = overrides["tradition"].title()
        if "focus_spell" in overrides:
            focus_spell = overrides["focus_spell"]
        if "granted_spells" in overrides:
            granted_spells = overrides["granted_spells"]

    proficiencies = {"ac": 2}
    for k, v in base_profs.items():
        proficiencies[k.lower()] = v
        
    for sk in data.get('skills', []):
        proficiencies[sk.lower()] = max(proficiencies.get(sk.lower(), 0), 2)

    feats_arr = []
    for f in data.get('feats', []):
        feats_arr.append([f.get('name'), f.get('type'), 1, f.get('desc', '')])
        
    if focus_spell:
        feats_arr.append([focus_spell, "Focus Spell", 1])

    bg_name = data.get('background', '')
    bg_data = BUILDER_BACKGROUNDS.get(bg_name, {})
    if bg_data.get('feat'):
        feats_arr.append([bg_data['feat'], "Background Feat", 1, "Granted automatically by your background."])

    # L5 + L11 + L12: auto-grant class-awarded, subclass-awarded, and
    # heritage-awarded feats. Pathbuilder marks these as "Awarded Feat" in
    # exports; from-scratch builds were skipping them.
    UNHOLY_CHAMPION_CAUSES = {'Tyrant', 'Desecrator', 'Antipaladin'}
    def _append_grant(g):
        # For unholy Champion causes, swap Lay on Hands → Touch of the Void.
        if (g.get('name') == 'Lay on Hands' and class_name.lower() == 'champion'
                and subclass_name in UNHOLY_CHAMPION_CAUSES):
            g = {**g, 'name': 'Touch of the Void',
                 'desc': 'Inflict void damage on a living touched target (1d6 per spell rank).'}
        existing_names = {(f[0] if isinstance(f, list) else None) for f in feats_arr}
        if g.get('name') and g['name'] not in existing_names:
            feats_arr.append([g['name'], g.get('type', 'Awarded Feat'), g.get('level', 1), g.get('desc', '')])
    for g in CLASS_AWARDED_FEATS.get(class_name.lower(), []):
        _append_grant(g)
    for g in SUBCLASS_AWARDED_FEATS.get(subclass_name, []):
        _append_grant(g)
    for g in HERITAGE_AWARDED_FEATS.get(heritage_name, []):
        _append_grant(g)

    heritage_desc = ""
    ancestry_key = "unknown"
    for a_key, h_list in BUILDER_DATA["heritages"].items():
        for h in h_list:
            if h["name"] == heritage_name:
                heritage_desc = h["desc"].lower()
                ancestry_key = a_key
                feats_arr.append([heritage_name, "Heritage", 1, h["desc"]])
                break
    
    spell_casters = data.get('spellCasters', [])
    if spell_casters:
        # Look up casting type from RICH_CLASS_DATA (CLASS_MATRIX doesn't have spellcasting key)
        rich_data = RICH_CLASS_DATA.get(class_name.lower(), {})
        c_type = rich_data.get("spellcasting", "spontaneous").lower()
        table_key = "spontaneous"
        if "bounded" in c_type: table_key = "bounded"
        elif "prepared" in c_type: table_key = "prepared"
        if class_name.lower() == "sorcerer": table_key = "sorcerer"
            
        per_day_slots = [5] + SPELL_SLOT_TABLES.get(table_key, {}).get(1, [0]*10)
        
        spell_casters[0]["magicTradition"] = tradition
        spell_casters[0]["perDay"] = per_day_slots
        
        for g_spell in granted_spells:
            lvl_arr = next((l for l in spell_casters[0]["spells"] if l["spellLevel"] == g_spell["lvl"]), None)
            if not lvl_arr:
                lvl_arr = {"spellLevel": g_spell["lvl"], "list": []}
                spell_casters[0]["spells"].append(lvl_arr)
            if g_spell["name"] not in lvl_arr["list"]:
                lvl_arr["list"].append(g_spell["name"])

    if focus_spell:
        spell_casters.append({
            "name": "Focus Spells",
            "magicTradition": tradition,
            "castingType": "Focus",
            "spells": [{"spellLevel": 1, "list": [focus_spell]}],
            "perDay": [0,0,0,0,0,0,0,0,0,0]
        })

    weapons_arr = []
    if class_name.lower() == 'kineticist':
        # Determine element from guided choices in feats
        kin_element = 'fire'  # Default
        for f in data.get('feats', []):
            f_name = f.get('name', '') if isinstance(f, dict) else str(f)
            if 'Elements:' in f_name:
                el = f_name.replace('Elements:', '').strip().lower().split(',')[0].strip()
                kin_element = el
                break
        # Map elements to damage types
        kin_dmg_map = {'fire': 'F', 'water': 'B', 'earth': 'B', 'air': 'S', 'metal': 'S', 'wood': 'B'}
        kin_dmg_type = kin_dmg_map.get(kin_element, 'B')
        weapons_arr.append({
            "name": "Elemental Blast",
            "attack_stat": "con",
            "prof_val": 2,
            "damage": f"1d8 {kin_dmg_type}",
            "traits": ["kineticist", "magical", kin_element]
        })

    # Process equipment from builder payload
    equipment_list = data.get('equipment', [])
    armor_arr = []
    eq_items = []
    ac_item_bonus = 0
    ac_dex_cap = 99
    armor_penalty = 0
    armor_speed_pen = 0
    stealth_penalty = 0
    for eq in equipment_list:
        eq_type = eq.get('type', 'gear')
        if eq_type == 'weapon':
            w_name = eq.get('name', '')
            w_info = next((w for w in BUILDER_WEAPONS if w['name'] == w_name), None)
            dmg = eq.get('damage', '1d4')
            cat = eq.get('category', 'simple')
            traits = eq.get('traits', [])
            if w_info:
                dmg = w_info.get('damage', dmg)
                cat = w_info.get('category', cat)
                traits = w_info.get('traits', traits)
            # Parse damage die from string like "1d8 S"
            dmg_parts = dmg.split()
            die = dmg_parts[0] if dmg_parts else '1d4'
            dmg_type = dmg_parts[1] if len(dmg_parts) > 1 else ''
            weapons_arr.append({
                "name": w_name, "qty": 1, "prof": cat, "die": die,
                "pot": 0, "str": "", "mat": None, "display": w_name,
                "runes": [], "damageType": dmg_type, "extraDamage": [],
                "increasedDice": False, "isInventor": False, "grade": ""
            })
        elif eq_type == 'armor':
            a_name = eq.get('name', '')
            a_info = next((a for a in BUILDER_ARMOR if a['name'] == a_name), None)
            cat = eq.get('category', 'light')
            ac_bonus = eq.get('ac', 0)
            dex_cap = eq.get('dex_cap')
            if a_info:
                ac_bonus = a_info.get('ac', ac_bonus)
                dex_cap = a_info.get('dex_cap', dex_cap)
                cat = a_info.get('category', cat)
                armor_penalty = safe_int(a_info.get('penalty', 0))
                armor_speed_pen = safe_int(a_info.get('speed_penalty', 0))
                if 'noisy' in str(a_info.get('traits', [])).lower():
                    stealth_penalty = armor_penalty
            ac_item_bonus = ac_bonus
            if dex_cap is not None:
                ac_dex_cap = dex_cap
            armor_arr.append({
                "name": a_name, "qty": 1, "prof": cat,
                "pot": 0, "res": "", "mat": None, "display": a_name,
                "worn": True, "runes": [], "grade": ""
            })
        elif eq_type == 'gear':
            gear_name = eq.get('name', '')
            if gear_name == "Adventurer's Pack":
                for item, qty in [("Backpack", 1), ("Bedroll", 1), ("Chalk", 10), ("Flint and Steel", 1), ("Rope", 1), ("Rations", 2), ("Torch", 5), ("Waterskin", 1)]:
                    eq_items.append([item, qty, "Invested"])
            else:
                eq_items.append([gear_name, 1, "Invested"])

    anc_hp = BUILDER_ANCESTRIES.get(ancestry_name, {}).get('hp', 8)
    cls_hp = BUILDER_CLASSES.get(class_name, {}).get('hp', 8)
    anc_speed = ANCESTRY_SPEEDS.get(ancestry_name.lower(), 25)
    anc_size = ANCESTRY_SIZES.get(ancestry_name.lower(), 'Medium')

    new_char_json = {
        "build": {
            "name": char_name, "level": 1, 
            "ancestry": ancestry_name, 
            "heritage": data.get('heritage', ''),
            "background": data.get('background', ''),
            "class": class_name, 
            "subclass": subclass_name,
            "deity": data.get('deity', 'None'),
            "sanctification": data.get('sanctification', 'Neutral'),
            "abilities": abilities,
            "proficiencies": proficiencies, 
            "ac_item": ac_item_bonus, "ac_dex_cap": ac_dex_cap, "armor_penalty": armor_penalty, "stealth_penalty": stealth_penalty, "armor_speed_pen": armor_speed_pen,
            "armor": armor_arr,
            "attributes": {"ancestryhp": anc_hp, "classhp": cls_hp, "bonushp": 0, "bonushpPerLevel": 0, "speed": anc_speed},
            "size": anc_size,
            "feats": feats_arr, 
            "weapons": weapons_arr, 
            "spellCasters": spell_casters,
            "current_focus": 1 if focus_spell else 0,
            "focus": {"pool": 1} if focus_spell else {"pool": 0},
            "money": {"pp": 0, "gp": 15, "sp": 0, "cp": 0}, 
            "equipment": eq_items,
            "conditions": {},
            "active_effects": {},
            "active_toggles": [],
            "notes": "",
            "languages": data.get('languages', ['Common']),
            "lores": data.get('customLores', []),
        }
    }
    file_path = os.path.join(PARTY_DIR, f"{safe_name}.json")
    # In account/campaign mode, stamp the campaign envelope so this PC is
    # invitable/claimable and shows up in My Characters (re-save preserves any
    # existing ownership). Legacy single-game mode (no active campaign) is
    # unchanged.
    cid = _active_campaign_id()
    if cid:
        existing = _storage.load_json(file_path) if os.path.exists(file_path) else None
        new_char_json = _storage.ensure_character_envelope(new_char_json, cid, existing=existing)
    save_and_reload_character(char_name, new_char_json, file_path)
    return jsonify({"success": True, "message": "Character saved successfully!"})

def _filter_class_level_features_for_pc(pc):
    """Return a per-PC subset of CLASS_LEVEL_FEATURES.

    Each entry in CLASS_LEVEL_FEATURES may carry an optional `subclass`
    field — a list of subclass names (case-insensitive) the entry applies
    to. Entries with no `subclass` apply to every subclass in the class.
    This lets us ship "Storm-only" or "Warpriest-only" entries instead of
    one generic line that mentions every option.

    Falls back to the unfiltered class entries if the PC has no subclass
    set yet — keeps level-up working during character creation.
    """
    cls = (getattr(pc, 'class_name', '') or '').strip().lower()
    sub = (getattr(pc, 'subclass', '') or '').strip().lower()
    src = CLASS_LEVEL_FEATURES.get(cls, {})
    out = {}
    for lvl, entries in src.items():
        kept = []
        for e in entries:
            scope = e.get('subclass')
            if scope is None:
                kept.append(e)
                continue
            scopes_lc = [str(s).strip().lower() for s in scope]
            if sub and sub in scopes_lc:
                kept.append(e)
                continue
            # No subclass selected yet — show all variants so the player
            # can preview what each branch unlocks before committing.
            if not sub:
                kept.append(e)
        if kept:
            out[lvl] = kept
    return out

@app.route('/player/levelup/<pc_name>')
def player_levelup(pc_name):
    if pc_name in PARTY_LIBRARY:
        pc = PARTY_LIBRARY[pc_name]
        # Filter class-level features by the PC's subclass before passing
        # to the template — Storm Druid only sees Storm entries, etc.
        clf = _filter_class_level_features_for_pc(pc)
        return render_template('player_levelup.html', pc=pc, feats=BUILDER_FEATS, spells=BUILDER_SPELLS, class_matrix=CLASS_MATRIX, builder_data=BUILDER_DATA, class_progression=CLASS_PROGRESSION, subclass_progression=SUBCLASS_PROGRESSION, monk_path_config=MONK_PATH_CONFIG, skill_feat_prereqs=SKILL_FEAT_PREREQS, char_proficiencies=pc.proficiencies, class_level_features=clf)
    return redirect(url_for('player_view'))

def _count_feats_at_level(feats, level, slot_type):
    """Count Pathbuilder feat entries that satisfy a given progression slot
    at this level. Handles `Class Feat`, `Ancestry Feat`, `Skill Feat`,
    `General Feat`, and class-specific labels (`Kineticist Feat`,
    `Champion Feat`, etc.).

    Versatile Human's "Natural Ambition" counts as both an ancestry feat AND
    grants a bonus class feat in a child entry — the child entry covers the
    class_feat slot independently."""
    SLOT_TYPE_MAP = {
        'class_feat':    {'class feat', 'kineticist feat', 'champion feat',
                          'cleric feat', 'druid feat', 'fighter feat',
                          'wizard feat', 'rogue feat', 'bard feat',
                          'sorcerer feat', 'monk feat', 'ranger feat',
                          'witch feat', 'oracle feat', 'magus feat',
                          'summoner feat', 'investigator feat',
                          'swashbuckler feat', 'psychic feat',
                          'alchemist feat', 'animist feat', 'thaumaturge feat',
                          'barbarian feat', 'inventor feat', 'gunslinger feat'},
        'ancestry_feat': {'ancestry feat', 'heritage'},
        'skill_feat':    {'skill feat'},
        'general_feat':  {'general feat'},
    }
    accepted = SLOT_TYPE_MAP.get(slot_type, set())
    n = 0
    for ft in (feats or []):
        if not isinstance(ft, list) or len(ft) < 4:
            continue
        ft_type = (ft[2] or '').strip().lower() if len(ft) > 2 else ''
        ft_lvl = ft[3] if len(ft) > 3 else None
        # L6: Versatile Human "Natural Ambition" / "Skilled Heritage" etc. can
        # add a *child* feat at the same level (PB src='childChoice'). These
        # are bonus feats granted by another feat — they fill the slot they
        # belong to, but must not be double-counted toward the parent's slot.
        # We accept them: a child class-feat at L1 satisfies the class_feat
        # slot for L1 (so a PC with Versatile Human + Natural Ambition + class
        # feat gets credit for class_feat=1 even if their "main" L1 class feat
        # came from the child).
        if ft_type in accepted and ft_lvl == level:
            n += 1
    return n

def _new_skill_increases_at_level(build, new_level):
    """Crude check: did the level-up apply a new skill increase?
    Compares the previous proficiencies snapshot against the current."""
    history = (build.get('level_history') or {}).get(str(new_level), {})
    prev = history.get('previous_proficiencies') or {}
    cur = build.get('proficiencies') or {}
    STANDARD_SKILLS = {'acrobatics', 'arcana', 'athletics', 'crafting',
                       'deception', 'diplomacy', 'intimidation', 'medicine',
                       'nature', 'occultism', 'performance', 'religion',
                       'society', 'stealth', 'survival', 'thievery'}
    bumps = 0
    for sk in STANDARD_SKILLS:
        if (cur.get(sk, 0) or 0) > (prev.get(sk, 0) or 0):
            bumps += 1
    # Lore skills also count
    for k, v in cur.items():
        if k.startswith('lore') and (v or 0) > (prev.get(k, 0) or 0):
            bumps += 1
    return bumps

def _missing_progression_for_level(build, new_level):
    """Returns a list of human-readable strings describing required choices
    that haven't been made for this level. Empty list = ready to save."""
    cls = build.get('class', '')
    expected = get_required_slots_at_level(cls, new_level)
    feats = build.get('feats') or []
    missing = []
    for slot, count in expected.items():
        if slot == 'skill_increase':
            actual = _new_skill_increases_at_level(build, new_level)
            if actual < count:
                missing.append(f"{count - actual} skill increase(s)")
            continue
        if slot == 'ability_boosts':
            # Ability boosts are validated separately by the wizard form
            # against `build.abilities`; skip here.
            continue
        actual = _count_feats_at_level(feats, new_level, slot)
        if actual < count:
            label = slot.replace('_', ' ')
            missing.append(f"{count - actual} {label}(s) at level {new_level}")

    # L2: Champion Divine Ally must be chosen at L3
    if cls.lower() == 'champion' and new_level == 3 and not build.get('divine_ally'):
        missing.append('Divine Ally choice (Blade / Steed / Shield)')

    # L6: Versatile Human grants a bonus general feat at L1.
    if new_level == 1 and (build.get('heritage', '') or '').lower() == 'versatile human':
        general_count = _count_feats_at_level(feats, 1, 'general_feat')
        if general_count < 1:
            missing.append('1 bonus general feat from Versatile Human heritage')

    return missing

@app.route('/api/submit_levelup/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def submit_levelup(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    new_level = data.get('new_level', build['level'] + 1)
    
    # Store snapshot of state BEFORE level-up for clean undo
    import copy as _copy
    level_history = build.get('level_history') or {}
    level_history[str(new_level)] = {
        'previous_level': build['level'],
        'previous_abilities': _copy.deepcopy(build.get('abilities', {})),
        'previous_half_boosts': _copy.deepcopy(build.get('half_boosts', [])),
        'previous_proficiencies': _copy.deepcopy(build.get('proficiencies', {})),
        'previous_feats': _copy.deepcopy(build.get('feats', [])),
        'previous_spellCasters': _copy.deepcopy(build.get('spellCasters', [])),
    }
    build['level_history'] = level_history
    
    build['level'] = new_level
    # Advancement: this level-up consumes the "ready to level" flag, and in XP
    # mode spends 1000 XP toward the level just gained.
    build['ready_to_level'] = False
    if int(build.get('xp', 0) or 0) >= 1000:
        build['xp'] = int(build['xp']) - 1000

    # Only apply ability boosts at PF2E-qualifying levels (5, 10, 15, 20)
    ABILITY_BOOST_LEVELS = {5, 10, 15, 20}
    if 'abilities' in data and new_level in ABILITY_BOOST_LEVELS:
        build['abilities'] = data['abilities']
    if 'half_boosts' in data and new_level in ABILITY_BOOST_LEVELS:
        build['half_boosts'] = data['half_boosts']
    
    if 'feats' in data: build['feats'] = data['feats']

    # Champion Divine Ally choice (L3 only). Stored on the build so it shows
    # on the sheet and survives reloads.
    if 'divine_ally' in data and data['divine_ally']:
        build['divine_ally'] = data['divine_ally']

    # L10: Domain Initiate cascade — when Cleric/Champion takes Domain Initiate
    # the wizard sends `chosen_domain`. Stored as a child entry on the feat
    # itself so Character.__init__'s domain resolver picks it up via the same
    # path it uses for Pathbuilder imports.
    chosen_domain = data.get('chosen_domain')
    if chosen_domain and 'feats' in data:
        for ft in build['feats']:
            if isinstance(ft, list) and len(ft) >= 1 and ft[0] == 'Domain Initiate':
                # Store the domain in slot 4 (extra) so the resolver picks it up.
                while len(ft) < 7:
                    ft.append(None)
                ft[4] = chosen_domain.lower()
                ft[5] = 'childChoice'
                break

    if 'proficiencies' not in build: build['proficiencies'] = {}

    # ---- L3+L9+L13: REQUIRED-PROGRESSION VALIDATOR ----------------
    # Reject the level-up if any feat slot or skill increase mandated by the
    # class progression at this level is missing. Returns 400 with a list of
    # what's missing so the wizard can highlight the gaps. Pass `force=true`
    # to override (homebrew / variant rules).
    if not data.get('force_save'):
        missing = _missing_progression_for_level(build, new_level)
        if missing:
            return jsonify({
                "success": False,
                "missing": missing,
                "error": "Level-up is missing required choices: " + ", ".join(missing)
                         + ". Pass force_save=true to override."
            }), 400

    # Normalize Pathbuilder camelCase proficiency keys to snake_case
    PB_KEY_MAP = {'classDC': 'class_dc', 'castingArcane': 'spell_attack', 'castingDivine': 'spell_attack',
                  'castingOccult': 'spell_attack', 'castingPrimal': 'spell_attack'}
    for pb_key, norm_key in PB_KEY_MAP.items():
        if pb_key in build['proficiencies']:
            val = build['proficiencies'][pb_key]
            if isinstance(val, int) and val > 0:
                build['proficiencies'][norm_key] = max(build['proficiencies'].get(norm_key, 0), val)
                if pb_key.startswith('casting') and val > 0:
                    build['proficiencies']['spell_dc'] = max(build['proficiencies'].get('spell_dc', 0), val)

    # --- SERVER-SIDE AUTO-BUMPS FROM CLASS PROGRESSION ---
    # This is the authoritative source: even if the frontend doesn't send auto_bumps,
    # the server applies the correct proficiency increases from CLASS_PROGRESSION.
    class_name = build.get('class', '').lower()
    # Subclass detection: PB exports never populate `build['subclass']` —
    # the field is auto-detected from `build['specials']` at Character
    # construction time and stored on the live PC object. Fall back to
    # that when the build dict is empty so subclass-specific progressions
    # (Warpriest doctrines, Ruffian armor scaling) actually fire on
    # PB-imported PCs. Persist back into the build dict so the saved
    # JSON carries it forward and future loads don't have to re-detect.
    subclass_name = build.get('subclass', '') or getattr(PARTY_LIBRARY.get(pc_name), 'subclass', '') or ''
    if subclass_name and not build.get('subclass'):
        build['subclass'] = subclass_name
    cumulative_bumps = get_class_proficiency_at_level(class_name, new_level, subclass=subclass_name)
    for b_key, b_val in cumulative_bumps.items():
        if b_key in ['fortitude', 'reflex', 'will', 'perception', 'ac', 'unarmored', 'light', 'medium', 'heavy', 'unarmed', 'simple', 'martial', 'advanced', 'class_dc', 'spell_attack', 'spell_dc']:
            build['proficiencies'][b_key] = max(build['proficiencies'].get(b_key, 0), b_val)
    
    # Also apply any frontend-sent auto_bumps (for edge cases like subclass overrides)
    auto_bumps = data.get('auto_bumps', {})
    for b_key, b_val in auto_bumps.items():
        if b_key in ['fortitude', 'reflex', 'will', 'perception', 'ac', 'unarmored', 'light', 'medium', 'heavy', 'unarmed', 'simple', 'martial', 'advanced', 'class_dc', 'spell_attack', 'spell_dc']:
            build['proficiencies'][b_key.lower()] = max(build['proficiencies'].get(b_key.lower(), 0), b_val)
        elif b_key == 'weapons':
            for w in build.get('weapons', []):
                w['prof_val'] = max(w.get('prof_val', 2), b_val)

    # --- SKILL RANK VALIDATION ---
    # The frontend sends ALL proficiencies (skills + saves + armor + etc.)
    # under the `skills` key. Only touch the 16 standard skills and any
    # lore skills here — saves, AC, and armor profs are handled by
    # CLASS_PROGRESSION auto_bumps above.
    STANDARD_SKILLS = {'acrobatics', 'arcana', 'athletics', 'crafting', 'deception', 'diplomacy', 'intimidation', 'medicine', 'nature', 'occultism', 'performance', 'religion', 'society', 'stealth', 'survival', 'thievery'}
    for sk, rank in data.get('skills', {}).items():
        sk_lower = sk.lower()
        # Only write standard skills and lore skills; skip saves/AC/armor
        if sk_lower not in STANDARD_SKILLS and not sk_lower.startswith('lore'):
            continue
        rank = safe_int(rank, 0)
        # Enforce PF2e skill rank gating
        if validate_skill_rank(rank, new_level):
            build['proficiencies'][sk_lower] = rank
        else:
            # Cap to the highest valid rank for this level
            if new_level < 7 and rank > 4: rank = 4
            elif new_level < 15 and rank > 6: rank = 6
            build['proficiencies'][sk_lower] = rank

    # --- FEAT PREREQUISITE VALIDATION ---
    # Reject feats taken THIS level whose prerequisites aren't met (e.g.
    # Battle Medicine without trained Medicine). Checked against the
    # proficiency table AFTER this level's skill increases are applied, so
    # a skill trained this level can satisfy a same-level skill feat (RAW).
    # Only SKILL_FEAT_PREREQS-known feats are gated; unknown feats pass, so
    # there are no false positives on class/ancestry/archetype feats. Gated
    # behind force_save so homebrew/variant picks can override — mirroring
    # the wizard's "Ignore Pre-Reqs" checkbox. Nothing is persisted until
    # save_and_reload_character below, so returning here is side-effect free.
    if not data.get('force_save'):
        illegal = []
        for ft in (build.get('feats') or []):
            if not isinstance(ft, list) or len(ft) < 4:
                continue
            if ft[3] != new_level:
                continue  # only validate feats added at THIS level
            chk = check_feat_prereqs(ft[0], build['proficiencies'])
            if chk and not chk.get('met', True):
                illegal.append(f"{ft[0]} requires {chk.get('reason', 'an unmet prerequisite')}")
        if illegal:
            return jsonify({
                "success": False,
                "illegal": illegal,
                "error": "Level-up has illegal picks: " + "; ".join(illegal)
                         + ". Pass force_save=true to override.",
            }), 400

    if 'spellCasters' in data:
        build['spellCasters'] = data['spellCasters']

    # --- SERVER-SIDE SPELL SLOT VALIDATION ---
    # Ensure perDay values match the correct SPELL_SLOT_TABLES for the new level
    rich = RICH_CLASS_DATA.get(class_name, {})
    if rich.get('spellcasting') and build.get('spellCasters'):
        c_type = rich['spellcasting'].lower()
        table_key = 'spontaneous'
        if 'bounded' in c_type:
            table_key = 'bounded'
        elif 'prepared' in c_type:
            table_key = 'prepared'
        if class_name == 'sorcerer':
            table_key = 'sorcerer'
        slot_table = SPELL_SLOT_TABLES.get(table_key, {}).get(new_level)
        if slot_table:
            correct_perDay = [5] + list(slot_table)
            for caster in build['spellCasters']:
                ct = (caster.get('castingType') or caster.get('spellcastingType') or '').lower()
                if ct in ('focus', 'innate', 'alchemical') or 'focus' in caster.get('name', '').lower():
                    continue
                caster['perDay'] = correct_perDay[:len(caster.get('perDay', correct_perDay))]
                # Ensure perDay is at least as long as the correct table
                while len(caster['perDay']) < len(correct_perDay):
                    caster['perDay'].append(correct_perDay[len(caster['perDay'])])

    # --- MONK PATH TO PERFECTION ---
    monk_path_choice = data.get('monk_path_choice')
    if monk_path_choice and class_name == 'monk' and new_level in MONK_PATH_CONFIG:
        save_key = monk_path_choice.lower()
        if save_key in ['fortitude', 'reflex', 'will']:
            config = MONK_PATH_CONFIG[new_level]
            target_rank = config['target_rank']
            restriction = config.get('restriction')
            existing_paths = build.get('monk_paths', {})
            
            # Validate restriction rules
            valid = True
            if restriction == 'exclude_previous':
                l7_choice = existing_paths.get('7')
                if l7_choice and save_key == l7_choice:
                    valid = False  # L11 must differ from L7
            elif restriction == 'only_previous':
                prev_choices = [existing_paths.get('7'), existing_paths.get('11')]
                prev_choices = [p for p in prev_choices if p]
                if prev_choices and save_key not in prev_choices:
                    valid = False  # L15 must be one of L7/L11 choices
            
            if valid:
                build['proficiencies'][save_key] = max(build['proficiencies'].get(save_key, 0), target_rank)
                if 'monk_paths' not in build: build['monk_paths'] = {}
                build['monk_paths'][str(new_level)] = save_key

    # ─── Animal companion / familiar auto-level (PF2e PC1 p.220) ──────
    # Each level a companion gains:
    #   +1 Perception, AC, all saves, attack bonus, weapon DC
    #   +1 to all skill proficiencies (single bonus, not per-skill)
    #   +(6 + Con mod) HP per level
    # Familiars scale by PC level; their HP is 5 × PC level. We bump
    # any pet entry that looks like a companion or familiar by the level
    # delta. Uses the snapshot we just took (`previous_level`) to compute
    # delta, so a multi-level jump scales correctly.
    prev_level = level_history[str(new_level)].get('previous_level', new_level - 1)
    level_delta = max(0, new_level - prev_level)
    if level_delta > 0:
        def _is_companion(p):
            t = (p.get('type', '') + ' ' + p.get('class', '') + ' ' + p.get('name', '')).lower()
            if any(k in t for k in ['animal companion', 'companion', 'mount', 'eidolon']):
                return True
            return bool(p.get('is_animal_companion'))
        def _is_familiar(p):
            t = (p.get('type', '') + ' ' + p.get('name', '')).lower()
            return 'familiar' in t or bool(p.get('is_familiar'))

        def _bump_companion(p):
            """Apply RAW per-level scaling to an animal companion."""
            con_mod = safe_int(p.get('con_mod'), safe_int(p.get('con'), 2))
            hp_per_level = max(1, 6 + con_mod)
            new_max = safe_int(p.get('hp'), 0) + hp_per_level * level_delta
            old_max = safe_int(p.get('hp'), 0)
            current = safe_int(p.get('current_hp'), old_max)
            # Heal proportionally so a wounded companion doesn't suddenly
            # appear at full HP after level-up.
            if old_max > 0:
                new_current = min(new_max, int(current + hp_per_level * level_delta))
            else:
                new_current = new_max
            p['hp'] = new_max
            p['current_hp'] = new_current
            for k in ('perception', 'ac', 'fort', 'ref', 'will'):
                p[k] = safe_int(p.get(k), 0) + level_delta
            for atk in (p.get('attacks') or []):
                if isinstance(atk, dict):
                    atk['bonus'] = safe_int(atk.get('bonus'), 0) + level_delta
            # Track per-level so future level-ups don't double-bump
            p['_last_levelled_at'] = new_level
            p['level'] = new_level

        def _bump_familiar(p):
            """Familiars: HP = 5 × PC level. Saves/perception use PC's
            check totals at table time, not stored stats — but if the
            stored stats exist, bump them to keep the sheet readable."""
            new_max = max(safe_int(p.get('hp'), 0), 5 * new_level)
            old_max = safe_int(p.get('hp'), 0)
            current = safe_int(p.get('current_hp'), old_max)
            new_current = min(new_max, current + (new_max - old_max))
            p['hp'] = new_max
            p['current_hp'] = new_current
            p['level'] = new_level
            for k in ('perception', 'ac', 'fort', 'ref', 'will'):
                if p.get(k) is not None:
                    p[k] = safe_int(p.get(k), 0) + level_delta
            p['_last_levelled_at'] = new_level

        for bucket_key in ('pets_custom', 'pets'):
            bucket = build.get(bucket_key) or []
            if not isinstance(bucket, list):
                continue
            for pet in bucket:
                if not isinstance(pet, dict):
                    continue
                # Skip pets the player already manually levelled at this
                # level — the _last_levelled_at flag prevents re-applying.
                if pet.get('_last_levelled_at') == new_level:
                    continue
                if _is_companion(pet):
                    _bump_companion(pet)
                elif _is_familiar(pet):
                    _bump_familiar(pet)

    save_and_reload_character(pc_name, pc_json, file_path)
    # Push updated HP / conditions / level to all connected clients so the
    # GM tracker and party view reflect the level-up immediately without
    # a manual refresh. The levelling player's own sheet redirects via JS,
    # but everyone else (GM, other players viewing party) receives this SSE.
    _broadcast_pc_state(pc_name)
    _broadcast_encounter_state()
    return jsonify({"success": True})

@app.route('/api/revert_level/<pc_name>', methods=['POST'])
@require_pc_self_or_gm
def revert_level(pc_name):
    file_path = get_pc_file_path(pc_name)
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        if build.get('level', 1) > 1:
            current_level = build['level']
            
            # Try to restore from level history (clean undo)
            level_history = build.get('level_history') or {}
            snapshot = level_history.get(str(current_level))
            
            if snapshot:
                # Full restore from snapshot
                build['level'] = snapshot['previous_level']
                build['abilities'] = snapshot['previous_abilities']
                build['half_boosts'] = snapshot.get('previous_half_boosts', [])
                build['proficiencies'] = snapshot['previous_proficiencies']
                build['feats'] = snapshot['previous_feats']
                build['spellCasters'] = snapshot.get('previous_spellCasters', build.get('spellCasters', []))
                # Companion / familiar reverse-bump (mirror of submit_levelup).
                # Computed from the level delta so a multi-level revert
                # rolls back the matching number of bumps.
                _delta = max(0, current_level - snapshot['previous_level'])
                if _delta > 0:
                    for bk in ('pets_custom', 'pets'):
                        for pet in (build.get(bk) or []):
                            if not isinstance(pet, dict):
                                continue
                            t = (pet.get('type', '') + ' ' + pet.get('name', '')).lower()
                            is_comp = ('companion' in t or 'mount' in t or 'eidolon' in t
                                       or pet.get('is_animal_companion'))
                            is_fam  = 'familiar' in t or pet.get('is_familiar')
                            if not (is_comp or is_fam):
                                continue
                            con_mod = safe_int(pet.get('con_mod'), safe_int(pet.get('con'), 2))
                            hp_per_level = max(1, 6 + con_mod) if is_comp else 5
                            new_max = max(1, safe_int(pet.get('hp'), 0) - hp_per_level * _delta)
                            new_cur = min(new_max, safe_int(pet.get('current_hp'), new_max))
                            pet['hp'] = new_max
                            pet['current_hp'] = new_cur
                            for k in ('perception', 'ac', 'fort', 'ref', 'will'):
                                if pet.get(k) is not None:
                                    pet[k] = max(0, safe_int(pet.get(k), 0) - _delta)
                            for atk in (pet.get('attacks') or []):
                                if isinstance(atk, dict):
                                    atk['bonus'] = safe_int(atk.get('bonus'), 0) - _delta
                            pet['level'] = snapshot['previous_level']
                            pet.pop('_last_levelled_at', None)
                # Remove this level's history entry
                del level_history[str(current_level)]
                build['level_history'] = level_history
            else:
                # Fallback: best-effort undo for characters without history
                build['level'] -= 1
                # Remove feats added at this level — check both builder (feat[2]) and Pathbuilder (feat[3]) formats
                if 'feats' in build:
                    new_feats = []
                    for feat in build['feats']:
                        if not isinstance(feat, list) or len(feat) < 3:
                            new_feats.append(feat)
                            continue
                        # Builder format: [name, type, level, desc] — level at index 2
                        # Pathbuilder format: [name, null, category, level, ...] — level at index 3
                        feat_level = None
                        if len(feat) >= 4 and isinstance(feat[3], int):
                            feat_level = feat[3]  # Pathbuilder
                        elif isinstance(feat[2], int):
                            feat_level = feat[2]  # Builder
                        
                        if feat_level != current_level:
                            new_feats.append(feat)
                    build['feats'] = new_feats
            
            # Clean up monk path choice if reverting a path level
            if 'monk_paths' in build and str(current_level) in build['monk_paths']:
                reverted_save = build['monk_paths'].pop(str(current_level))
                if reverted_save and reverted_save in build.get('proficiencies', {}):
                    cumulative = get_class_proficiency_at_level(build.get('class', ''), build['level'])
                    base_rank = cumulative.get(reverted_save, 0)
                    for plvl, psave in build.get('monk_paths', {}).items():
                        if psave == reverted_save and int(plvl) <= build['level']:
                            path_rank = MONK_PATH_CONFIG.get(int(plvl), {}).get('target_rank', 0)
                            base_rank = max(base_rank, path_rank)
                    build['proficiencies'][reverted_save] = base_rank
            
            save_and_reload_character(pc_name, pc_json, file_path)
            _broadcast_pc_state(pc_name)
            _broadcast_encounter_state()
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Character not found or already at Level 1."})

# =============================================================================
# PDF CHARACTER EXPORT
# =============================================================================
@app.route('/api/export_pdf/<pc_name>')
def export_pdf(pc_name):
    """Generate a printable PDF character sheet."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    pc = PARTY_LIBRARY[pc_name]
    
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        from reportlab.lib.colors import HexColor
        import io
        
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        w, h = letter
        
        # Colors
        bg = HexColor('#1C1917')
        panel = HexColor('#2E2B25')
        border = HexColor('#4A453D')
        title_color = HexColor('#FBBF24')
        teal = HexColor('#7DC4C4')
        text_color = HexColor('#EDE5D8')
        label = HexColor('#9E968B')
        white = HexColor('#FFFFFF')
        
        # Background
        c.setFillColor(bg)
        c.rect(0, 0, w, h, fill=1)
        
        # Header
        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 22)
        c.drawString(40, h - 50, pc.name)
        c.setFillColor(label)
        c.setFont("Helvetica", 11)
        c.drawString(40, h - 68, f"Level {pc.level} {pc.ancestry} {pc.class_name}")
        if pc.subclass:
            c.drawString(40, h - 82, pc.subclass)
        
        # Ability Scores row
        y = h - 115
        c.setFillColor(panel)
        c.roundRect(35, y - 10, w - 70, 45, 5, fill=1, stroke=0)
        stats = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA']
        stat_keys = ['str', 'dex', 'con', 'int', 'wis', 'cha']
        col_w = (w - 80) / 6
        for i, (s, k) in enumerate(zip(stats, stat_keys)):
            x = 45 + i * col_w
            mod = pc.mods.get(k, 0)
            c.setFillColor(label)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(x + col_w/2, y + 22, s)
            c.setFillColor(text_color)
            c.setFont("Helvetica-Bold", 16)
            c.drawCentredString(x + col_w/2, y + 2, f"+{mod}" if mod >= 0 else str(mod))
        
        # Defenses row
        y -= 55
        c.setFillColor(panel)
        c.roundRect(35, y - 10, w - 70, 45, 5, fill=1, stroke=0)
        defs = [
            ('AC', str(pc.ac)), ('FORT', f"+{pc.fort}"), ('REF', f"+{pc.ref}"),
            ('WILL', f"+{pc.will}"), ('PER', f"+{pc.perception}"),
            ('HP', f"{pc.current_hp}/{pc.hp}"), ('SPD', f"{pc.active_speed}ft")
        ]
        col_w = (w - 80) / len(defs)
        for i, (lbl, val) in enumerate(defs):
            x = 45 + i * col_w
            c.setFillColor(label)
            c.setFont("Helvetica-Bold", 8)
            c.drawCentredString(x + col_w/2, y + 22, lbl)
            c.setFillColor(teal if lbl in ('AC', 'HP') else text_color)
            c.setFont("Helvetica-Bold", 14)
            c.drawCentredString(x + col_w/2, y + 2, val)
        
        # Skills - left column
        y -= 35
        c.setFillColor(title_color)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(40, y, "Skills")
        y -= 18
        rank_letters = {0: 'U', 2: 'T', 4: 'E', 6: 'M', 8: 'L'}
        for i, sk in enumerate(pc.skills):
            col = 0 if i < len(pc.skills) // 2 + 1 else 1
            row = i if col == 0 else i - (len(pc.skills) // 2 + 1)
            sx = 45 + col * 265
            sy = y - row * 16
            if sy < 80:  # Stop before page bottom
                break
            prof = rank_letters.get(sk['prof_val'], 'U')
            c.setFillColor(teal if prof != 'U' else label)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(sx, sy, f"[{prof}]")
            c.setFillColor(text_color)
            c.setFont("Helvetica", 10)
            c.drawString(sx + 25, sy, sk['name'])
            c.setFont("Helvetica-Bold", 10)
            c.drawRightString(sx + 240, sy, str(sk['total']))
        
        # Attacks section
        atk_y = y - (len(pc.skills) // 2 + 2) * 16
        if atk_y > 120 and pc.attacks:
            c.setFillColor(title_color)
            c.setFont("Helvetica-Bold", 12)
            c.drawString(40, atk_y, "Attacks")
            atk_y -= 18
            for atk in pc.attacks:
                if atk_y < 80: break
                c.setFillColor(text_color)
                c.setFont("Helvetica-Bold", 11)
                c.drawString(50, atk_y, atk['name'])
                strikes_text = " / ".join(s['label'] for s in atk['strikes'])
                c.setFillColor(teal)
                c.setFont("Helvetica", 10)
                c.drawString(200, atk_y, strikes_text)
                c.setFillColor(label)
                c.drawString(380, atk_y, f"Dmg: {atk['damage']}")
                atk_y -= 16
        
        # Footer
        c.setFillColor(label)
        c.setFont("Helvetica", 8)
        c.drawString(40, 30, f"PF2E Dashboard — {pc.name} — Exported {time.strftime('%Y-%m-%d')}")
        
        c.save()
        buf.seek(0)
        
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', pc.name)
        return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f"{safe_name}_character_sheet.pdf")
    except ImportError:
        return jsonify({"error": "reportlab not installed. Add to requirements.txt."}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# =============================================================================
# MONSTER IMPORT
# =============================================================================
@app.route('/api/import_monster', methods=['POST'])
@gm_required
def import_monster():
    """Import a monster from JSON (Foundry PF2E format or simplified)."""
    try:
        if 'file' in request.files:
            raw = request.files['file'].read().decode('utf-8')
            data = json.loads(raw)
        elif request.json:
            data = request.json
        else:
            return jsonify({"error": "No data provided"}), 400
        
        # Accept Foundry format or simplified
        name = data.get('name', '')
        if not name:
            # Try nested format
            name = data.get('system', {}).get('details', {}).get('name', '')
        if not name:
            return jsonify({"error": "Monster has no name"}), 400
        
        # Save to monster_data
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
        file_path = os.path.join(MONSTER_DIR, f"{safe_name}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        
        # Try to load into library
        try:
            m = Monster(data, f"{safe_name}.json")
            MONSTER_LIBRARY[f"{safe_name}.json"] = m
            return jsonify({"success": True, "name": name, "level": m.level})
        except Exception as e:
            return jsonify({"success": True, "name": name, "warning": f"Saved but parse error: {e}"})
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def _save_custom_monster(monster_json, name):
    """Persist a GM-authored monster to the bestiary (atomic write) and register
    it in MONSTER_LIBRARY for reuse. Returns (filename, Monster)."""
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name) or 'Custom_Monster'
    fname = f"{safe_name}.json"
    _atomic_write_json(os.path.join(MONSTER_DIR, fname), monster_json, indent=2)
    m = Monster(monster_json, fname)
    MONSTER_LIBRARY[fname] = m
    return fname, m


@app.route('/api/add_custom_monster', methods=['POST'])
@gm_required
def add_custom_monster():
    """Create a one-off custom monster from the tracker's quick form, persist it
    as a reusable bestiary entry (atomic), and add it to the LIVE encounter.
    (Previously this route did not exist, so the tracker's Add-Custom-Monster
    button silently did nothing -- the /api/add_combatant fallback has no
    'custom' branch.)"""
    data = request.json or {}
    name = (data.get('name') or 'Custom Monster').strip() or 'Custom Monster'
    hp = int(data.get('hp', 20) or 20)
    monster_json = {
        "name": name, "type": "npc",
        "system": {
            "details": {"level": {"value": int(data.get('level', 1) or 1)}},
            "attributes": {
                "hp": {"value": hp, "max": hp},
                "ac": {"value": int(data.get('ac', 15) or 15)},
                "speed": {"value": int(data.get('speed', 25) or 25)},
            },
            "saves": {
                "fortitude": {"value": int(data.get('fort', 5) or 5)},
                "reflex": {"value": int(data.get('ref', 5) or 5)},
                "will": {"value": int(data.get('will', 3) or 3)},
            },
            "perception": {"value": int(data.get('perception', 5) or 5)},
            "traits": {"value": []},
        },
        "items": [{
            "name": (data.get('atk_name') or 'Strike').strip() or 'Strike',
            "type": "melee",
            "system": {
                "bonus": {"value": int(data.get('atk_mod', 8) or 8)},
                "damageRolls": {"0": {"damage": (data.get('atk_dmg') or '1d6+3'),
                                      "damageType": "slashing"}},
                "traits": {"value": []},
            },
        }],
    }
    try:
        _fname, m = _save_custom_monster(monster_json, name)
    except Exception as e:
        return jsonify({"success": False, "error": f"could not create monster: {e}"}), 500
    new_c = copy.deepcopy(m)
    new_c.instance_id = str(uuid.uuid4())
    ACTIVE_ENCOUNTER.append(new_c)
    _persist_encounter_state()
    _broadcast_encounter_state()
    return jsonify({"success": True, "name": name, "instance_id": new_c.instance_id})


@app.route('/api/create_monster', methods=['POST'])
@gm_required
def create_custom_monster():
    """Create a monster from a simple form (name, level, hp, ac, saves, attacks)."""
    data = request.json
    name = data.get('name', 'Unknown Monster')
    
    monster_json = {
        "name": name,
        "type": "npc",
        "system": {
            "details": {"level": {"value": int(data.get('level', 1))}},
            "attributes": {
                "hp": {"value": int(data.get('hp', 20)), "max": int(data.get('hp', 20))},
                "ac": {"value": int(data.get('ac', 15))},
                "speed": {"value": int(data.get('speed', 25))}
            },
            "saves": {
                "fortitude": {"value": int(data.get('fort', 5))},
                "reflex": {"value": int(data.get('ref', 5))},
                "will": {"value": int(data.get('will', 5))}
            },
            "perception": {"value": int(data.get('perception', 5))},
            "traits": {"value": data.get('traits', [])},
        },
        "items": []
    }
    
    # Add strikes
    for i, strike in enumerate(data.get('strikes', [])):
        monster_json['items'].append({
            "name": strike.get('name', f'Strike {i+1}'),
            "type": "melee",
            "system": {
                "bonus": {"value": int(strike.get('attack', 10))},
                "damageRolls": {"0": {"damage": strike.get('damage', '1d8+4'), "damageType": strike.get('type', 'slashing')}},
                "traits": {"value": strike.get('traits', [])},
            }
        })
    
    # Add special actions
    for action in data.get('actions', []):
        monster_json['items'].append({
            "name": action.get('name', 'Action'),
            "type": "action",
            "system": {"description": {"value": action.get('desc', '')}}
        })
    
    try:
        _fname, m = _save_custom_monster(monster_json, name)
        return jsonify({"success": True, "name": name, "level": m.level})
    except Exception as e:
        return jsonify({"success": True, "name": name, "warning": f"Saved but parse error: {e}"})

def _emit_reaction_triggers(*, pc_name=None, instance_id=None, event, damage_amount=None):
    """Surface available reactions to the affected player + GM. Called
    from damage paths after the HP delta lands. Pulls active effects
    from the PC sheet and dispatches the
    engine's reaction matcher.

    A reaction hint is best-effort: if the engine throws, log and move
    on — damage application must not get blocked by a buggy effect.
    """
    try:
        effects_list = []
        target_name = None
        if pc_name and pc_name in PARTY_LIBRARY:
            pc = PARTY_LIBRARY[pc_name]
            effects_list = list(getattr(pc, 'pc_active_effects', []) or [])
            target_name = pc_name
        if not effects_list:
            return
        triggers = effects_service.find_reaction_triggers(effects_list, event=event)
        if not triggers:
            return
        # Combat log + dedicated SSE event for the player sheet to toast.
        for t in triggers:
            label = t['trigger'].get('reaction_name') or t.get('name', 'Reaction')
            _combat_log(
                f"{target_name or 'Target'}: {label} ready ({t['trigger']['event_label']})",
                'condition',
            )
        sse_broadcast('reaction_available', {
            'pc_name': pc_name,
            'instance_id': instance_id,
            'target': target_name,
            'event': event,
            'damage': damage_amount,
            'triggers': triggers,
        })
    except Exception as _e:
        print(f"[REACTION] emit failed for {pc_name or instance_id}: {_e}")


def _expire_token_effects_for_round():
    """Walk every token's active_effects AND every PC's sheet-level
    pc_active_effects and drop any whose round-based duration has fully
    elapsed. Logs expiries to the combat log so the GM sees Bless /
    Bane / Shield falling off automatically. Called from cycle_turn at
    end-of-turn.

    Two storage locations to keep in sync — a sheet-applied Heroism
    mirrors to the map token via add_pc_effect, but only the token side
    used to expire. That left the player sheet showing "Heroism" minutes
    after it should have ended, with no way to clear it but manually
    clicking ×.
    """
    expired_log = []
    # Sheet-side expiry — track which PCs changed so we broadcast only those.
    changed_pcs = set()
    with ENCOUNTER_LOCK:
        for pc_name, pc in PARTY_LIBRARY.items():
            effs = list(getattr(pc, 'pc_active_effects', []) or [])
            if not effs:
                continue
            kept, exp = effects_service.expire_round_effects(effs, ROUND_NUMBER)
            if not exp:
                continue
            pc.pc_active_effects = kept
            changed_pcs.add(pc_name)
            for e in exp:
                expired_log.append((pc_name, e.get('name', '—')))
    for tname, ename in expired_log:
        _combat_log(f"{tname}: {ename} expired", 'condition')
    for pc_name in changed_pcs:
        _persist_pc_combat_state(pc_name)
        _broadcast_pc_state(pc_name)


@app.route('/api/pc/<pc_name>/effect/add', methods=['POST'])
@require_pc_self_or_gm
def add_pc_effect(pc_name):
    """Apply a catalog (or custom) Active Effect to a PC's sheet.
    Body shape mirrors the token effect/add endpoint. Sheet-level
    effects persist with the PC's combat state, so they survive
    server restarts and follow the PC into encounter tracker
    state."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({'success': False, 'error': 'unknown pc'}), 404
    pc = PARTY_LIBRARY[pc_name]
    data = request.json or {}
    catalog_key = data.get('catalog_key')
    if not catalog_key:
        return jsonify({'success': False, 'error': 'catalog_key required'}), 400
    eff = effects_service.instantiate_effect(
        catalog_key,
        effect_id=str(uuid.uuid4())[:8],
        caster=(data.get('caster') or '').strip()[:40] or None,
        current_round=int(ROUND_NUMBER or 1),
        duration_override=data.get('duration_override'),
        custom_modifiers=data.get('custom_modifiers'),
        custom_name=data.get('custom_name'),
        save_dc=data.get('save_dc'),
    )
    if not eff:
        return jsonify({'success': False, 'error': f'unknown catalog key {catalog_key!r}'}), 400
    pc.pc_active_effects = list(getattr(pc, 'pc_active_effects', []) or []) + [eff]
    _persist_pc_combat_state(pc_name)
    _broadcast_pc_state(pc_name)
    _combat_log(f"{pc_name}: gained {eff.get('name')}"
                + (f" from {eff['caster']}" if eff.get('caster') else ''),
                'condition')
    return jsonify({'success': True, 'effect': eff})


@app.route('/api/pc/<pc_name>/effect/remove', methods=['POST'])
@require_pc_self_or_gm
def remove_pc_effect(pc_name):
    """Drop a sheet-level effect by id. Also strips any matching
    mirror entry on the linked map token."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({'success': False, 'error': 'unknown pc'}), 404
    pc = PARTY_LIBRARY[pc_name]
    data = request.json or {}
    effect_id = data.get('effect_id')
    if not effect_id:
        return jsonify({'success': False, 'error': 'effect_id required'}), 400
    before = list(getattr(pc, 'pc_active_effects', []) or [])
    pc.pc_active_effects = [e for e in before if e.get('id') != effect_id]
    if len(pc.pc_active_effects) == len(before):
        return jsonify({'success': False, 'error': 'effect not found'}), 404
    _persist_pc_combat_state(pc_name)
    _broadcast_pc_state(pc_name)
    return jsonify({'success': True})


@app.route('/api/pc/<pc_name>/effects')
def list_pc_effects(pc_name):
    """Read-only view of a PC's sheet-level Active Effects + the
    computed effective stats. Player view fetches this on the
    sheet to render the effects panel."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({'success': False, 'error': 'unknown pc'}), 404
    # Players can only inspect their own sheet.
    if not _is_gm() and session.get('player_name') != pc_name:
        return jsonify({'success': False, 'error': 'forbidden'}), 403
    pc = PARTY_LIBRARY[pc_name]
    eff = pc.compute_effective_stats()
    return jsonify({
        'success': True,
        'effects': list(getattr(pc, 'pc_active_effects', []) or []),
        'effective': eff['effective'],
        'breakdown': eff['breakdown'],
    })


# =====================================================================
#  PARTY CHAT (Tier 4, feature 20) -- lightweight group chat visible to
#  all players + GM. In-memory only, clears on restart. SSE broadcast
#  for real-time delivery.
# =====================================================================

@app.route('/api/chat', methods=['GET'])
def api_chat_get():
    """Return the last N chat messages."""
    with CHAT_LOCK:
        msgs = list(CHAT_MESSAGES[-50:])
    return jsonify({'messages': msgs})


@app.route('/api/chat', methods=['POST'])
def api_chat_send():
    """Send a chat message. GM sees 'GM' as sender; players use their name."""
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text or len(text) > 500:
        return jsonify({'error': 'Message must be 1-500 characters'}), 400
    if _is_gm():
        sender = 'GM'
    else:
        sender = session.get('player_name') or 'Anonymous'
    from datetime import datetime
    msg = {
        'sender': sender,
        'text': text,
        'timestamp': datetime.now().strftime('%H:%M:%S'),
    }
    with CHAT_LOCK:
        CHAT_MESSAGES.append(msg)
        if len(CHAT_MESSAGES) > _CHAT_MAX:
            CHAT_MESSAGES.pop(0)
    sse_broadcast('chat_message', msg)
    _bump_campaign_stat('chat_messages_sent')
    return jsonify({'success': True, 'message': msg})


# =====================================================================
#  SESSION RECAP (Tier 4, feature 21) -- compile session data from the
#  scrapbook, combat log, and loot ledger into a formatted recap.
# =====================================================================

@app.route('/api/session_recap')
@gm_required
def api_session_recap():
    """Compile a formatted session recap from available session data."""
    with SESSION_HIGHLIGHTS_LOCK:
        sh = copy.deepcopy(SESSION_HIGHLIGHTS)

    started = sh.get('started_at', '')
    session_num = sh.get('session_number', '?')

    duration_str = ''
    if started:
        try:
            st = time.strptime(started, '%Y-%m-%d %H:%M')
            start_epoch = time.mktime(st)
            elapsed_min = int((time.time() - start_epoch) / 60)
            hours, mins = divmod(elapsed_min, 60)
            duration_str = f"{hours}h {mins}m" if hours else f"{mins}m"
        except Exception:
            pass

    encounter_count = len(ACTIVE_ENCOUNTER)
    crits = sh.get('crits', [])
    fumbles = sh.get('fumbles', [])
    big_hits = sh.get('big_hits', [])
    big_hits_sorted = sorted(big_hits, key=lambda x: x.get('amount', 0), reverse=True)[:5]
    loot = sh.get('loot', [])

    hp_events = []
    for log_e in COMBAT_LOGS:
        action_str = str(log_e.get('action', ''))
        if 'Dying' in action_str or 'damage' in action_str.lower() or 'heal' in action_str.lower():
            hp_events.append({'name': log_e.get('name', ''), 'action': action_str, 'time': log_e.get('time', '')})

    lines = []
    lines.append(f"SESSION {session_num} RECAP")
    lines.append(f"Date: {started or 'Unknown'}")
    if duration_str:
        lines.append(f"Duration: {duration_str}")
    lines.append("")

    if crits:
        lines.append(f"CRITICAL HITS ({len(crits)}):")
        for c in crits[:10]:
            lines.append(f"  - {c.get('pc', '?')}: {c.get('action', '')} (Round {c.get('round', '?')})")
        lines.append("")

    if fumbles:
        lines.append(f"CRITICAL FAILURES ({len(fumbles)}):")
        for f_entry in fumbles[:10]:
            lines.append(f"  - {f_entry.get('pc', '?')}: {f_entry.get('action', '')} (Round {f_entry.get('round', '?')})")
        lines.append("")

    if big_hits_sorted:
        lines.append("BIGGEST HITS:")
        for bh in big_hits_sorted:
            lines.append(f"  - {bh.get('target', '?')} took {bh.get('amount', 0)} damage (Round {bh.get('round', '?')})")
        lines.append("")

    if loot:
        lines.append("LOOT AWARDED:")
        for lt in loot:
            items_str = ', '.join(f"{i['name']} x{i['qty']}" for i in lt.get('items', []))
            coins = lt.get('coins', {})
            coins_parts = [f"{coins.get(d, 0)} {d}" for d in ('pp', 'gp', 'sp', 'cp') if coins.get(d, 0)]
            coins_str = ', '.join(coins_parts)
            parts = [p for p in [items_str, coins_str] if p]
            lines.append(f"  - {lt.get('pc', '?')}: {'; '.join(parts) if parts else '(empty)'}")
        lines.append("")

    if hp_events:
        lines.append(f"NOTABLE HP EVENTS ({len(hp_events)}):")
        for he in hp_events[:15]:
            lines.append(f"  - [{he.get('time', '')}] {he.get('name', '')}: {he.get('action', '')}")
        lines.append("")

    lines.append(f"Total combat log entries: {len(COMBAT_LOGS)}")
    lines.append(f"Current round: {ROUND_NUMBER}")
    lines.append(f"Combatants in encounter: {encounter_count}")

    recap_text = '\n'.join(lines)
    return jsonify({
        'success': True,
        'recap': recap_text,
        'session_number': session_num,
        'started_at': started,
        'duration': duration_str,
        'crits': len(crits),
        'fumbles': len(fumbles),
        'loot_count': len(loot),
        'log_entries': len(COMBAT_LOGS),
    })


# =====================================================================
#  PLAYER JOURNAL (Tier 4, feature 26)
# =====================================================================

def _journal_path(name):
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    return os.path.join(JOURNAL_DIR, f'{safe}.json')


def _load_journal(name):
    path = _journal_path(name)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'entries': []}


def _save_journal(name, journal):
    path = _journal_path(name)
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(journal, f, indent=2, ensure_ascii=False)
    except IOError:
        pass


@app.route('/api/journal')
def api_journal_get():
    """Read the calling player's journal entries."""
    player = session.get('player_name')
    if not player and not _is_gm():
        return jsonify({'error': 'Not joined as a player'}), 403
    name = request.args.get('name') or player
    if not name:
        return jsonify({'error': 'No player name specified'}), 400
    if name != player and not _is_gm():
        return jsonify({'error': 'Forbidden'}), 403
    journal = _load_journal(name)
    return jsonify(journal)


@app.route('/api/journal', methods=['POST'])
def api_journal_append():
    """Append an entry to the calling player's journal."""
    player = session.get('player_name')
    if not player and not _is_gm():
        return jsonify({'error': 'Not joined as a player'}), 403
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text or len(text) > 2000:
        return jsonify({'error': 'Entry must be 1-2000 characters'}), 400
    name = data.get('name') or player
    if not name:
        return jsonify({'error': 'No player name specified'}), 400
    if name != player and not _is_gm():
        return jsonify({'error': 'Forbidden'}), 403
    from datetime import datetime
    journal = _load_journal(name)
    journal['entries'].append({
        'text': text,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    })
    journal['entries'] = journal['entries'][-500:]
    _save_journal(name, journal)
    return jsonify({'success': True, 'entries': journal['entries']})


@app.route('/api/journal/delete', methods=['POST'])
def api_journal_delete():
    """Delete a journal entry by index."""
    player = session.get('player_name')
    if not player and not _is_gm():
        return jsonify({'error': 'Not joined as a player'}), 403
    data = request.get_json(silent=True) or {}
    idx = data.get('index')
    name = data.get('name') or player
    if not name:
        return jsonify({'error': 'No player name specified'}), 400
    if name != player and not _is_gm():
        return jsonify({'error': 'Forbidden'}), 403
    journal = _load_journal(name)
    try:
        idx = int(idx)
        if 0 <= idx < len(journal['entries']):
            journal['entries'].pop(idx)
            _save_journal(name, journal)
    except (TypeError, ValueError):
        pass
    return jsonify({'success': True, 'entries': journal['entries']})


# =====================================================================
#  TIER 4 NEW GM TOOLS (features 11, 15, 23, 24, 25, 27)
# =====================================================================

# -- In-Game Calendar (Golarion) --------------------------------------
# CALENDAR_FILE bound to the active campaign in _bind_campaign_paths().
GOLARION_MONTHS = [
    {'name': 'Abadius',   'days': 31},
    {'name': 'Calistril', 'days': 28},
    {'name': 'Pharast',   'days': 31},
    {'name': 'Gozran',    'days': 30},
    {'name': 'Desnus',    'days': 31},
    {'name': 'Sarenith',  'days': 30},
    {'name': 'Erastus',   'days': 31},
    {'name': 'Arodus',    'days': 31},
    {'name': 'Rova',      'days': 30},
    {'name': 'Lamashan',  'days': 31},
    {'name': 'Neth',      'days': 30},
    {'name': 'Kuthona',   'days': 31},
]
GOLARION_WEEKDAYS = ['Moonday', 'Toilday', 'Wealday', 'Oathday', 'Fireday', 'Starday', 'Sunday']

def _load_calendar():
    if os.path.exists(CALENDAR_FILE):
        try:
            with open(CALENDAR_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {'year': 4724, 'month': 0, 'day': 1, 'events': []}

def _save_calendar(cal):
    with open(CALENDAR_FILE, 'w', encoding='utf-8') as f:
        json.dump(cal, f, indent=2, ensure_ascii=False)
        f.write('\n')

def _golarion_day_of_week(year, month, day):
    total = 0
    for y in range(1, year):
        total += sum(m['days'] for m in GOLARION_MONTHS)
    for mi in range(month):
        total += GOLARION_MONTHS[mi]['days']
    total += day - 1
    return total % 7

@app.route('/gm/calendar')
@gm_required
def gm_calendar():
    cal = _load_calendar()
    return render_template('calendar.html', calendar=cal,
                           months=GOLARION_MONTHS, weekdays=GOLARION_WEEKDAYS)

@app.route('/api/calendar', methods=['GET'])
@gm_required
def api_calendar_get():
    cal = _load_calendar()
    mi = GOLARION_MONTHS[cal['month']]
    dow = _golarion_day_of_week(cal['year'], cal['month'], cal['day'])
    return jsonify({'success': True, 'year': cal['year'], 'month': cal['month'],
        'month_name': mi['name'], 'day': cal['day'], 'days_in_month': mi['days'],
        'weekday': GOLARION_WEEKDAYS[dow], 'events': cal.get('events', []),
        'months': [m['name'] for m in GOLARION_MONTHS]})

@app.route('/api/calendar/advance', methods=['POST'])
@gm_required
def api_calendar_advance():
    data = request.json or {}
    days = int(data.get('days', 1))
    cal = _load_calendar()
    for _ in range(abs(days)):
        if days > 0:
            cal['day'] += 1
            if cal['day'] > GOLARION_MONTHS[cal['month']]['days']:
                cal['day'] = 1
                cal['month'] += 1
                if cal['month'] >= 12:
                    cal['month'] = 0
                    cal['year'] += 1
        else:
            cal['day'] -= 1
            if cal['day'] < 1:
                cal['month'] -= 1
                if cal['month'] < 0:
                    cal['month'] = 11
                    cal['year'] -= 1
                cal['day'] = GOLARION_MONTHS[cal['month']]['days']
    _save_calendar(cal)
    mi = GOLARION_MONTHS[cal['month']]
    dow = _golarion_day_of_week(cal['year'], cal['month'], cal['day'])
    return jsonify({'success': True, 'year': cal['year'], 'month': cal['month'],
        'month_name': mi['name'], 'day': cal['day'], 'days_in_month': mi['days'],
        'weekday': GOLARION_WEEKDAYS[dow]})

@app.route('/api/calendar/set', methods=['POST'])
@gm_required
def api_calendar_set():
    data = request.json or {}
    cal = _load_calendar()
    if 'year' in data: cal['year'] = int(data['year'])
    if 'month' in data: cal['month'] = max(0, min(11, int(data['month'])))
    if 'day' in data:
        max_day = GOLARION_MONTHS[cal['month']]['days']
        cal['day'] = max(1, min(max_day, int(data['day'])))
    _save_calendar(cal)
    return jsonify({'success': True})

@app.route('/api/calendar/event', methods=['POST'])
@gm_required
def api_calendar_event():
    data = request.json or {}
    cal = _load_calendar()
    if data.get('action') == 'remove':
        eid = data.get('id')
        cal['events'] = [e for e in cal.get('events', []) if e.get('id') != eid]
    else:
        evt = {'id': str(uuid.uuid4())[:8],
               'year': int(data.get('year', cal['year'])),
               'month': int(data.get('month', cal['month'])),
               'day': int(data.get('day', cal['day'])),
               'title': str(data.get('title', ''))[:200],
               'note': str(data.get('note', ''))[:500]}
        cal.setdefault('events', []).append(evt)
    _save_calendar(cal)
    return jsonify({'success': True, 'events': cal.get('events', [])})

# -- Encounter Templates ----------------------------------------------
PF2E_ENCOUNTER_BUDGETS = [
    {'label': 'Trivial', 'xp': 40,  'desc': 'Barely a challenge'},
    {'label': 'Low',     'xp': 60,  'desc': 'A straightforward fight'},
    {'label': 'Moderate','xp': 80,  'desc': 'A meaningful challenge'},
    {'label': 'Severe',  'xp': 120, 'desc': 'Dangerous and draining'},
    {'label': 'Extreme', 'xp': 160, 'desc': 'Potentially deadly'},
]
CREATURE_XP_BY_DIFF = {-4: 10, -3: 15, -2: 20, -1: 30, 0: 40, 1: 60, 2: 80, 3: 120, 4: 160}

@app.route('/gm/encounter_templates')
@gm_required
def gm_encounter_templates():
    return render_template('encounter_templates.html',
                           budgets=PF2E_ENCOUNTER_BUDGETS, xp_table=CREATURE_XP_BY_DIFF)

@app.route('/api/encounter_templates/creatures')
@gm_required
def api_encounter_template_creatures():
    try: party_level = int(request.args.get('party_level', 3))
    except (TypeError, ValueError): party_level = 3
    results = []
    for path, m in MONSTER_LIBRARY.items():
        diff = m.level - party_level
        if -4 <= diff <= 4:
            xp = CREATURE_XP_BY_DIFF.get(diff, 40)
            results.append({'name': m.name, 'level': m.level, 'diff': diff,
                'xp': xp, 'traits': list(getattr(m, 'traits', []) or [])[:5], 'path': path})
    results.sort(key=lambda r: (r['level'], r['name']))
    return jsonify({'success': True, 'creatures': results[:200]})

# -- Skill Challenge Mode ---------------------------------------------
ACTIVE_SKILL_CHALLENGES = {}

@app.route('/gm/skill_challenge')
@gm_required
def gm_skill_challenge():
    return render_template('skill_challenge.html', challenges=ACTIVE_SKILL_CHALLENGES)

@app.route('/api/skill_challenge', methods=['GET'])
def api_skill_challenge_get():
    if not ACTIVE_SKILL_CHALLENGES:
        return jsonify({'success': True, 'challenge': None})
    key = list(ACTIVE_SKILL_CHALLENGES.keys())[-1]
    return jsonify({'success': True, 'challenge': ACTIVE_SKILL_CHALLENGES[key]})

@app.route('/api/skill_challenge/create', methods=['POST'])
@gm_required
def api_skill_challenge_create():
    data = request.json or {}
    cid = str(uuid.uuid4())[:8]
    ch = {'id': cid, 'name': str(data.get('name', 'Skill Challenge'))[:100],
          'required_successes': max(1, int(data.get('required_successes', 4))),
          'max_failures': max(1, int(data.get('max_failures', 3))),
          'dc': int(data.get('dc', 15)),
          'skills': [s.strip() for s in str(data.get('skills', '')).split(',') if s.strip()][:10],
          'successes': 0, 'failures': 0, 'log': [], 'status': 'active'}
    ACTIVE_SKILL_CHALLENGES[cid] = ch
    sse_broadcast('skill_challenge', ch)
    return jsonify({'success': True, 'challenge': ch})

@app.route('/api/skill_challenge/mark', methods=['POST'])
@gm_required
def api_skill_challenge_mark():
    data = request.json or {}
    cid = data.get('id')
    result = data.get('result', 'success')
    if not cid or cid not in ACTIVE_SKILL_CHALLENGES:
        return jsonify({'error': 'Challenge not found'}), 404
    ch = ACTIVE_SKILL_CHALLENGES[cid]
    if ch['status'] != 'active':
        return jsonify({'error': 'Challenge is no longer active'}), 400
    ch['log'].append({'pc': data.get('pc_name', ''), 'skill': data.get('skill', ''), 'result': result})
    if result == 'success': ch['successes'] += 1
    elif result == 'crit_success': ch['successes'] += 2
    elif result == 'crit_failure': ch['failures'] += 2
    else: ch['failures'] += 1
    if ch['successes'] >= ch['required_successes']: ch['status'] = 'victory'
    elif ch['failures'] >= ch['max_failures']: ch['status'] = 'defeat'
    sse_broadcast('skill_challenge', ch)
    return jsonify({'success': True, 'challenge': ch})

@app.route('/api/skill_challenge/end', methods=['POST'])
@gm_required
def api_skill_challenge_end():
    data = request.json or {}
    cid = data.get('id')
    if cid and cid in ACTIVE_SKILL_CHALLENGES: del ACTIVE_SKILL_CHALLENGES[cid]
    sse_broadcast('skill_challenge', {'id': cid, 'status': 'ended'})
    return jsonify({'success': True})

# -- Rest & Recovery Wizard -------------------------------------------
@app.route('/gm/rest')
@gm_required
def gm_rest_wizard():
    party = []
    for name, pc in PARTY_LIBRARY.items():
        party.append({'name': name, 'current_hp': pc.current_hp, 'max_hp': pc.hp,
            'level': pc.level, 'con_mod': int(getattr(pc, 'mods', {}).get('con', 0) or 0),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'focus_current': getattr(pc, 'current_focus', 0),
            'focus_max': getattr(pc, 'focus_max', 0),
            'class_name': getattr(pc, 'class_name', '')})
    return render_template('rest_wizard.html', party=party)

@app.route('/api/rest/treat_wounds', methods=['POST'])
@gm_required
def api_treat_wounds():
    data = request.json or {}
    target_name = data.get('target', '')
    try: dc = int(data.get('dc', 15))
    except (TypeError, ValueError): dc = 15
    healer_name = data.get('healer', '')
    if target_name not in PARTY_LIBRARY:
        return jsonify({'error': 'PC not found'}), 404
    pc = PARTY_LIBRARY[target_name]
    med_mod = 0
    healer = PARTY_LIBRARY.get(healer_name)
    if healer:
        sk_list = getattr(healer, 'skills', [])
        if isinstance(sk_list, list):
            for sk in sk_list:
                if isinstance(sk, dict) and sk.get('name', '').lower() == 'medicine':
                    med_mod = sk.get('total', 0); break
        elif isinstance(sk_list, dict): med_mod = sk_list.get('medicine', 0)
    d20 = random.randint(1, 20)
    adjusted = d20 + med_mod
    if d20 == 20: adjusted += 10
    if d20 == 1:  adjusted -= 10
    diff = adjusted - dc
    if diff >= 10:   degree = 'critical_success'
    elif diff >= 0:  degree = 'success'
    elif diff > -10: degree = 'failure'
    else:            degree = 'critical_failure'
    hp_change, rolls, detail = 0, [], 'No effect'
    bonus = {20: 10, 30: 30, 40: 50}.get(dc, 0)
    if degree == 'critical_success':
        rolls = [random.randint(1, 8) for _ in range(4)]
        hp_change = sum(rolls) + bonus
        detail = f"4d8: [{', '.join(str(r) for r in rolls)}]{' + ' + str(bonus) if bonus else ''} = {hp_change} HP healed"
    elif degree == 'success':
        rolls = [random.randint(1, 8) for _ in range(2)]
        hp_change = sum(rolls) + bonus
        detail = f"2d8: [{', '.join(str(r) for r in rolls)}]{' + ' + str(bonus) if bonus else ''} = {hp_change} HP healed"
    elif degree == 'critical_failure':
        r = random.randint(1, 8); hp_change = -r
        detail = f"1d8 damage: [{r}] = {r} HP lost"
    if hp_change > 0: pc.current_hp = min(pc.hp, pc.current_hp + hp_change)
    elif hp_change < 0: pc.current_hp = max(0, pc.current_hp + hp_change)
    if hp_change != 0: _broadcast_pc_state(target_name)
    return jsonify({'success': True, 'target': target_name, 'healer': healer_name,
        'd20': d20, 'modifier': med_mod, 'total': d20 + med_mod, 'dc': dc,
        'degree': degree, 'hp_change': hp_change, 'detail': detail,
        'new_hp': pc.current_hp, 'max_hp': pc.hp})

@app.route('/api/rest/apply', methods=['POST'])
@gm_required
def api_rest_apply():
    data = request.json or {}
    rest_type = data.get('type', 'long')
    refocus = data.get('refocus', [])
    results = []
    for name, pc in PARTY_LIBRARY.items():
        changes = {'name': name}
        if rest_type == 'long':
            con_mod = int(getattr(pc, 'mods', {}).get('con', 0) or 0)
            hp_before = pc.current_hp
            pc.current_hp = min(pc.hp, pc.current_hp + max(1, con_mod) * pc.level)
            changes['hp_regained'] = pc.current_hp - hp_before
            changes['new_hp'] = pc.current_hp
            drained_val = max(0, pc.conditions.get('drained', 0) - 1)
            doomed_val = pc.conditions.get('doomed', 0)
            pc.conditions = {'frightened': 0, 'sickened': 0, 'dying': 0, 'wounded': 0,
                'doomed': doomed_val, 'drained': drained_val, 'fatigued': 0,
                'stunned': 0, 'slowed': 0, 'stupefied': 0, 'enfeebled': 0, 'clumsy': 0,
                'prone': False, 'off_guard': False, 'concealed': False,
                'hidden': False, 'undetected': False}
            changes['conditions_cleared'] = True
            pc.current_focus = pc.focus_max
            changes['focus'] = pc.current_focus
            pc.temp_hp_manual = 0
            try: pc.temp_hp = pc.toggle_effects_summary.get('temp_hp', 0)
            except Exception: pc.temp_hp = 0
        if name in refocus and rest_type == 'short':
            pc.current_focus = min(pc.focus_max, pc.current_focus + 1)
            changes['focus_regained'] = True
            changes['focus'] = pc.current_focus
        file_path = get_pc_file_path(name)
        if file_path and os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
            build = pc_json.get('build', pc_json)
            build['current_hp'] = pc.current_hp
            build['current_focus'] = pc.current_focus
            build['conditions'] = dict(pc.conditions)
            if rest_type == 'long':
                build['expended_slots'] = {}; build['prepared_spells'] = {}; build['cast_prep'] = {}
            save_and_reload_character(name, pc_json, file_path)
        _broadcast_pc_state(name)
        results.append(changes)
    return jsonify({'success': True, 'results': results})

# -- Quick Status Board -----------------------------------------------
def _cosmere_status_party(docs):
    """Party-status rows for Cosmere PCs (mirrors the PF2e shape the status board
    expects). Live health/focus/investiture/conditions come from each PC's saved
    play_state; the maxes + defenses come from the build. `hero_points` is None
    (Cosmere has none) so the board can hide that stat."""
    import systems.cosmere.build as _cb
    hb = _cosmere_homebrew_store()
    out = []
    for d in docs:
        try:
            b = _cb.CosmereBuild(d.get('build') or {}, homebrew=hb)
        except Exception:
            continue
        ps = d.get('play_state') if isinstance(d.get('play_state'), dict) else {}
        def _ps(key, default):
            try:
                return int(ps[key])
            except (KeyError, TypeError, ValueError):
                return int(default)
        hmax = b.health_max()
        cur = _ps('health', hmax)
        o = b.order()
        out.append({
            'name': d.get('name', 'Unknown'),
            'ancestry': b.ancestry,
            'class_name': (o['name'] if o else b.path) or '',
            'level': b.level,
            'ac': b.defenses().get('phy', 10),
            'current_hp': cur, 'max_hp': hmax,
            'hp_pct': round(cur / hmax * 100) if hmax > 0 else 0,
            'temp_hp': 0,
            'conditions': {k: v for k, v in (ps.get('conditions') or {}).items() if v},
            'hero_points': None,
            'injuries': _ps('injuries', 0),
            'focus_current': _ps('focus', b.focus_max()), 'focus_max': b.focus_max(),
            'investiture_current': _ps('investiture', b.investiture_max()),
            'investiture_max': b.investiture_max(),
            'exploration_activity': '',
        })
    return out


@app.route('/status')
def status_board():
    # The party board must follow the active system — a Cosmere campaign was
    # getting a blank board because this iterated the (empty) PF2e PARTY_LIBRARY.
    if _active_system() == 'cosmere':
        party = _cosmere_status_party(_list_cosmere_pcs())
        return render_template('status_board.html', party=party, system='cosmere')
    party = []
    for name, pc in PARTY_LIBRARY.items():
        party.append({'name': name, 'current_hp': pc.current_hp, 'max_hp': pc.hp,
            'hp_pct': round(pc.current_hp / pc.hp * 100) if pc.hp > 0 else 0,
            'level': pc.level, 'class_name': getattr(pc, 'class_name', ''),
            'ancestry': getattr(pc, 'ancestry', ''),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'hero_points': getattr(pc, 'hero_points', 1),
            'exploration_activity': str(getattr(pc, 'exploration_activity', '') or ''),
            'focus_current': getattr(pc, 'current_focus', 0),
            'focus_max': getattr(pc, 'focus_max', 0),
            'temp_hp': int(getattr(pc, 'temp_hp', 0) or 0),
            'ac': int(getattr(pc, 'ac', 0) or 0)})
    return render_template('status_board.html', party=party, system='pf2e')


# =====================================================================
#  CAMPAIGN STATS (Tier 4, feature 30)
# =====================================================================

def _load_campaign_stats():
    if os.path.exists(CAMPAIGN_STATS_FILE):
        try:
            with open(CAMPAIGN_STATS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {
        'total_rolls': 0, 'total_crits': 0, 'total_fumbles': 0,
        'total_damage_dealt': 0, 'total_healing': 0,
        'total_combat_rounds': 0, 'total_encounters': 0,
        'hero_points_awarded': 0, 'conditions_applied': 0,
        'sessions_started': 0, 'chat_messages_sent': 0,
    }


def _save_campaign_stats(stats):
    try:
        with open(CAMPAIGN_STATS_FILE, 'w', encoding='utf-8') as f:
            json.dump(stats, f, indent=2)
    except IOError:
        pass


def _bump_campaign_stat(key, amount=1):
    """Thread-safe increment of a campaign stat counter."""
    stats = _load_campaign_stats()
    stats[key] = stats.get(key, 0) + amount
    _save_campaign_stats(stats)


@app.route('/gm/stats')
@gm_required
def gm_stats():
    """Campaign stats dashboard."""
    stats = _load_campaign_stats()
    return render_template('campaign_stats.html', stats=stats)


@app.route('/api/campaign_stats')
@gm_required
def api_campaign_stats():
    return jsonify(_load_campaign_stats())


# =====================================================================
#  SERVICE WORKER + MANIFEST (Tier 4, feature 31 - PWA)
# =====================================================================

@app.route('/sw.js')
def service_worker():
    """Serve the service worker from the root path (scope requirement)."""
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'sw.js',
                               mimetype='application/javascript')


@app.route('/manifest.json')
def web_manifest():
    """Serve the web app manifest from the root path."""
    return send_from_directory(os.path.join(BASE_DIR, 'static'), 'manifest.json',
                               mimetype='application/manifest+json')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    # Secure default: the interactive Werkzeug debugger (source view + an RCE
    # console) must never come up unless FLASK_DEBUG is explicitly 'true'. Prod
    # runs under gunicorn (this __main__ block doesn't execute there) regardless.
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    # Launch the debounced persistence flush thread. With Flask debug reloader,
    # _start_persistence_thread() becomes a no-op in the parent process because
    # the daemon thread is tied to the child; the WERKZEUG_RUN_MAIN check keeps
    # us from starting it twice.
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_persistence_thread()
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)