from flask import Flask, render_template, request, redirect, url_for, send_file, send_from_directory, jsonify, session, Response
import sqlite3
import json
import math
import os
import uuid
import copy
import re
import urllib.parse
import markdown
import random
import time
import queue
import threading
from functools import wraps

from class_matrix import ABP_TABLE, get_abp_bonus, CLASS_MATRIX, SUBCLASS_MATRIX, SPELL_SLOT_TABLES, PASSIVE_FEATURES, CLASS_FEATURES
from class_matrix import CLASS_PROGRESSION, SUBCLASS_PROGRESSION, get_class_proficiency_at_level, get_new_bumps_at_level, validate_skill_rank, ANCESTRY_SPEEDS, ANCESTRY_SENSES, ANCESTRY_SIZES, ANCESTRY_FEATURES
from class_matrix import MONK_PATH_CONFIG
from class_matrix import SUBCLASS_DESCRIPTIONS
from class_matrix import SPELL_ACTIONS, get_action_cost
from class_matrix import SKILL_FEAT_PREREQS, check_feat_prereqs, RANK_VALUES
from pf2e_generator import RobustPF2eGenerator

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'pf2e-gm-dashboard-' + str(uuid.uuid4()))

# --- GM ACCESS CONTROL ---
GM_PASSWORD = os.environ.get('GM_PASSWORD', '')  # Set in Railway env vars

