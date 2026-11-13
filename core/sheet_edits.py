"""Validation and merging for a PF2e character's sheet edit layer.

``build['sheet_edits']`` holds corrections made on the sheet instead of by a
Pathbuilder re-import (spec: docs/superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md).
The engine still computes every stat; ``adjust`` only adds a signed GM
adjustment on top, and ``meta`` overrides display fields. ``Character`` applies
the layer; this module only decides what a well-formed patch is.

Pure: no Flask, no file I/O, so the route can validate before it reads.
"""

ADJUST_KEYS = frozenset({
    'str', 'dex', 'con', 'int', 'wis', 'cha',
    'ac', 'fortitude', 'reflex', 'will', 'perception',
    'hp_max', 'speed', 'class_dc', 'spell_dc',
})
SKILLS = frozenset({
    'acrobatics', 'arcana', 'athletics', 'crafting', 'deception', 'diplomacy',
    'intimidation', 'medicine', 'nature', 'occultism', 'performance', 'religion',
    'society', 'stealth', 'survival', 'thievery',
})
META_KEYS = ('display_name', 'deity', 'sanctification', 'languages', 'senses', 'size', 'background')
META_LIST_KEYS = frozenset({'languages', 'senses'})
SIZES = ('Tiny', 'Small', 'Medium', 'Large', 'Huge', 'Gargantuan')

ADJUST_LIMIT = 20
TEXT_MAX = 80
LIST_MAX = 30
LIST_ITEM_MAX = 40

FEAT_ADDED_MAX = 40
NAME_LIST_MAX = 200
CUSTOM_FEATURE_MAX = 30
FEAT_TYPE_MAX = 30
DESC_MAX = 2000
FEAT_KEYS = ('added', 'removed', 'order')
FEATURE_KEYS = ('hidden', 'custom', 'order')


