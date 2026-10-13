"""Spell numbers for the sheet: attack / save, damage or healing dice at the
character's level, and the per-slot increase for upcasting.

Read from the 5etools record: ``spellAttack`` (M/R), ``savingThrow``,
``damageInflict``, ``scalingLevelDice`` (cantrips) and the ``{@damage}`` /
``{@dice}`` / ``{@scaledamage base|levels|step}`` tags in the text.
"""
from __future__ import annotations

import re

_ABBR = {'strength': 'STR', 'dexterity': 'DEX', 'constitution': 'CON', 'intelligence': 'INT',
         'wisdom': 'WIS', 'charisma': 'CHA'}


def _text(entries) -> str:
    out = []

    def walk(e):
        if isinstance(e, str):
            out.append(e)
        elif isinstance(e, list):
            for x in e:
                walk(x)
        elif isinstance(e, dict):
            walk(e.get('entries'))
            walk(e.get('items'))
    walk(entries)
    return ' '.join(out)


def cantrip_dice(rec: dict, char_level: int):
    sld = rec.get('scalingLevelDice')
    if isinstance(sld, list):
        sld = sld[0] if sld else None
    if not isinstance(sld, dict):
        return None, None
    best = None
    for lvl, dice in (sld.get('scaling') or {}).items():
        if int(lvl) <= char_level and (best is None or int(lvl) > best[0]):
            best = (int(lvl), dice)
    return (best[1] if best else None), sld.get('label')


def spell_numbers(rec: dict, *, char_level: int, ability_mod: int = 0, bonuses=()) -> dict:
    """{'attack': 'ranged'|'melee'|None, 'save': 'DEX'|None, 'dice': '8d6',
    'kind': 'damage'|'healing', 'type': 'fire', 'per_slot': '1d6', 'bonus': 3,
    'bonus_label': 'Agonizing Blast'}"""
    out = {'attack': None, 'save': None, 'dice': None, 'kind': None, 'type': None, 'per_slot': None,
           'bonus': 0, 'bonus_label': ''}
    sa = rec.get('spellAttack') or []
    if sa:
        out['attack'] = 'melee' if sa[0] == 'M' else 'ranged'
    st = rec.get('savingThrow') or []
    if st:
        out['save'] = '/'.join(_ABBR.get(x, x[:3].upper()) for x in st)
    dmg_types = rec.get('damageInflict') or []
    out['type'] = '/'.join(dmg_types) or None
    if rec.get('level', 0) == 0:
        dice, label = cantrip_dice(rec, char_level)
        if dice:
            out['dice'], out['kind'] = dice, 'damage' if dmg_types else ('healing' if 'heal' in (label or '').lower() else 'damage')
        else:
            # Cantrips that scale by count rather than dice (Eldritch Blast's beams).
            mt = re.search(r'\{@damage ([^}|]+)', _text(rec.get('entries')))
            if mt and dmg_types:
                out['dice'], out['kind'] = mt.group(1).strip(), 'damage'
                if rec.get('name') == 'Eldritch Blast':
                    out['count'] = 1 + sum(1 for t in (5, 11, 17) if char_level >= t)
    else:
        body = _text(rec.get('entries'))
        mt = re.search(r'\{@damage ([^}|]+)', body)
        if mt and dmg_types:
            out['dice'], out['kind'] = mt.group(1).strip(), 'damage'
        else:
            mt = re.search(r'\{@dice ([^}|]+)', body)
            # "regain(s) hit points" = healing; Sleep's "5d8 hit points" pool is not.
            if mt and re.search(r'\bregains?\b', body, re.I):
                out['dice'], out['kind'] = mt.group(1).strip(), 'healing'
            elif mt and re.search(r'temporary hit points', body, re.I):
                out['dice'], out['kind'] = mt.group(1).strip(), 'temp HP'
        higher = _text(rec.get('entriesHigherLevel'))
        mt = re.search(r'\{@scale(?:damage|dice) [^|}]+\|[^|}]+\|([^|}]+)', higher)
        if mt:
            out['per_slot'] = mt.group(1).strip()
    if out['kind'] == 'healing' and out['dice'] and 'spellcasting ability modifier' in _text(rec.get('entries')):
        out['bonus'] += ability_mod
        out['bonus_label'] = 'spellcasting modifier'
    for amount, label in bonuses:
        out['bonus'] += amount
        out['bonus_label'] = (out['bonus_label'] + ', ' if out['bonus_label'] else '') + label
    return out


def damage_bonuses(rec: dict, *, sheet_feature_names: set, invocations: set, flags: set, mods: dict,
                   caster_class: str | None) -> list:
    """Flat bonuses some features add to a spell's damage roll."""
    out = []
    name = rec.get('name')
    lvl = rec.get('level', 0)
    if name == 'Eldritch Blast' and 'Agonizing Blast' in invocations:
        out.append((mods['cha'], 'Agonizing Blast (per beam)'))
    if lvl == 0 and rec.get('damageInflict'):
        potent = ('Potent Spellcasting' in sheet_feature_names and caster_class in ('Cleric', 'Druid')) or \
                 'option:Blessed Strikes:potent spellcasting' in flags or \
                 'option:Elemental Fury:potent spellcasting' in flags
        if potent:
            out.append((mods['wis'], 'Potent Spellcasting'))
    if 'Empowered Evocation' in sheet_feature_names and rec.get('school') == 'V' and rec.get('damageInflict') \
            and caster_class == 'Wizard':
        out.append((mods['int'], 'Empowered Evocation (one roll)'))
    return out
