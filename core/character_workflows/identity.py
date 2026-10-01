"""Resolve exact identities without trusting imported locators or SQL grants."""

from pathlib import Path
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from core import storage
from core.persistence import runtime, ownership
from core.persistence.models import Campaign, Character
from core.request_context import campaign_role_for
from .types import CharacterRecord, WorkflowError


def _missing():
    return WorkflowError('character_not_found', 'Character not found.', 404)


def _conflict():
    return WorkflowError('character_identity_conflict',
                         'Character identity is ambiguous or inconsistent.', 409)


def _safe_path(path: Path) -> Path:
    path = path.absolute()
    if path.resolve() != path:
        raise _conflict()
    return path


def _read(path: Path):
    try:
        return json.loads(_safe_path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise _missing() from None
    except (OSError, ValueError, RecursionError):
        raise WorkflowError('character_storage_unavailable',
                            'Character storage is unavailable.', 503) from None


def _resolve(cid, reference, *, by_name, session):
    try:
        storage._check_id(cid, 'campaign_id')
    except ValueError:
        raise _missing() from None
    if (not isinstance(reference, str) or not reference.strip()
            or (not by_name and (len(reference) > 64
                                or any(c in reference for c in '/\\\x00')))):
        raise _missing()
    sql = runtime.sql_enabled()
    if sql and session is None:
        def read():
            with runtime.database().session() as db:
                return _resolve(cid, reference, by_name=by_name, session=db)
        return runtime.store_call(read)
    if sql:
        campaign = session.get(Campaign, cid)
        if campaign is None or campaign.trashed_at is not None:
            raise _missing()
        system = campaign.system
        registry = {row.legacy_file: row for row in session.scalars(
            select(Character).where(Character.campaign_id == cid))}
    else:
        campaign = _read(Path(storage.campaign_file(cid)))
        if not isinstance(campaign, dict) or campaign.get('id') != cid:
            raise _conflict()
        system = campaign.get('system')
        registry = {}
    if system not in {'pf2e', 'cosmere'}:
        raise _conflict()
    store = 'party_data' if system == 'pf2e' else 'cosmere_pcs'
    directory = _safe_path(Path(storage.campaign_dir(cid)) / store)
    matches = []
    identity_files = {}
    try:
        paths = sorted(directory.glob('*.json'))
        for path in paths:
            from .recovery import pending_for, repair_required
            pending = pending_for(cid, store=store, filename=path.name)
            if pending is not None and pending.metadata.get('created'):
                continue
            document = _read(path)
            if not isinstance(document, dict):
                continue
            row = registry.get(path.name)
            stored_id = document.get('id') or (row.id if row is not None else None)
            if isinstance(stored_id, str):
                identity_files.setdefault(stored_id, set()).add(path.name)
            build = document.get('build')
            name = (build.get('name') if isinstance(build, dict) else None) or document.get('name')
            match = name == reference if by_name else (
                document.get('id') == reference or (
                    row is not None and row.id == reference
                    and document.get('id') in (None, '', reference)))
            if match:
                if pending is not None:
                    raise repair_required()
                matches.append((path.name, document, row))
    except OSError:
        raise WorkflowError('character_storage_unavailable',
                            'Character storage is unavailable.', 503) from None
    if not matches:
        if by_name:
            return None
        raise _missing()
    if len(matches) != 1:
        raise _conflict()
    filename, document, row = matches[0]
    selected_id = document.get('id') or (row.id if row is not None else None)
    if len(identity_files.get(selected_id, ())) != 1:
        raise _conflict()
    if document.get('campaign_id') not in (None, '', cid) or document.get('system') not in (None, '', system):
        raise _conflict()
    if sql:
        if (row is None or row.system != system or row.legacy_storage != store
                or document.get('id') not in (None, '', row.id)):
            raise _conflict()
        document = ownership._overlay(session, row, document)
    else:
        document = dict(document)
        document.pop('owner_user_ids', None)
        owner = document.get('owner_user_id')
        if not isinstance(owner, str) or campaign_role_for(campaign, owner) is None:
            owner = None
        document.update(owner_user_id=owner, editor_user_ids=[], viewer_user_ids=[])
    chid = document.get('id')
    if not isinstance(chid, str) or not chid or len(chid) > 64:
        raise _conflict()
    return CharacterRecord(cid, chid, system, store, filename, document,
        document.get('owner_user_id'), frozenset(document.get('editor_user_ids', ())),
        frozenset(document.get('viewer_user_ids', ())))


def resolve_character(campaign_id: str, character_id: str, *,
                      session: Session | None = None) -> CharacterRecord:
    return _resolve(campaign_id, character_id, by_name=False, session=session)


def resolve_legacy_name(campaign_id: str, name: str, *,
                        session: Session | None = None) -> CharacterRecord | None:
    return _resolve(campaign_id, name, by_name=True, session=session)
