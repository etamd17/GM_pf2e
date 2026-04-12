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
    'walls': [],  # [{id, points: [[x,y],...], type: 'normal'|'terrain'|'invisible'|'ethereal'|'door', closed: bool, open: bool}]
    'explored': [],  # List of "x,y" strings for explored grid cells
    'difficult_terrain': [],  # [{x, y}] grid cells with difficult terrain
    'spawn_point': None,  # {x, y} grid position for party spawn
    'player_control': True,  # Can players move their own tokens?
}
MAP_LOCK = threading.Lock()

def _combat_log(msg, log_type='action'):
    """Append a timestamped entry to the combat log."""
    COMBAT_LOGS.append({
        'id': str(uuid.uuid4())[:8],
        'time': time.strftime('%H:%M:%S'),
        'round': ROUND_NUMBER,
        'msg': msg,
        'type': log_type
    })

def _persist_encounter_state():
    """Auto-save the active encounter to disk so it survives restarts."""
    if not ACTIVE_ENCOUNTER:
        # Remove autosave file if encounter is empty
        autosave_path = os.path.join(ENCOUNTER_DIR, '_autosave.json')
        if os.path.exists(autosave_path):
            os.remove(autosave_path)
        return
    try:
        os.makedirs(ENCOUNTER_DIR, exist_ok=True)
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
                'delaying': getattr(c, 'delaying', False),
            }
            encounter_data['combatants'].append(entry)
        with open(os.path.join(ENCOUNTER_DIR, '_autosave.json'), 'w', encoding='utf-8') as f:
            json.dump(encounter_data, f, indent=2)
    except Exception as e:
        print(f"[ENCOUNTER PERSIST ERROR] {e}")

# --- SERVER-SENT EVENTS (SSE) FOR REAL-TIME SYNC ---
_sse_subscribers = []  # List of queue.Queue objects, one per connected client
_sse_lock = threading.Lock()
_sse_last_cleanup = time.time()
_SSE_MAX_SUBSCRIBERS = 50  # Hard cap to prevent memory leaks
_SSE_STALE_TIMEOUT = 120  # Seconds before a non-consuming queue is considered stale

def sse_broadcast(event_type, data):
    """Push an event to all connected SSE clients."""
    global _sse_last_cleanup
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)
        
        # Periodic stale subscriber cleanup (every 60 seconds)
        now = time.time()
        if now - _sse_last_cleanup > 60:
            _sse_last_cleanup = now
            # Remove queues that are nearly full (stale clients)
            stale = [q for q in _sse_subscribers if q.qsize() > 40]
            for q in stale:
                _sse_subscribers.remove(q)
            if _sse_subscribers or stale:
                print(f"[SSE] Active: {len(_sse_subscribers)}, Cleaned: {len(stale)}")

def sse_subscriber_count():
    """Return the number of active SSE subscribers."""
    with _sse_lock:
        return len(_sse_subscribers)

