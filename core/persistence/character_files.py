"""Bind retained character payload files to their SQL authorization identity.

Only direct JSON files inside a validated campaign's character directories are
eligible. Staging files, global stores, and uploaded paths never choose a SQL
identity implicitly. Character payloads remain on disk during PR4B.
"""

import os
from pathlib import Path
import re

from . import runtime


def locator(path):
    from core import storage

    root = Path(os.path.abspath(storage.CAMPAIGNS_DIR))
    target = Path(os.path.abspath(path))
    try:
        parts = target.relative_to(root).parts
    except ValueError:
        return None
    if (len(parts) != 3 or not re.fullmatch(r'[0-9a-f]{32}', parts[0])
            or parts[1] not in {'party_data', 'cosmere_pcs'}
            or not parts[2].endswith('.json')):
        return None
    if target.resolve() != target:
        raise runtime.StoreUnavailable('Character storage path is unsafe')
    return parts


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
    document.setdefault('system', 'pf2e' if store == 'party_data' else 'cosmere')
    if store == 'party_data':
        document.setdefault('schema_version', storage.SCHEMA_VERSION)
    ownership.register_character(
        cid, store, filename, document, owner_user_id=owner_user_id,
        write_document=writer,
    )