class SheetEditError(ValueError):
    """A rejected patch. ``status`` is the HTTP status the route returns."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _adjust_key(raw):
    if not isinstance(raw, str):
        raise SheetEditError('Adjustment names must be text.')
    key = raw.strip().lower()
    if key in ADJUST_KEYS:
        return key
    if key.startswith('skill:'):
        skill = key[len('skill:'):]
        if skill in SKILLS:
            return key
        if skill.startswith('lore:') and skill[len('lore:'):].strip():
            # Pathbuilder writes 'lore:warfare', the in-app builder 'lore: warfare'.
            return 'skill:lore:' + skill[len('lore:'):].strip()
    raise SheetEditError(f'Unknown stat to adjust: {raw!r}.')


def _clean_adjust(section):
    if not isinstance(section, dict) or not section:
        raise SheetEditError('Adjustments must be a set of stat names and numbers.')
    out = {}
    for raw_key, value in section.items():
        key = _adjust_key(raw_key)
        # bool is an int subclass; a checkbox value is never a valid adjustment.
        if isinstance(value, bool) or not isinstance(value, int):
            raise SheetEditError(f'The adjustment for {key} must be a whole number.')
        if not -ADJUST_LIMIT <= value <= ADJUST_LIMIT:
            raise SheetEditError(
                f'The adjustment for {key} must be between -{ADJUST_LIMIT} and +{ADJUST_LIMIT}.')
        out[key] = value
    return out


def _clean_list(key, value):
    if not isinstance(value, list):
        raise SheetEditError(f'{key.title()} must be a list.')
    if len(value) > LIST_MAX:
        raise SheetEditError(f'At most {LIST_MAX} {key} are allowed.')
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            raise SheetEditError(f'Each entry in {key} must be text.')
        item = item.strip()
        if len(item) > LIST_ITEM_MAX:
            raise SheetEditError(f'Entries in {key} are limited to {LIST_ITEM_MAX} characters.')
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _clean_meta(section):
    if not isinstance(section, dict) or not section:
        raise SheetEditError('Details must be a set of field names and values.')
    out = {}
    for key, value in section.items():
        if key not in META_KEYS:
            raise SheetEditError(f'Unknown detail field: {key!r}.')
        if key in META_LIST_KEYS:
            out[key] = _clean_list(key, value)
            continue
        if not isinstance(value, str):
            raise SheetEditError(f'{key.replace("_", " ").title()} must be text.')
        value = value.strip()
        if len(value) > TEXT_MAX:
            raise SheetEditError(
                f'{key.replace("_", " ").title()} is limited to {TEXT_MAX} characters.')
        if key == 'size' and value and value.title() not in SIZES:
            raise SheetEditError('Size must be one of: ' + ', '.join(SIZES) + '.')
        out[key] = value.title() if key == 'size' else value
    return out


def _clean_name_list(label, value):
    if not isinstance(value, list):
        raise SheetEditError(f'{label} must be a list of names.')
    if len(value) > NAME_LIST_MAX:
        raise SheetEditError(f'{label} is limited to {NAME_LIST_MAX} names.')
    out, seen = [], set()
    for item in value:
        if not isinstance(item, str):
            raise SheetEditError(f'Each entry in {label} must be text.')
        item = item.strip()
        if len(item) > TEXT_MAX:
            raise SheetEditError(f'Names in {label} are limited to {TEXT_MAX} characters.')
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def _clean_added_feats(value):
    if not isinstance(value, list):
        raise SheetEditError('Added feats must be a list.')
    if len(value) > FEAT_ADDED_MAX:
        raise SheetEditError(f'At most {FEAT_ADDED_MAX} feats can be added on the sheet.')
    out, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict):
            raise SheetEditError('Each added feat needs a name, type and level.')
        name, ftype, level = entry.get('name'), entry.get('type', ''), entry.get('level', 1)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > TEXT_MAX:
            raise SheetEditError(f'A feat name is required (at most {TEXT_MAX} characters).')
        if not isinstance(ftype, str) or len(ftype.strip()) > FEAT_TYPE_MAX:
            raise SheetEditError(f'Feat type is limited to {FEAT_TYPE_MAX} characters.')
        if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 20:
            raise SheetEditError(f'The level for {name.strip()} must be a whole number from 1 to 20.')
        if name.strip().lower() in seen:
            continue
        seen.add(name.strip().lower())
        # Pathbuilder's feat shape [name, id, category, level]: Character reads
        # the description from the compendium and applies its rules.
        out.append([name.strip(), None, ftype.strip(), level])
    return out


def _clean_custom_features(value):
    if not isinstance(value, list):
        raise SheetEditError('Custom features must be a list.')
    if len(value) > CUSTOM_FEATURE_MAX:
        raise SheetEditError(f'At most {CUSTOM_FEATURE_MAX} custom features are allowed.')
    out = []
    for entry in value:
        if not isinstance(entry, dict):
            raise SheetEditError('Each custom feature needs a name and a description.')
        name, desc = entry.get('name'), entry.get('desc', '')
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > TEXT_MAX:
            raise SheetEditError(f'A custom feature name is required (at most {TEXT_MAX} characters).')
        if not isinstance(desc, str) or len(desc) > DESC_MAX:
            raise SheetEditError(f'A custom feature description is limited to {DESC_MAX} characters.')
        out.append({'name': name.strip(), 'desc': desc.strip()})
    return out


def _clean_list_section(section, label, keys, cleaners):
    if not isinstance(section, dict) or not section:
        raise SheetEditError(f'{label} must be a set of lists.')
    out = {}
    for key, value in section.items():
        if key not in keys:
            raise SheetEditError(f'Unknown {label.lower()} field: {key!r}.')
        out[key] = cleaners.get(key, lambda v, k=key: _clean_name_list(f'{label} {k}', v))(value)
    return out


def validate_patch(patch, *, is_gm):
    """Return the cleaned patch, or raise SheetEditError (400, or 403 for a
    non-GM changing numbers). An empty string, empty list or 0 means "clear"."""
    if not isinstance(patch, dict) or not patch:
        raise SheetEditError('Nothing to save.')
    for key in patch:
        if key not in ('meta', 'adjust', 'feats', 'features'):
            raise SheetEditError(f'Unknown section: {key!r}.')
    clean = {}
    if 'adjust' in patch:
        if not is_gm:
            raise SheetEditError('Only the GM can adjust numbers.', status=403)
        clean['adjust'] = _clean_adjust(patch['adjust'])
    if 'meta' in patch:
        clean['meta'] = _clean_meta(patch['meta'])
    if 'feats' in patch:
        clean['feats'] = _clean_list_section(patch['feats'], 'Feats', FEAT_KEYS,
                                             {'added': _clean_added_feats})
    if 'features' in patch:
        clean['features'] = _clean_list_section(patch['features'], 'Features', FEATURE_KEYS,
                                                {'custom': _clean_custom_features})
    return clean


def merge_patch(existing, clean):
    """Return a new sheet_edits dict with ``clean`` applied. Cleared values
    delete their key and empty sections are dropped; ``existing`` is untouched."""
    out = {}
    for section, value in (existing or {}).items():
        out[section] = dict(value) if isinstance(value, dict) else value
    for section, fields in clean.items():
        target = out.setdefault(section, {})
        for key, value in fields.items():
            if value in ('', [], 0):
                target.pop(key, None)
            else:
                target[key] = value
        if not target:
            out.pop(section)
    return out