def gm_required(f):
    """Decorator: requires GM password to access route."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not GM_PASSWORD:
            return f(*args, **kwargs)  # No password set = open access (local dev)
        if session.get('gm_authenticated'):
            return f(*args, **kwargs)
        return redirect('/gm/login')
    return decorated

def _is_gm():
    """Return True if the caller is effectively the GM.

    Mirrors gm_required: when GM_PASSWORD is unset we're in local dev, so
    everyone is treated as GM. Use this instead of session.get('gm_authenticated')
    in endpoints that also need to distinguish 'player view' (filtered) vs
    'GM view' (raw). Otherwise local dev shows the GM the filtered player state.
    """
    return (not GM_PASSWORD) or session.get('gm_authenticated', False)

# GM-only API prefixes — these are encounter/tracker/vault APIs that players shouldn't access
GM_API_PREFIXES = (
    '/api/add_combatant', '/api/add_party', '/api/remove_combatant', '/api/clear_encounter',
    '/api/adjust_hp/',  # Encounter tracker HP (not adjust_party_hp which is player-facing)
    '/api/toggle_condition/', '/api/set_persistent_damage/', '/api/toggle_elite_weak/',
    '/api/update_initiative/', '/api/roll_npc_initiative', '/api/sort_initiative',
    '/api/cycle_turn/', '/api/delay_turn/', '/api/reenter_initiative/',
    '/api/save_encounter', '/api/load_encounter',
    '/api/monster_search', '/api/stage_encounter', '/api/party_stats',
    '/api/monster_statblock/', '/api/combatant_stats/',
    '/api/toggle_combatant_visibility/',
    # /api/tracker_state returns raw combatant data (AC, HP, strikes, saves,
    # and visible_to_players). It's only consumed by templates/tracker.html
    # which is GM-only, so gate it here. /api/player_state is the sanitized
    # counterpart for player views.
    '/api/tracker_state',
    # /api/gm_secret_log holds GM-only secret rolls (stealth/perception/etc.)
    # — never fetched by player templates. Keep it GM-only.
    '/api/gm_secret_log',
    '/api/generate/', '/api/vault_',
)

@app.before_request
def check_gm_access():
    """Block GM API routes for unauthenticated users."""
    if not GM_PASSWORD:
        return  # No password = open access
    path = request.path
    if any(path.startswith(prefix) for prefix in GM_API_PREFIXES):
        if not session.get('gm_authenticated'):
            return jsonify({"error": "GM authentication required"}), 403


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
    # Short-circuit: client must accept gzip, body must be eligible.
    accept = request.headers.get('Accept-Encoding', '')
    if 'gzip' not in accept.lower():
        return response
    # Never compress SSE — it must stream event-by-event with immediate flush.
    if response.mimetype == 'text/event-stream':
        return response
    # Only compress the mime types that actually benefit.
    ctype = (response.content_type or '').split(';', 1)[0].strip().lower()
    if not any(ctype.startswith(p) for p in _GZIP_MIME_PREFIXES):
        return response
    # Already encoded? Leave alone.
    if response.headers.get('Content-Encoding'):
        return response
    # Passthrough / direct responses (e.g. send_file in chunked mode) —
    # we'd have to buffer them entirely; not worth the RAM cost here.
    if response.direct_passthrough:
        return response
    try:
        data = response.get_data()
    except RuntimeError:
        return response
    if len(data) < _GZIP_MIN_BYTES:
        return response
    # Compress.
    buf = _BytesIO()
    with _gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6) as gz:
        gz.write(data)
    compressed = buf.getvalue()
    response.set_data(compressed)
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Content-Length'] = str(len(compressed))
    # Vary so intermediaries cache gzip + non-gzip variants separately.
    vary = response.headers.get('Vary')
    if vary:
        if 'Accept-Encoding' not in vary:
            response.headers['Vary'] = vary + ', Accept-Encoding'
    else:
        response.headers['Vary'] = 'Accept-Encoding'
    return response


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)  # Railway volume mount or local
MONSTER_DIR = os.path.join(DATA_DIR, 'monster_data')
PARTY_DIR = os.path.join(DATA_DIR, 'party_data') 
ENCOUNTER_DIR = os.path.join(DATA_DIR, 'saved_encounters') 
OBSIDIAN_DIR = os.path.join(DATA_DIR, 'obsidian_vault')
MAP_DIR = os.path.join(DATA_DIR, 'maps')  # VTT map images and state
DB_PATH = os.path.join(BASE_DIR, 'pf2e_database.db')  # Ships with repo, read-only
COMPENDIUM_DATA_DIR = os.path.join(BASE_DIR, 'compendium_data')

# Ensure data directories exist (important for fresh deployments)
for _dir in [MONSTER_DIR, PARTY_DIR, ENCOUNTER_DIR, MAP_DIR, os.path.join(PARTY_DIR, 'portraits')]:
    os.makedirs(_dir, exist_ok=True)

MONSTER_LIBRARY = {}
PARTY_LIBRARY = {}
PENDING_INITIATIVES = {}
ACTIVE_ENCOUNTER = []
TURN_INDEX = 0
ROUND_NUMBER = 1
COMBAT_LOGS = []

# --- VTT MAP STATE ---
ACTIVE_MAP = {
    'id': None,
    'name': None,
    'image': None,  # filename
    'grid_size': 70,  # pixels per square
    'grid_offset_x': 0,
    'grid_offset_y': 0,
    'tokens': [],  # [{id, name, x, y, size, color, hp, max_hp, ac, speed, is_pc, conditions, assigned_player, visible_to_players, initiative}]
    'walls': [],  # [{id, points: [[x,y],...], type: 'normal'|'terrain'|'invisible'|'ethereal'|'door', closed: bool, open: bool, secret: bool, hidden_dc: int, discovered_by: [player_name]}]
    'explored': [],  # List of "x,y" strings for explored grid cells
    'difficult_terrain': [],  # [{x, y}] grid cells with difficult terrain
    'spawn_point': None,  # {x, y} grid position for party spawn
    'player_control': True,  # Can players move their own tokens?
    'gm_notes': [],  # [{id, x, y, text, color, icon}] GM-only pins/notes (never sent to players)
    # --- Lighting (Phase 2) --------------------------------------------------
    # ambient_light drives the client's fog layer. NOTE: we deliberately do not
    # reuse `vision_mode` here — that field was already taken to describe the
    # fog-reveal policy (e.g. 'explored' = cells stay seen after the PC leaves).
    #   'bright' — no darkness; walls still block LOS (outdoors / default)
    #   'dim'    — everything dim unless a light covers it
    #   'dark'   — pitch black unless lit or viewer has darkvision
    'ambient_light': 'bright',
    # Stand-alone light sources placed by the GM. Tokens with their own
    # emit_light field carry their own attached torch; lights listed here are
    # free-standing (braziers, wall sconces, dropped torches).
    #   {id, x, y, bright, dim, color, enabled, attached_to: token_id|null,
    #    name}
    # Radii are in squares (grid units), not pixels.
    'lights': [],
    # --- Templates (Phase 3) -------------------------------------------------
    # AOE templates — burst (circle from point), emanation (circle from token),
    # cone (PF2e 90° wedge by default), line (rectangle). Radii/lengths are in
    # squares; pixel coords on the map.
    #   {id, type: 'burst'|'emanation'|'cone'|'line',
    #    x, y,                       # origin (pixel coords)
    #    attached_to: token_id|null, # emanation follows this token
    #    radius, length, width,      # in squares (type-dependent)
    #    direction, angle,           # degrees — direction is aim, angle is spread
    #    color, name, owner, source, temporary}
    'templates': [],
}
MAP_LOCK = threading.Lock()

# Reentrant lock for encounter/PC state mutations. Used by internal helpers
# (_combat_log, _broadcast_*, _get_tracker_state, _do_persist_*) so that
# multi-step reads/writes are consistent under threaded=True.
ENCOUNTER_LOCK = threading.RLock()

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
            }
            encounter_data['combatants'].append(entry)
    try:
        os.makedirs(ENCOUNTER_DIR, exist_ok=True)
        with open(os.path.join(ENCOUNTER_DIR, '_autosave.json'), 'w', encoding='utf-8') as f:
            json.dump(encounter_data, f, indent=2)
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
_SSE_MAX_SUBSCRIBERS = 50  # Hard cap to prevent memory leaks
_SSE_STALE_TIMEOUT = 120  # Seconds before a non-consuming queue is considered stale

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

    with _sse_lock:
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
    with _PC_BROADCAST_LOCK:
        _PC_BROADCAST_PENDING.add(pc_name)
        if _PC_BROADCAST_TIMER is None:
            t = threading.Timer(_PC_BROADCAST_DELAY, _flush_pc_broadcasts)
            t.daemon = True
            _PC_BROADCAST_TIMER = t
            t.start()

def _do_broadcast_pc_state(pc_name):
    """Actually compute and emit the PC-state SSE frame."""
    if pc_name not in PARTY_LIBRARY:
        return
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
            'persistent_damage': list(getattr(pc, 'persistent_damage', []) or []),
            'exploration_activity': str(getattr(pc, 'exploration_activity', '') or ''),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'focus': getattr(pc, 'current_focus', 0),
            'hero_points': getattr(pc, 'hero_points', 1),
            'spell_casters': spell_summary,
        }
    # Read expended slots from disk outside the lock (file I/O)
    expended_slots = {}
    try:
        fp = get_pc_file_path(pc_name)
        if fp and os.path.exists(fp):
            with open(fp, 'r', encoding='utf-8') as f:
                build = json.load(f).get('build', {})
                expended_slots = build.get('expended_slots', {})
    except Exception:
        pass
    payload['expended_slots'] = expended_slots
    sse_broadcast('pc_update', payload)

def _broadcast_encounter_state():
    """Broadcast the full encounter state to all SSE clients.

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
        combatants = []
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            entry = {
                'name': c.name,
                'is_pc': c.is_pc,
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
            else:
                pct = c.current_hp / c.hp if c.hp > 0 else 0
                if c.current_hp == 0:
                    entry['hp_status'] = 'Dead'
                elif pct <= 0.5:
                    entry['hp_status'] = 'Wounded'
                else:
                    entry['hp_status'] = ''
            entry['conditions'] = {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False}
            combatants.append(entry)
        payload = {
            'encounter': combatants,
            'round': ROUND_NUMBER,
            'active_name': active_name,
            'turn_index': TURN_INDEX,
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
VALID_GENERATOR_TYPES = {'npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter', 'weather', 'trap', 'rumor', 'settlement', 'treasure_hoard', 'random_event'}

# --- SECURITY: Allowed image extensions for vault image serving ---
ALLOWED_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'}

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
                pc = Character(char_data, f"{os.path.basename(file_path)}[{idx}]")
                PARTY_LIBRARY[pc.name] = pc
        else:
            pc = Character(data, os.path.basename(file_path))
            PARTY_LIBRARY[pc.name] = pc
    except Exception as e:
        print(f"Reload Error for {file_path}: {e}")

def save_and_reload_character(pc_name, pc_json, file_path):
    """Save a character JSON to disk and reload just that character (not the whole compendium)."""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(pc_json, f, indent=4)
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
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(pc_json, f, indent=2)
    except Exception as e:
        print(f"[PERSIST ERROR] {pc_name}: {e}")

# ---- UNIFIED PC STATE MUTATION ----
# Historically every endpoint hand-rolled the four-step dance:
#   mutate pc → sync encounter tracker → persist → broadcast.
# Result: drift (focus wasn't broadcast, temp_hp wasn't tracker-synced, etc.)
# and bugs where one endpoint forgot a step. `apply_pc_delta` centralizes it.
#
# Fields mirrored on the encounter tracker so initiative HUD stays fresh:
_TRACKER_MIRRORED_FIELDS = (
    'current_hp', 'current_focus', 'hero_points', 'temp_hp',
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
    with ENCOUNTER_LOCK:
        pc = PARTY_LIBRARY[pc_name]
        try:
            result = mutator(pc)
        except Exception as e:
            print(f"[apply_pc_delta] {pc_name}: {e}")
            raise
        _sync_tracker_from_pc(pc_name, conditions=sync_conditions)
    if persist:
        _persist_pc_combat_state(pc_name)
    if broadcast:
        _broadcast_pc_state(pc_name)
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

def _sort_encounter():
    global ACTIVE_ENCOUNTER, TURN_INDEX
    active_id = None
    if ACTIVE_ENCOUNTER and 0 <= TURN_INDEX < len(ACTIVE_ENCOUNTER):
        active_id = ACTIVE_ENCOUNTER[TURN_INDEX].instance_id
        
    ACTIVE_ENCOUNTER.sort(key=lambda x: x.initiative, reverse=True)
    if active_id:
        for i, c in enumerate(ACTIVE_ENCOUNTER):
            if c.instance_id == active_id:
                TURN_INDEX = i; break
    else: TURN_INDEX = 0

def calculate_encounter_xp(encounter, party_level):
    xp_map = { -4: 10, -3: 15, -2: 20, -1: 30, 0: 40, 1: 60, 2: 80, 3: 120, 4: 160 }
    total_xp = 0
    for c in encounter:
        if not c.is_pc:
            lvl_diff = max(-4, min(4, c.level - party_level))
            total_xp += xp_map.get(lvl_diff, 160 if lvl_diff > 4 else 10)
    return total_xp

def get_difficulty_label(xp):
    """PF2E encounter difficulty (GM Core p.74, 4-player party)."""
    if xp < 40: return "Trivial", "text-gray-400"
    elif xp < 60: return "Low", "text-green-400"
    elif xp < 80: return "Moderate", "text-yellow-400"
    elif xp < 120: return "Severe", "text-orange-500"
    elif xp < 160: return "Extreme", "text-red-600 font-bold"
    else: return "Impossible", "text-red-600 font-bold animate-pulse"

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
        
        self.name = safe_str(build.get('name'), 'Unknown Hero')
        self.level = safe_int(build.get('level'), 1)
        self.class_name = safe_str(build.get('class'), 'Unknown Class')
        self.subclass = safe_str(build.get('subclass'), '')
        self.ancestry = safe_str(build.get('ancestry'), 'Unknown Ancestry')
        
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
        self.signature_spells = build.get('signature_spells') or []
        self.session_notes = build.get('session_notes') or []
        self.expended_slots = build.get('expended_slots') or {}
        
        self.raw_feats = build.get('feats') or []
        self.raw_spellCasters = build.get('spellCasters') or []
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
        
        # Merge into self.proficiencies (upgrade only — never downgrade)
        COMBAT_PROF_KEYS = {'fortitude', 'reflex', 'will', 'perception', 'ac',
                            'unarmored', 'light', 'medium', 'heavy',
                            'unarmed', 'simple', 'martial', 'advanced',
                            'class_dc', 'spell_attack', 'spell_dc'}
        for key in COMBAT_PROF_KEYS:
            computed = self._class_profs.get(key, 0)
            if computed > 0:
                current = safe_int(self.proficiencies.get(key, 0))
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

        attributes = build.get('attributes') or {}
        anc_hp = safe_int(attributes.get('ancestryhp'), 8)
        cls_hp = safe_int(attributes.get('classhp'), 8)
        
        # Cross-reference against known correct values from DB
        # BUILDER dicts use title case keys; try multiple formats
        for anc_key in [self.ancestry, self.ancestry.lower(), self.ancestry.title()]:
            if anc_key in BUILDER_ANCESTRIES and BUILDER_ANCESTRIES[anc_key].get('hp'):
                anc_hp = safe_int(BUILDER_ANCESTRIES[anc_key]['hp'], anc_hp)
                break
        for cls_key in [self.class_name, self.class_name.lower(), self.class_name.title()]:
            if cls_key in BUILDER_CLASSES and BUILDER_CLASSES[cls_key].get('hp'):
                cls_hp = safe_int(BUILDER_CLASSES[cls_key]['hp'], cls_hp)
                break
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

        for caster in self.raw_spellCasters:
            cast_type = safe_str(caster.get('castingType') or caster.get('spellcastingType'), 'Prepared')
            c_info = {'name': safe_str(caster.get('name'), 'Spellcasting'), 'tradition': safe_str(caster.get('magicTradition'), 'Unknown'), 'type': cast_type, 'levels': []}
            slots_per_day = caster.get('perDay') or []
            
            for lvl in range(11):
                max_slots = safe_int(slots_per_day[lvl]) if lvl < len(slots_per_day) else 0
                spells_at_lvl = []
                for s in (caster.get('spells') or []):
                    if safe_int(s.get('spellLevel')) == lvl:
                        for s_name in (s.get('list') or []):
                            spells_at_lvl.append({'name': safe_str(s_name), 'desc': COMPENDIUM_LIBRARY.get(safe_str(s_name).lower(), "<em>No description.</em>")})
                
                if spells_at_lvl or max_slots > 0:
                    # Paizo-style rank labels: "Cantrips", "1st-Rank Spells", etc.
                    if lvl == 0:
                        rank_label = 'Cantrips'
                    else:
                        _suf = 'th' if (10 <= lvl % 100 <= 20) else {1:'st', 2:'nd', 3:'rd'}.get(lvl % 10, 'th')
                        rank_label = f'{lvl}{_suf}-Rank Spells'
                    c_info['levels'].append({'level': lvl, 'label': rank_label, 'slots': max_slots, 'spells': spells_at_lvl})
            if c_info['levels']: self.spell_casters.append(c_info)

        # Kineticist impulses — shown as spontaneous-style (no prep needed)
        if self.class_name.lower() == 'kineticist':
            k_impulses = [{'name': f['name'], 'desc': f['desc']} for f in self.feats 
                          if f.get('type', '').lower() in ['class feat', 'kineticist feat']]
            for cf in self.class_features:
                if cf['type'] in ['action', 'toggle'] and cf['name'] not in [i['name'] for i in k_impulses]:
                    k_impulses.append({'name': cf['name'], 'desc': cf['desc']})
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
                
                self.spell_casters.append({
                    'name': 'Focus Spells', 
                    'tradition': tradition,
                    'type': 'Focus', 
                    'levels': [{'level': 1, 'label': 'Focus Spells', 'slots': self.focus_max, 'spells': focus_spells}]
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
                'levels': [{'level': 1, 'label': 'Focus Spells', 'slots': self.focus_max, 'spells': []}]
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

    @property
    def base_ac(self):
        """AC without condition penalties or buffs — used by tracker to detect debuffs."""
        prof_val = safe_int(self.proficiencies.get('ac'), 2)
        effective_dex = min(self.mods.get('dex', 0), self.ac_dex_cap)
        prof_bonus = prof_val + self.level if prof_val > 0 else 0
        abp_ac = get_abp_bonus(self.level, 'defense_potency')
        return 10 + self.ac_item + effective_dex + prof_bonus + abp_ac + self.get_rule_mod('ac')

    @property
    def ac(self): 
        prof_val = safe_int(self.proficiencies.get('ac'), 2)
        effective_dex = min(self.mods.get('dex', 0), self.ac_dex_cap)
        prof_bonus = prof_val + self.level if prof_val > 0 else 0
        abp_ac = get_abp_bonus(self.level, 'defense_potency')
        base_ac = 10 + self.ac_item + effective_dex + prof_bonus + abp_ac
        circ_pen = 2 if (self.conditions.get('prone') or self.conditions.get('off_guard')) else 0
        shield_bonus = self.shield_ac_bonus if self.shield_raised else 0
        toggle_ac = self.toggle_effects_summary.get('ac', 0)
        return base_ac - self.get_status_penalty('dex') + self.highest_buff - circ_pen + self.get_rule_mod('ac') + toggle_ac + shield_bonus
    
    def _calc_save(self, stat_key, prof_key):
        prof_val = safe_int(self.proficiencies.get(prof_key), 2)
        base = self.mods.get(stat_key, 0) if prof_val == 0 else self.mods.get(stat_key, 0) + self.level + prof_val
        abp_save = get_abp_bonus(self.level, 'save_potency')
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
        abp = get_abp_bonus(self.level, 'save_potency') if prof_key != 'perception' else get_abp_bonus(self.level, 'perception_potency')
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
        abp_perc = get_abp_bonus(self.level, 'perception_potency')
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
            
        key_options = BUILDER_DATA["classes"].get(c_name, {}).get("key_options", ["cha"])
        subclass_info = SUBCLASS_MATRIX.get(self.subclass, {})
        if "key_ability" in subclass_info:
            key_options = [subclass_info["key_ability"]]
            
        key_mod = max([self.mods.get(stat, 0) for stat in key_options]) if key_options else 0
        return self.level + prof + key_mod - self.get_status_penalty('cha')

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
        abp_hit = get_abp_bonus(self.level, 'attack_potency')
        abp_dice = get_abp_bonus(self.level, 'devastating_attacks') or 1
        
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
                atk_parts.append(f"{ {0:'U',2:'T',4:'E',6:'M',8:'L'}.get(prof_val,'T') } +{prof_val}")
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
        return d

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
        
        for item in (data.get('items') or []):
            item_type = item.get('type')
            name = item.get('name')
            if item_type in ['melee', 'weapon']:
                damage = "Check Details"
                system_data = item.get('system', {})
                damage_rolls = system_data.get('damageRolls', {})
                if isinstance(damage_rolls, dict) and damage_rolls:
                    parts = [f"{roll['damage']} {roll.get('damageType', '')}".strip() for k, roll in damage_rolls.items() if isinstance(roll, dict) and 'damage' in roll]
                    if parts: damage = ", ".join(parts)
                self.strikes.append({'name': name, 'bonus': safe_int(system_data.get('bonus', {}).get('value'), 0), 'damage': damage})
            elif item_type == 'action':
                self.actions.append({'name': name, 'description': clean_foundry_text(item.get('system', {}).get('description', {}).get('value', ''))})

        self.conditions = { 'frightened': 0, 'sickened': 0, 'dying': 0, 'wounded': 0, 'doomed': 0, 'stunned': 0, 'slowed': 0, 'enfeebled': 0, 'clumsy': 0, 'drained': 0, 'stupefied': 0, 'prone': False, 'off_guard': False, 'concealed': False, 'hidden': False, 'undetected': False }
        
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
                        sys_data = safe_json_load(r, 'system', {})
                        
                        desc = get_col(r, 'description', '')
                        if not desc and isinstance(sys_data, dict):
                            d_obj = sys_data.get('description', {})
                            desc = d_obj.get('value', '') if isinstance(d_obj, dict) else (d_obj if isinstance(d_obj, str) else '')

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
                        pc = Character(char_data, f"{file}[{idx}]")
                        PARTY_LIBRARY[pc.name] = pc
                else: 
                    pc = Character(data, file)
                    PARTY_LIBRARY[pc.name] = pc
            except Exception as e: 
                print(f"[LOAD ERROR] Character {file}: {e}")
    _build_pc_file_cache()
    
    # --- AUTO-RESTORE ENCOUNTER FROM AUTOSAVE ---
    _restore_encounter_autosave()

def _restore_encounter_autosave():
    """Restore the active encounter from autosave file on startup."""
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER
    autosave_path = os.path.join(ENCOUNTER_DIR, '_autosave.json')
    if not os.path.exists(autosave_path):
        return
    try:
        with open(autosave_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        combatants = raw.get('combatants', [])
        ROUND_NUMBER = raw.get('round', 1)
        TURN_INDEX = raw.get('turn_index', 0)
        ACTIVE_ENCOUNTER.clear()
        for item in combatants:
            new_c = None
            if item.get('type') == 'monster' and item.get('path') in MONSTER_LIBRARY:
                new_c = copy.deepcopy(MONSTER_LIBRARY[item['path']])
            elif item.get('type') == 'pc' and item.get('path') in PARTY_LIBRARY:
                new_c = copy.deepcopy(PARTY_LIBRARY[item['path']])
            if new_c:
                new_c.instance_id = item.get('instance_id', str(uuid.uuid4()))
                new_c.initiative = item.get('initiative', 0)
                if 'current_hp' in item: new_c.current_hp = item['current_hp']
                if 'conditions' in item: new_c.conditions = item['conditions']
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
                ACTIVE_ENCOUNTER.append(new_c)
        if TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = 0
        if ACTIVE_ENCOUNTER:
            print(f"[ENCOUNTER] Restored autosave: {len(ACTIVE_ENCOUNTER)} combatants, Round {ROUND_NUMBER}")
    except Exception as e:
        print(f"[ENCOUNTER] Failed to restore autosave: {e}")

def get_vault_tree(dir_path):
    tree = []
    if not os.path.exists(dir_path): return tree
    for item in sorted(os.listdir(dir_path)):
        if item.startswith('.'): continue 
        full_path = os.path.join(dir_path, item)
        rel_path = os.path.relpath(full_path, OBSIDIAN_DIR).replace('\\', '/') 
        if os.path.isdir(full_path):
            children = get_vault_tree(full_path)
            if children: tree.append({'name': item, 'type': 'folder', 'children': children})
        elif item.endswith('.md'): tree.append({'name': item[:-3], 'type': 'file', 'path': rel_path})
    return tree

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

@app.route('/')
def index():
    """Root redirects to player hub (public). GMs go to /gm."""
    return redirect('/player')

@app.route('/party')
@gm_required
def party_view(): 
    _sync_party_from_disk()
    return render_template('party_view.html', party=list(PARTY_LIBRARY.values()))

@app.route('/gm/login', methods=['GET', 'POST'])
def gm_login():
    if request.method == 'POST':
        pw = request.form.get('password', '')
        if pw == GM_PASSWORD:
            session['gm_authenticated'] = True
            return redirect(request.args.get('next', '/gm'))
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

@app.route('/gm')
@gm_required
def gm_hub():
    """GM Dashboard hub — links to all GM tools."""
    party_count = len(PARTY_LIBRARY)
    monster_count = len(MONSTER_LIBRARY)
    encounter_count = len(ACTIVE_ENCOUNTER)
    return f'''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
    <title>GM Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Cinzel:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family:'Inter',system-ui,sans-serif; background:#0d0d12; color:#e8e8f0; }}
        .font-display {{ font-family:'Cinzel',serif; }}
        .gm-card {{ background:#24242e; border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:20px; transition:all 0.2s; }}
        .gm-card:hover {{ border-color:rgba(94,173,173,0.2); transform:translateY(-2px); box-shadow:0 8px 24px rgba(0,0,0,0.4); }}
    </style></head>
    <body class="min-h-screen flex items-center justify-center p-6">
    <div class="max-w-2xl w-full">
        <div class="text-center mb-10">
            <h1 class="font-display text-2xl tracking-wide mb-2" style="color:#7DC4C4;">Game Master</h1>
            <p class="text-xs" style="color:#50506a;">Dashboard &amp; Tools</p>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-8">
            <a href="/party" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#7DC4C4;">Party View</div>
                <p class="text-xs" style="color:#8080a0;">HP, conditions, spells at a glance</p>
                <span class="text-[10px] mt-2 block" style="color:#50506a;">{party_count} characters loaded</span>
            </a>
            <a href="/tracker" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#d4a244;">Encounter Tracker</div>
                <p class="text-xs" style="color:#8080a0;">Initiative, turns, HP, and conditions</p>
                <span class="text-[10px] mt-2 block" style="color:#50506a;">{encounter_count} combatants active</span>
            </a>
            <a href="/encounter_builder" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#FBBF24;">Encounter Builder</div>
                <p class="text-xs" style="color:#8080a0;">Search monsters, build balanced encounters</p>
                <span class="text-[10px] mt-2 block" style="color:#50506a;">{monster_count} monsters in library</span>
            </a>
            <a href="/gmscreen" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#a78bfa;">GM Screen</div>
                <p class="text-xs" style="color:#8080a0;">Quick reference tables and rules</p>
            </a>
            <a href="/generator" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#4ade80;">Generator</div>
                <p class="text-xs" style="color:#8080a0;">NPCs, loot, and encounter ideas</p>
            </a>
            <a href="/player" class="gm-card block">
                <div class="font-semibold text-sm mb-1" style="color:#fca5a5;">Player Hub</div>
                <p class="text-xs" style="color:#8080a0;">View what your players see</p>
            </a>
        </div>
        <div class="text-center">
            <a href="/gm/logout" class="text-xs font-medium" style="color:#50506a;">Logout</a>
        </div>
    </div></body></html>'''

@app.route('/tracker')
@gm_required
def tracker_view():
    sorted_monsters = sorted(MONSTER_LIBRARY.values(), key=lambda m: m.name)
    sorted_party = sorted(PARTY_LIBRARY.values(), key=lambda p: p.name)
    saved_encounters = [f.replace('.json', '') for f in os.listdir(ENCOUNTER_DIR) if f.endswith('.json')] if os.path.exists(ENCOUNTER_DIR) else []
    party_level = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc] or [p.level for p in PARTY_LIBRARY.values()] or [1])
    encounter_xp = calculate_encounter_xp(ACTIVE_ENCOUNTER, party_level)
    diff_label, diff_color = get_difficulty_label(encounter_xp)
    initial_state = _get_tracker_state()
    return render_template('tracker.html', monsters=sorted_monsters, party=sorted_party, initial_state=initial_state, turn_index=TURN_INDEX, round_number=ROUND_NUMBER, saved_encounters=sorted(saved_encounters), encounter_xp=encounter_xp, diff_label=diff_label, diff_color=diff_color, party_level=party_level, turn_reminders=TURN_REMINDERS)

def _is_ajax():
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json or request.content_type == 'application/json'

def _tracker_json_response():
    """Return full tracker state as JSON for AJAX calls."""
    return jsonify(_get_tracker_state())

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
                'initiative': c.initiative, 'is_active': (i == TURN_INDEX),
                'level': c.level, 'ac': c.ac, 'current_hp': c.current_hp, 'max_hp': c.hp,
                'fort': c.fort, 'ref': c.ref, 'will': c.will,
                'perception': c.perception, 'speed': getattr(c, 'active_speed', getattr(c, 'speed', 25)),
                'conditions': {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False},
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
            }
            hp_pct = (c.current_hp / c.hp * 100) if c.hp > 0 else 0
            entry['hp_pct'] = round(hp_pct)
            if c.is_pc:
                entry['strikes'] = [{'name': a['name'], 'hit': a['strikes'][0]['label'] if a.get('strikes') else '+?', 'damage': a['damage']} for a in getattr(c, 'attacks', [])]
                entry['feats'] = [{'name': f['name'], 'desc': f.get('desc', '')} for f in getattr(c, 'feats', [])]
            else:
                entry['strikes'] = [{'name': s['name'], 'hit': f"+{s['bonus']}" if s['bonus'] >= 0 else str(s['bonus']), 'damage': s['damage']} for s in getattr(c, 'strikes', [])]
                entry['actions'] = [{'name': a['name'], 'description': a.get('description', '')} for a in getattr(c, 'actions', [])]
                entry['immunities'] = getattr(c, 'immunities', [])
                entry['resistances'] = getattr(c, 'resistances', [])
                entry['weaknesses'] = getattr(c, 'weaknesses', [])
                entry['traits'] = getattr(c, 'traits', [])
            combatants.append(entry)
        party_level = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc] or [p.level for p in PARTY_LIBRARY.values()] or [1])
        encounter_xp = calculate_encounter_xp(ACTIVE_ENCOUNTER, party_level)
        diff_label, diff_color = get_difficulty_label(encounter_xp)
        result = {
            'combatants': combatants, 'round': ROUND_NUMBER, 'turn_index': TURN_INDEX,
            'active_name': active_name, 'encounter_xp': encounter_xp,
            'diff_label': diff_label, 'diff_color': diff_color, 'party_level': party_level,
        }
    _TRACKER_STATE_CACHE = result
    _TRACKER_STATE_CACHE_TIME = now
    return result

@app.route('/api/tracker_state')
def api_tracker_state():
    """GET endpoint for full tracker state (AJAX polling fallback)."""
    return _tracker_json_response()

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
        if 'visible' in data:
            new_vis = bool(data['visible'])
        else:
            new_vis = not getattr(target, 'visible_to_players', True)
        # PCs are always visible. Silently no-op rather than error so a
        # mis-click on a PC row doesn't confuse the GM mid-fight.
        if target.is_pc:
            new_vis = True
        target.visible_to_players = new_vis
        # Sync the map token so moving a creature onto the map keeps the
        # same visibility the GM set in the tracker. ACTIVE_MAP is the
        # shared token dict used by the VTT — we mutate it in place under
        # the ENCOUNTER_LOCK since callers expect the token visibility flip
        # to land atomically with the combatant flip.
        for token in ACTIVE_MAP.get('tokens', []):
            if token.get('instance_id') == instance_id:
                token['visible_to_players'] = new_vis
    _persist_encounter_state()
    _broadcast_encounter_state()
    # Also broadcast map state so the player map view updates immediately
    # when a token goes hidden/visible.
    try:
        _broadcast_map_state()
    except Exception:
        pass
    _combat_log(
        f"{target.name} is now {'visible to' if new_vis else 'hidden from'} players",
        'system'
    )
    if _is_ajax() or request.is_json:
        return jsonify({'success': True, 'instance_id': instance_id, 'visible_to_players': new_vis})
    return redirect(url_for('tracker_view'))

@app.route('/api/clear_encounter', methods=['POST'])
def clear_encounter():
    global TURN_INDEX, ROUND_NUMBER
    if ACTIVE_ENCOUNTER:
        names = [c.name for c in ACTIVE_ENCOUNTER]
        _combat_log(f"Encounter ended ({', '.join(names)})", 'system')
    ACTIVE_ENCOUNTER.clear(); TURN_INDEX = 0; ROUND_NUMBER = 1
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

    # Broadcast to all clients (players filter client-side)
    sse_broadcast('handout', handout)

    return jsonify({"success": True, "handout": handout})

@app.route('/api/handouts/<handout_id>', methods=['DELETE'])
def delete_handout(handout_id):
    """GM deletes a handout."""
    global HANDOUTS
    HANDOUTS = [h for h in HANDOUTS if h['id'] != handout_id]
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

    # Save to static/uploads/handouts/
    upload_dir = os.path.join(BASE_DIR, 'static', 'uploads', 'handouts')
    os.makedirs(upload_dir, exist_ok=True)

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
        return jsonify({"error": "Invalid image format"}), 400

    filename = f"{uuid.uuid4().hex[:12]}{ext}"
    filepath = os.path.join(upload_dir, filename)
    f.save(filepath)

    url = f"/static/uploads/handouts/{filename}"
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

@app.route('/api/adjust_hp/<instance_id>', methods=['POST'])
def adjust_hp(instance_id):
    try:
        amount = int(request.form.get('amount', 0))
        action = request.form.get('action')
        damage_type = request.form.get('damage_type', 'untyped').strip()
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                old_hp = c.current_hp
                if action == 'damage':
                    was_above_zero = c.current_hp > 0
                    # Apply W/R/I calculation for non-PC targets
                    effective = amount
                    wri_notes = []
                    if not c.is_pc and damage_type and damage_type != 'untyped':
                        effective, wri_notes = _calculate_damage_with_wri(amount, damage_type, c)
                    c.current_hp = max(0, c.current_hp - effective)
                    # Build detailed log message
                    type_label = f" {damage_type}" if damage_type and damage_type != 'untyped' else ''
                    if wri_notes:
                        adj_detail = ' | '.join(wri_notes)
                        _combat_log(f"{c.name} took {effective}{type_label} damage ({old_hp}→{c.current_hp}) [{adj_detail}, raw {amount}]", 'damage')
                    else:
                        _combat_log(f"{c.name} took {effective}{type_label} damage ({old_hp}→{c.current_hp})", 'damage')
                    if c.current_hp == 0:
                        c.conditions['dying'] = 1 + c.conditions.get('wounded', 0) if was_above_zero else c.conditions.get('dying', 0) + 1
                        _combat_log(f"{c.name} is Dying {c.conditions['dying']}!", 'critical')
                elif action == 'heal':
                    was_dying = c.conditions.get('dying', 0) > 0
                    c.current_hp = min(c.hp, c.current_hp + amount)
                    _combat_log(f"{c.name} healed {amount} HP ({old_hp}→{c.current_hp})", 'heal')
                    if c.current_hp > 0 and was_dying:
                        c.conditions['dying'] = 0; c.conditions['wounded'] = c.conditions.get('wounded', 0) + 1
                        _combat_log(f"{c.name} recovered from Dying! (Wounded {c.conditions['wounded']})", 'critical')
                if c.is_pc and c.name in PARTY_LIBRARY:
                    PARTY_LIBRARY[c.name].current_hp = c.current_hp
                    PARTY_LIBRARY[c.name].conditions['dying'] = c.conditions['dying']
                    PARTY_LIBRARY[c.name].conditions['wounded'] = c.conditions['wounded']
                    _broadcast_pc_state(c.name)
                    _persist_pc_combat_state(c.name)
                _persist_encounter_state()
                _broadcast_encounter_state()
                break
    except ValueError: pass
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/adjust_party_hp/<pc_name>', methods=['POST'])
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
                # PF2e: temporary HP is lost first, then real HP. Drain the
                # manual pool (the toggle pool is passive and not drained).
                thp = int(getattr(pc, 'temp_hp_manual', 0) or 0)
                if thp > 0 and remaining > 0:
                    used = min(thp, remaining)
                    pc.temp_hp_manual = thp - used
                    try:
                        pc.temp_hp = pc.temp_hp_manual + pc.toggle_effects_summary.get('temp_hp', 0)
                    except Exception:
                        pc.temp_hp = pc.temp_hp_manual
                    remaining -= used
                pc.current_hp = max(0, pc.current_hp - remaining)
                if pc.current_hp == 0 and was_above_zero:
                    pc.conditions['dying'] = 1 + pc.conditions.get('wounded', 0)
                elif pc.current_hp == 0 and not was_above_zero:
                    pc.conditions['dying'] = pc.conditions.get('dying', 0) + 1
                if pc.conditions.get('dying', 0) >= 4:
                    pc.conditions['dying'] = 4
            elif action == 'heal':
                was_dying = pc.conditions.get('dying', 0) > 0
                pc.current_hp = min(pc.hp, pc.current_hp + amount)
                if pc.current_hp > 0 and was_dying:
                    pc.conditions['dying'] = 0
                    pc.conditions['wounded'] = pc.conditions.get('wounded', 0) + 1
            return True

        _, pc = apply_pc_delta(pc_name, _mutate)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
            return jsonify({
                "success": True, "current_hp": pc.current_hp,
                "temp_hp": int(getattr(pc, 'temp_hp', 0) or 0),
                "temp_hp_manual": int(getattr(pc, 'temp_hp_manual', 0) or 0),
                "conditions": {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
                "dying": pc.conditions.get('dying', 0),
                "wounded": pc.conditions.get('wounded', 0),
                "dead": pc.conditions.get('dying', 0) >= 4
            })
    except ValueError: pass
    return redirect(url_for('party_view'))

@app.route('/api/adjust_temp_hp/<pc_name>', methods=['POST'])
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

@app.route('/api/adjust_hero/<pc_name>', methods=['POST'])
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
def daily_preparations(pc_name):
    """Daily preparations: reset spell slots, focus points, conditions, optionally heal to full."""
    pc, file_path, err = require_pc(pc_name)
    if err: return err
    
    pc_json, file_path, err = require_pc_json(pc_name)
    if err: return err
    build = pc_json.get('build', pc_json)
    
    data = request.json or {}
    heal_full = data.get('heal_full', True)
    
    # Reset expended spell slots
    build['expended_slots'] = {}
    
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
def shield_block(pc_name):
    """Use Shield Block reaction: reduce damage by hardness, shield takes remaining."""
    pc, _, err = require_pc(pc_name)
    if err: return err

    data = request.json or {}
    damage = safe_int(data.get('damage', 0))

    # Eligibility checks up-front (pc-state, not build JSON — source of truth
    # is the in-memory PC). If the shield can't block, bail before mutation.
    shield_hp0 = int(getattr(pc, 'shield_hp', 0) or 0)
    shield_max_hp = int(getattr(pc, 'shield_max_hp', 0) or 0)
    shield_hardness = int(getattr(pc, 'shield_hardness', 0) or 0)
    shield_bt = int(getattr(pc, 'shield_bt', 0) or (shield_max_hp // 2))
    if shield_max_hp <= 0:
        return jsonify({"success": False, "error": "No shield equipped"})
    if shield_hp0 <= 0:
        return jsonify({"success": False, "error": "Shield is destroyed"})
    if shield_hp0 <= shield_bt:
        return jsonify({"success": False, "error": "Shield is broken — repair it before blocking"})
    # Reaction-budget check (Phase 3). Shield Block consumes the reaction.
    if getattr(pc, 'reaction_used', False):
        return jsonify({"success": False, "error": "Reaction already spent this round"})

    # Shield Block (PF2e CRB): the shield prevents damage equal to its
    # Hardness. You and the shield each take any leftover damage.
    blocked = min(damage, shield_hardness)
    leftover = max(0, damage - shield_hardness)

    result = {}

    def _mutate(pc):
        new_shield_hp = max(0, shield_hp0 - leftover)
        pc.shield_hp = new_shield_hp
        pc.shield_broken = new_shield_hp <= shield_bt
        pc.shield_destroyed = new_shield_hp <= 0
        # Reaction is gone for the round.
        pc.reaction_used = True

        # Full damage pipeline for the leftover that hits the PC.
        was_above_zero = pc.current_hp > 0
        remaining = leftover
        thp = int(getattr(pc, 'temp_hp_manual', 0) or 0)
        if thp > 0 and remaining > 0:
            used = min(thp, remaining)
            pc.temp_hp_manual = thp - used
            try:
                pc.temp_hp = pc.temp_hp_manual + pc.toggle_effects_summary.get('temp_hp', 0)
            except Exception:
                pc.temp_hp = pc.temp_hp_manual
            remaining -= used
        pc.current_hp = max(0, pc.current_hp - remaining)
        if pc.current_hp == 0 and was_above_zero:
            pc.conditions['dying'] = 1 + pc.conditions.get('wounded', 0)
        elif pc.current_hp == 0 and not was_above_zero:
            pc.conditions['dying'] = pc.conditions.get('dying', 0) + 1
        if pc.conditions.get('dying', 0) >= 4:
            pc.conditions['dying'] = 4

        result['shield_hp'] = new_shield_hp
        result['shield_broken'] = pc.shield_broken
        result['shield_destroyed'] = pc.shield_destroyed
        return True

    _, pc = apply_pc_delta(pc_name, _mutate)

    status = "destroyed" if result['shield_destroyed'] else "broken" if result['shield_broken'] else "intact"
    _combat_log(f"{pc_name}: Shield Block! Blocked {blocked} dmg (Hardness {shield_hardness}). Shield took {leftover} ({status}). {leftover} dmg to HP.", 'action')

    return jsonify({
        "success": True,
        "blocked": blocked,
        "damage_to_char": leftover,
        "damage_to_shield": leftover,
        "shield_hp": result['shield_hp'],
        "shield_max_hp": shield_max_hp,
        "shield_hardness": shield_hardness,
        "shield_bt": shield_bt,
        "shield_broken": result['shield_broken'],
        "shield_destroyed": result['shield_destroyed'],
        "current_hp": pc.current_hp,
        "temp_hp": int(getattr(pc, 'temp_hp', 0) or 0),
        "reaction_used": bool(getattr(pc, 'reaction_used', False)),
        "dying": pc.conditions.get('dying', 0),
        "wounded": pc.conditions.get('wounded', 0),
        "dead": pc.conditions.get('dying', 0) >= 4,
        "conditions": {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
    })

@app.route('/api/repair_shield/<pc_name>', methods=['POST'])
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
    return jsonify({"success": True, "shield_hp": build['shield_hp'], "shield_max_hp": shield_max_hp})

@app.route('/api/set_shield_stats/<pc_name>', methods=['POST'])
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
    return jsonify({"success": True})

# =============================================================================
# MAP FLANKING DETECTION
# =============================================================================
@app.route('/api/map/flanking', methods=['GET'])
def check_flanking():
    """Check all token pairs for flanking geometry on the VTT map.
    Two allied tokens flank an enemy when they are on opposite sides (within 45 degrees of a line through the enemy).
    Returns list of enemy token IDs that are currently flanked."""
    with MAP_LOCK:
        tokens = ACTIVE_MAP.get('tokens', [])
    
    gs = ACTIVE_MAP.get('grid_size', 70)
    flanked_ids = []
    
    # Separate PCs and NPCs
    pcs = [t for t in tokens if t.get('is_pc')]
    npcs = [t for t in tokens if not t.get('is_pc') and t.get('visible_to_players', True)]
    
    for npc in npcs:
        npc_cx = npc['x'] + (npc.get('size', 1) / 2)
        npc_cy = npc['y'] + (npc.get('size', 1) / 2)
        
        # Check all pairs of PCs
        is_flanked = False
        for i in range(len(pcs)):
            if is_flanked: break
            for j in range(i + 1, len(pcs)):
                pc_a = pcs[i]
                pc_b = pcs[j]
                
                ax = pc_a['x'] + (pc_a.get('size', 1) / 2)
                ay = pc_a['y'] + (pc_a.get('size', 1) / 2)
                bx = pc_b['x'] + (pc_b.get('size', 1) / 2)
                by = pc_b['y'] + (pc_b.get('size', 1) / 2)
                
                # Both must be adjacent to the enemy (within 1.5 squares for reach/diagonals)
                dist_a = max(abs(ax - npc_cx), abs(ay - npc_cy))
                dist_b = max(abs(bx - npc_cx), abs(by - npc_cy))
                if dist_a > 1.5 or dist_b > 1.5:
                    continue
                
                # Check if PCs are on opposite sides: the line from A to B must pass through or near the enemy
                # Vector from A to B
                dx = bx - ax
                dy = by - ay
                line_len_sq = dx * dx + dy * dy
                if line_len_sq < 0.01: continue
                
                # Project enemy onto line A→B
                t = ((npc_cx - ax) * dx + (npc_cy - ay) * dy) / line_len_sq
                
                # Enemy should be between A and B (t between 0.1 and 0.9)
                # and close to the line
                if 0.1 <= t <= 0.9:
                    proj_x = ax + t * dx
                    proj_y = ay + t * dy
                    perp_dist = math.hypot(npc_cx - proj_x, npc_cy - proj_y)
                    if perp_dist <= 0.75:  # Within tolerance
                        is_flanked = True
                        break
        
        if is_flanked:
            flanked_ids.append(npc['id'])
    
    return jsonify({"success": True, "flanked": flanked_ids})

# NEW: FRONTEND CONDITION SYNC
@app.route('/api/update_pc_condition/<pc_name>', methods=['POST'])
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
        try:
            if pc_name in _PC_PERSIST_DIRTY:
                _do_persist_pc_combat_state(pc_name)
                _PC_PERSIST_DIRTY.discard(pc_name)
        except Exception: pass
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
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route('/api/toggle_condition/<instance_id>', methods=['POST'])
def toggle_condition(instance_id):
    condition = request.form.get('condition')
    action = request.form.get('action') 
    for combatant in ACTIVE_ENCOUNTER:
        if combatant.instance_id == instance_id:
            if condition in ['frightened', 'sickened', 'dying', 'wounded', 'doomed', 'stunned', 'slowed', 'enfeebled', 'clumsy', 'drained', 'stupefied']:
                current = combatant.conditions.get(condition, 0)
                if action in ['increase', 'add']: combatant.conditions[condition] = current + 1
                elif action == 'decrease' and current > 0:
                    combatant.conditions[condition] = current - 1
                    if condition == 'dying' and combatant.conditions[condition] == 0: combatant.conditions['wounded'] = combatant.conditions.get('wounded', 0) + 1
            elif condition in ['prone', 'off_guard', 'concealed', 'hidden', 'undetected']:
                if action == 'toggle': combatant.conditions[condition] = not combatant.conditions[condition]
                elif action == 'add': combatant.conditions[condition] = True
            if combatant.is_pc and combatant.name in PARTY_LIBRARY: 
                PARTY_LIBRARY[combatant.name].conditions[condition] = combatant.conditions[condition]
                _broadcast_pc_state(combatant.name)
                _persist_pc_combat_state(combatant.name)
            new_val = combatant.conditions.get(condition, 0)
            if isinstance(new_val, bool):
                _combat_log(f"{combatant.name} {'gained' if new_val else 'lost'} {condition.replace('_','-').title()}", 'condition')
            else:
                _combat_log(f"{combatant.name}: {condition.title()} → {new_val}", 'condition')
            _persist_encounter_state()
            _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/set_persistent_damage/<instance_id>', methods=['POST'])
def set_persistent_damage(instance_id):
    pd_val = request.form.get('persistent_damage', '') or (request.json or {}).get('persistent_damage', '')
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id:
            # Monsters keep the legacy string format ('1d6 fire'); PCs use
            # the list-of-dicts format. Don't splat a string onto a PC's
            # persistent_damage — it corrupts the list (list("[]") -> ['[',']']).
            # GM edits for PCs go through /api/persistent_damage/<name>/... instead.
            if c.is_pc and c.name in PARTY_LIBRARY:
                c.persistent_damage = pd_val  # update the tracker row only
            else:
                c.persistent_damage = pd_val
            _persist_encounter_state()
            _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/toggle_elite_weak/<instance_id>', methods=['POST'])
def toggle_elite_weak(instance_id):
    mode = request.form.get('mode', 'normal') or (request.json or {}).get('mode', 'normal')
    mode_val = {'elite': 1, 'weak': -1, 'normal': 0}.get(mode, 0)
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id and not c.is_pc and hasattr(c, 'apply_elite_weak'):
            c.apply_elite_weak(mode_val)
            _persist_encounter_state()
            _broadcast_encounter_state()
            break
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/update_initiative/<instance_id>', methods=['POST'])
def update_initiative(instance_id):
    try: init_val = int(request.form.get('initiative', 0) or (request.json or {}).get('initiative', 0))
    except ValueError: init_val = 0
    for c in ACTIVE_ENCOUNTER:
        if c.instance_id == instance_id: c.initiative = init_val; break
    _sort_encounter(); _persist_encounter_state(); _broadcast_encounter_state()
    if _is_ajax(): return _tracker_json_response()
    return redirect(url_for('tracker_view'))

@app.route('/api/roll_npc_initiative', methods=['POST'])
def roll_npc_initiative():
    for c in ACTIVE_ENCOUNTER:
        if not c.is_pc: c.initiative = random.randint(1, 20) + getattr(c, 'perception', 0)
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
            if TURN_INDEX <= old_index: ROUND_NUMBER += 1
            if not getattr(ACTIVE_ENCOUNTER[TURN_INDEX], 'delaying', False):
                break
            old_index = TURN_INDEX
        
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

        # Stunned: lose actions, then reduce stunned by the number lost (PF2E Core p.448)
        stunned_val = new_c.conditions.get('stunned', 0)
        if stunned_val > 0:
            actions_lost = min(stunned_val, 3)
            new_c.conditions['stunned'] = max(0, stunned_val - actions_lost)
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

@app.route('/api/save_encounter', methods=['POST'])
def save_encounter():
    name = request.form.get('encounter_name')
    if name and ACTIVE_ENCOUNTER:
        if not os.path.exists(ENCOUNTER_DIR): os.makedirs(ENCOUNTER_DIR)
        encounter_data = {
            "round": ROUND_NUMBER,
            "turn_index": TURN_INDEX,
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
                'persistent_damage': getattr(c, 'persistent_damage', ''),
                'elite_weak': getattr(c, 'elite_weak', 0),
                # Persist hidden/visible state so saved encounters reload with
                # the same player visibility as when they were saved.
                'visible_to_players': getattr(c, 'visible_to_players', True),
            }
            encounter_data['combatants'].append(entry)
        # Snapshot the current map alongside the encounter so Load Encounter
        # restores the world, not just initiative. Stored under 'map' to keep
        # older saves (no map key) backward-compatible on load.
        with MAP_LOCK:
            encounter_data['map'] = copy.deepcopy(ACTIVE_MAP)
        with open(os.path.join(ENCOUNTER_DIR, f"{name}.json"), 'w', encoding='utf-8') as f:
            json.dump(encounter_data, f, indent=2)
    return redirect(url_for('tracker_view'))

@app.route('/api/load_encounter', methods=['POST'])
def load_encounter():
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER, ACTIVE_MAP
    name = request.form.get('encounter_name')
    enc_path = os.path.join(ENCOUNTER_DIR, f"{name}.json")
    if name and os.path.exists(enc_path):
        ACTIVE_ENCOUNTER.clear(); TURN_INDEX = 0; ROUND_NUMBER = 1
        with open(enc_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        # Support both old format (list) and new format (dict with metadata)
        if isinstance(raw, list):
            combatants = raw
            saved_map = None
        else:
            combatants = raw.get('combatants', raw)
            ROUND_NUMBER = raw.get('round', 1)
            TURN_INDEX = raw.get('turn_index', 0)
            saved_map = raw.get('map')

        for item in combatants:
            new_c = None
            if item.get('type') == 'monster' and item.get('path') in MONSTER_LIBRARY:
                new_c = copy.deepcopy(MONSTER_LIBRARY[item['path']])
            elif item.get('type') == 'pc' and item.get('path') in PARTY_LIBRARY:
                new_c = copy.deepcopy(PARTY_LIBRARY[item['path']])

            if new_c:
                new_c.instance_id = item.get('instance_id', str(uuid.uuid4()))
                new_c.initiative = item.get('initiative', 0)
                if 'current_hp' in item: new_c.current_hp = item['current_hp']
                if 'conditions' in item: new_c.conditions = item['conditions']
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
                if 'elite_weak' in item and hasattr(new_c, 'apply_elite_weak'):
                    new_c.apply_elite_weak(item['elite_weak'])
                # Restore hidden/visible state from saved encounter files.
                if 'visible_to_players' in item:
                    new_c.visible_to_players = bool(item['visible_to_players']) if not new_c.is_pc else True
                ACTIVE_ENCOUNTER.append(new_c)

        # Validate turn index
        if TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = 0

        # Restore snapshotted map state if present. Older saves skip this.
        if saved_map and isinstance(saved_map, dict):
            with MAP_LOCK:
                ACTIVE_MAP.clear()
                ACTIVE_MAP.update(copy.deepcopy(saved_map))
            try:
                _save_map_state()
            except Exception:
                pass
            try:
                _broadcast_map_state()
                _broadcast_map_walls()
            except Exception:
                pass
    return redirect(url_for('tracker_view'))

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

@app.route('/gmscreen')
@gm_required
def gm_screen(): 
    return render_template('gmscreen.html')

@app.route('/encounter_builder')
@gm_required
def encounter_builder():
    sorted_party = sorted(PARTY_LIBRARY.values(), key=lambda p: p.name)
    party_level = max([p.level for p in PARTY_LIBRARY.values()]) if PARTY_LIBRARY else 1
    return render_template('encounter_builder.html', party=sorted_party, party_level=party_level)

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
    
    # Add monsters
    for entry in monsters:
        path = entry.get('path')
        count = entry.get('count', 1)
        elite_weak = entry.get('elite_weak', 0)
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
    party_level = max([p.level for p in PARTY_LIBRARY.values()]) if PARTY_LIBRARY else 1
    gen_types = ['npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter', 'weather', 'trap', 'rumor', 'settlement', 'treasure_hoard', 'random_event']
    data = {}
    for k in gen_types:
        try:
            data[k] = getattr(pf2e_gen, f'get_{k}')(level=party_level, biome="City")
        except AttributeError:
            data[k] = f"<em>Generator '{k}' not available.</em>"
    return render_template('generator.html', data=data, current_level=party_level)

@app.route('/api/generate/<element_type>', methods=['POST'])
def api_generate(element_type):
    if element_type not in VALID_GENERATOR_TYPES:
        return jsonify({'error': 'Invalid generator type'}), 400
    data = request.get_json()
    return jsonify({'html': getattr(pf2e_gen, f'get_{element_type}')(int(data.get('level', 1)), data.get('biome', 'City'))})

@app.route('/api/vault_image/<path:filename>')
def vault_image(filename):
    search_name = urllib.parse.unquote(os.path.basename(filename))
    # Security: reject path traversal attempts and non-image files
    if '..' in search_name or '/' in search_name or '\\' in search_name:
        return "Invalid filename", 400
    file_ext = os.path.splitext(search_name)[1].lower()
    if file_ext not in ALLOWED_IMAGE_EXTENSIONS:
        return "File type not allowed", 403
    for root, dirs, files in os.walk(OBSIDIAN_DIR):
        if search_name in files:
            full_path = os.path.join(root, search_name)
            # Double-check the resolved path is still inside the vault
            if not os.path.abspath(full_path).startswith(os.path.abspath(OBSIDIAN_DIR)):
                return "Access denied", 403
            return send_file(full_path)
    return "Image Not Found", 404

def _build_vault_note_index():
    """Build a dict mapping note name (lowercase, no .md) → relative path for wikilink resolution."""
    index = {}
    if not os.path.exists(OBSIDIAN_DIR): return index
    for root, dirs, files in os.walk(OBSIDIAN_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if f.endswith('.md'):
                rel = os.path.relpath(os.path.join(root, f), OBSIDIAN_DIR).replace('\\', '/')
                name = f[:-3].lower()
                index[name] = rel
    return index

def _render_vault_markdown(md_content):
    """Convert Obsidian-flavored markdown to HTML with image embeds and clickable wikilinks."""
    # Obsidian image embeds: ![[image.png]]
    md_content = re.sub(r'!\[\[(.*?)\]\]', r'<img src="/api/vault_image/\1" alt="\1" class="max-w-full rounded-lg shadow-md border border-gray-700 my-4">', md_content)
    # Standard markdown images
    md_content = re.sub(r'!\[(.*?)\]\((.*?)\)', r'<img src="/api/vault_image/\2" alt="\1" class="max-w-full rounded-lg shadow-md border border-gray-700 my-4">', md_content)
    # Wikilinks: [[Note Name]] → clickable links (handle [[Note Name|Display Text]] too)
    def wikilink_replace(match):
        full = match.group(1)
        if '|' in full:
            target, display = full.split('|', 1)
        else:
            target = display = full
        return f'<a href="#" class="vault-wikilink text-amber-400 font-bold hover:text-amber-300 bg-amber-900/20 px-1 rounded border border-amber-900/50 cursor-pointer transition-colors" data-target="{target.strip()}">{display.strip()}</a>'
    md_content = re.sub(r'\[\[(.*?)\]\]', wikilink_replace, md_content)
    return markdown.markdown(md_content, extensions=['tables', 'fenced_code'])

@app.route('/api/vault_note/<path:file_path>')
def api_vault_note(file_path):
    """AJAX endpoint: returns rendered note HTML + title as JSON."""
    full_path = os.path.join(OBSIDIAN_DIR, file_path)
    if not os.path.abspath(full_path).startswith(os.path.abspath(OBSIDIAN_DIR)) or not os.path.exists(full_path):
        return jsonify({"error": "Note not found"}), 404
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            md_content = f.read()
        content_html = _render_vault_markdown(md_content)
        title = os.path.basename(file_path)[:-3]
        return jsonify({"title": title, "html": content_html, "path": file_path, "raw": md_content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/vault_save', methods=['POST'])
def api_vault_save():
    """Save edited note content back to the vault."""
    data = request.json
    file_path = data.get('path', '')
    content = data.get('content', '')
    if not file_path or '..' in file_path:
        return jsonify({"error": "Invalid path"}), 400
    full_path = os.path.join(OBSIDIAN_DIR, file_path)
    if not os.path.abspath(full_path).startswith(os.path.abspath(OBSIDIAN_DIR)):
        return jsonify({"error": "Access denied"}), 403
    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/vault_create', methods=['POST'])
def api_vault_create():
    """Create a new note in the vault."""
    data = request.json
    note_name = data.get('name', '').strip()
    folder = data.get('folder', '').strip()
    if not note_name or '..' in note_name or '/' in note_name or '\\' in note_name:
        return jsonify({"error": "Invalid note name"}), 400
    if folder and ('..' in folder):
        return jsonify({"error": "Invalid folder"}), 400
    rel_path = os.path.join(folder, f"{note_name}.md") if folder else f"{note_name}.md"
    full_path = os.path.join(OBSIDIAN_DIR, rel_path)
    if not os.path.abspath(full_path).startswith(os.path.abspath(OBSIDIAN_DIR)):
        return jsonify({"error": "Access denied"}), 403
    if os.path.exists(full_path):
        return jsonify({"error": "Note already exists"}), 409
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(f"# {note_name}\n\n")
    return jsonify({"success": True, "path": rel_path.replace('\\', '/')})

@app.route('/api/vault_search')
def api_vault_search():
    """Search vault notes by name and content."""
    query = request.args.get('q', '').strip().lower()
    if not query or len(query) < 2:
        return jsonify({"results": []})
    results = []
    if not os.path.exists(OBSIDIAN_DIR):
        return jsonify({"results": []})
    for root, dirs, files in os.walk(OBSIDIAN_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            if not f.endswith('.md'): continue
            rel = os.path.relpath(os.path.join(root, f), OBSIDIAN_DIR).replace('\\', '/')
            name = f[:-3]
            name_match = query in name.lower()
            snippet = ""
            try:
                with open(os.path.join(root, f), 'r', encoding='utf-8') as fh:
                    content = fh.read()
                content_lower = content.lower()
                content_match = query in content_lower
                if content_match and not name_match:
                    idx = content_lower.index(query)
                    start = max(0, idx - 40)
                    end = min(len(content), idx + len(query) + 40)
                    snippet = ("..." if start > 0 else "") + content[start:end].replace('\n', ' ') + ("..." if end < len(content) else "")
                if name_match or content_match:
                    results.append({"name": name, "path": rel, "snippet": snippet, "name_match": name_match})
            except: pass
    # Sort: name matches first, then content matches
    results.sort(key=lambda r: (0 if r['name_match'] else 1, r['name'].lower()))
    return jsonify({"results": results[:20]})

@app.route('/api/vault_resolve')
def api_vault_resolve():
    """Resolve a wikilink name to a vault path."""
    target = request.args.get('name', '').strip()
    if not target:
        return jsonify({"error": "No name provided"}), 400
    index = _build_vault_note_index()
    # Try exact match first, then case-insensitive
    path = index.get(target.lower())
    if path:
        return jsonify({"path": path})
    return jsonify({"error": "Note not found"}), 404

@app.route('/vault')
@app.route('/vault/<path:file_path>')
@gm_required
def vault_view(file_path=None):
    tree = get_vault_tree(OBSIDIAN_DIR)
    content_html = ""
    note_title = "Obsidian Vault"
    raw_content = ""
    if file_path:
        full_path = os.path.join(OBSIDIAN_DIR, file_path)
        if os.path.abspath(full_path).startswith(os.path.abspath(OBSIDIAN_DIR)) and os.path.exists(full_path):
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    md_content = f.read()
                    raw_content = md_content
                    content_html = _render_vault_markdown(md_content)
                    note_title = os.path.basename(file_path)[:-3] 
            except Exception as e: content_html = f"<div class='text-red-500'>Error reading file: {e}</div>"
        else: content_html = "<div class='text-red-500'>Note not found.</div>"
    return render_template('vault.html', tree=tree, content=content_html, note_title=note_title, current_path=file_path or '', raw_content=raw_content)

# =============================================================================
# OBSIDIAN VAULT EXPORT — Auto-generate markdown files from dashboard data
# =============================================================================

@app.route('/api/vault_export_character/<pc_name>', methods=['POST'])
def vault_export_character(pc_name):
    """Export a character snapshot as an Obsidian-compatible markdown file."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    pc = PARTY_LIBRARY[pc_name]
    import datetime
    now = datetime.datetime.now()
    
    # Build YAML frontmatter (Dataview-compatible)
    lines = [
        '---',
        f'type: character',
        f'name: "{pc.name}"',
        f'class: "{pc.class_name}"',
        f'level: {pc.level}',
        f'ancestry: "{pc.ancestry}"',
        f'hp: {pc.hp}',
        f'ac: {pc.ac}',
        f'updated: "{now.strftime("%Y-%m-%d %H:%M")}"',
        '---',
        '',
        f'# {pc.name}',
        f'**Level {pc.level} {pc.ancestry} {pc.class_name}**',
        f'{f"*{pc.subclass}*" if pc.subclass else ""}',
        '',
        '## Ability Scores',
        '| STR | DEX | CON | INT | WIS | CHA |',
        '|-----|-----|-----|-----|-----|-----|',
        f'| {" | ".join(s["mod"] for s in pc.ability_display)} |',
        '',
        '## Defenses',
        f'- **AC:** {pc.ac}',
        f'- **Fortitude:** +{pc.fort}',
        f'- **Reflex:** +{pc.ref}',
        f'- **Will:** +{pc.will}',
        f'- **Perception:** +{pc.perception}',
        f'- **HP:** {pc.current_hp}/{pc.hp}',
        f'- **Speed:** {pc.active_speed} ft',
        '',
    ]
    
    if pc.attacks:
        lines.append('## Attacks')
        for a in pc.attacks:
            lines.append(f'- **{a["name"]}:** {a["strikes"][0]["label"]} ({a["damage"]})')
        lines.append('')
    
    if pc.spell_casters:
        lines.append('## Spellcasting')
        for caster in pc.spell_casters:
            lines.append(f'### {caster["name"]} ({caster.get("tradition", "Unknown")})')
            if pc.spell_attack > 0:
                lines.append(f'- Attack: +{pc.spell_attack} | DC: {pc.spell_dc}')
            for lvl in caster.get('levels', []):
                spell_names = [sp['name'] for sp in lvl.get('spells', [])]
                if spell_names:
                    lines.append(f'- **{lvl["label"]}** ({lvl["slots"]} slots): {", ".join(spell_names)}')
        lines.append('')
    
    if pc.feats:
        lines.append('## Feats')
        for feat in pc.feats:
            lines.append(f'- [[{feat["name"]}]]')
        lines.append('')
    
    if pc.equipment:
        lines.append('## Equipment')
        for eq in pc.equipment:
            lines.append(f'- {eq["name"]} ×{eq["qty"]}')
        lines.append('')
    
    # Write to vault
    char_dir = os.path.join(OBSIDIAN_DIR, 'Characters')
    os.makedirs(char_dir, exist_ok=True)
    file_path = os.path.join(char_dir, f'{pc.name}.md')
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    rel_path = f'Characters/{pc.name}.md'
    return jsonify({"success": True, "path": rel_path})

@app.route('/api/vault_export_session', methods=['POST'])
def vault_export_session():
    """Export a session recap to the vault with party status and notes."""
    data = request.json or {}
    import datetime
    now = datetime.datetime.now()
    
    session_num = data.get('session_number', '?')
    title = data.get('title', f'Session {session_num}')
    recap = data.get('recap', '')
    
    lines = [
        '---',
        f'type: session',
        f'session: {session_num}',
        f'date: "{now.strftime("%Y-%m-%d")}"',
        f'party_level: {max((pc.level for pc in PARTY_LIBRARY.values()), default=1)}',
        '---',
        '',
        f'# {title}',
        f'*{now.strftime("%B %d, %Y")}*',
        '',
    ]
    
    # Party status snapshot
    lines.append('## Party Status')
    lines.append('| Character | Class | Level | HP | Conditions |')
    lines.append('|-----------|-------|-------|----|------------|')
    for pc in PARTY_LIBRARY.values():
        active_conds = [f'{k} {v}' for k, v in pc.conditions.items() if v and v != 0 and v is not False]
        cond_str = ', '.join(active_conds) if active_conds else '—'
        lines.append(f'| [[{pc.name}]] | {pc.class_name} | {pc.level} | {pc.current_hp}/{pc.hp} | {cond_str} |')
    lines.append('')
    
    # Session notes from each character
    has_notes = False
    for pc in PARTY_LIBRARY.values():
        if pc.session_notes:
            latest = pc.session_notes[-1] if pc.session_notes else None
            if latest:
                if not has_notes:
                    lines.append('## Player Notes')
                    has_notes = True
                lines.append(f'**{pc.name}:** {latest["text"]}')
    if has_notes:
        lines.append('')
    
    # GM recap
    if recap:
        lines.append('## Recap')
        lines.append(recap)
        lines.append('')
    
    # Write
    session_dir = os.path.join(OBSIDIAN_DIR, 'Sessions')
    os.makedirs(session_dir, exist_ok=True)
    safe_title = re.sub(r'[^a-zA-Z0-9_\- ]', '', title)
    file_name = f'{now.strftime("%Y-%m-%d")} - {safe_title}.md'
    file_path = os.path.join(session_dir, file_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    rel_path = f'Sessions/{file_name}'
    return jsonify({"success": True, "path": rel_path})

@app.route('/api/vault_export_encounter', methods=['POST'])
def vault_export_encounter():
    """Export the current/last encounter as a vault log."""
    import datetime
    now = datetime.datetime.now()
    
    data = request.json or {}
    encounter_name = data.get('name', 'Encounter')
    outcome = data.get('outcome', '')
    loot = data.get('loot', '')
    xp = data.get('xp', '')
    
    lines = [
        '---',
        f'type: encounter',
        f'date: "{now.strftime("%Y-%m-%d %H:%M")}"',
        f'round: {ROUND_NUMBER}',
        f'outcome: "{outcome}"',
        '---',
        '',
        f'# {encounter_name}',
        f'*{now.strftime("%B %d, %Y at %I:%M %p")}*',
        '',
    ]
    
    # Combatants
    if ACTIVE_ENCOUNTER:
        lines.append('## Combatants')
        lines.append('| Name | Init | HP | Status |')
        lines.append('|------|------|----|--------|')
        for c in sorted(ACTIVE_ENCOUNTER, key=lambda x: x.initiative, reverse=True):
            hp_str = f'{c.current_hp}/{c.hp}' if hasattr(c, 'current_hp') else '—'
            status = 'Alive' if c.current_hp > 0 else 'Down'
            name = f'[[{c.name}]]' if c.is_pc else c.name
            lines.append(f'| {name} | {c.initiative} | {hp_str} | {status} |')
        lines.append('')
    
    lines.append(f'**Rounds:** {ROUND_NUMBER}')
    if xp: lines.append(f'**XP Earned:** {xp}')
    if loot: lines.append(f'\n## Loot\n{loot}')
    if outcome: lines.append(f'\n## Outcome\n{outcome}')
    lines.append('')
    
    # Write
    enc_dir = os.path.join(OBSIDIAN_DIR, 'Encounters')
    os.makedirs(enc_dir, exist_ok=True)
    safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', encounter_name)
    file_name = f'{now.strftime("%Y-%m-%d")} - {safe_name}.md'
    file_path = os.path.join(enc_dir, file_name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    
    rel_path = f'Encounters/{file_name}'
    return jsonify({"success": True, "path": rel_path})

@app.route('/player')
def player_view(): 
    # Sync from disk to catch any characters added outside this process
    _sync_party_from_disk()
    return render_template('player_view.html', party=list(PARTY_LIBRARY.values()))

def _sync_party_from_disk():
    """Ensure PARTY_LIBRARY matches what's on disk. Adds missing characters, removes deleted ones."""
    if not os.path.exists(PARTY_DIR): return
    
    try:
        disk_files = {f for f in os.listdir(PARTY_DIR) if f.endswith('.json')}
    except OSError as e:
        print(f"[SYNC ERROR] Failed to list party directory: {e}")
        return
    
    disk_names = set()
    load_errors = []
    
    for f in disk_files:
        file_path = os.path.join(PARTY_DIR, f)
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
        except Exception as e:
            load_errors.append(f"[SYNC ERROR] Failed to load character from {f}: {e}")
    
    # Log any errors encountered
    for err in load_errors:
        print(err)
    
    # Remove characters from memory that were deleted from disk
    for name in list(PARTY_LIBRARY.keys()):
        if name not in disk_names:
            del PARTY_LIBRARY[name]
    
    _build_pc_file_cache()

@app.route('/player/sheet/<pc_name>')
def player_sheet(pc_name):
    if pc_name in PARTY_LIBRARY: 
        return render_template('player_sheet.html', pc=PARTY_LIBRARY[pc_name], weapons_json=json.dumps(BUILDER_WEAPONS), builder_armor=BUILDER_ARMOR, armor_json=json.dumps(BUILDER_ARMOR), spells_json=json.dumps([{'name': s['name'], 'level': s['level'], 'traditions': s['traditions']} for s in BUILDER_SPELLS]))
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
    # Resolve GM status from the live Flask session at connect time. SSE
    # connections are long-lived; the session state captured here is what
    # sse_broadcast() uses to decide whether this subscriber gets raw GM
    # data or the player-sanitized view.
    is_gm = _is_gm()

    def generate():
        q = queue.Queue(maxsize=50)
        entry = (q, is_gm)
        with _sse_lock:
            # Enforce max subscriber cap
            if len(_sse_subscribers) >= _SSE_MAX_SUBSCRIBERS:
                # Remove oldest subscriber
                _sse_subscribers.pop(0)
            _sse_subscribers.append(entry)
        try:
            yield "event: connected\ndata: {}\n\n"
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
                data['attacks'] = [{'name': s['name'], 'hit': f"+{s['bonus']}", 'damage': s['damage']} for s in c.strikes]
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
    log_entry = {
        'id': str(uuid.uuid4()),
        'name': data.get('name', 'Player'),
        'action': data.get('action', 'Action'),
        'result': data.get('result', ''),
        'detail': data.get('detail', ''),
        'degree': degree,
        'time': datetime.now().strftime('%H:%M:%S'),
        'round': ROUND_NUMBER
    }
    COMBAT_LOGS.append(log_entry)
    if len(COMBAT_LOGS) > 200: COMBAT_LOGS.pop(0)

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

@app.route('/api/compendium_search')
def compendium_search():
    """Search the PF2E compendium database across feats, spells, and equipment."""
    query = request.args.get('q', '').strip()
    category = request.args.get('cat', 'all')  # all, feats, spells, equipment
    if not query or len(query) < 2:
        return jsonify({"results": []})

    conn = _get_compendium_db()
    if conn is None:
        return jsonify({"results": [], "error": "Database not found"})
    c = conn.cursor()
    results = []
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
        file_path = get_pc_file_path(pc_name)
        if file_path and os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
            build = pc_json.get('build', pc_json)
            build['expended_slots'] = {}
            build['prepared_spells'] = {}
            build['cast_prep'] = {}
            save_and_reload_character(pc_name, pc_json, file_path)

        # Sync to tracker
        for c in ACTIVE_ENCOUNTER:
            if c.is_pc and c.name == pc_name:
                c.current_hp = pc.current_hp
                c.current_focus = pc.focus_max
                c.conditions = dict(pc.conditions)
        
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

@app.route('/api/equip_armor/<pc_name>', methods=['POST'])
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

    # POST — save slot state (accepts any combination of the three keys)
    data = request.json
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

@app.route('/api/add_weapon/<pc_name>', methods=['POST'])
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
def toggle_two_hand(pc_name):
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    w_name = data.get('name', '')
    if 'weapons' in build and isinstance(build['weapons'], list):
        for w in build['weapons']:
            if w.get('name') == w_name:
                w['is_two_handed'] = not w.get('is_two_handed', False)
                break
                
    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True})

@app.route('/api/delete_weapon/<pc_name>', methods=['POST'])
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
def toggle_feature(pc_name, feature_name):
    """Toggle a class feature on/off (like Rage, Panache, etc.)."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    pc = PARTY_LIBRARY[pc_name]
    file_path = get_pc_file_path(pc_name)
    if not file_path:
        return jsonify({"error": "File not found"}), 404
    
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
    
    return jsonify({"success": True, "active": active, "feature": feature_name, "effects": effects})

@app.route('/api/set_reaction/<pc_name>', methods=['POST'])
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

    # Signature spells (spontaneous casters) — drop the entry if present
    sigs = build.get('signature_spells') or []
    if spell_name in sigs:
        build['signature_spells'] = [s for s in sigs if s != spell_name]

    save_and_reload_character(pc_name, pc_json, file_path)
    return jsonify({"success": True, "removed": removed, "spell": spell_name, "level": spell_level})

@app.route('/api/set_signature_spells/<pc_name>', methods=['POST'])
def set_signature_spells(pc_name):
    """Set signature spells for spontaneous casters."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    data = request.json
    file_path = get_pc_file_path(pc_name)
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    build['signature_spells'] = data.get('signature_spells', [])
    save_and_reload_character(pc_name, pc_json, file_path)
    
    return jsonify({"success": True})

@app.route('/api/set_focus_spells/<pc_name>', methods=['POST'])
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
def send_initiative(pc_name):
    """Player rolls initiative and sends it to the GM encounter tracker."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json
    roll_total = safe_int(data.get('total'), 0)
    PENDING_INITIATIVES[pc_name] = {'total': roll_total, 'time': time.time()}
    
    return jsonify({"success": True, "initiative": roll_total})

@app.route('/api/save_session_note/<pc_name>', methods=['POST'])
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

@app.route('/api/cast_spell/<pc_name>', methods=['POST'])
def cast_spell(pc_name):
    """Cast a spell: auto-deduct the spell slot and broadcast the change.
    Body: {caster_idx: int, level: int, slot_idx: int, spell_name: str}
    """
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    data = request.json or {}
    caster_idx = int(data.get('caster_idx', 0))
    spell_level = int(data.get('level', 0))
    slot_idx = int(data.get('slot_idx', 0))
    spell_name = data.get('spell_name', 'Unknown')

    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return jsonify({"error": "Character file not found"}), 404

    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    expended = build.get('expended_slots', {})

    # Mark the slot as expended: key is "caster_idx-level-slot_idx"
    slot_key = f"{caster_idx}-{spell_level}-{slot_idx}"
    expended[slot_key] = True
    build['expended_slots'] = expended
    save_and_reload_character(pc_name, pc_json, file_path)

    # Broadcast to all clients (GM sees updated spell usage)
    _broadcast_pc_state(pc_name)

    return jsonify({"success": True, "slot_key": slot_key, "spell_name": spell_name})

@app.route('/api/upload_portrait/<pc_name>', methods=['POST'])
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
        # Accept either file upload or JSON body
        if 'file' in request.files:
            file = request.files['file']
            raw = file.read().decode('utf-8')
            pc_json = json.loads(raw)
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
    save_and_reload_character(char_name, new_char_json, file_path)
    return jsonify({"success": True, "message": "Character saved successfully!"})

@app.route('/player/levelup/<pc_name>')
def player_levelup(pc_name):
    if pc_name in PARTY_LIBRARY: 
        pc = PARTY_LIBRARY[pc_name]
        return render_template('player_levelup.html', pc=pc, feats=BUILDER_FEATS, spells=BUILDER_SPELLS, class_matrix=CLASS_MATRIX, builder_data=BUILDER_DATA, class_progression=CLASS_PROGRESSION, subclass_progression=SUBCLASS_PROGRESSION, monk_path_config=MONK_PATH_CONFIG, skill_feat_prereqs=SKILL_FEAT_PREREQS, char_proficiencies=pc.proficiencies)
    return redirect(url_for('player_view'))

@app.route('/api/submit_levelup/<pc_name>', methods=['POST'])
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
    
    # Only apply ability boosts at PF2E-qualifying levels (5, 10, 15, 20)
    ABILITY_BOOST_LEVELS = {5, 10, 15, 20}
    if 'abilities' in data and new_level in ABILITY_BOOST_LEVELS:
        build['abilities'] = data['abilities']
    if 'half_boosts' in data and new_level in ABILITY_BOOST_LEVELS:
        build['half_boosts'] = data['half_boosts']
    
    if 'feats' in data: build['feats'] = data['feats']
    
    if 'proficiencies' not in build: build['proficiencies'] = {}

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
    subclass_name = build.get('subclass', '')
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

    save_and_reload_character(pc_name, pc_json, file_path)
    # Push updated HP / conditions / level to all connected clients so the
    # GM tracker and party view reflect the level-up immediately without
    # a manual refresh. The levelling player's own sheet redirects via JS,
    # but everyone else (GM, other players viewing party) receives this SSE.
    _broadcast_pc_state(pc_name)
    _broadcast_encounter_state()
    return jsonify({"success": True})

@app.route('/api/revert_level/<pc_name>', methods=['POST'])
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
    
    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', name)
    file_path = os.path.join(MONSTER_DIR, f"{safe_name}.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(monster_json, f, indent=2)
    
    try:
        m = Monster(monster_json, f"{safe_name}.json")
        MONSTER_LIBRARY[f"{safe_name}.json"] = m
        return jsonify({"success": True, "name": name, "level": m.level})
    except Exception as e:
        return jsonify({"success": True, "name": name, "warning": f"Saved but parse error: {e}"})

# =============================================================================
# VTT MAP SYSTEM
# =============================================================================

def _broadcast_map_state():
    """Broadcast full map state to all connected clients.

    Keep this payload in sync with the ACTIVE_MAP schema — the client does
    `mapState = JSON.parse(e.data)` on this event, so any key missing here
    is erased from the browser's view until the next page reload. That's
    how Phase 2's lights/ambient and Phase 3's templates silently vanished
    on every refresh before we caught it.
    """
    with MAP_LOCK:
        state = {
            'id': ACTIVE_MAP['id'],
            'name': ACTIVE_MAP['name'],
            'image': ACTIVE_MAP['image'],
            'grid_size': ACTIVE_MAP['grid_size'],
            'grid_offset_x': ACTIVE_MAP['grid_offset_x'],
            'grid_offset_y': ACTIVE_MAP['grid_offset_y'],
            'tokens': ACTIVE_MAP['tokens'],
            'walls': ACTIVE_MAP.get('walls', []),
            'explored': ACTIVE_MAP.get('explored', []),
            'difficult_terrain': ACTIVE_MAP.get('difficult_terrain', []),
            'spawn_point': ACTIVE_MAP.get('spawn_point'),
            'player_control': ACTIVE_MAP['player_control'],
            'ambient_light': ACTIVE_MAP.get('ambient_light', 'bright'),
            'lights': ACTIVE_MAP.get('lights', []),
            'gm_notes': ACTIVE_MAP.get('gm_notes', []),
            'templates': ACTIVE_MAP.get('templates', []),
        }
    sse_broadcast('map_state', state)

def _broadcast_map_tokens():
    """Broadcast just token positions."""
    with MAP_LOCK:
        tokens = ACTIVE_MAP['tokens']
    sse_broadcast('map_tokens', {'tokens': tokens})

def _broadcast_map_fog():
    """Broadcast fog state (GM only sends, players receive filtered)."""
    with MAP_LOCK:
        fog = ACTIVE_MAP['fog']
    sse_broadcast('map_fog', {'fog': fog})

def _broadcast_event(event_type, data):
    """Broadcast a generic event to all connected clients."""
    sse_broadcast(event_type, data)

def _save_map_state():
    """Persist current map state to disk."""
    with MAP_LOCK:
        if not ACTIVE_MAP['id']:
            return
        state_path = os.path.join(MAP_DIR, f"{ACTIVE_MAP['id']}_state.json")
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(ACTIVE_MAP, f, indent=2)

def _load_map_state(map_id):
    """Load map state from disk."""
    global ACTIVE_MAP
    state_path = os.path.join(MAP_DIR, f"{map_id}_state.json")
    if os.path.exists(state_path):
        with open(state_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            with MAP_LOCK:
                ACTIVE_MAP.update(data)
            return True
    return False

@app.route('/gm/map')
@gm_required
def gm_map_view():
    """GM's full-control VTT map view."""
    # Get list of available maps
    maps = []
    if os.path.exists(MAP_DIR):
        for f in os.listdir(MAP_DIR):
            if f.endswith(('.png', '.jpg', '.jpeg', '.webp')):
                maps.append({'id': f.rsplit('.', 1)[0], 'filename': f})
    
    with MAP_LOCK:
        current_map = dict(ACTIVE_MAP)
    
    # Get party with full stats for token options
    party = []
    for pc in PARTY_LIBRARY.values():
        party.append({
            'name': pc.name,
            'hp': pc.hp,
            'max_hp': pc.hp,
            'current_hp': pc.current_hp,
            'ac': pc.ac,
            'speed': getattr(pc, 'speed', 25),
            'perception': pc.perception if hasattr(pc, 'perception') else 10,
        })
    
    # Get encounter with full stats
    encounter = []
    for c in ACTIVE_ENCOUNTER:
        encounter.append({
            'id': c.instance_id,
            'name': c.name,
            'hp': c.hp,
            'current_hp': c.current_hp,
            'ac': c.ac if hasattr(c, 'ac') else 10,
            'is_pc': c.is_pc,
            'initiative': getattr(c, 'initiative', 0),
            'conditions': {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False} if hasattr(c, 'conditions') else {},
        })
    
    return render_template('map_vtt.html', 
                           maps=maps, 
                           current_map=current_map,
                           party=party,
                           encounter=encounter,
                           turn_index=TURN_INDEX,
                           round_number=ROUND_NUMBER)

def _apply_player_map_filter(state, player_name):
    """Mutate a copy of ACTIVE_MAP to produce the view a non-GM player
    should see. Consolidates the secret-door / hidden-token / gm-note /
    token-vision filtering so /map bootstrap and /api/map/state never
    drift apart.

    Rules (applied in order):
      1. Drop any token with visible_to_players == False (GM-hidden).
      2. Mask secret doors → plain walls unless this player has discovered
         them (via the Seek action — see seek_wall).
      3. Drop GM notes entirely — pins are GM-only by definition.
      4. If ambient_light != 'bright' and the player has an assigned token,
         drop tokens that are NOT in line-of-sight + lit area of any of
         the player's owned tokens. GM vision always wins.

    Keeping this in one place matters: SSE broadcasts are fan-out, so the
    /api/map/state refetch that player clients do on every SSE tick is
    the ONLY scrubber between raw ACTIVE_MAP and the player's browser.
    """
    # Step 1 — hidden tokens
    state['tokens'] = [t for t in state.get('tokens', []) if t.get('visible_to_players', True)]

    # Step 2 — secret door masking
    walls_out = []
    for w in state.get('walls', []):
        if w.get('secret') and w.get('type') == 'door':
            discovered = w.get('discovered_by') or []
            if player_name and player_name in discovered:
                walls_out.append(w)
            else:
                masked = dict(w)
                masked['type'] = 'normal'
                masked['open'] = False
                masked.pop('secret', None)
                masked.pop('hidden_dc', None)
                masked.pop('discovered_by', None)
                walls_out.append(masked)
        else:
            walls_out.append(w)
    state['walls'] = walls_out

    # Step 3 — GM notes are never sent to players
    state.pop('gm_notes', None)

    # Step 4 — token vision filter
    ambient = state.get('ambient_light', 'bright')
    if ambient != 'bright' and player_name:
        owned = [t for t in state['tokens']
                 if (t.get('assigned_player') == player_name
                     or t.get('pc_name') == player_name)]
        if owned:
            visible_ids = _tokens_visible_to_owners(
                state['tokens'], owned, state['walls'],
                state.get('lights', []), ambient,
                state.get('grid_size', 70),
            )
            state['tokens'] = [t for t in state['tokens'] if t['id'] in visible_ids]

    return state


def _tokens_visible_to_owners(all_tokens, owners, walls, lights, ambient, grid_size):
    """Return the set of token ids a player should see, given the tokens
    they control and the map's lighting state.

    A token is visible if, from ANY owner token:
      - it lies within light (attached lights on owner count as lights) AND
      - the straight line between them isn't blocked by a vision-blocking wall.

    'bright' ambient skips this path entirely (see caller).
    'dim' ambient: everywhere counts as dim light — only darkvision needed for full vision.
    'dark' ambient: only bright rings of lights illuminate. Owners with darkvision see out to their vision_radius as dim.
    """
    visible = set()
    # Owners are always visible to themselves
    for o in owners:
        visible.add(o['id'])

    # Light rings — attached lights move with their token
    def _light_origin(light):
        if light.get('attached_to'):
            tok = next((t for t in all_tokens if t['id'] == light['attached_to']), None)
            if tok:
                return (tok['x'] * grid_size + grid_size/2, tok['y'] * grid_size + grid_size/2)
            return None
        return (light['x'], light['y'])

    active_lights = []
    for l in lights:
        if not l.get('enabled', True):
            continue
        origin = _light_origin(l)
        if origin is None:
            continue
        active_lights.append({
            'x': origin[0], 'y': origin[1],
            'bright_px': l['bright'] * grid_size,
            'dim_px': (l['bright'] + l['dim']) * grid_size,
        })

    for target in all_tokens:
        if target['id'] in visible:
            continue
        tx = target['x'] * grid_size + grid_size/2
        ty = target['y'] * grid_size + grid_size/2

        for owner in owners:
            ox = owner['x'] * grid_size + grid_size/2
            oy = owner['y'] * grid_size + grid_size/2
            dist = ((tx - ox)**2 + (ty - oy)**2) ** 0.5
            # LOS first — if walls block, no light matters
            if _segment_blocked(ox, oy, tx, ty, walls):
                continue
            # Is target in any bright light?
            in_bright = any(((tx - L['x'])**2 + (ty - L['y'])**2) ** 0.5 <= L['bright_px']
                            for L in active_lights)
            in_dim = any(((tx - L['x'])**2 + (ty - L['y'])**2) ** 0.5 <= L['dim_px']
                         for L in active_lights)

            owner_vision_px = (owner.get('vision_radius') or 0) * grid_size
            in_owner_vision = dist <= owner_vision_px if owner_vision_px > 0 else False
            has_dv = bool(owner.get('darkvision'))
            has_ll = bool(owner.get('low_light_vision'))

            if ambient == 'dim':
                # Whole map is dim light. Everyone sees within vision_radius
                # as dim; darkvision/lowlight upgrades but result is the same
                # (we just track visible vs not).
                if in_owner_vision or in_bright or in_dim:
                    visible.add(target['id']); break
            elif ambient == 'dark':
                # Only lit areas are seen, except darkvision sees up to
                # vision_radius even in darkness.
                if in_bright:
                    visible.add(target['id']); break
                if in_dim and (has_dv or has_ll):
                    visible.add(target['id']); break
                if has_dv and in_owner_vision:
                    visible.add(target['id']); break

    return visible


def _segment_blocked(x1, y1, x2, y2, walls):
    """True if any LOS-blocking wall segment intersects the line from
    (x1,y1) to (x2,y2). Doors that are open (open=True) don't block."""
    for w in walls:
        wtype = w.get('type', 'normal')
        if wtype == 'ethereal':
            continue
        if wtype == 'door' and w.get('open'):
            continue
        pts = w.get('points') or []
        for i in range(len(pts) - 1):
            if _segments_cross(x1, y1, x2, y2, pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]):
                return True
        if w.get('closed') and len(pts) >= 3:
            if _segments_cross(x1, y1, x2, y2, pts[-1][0], pts[-1][1], pts[0][0], pts[0][1]):
                return True
    return False


def _segments_cross(ax, ay, bx, by, cx, cy, dx, dy):
    """Standard 2D segment intersection. Endpoint-touch doesn't count so a
    wall touching an owner token doesn't block its own vision."""
    d1x, d1y = bx - ax, by - ay
    d2x, d2y = dx - cx, dy - cy
    denom = d1x * d2y - d1y * d2x
    if abs(denom) < 1e-9:
        return False
    s = ((cx - ax) * d2y - (cy - ay) * d2x) / denom
    t = ((cx - ax) * d1y - (cy - ay) * d1x) / denom
    return 0.001 < s < 0.999 and 0.001 < t < 0.999


@app.route('/map')
def player_map_view():
    """Player's restricted map view."""
    player_name = session.get('player_name')
    with MAP_LOCK:
        current_map = copy.deepcopy(ACTIVE_MAP)
    _apply_player_map_filter(current_map, player_name)
    return render_template('map_player.html', current_map=current_map)

@app.route('/api/map/upload', methods=['POST'])
@gm_required
def upload_map():
    """Upload a new map image."""
    if 'map' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    
    file = request.files['map']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
    
    # Validate file type
    allowed = {'png', 'jpg', 'jpeg', 'webp'}
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in allowed:
        return jsonify({'success': False, 'error': f'Invalid file type. Allowed: {allowed}'}), 400
    
    # Generate unique ID and save
    map_id = str(uuid.uuid4())[:8]
    filename = f"{map_id}.{ext}"
    filepath = os.path.join(MAP_DIR, filename)
    file.save(filepath)
    
    # Get custom name or use filename
    map_name = request.form.get('name', file.filename.rsplit('.', 1)[0])
    
    return jsonify({
        'success': True,
        'id': map_id,
        'filename': filename,
        'name': map_name
    })

@app.route('/api/map/load', methods=['POST'])
@gm_required
def load_map():
    """Load a map as the active map."""
    global ACTIVE_MAP
    data = request.json or {}
    map_id = data.get('id')
    filename = data.get('filename')
    
    if not filename:
        return jsonify({'success': False, 'error': 'No map specified'}), 400
    
    filepath = os.path.join(MAP_DIR, filename)
    if not os.path.exists(filepath):
        return jsonify({'success': False, 'error': 'Map file not found'}), 404
    
    # Try to load existing state, or create new
    if not _load_map_state(map_id):
        with MAP_LOCK:
            ACTIVE_MAP = {
                'id': map_id,
                'name': data.get('name', map_id),
                'image': filename,
                'grid_size': int(data.get('grid_size', 70)),
                'grid_offset_x': 0,
                'grid_offset_y': 0,
                'tokens': [],
                'fog': [],
                'walls': [],
                'fog_enabled': True,
                'player_control': False,
                'vision_mode': 'explored',
            }
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'map': ACTIVE_MAP})

@app.route('/api/map/settings', methods=['POST'])
@gm_required
def update_map_settings():
    """Update map settings (grid size, offset, etc.)."""
    data = request.json or {}
    with MAP_LOCK:
        if 'grid_size' in data:
            ACTIVE_MAP['grid_size'] = int(data['grid_size'])
        if 'grid_offset_x' in data:
            ACTIVE_MAP['grid_offset_x'] = int(data['grid_offset_x'])
        if 'grid_offset_y' in data:
            ACTIVE_MAP['grid_offset_y'] = int(data['grid_offset_y'])
        if 'fog_enabled' in data:
            ACTIVE_MAP['fog_enabled'] = bool(data['fog_enabled'])
        if 'player_control' in data:
            ACTIVE_MAP['player_control'] = bool(data['player_control'])
        if 'vision_mode' in data:
            ACTIVE_MAP['vision_mode'] = data['vision_mode']
        if 'lighting' in data:
            ACTIVE_MAP['lighting'] = data['lighting']  # bright, dim, darkness
        if 'name' in data:
            ACTIVE_MAP['name'] = data['name']
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})

@app.route('/api/map/image/<filename>')
def serve_map_image(filename):
    """Serve map image files."""
    return send_from_directory(MAP_DIR, filename)

# --- TOKEN MANAGEMENT ---

@app.route('/api/map/token/add', methods=['POST'])
@gm_required
def add_map_token():
    """Add a token to the map."""
    data = request.json or {}
    
    # Default vision: 6 squares (30ft) for PCs, 0 for monsters (GM controls monster visibility)
    default_vision = 6 if data.get('is_pc', False) else 0
    
    # Auto-detect senses from character data for PCs
    has_darkvision = data.get('darkvision', False)
    has_low_light = data.get('low_light_vision', False)
    pc_name = data.get('pc_name') or data.get('name')
    if data.get('is_pc') and pc_name:
        for lib_name, pc in PARTY_LIBRARY.items():
            if lib_name == pc_name or pc.name == pc_name:
                senses = getattr(pc, 'senses', [])
                if any('darkvision' in s.lower() for s in senses):
                    has_darkvision = True
                if any('low-light' in s.lower() for s in senses):
                    has_low_light = True
                break
    
    token = {
        'id': str(uuid.uuid4())[:8],
        'name': data.get('name', 'Token'),
        'x': int(data.get('x', 0)),  # Grid coordinates
        'y': int(data.get('y', 0)),
        'size': int(data.get('size', 1)),  # 1 = medium, 2 = large, etc.
        'color': data.get('color', '#3B82F6'),
        'image': data.get('image'),  # Optional custom image
        'pc_name': data.get('pc_name'),  # Link to party member
        'instance_id': data.get('instance_id'),  # Link to encounter combatant
        'is_pc': data.get('is_pc', False),
        'hp': int(data.get('hp', 0)),
        'max_hp': int(data.get('max_hp', 0)),
        'visible_to_players': data.get('visible_to_players', True),
        'vision_radius': int(data.get('vision_radius', default_vision)),  # Squares of vision (0 = no vision)
        'assigned_player': data.get('assigned_player'),  # Player name who can control this token
        'darkvision': has_darkvision,
        'low_light_vision': has_low_light,
    }
    
    with MAP_LOCK:
        ACTIVE_MAP['tokens'].append(token)
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True, 'token': token})

@app.route('/api/map/player/register', methods=['POST'])
def register_player_name():
    """Register a player name in the server session for token auth."""
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'No name provided'}), 400
    session['player_name'] = name
    return jsonify({'success': True, 'name': name})

