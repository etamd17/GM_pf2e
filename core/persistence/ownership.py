"""SQL authority for character identities while sheet payloads remain files.

Callers supply campaign-scoped locators; names and payload ownership fields are
never evidence of ownership. File callbacks run before SQL commit, so a failed
write rolls back SQL changes. A later SQL commit failure cannot undo a completed
file replacement: this adapter does not promise a cross-store transaction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from sqlalchemy import select

from . import runtime
from .models import (
    AuditEvent,
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Invitation,
    InviteRedemption,
    User,
    new_id,
    utc_now,
)


def _checksum(document):
    return hashlib.sha256(json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _validate_locator(legacy_storage, filename):
    if legacy_storage not in {"party_data", "cosmere_pcs"}:
        raise ValueError("unknown character storage")
    if (not isinstance(filename, str) or not filename.endswith(".json")
            or filename in {".json", "..json"}
            or any(char in filename for char in ("/", "\\", "\x00"))):
        raise ValueError("character filename must be one JSON basename")


def _no_grants(document):
    result = dict(document)
    result.pop("owner_user_ids", None)
    result.update(owner_user_id=None, editor_user_ids=[], viewer_user_ids=[])
    return result


def _overlay(session, character, document):
    result = _no_grants(document)
    result.update(id=character.id, campaign_id=character.campaign_id,
                  system=character.system)
    assignments = session.scalars(
        select(CharacterAssignment)
        .join(CampaignMembership, (
            (CampaignMembership.campaign_id == CharacterAssignment.campaign_id)
            & (CampaignMembership.user_id == CharacterAssignment.user_id)
        ))
        .where(CharacterAssignment.campaign_id == character.campaign_id,
               CharacterAssignment.character_id == character.id)
        .order_by(CharacterAssignment.user_id)
    )
    for assignment in assignments:
        if assignment.role == "owner":
            result["owner_user_id"] = assignment.user_id
        elif assignment.role == "editor":
            result["editor_user_ids"].append(assignment.user_id)
        elif assignment.role == "viewer":
            result["viewer_user_ids"].append(assignment.user_id)
    return result


def _resolve_identity(session, cid, document, legacy_storage=None, filename=None):
    declared_campaign = document.get("campaign_id")
    if declared_campaign not in (None, "", cid):
        raise ValueError("character campaign does not match the scoped campaign")
    if legacy_storage is not None or filename is not None:
        _validate_locator(legacy_storage, filename)
    character_id = document.get("id")
    if character_id is not None and character_id != "":
        if not isinstance(character_id, str) or len(character_id) > 64:
            raise ValueError("invalid character identity")
        character = session.get(Character, character_id)
    elif legacy_storage is not None:
        character = session.scalar(select(Character).where(
            Character.campaign_id == cid,
            Character.legacy_storage == legacy_storage,
            Character.legacy_file == filename,
        ))
    else:
        character = None
    if character is not None:
        if character.campaign_id != cid:
            raise ValueError("character identity belongs to another campaign")
        if legacy_storage is not None and (
            character.legacy_storage != legacy_storage or character.legacy_file != filename
        ):
            raise ValueError("character identity does not match its scoped locator")
        if document.get("system") not in (None, "", character.system):
            raise ValueError("character system does not match its SQL identity")
    return character


def _authoritative_document(cid, document, *, legacy_storage=None, filename=None,
                            require_identity=False):
    with runtime.database().session() as session:
        character = _resolve_identity(session, cid, document, legacy_storage, filename)
        campaign = session.get(Campaign, cid)
        if character is None or campaign is None or campaign.trashed_at is not None:
            if require_identity:
                raise ValueError("character identity does not exist in an active campaign")
            return _no_grants(document)
        return _overlay(session, character, document)


def authoritative_document(cid, doc: dict, *, legacy_storage=None, filename=None,
                           require_identity=False) -> dict:
    """Overlay SQL grants, or observe them without changing legacy behavior.

    Missing identities fail closed with empty grants. Security checks which must
    reject unknown identities even for a GM may request ``require_identity``.
    Explicit locators resolve only documents without an ID; a forged nonempty
    ID is never silently replaced by the character at that locator.
    """
    if not isinstance(doc, dict):
        raise ValueError("character document must be an object")
    mode = runtime.backend()
    if mode == "json":
        return doc
    def read():
        return runtime.store_call(
            _authoritative_document, cid, doc, legacy_storage=legacy_storage,
            filename=filename, require_identity=require_identity,
        )
    if mode == "shadow":
        # Only compare authority, so gameplay payload differences cannot drown
        # out relevant drift. compare_shadow logs neither payload nor user IDs.
        keys = ("owner_user_id", "editor_user_ids", "viewer_user_ids")
        def grants(document):
            return {key: document.get(key, None if key == "owner_user_id" else [])
                    for key in keys}
        runtime.compare_shadow("character.authority", grants(doc), lambda: grants(read()))
        return doc
    return read()


def characters_for_user(user_id) -> list[dict]:
    """Read owned character cards exclusively through active SQL membership."""
    from core.character_workflows.recovery import pending_for
    def read():
        with runtime.database().session() as session:
            rows = session.execute(
                select(Character, Campaign)
                .join(Campaign, Campaign.id == Character.campaign_id)
                .join(CharacterAssignment, (
                    (CharacterAssignment.campaign_id == Character.campaign_id)
                    & (CharacterAssignment.character_id == Character.id)
                ))
                .join(CampaignMembership, (
                    (CampaignMembership.campaign_id == CharacterAssignment.campaign_id)
                    & (CampaignMembership.user_id == CharacterAssignment.user_id)
                ))
                .where(CharacterAssignment.user_id == user_id,
                       CharacterAssignment.role == "owner", Campaign.trashed_at.is_(None))
                .order_by(Campaign.id, Character.legacy_file, Character.id)
            )
            return [{"campaign_id": campaign.id, "campaign_name": campaign.name,
                     "system": character.system, "file": character.legacy_file,
                     "id": character.id, "name": character.display_name or "?"}
                    for character, campaign in rows
                    if not pending_for(campaign.id, character_id=character.id)]
    return runtime.store_call(read)


def _lock_campaign(session, cid):
    campaign = session.scalar(select(Campaign).where(Campaign.id == cid).with_for_update())
    if campaign is None or campaign.trashed_at is not None:
        raise ValueError("campaign does not exist or is trashed")
    return campaign


def _lock_character(session, cid, character_id):
    character = session.scalar(select(Character).where(
        Character.campaign_id == cid, Character.id == character_id,
    ).with_for_update())
    if character is None:
        raise ValueError("character does not exist in this campaign")
    return character


def _audit(session, cid, character_id, action, actor_user_id=None, **details):
    session.add(AuditEvent(
        actor_user_id=actor_user_id, campaign_id=cid, action=action,
        target_type="character", target_id=character_id, details=details,
    ))


def _set_payload_metadata(character, document):
    schema_version = document.get("schema_version")
    if schema_version is not None and (
        not isinstance(schema_version, int) or isinstance(schema_version, bool)
        or not 0 <= schema_version <= 2147483647
    ):
        raise ValueError("invalid character schema version")
    build = document.get("build")
    name = ((build.get("name") if isinstance(build, dict) else None)
            or document.get("name") or "?")
    if not isinstance(name, str):
        raise ValueError("character name must be text")
    character.source_schema_version = schema_version
    character.display_name = name
    character.content_checksum = _checksum(document)


def register_character(cid, legacy_storage, filename, doc: dict, *,
                       owner_user_id=None,
                       write_document: Callable[[dict], None] | None = None) -> dict:
    """Create/update identity and checksum without trusting payload grants.

    ``owner_user_id`` is a trusted caller decision for a NEW identity only.
    Existing ownership (including an unowned/released identity) never changes.
    ``write_document`` receives the normalized payload inside the SQL transaction.
    """
    if not runtime.sql_enabled():
        if write_document is not None:
            write_document(doc)
        return doc
    _validate_locator(legacy_storage, filename)
    if not isinstance(doc, dict):
        raise ValueError("character document must be an object")

    def register():
        with runtime.database().transaction() as session:
            result = register_character_in_session(
                session, cid, legacy_storage, filename, doc, owner_user_id=owner_user_id)
            if write_document is not None:
                write_document(result)
            session.flush()
            return result
    return runtime.store_call(register)


def register_character_in_session(session, cid, legacy_storage, filename, doc: dict, *,
                                  owner_user_id=None) -> dict:
    """Register metadata in the caller's transaction; never write or commit."""
    _validate_locator(legacy_storage, filename)
    if not isinstance(doc, dict):
        raise ValueError("character document must be an object")
    campaign = _lock_campaign(session, cid)
    character = _resolve_identity(session, cid, doc, legacy_storage, filename)
    by_locator = session.scalar(select(Character).where(
        Character.campaign_id == cid, Character.legacy_storage == legacy_storage,
        Character.legacy_file == filename,
    ).with_for_update())
    if by_locator is not None and character is None:
        raise ValueError("character identity conflicts with the existing locator")
    created = character is None
    if created:
        expected_system = "pf2e" if legacy_storage == "party_data" else "cosmere"
        system = doc.get("system") or campaign.system
        if system != campaign.system or system != expected_system:
            raise ValueError("character system does not match campaign and storage")
        character = Character(
            id=doc.get("id") or new_id(), campaign_id=cid, system=system,
            legacy_storage=legacy_storage, legacy_file=filename,
            content_checksum="0" * 64,
        )
        session.add(character)
        session.flush()
        if owner_user_id is not None:
            membership = session.scalar(select(CampaignMembership).where(
                CampaignMembership.campaign_id == cid,
                CampaignMembership.user_id == owner_user_id,
            ).with_for_update())
            if membership is None:
                raise ValueError("campaign membership is required for the owner")
            session.add(CharacterAssignment(
                campaign_id=cid, character_id=character.id,
                user_id=owner_user_id, role="owner",
            ))
            membership.character_id = character.id
        _audit(session, cid, character.id, "character.created", owner_user_id)
    else:
        character = _lock_character(session, cid, character.id)
    session.flush()
    result = _overlay(session, character, doc)
    _set_payload_metadata(character, result)
    session.flush()
    return result


