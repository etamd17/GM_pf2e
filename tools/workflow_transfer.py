"""Read-only discovery and private writers for the PR5 workflow format.

Keep discovery free of SQL imports and JSON-store transactions: planning an
offline tree must neither require the database driver nor replay a redo journal.
"""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from core.character_workflows.store import decode_draft, decode_receipt
from core.character_workflows.types import WorkflowError

ID = re.compile(r'^[0-9a-f]{32}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
FINGERPRINT = re.compile(r'^(?:v2:)?[0-9a-f]{64}$')


def private_files(root):
    base = Path(root) / 'character_drafts'
    files, links = [], []
    if base.is_symlink():
        return files, [base]
    if not base.exists():
        return files, links
    if not base.is_dir():
        return [base], links
    for directory, dirs, names in os.walk(base, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                links.append(path)
                dirs.remove(name)
        for name in names:
            path = Path(directory) / name
            (links if path.is_symlink() else files).append(path)
    return sorted(files), sorted(links)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp')
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _identifier(value, *, optional=False):
    if optional and value is None:
        return
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ValueError('identifier')


def _draft_row(payload, records):
    draft = decode_draft(payload)
    campaigns = {row['id']: row for row in records['campaigns']}
    memberships = {(row['campaign_id'], row['user_id']) for row in records['campaign_memberships']}
    characters = {row['id']: row for row in records['characters']}
    if (draft.campaign_id not in campaigns
            or (draft.campaign_id, draft.author_id) not in memberships
            or draft.system != campaigns[draft.campaign_id]['system']
            or draft.state not in ('active', 'publishing', 'committed', 'discarded')
            or type(draft.revision) is not int or not 0 <= draft.revision <= 2_147_483_647):
        raise ValueError('draft scope or state')
    if draft.target_id is not None:
        target = characters.get(draft.target_id)
        if not target or (target['campaign_id'], target['system']) != (draft.campaign_id, draft.system):
            raise ValueError('draft target')
    if (draft.base_fingerprint is not None
            and not FINGERPRINT.fullmatch(draft.base_fingerprint)):
        raise ValueError('fingerprint')
    if draft.result is not None and not isinstance(draft.result, dict):
        raise ValueError('result')
    if draft.state in ('active', 'publishing'):
        inputs = draft.inputs
        if (inputs is None or type(inputs.payload_version) is not int or inputs.payload_version != 1
                or inputs.kind not in (draft.system + '_builder', draft.system + '_import')
                or any(not isinstance(v, dict) for v in (inputs.form, inputs.ui, inputs.submission))
                or len(json.dumps(asdict(inputs), ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 2_097_152):
            raise ValueError('inputs')
    elif draft.inputs is not None:
        raise ValueError('terminal draft inputs')
    return dict(id=draft.id, campaign_id=draft.campaign_id, user_id=draft.author_id,
                character_id=draft.target_id,
                kind=draft.inputs.kind if draft.inputs else draft.system + '_builder',
                state=draft.state, revision=draft.revision, payload=payload,
                source_checksum=None, created_at=_timestamp(draft.created_at),
                updated_at=_timestamp(draft.updated_at), expires_at=None)


def _receipt_row(payload):
    receipt = decode_receipt(payload)
    if (not isinstance(receipt.metadata, dict)
            or not HASH.fullmatch(receipt.key_hash) or not HASH.fullmatch(receipt.request_digest)):
        raise ValueError('receipt metadata')
    _identifier(receipt.draft_id, optional=True)
    _identifier(receipt.target_id, optional=True)
    # No parent lookups: historical references never create live grants.
    return asdict(receipt)


def inspect_private(root, records, conflicts):
    from tools import migrate_transactional_store as migration
    files, links = private_files(root)
    def conflict(path, code='invalid_private_workflow'):
        migration._conflict(conflicts, code, entity='character_workflows',
            path=path.relative_to(root).as_posix(),
            message='Private workflow data is inconsistent or needs recovery; source was not changed.')
    for path in links:
        conflict(path, 'unsupported_source_symlink')
    seen_ids, seen_keys = set(), set()
    for path in files:
        parts = path.relative_to(Path(root) / 'character_drafts').parts
        if len(parts) == 2 and parts[1] == '_journal.json':
            conflict(path, 'publication_repair_required')
            continue
        receipt = len(parts) == 4 and parts[1] == '_receipts'
        if not (len(parts) == 3 or receipt):
            conflict(path)
            continue
        cid, author, filename = parts[0], parts[-2], parts[-1]
        if (not ID.fullmatch(cid) or not ID.fullmatch(author)
                or not filename.endswith('.json') or not ID.fullmatch(filename[:-5])):
            conflict(path)
            continue
        payload = migration._read_expected_document(path, root, conflicts, entity='character_workflows')
        if payload is None:
            continue
        try:
            if (not isinstance(payload, dict) or type(payload.get('workflow_version')) is not int
                    or payload.get('workflow_version') != 1
                    or (payload.get('id'), payload.get('campaign_id'), payload.get('author_id'))
                    != (filename[:-5], cid, author)):
                raise ValueError('envelope')
            row = _receipt_row(payload) if receipt else _draft_row(payload, records)
            entity = 'character_workflow_receipts' if receipt else 'character_drafts'
            identity = (entity, row['id'])
            if identity in seen_ids:
                raise ValueError('duplicate id')
            seen_ids.add(identity)
            if receipt:
                key = (cid, author, row['operation'], row['key_hash'])
                if key in seen_keys:
                    raise ValueError('duplicate request key')
                seen_keys.add(key)
            if row['state'] == 'publishing':
                conflict(path, 'publication_repair_required')
            records[entity].append(row)
        except (WorkflowError, TypeError, KeyError, ValueError, OverflowError):
            conflict(path)


def assert_quiescent(records):
    if any(row['state'] == 'publishing' for entity in ('character_drafts', 'character_workflow_receipts')
           for row in records[entity]):
        raise WorkflowError('publication_repair_required',
                            'Recover pending character publications before exporting.', 503)


def _private_write(path, value):
    from tools import migrate_transactional_store as migration
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # mkdir(parents=True) does not apply mode to intermediate directories.
    for parent in (path.parent, *path.parents):
        parent.chmod(0o700)
        if parent.name == 'character_drafts':
            break
    migration._write_json(path, value)
    path.chmod(0o600)


def write_private(staging, records):
    from tools import migrate_transactional_store as migration
    assert_quiescent(records)
    for row in records['character_drafts']:
        payload = row['payload']
        if 'workflow_version' not in payload:
            continue  # Pre-PR5 SQL history remains sidecar-only, as before.
        payload = {**payload, 'id': row['id'], 'campaign_id': row['campaign_id'],
                   'author_id': row['user_id'], 'target_id': row['character_id'],
                   'revision': row['revision'], 'state': row['state']}
        if payload.get('workflow_version') != 1:
            raise migration.MigrationToolError('Unsupported private draft version; export refused')
        path = Path(staging) / 'character_drafts' / row['campaign_id'] / row['user_id'] / (row['id'] + '.json')
        if any(not isinstance(v, str) or not ID.fullmatch(v)
               for v in (row['campaign_id'], row['user_id'], row['id'])):
            raise migration.MigrationToolError('Unsafe private draft identity; export refused')
        _private_write(path, payload)
    for row in records['character_workflow_receipts']:
        if any(not isinstance(v, str) or not ID.fullmatch(v)
               for v in (row['campaign_id'], row['author_id'], row['id'])):
            raise migration.MigrationToolError('Unsafe workflow receipt identity; export refused')
        path = Path(staging) / 'character_drafts' / row['campaign_id'] / '_receipts' / row['author_id'] / (row['id'] + '.json')
        _private_write(path, {'workflow_version': 1, **row})