@app.route('/api/map/token/move', methods=['POST'])
def move_map_token():
    """Move a token on the map."""
    data = request.json or {}
    token_id = data.get('id')
    new_x = int(data.get('x', 0))
    new_y = int(data.get('y', 0))
    
    # Check if player is allowed to move tokens
    is_gm = _is_gm()

    with MAP_LOCK:
        if not is_gm and not ACTIVE_MAP.get('player_control'):
            return jsonify({'success': False, 'error': 'Player movement disabled'}), 403
        
        for token in ACTIVE_MAP['tokens']:
            if token['id'] == token_id:
                # If player, verify they are assigned to this token via server-side session
                if not is_gm:
                    player_name = session.get('player_name')
                    if not player_name:
                        return jsonify({'success': False, 'error': 'Register your player name first'}), 403
                    if token.get('assigned_player') != player_name:
                        return jsonify({'success': False, 'error': 'Not your token'}), 403
                
                token['x'] = new_x
                token['y'] = new_y
                break
        else:
            return jsonify({'success': False, 'error': 'Token not found'}), 404
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/token/update', methods=['POST'])
@gm_required
def update_map_token():
    """Update token properties."""
    data = request.json or {}
    token_id = data.get('id')
    
    with MAP_LOCK:
        for token in ACTIVE_MAP['tokens']:
            if token['id'] == token_id:
                if 'name' in data: token['name'] = data['name']
                if 'color' in data: token['color'] = data['color']
                if 'size' in data: token['size'] = int(data['size'])
                if 'hp' in data: token['hp'] = int(data['hp'])
                if 'max_hp' in data: token['max_hp'] = int(data['max_hp'])
                if 'visible_to_players' in data: token['visible_to_players'] = bool(data['visible_to_players'])
                if 'vision_radius' in data: token['vision_radius'] = int(data['vision_radius'])
                if 'assigned_player' in data: token['assigned_player'] = data['assigned_player']
                if 'initiative' in data: token['initiative'] = data['initiative']
                if 'conditions' in data: token['conditions'] = data['conditions']  # Can be dict or list
                if 'ac' in data: token['ac'] = int(data['ac'])
                if 'speed' in data: token['speed'] = int(data['speed'])
                break
        else:
            return jsonify({'success': False, 'error': 'Token not found'}), 404
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/token/remove', methods=['POST'])
@gm_required
def remove_map_token():
    """Remove a token from the map."""
    data = request.json or {}
    token_id = data.get('id')
    
    with MAP_LOCK:
        ACTIVE_MAP['tokens'] = [t for t in ACTIVE_MAP['tokens'] if t['id'] != token_id]
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/token/sync_encounter', methods=['POST'])
@gm_required
def sync_tokens_from_encounter():
    """Sync tokens with the active encounter (add missing, update HP)."""
    added = 0
    updated = 0
    
    with MAP_LOCK:
        existing_ids = {t.get('instance_id') for t in ACTIVE_MAP['tokens'] if t.get('instance_id')}
        
        for i, combatant in enumerate(ACTIVE_ENCOUNTER):
            if combatant.instance_id in existing_ids:
                # Update stats
                for token in ACTIVE_MAP['tokens']:
                    if token.get('instance_id') == combatant.instance_id:
                        token['hp'] = combatant.current_hp
                        token['max_hp'] = combatant.hp
                        token['ac'] = combatant.ac if hasattr(combatant, 'ac') else 10
                        token['conditions'] = [f"{k}:{v}" if isinstance(v, int) and v > 0 else k 
                                               for k, v in combatant.conditions.items() 
                                               if v and v != 0 and v is not False] if hasattr(combatant, 'conditions') else []
                        token['initiative'] = getattr(combatant, 'initiative', 0)
                        updated += 1
                        break
            else:
                # Add new token
                color = '#22C55E' if combatant.is_pc else '#EF4444'
                # Get speed from party library if PC
                speed = 25
                if combatant.is_pc and combatant.name in PARTY_LIBRARY:
                    pc = PARTY_LIBRARY[combatant.name]
                    speed = getattr(pc, 'speed', 25)
                
                token = {
                    'id': str(uuid.uuid4())[:8],
                    'name': combatant.name,
                    'x': 5 + (i % 5),  # Spread out initially
                    'y': 5 + (i // 5),
                    'size': getattr(combatant, 'size', 1) if hasattr(combatant, 'size') else 1,
                    'color': color,
                    'instance_id': combatant.instance_id,
                    'is_pc': combatant.is_pc,
                    'hp': combatant.current_hp,
                    'max_hp': combatant.hp,
                    'ac': combatant.ac if hasattr(combatant, 'ac') else 10,
                    'speed': speed,
                    'conditions': [],
                    'assigned_player': combatant.name if combatant.is_pc else None,
                    'visible_to_players': True,
                    'initiative': getattr(combatant, 'initiative', 0),
                }
                ACTIVE_MAP['tokens'].append(token)
                added += 1
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True, 'added': added, 'updated': updated})

