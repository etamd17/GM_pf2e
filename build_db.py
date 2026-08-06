import sqlite3
import json
import os
import re
import uuid

# --- DIRECTORY SETUP ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
COMPENDIUM_DIR = os.path.join(BASE_DIR, 'compendium_data')
DB_PATH = os.path.join(BASE_DIR, 'pf2e_database.db')

def safe_int(val, default=0):
    try: return int(float(val)) if val is not None else default
    except: return default

def clean_foundry_text(text):
    if not isinstance(text, str): return ""
    text = re.sub(r'@Localize\[.*?\]', '', text)
    text = re.sub(r'@\w+\[.*?\]\{(.*?)\}', r'\1', text)
    def extract_name(match): return match.group(1).split('.')[-1]
    text = re.sub(r'@\w+\[(.*?)\]', extract_name, text)
    return text.strip()

def setup_database():
    """Creates the relational schema for the PF2e Engine."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH) # Start fresh every time we build
        
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS ancestries (id TEXT PRIMARY KEY, name TEXT, hp INTEGER, size TEXT, speed INTEGER, boosts TEXT, flaws TEXT, description TEXT, rule_elements TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS backgrounds (id TEXT PRIMARY KEY, name TEXT, boosts TEXT, description TEXT, rule_elements TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS classes (id TEXT PRIMARY KEY, name TEXT, key_ability TEXT, hp INTEGER, description TEXT, rule_elements TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS feats (id TEXT PRIMARY KEY, name TEXT, category TEXT, level INTEGER, traits TEXT, description TEXT, rule_elements TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS spells (id TEXT PRIMARY KEY, name TEXT, level INTEGER, traditions TEXT, description TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS equipment (id TEXT PRIMARY KEY, name TEXT, type TEXT, level INTEGER, damage_die TEXT, ac_bonus INTEGER, dex_cap INTEGER, check_penalty INTEGER, traits TEXT, description TEXT, rule_elements TEXT)''')

    conn.commit()
    return conn

def extract_level(system_dict):
    lvl = system_dict.get('level', 1)
    if isinstance(lvl, dict): return safe_int(lvl.get('value'), 1)
    return safe_int(lvl, 1)

def extract_compendium(conn):
    """Crawls the JSONs and injects them into SQLite securely."""
    cursor = conn.cursor()
    counters = {'ancestries': 0, 'backgrounds': 0, 'classes': 0, 'feats': 0, 'spells': 0, 'equipment': 0}
    
    if not os.path.exists(COMPENDIUM_DIR):
        print(f"ERROR: Could not find {COMPENDIUM_DIR}")
        return

    print("Crawling Compendium JSONs... This may take a moment.")
    
    for root, dirs, files in os.walk(COMPENDIUM_DIR):
        root_lower = root.lower()
        for file in files:
            if not file.endswith('.json'): continue
            
            full_path = os.path.join(root, file)
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    items_to_process = [data] if isinstance(data, dict) else data
                    
                    for item in items_to_process:
                        name = item.get('name')
                        if not name: continue
                        
                        item_id = item.get('_id', str(uuid.uuid4()))
                        item_type = item.get('type', '')
                        system = item.get('system', {})
                        traits_raw = system.get('traits', {})
                        
                        desc = clean_foundry_text(system.get('description', {}).get('value', ''))
                        rule_elements = json.dumps(system.get('rules', []))
                        
                        # --- ANCESTRIES ---
                        if item_type == 'ancestry' or 'ancestries' in root_lower:
                            hp = safe_int(system.get('hp'), 8)
                            speed = safe_int(system.get('speed'), 25)
                            size = system.get('size', {}).get('value', 'med') if isinstance(system.get('size'), dict) else 'med'
                            
                            boosts = [v['value'] for k, v in system.get('boosts', {}).items() if isinstance(v, dict) and 'value' in v and v['value']]
                            flaws = [v['value'] for k, v in system.get('flaws', {}).items() if isinstance(v, dict) and 'value' in v and v['value']]
                            
                            cursor.execute("INSERT OR REPLACE INTO ancestries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                                           (item_id, name, hp, size, speed, json.dumps(boosts), json.dumps(flaws), desc, rule_elements))
                            counters['ancestries'] += 1
                            
                        # --- BACKGROUNDS ---
                        elif item_type == 'background' or 'backgrounds' in root_lower:
                            boosts = [v['value'] for k, v in system.get('boosts', {}).items() if isinstance(v, dict) and 'value' in v and v['value']]
                            cursor.execute("INSERT OR REPLACE INTO backgrounds VALUES (?, ?, ?, ?, ?)", 
                                           (item_id, name, json.dumps(boosts), desc, rule_elements))
                            counters['backgrounds'] += 1
                            
                        # --- CLASSES ---
                        elif item_type == 'class' or 'classes' in root_lower:
                            hp = safe_int(system.get('hp'), 8)
                            key_ability = system.get('keyAbility', {}).get('value', [])
                            cursor.execute("INSERT OR REPLACE INTO classes VALUES (?, ?, ?, ?, ?, ?)", 
                                           (item_id, name, json.dumps(key_ability), hp, desc, rule_elements))
                            counters['classes'] += 1
                            
                        # --- FEATS & FEATURES ---
                        elif item_type in ['feat', 'action', 'feature'] or 'feats' in root_lower or 'actions' in root_lower:
                            level = extract_level(system)
                            
                            feat_traits = traits_raw.get('value', []) if isinstance(traits_raw, dict) else []
                            feat_traits = [t.lower() for t in feat_traits] if isinstance(feat_traits, list) else []
                            
                            cat = 'general'
                            if 'class' in feat_traits or 'class' in root_lower: cat = 'class'
                            elif 'skill' in feat_traits or 'skill' in root_lower: cat = 'skill'
                            elif 'ancestry' in feat_traits or 'ancestry' in root_lower: cat = 'ancestry'
                            
                            cursor.execute("INSERT OR REPLACE INTO feats VALUES (?, ?, ?, ?, ?, ?, ?)", 
                                           (item_id, name, cat, level, json.dumps(feat_traits), desc, rule_elements))
                            counters['feats'] += 1

                        # --- SPELLS ---
                        elif item_type == 'spell' or 'spells' in root_lower:
                            level = extract_level(system)
                            
                            t_val = traits_raw.get('value', []) if isinstance(traits_raw, dict) else []
                            if isinstance(t_val, list) and any('cantrip' in str(t).lower() for t in t_val): 
                                level = 0
                                
                            trads = []
                            if isinstance(traits_raw, dict) and 'traditions' in traits_raw:
                                t_tr = traits_raw['traditions']
                                if isinstance(t_tr, list): trads.extend(t_tr)
                                elif isinstance(t_tr, dict) and 'value' in t_tr: trads.extend(t_tr['value'])
                            
                            sys_tr = system.get('traditions', {})
                            if isinstance(sys_tr, dict) and 'value' in sys_tr: trads.extend(sys_tr['value'])
                            elif isinstance(sys_tr, list): trads.extend(sys_tr)
                            
                            if isinstance(t_val, list): trads.extend([t for t in t_val if isinstance(t, str) and t.lower() in ['arcane', 'divine', 'occult', 'primal']])
                            
                            traditions = list(set([t.lower() for t in trads]))
                            
                            cursor.execute("INSERT OR REPLACE INTO spells VALUES (?, ?, ?, ?, ?)", 
                                           (item_id, name, level, json.dumps(traditions), desc))
                            counters['spells'] += 1

                        # --- EQUIPMENT (WEAPONS & ARMOR) ---
                        elif item_type in ['weapon', 'armor', 'equipment', 'consumable'] or 'equipment' in root_lower or 'weapons' in root_lower:
                            level = extract_level(system)
                            t_val = traits_raw.get('value', []) if isinstance(traits_raw, dict) else []
                            traits = [str(t).lower() for t in t_val] if isinstance(t_val, list) else []
                            
                            dmg_die = ""
                            ac_bonus = 0
                            dex_cap = 99
                            check_pen = 0
                            
                            if item_type == 'weapon' or 'weapons' in root_lower:
                                # Foundry PF2E stores damage as system.damage.die ("d8") + system.damage.dice (1) + system.damage.damageType
                                dmg_data = system.get('damage', {})
                                if isinstance(dmg_data, dict):
                                    die = dmg_data.get('die', '')
                                    dice_count = safe_int(dmg_data.get('dice'), 1)
                                    dmg_type = dmg_data.get('damageType', '')
                                    if die:
                                        dmg_letter = dmg_type[0].upper() if dmg_type else ''
                                        dmg_die = f"{dice_count}{die} {dmg_letter}".strip()
                                    else:
                                        # Fallback: try .value or .roll
                                        dmg_die = dmg_data.get('value', '')
                                        if not dmg_die:
                                            roll = dmg_data.get('roll', [])
                                            if isinstance(roll, dict) and roll: dmg_die = str(list(roll.values())[0])
                                            elif isinstance(roll, list) and roll: dmg_die = str(roll[0])
                            elif item_type == 'armor' or 'armor' in root_lower:
                                ac_bonus = safe_int(system.get('acBonus'), 0)
                                dex_cap = safe_int(system.get('dexCap'), 99)
                                check_pen = safe_int(system.get('checkPenalty'), 0)

                            # Assign default string if it's still missing so SQLite doesn't complain
                            if not dmg_die: dmg_die = "1d4"

                            cursor.execute("INSERT OR REPLACE INTO equipment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                                           (item_id, name, item_type, level, str(dmg_die), ac_bonus, dex_cap, check_pen, json.dumps(traits), desc, rule_elements))
                            counters['equipment'] += 1

            except Exception as e:
                print(f"Error parsing {file}: {e}")

    conn.commit()
    print("\n✅ DATABASE BUILD COMPLETE!")
    print("-------------------------------")
    for category, count in counters.items():
        print(f"Loaded {count} {category.title()}")
    print("-------------------------------")
    print(f"Database saved to: {DB_PATH}")

if __name__ == "__main__":
    connection = setup_database()
    extract_compendium(connection)
    connection.close()