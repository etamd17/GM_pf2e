"""Publish a validated campaign archive with fresh SQL character identities.

The caller owns archive extraction and the private staging directory. SQL owns
identity and access; retained payload files are published before commit, with a
best-effort rename back on failure. A process crash can leave inaccessible files
but cannot confer campaign access without committed relational rows.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
import stat

from sqlalchemy.exc import DataError, IntegrityError

from core import storage
from . import runtime
from .campaign_runtime import _metadata
from .models import AuditEvent, Campaign, Character, User, new_id
from .ownership import _checksum, _validate_locator
from .repositories import CampaignRepository


_CHARACTER_STORES = {'party_data': 'pf2e', 'cosmere_pcs': 'cosmere'}
_ID_PATTERN = re.compile(r'[0-9a-f]{32}')
_GRANTS = {'owner_user_ids', 'editor_user_ids', 'viewer_user_ids'}
_WINDOWS = os.name == 'nt'


def _identity(path):
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return None
    return result.st_dev, result.st_ino


def _paths(staging_root, final_dir, cid):
    root = Path(os.path.abspath(storage.CAMPAIGNS_DIR))
    stage = Path(os.path.abspath(staging_root))
    final = Path(os.path.abspath(final_dir))
    if (root.resolve() != root or stage.resolve() != stage
            or stage.parent != root
            or not stage.name.startswith(f'.campaign-import-{cid}-')
            or final != Path(os.path.abspath(storage.campaign_dir(cid)))):
        raise ValueError('unsafe campaign import paths')
    stage_stat = stage.lstat()
    # Windows directory access is ACL-based; its synthetic POSIX mode bits
    # cannot validate privacy of the caller's tempfile-created staging folder.
    if (not stat.S_ISDIR(stage_stat.st_mode)
            or (not _WINDOWS and stage_stat.st_mode & 0o077)):
        raise ValueError('campaign import staging directory must be private')
    if os.path.lexists(final):
        raise ValueError('campaign import destination already exists')
    return stage, final


def _json_files(stage):
    files = []
    for directory, directories, names in os.walk(stage, followlinks=False):
        parent = Path(directory)
        relative_parent = parent.relative_to(stage)
        if relative_parent.parts and relative_parent.parts[0] in _CHARACTER_STORES and directories:
            raise ValueError('nested character directories are not supported')
        for name in directories + names:
            path = parent / name
            entry = path.lstat()
            if (stat.S_ISLNK(entry.st_mode)
                    or not (stat.S_ISDIR(entry.st_mode) or stat.S_ISREG(entry.st_mode))
                    or (stat.S_ISREG(entry.st_mode) and entry.st_nlink != 1)
                    or '\\' in name or ':' in name or name.endswith((' ', '.'))):
                raise ValueError('unsafe campaign import entry')
            if path.is_file() and name.endswith('.json'):
                files.append(path)
    if stage / 'campaign.json' not in files:
        raise ValueError('campaign import requires campaign.json')
    return sorted(files)


def _load(path):
    def invalid_constant(_value):
        raise ValueError('non-finite numbers are not valid campaign data')
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON keys in campaign data')
            result[key] = value
        return result
    try:
        with path.open(encoding='utf-8') as source:
            return json.load(source, parse_constant=invalid_constant, object_pairs_hook=unique_keys)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError('invalid JSON in campaign import') from None


def _remap(value, ids, *, depth=0):
    if depth > 100:
        raise ValueError('campaign JSON is nested too deeply')
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [_remap(item, ids, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = ids.get(key, key)
            if new_key in result:
                raise ValueError('character identity remapping conflicts with JSON keys')
            if key in _GRANTS:
                result[new_key] = []
            elif key in {'owner_user_id', 'editor_user_id', 'viewer_user_id'}:
                result[new_key] = None
            else:
                result[new_key] = _remap(item, ids, depth=depth + 1)
        return result
    return value


def _prepare(session, stage, paths, campaign_doc, importer_user_id):
    cid = campaign_doc['id']
    character_paths = {}
    old_ids = set()
    for path in paths:
        relative = path.relative_to(stage)
        if relative.parts[0] not in _CHARACTER_STORES:
            continue
        if len(relative.parts) != 2:
            raise ValueError('character files must be directly inside character storage')
        document = _load(path)
        old_id = document.get('id') if isinstance(document, dict) else None
        if not isinstance(old_id, str) or not _ID_PATTERN.fullmatch(old_id):
            raise ValueError('invalid character identity in campaign import')
        if old_id in old_ids:
            raise ValueError('duplicate character identity in campaign import')
        _validate_locator(relative.parts[0], relative.name)
        old_ids.add(old_id)
        character_paths[path] = relative.parts[0]
    ids = {}
    for old_id in sorted(old_ids):
        for _attempt in range(128):
            fresh = new_id()
            if not isinstance(fresh, str) or not _ID_PATTERN.fullmatch(fresh):
                raise ValueError('invalid generated character identity')
            if (fresh not in old_ids and fresh not in ids.values()
                    and session.get(Character, fresh) is None):
                ids[old_id] = fresh
                break
        else:
            raise ValueError('could not allocate a new character identity')

    # Regenerate only from the caller's already-normalized campaign document.
    trusted = _remap(deepcopy(campaign_doc), ids)
    trusted.update(id=cid, created_by=importer_user_id,
                   members=[storage.campaign_member(importer_user_id, 'gm')])
    trusted.pop('_trashed_at', None)
    records = []
    for path in paths:
        document = trusted if path == stage / 'campaign.json' else _remap(_load(path), ids)
        if path in character_paths:
            store = character_paths[path]
            system = _CHARACTER_STORES[store]
            if (system != trusted['system'] or document.get('system') != system
                    or document.get('campaign_id') != cid):
                raise ValueError('character system or campaign does not match the import')
            document.update(owner_user_id=None, editor_user_ids=[], viewer_user_ids=[])
            document.pop('owner_user_ids', None)
            schema = document.get('schema_version')
            if schema is not None and (not isinstance(schema, int) or isinstance(schema, bool)
                                       or not 0 <= schema <= 2147483647):
                raise ValueError('invalid character schema version')
            build = document.get('build')
            name = ((build.get('name') if isinstance(build, dict) else None)
                    or document.get('name') or '?')
            if not isinstance(name, str):
                raise ValueError('character name must be text')
            target = path.parent / (document['id'] + '.json') if store == 'cosmere_pcs' else path
            if target != path and os.path.lexists(target):
                raise ValueError('character import filename collision')
            records.append({
                'character_id': document['id'], 'campaign_id': cid, 'system': system,
                'legacy_storage': store, 'legacy_file': target.name,
                'content_checksum': _checksum(document), 'display_name': name,
                'source_schema_version': schema,
            })
            storage.atomic_write_json(str(target), document)
            if target != path:
                path.unlink()
        else:
            storage.atomic_write_json(str(path), document)
    return trusted, records


def _import(staging_root, final_dir, campaign_doc, importer_user_id):
    if not isinstance(campaign_doc, dict):
        raise ValueError('campaign document must be an object')
    cid = storage._check_id(campaign_doc.get('id'), 'campaign_id')
    if campaign_doc.get('system') not in storage.SUPPORTED_SYSTEMS:
        raise ValueError('unknown campaign system')
    if not isinstance(campaign_doc.get('name'), str) or not campaign_doc['name']:
        raise ValueError('campaign name must be text')
    stage, final = _paths(staging_root, final_dir, cid)
    paths = _json_files(stage)
    stage_identity = _identity(stage)
    reservation_identity = None
    try:
        with runtime.database().transaction() as session:
            if session.get(User, importer_user_id) is None:
                raise ValueError('importer user does not exist')
            if session.get(Campaign, cid) is not None:
                raise ValueError('campaign identity already exists')
            trusted, characters = _prepare(session, stage, paths, campaign_doc, importer_user_id)
            session_number = trusted.get('session_number', 1)
            if (not isinstance(session_number, int) or isinstance(session_number, bool)
                    or not 0 <= session_number <= 2147483647):
                raise ValueError('invalid campaign session number')
            session.add(Campaign(
                id=cid, name=trusted['name'],
                slug=str(trusted.get('slug') or storage.slugify(trusted['name'])),
                system=trusted['system'], created_by_user_id=importer_user_id,
                session_number=session_number, settings=_metadata(trusted),
                source_payload=_metadata(trusted),
            ))
            session.flush()
            repository = CampaignRepository(session)
            repository.upsert_membership(campaign_id=cid, user_id=importer_user_id, role='gm')
            for character in characters:
                # These are new global identities, never an upsert into an
                # existing campaign's character, even on a UUID collision.
                columns = dict(character)
                columns['id'] = columns.pop('character_id')
                session.add(Character(**columns))
            session.add(AuditEvent(
                actor_user_id=importer_user_id, campaign_id=cid,
                action='campaign.imported', target_type='campaign', target_id=cid,
                details={'character_count': len(characters)},
            ))
            session.flush()
            try:
                if _WINDOWS:
                    # Windows rename atomically refuses an existing target;
                    # unlike Unix, replace cannot replace an empty directory.
                    os.rename(stage, final)
                else:
                    # Reserve exclusively before replacing our own empty
                    # directory, never a pre-existing campaign directory.
                    os.mkdir(final, 0o700)
                    reservation_identity = _identity(final)
                    os.replace(stage, final)
            except FileExistsError:
                raise ValueError('campaign import destination already exists') from None
    except Exception:
        # Identify our own directory before compensating. A changed path is
        # left inaccessible for explicit recovery, never overwritten/deleted.
        if _identity(final) == stage_identity and not os.path.lexists(stage):
            if _WINDOWS:
                os.rename(final, stage)
            else:
                os.replace(final, stage)
        elif reservation_identity is not None and _identity(final) == reservation_identity:
            os.rmdir(final)
        raise


def import_staged_campaign(staging_root: str, final_dir: str, campaign_doc: dict,
                           importer_user_id: str) -> None:
    """Insert campaign/grants/characters and publish a caller-owned staged tree."""
    try:
        runtime.store_call(_import, staging_root, final_dir, campaign_doc, importer_user_id)
    except (DataError, IntegrityError):
        raise ValueError('campaign import conflicts with the identity store') from None