@app.route('/api/map/token/damage', methods=['POST'])
@gm_required
def damage_map_token():
    """Apply damage to a token and sync with encounter."""
    data = request.json or {}
    token_id = data.get('id')
    amount = int(data.get('amount', 0))
    
    with MAP_LOCK:
        for token in ACTIVE_MAP['tokens']:
            if token['id'] == token_id:
                token['hp'] = max(0, token['hp'] - amount)
                
                # Sync with encounter if linked
                if token.get('instance_id'):
                    for c in ACTIVE_ENCOUNTER:
                        if c.instance_id == token['instance_id']:
                            c.current_hp = token['hp']
                            # Handle dying/wounded
                            if c.current_hp <= 0 and hasattr(c, 'conditions'):
                                if c.is_pc:
                                    c.conditions['dying'] = 1 + c.conditions.get('wounded', 0)
                            break
                break
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/token/heal', methods=['POST'])
@gm_required
def heal_map_token():
    """Heal a token and sync with encounter."""
    data = request.json or {}
    token_id = data.get('id')
    amount = int(data.get('amount', 0))
    
    with MAP_LOCK:
        for token in ACTIVE_MAP['tokens']:
            if token['id'] == token_id:
                token['hp'] = min(token['max_hp'], token['hp'] + amount)
                
                # Sync with encounter if linked
                if token.get('instance_id'):
                    for c in ACTIVE_ENCOUNTER:
                        if c.instance_id == token['instance_id']:
                            c.current_hp = token['hp']
                            # Clear dying if healed above 0
                            if c.current_hp > 0 and hasattr(c, 'conditions'):
                                if c.conditions.get('dying', 0) > 0:
                                    c.conditions['dying'] = 0
                                    c.conditions['wounded'] = c.conditions.get('wounded', 0) + 1
                            break
                break
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/token/condition', methods=['POST'])
@gm_required
def toggle_map_token_condition():
    """Toggle a condition on a token."""
    data = request.json or {}
    token_id = data.get('id')
    condition = data.get('condition', '')
    value = data.get('value')  # Optional value for valued conditions
    
    with MAP_LOCK:
        for token in ACTIVE_MAP['tokens']:
            if token['id'] == token_id:
                conditions = token.get('conditions', [])
                
                # Check if condition exists
                existing = None
                for i, c in enumerate(conditions):
                    if c.startswith(condition.lower()):
                        existing = i
                        break
                
                if existing is not None:
                    # Remove condition
                    conditions.pop(existing)
                else:
                    # Add condition
                    if value is not None:
                        conditions.append(f"{condition.lower()}:{value}")
                    else:
                        conditions.append(condition.lower())
                
                token['conditions'] = conditions
                
                # Sync with encounter
                if token.get('instance_id'):
                    for c in ACTIVE_ENCOUNTER:
                        if c.instance_id == token['instance_id'] and hasattr(c, 'conditions'):
                            cond_lower = condition.lower().replace('-', '_').replace(' ', '_')
                            if cond_lower in c.conditions:
                                if isinstance(c.conditions[cond_lower], bool):
                                    c.conditions[cond_lower] = not c.conditions[cond_lower]
                                elif isinstance(c.conditions[cond_lower], int):
                                    if c.conditions[cond_lower] > 0:
                                        c.conditions[cond_lower] = 0
                                    else:
                                        c.conditions[cond_lower] = value if value else 1
                            break
                break
    
    _save_map_state()
    _broadcast_map_tokens()
    return jsonify({'success': True})

