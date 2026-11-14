"""Character storage layout and the exact D&D id rule, with no dependencies.

This is the single definition of where each logical character store lives
below ``campaigns/<cid>/`` and which system it may hold. It imports nothing
beyond the standard library so that offline planning
(``tools/migrate_transactional_store.py build_plan``) works without the SQL
packages. ``core.persistence.character_files`` re-exports these names.
"""

import re


# Logical character storage -> path parts below ``campaigns/<cid>/``. The
# storage value is what SQL records; ``dnd5e_characters`` is never a directory.
LEGACY_STORAGE_DIRS = {
    'party_data': ('party_data',),
    'cosmere_pcs': ('cosmere_pcs',),
    'dnd5e_characters': ('dnd5e', 'characters'),
}

# The only system each storage may hold (mirrors DND5E_LOCATOR_CHECK).
STORAGE_SYSTEMS = {
    'party_data': 'pf2e',
    'cosmere_pcs': 'cosmere',
    'dnd5e_characters': 'dnd5e',
}

DND5E_STORAGE = 'dnd5e_characters'

_DND5E_ID = re.compile(r'[0-9a-f]{32}')


def is_dnd5e_character_id(value):
    """Exact D&D character id: 32 lowercase hex characters, nothing else.

    Deliberately not ``systems.dnd5e.store.valid_id``: its ``re.match`` with
    ``$`` also accepts a trailing newline, which SQL identities refuse.
    """
    return isinstance(value, str) and _DND5E_ID.fullmatch(value) is not None


def storage_relative_parts(legacy_storage):
    try:
        return LEGACY_STORAGE_DIRS[legacy_storage]
    except (KeyError, TypeError):
        raise ValueError('unknown character storage') from None


def storage_system(legacy_storage):
    try:
        return STORAGE_SYSTEMS[legacy_storage]
    except (KeyError, TypeError):
        raise ValueError('unknown character storage') from None