def write_character_batch(entries, *, write_documents):
    """Coordinate a file batch with one transaction for existing SQL identities.

    Each entry is ``(campaign_id, storage, filename, document)``. The callback
    receives normalized documents in the original order and its return value is
    forwarded. It must use the application's journaled file writer; a callback
    failure rolls back every SQL metadata update. The same file/commit gap as
    ``register_character`` remains if SQL commit fails after file publication.
    """
    entries = list(entries)
    if not runtime.sql_enabled():
        return write_documents([entry[3] for entry in entries])

    def write():
        with runtime.database().transaction() as session:
            for cid in sorted({entry[0] for entry in entries}):
                _lock_campaign(session, cid)
            normalized = []
            seen_ids = set()
            for cid, legacy_storage, filename, document in entries:
                _validate_locator(legacy_storage, filename)
                if not isinstance(document, dict):
                    raise ValueError("character document must be an object")
                character = _resolve_identity(session, cid, document, legacy_storage, filename)
                if character is None:
                    raise ValueError("character batch requires an existing SQL identity")
                if character.id in seen_ids:
                    raise ValueError("duplicate character identity in batch")
                seen_ids.add(character.id)
                character = _lock_character(session, cid, character.id)
                result = _overlay(session, character, document)
                _set_payload_metadata(character, result)
                normalized.append(result)
            session.flush()
            return write_documents(normalized)
    return runtime.store_call(write)