@app.route('/api/map/terrain/toggle', methods=['POST'])
@gm_required
def toggle_difficult_terrain():
    """Toggle difficult terrain on a grid cell."""
    data = request.json or {}
    x = int(data.get('x', 0))
    y = int(data.get('y', 0))
    
    with MAP_LOCK:
        terrain = ACTIVE_MAP.get('difficult_terrain', [])
        cell = {'x': x, 'y': y}
        
        # Check if already marked
        found = False
        for i, t in enumerate(terrain):
            if t['x'] == x and t['y'] == y:
                terrain.pop(i)
                found = True
                break
        
        if not found:
            terrain.append(cell)
        
        ACTIVE_MAP['difficult_terrain'] = terrain
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})

@app.route('/api/map/spawn', methods=['POST'])
@gm_required
def set_spawn_point():
    """Set the party spawn point on the map."""
    data = request.json or {}
    x = int(data.get('x', 0))
    y = int(data.get('y', 0))
    
    with MAP_LOCK:
        ACTIVE_MAP['spawn_point'] = {'x': x, 'y': y}
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})

@app.route('/api/map/wall/toggle_door', methods=['POST'])
@gm_required
def toggle_door():
    """Toggle a door open/closed."""
    data = request.json or {}
    wall_id = data.get('id')
    
    with MAP_LOCK:
        for wall in ACTIVE_MAP.get('walls', []):
            if wall['id'] == wall_id and wall.get('type') == 'door':
                wall['open'] = not wall.get('open', False)
                break
    
    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True})