def _broadcast_pc_state(pc_name):
    """Broadcast a PC's current state to all SSE clients."""
    if pc_name in PARTY_LIBRARY:
        pc = PARTY_LIBRARY[pc_name]
        pct = pc.current_hp / pc.hp if pc.hp > 0 else 0
        # Build spell slot summary for GM visibility
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
        sse_broadcast('pc_update', {
            'name': pc_name,
            'current_hp': pc.current_hp,
            'max_hp': pc.hp,
            'hp_pct': round(pct * 100),
            'conditions': {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
            'focus': getattr(pc, 'current_focus', 0),
            'hero_points': getattr(pc, 'hero_points', 1),
            'spell_casters': spell_summary,
            'expended_slots': expended_slots,
        })

def _broadcast_encounter_state():
    """Broadcast the full encounter state to all SSE clients."""
    active_name = ACTIVE_ENCOUNTER[TURN_INDEX].name if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
    combatants = []
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        entry = {'name': c.name, 'is_pc': c.is_pc, 'initiative': c.initiative, 'is_active': (i == TURN_INDEX)}
        if c.is_pc:
            pct = c.current_hp / c.hp if c.hp > 0 else 0
            entry['current_hp'] = c.current_hp
            entry['max_hp'] = c.hp
            entry['hp_pct'] = round(pct * 100)
        else:
            pct = c.current_hp / c.hp if c.hp > 0 else 0
            if c.current_hp == 0: entry['hp_status'] = 'Dead'
            elif pct <= 0.5: entry['hp_status'] = 'Wounded'
            else: entry['hp_status'] = ''
        entry['conditions'] = {k: v for k, v in c.conditions.items() if v and v != 0 and v is not False}
        combatants.append(entry)
    sse_broadcast('encounter_update', {'encounter': combatants, 'round': ROUND_NUMBER, 'active_name': active_name, 'turn_index': TURN_INDEX})

COMPENDIUM_LIBRARY = {} 
COMPENDIUM_RULES = {} 
BUILDER_ANCESTRIES = {}
BUILDER_BACKGROUNDS = {}
BUILDER_CLASSES = {}
BUILDER_FEATS = { 'class': [], 'skill': [], 'general': [], 'ancestry': [] }
BUILDER_SPELLS = []
BUILDER_WEAPONS = []
BUILDER_ARMOR = []

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
VALID_GENERATOR_TYPES = {'npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter'}

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
        "orc": [{"name": "Badlands", "desc": "You gain fire resistance equal to half your level (minimum 1)."}, {"name": "Deep", "desc": "You gain darkvision."}, {"name": "Hold-Scarred", "desc": "You gain 12 Hit Points from your ancestry instead of 10, and gain the Diehard feat."}, {"name": "Rainfall", "desc": "You gain a +2 circumstance bonus to saving throws against diseases."}, {"name": "Winter", "desc": "You gain cold resistance equal to half your level (minimum 1)."}]
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
    """Lightweight persistence of HP, conditions, and focus to disk without full reload.
    Used by adjust_party_hp and adjust_focus to survive server restarts."""
    if pc_name not in PARTY_LIBRARY:
        return
    pc = PARTY_LIBRARY[pc_name]
    file_path = get_pc_file_path(pc_name)
    if not file_path or not os.path.exists(file_path):
        return
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        build['current_hp'] = pc.current_hp
        build['current_focus'] = getattr(pc, 'current_focus', 0)
        build['conditions'] = {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False}
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(pc_json, f, indent=2)
    except Exception as e:
        print(f"[PERSIST ERROR] {pc_name}: {e}")

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
        self.active_toggles = build.get('active_toggles') or []
        self.shield_raised = build.get('shield_raised', False)
        self.shield_ac_bonus = safe_int(build.get('shield_ac_bonus'), 2)  # Most shields = +2
        self.signature_spells = build.get('signature_spells') or []
        self.session_notes = build.get('session_notes') or []
        self.expended_slots = build.get('expended_slots') or {}
        
        self.raw_feats = build.get('feats') or []
        self.raw_spellCasters = build.get('spellCasters') or []
        self.monk_paths = build.get('monk_paths', {})
        self.half_boosts = build.get('half_boosts') or []
        
        self.abilities = build.get('abilities') or {}
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

        # Parse Pathbuilder lores array into proficiencies
        for lore_entry in (build.get('lores') or []):
            if isinstance(lore_entry, (list, tuple)) and len(lore_entry) >= 2:
                lore_name = str(lore_entry[0]).lower().strip()
                lore_rank = safe_int(lore_entry[1], 2)
                key = f"lore:{lore_name}"
                if key not in self.proficiencies:
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
        
        # Compute AC proficiency from best armor proficiency the character actually uses
        armor_name = build.get('armor_name', '')
        if armor_name:
            # Determine which armor category is equipped
            armor_cat = 'unarmored'
            for a in BUILDER_ARMOR:
                if a.get('name', '').lower() == armor_name.lower():
                    cat = a.get('category', 'unarmored').lower()
                    if cat in ('light', 'medium', 'heavy'): armor_cat = cat
                    break
            self.proficiencies['ac'] = max(safe_int(self.proficiencies.get('ac', 0)), self._class_profs.get(armor_cat, 2))
        else:
            self.proficiencies['ac'] = max(safe_int(self.proficiencies.get('ac', 0)), self._class_profs.get('unarmored', 2))
        
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
                try: return int(v)
                except: return 0
            if isinstance(v, dict) and 'brackets' in v:
                for b in v['brackets']:
                    if b.get('start', 1) <= self.level <= b.get('end', 20):
                        return resolve_val(b.get('value', 0))
            return 0

        sources = [self.ancestry, self.heritage, self.class_name, self.subclass, self.background]
        for f in self.raw_feats:
            if isinstance(f, list) and len(f) > 0: sources.append(f[0])
            elif isinstance(f, dict): sources.append(f.get('name', ''))
        for eq in (build.get('equipment') or []):
            if isinstance(eq, dict): sources.append(eq.get('name', ''))
            elif isinstance(eq, list) and len(eq) > 0: sources.append(eq[0])
        for w in (build.get('weapons') or []):
            if isinstance(w, dict): sources.append(w.get('name', ''))
        sources.append(build.get('armor_name', ''))

        for src in sources:
            if not src: continue
            r_list = COMPENDIUM_RULES.get(str(src).lower()) or []
            for rule in r_list:
                try:
                    if not isinstance(rule, dict): continue
                    key = rule.get('key', '')
                    if key == 'FlatModifier':
                        selectors = rule.get('selector', [])
                        if isinstance(selectors, str): selectors = [selectors]
                        val = resolve_val(rule.get('value', 0))
                        m_type = rule.get('type', 'untyped').lower()
                        for s in selectors: 
                            if s: add_mod(str(s).lower(), m_type, val)
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
                except Exception:
                    pass  # Don't let any single rule crash the entire character init

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

        self.armor_name = safe_str(build.get('armor_name'), '')
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
        
        if self.mods.get('str', 0) >= self.armor_str_req:
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
        self.temp_hp = self.toggle_effects_summary.get('temp_hp', 0)
        
        # Tracker-compatibility aliases (tracker.html uses these on both PCs and Monsters)
        self.speed = self.active_speed
        self.strikes = []   # PCs use the 'attacks' property; strikes stays empty for template compat
        self.actions = []   # PCs don't have monster-style actions
        self.persistent_damage = safe_str(build.get('persistent_damage'), '')

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
                    c_info['levels'].append({'level': lvl, 'label': 'Cantrips' if lvl == 0 else f'Level {lvl}', 'slots': max_slots, 'spells': spells_at_lvl})
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
            res.append({'name': skill.title(), 'stat': stat.upper(), 'prof_val': prof_val, 'prof_letter': prof_letter, 'total': f"+{total_mod}" if total_mod >= 0 else str(total_mod), 'penalty': penalty})
            
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
            strikes = [
                {'label': fmt(total_hit), 'mod': total_hit},
                {'label': fmt(second_hit), 'mod': second_hit},
                {'label': fmt(third_hit), 'mod': third_hit}
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
            
            res.append({
                'name': w.get('name'), 
                'strikes': strikes, 
                'damage': dmg_str, 
                'traits': traits,
                'has_two_hand': has_two_hand_trait,
                'is_two_handed': is_two_handed,
                'crit_effects': " | ".join(crit_effects)
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
                        COMPENDIUM_RULES[name.lower()] = safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or []
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
                        COMPENDIUM_RULES[name.lower()] = safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or []
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
                        COMPENDIUM_RULES[name.lower()] = safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or []
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
                        COMPENDIUM_RULES[name.lower()] = safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or []
                        
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
                        COMPENDIUM_RULES[name.lower()] = feat_rules
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
                        COMPENDIUM_RULES[name.lower()] = safe_json_load(r, 'rule_elements', []) or sys_data.get('rules') or []
                        
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
                                COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
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
                            COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
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
                                COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
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
                                COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
                                
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
                                COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
                                
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
                                    COMPENDIUM_RULES[name.lower()] = sys.get('rules') or []
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
                if 'persistent_damage' in item: new_c.persistent_damage = item['persistent_damage']
                if 'delaying' in item: new_c.delaying = item['delaying']
                if 'elite_weak' in item and hasattr(new_c, 'apply_elite_weak'):
                    new_c.apply_elite_weak(item['elite_weak'])
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
            <style>body{font-family:'Inter',system-ui,sans-serif;background:#0f0f14;color:#e8e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
            .box{background:#1e1e2a;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:40px;max-width:340px;width:100%;text-align:center;}
            h1{font-family:'Cinzel',serif;color:#ef4444;font-size:16px;margin-bottom:8px;}
            p{color:#8080a0;font-size:13px;}
            input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);background:#0f0f14;color:#e8e8f0;font-size:14px;margin:16px 0;box-sizing:border-box;font-family:'Inter',sans-serif;}
            input:focus{outline:none;border-color:rgba(94,173,173,0.3);}
            button{width:100%;padding:10px;border-radius:6px;border:none;background:#3A7878;color:#A8DEDE;font-family:'Inter',sans-serif;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.2s;}
            button:hover{background:#4A9696;}
            </style></head>
            <body><div class="box"><h1>Wrong Password</h1><p>Try again.</p>
            <form method="POST"><input type="password" name="password" placeholder="GM Password" autofocus>
            <button type="submit">Sign In</button></form></div></body></html>'''
    return '''<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
        <title>GM Login</title><link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Cinzel:wght@600&display=swap" rel="stylesheet">
        <style>body{font-family:'Inter',system-ui,sans-serif;background:#0f0f14;color:#e8e8f0;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;}
        .box{background:#1e1e2a;border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:40px;max-width:340px;width:100%;text-align:center;}
        h1{font-family:'Cinzel',serif;color:#7DC4C4;font-size:18px;margin-bottom:4px;}
        p{color:#8080a0;font-size:13px;margin-bottom:20px;}
        input{width:100%;padding:10px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);background:#0f0f14;color:#e8e8f0;font-size:14px;margin-bottom:16px;box-sizing:border-box;font-family:'Inter',sans-serif;}
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
        body {{ font-family:'Inter',system-ui,sans-serif; background:#0f0f14; color:#e8e8f0; }}
        .font-display {{ font-family:'Cinzel',serif; }}
        .gm-card {{ background:#1e1e2a; border:1px solid rgba(255,255,255,0.06); border-radius:10px; padding:20px; transition:all 0.2s; }}
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
    """Build the full tracker state dict."""
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
            'persistent_damage': getattr(c, 'persistent_damage', ''),
            'elite_weak': getattr(c, 'elite_weak', 0),
            'delaying': getattr(c, 'delaying', False),
            'base_ac': getattr(c, 'base_ac', c.ac),
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
        combatants.append(entry)
    party_level = max([c.level for c in ACTIVE_ENCOUNTER if c.is_pc] or [p.level for p in PARTY_LIBRARY.values()] or [1])
    encounter_xp = calculate_encounter_xp(ACTIVE_ENCOUNTER, party_level)
    diff_label, diff_color = get_difficulty_label(encounter_xp)
    return {
        'combatants': combatants, 'round': ROUND_NUMBER, 'turn_index': TURN_INDEX,
        'active_name': active_name, 'encounter_xp': encounter_xp,
        'diff_label': diff_label, 'diff_color': diff_color, 'party_level': party_level,
    }

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
    """Return combat log entries."""
    return jsonify({"log": COMBAT_LOGS, "count": len(COMBAT_LOGS)})

@app.route('/api/combat_log/clear', methods=['POST'])
def clear_combat_log():
    """Clear the combat log."""
    COMBAT_LOGS.clear()
    return jsonify({"success": True})

@app.route('/api/adjust_hp/<instance_id>', methods=['POST'])
def adjust_hp(instance_id):
    try:
        amount = int(request.form.get('amount', 0))
        action = request.form.get('action')
        for c in ACTIVE_ENCOUNTER:
            if c.instance_id == instance_id:
                old_hp = c.current_hp
                if action == 'damage':
                    was_above_zero = c.current_hp > 0
                    c.current_hp = max(0, c.current_hp - amount)
                    _combat_log(f"{c.name} took {amount} damage ({old_hp}→{c.current_hp})", 'damage')
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
        if pc_name in PARTY_LIBRARY:
            pc = PARTY_LIBRARY[pc_name]
            if action == 'damage':
                was_above_zero = pc.current_hp > 0
                pc.current_hp = max(0, pc.current_hp - amount)
                if pc.current_hp == 0 and was_above_zero:
                    # Dropping to 0: gain dying 1 + wounded value
                    pc.conditions['dying'] = 1 + pc.conditions.get('wounded', 0)
                elif pc.current_hp == 0 and not was_above_zero:
                    # Already at 0, taking more damage: increase dying
                    pc.conditions['dying'] = pc.conditions.get('dying', 0) + 1
                # Check for death at dying 4
                if pc.conditions.get('dying', 0) >= 4:
                    pc.conditions['dying'] = 4  # Cap at 4 (dead)
            elif action == 'heal':
                was_dying = pc.conditions.get('dying', 0) > 0
                pc.current_hp = min(pc.hp, pc.current_hp + amount)
                if pc.current_hp > 0 and was_dying:
                    # Healed from dying: remove dying, gain wounded
                    pc.conditions['dying'] = 0
                    pc.conditions['wounded'] = pc.conditions.get('wounded', 0) + 1
            # Sync to encounter tracker
            for c in ACTIVE_ENCOUNTER:
                if c.is_pc and c.name == pc_name:
                    c.current_hp = pc.current_hp
                    c.conditions['dying'] = pc.conditions['dying']
                    c.conditions['wounded'] = pc.conditions['wounded']
            # Persist HP and conditions to disk
            _persist_pc_combat_state(pc_name)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json: 
                _broadcast_pc_state(pc_name)
                return jsonify({
                    "success": True, "current_hp": pc.current_hp,
                    "conditions": {k: v for k, v in pc.conditions.items() if v and v != 0 and v is not False},
                    "dying": pc.conditions.get('dying', 0),
                    "wounded": pc.conditions.get('wounded', 0),
                    "dead": pc.conditions.get('dying', 0) >= 4
                })
    except ValueError: pass
    return redirect(url_for('party_view'))

@app.route('/api/adjust_focus/<pc_name>', methods=['POST'])
def adjust_focus(pc_name):
    try:
        action = request.form.get('action')
        if pc_name in PARTY_LIBRARY:
            pc = PARTY_LIBRARY[pc_name]
            if action == 'increase' and pc.current_focus < pc.focus_max: pc.current_focus += 1
            elif action == 'decrease' and pc.current_focus > 0: pc.current_focus -= 1
            for c in ACTIVE_ENCOUNTER:
                if c.is_pc and c.name == pc_name: c.current_focus = pc.current_focus
            _persist_pc_combat_state(pc_name)
            return jsonify({"success": True, "current_focus": pc.current_focus})
    except ValueError: pass
    return jsonify({"success": False})

@app.route('/api/adjust_hero/<pc_name>', methods=['POST'])
def adjust_hero(pc_name):
    try:
        action = request.form.get('action')
        if pc_name in PARTY_LIBRARY:
            pc = PARTY_LIBRARY[pc_name]
            if action == 'increase' and pc.hero_points < 3: pc.hero_points += 1
            elif action == 'decrease' and pc.hero_points > 0: pc.hero_points -= 1
            
            file_path = get_pc_file_path(pc_name)
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
                build = pc_json.get('build', pc_json)
                build['hero_points'] = pc.hero_points
                with open(file_path, 'w', encoding='utf-8') as f: json.dump(pc_json, f, indent=4)
                
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
    
    # Clear persistent damage
    build['persistent_damage'] = ''
    
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
            build['persistent_damage'] = ''
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
    pc, file_path, err = require_pc(pc_name)
    if err: return err
    
    data = request.json or {}
    damage = safe_int(data.get('damage', 0))
    
    pc_json, file_path, err = require_pc_json(pc_name)
    if err: return err
    build = pc_json.get('build', pc_json)
    
    # Get shield stats from build or defaults
    shield_hp = safe_int(build.get('shield_hp'), 20)
    shield_max_hp = safe_int(build.get('shield_max_hp'), 20)
    shield_hardness = safe_int(build.get('shield_hardness'), 5)
    shield_bt = safe_int(build.get('shield_bt'), shield_max_hp // 2)  # Broken threshold = half max HP
    
    if shield_hp <= 0:
        return jsonify({"success": False, "error": "Shield is broken/destroyed"})
    if shield_hp <= shield_bt:
        return jsonify({"success": False, "error": "Shield is broken (below BT)"})
    
    # Shield Block: reduce damage by hardness, shield takes the rest
    blocked = min(damage, shield_hardness)
    damage_to_char = max(0, damage - shield_hardness)
    damage_to_shield = max(0, damage - shield_hardness)
    
    shield_hp = max(0, shield_hp - damage_to_shield)
    shield_broken = shield_hp <= shield_bt
    shield_destroyed = shield_hp <= 0
    
    # Apply reduced damage to character
    pc.current_hp = max(0, pc.current_hp - damage_to_char)
    
    # Save shield state
    build['shield_hp'] = shield_hp
    build['current_hp'] = pc.current_hp
    
    save_and_reload_character(pc_name, pc_json, file_path)
    _broadcast_pc_state(pc_name)
    
    status = "destroyed" if shield_destroyed else "broken" if shield_broken else "intact"
    _combat_log(f"{pc_name}: Shield Block! Blocked {blocked} dmg (Hardness {shield_hardness}). Shield took {damage_to_shield} ({status}). {damage_to_char} dmg to HP.", 'action')
    
    return jsonify({
        "success": True,
        "blocked": blocked,
        "damage_to_char": damage_to_char,
        "damage_to_shield": damage_to_shield,
        "shield_hp": shield_hp,
        "shield_max_hp": shield_max_hp,
        "shield_broken": shield_broken,
        "shield_destroyed": shield_destroyed,
        "current_hp": pc.current_hp
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
            c.persistent_damage = pd_val
            if c.is_pc and c.name in PARTY_LIBRARY: PARTY_LIBRARY[c.name].persistent_damage = c.persistent_damage
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
        # === END OF CURRENT TURN: auto-tick conditions ===
        current_c = ACTIVE_ENCOUNTER[TURN_INDEX]
        # Frightened decreases by 1 at end of turn (PF2E Core)
        if current_c.conditions.get('frightened', 0) > 0:
            current_c.conditions['frightened'] -= 1
            _combat_log(f"{current_c.name}: Frightened reduced to {current_c.conditions['frightened']}", 'condition')
            if current_c.is_pc and current_c.name in PARTY_LIBRARY: PARTY_LIBRARY[current_c.name].conditions['frightened'] = current_c.conditions['frightened']
        
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
        
        # Stunned: lose actions, then reduce stunned by the number lost (PF2E Core p.448)
        stunned_val = new_c.conditions.get('stunned', 0)
        if stunned_val > 0:
            actions_lost = min(stunned_val, 3)
            new_c.conditions['stunned'] = max(0, stunned_val - actions_lost)
            _combat_log(f"{new_c.name}: Lost {actions_lost} action(s) to Stunned. Stunned reduced to {new_c.conditions['stunned']}", 'condition')
            if new_c.is_pc and new_c.name in PARTY_LIBRARY: PARTY_LIBRARY[new_c.name].conditions['stunned'] = new_c.conditions['stunned']
        
        # Persistent damage auto-roll (start of turn, PF2E Core p.451)
        pd = getattr(new_c, 'persistent_damage', '')
        if pd:
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
                if new_c.is_pc and new_c.name in PARTY_LIBRARY:
                    PARTY_LIBRARY[new_c.name].current_hp = new_c.current_hp
                    _broadcast_pc_state(new_c.name)
                # Auto-roll DC 15 flat check to end persistent damage
                flat_roll = random.randint(1, 20)
                if flat_roll >= 15:
                    new_c.persistent_damage = ''
                    if new_c.is_pc and new_c.name in PARTY_LIBRARY: PARTY_LIBRARY[new_c.name].persistent_damage = ''
                    _combat_log(f"{new_c.name}: Flat check {flat_roll} >= 15 — persistent damage ends!", 'heal')
                else:
                    _combat_log(f"{new_c.name}: Flat check {flat_roll} < 15 — persistent damage continues", 'damage')
        
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
    
    # Persistent damage (happens at start of turn)
    if getattr(c, 'persistent_damage', ''):
        TURN_REMINDERS.append({
            'type': 'danger', 'icon': '🔥',
            'title': f'Persistent Damage: {c.persistent_damage}',
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
            'detail': f'−{drained_val} to Con-based checks. Max HP reduced by {drained_val} × level.',
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
            }
            encounter_data['combatants'].append(entry)
        with open(os.path.join(ENCOUNTER_DIR, f"{name}.json"), 'w', encoding='utf-8') as f:
            json.dump(encounter_data, f, indent=2)
    return redirect(url_for('tracker_view'))

@app.route('/api/load_encounter', methods=['POST'])
def load_encounter():
    global ACTIVE_ENCOUNTER, TURN_INDEX, ROUND_NUMBER
    name = request.form.get('encounter_name')
    enc_path = os.path.join(ENCOUNTER_DIR, f"{name}.json")
    if name and os.path.exists(enc_path):
        ACTIVE_ENCOUNTER.clear(); TURN_INDEX = 0; ROUND_NUMBER = 1
        with open(enc_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        
        # Support both old format (list) and new format (dict with metadata)
        if isinstance(raw, list):
            combatants = raw
        else:
            combatants = raw.get('combatants', raw)
            ROUND_NUMBER = raw.get('round', 1)
            TURN_INDEX = raw.get('turn_index', 0)
        
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
                if 'persistent_damage' in item: new_c.persistent_damage = item['persistent_damage']
                if 'elite_weak' in item and hasattr(new_c, 'apply_elite_weak'):
                    new_c.apply_elite_weak(item['elite_weak'])
                ACTIVE_ENCOUNTER.append(new_c)
        
        # Validate turn index
        if TURN_INDEX >= len(ACTIVE_ENCOUNTER): TURN_INDEX = 0
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
    """Search monster library by name for the encounter builder."""
    query = request.args.get('q', '').strip().lower()
    if not query or len(query) < 2:
        return jsonify({"results": []})
    results = []
    for path, m in MONSTER_LIBRARY.items():
        if query in m.name.lower():
            results.append({
                'name': m.name, 'level': m.level, 'path': path,
                'hp': m.hp, 'ac': m.base_ac,
                'immunities': m.immunities, 'resistances': m.resistances, 'weaknesses': m.weaknesses
            })
    results.sort(key=lambda r: (r['level'], r['name']))
    return jsonify({"results": results[:30]})

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
    data = {k: getattr(pf2e_gen, f'get_{k}')(level=party_level, biome="City") for k in ['npc', 'tavern', 'shop', 'loot', 'magic_item', 'puzzle', 'quest', 'encounter']}
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
    state = []
    active_name = ACTIVE_ENCOUNTER[TURN_INDEX].name if ACTIVE_ENCOUNTER and TURN_INDEX < len(ACTIVE_ENCOUNTER) else None
    for i, c in enumerate(ACTIVE_ENCOUNTER):
        if not c.is_pc and (c.conditions.get('hidden') or c.conditions.get('undetected')): continue
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
            'attacks': [{'name': a['name'], 'hit': a['strikes'][0]['label'] if a.get('strikes') else '+?', 'damage': a['damage']} for a in getattr(pc, 'attacks', [])],
            'mods': getattr(pc, 'mods', {}),
        }
        party.append(pc_data)
    return jsonify({'party': party})

@app.route('/api/events')
def sse_stream():
    """Server-Sent Events stream for real-time updates to player sheets."""
    def generate():
        q = queue.Queue(maxsize=50)
        with _sse_lock:
            # Enforce max subscriber cap
            if len(_sse_subscribers) >= _SSE_MAX_SUBSCRIBERS:
                # Remove oldest subscriber
                _sse_subscribers.pop(0)
            _sse_subscribers.append(q)
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
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)
    
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
    log_entry = {
        'id': str(uuid.uuid4()), 
        'name': data.get('name', 'Player'), 
        'action': data.get('action', 'Action'), 
        'result': data.get('result', ''), 
        'detail': data.get('detail', ''),
        'time': datetime.now().strftime('%H:%M:%S'),
        'round': ROUND_NUMBER
    }
    COMBAT_LOGS.append(log_entry)
    if len(COMBAT_LOGS) > 200: COMBAT_LOGS.pop(0)
    
    # Broadcast to all connected clients so everyone sees each other's rolls
    sse_broadcast('player_roll', {
        'name': log_entry['name'],
        'action': log_entry['action'],
        'result': log_entry['result'],
        'detail': log_entry['detail'],
        'time': log_entry['time']
    })
    
    return jsonify({"success": True})

@app.route('/api/get_logs')
def get_logs():
    last_id = request.args.get('last_id')
    if not last_id: return jsonify({'logs': COMBAT_LOGS[-5:]}) 
    idx = next((i for i, log in enumerate(COMBAT_LOGS) if log['id'] == last_id), -1)
    if idx != -1: return jsonify({'logs': COMBAT_LOGS[idx+1:]})
    return jsonify({'logs': COMBAT_LOGS[-5:]})

@app.route('/api/get_full_log')
def get_full_log():
    """Return the complete combat log for the history panel."""
    return jsonify({'logs': list(reversed(COMBAT_LOGS))})

@app.route('/api/clear_log', methods=['POST'])
def clear_log():
    COMBAT_LOGS.clear()
    return jsonify({"success": True})

@app.route('/api/compendium_search')
def compendium_search():
    """Search the PF2E compendium database across feats, spells, and equipment."""
    query = request.args.get('q', '').strip()
    category = request.args.get('cat', 'all')  # all, feats, spells, equipment
    if not query or len(query) < 2:
        return jsonify({"results": []})
    
    db_path = os.path.join(BASE_DIR, 'pf2e_database.db')
    if not os.path.exists(db_path):
        return jsonify({"results": [], "error": "Database not found"})
    
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
        conn.close()
    
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
    
    db_path = os.path.join(BASE_DIR, 'pf2e_database.db')
    if not os.path.exists(db_path):
        return jsonify({"error": "Database not found"}), 404
    
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
        conn.close()

@app.route('/api/long_rest/<pc_name>', methods=['POST'])
def long_rest(pc_name):
    if pc_name in PARTY_LIBRARY:
        pc = PARTY_LIBRARY[pc_name]
        pc.current_hp = pc.hp
        pc.current_focus = pc.focus_max
        
        # PF2E Rest Rules:
        # - Wounded: clears entirely after full night's rest
        # - Drained: reduces by 1 (not cleared)
        # - Doomed: does NOT change from rest (only specific effects remove it)
        # - Fatigued: clears after rest
        # - All other temporary conditions clear
        drained_val = max(0, pc.conditions.get('drained', 0) - 1)
        doomed_val = pc.conditions.get('doomed', 0)  # Preserved
        
        pc.conditions = {
            'frightened': 0, 'sickened': 0, 'dying': 0, 'wounded': 0,
            'doomed': doomed_val, 'drained': drained_val,
            'prone': False, 'off_guard': False, 'concealed': False,
            'hidden': False, 'undetected': False
        }
        
        # Set a flag so the player sheet knows to clear localStorage spell state
        pc._spell_slots_refreshed = True
        
        # Sync to tracker
        for c in ACTIVE_ENCOUNTER:
            if c.is_pc and c.name == pc_name:
                c.current_hp = pc.hp
                c.current_focus = pc.focus_max
                c.conditions = dict(pc.conditions)
        
        result = {"success": True, "restored": {
            "hp": pc.hp, "focus": pc.focus_max,
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
    """Server-side spell slot persistence. GET returns current state, POST saves it."""
    file_path = get_pc_file_path(pc_name)
    if not os.path.exists(file_path):
        return jsonify({"error": "Character not found"}), 404
    
    if request.method == 'GET':
        with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
        build = pc_json.get('build', pc_json)
        return jsonify({"success": True, "expended_slots": build.get('expended_slots', {})})
    
    # POST — save slot state
    data = request.json
    slots = data.get('expended_slots', {})
    with open(file_path, 'r', encoding='utf-8') as f: pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    build['expended_slots'] = slots
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

@app.route('/api/toggle_shield/<pc_name>', methods=['POST'])
def toggle_shield(pc_name):
    """Toggle Raise Shield on/off."""
    if pc_name not in PARTY_LIBRARY:
        return jsonify({"error": "Character not found"}), 404
    
    file_path = get_pc_file_path(pc_name)
    if not file_path:
        return jsonify({"error": "File not found"}), 404
    
    with open(file_path, 'r', encoding='utf-8') as f:
        pc_json = json.load(f)
    build = pc_json.get('build', pc_json)
    
    build['shield_raised'] = not build.get('shield_raised', False)
    save_and_reload_character(pc_name, pc_json, file_path)
    
    pc = PARTY_LIBRARY[pc_name]
    return jsonify({"success": True, "shield_raised": pc.shield_raised, "ac": pc.ac})

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
    """Upload a character portrait image."""
    if 'file' not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({"error": "No filename"}), 400
    
    # Validate image type
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
        return jsonify({"error": "Invalid image type"}), 400
    
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
        save_and_reload_character(pc_name, pc_json, file_path)
    
    return jsonify({"success": True, "filename": filename})

@app.route('/portraits/<filename>')
def serve_portrait(filename):
    """Serve portrait images from party_data/portraits/."""
    portraits_dir = os.path.join(PARTY_DIR, 'portraits')
    return send_from_directory(portraits_dir, filename)

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
                'notes', 'session_notes', 'portrait', 'active_toggles',
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
    for sk in ['acrobatics', 'arcana', 'athletics', 'crafting', 'deception', 'diplomacy', 'intimidation', 'medicine', 'nature', 'occultism', 'performance', 'religion', 'society', 'stealth', 'survival', 'thievery']:
        if sk in build['proficiencies'] and sk not in data.get('skills', {}):
            del build['proficiencies'][sk]
    
    for sk, rank in data.get('skills', {}).items():
        # Enforce skill rank gating
        if validate_skill_rank(rank, new_level):
            build['proficiencies'][sk.lower()] = rank
        else:
            # Cap to the highest valid rank for this level
            if new_level < 7 and rank > 4: rank = 4
            elif new_level < 15 and rank > 6: rank = 6
            build['proficiencies'][sk.lower()] = rank

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
    """Broadcast full map state to all connected clients."""
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

@app.route('/map')
def player_map_view():
    """Player's restricted map view."""
    with MAP_LOCK:
        current_map = dict(ACTIVE_MAP)
        # Players don't see fog data - they just see what's revealed
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
    is_gm = session.get('gm_authenticated', False)
    
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
@gm_required
def update_explored():
    """Update explored grid cells."""
    data = request.json or {}
    explored = data.get('explored', [])
    
    with MAP_LOCK:
        ACTIVE_MAP['explored'] = explored
    
    _save_map_state()
    _broadcast_map_state()
    return jsonify({'success': True})

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

@app.route('/api/map/state')
def get_map_state():
    """Get current map state (filtered for players)."""
    is_gm = session.get('gm_authenticated', False)
    
    with MAP_LOCK:
        state = dict(ACTIVE_MAP)
        
        if not is_gm:
            # Filter tokens not visible to players
            state['tokens'] = [t for t in state['tokens'] if t.get('visible_to_players', True)]
            # Don't send raw fog data to players - they'll compute visibility client-side
    
    return jsonify(state)

@app.route('/api/map/clear', methods=['POST'])
@gm_required  
def clear_map():
    """Clear the current map."""
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
            'fog': [],
            'walls': [],
            'fog_enabled': True,
            'player_control': False,
        }
    _broadcast_map_state()
    return jsonify({'success': True})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    debug = os.environ.get('FLASK_DEBUG', 'true').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)