def _actor_is_gm(session, cid, actor_user_id):
    user = session.get(User, actor_user_id) if actor_user_id else None
    if user is None:
        return False
    membership = session.get(CampaignMembership, (cid, actor_user_id))
    return user.is_admin or (membership is not None and membership.role == "gm")


def release_character(cid, character_id, actor_user_id) -> None:
    """A GM/admin releases the owner; editors, viewers and other links survive."""
    def release():
        with runtime.database().transaction() as session:
            _lock_campaign(session, cid)
            character = _lock_character(session, cid, character_id)
            if not _actor_is_gm(session, cid, actor_user_id):
                raise ValueError("GM or administrator required to release a character")
            owner = session.scalar(select(CharacterAssignment).where(
                CharacterAssignment.campaign_id == cid,
                CharacterAssignment.character_id == character.id,
                CharacterAssignment.role == "owner",
            ).with_for_update())
            previous_owner = owner.user_id if owner is not None else None
            if owner is not None:
                membership = session.get(CampaignMembership, (cid, owner.user_id))
                if membership is not None and membership.character_id == character_id:
                    membership.character_id = None
                session.delete(owner)
            _audit(session, cid, character_id, "character.released", actor_user_id,
                   previous_owner_user_id=previous_owner)
    runtime.store_call(release)


def delete_character(cid, character_id, actor_user_id, *,
                     delete_document: Callable[[], None] | None = None) -> None:
    """Delete identity, detach historical links and revoke its old join codes."""
    def delete():
        with runtime.database().transaction() as session:
            _lock_campaign(session, cid)
            _lock_character(session, cid, character_id)
            owner = session.scalar(select(CharacterAssignment).where(
                CharacterAssignment.campaign_id == cid,
                CharacterAssignment.character_id == character_id,
                CharacterAssignment.user_id == actor_user_id,
                CharacterAssignment.role == "owner",
            ))
            if owner is None and not _actor_is_gm(session, cid, actor_user_id):
                raise ValueError("not allowed to delete this character")
            from core.character_workflows.lifecycle import invalidate_target, sql_transaction
            tx = sql_transaction(session, cid)
            invalidate_target(tx.context, character_id, transaction=tx)
            for membership in session.scalars(select(CampaignMembership).where(
                CampaignMembership.campaign_id == cid,
                CampaignMembership.character_id == character_id,
            )):
                membership.character_id = None
            for invitation in session.scalars(select(Invitation).where(
                Invitation.campaign_id == cid, Invitation.character_id == character_id,
            )):
                invitation.character_id = None
                invitation.revoked_at = utc_now()
                invitation.source_payload = {**invitation.source_payload,
                                             "character_id": None}
                invitation.source_checksum = _checksum(invitation.source_payload)
            for redemption in session.scalars(select(InviteRedemption).where(
                InviteRedemption.campaign_id == cid,
                InviteRedemption.character_id == character_id,
            )):
                redemption.character_id = None
                redemption.details = {**redemption.details, "deleted_character_id": character_id}
            session.flush()
            character = session.get(Character, character_id)
            session.delete(character)
            _audit(session, cid, character_id, "character.deleted", actor_user_id)
            session.flush()
            if delete_document is not None:
                delete_document()
    runtime.store_call(delete)