@app.route('/api/map/walls/clear', methods=['POST'])
@gm_required
def clear_all_walls():
    """Clear all walls from the map."""
    with MAP_LOCK:
        ACTIVE_MAP['walls'] = []
    
    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True})

@app.route('/api/map/border', methods=['POST'])
@gm_required
def border_map():
    """Create invisible walls around the entire map border."""
    if not ACTIVE_MAP.get('image'):
        return jsonify({'success': False, 'error': 'No map loaded'}), 400
    
    # Get map image dimensions
    map_path = os.path.join(MAP_DIR, ACTIVE_MAP['image'])
    if not os.path.exists(map_path):
        return jsonify({'success': False, 'error': 'Map file not found'}), 400
    
    # Use PIL to get dimensions
    try:
        from PIL import Image
        with Image.open(map_path) as img:
            width, height = img.size
    except:
        # Fallback: estimate from grid
        width = 2000
        height = 2000
    
    # Create border walls (invisible type - blocks movement only)
    border_wall = {
        'id': 'border-' + str(uuid.uuid4())[:8],
        'points': [
            [0, 0],
            [width, 0],
            [width, height],
            [0, height]
        ],
        'type': 'invisible',
        'closed': True,
        'open': False,
    }
    
    with MAP_LOCK:
        # Remove any existing border walls
        ACTIVE_MAP['walls'] = [w for w in ACTIVE_MAP.get('walls', []) if not w['id'].startswith('border-')]
        ACTIVE_MAP['walls'].append(border_wall)
    
    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True, 'width': width, 'height': height})

