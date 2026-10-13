"""Numeric effects layered on top of the build: magic items, toggleable
effects (Rage, Bladesong, Shield...), always-on class features, and
limited-use detection.

Everything here produces a `Mods` accumulator; `character.compute` applies it
at the right point (ability scores first, then saves/skills/initiative, AC,
attacks, speed, resistances). Values that depend on an ability modifier are
stored as ``('mod', 'int')`` / ``('pb',)`` references and resolved once the
modifiers are known, so ordering inside a source never matters.

Where 5etools carries the data (item `ability`, `bonusSpellAttack`, `resist`,
`modifySpeed`, `charges`...), it is read from the record. Features that only
exist as prose are listed by name in the tables below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from systems.dnd5e.render import strip_tags
from systems.dnd5e.rules.tables import ABILITIES

DAMAGE_PHYSICAL = ('bludgeoning', 'piercing', 'slashing')


@dataclass
class Mods:
    ability_static: dict = field(default_factory=dict)   # ability -> minimum score (Belt of Giant Strength)
    ability_bonus: dict = field(default_factory=dict)    # ability -> +n (attuned Ioun-style items)
    save_all: list = field(default_factory=list)         # refs added to every save
    save: dict = field(default_factory=dict)             # ability -> [refs]
    check_all: list = field(default_factory=list)        # refs added to every ability check (skills, init)
    skill: dict = field(default_factory=dict)            # skill -> [refs]
    initiative: list = field(default_factory=list)
    pb_bonus: int = 0
    ac_bonus: list = field(default_factory=list)         # (ref, label)
    ac_min: list = field(default_factory=list)           # (value, label)
    ac_unarmored: list = field(default_factory=list)     # (('base', 13, 'dex'), label)
    spell_attack: int = 0
    spell_dc: int = 0
    resist: set = field(default_factory=set)
    immune: set = field(default_factory=set)
    vulnerable: set = field(default_factory=set)
    condition_immune: set = field(default_factory=set)
    speed_bonus: dict = field(default_factory=dict)      # mode -> +n
    speed_equal: dict = field(default_factory=dict)      # mode -> 'walk'
    speed_static: dict = field(default_factory=dict)     # mode -> n
    speed_mult: float = 1.0
    melee_str_damage: list = field(default_factory=list)  # refs added to STR melee damage (Rage)
    weapon_extra: list = field(default_factory=list)      # (dice text, applies: 'melee'|'all', label)
    attack_notes: list = field(default_factory=list)      # (applies, text)
    notes: list = field(default_factory=list)             # general reminders shown on the sheet
    crit_threshold: int = 20
    resources: list = field(default_factory=list)        # dicts like character._resources rows

    def resolve(self, ref, mods: dict, pb: int) -> int:
        if isinstance(ref, int):
            return ref
        if isinstance(ref, tuple):
            if ref[0] == 'mod':
                v = mods[ref[1]]
                return max(ref[2], v) if len(ref) > 2 else v
            if ref[0] == 'pb':
                return pb
            if ref[0] == 'half_pb_up':
                return -(-pb // 2)
        return 0


def _b(v) -> int:
    try:
        return int(str(v or '0').replace('+', ''))
    except ValueError:
        return 0


# --- magic items ------------------------------------------------------------

def apply_item(m: Mods, rec: dict, *, active: bool, label: str):
    """Fold one inventory item into Mods. ``active``: equipped, and attuned if
    the item requires attunement (worn magic does nothing in a backpack)."""
    if not active:
        return
    ab = rec.get('ability') or {}
    if isinstance(ab, dict):
        for a, v in (ab.get('static') or {}).items():
            if a in ABILITIES:
                m.ability_static[a] = max(m.ability_static.get(a, 0), int(v))
        # Additive bonuses only from attunement items: the unattuned ones with
        # a bonus are consumables (Manual of Bodily Health is read once).
        if rec.get('reqAttune'):
            for a, v in ab.items():
                if a in ABILITIES and isinstance(v, int):
                    m.ability_bonus[a] = m.ability_bonus.get(a, 0) + v
    t = (rec.get('type') or '').split('|')[0]
    if t not in ('LA', 'MA', 'HA', 'S') and rec.get('bonusAc'):
        m.ac_bonus.append((_b(rec['bonusAc']), rec['name']))
    if rec.get('bonusSavingThrow'):
        m.save_all.append(_b(rec['bonusSavingThrow']))
    if rec.get('bonusAbilityCheck'):
        m.check_all.append(_b(rec['bonusAbilityCheck']))
    if rec.get('bonusProficiencyBonus'):
        m.pb_bonus += _b(rec['bonusProficiencyBonus'])
    if rec.get('bonusSpellAttack'):
        m.spell_attack += _b(rec['bonusSpellAttack'])
    if rec.get('bonusSpellSaveDc'):
        m.spell_dc += _b(rec['bonusSpellSaveDc'])
    for key, target in (('resist', m.resist), ('immune', m.immune), ('vulnerable', m.vulnerable),
                        ('conditionImmune', m.condition_immune)):
        for x in rec.get(key) or []:
            if isinstance(x, str):
                target.add(x.lower())
    ms = rec.get('modifySpeed') or {}
    for mode, src in (ms.get('equal') or {}).items():
        m.speed_equal[mode] = src
    for mode, v in (ms.get('static') or {}).items():
        m.speed_static[mode] = max(m.speed_static.get(mode, 0), int(v))
    for mode, v in (ms.get('bonus') or {}).items():
        m.speed_bonus[mode] = m.speed_bonus.get(mode, 0) + int(v)
    if rec.get('critThreshold'):
        m.crit_threshold = min(m.crit_threshold, int(rec['critThreshold']))


def item_charges(rec: dict) -> int | None:
    c = rec.get('charges')
    if isinstance(c, int) and c > 0:
        return c
    return None


# --- toggleable effects ---------------------------------------------------------
# key -> (label, requirement, description). The numbers are applied in
# apply_effect; `requires` gates what the sheet offers (feature names / class).

EFFECTS = {
    'rage': ('Rage', ('class', 'Barbarian'), 'Resistance to bludgeoning, piercing and slashing; +rage damage on Strength attacks; advantage on Strength checks and saves.'),
    'bladesong': ('Bladesong', ('feature', 'Bladesong'), '+INT to AC (min +1), +10 ft. speed, advantage on Acrobatics, +INT to Concentration saves.'),
    'mage_armor': ('Mage Armor', None, 'Unarmored AC becomes 13 + DEX.'),
    'shield': ('Shield (spell)', None, '+5 AC until the start of your next turn.'),
    'shield_of_faith': ('Shield of Faith', None, '+2 AC (concentration).'),
    'haste': ('Haste', None, '+2 AC, speed doubled, advantage on DEX saves, one extra limited action.'),
    'barkskin': ('Barkskin', None, 'AC can\'t be less than 16 (2014) / 17 (2024).'),
    'bless': ('Bless', None, 'Add 1d4 to attack rolls and saving throws.'),
    'hunters_mark': ("Hunter's Mark", None, 'Extra 1d6 damage (Force in 2024) when you hit the marked target.'),
    'hex': ('Hex', None, 'Extra 1d6 necrotic damage when you hit the cursed target.'),
    'divine_favor': ('Divine Favor', None, 'Weapon hits deal an extra 1d4 radiant damage.'),
    'shillelagh': ('Shillelagh', None, 'Club / quarterstaff attacks use your spellcasting ability and a bigger die.'),
    'dodge': ('Dodge', None, 'Attacks against you have disadvantage; advantage on DEX saves.'),
}


def available_effects(feature_names: set, class_levels: dict) -> list:
    out = []
    for key, (label, req, desc) in EFFECTS.items():
        if req and req[0] == 'class' and not class_levels.get(req[1]):
            continue
        if req and req[0] == 'feature' and req[1] not in feature_names:
            continue
        out.append({'key': key, 'name': label, 'description': desc})
    return out


def apply_effects(m: Mods, active: list, *, edition: str, feature_names: set, rage_damage,
                  totem: str | None = None):
    for key in active or []:
        if key == 'rage':
            if totem == 'bear':
                m.resist |= {'acid', 'bludgeoning', 'cold', 'fire', 'force', 'lightning', 'necrotic',
                             'piercing', 'poison', 'radiant', 'slashing', 'thunder'}
            else:
                m.resist |= set(DAMAGE_PHYSICAL)
            if rage_damage:
                m.melee_str_damage.append(int(str(rage_damage).replace('+', '') or 0))
            m.notes.append('Raging: advantage on Strength checks and saves.')
        elif key == 'bladesong':
            m.ac_bonus.append((('mod', 'int', 1), 'Bladesong'))
            m.speed_bonus['walk'] = m.speed_bonus.get('walk', 0) + 10
        elif key == 'mage_armor':
            m.ac_unarmored.append(((13, 'dex'), 'Mage Armor'))
        elif key == 'shield':
            m.ac_bonus.append((5, 'Shield'))
        elif key == 'shield_of_faith':
            m.ac_bonus.append((2, 'Shield of Faith'))
        elif key == 'haste':
            m.ac_bonus.append((2, 'Haste'))
            m.speed_mult *= 2
        elif key == 'barkskin':
            m.ac_min.append((17 if edition == 'one' else 16, 'Barkskin'))
        elif key == 'bless':
            m.attack_notes.append(('all', 'Bless: +1d4'))
            m.notes.append('Bless: +1d4 to saving throws.')
        elif key == 'hunters_mark':
            m.weapon_extra.append(('1d6 force' if edition == 'one' else '1d6', 'all', "Hunter's Mark (marked target)"))
        elif key == 'hex':
            m.weapon_extra.append(('1d6 necrotic', 'all', 'Hex (cursed target)'))
        elif key == 'divine_favor':
            m.weapon_extra.append(('1d4 radiant', 'all', 'Divine Favor'))
        elif key == 'dodge':
            m.notes.append('Dodging: attacks against you have disadvantage.')


# --- always-on features -------------------------------------------------------

def apply_features(m: Mods, *, feature_names: set, options: set, edition: str, class_levels: dict,
                   feat_names: set):
    """Features whose numbers the 5etools data only states in prose."""
    if 'Aura of Protection' in feature_names:
        m.save_all.append(('mod', 'cha', 1))
    if 'Dread Ambusher' in feature_names:
        m.initiative.append(('mod', 'wis'))
    if 'option:Divine Order:thaumaturge' in options:
        for sk in ('arcana', 'religion'):
            m.skill.setdefault(sk, []).append(('mod', 'wis', 1))
    if 'option:Primal Order:magician' in options:
        for sk in ('arcana', 'nature'):
            m.skill.setdefault(sk, []).append(('mod', 'wis', 1))
    if 'Improved Divine Smite' in feature_names or 'Radiant Strikes' in feature_names:
        m.weapon_extra.append(('1d8 radiant', 'melee', 'Radiant strike'))
    if 'Divine Smite' in feature_names and edition == 'classic':
        m.attack_notes.append(('melee', 'Divine Smite: expend a slot for +2d8 radiant (+1d8 per slot level above 1st, +1d8 vs undead/fiends)'))
    if 'Reliable Talent' in feature_names:
        m.notes.append('Reliable Talent: a d20 roll of 9 or lower counts as 10 on checks you are proficient in.')
    if 'Feral Instinct' in feature_names:
        m.notes.append('Feral Instinct: advantage on Initiative.')
    if 'Evasion' in feature_names:
        m.notes.append('Evasion: DEX save for half damage: success = none, failure = half.')
    if 'Danger Sense' in feature_names:
        m.notes.append('Danger Sense: advantage on DEX saves (not while incapacitated).')
    if 'Elemental Affinity' in feature_names:
        m.notes.append('Elemental Affinity: add CHA to one damage roll of spells of your ancestry\'s damage type.')
    if 'great weapon master' in feat_names:
        if edition == 'one':
            m.attack_notes.append(('heavy', 'Great Weapon Master: +PB damage on a hit with the Attack action'))
        else:
            m.attack_notes.append(('heavy', 'Great Weapon Master: may take -5 to hit for +10 damage'))
    if 'sharpshooter' in feat_names and edition == 'classic':
        m.attack_notes.append(('ranged', 'Sharpshooter: may take -5 to hit for +10 damage'))
    if 'heavy armor master' in feat_names:
        m.notes.append('Heavy Armor Master: reduce bludgeoning, piercing and slashing damage by '
                       + ('your Proficiency Bonus' if edition == 'one' else '3') + ' while in heavy armor.')
    if 'Remarkable Athlete' in feature_names and edition == 'classic':
        m.initiative.append(('remarkable',))


# --- limited-use detection ---------------------------------------------------------
# Features that say how often they can be used. Anything already tracked by
# character._resources is excluded by name so it is never counted twice.

_WORDS = {'once': 1, 'twice': 2, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
_REST = r'(short or long|short|long) rest'
_FINISH = r'(?:finish|complete)(?:es|s)? a '
_PATTERNS = [
    # "a number of times equal to your Proficiency Bonus / Wisdom modifier (minimum of once)"
    (re.compile(r'number of times equal to (?:your|twice your) (proficiency bonus|(strength|dexterity|constitution|'
                r'intelligence|wisdom|charisma) modifier)(?: \(minimum of (?:once|1)\))?', re.I), 'expr'),
    # "You can use this feature/trait twice/three times"
    (re.compile(r'you can (?:use|do|cast) (?:this|it|the spell)[^.]{0,40}? (once|twice|three times|two times)', re.I), 'word'),
    # "... you can't use it again until you finish a Long Rest" (2014: "complete a short or long rest")
    (re.compile(r"(?:can't|cannot) (?:use|do|cast)[^.]{0,40}?again until you " + _FINISH + _REST, re.I), 'once'),
]

# Tracked by character._resources with exact class-table numbers.
HANDLED = {
    'Rage', 'Bardic Inspiration', 'Channel Divinity', 'Wild Shape', 'Action Surge', 'Indomitable',
    'Second Wind', 'Arcane Recovery', 'Favored Enemy', 'Magical Cunning', 'Lay on Hands', 'Divine Sense',
    'Font of Magic', 'Ki', 'Focus', "Monk's Focus", 'Combat Superiority', 'Superiority Dice', 'Pact Boon',
    'Pact Magic', 'Spellcasting',
}
_ABIL = {'strength': 'str', 'dexterity': 'dex', 'constitution': 'con', 'intelligence': 'int',
         'wisdom': 'wis', 'charisma': 'cha'}


def detect_uses(name: str, entries, *, mods: dict, pb: int):
    """(max_uses, recharge) parsed from a feature's text, or None."""
    text = strip_tags(_flatten(entries))
    if not text:
        return None
    if name in HANDLED:
        return None
    rest = re.search(_FINISH + _REST, text, re.I)
    if not rest:
        return None
    recharge = 'short' if rest.group(1).lower().startswith('short') else 'long'
    for pat, kind in _PATTERNS:
        mt = pat.search(text)
        if not mt:
            continue
        if kind == 'expr':
            what = mt.group(1).lower()
            if what.startswith('proficiency'):
                n = pb
            else:
                n = max(1, mods[_ABIL[mt.group(2).lower()]])
            if 'twice your' in mt.group(0).lower():
                n *= 2
            return n, recharge
        if kind == 'word':
            w = mt.group(1).lower()
            return {'once': 1, 'twice': 2, 'two times': 2, 'three times': 3}.get(w, 1), recharge
        if kind == 'once':
            return 1, recharge
    return None


def _flatten(entries) -> str:
    out = []

    def walk(e):
        if isinstance(e, str):
            out.append(e)
        elif isinstance(e, list):
            for x in e:
                walk(x)
        elif isinstance(e, dict):
            # Don't read sub-features' text into their parent's usage rule.
            if e.get('__prop') in ('classFeature', 'subclassFeature'):
                return
            walk(e.get('entries'))
            walk(e.get('items'))
    walk(entries)
    return ' '.join(out)
