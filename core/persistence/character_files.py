"""Bind retained character payload files to their SQL authorization identity.

Only direct JSON files inside a validated campaign's character directories are
eligible. Staging files, global stores, and uploaded paths never choose a SQL
identity implicitly. Character payloads remain on disk during PR4B.
"""

import os
from pathlib import Path
import re

from . import runtime


# Logical character storage -> path parts below ``campaigns/<cid>/``. The
# storage value is what SQL records; ``dnd5e_characters`` is never a directory.
LEGACY_STORAGE_DIRS = {
    'party_data': ('party_data',),
    'cosmere_pcs': ('cosmere_pcs',),
    'dnd5e_characters': ('dnd5e', 'characters'),
}
_STORAGE_BY_DIRS = {parts: storage for storage, parts in LEGACY_STORAGE_DIRS.items()}

# The only system each storage may hold (mirrors DND5E_LOCATOR_CHECK).
STORAGE_SYSTEMS = {
    'party_data': 'pf2e',
    'cosmere_pcs': 'cosmere',
    'dnd5e_characters': 'dnd5e',
}

DND5E_WRITE_REFUSED = 'D&D characters are not written through the shared adapter until PE-15'


def is_dnd5e_character_id(value):
    """Exact D&D character id: 32 lowercase hex characters, nothing else.

    Deliberately not ``systems.dnd5e.store.valid_id``: its ``re.match`` with
    ``$`` also accepts a trailing newline, which SQL identities refuse.
    """
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) is not None


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


def refuse_dnd5e_write(legacy_storage):
    if legacy_storage == 'dnd5e_characters':
        raise ValueError(DND5E_WRITE_REFUSED)


def locator(path):
    """Return ``(cid, logical_storage, filename)`` for a character payload.

    Only an exact direct file in a known character directory qualifies:
    ``<cid>/party_data/<file>.json``, ``<cid>/cosmere_pcs/<file>.json`` or
    ``<cid>/dnd5e/characters/<32-hex>.json``. Anything else returns None.
    """
    from core import storage

    root = Path(os.path.abspath(storage.CAMPAIGNS_DIR))
    target = Path(os.path.abspath(path))
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 3 or not re.fullmatch(r'[0-9a-f]{32}', parts[0]):
        return None
    legacy_storage = _STORAGE_BY_DIRS.get(tuple(parts[1:-1]))
    filename = parts[-1]
    if legacy_storage is None or not filename.endswith('.json'):
        return None
    if (legacy_storage == 'dnd5e_characters'
            and not is_dnd5e_character_id(filename[:-len('.json')])):
        return None
    if target.resolve() != target:
        raise runtime.StoreUnavailable('Character storage path is unsafe')
    return parts[0], legacy_storage, filename


def authoritative_document(path, document):
    location = locator(path)
    if location is not None:
        from core.character_workflows.recovery import pending_for, repair_required
        pending = pending_for(location[0], store=location[1], filename=location[2])
        if pending is not None:
            if pending.metadata.get('created'):
                return None
            raise repair_required()
    if runtime.backend() == 'json' or not isinstance(document, dict):
        return document
    if location is None:
        if runtime.sql_enabled():
            result = dict(document)
            result.pop('owner_user_ids', None)
            result.update(owner_user_id=None, editor_user_ids=[], viewer_user_ids=[])
            return result
        return document
    from . import ownership

    cid, store, filename = location
    return ownership.authoritative_document(
        cid, document, legacy_storage=store, filename=filename,
    )


def pending_publication(path):
    location = locator(path)
    if location is None:
        return None
    from core.character_workflows.recovery import pending_for
    return pending_for(location[0], store=location[1], filename=location[2])


def assert_available(path):
    if pending_publication(path):
        from core.character_workflows.recovery import repair_required
        raise repair_required()


def write_document(path, document, writer, *, owner_user_id=None):
    """Write file content under a transaction that registers its SQL identity.

    A failed file write rolls back SQL. A later database commit failure can
    leave an unregistered file; SQL authorization then denies it until an
    explicit retry/reconciliation. This is not a distributed transaction.
    """
    location = locator(path)
    if location is not None:
        refuse_dnd5e_write(location[1])
        from core.character_workflows.recovery import pending_for, repair_required
        if pending_for(location[0], store=location[1], filename=location[2]):
            raise repair_required()
    if not runtime.sql_enabled():
        writer(document)
        return
    if location is None:
        writer(document)
        return
    if not isinstance(document, dict):
        raise ValueError('SQL character storage requires one character per file')
    from core import storage
    from . import ownership

    cid, store, filename = location
    document = dict(document)
    document.setdefault('campaign_id', cid)
    document.setdefault('system', storage_system(store))
    if store == 'party_data':
        document.setdefault('schema_version', storage.SCHEMA_VERSION)
    ownership.register_character(
        cid, store, filename, document, owner_user_id=owner_user_id,
        write_document=writer,
    )