# --- EXPLORED FOG (Grid-based) ---

@app.route('/api/map/explored', methods=['POST'])
def update_explored():
    """Update explored grid cells.

    Player-facing: player clients auto-accumulate cells their token has seen
    and POST them here. We UNION into the existing set rather than replacing,
    so two players walking around in parallel don't clobber each other's
    memory. GM can replace wholesale by passing `replace: true`.
    """
    data = request.json or {}
    # Accept 'cells' (new player contract) and 'explored' (legacy GM contract)
    cells = data.get('cells') or data.get('explored') or []
    replace = bool(data.get('replace', False)) and session.get('gm_authenticated', False)

    with MAP_LOCK:
        if replace:
            ACTIVE_MAP['explored'] = list(cells)
        else:
            current = set(ACTIVE_MAP.get('explored') or [])
            current.update(cells)
            ACTIVE_MAP['explored'] = list(current)

    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'total': len(ACTIVE_MAP['explored'])})

@app.route('/api/map/explored/clear', methods=['POST'])
@gm_required
def clear_explored():
    """Clear all explored areas (reset fog)."""
    with MAP_LOCK:
        ACTIVE_MAP['explored'] = []
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})

# --- PING ---

@app.route('/api/map/ping', methods=['POST'])
def map_ping():
    """Broadcast a ping to all players."""
    data = request.json or {}
    x = data.get('x', 0)
    y = data.get('y', 0)
    player = data.get('player', 'Unknown')
    
    # Broadcast ping event to all clients
    _broadcast_event('ping', {'x': x, 'y': y, 'player': player})
    return jsonify({'success': True})

@app.route('/api/map/roll', methods=['POST'])
def broadcast_roll():
    """Broadcast a dice roll to all clients (especially GM)."""
    data = request.json or {}
    
    roll_data = {
        'player': data.get('player', 'Unknown'),
        'dice': data.get('dice', 'd20'),
        'result': data.get('result') or data.get('roll'),
        'total': data.get('total'),
        'bonus': data.get('bonus'),
        'attack': data.get('attack'),
        'damage': data.get('damage'),
        'crit': data.get('crit', False),
        'fumble': data.get('fumble', False),
        'time': time.strftime('%H:%M:%S')
    }
    
    sse_broadcast('dice_roll', roll_data)
    return jsonify({'success': True})

# --- CHARACTER API ---

@app.route('/api/character/<name>')
def get_character_api(n):
    """Get character data by name."""
    # Check party library
    for pc in PARTY_LIBRARY.values():
        if pc.name.lower() == n.lower():
            return jsonify({
                'success': True,
                'character': {
                    'name': pc.name,
                    'hp': pc.hp,
                    'current_hp': pc.current_hp,
                    'ac': pc.ac,
                    'speed': getattr(pc, 'speed', 25),
                    'perception': pc.perception if hasattr(pc, 'perception') else 10,
                    'level': pc.level if hasattr(pc, 'level') else 1,
                }
            })
    
    return jsonify({'success': False, 'error': 'Character not found'})

@app.route('/api/creature/<name>')
def get_creature_api(n):
    """Get full creature data by name (from encounter or monster library)."""
    name = n
    # Check active encounter
    for c in ACTIVE_ENCOUNTER:
        if c.name.lower() == name.lower() or (hasattr(c, 'instance_id') and c.instance_id == name):
            return jsonify({
                'success': True,
                'creature': {
                    'name': c.name,
                    'level': getattr(c, 'level', 0),
                    'hp': c.hp,
                    'current_hp': c.current_hp,
                    'ac': c.ac if hasattr(c, 'ac') else 10,
                    'speed': getattr(c, 'speed', 25),
                    'perception': c.base_perception if hasattr(c, 'base_perception') else 0,
                    'fort': c.base_fort if hasattr(c, 'base_fort') else 0,
                    'ref': c.base_ref if hasattr(c, 'base_ref') else 0,
                    'will': c.base_will if hasattr(c, 'base_will') else 0,
                    'strikes': getattr(c, 'strikes', []),
                    'actions': getattr(c, 'actions', []),
                    'immunities': getattr(c, 'immunities', []),
                    'resistances': getattr(c, 'resistances', []),
                    'weaknesses': getattr(c, 'weaknesses', []),
                    'traits': getattr(c, 'traits', []),
                    'conditions': {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False} if hasattr(c, 'conditions') else {},
                    'is_pc': getattr(c, 'is_pc', False),
                }
            })
    
    # Check party library
    for pc in PARTY_LIBRARY.values():
        if pc.name.lower() == name.lower():
            return jsonify({
                'success': True,
                'creature': {
                    'name': pc.name,
                    'level': pc.level if hasattr(pc, 'level') else 1,
                    'hp': pc.hp,
                    'current_hp': pc.current_hp,
                    'ac': pc.ac,
                    'speed': getattr(pc, 'speed', 25),
                    'perception': pc.perception if hasattr(pc, 'perception') else 10,
                    'fort': pc.fort if hasattr(pc, 'fort') else 0,
                    'ref': pc.ref if hasattr(pc, 'ref') else 0,
                    'will': pc.will if hasattr(pc, 'will') else 0,
                    'strikes': [],  # PC strikes would need different handling
                    'actions': [],
                    'immunities': [],
                    'resistances': [],
                    'weaknesses': [],
                    'traits': [],
                    'conditions': {},
                    'is_pc': True,
                }
            })
    
    return jsonify({'success': False, 'error': 'Creature not found'})

# --- FOG OF WAR (Legacy) ---

@app.route('/api/map/fog/reveal', methods=['POST'])
@gm_required
def reveal_fog():
    """Reveal an area of the map (add to revealed regions)."""
    data = request.json or {}
    region = {
        'id': str(uuid.uuid4())[:8],
        'type': data.get('type', 'rect'),  # rect, circle, polygon
        'x': int(data.get('x', 0)),
        'y': int(data.get('y', 0)),
        'w': int(data.get('w', 1)),
        'h': int(data.get('h', 1)),
        'r': int(data.get('r', 0)),  # For circles
        'points': data.get('points', []),  # For polygons
        'revealed': True,
    }
    
    with MAP_LOCK:
        ACTIVE_MAP['fog'].append(region)
    
    _save_map_state()
    _broadcast_map_fog()
    return jsonify({'success': True, 'region': region})

@app.route('/api/map/fog/hide', methods=['POST'])
@gm_required
def hide_fog():
    """Hide an area (remove from revealed regions or add hidden region)."""
    data = request.json or {}
    region_id = data.get('id')
    
    if region_id:
        # Remove specific region
        with MAP_LOCK:
            ACTIVE_MAP['fog'] = [r for r in ACTIVE_MAP['fog'] if r['id'] != region_id]
    else:
        # Add a hidden region
        region = {
            'id': str(uuid.uuid4())[:8],
            'type': data.get('type', 'rect'),
            'x': int(data.get('x', 0)),
            'y': int(data.get('y', 0)),
            'w': int(data.get('w', 1)),
            'h': int(data.get('h', 1)),
            'revealed': False,
        }
        with MAP_LOCK:
            ACTIVE_MAP['fog'].append(region)
    
    _save_map_state()
    _broadcast_map_fog()
    return jsonify({'success': True})

@app.route('/api/map/fog/reset', methods=['POST'])
@gm_required
def reset_fog():
    """Reset all fog (either reveal all or hide all)."""
    data = request.json or {}
    mode = data.get('mode', 'hide_all')  # 'hide_all' or 'reveal_all'
    
    with MAP_LOCK:
        if mode == 'reveal_all':
            ACTIVE_MAP['fog'] = [{'id': 'all', 'type': 'all', 'revealed': True}]
        else:
            ACTIVE_MAP['fog'] = []
    
    _save_map_state()
    _broadcast_map_fog()
    return jsonify({'success': True})

# --- WALL MANAGEMENT ---

def _broadcast_map_walls():
    """Broadcast wall state to all clients."""
    with MAP_LOCK:
        walls = ACTIVE_MAP.get('walls', [])
    sse_broadcast('map_walls', {'walls': walls})

@app.route('/api/map/wall/add', methods=['POST'])
@gm_required
def add_wall():
    """Add a wall segment to the map."""
    data = request.json or {}
    points = data.get('points', [])
    
    if len(points) < 2:
        return jsonify({'success': False, 'error': 'Wall needs at least 2 points'}), 400
    
    wall = {
        'id': str(uuid.uuid4())[:8],
        'points': points,  # [[x1,y1], [x2,y2], ...] in pixel coordinates
        'type': data.get('type', 'normal'),  # 'normal', 'terrain', 'invisible', 'ethereal', 'door'
        'open': False,  # For doors
        'closed': data.get('closed', False),  # Whether the wall forms a closed shape
        # Secret door fields — if secret=True, the door masquerades as a normal
        # wall to any player not listed in discovered_by. GM always sees it with
        # the secret styling so they can plan around it.
        'secret': bool(data.get('secret', False)) and data.get('type') == 'door',
        'hidden_dc': int(data.get('hidden_dc', 0) or 0),
        'discovered_by': [],
    }

    with MAP_LOCK:
        if 'walls' not in ACTIVE_MAP:
            ACTIVE_MAP['walls'] = []
        ACTIVE_MAP['walls'].append(wall)

    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True, 'wall': wall})

@app.route('/api/map/wall/remove', methods=['POST'])
@gm_required
def remove_wall():
    """Remove a wall from the map."""
    data = request.json or {}
    wall_id = data.get('id')
    
    with MAP_LOCK:
        ACTIVE_MAP['walls'] = [w for w in ACTIVE_MAP.get('walls', []) if w['id'] != wall_id]
    
    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True})

@app.route('/api/map/wall/hidden_side', methods=['POST'])
@gm_required
def set_wall_hidden_side():
    """Set which side of a wall is hidden from players."""
    data = request.json or {}
    wall_id = data.get('id')
    hidden_side = data.get('hidden_side', 'none')  # 'none', 'left', 'right'
    
    with MAP_LOCK:
        for wall in ACTIVE_MAP.get('walls', []):
            if wall['id'] == wall_id:
                wall['hidden_side'] = hidden_side
                break
    
    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True})

@app.route('/api/map/wall/clear', methods=['POST'])
@gm_required
def clear_walls():
    """Clear all walls from the map."""
    with MAP_LOCK:
        ACTIVE_MAP['walls'] = []

    _save_map_state()
    _broadcast_map_walls()
    return jsonify({'success': True})

