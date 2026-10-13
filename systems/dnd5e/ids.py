"""Stable record ids for 5etools content.

5etools identifies an entity by name + source (its "hash"); every cross
reference in the data (``"shield|xphb"``, ``{@spell fireball|XPHB}``) uses the
same pair. We use the lower-cased ``name|source`` as the primary key, so a
reference string can be turned into an id without a lookup table. Subclasses
need the parent class too: the PHB Berserker exists once for the PHB Barbarian
and once (as a ``_copy``) for the XPHB Barbarian.
"""
from __future__ import annotations

import json
from functools import lru_cache

# Data props whose reference tag has a different name.
_PROP_TO_TAG = {'monster': 'creature', 'optionalfeature': 'optfeature',
                'legendaryGroup': 'legroup', 'baseitem': 'item'}


@lru_cache(maxsize=1)
def _tag_defaults() -> dict:
    """tag name -> default source, exported from 5etools' own tag registry.
    Empty when the rules data isn't installed (see systems/dnd5e/paths.py)."""
    from systems.dnd5e import paths
    try:
        with open(paths.tags_path(), encoding='utf-8') as f:
            return {t['tag']: t.get('defaultSource') or '' for t in json.load(f)}
    except OSError:
        return {}


def reset_tag_cache():
    _tag_defaults.cache_clear()


def default_source(kind_or_tag: str) -> str:
    """The source 5etools assumes when a reference names none
    (``{@spell fireball}`` is the PHB Fireball)."""
    tag = _PROP_TO_TAG.get(kind_or_tag, kind_or_tag)
    return _tag_defaults().get(tag, '')


def entity_id(name: str, source: str | None) -> str:
    return f'{name}|{source or ""}'.lower()


def subclass_id(rec: dict) -> str:
    return '|'.join((rec.get('name') or '', rec.get('source') or '',
                     rec.get('className') or '', rec.get('classSource') or '')).lower()


def parse_ref(ref: str, kind: str = '') -> str:
    """``"leather armor|xphb"`` / ``"Fireball"`` -> entity id."""
    parts = (ref or '').split('|')
    name = parts[0].strip()
    source = parts[1].strip() if len(parts) > 1 and parts[1].strip() else default_source(kind)
    return entity_id(name, source)