@app.route('/api/map/wall/set_secret', methods=['POST'])
@gm_required
def set_wall_secret():
    """GM-only: toggle secret flag and hidden DC on a door wall.

    Doors flipped to secret vanish for any player not in discovered_by — they
    still block vision, but the player sees only what looks like a normal
    wall until somebody Seeks and passes the DC.
    """
    data = request.json or {}
    wall_id = data.get('id')
    secret = bool(data.get('secret', False))
    hidden_dc = int(data.get('hidden_dc', 0) or 0)

    with MAP_LOCK:
        for wall in ACTIVE_MAP.get('walls', []):
            if wall['id'] == wall_id and wall.get('type') == 'door':
                wall['secret'] = secret
                wall['hidden_dc'] = hidden_dc
                # Clear discovered list on any change — re-reveal required
                wall['discovered_by'] = [] if secret else wall.get('discovered_by', [])
                break

    _save_map_state()
    _broadcast_map_walls()
    # Player views depend on filtered state, so broadcast full state
    _broadcast_map_state()
    return jsonify({'success': True})


@app.route('/api/map/wall/seek', methods=['POST'])
def seek_wall():
    """Roll a token's Perception vs a secret door's hidden DC.

    Called when the GM clicks a secret door and picks which PC is seeking,
    or (future) when a player-side Seek action targets an adjacency. On
    success the seeking player is added to discovered_by and the door
    becomes visible to them.
    """
    data = request.json or {}
    wall_id = data.get('id')
    token_id = data.get('token_id')

    if not wall_id or not token_id:
        return jsonify({'success': False, 'error': 'wall id and token id required'}), 400

    with MAP_LOCK:
        wall = next((w for w in ACTIVE_MAP.get('walls', []) if w['id'] == wall_id), None)
        token = next((t for t in ACTIVE_MAP.get('tokens', []) if t['id'] == token_id), None)

        if not wall:
            return jsonify({'success': False, 'error': 'wall not found'}), 404
        if not token:
            return jsonify({'success': False, 'error': 'token not found'}), 404
        if not wall.get('secret'):
            return jsonify({'success': False, 'error': 'not a secret door'}), 400

        # Perception modifier lives on the PC character, not the token; pull
        # from the live party library so we read the real sheet (with buffs
        # applied) rather than the map's cached token.
        pc_name = token.get('pc_name') or token.get('assigned_player') or token.get('name')
        perception_mod = 0
        if pc_name:
            for lib_name, pc in PARTY_LIBRARY.items():
                if lib_name == pc_name or pc.name == pc_name:
                    perception_mod = int(getattr(pc, 'perception', 0) or 0)
                    break
        # Fallback — use token-level perception if no PC sheet
        if perception_mod == 0 and isinstance(token.get('perception'), (int, float)):
            perception_mod = int(token['perception'])

        roll = random.randint(1, 20)
        total = roll + perception_mod
        dc = int(wall.get('hidden_dc') or 0)

        # PF2e degrees of success — nat 20 bumps up, nat 1 bumps down
        diff = total - dc
        if diff >= 10:
            degree = 'critical success'
        elif diff >= 0:
            degree = 'success'
        elif diff > -10:
            degree = 'failure'
        else:
            degree = 'critical failure'
        if roll == 20:
            degree = {'critical failure': 'failure', 'failure': 'success', 'success': 'critical success'}.get(degree, 'critical success')
        elif roll == 1:
            degree = {'critical success': 'success', 'success': 'failure', 'failure': 'critical failure'}.get(degree, 'critical failure')

        revealed = degree in ('success', 'critical success')
        discoverer = pc_name or token.get('name') or 'someone'

        if revealed:
            discovered = wall.get('discovered_by') or []
            if discoverer not in discovered:
                discovered.append(discoverer)
            wall['discovered_by'] = discovered

    _combat_log(f"{discoverer} Seeks a hidden door: 1d20 ({roll}) + {perception_mod} = {total} vs DC {dc} — {degree}",
                log_type='action')

    if revealed:
        _save_map_state()
        _broadcast_map_walls()
        _broadcast_map_state()

    return jsonify({
        'success': True,
        'revealed': revealed,
        'roll': roll,
        'modifier': perception_mod,
        'total': total,
        'dc': dc,
        'degree': degree,
        'discoverer': discoverer,
    })


# --- LIGHTING -------------------------------------------------------------
# Light sources illuminate the map for PC tokens. Each light has a bright
# radius (full illumination) and a dim radius (extends beyond bright —
# darkvision treats dim as bright; low-light vision treats dim as bright too
# but adds half the bright radius). Lights can be attached to a token so
# they follow it (torch in a backpack). Radii are in squares for sanity —
# the client multiplies by grid_size when raycasting.

LIGHT_PRESETS = {
    'candle':     {'bright': 1,  'dim': 1,  'color': '#ffd27a'},
    'torch':      {'bright': 4,  'dim': 4,  'color': '#ff9c42'},
    'lantern':    {'bright': 6,  'dim': 6,  'color': '#ffb866'},
    'bullseye':   {'bright': 12, 'dim': 2,  'color': '#fff1c0'},
    'daylight':   {'bright': 12, 'dim': 12, 'color': '#fff8e0'},
}

def _build_light(data):
    """Normalize a light payload — preset picks defaults, data overrides."""
    preset_key = (data.get('preset') or '').lower()
    preset = LIGHT_PRESETS.get(preset_key, {})
    return {
        'id': str(uuid.uuid4())[:8],
        'x': float(data.get('x', 0)),                  # pixel coords
        'y': float(data.get('y', 0)),
        'bright': int(data.get('bright', preset.get('bright', 4))),
        'dim': int(data.get('dim', preset.get('dim', 4))),
        'color': data.get('color', preset.get('color', '#ff9c42')),
        'enabled': bool(data.get('enabled', True)),
        'attached_to': data.get('attached_to'),        # token id or None
        'name': data.get('name', preset_key or 'Light'),
        'preset': preset_key or None,
    }


@app.route('/api/map/light/add', methods=['POST'])
@gm_required
def add_light():
    """Place a new light source."""
    data = request.json or {}
    light = _build_light(data)

    with MAP_LOCK:
        ACTIVE_MAP.setdefault('lights', []).append(light)

    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'light': light})


@app.route('/api/map/light/update', methods=['POST'])
@gm_required
def update_light():
    """Patch an existing light — pass only the fields you want to change."""
    data = request.json or {}
    light_id = data.get('id')
    if not light_id:
        return jsonify({'success': False, 'error': 'light id required'}), 400

    mutable = {'x', 'y', 'bright', 'dim', 'color', 'enabled', 'attached_to', 'name'}
    with MAP_LOCK:
        light = next((l for l in ACTIVE_MAP.get('lights', []) if l['id'] == light_id), None)
        if not light:
            return jsonify({'success': False, 'error': 'light not found'}), 404
        for k, v in data.items():
            if k in mutable:
                if k in ('bright', 'dim'):
                    light[k] = int(v)
                elif k in ('x', 'y'):
                    light[k] = float(v)
                elif k == 'enabled':
                    light[k] = bool(v)
                else:
                    light[k] = v

    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'light': light})


@app.route('/api/map/light/remove', methods=['POST'])
@gm_required
def remove_light():
    data = request.json or {}
    light_id = data.get('id')
    with MAP_LOCK:
        ACTIVE_MAP['lights'] = [l for l in ACTIVE_MAP.get('lights', []) if l['id'] != light_id]
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})


@app.route('/api/map/light/clear', methods=['POST'])
@gm_required
def clear_lights():
    with MAP_LOCK:
        ACTIVE_MAP['lights'] = []
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})


@app.route('/api/map/ambient', methods=['POST'])
@gm_required
def set_ambient_light():
    """Switch the map between bright/dim/dark ambient lighting.

    Named `/api/map/ambient` rather than `/api/map/vision_mode` because
    `vision_mode` already exists on ACTIVE_MAP with different semantics
    (fog reveal policy, e.g. 'explored'). Keeping the names distinct
    prevents the two from being confused on the wire.
    """
    data = request.json or {}
    mode = data.get('mode', 'bright')
    if mode not in ('bright', 'dim', 'dark'):
        return jsonify({'success': False, 'error': 'invalid mode'}), 400
    with MAP_LOCK:
        ACTIVE_MAP['ambient_light'] = mode
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'mode': mode})


@app.route('/api/map/light/presets')
def light_presets():
    """Advertised presets for the GM's light-placement dropdown."""
    return jsonify({'presets': LIGHT_PRESETS})


# --- TEMPLATES (AOE) ------------------------------------------------------
# Templates represent spell/ability areas of effect. The client is
# authoritative about rendering — the server just stores pixel coords,
# dimensions (in squares), and metadata. Players can create templates too
# (ability → template flow), but only the creator or a GM can remove them.

TEMPLATE_COLORS = {
    'fire':     '#ef4444',
    'cold':     '#60a5fa',
    'acid':     '#84cc16',
    'electric': '#fde047',
    'sonic':    '#c4b5fd',
    'positive': '#fef3c7',
    'negative': '#8b5cf6',
    'force':    '#a78bfa',
    'generic':  '#8ED4D4',
}

def _build_template(data):
    """Normalize a template payload. Fills defaults based on type."""
    ttype = data.get('type', 'burst')
    if ttype not in ('burst', 'emanation', 'cone', 'line'):
        ttype = 'burst'
    color = data.get('color') or TEMPLATE_COLORS.get(data.get('damage_type', 'generic'), '#8ED4D4')
    return {
        'id': str(uuid.uuid4())[:8],
        'type': ttype,
        'x': float(data.get('x', 0)),
        'y': float(data.get('y', 0)),
        'attached_to': data.get('attached_to'),
        'radius': int(data.get('radius', 2)),       # squares — burst/emanation
        'length': int(data.get('length', 4)),       # squares — cone/line
        'width': int(data.get('width', 1)),         # squares — line (thickness)
        'direction': float(data.get('direction', 0)),  # deg; 0=east, 90=south
        'angle': float(data.get('angle', 90)),      # deg; PF2e cones default 90
        'color': color,
        'name': data.get('name', ''),
        'owner': data.get('owner', ''),             # player or 'GM'
        'source': data.get('source', ''),           # e.g. 'Fireball'
        'temporary': bool(data.get('temporary', False)),
        'created_round': ROUND_NUMBER,
    }


@app.route('/api/map/template/add', methods=['POST'])
def add_template():
    """Create an AOE template. GM or player (players own their templates)."""
    data = request.json or {}
    # Treat no-password local dev as GM (matches gm_required behavior).
    is_gm = _is_gm()
    player_name = session.get('player_name')
    if not is_gm and not player_name:
        return jsonify({'success': False, 'error': 'login required'}), 401
    if 'owner' not in data:
        data['owner'] = 'GM' if is_gm else player_name
    tmpl = _build_template(data)
    with MAP_LOCK:
        ACTIVE_MAP.setdefault('templates', []).append(tmpl)
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'template': tmpl})


@app.route('/api/map/template/update', methods=['POST'])
def update_template():
    """Patch a template. GM or the owning player only."""
    data = request.json or {}
    tid = data.get('id')
    if not tid:
        return jsonify({'success': False, 'error': 'template id required'}), 400
    is_gm = _is_gm()
    player_name = session.get('player_name')
    mutable = {'x', 'y', 'attached_to', 'radius', 'length', 'width',
               'direction', 'angle', 'color', 'name', 'source', 'temporary'}
    with MAP_LOCK:
        tmpl = next((t for t in ACTIVE_MAP.get('templates', []) if t['id'] == tid), None)
        if not tmpl:
            return jsonify({'success': False, 'error': 'template not found'}), 404
        if not is_gm and tmpl.get('owner') != player_name:
            return jsonify({'success': False, 'error': 'not owner'}), 403
        for k, v in data.items():
            if k not in mutable:
                continue
            if k in ('radius', 'length', 'width'):
                tmpl[k] = int(v)
            elif k in ('x', 'y', 'direction', 'angle'):
                tmpl[k] = float(v)
            elif k == 'temporary':
                tmpl[k] = bool(v)
            else:
                tmpl[k] = v
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True, 'template': tmpl})


@app.route('/api/map/template/remove', methods=['POST'])
def remove_template():
    """Remove a template. GM or owner only."""
    data = request.json or {}
    tid = data.get('id')
    is_gm = _is_gm()
    player_name = session.get('player_name')
    with MAP_LOCK:
        tmpl = next((t for t in ACTIVE_MAP.get('templates', []) if t['id'] == tid), None)
        if not tmpl:
            return jsonify({'success': True})
        if not is_gm and tmpl.get('owner') != player_name:
            return jsonify({'success': False, 'error': 'not owner'}), 403
        ACTIVE_MAP['templates'] = [t for t in ACTIVE_MAP.get('templates', []) if t['id'] != tid]
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})


@app.route('/api/map/template/clear', methods=['POST'])
@gm_required
def clear_templates():
    with MAP_LOCK:
        ACTIVE_MAP['templates'] = []
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})


# --- GM-ONLY PINS / NOTES LAYER -------------------------------------------
# Notes live in ACTIVE_MAP['gm_notes']. They never leave get_map_state() for
# non-GM viewers. Kept separate from walls/tokens so rendering order is simple.

@app.route('/api/map/note/add', methods=['POST'])
@gm_required
def add_gm_note():
    data = request.json or {}
    try:
        x = int(data.get('x', 0))
        y = int(data.get('y', 0))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'invalid coords'}), 400
    note = {
        'id': str(uuid.uuid4())[:8],
        'x': x,
        'y': y,
        'text': str(data.get('text', '') or '')[:500],
        'color': str(data.get('color', '#fbbf24'))[:16],
        'icon': str(data.get('icon', '📌'))[:4],
    }
    with MAP_LOCK:
        ACTIVE_MAP.setdefault('gm_notes', []).append(note)
    _save_map_state()
    # Notes are GM-only — a dedicated SSE event keeps players from re-rendering
    try:
        sse_broadcast('map_notes', {'notes': list(ACTIVE_MAP.get('gm_notes', []))})
    except Exception:
        pass
    return jsonify({'success': True, 'note': note})


@app.route('/api/map/note/update', methods=['POST'])
@gm_required
def update_gm_note():
    data = request.json or {}
    note_id = data.get('id')
    with MAP_LOCK:
        for n in ACTIVE_MAP.get('gm_notes', []):
            if n['id'] == note_id:
                if 'text' in data: n['text'] = str(data['text'] or '')[:500]
                if 'color' in data: n['color'] = str(data['color'])[:16]
                if 'icon' in data: n['icon'] = str(data['icon'])[:4]
                if 'x' in data:
                    try: n['x'] = int(data['x'])
                    except (TypeError, ValueError): pass
                if 'y' in data:
                    try: n['y'] = int(data['y'])
                    except (TypeError, ValueError): pass
                break
    _save_map_state()
    try:
        sse_broadcast('map_notes', {'notes': list(ACTIVE_MAP.get('gm_notes', []))})
    except Exception:
        pass
    return jsonify({'success': True})


@app.route('/api/map/note/remove', methods=['POST'])
@gm_required
def remove_gm_note():
    data = request.json or {}
    note_id = data.get('id')
    with MAP_LOCK:
        ACTIVE_MAP['gm_notes'] = [n for n in ACTIVE_MAP.get('gm_notes', []) if n['id'] != note_id]
    _save_map_state()
    try:
        sse_broadcast('map_notes', {'notes': list(ACTIVE_MAP.get('gm_notes', []))})
    except Exception:
        pass
    return jsonify({'success': True})


@app.route('/api/map/state')
def get_map_state():
    """Get current map state (filtered for players).

    Player clients refetch this on every SSE tick, so it's the scrubber
    between raw ACTIVE_MAP and the wire. Keep all player-side filtering in
    _apply_player_map_filter so /map bootstrap and this refetch can't drift.
    """
    is_gm = _is_gm()
    player_name = session.get('player_name')

    with MAP_LOCK:
        if is_gm:
            state = dict(ACTIVE_MAP)
        else:
            # deepcopy so the filter can mutate freely without touching the shared ref
            state = copy.deepcopy(ACTIVE_MAP)
            _apply_player_map_filter(state, player_name)

    return jsonify(state)

@app.route('/api/map/clear', methods=['POST'])
@gm_required
def clear_map():
    """Clear the current map. Keeps schema in sync with the module-level
    ACTIVE_MAP declaration so downstream code doesn't hit KeyErrors on the
    fields (gm_notes, lights, explored) that were added later."""
    global ACTIVE_MAP
    with MAP_LOCK:
        ACTIVE_MAP = {
            'id': None,
            'name': None,
            'image': None,
            'grid_size': 70,
            'grid_offset_x': 0,
            'grid_offset_y': 0,
            'tokens': [],
            'walls': [],
            'explored': [],
            'difficult_terrain': [],
            'spawn_point': None,
            'player_control': True,
            'gm_notes': [],
            'ambient_light': 'bright',
            'lights': [],
            'templates': [],
        }
    _broadcast_map_state()
    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    # Launch the debounced persistence flush thread. With Flask debug reloader,
    # _start_persistence_thread() becomes a no-op in the parent process because
    # the daemon thread is tied to the child; the WERKZEUG_RUN_MAIN check keeps
    # us from starting it twice.
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        _start_persistence_thread()
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)