"""SQL authority behind the legacy campaign API; no filesystem authority reads.

Campaign sheets/assets remain files. Soft deletion changes only SQL visibility
and deliberately retains those files; permanent deletion needs a durable file
cleanup workflow and is not offered by this adapter.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
import shutil
import tempfile

from sqlalchemy import func, select

from core import storage
from . import runtime
from .models import AuditEvent, Campaign, CampaignMembership, Character, User, utc_now
from .repositories import CampaignRepository
from .services import LastGameMasterError, TransactionalStore


_IDENTITY_FIELDS = {
    "schema_version", "id", "slug", "name", "system", "created_by",
    "created_at", "session_number", "members", "_trashed_at",
}


class CampaignPermissionDenied(ValueError):
    """A request actor no longer has campaign administration authority."""


class UnsupportedCampaignOperation(ValueError):
    """The requested campaign lifecycle operation is not yet supported in SQL."""


def _metadata(doc):
    return {key: deepcopy(value) for key, value in doc.items() if key not in _IDENTITY_FIELDS}


def _timestamp(value):
    return value.replace(tzinfo=None).isoformat(timespec="seconds") if value else None


def _document(session, campaign):
    # Imported snapshots may contain old memberships or forged identity fields.
    # Only non-authority extras can survive; relational columns always win.
    doc = _metadata(campaign.source_payload or {})
    doc.update(_metadata(campaign.settings or {}))
    members = session.scalars(
        select(CampaignMembership)
        .where(CampaignMembership.campaign_id == campaign.id)
        .order_by(CampaignMembership.joined_at, CampaignMembership.user_id)
    ).all()
    doc.update({
        "schema_version": storage.SCHEMA_VERSION,
        "id": campaign.id,
        "slug": campaign.slug,
        "name": campaign.name,
        "system": campaign.system,
        "created_by": campaign.created_by_user_id,
        "created_at": _timestamp(campaign.created_at),
        "session_number": campaign.session_number,
        "members": [storage.campaign_member(m.user_id, m.role, m.character_id) for m in members],
    })
    doc.setdefault("system_config", {})
    if campaign.trashed_at is not None:
        doc["_trashed_at"] = _timestamp(campaign.trashed_at)
    return doc


def get_campaign(cid, *, trashed=False):
    storage._check_id(cid, "campaign_id")
    with runtime.database().session() as session:
        campaign = session.get(Campaign, cid)
        if campaign is None or (campaign.trashed_at is not None) != trashed:
            return None
        return _document(session, campaign)


def _comparison_timestamp(value):
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value.strip())
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec='seconds')
    except (ValueError, OverflowError):
        return value


def _comparison_document(document):
    if not isinstance(document, dict):
        return document
    metadata = _metadata(document)
    metadata.setdefault('system_config', {})
    members = document.get('members', [])
    if isinstance(members, list):
        # Keep duplicate memberships visible: sorting must not collapse two
        # conflicting role/character grants for the same account.
        members = sorted((
            {key: member.get(key) for key in ('user_id', 'role', 'character_id')}
            if isinstance(member, dict) else member
            for member in members
        ), key=repr)
    return {
        'id': document.get('id'),
        'slug': document.get('slug') or '',
        'name': document.get('name') or '',
        'system': document.get('system') or 'pf2e',
        'created_by': document.get('created_by'),
        'created_at': _comparison_timestamp(document.get('created_at') or '1970-01-01T00:00:00Z'),
        'trashed_at': _comparison_timestamp(document.get('_trashed_at')),
        'session_number': document.get('session_number', 1),
        'members': members,
        'settings': metadata,
    }


def compare_shadow(cid, legacy):
    """Compare campaign state independently of legacy serialization details."""
    if runtime.backend() == 'shadow':
        runtime.compare_shadow(
            'campaign.get', _comparison_document(legacy),
            lambda: _comparison_document(get_campaign(cid)),
        )
    return legacy


def list_campaigns(*, trashed=False, user_id=None):
    with runtime.database().session() as session:
        statement = select(Campaign).where(
            Campaign.trashed_at.is_not(None) if trashed else Campaign.trashed_at.is_(None)
        ).order_by(Campaign.created_at, Campaign.id)
        if user_id is not None:
            statement = statement.join(
                CampaignMembership, CampaignMembership.campaign_id == Campaign.id
            ).where(CampaignMembership.user_id == user_id)
        return [_document(session, campaign) for campaign in session.scalars(statement)]


def create_campaign(name, system, created_by):
    cid = storage.new_id()
    doc = storage.new_campaign(cid, name, system, created_by)
    with runtime.database().transaction() as session:
        if session.get(User, created_by) is None:
            raise ValueError("creator user does not exist")
        repo = CampaignRepository(session)
        campaign = repo.upsert(
            campaign_id=cid, slug=doc["slug"], name=name, system=system,
            created_by_user_id=created_by, settings=_metadata(doc),
            source_payload=_metadata(doc),
        )
        repo.upsert_membership(campaign_id=cid, user_id=created_by, role="gm")
        session.add(AuditEvent(
            actor_user_id=created_by, campaign_id=cid, action="campaign.created",
            target_type="campaign", target_id=cid, details={"system": system},
        ))
        # Directory creation is non-destructive. A failed transaction can only
        # leave empty directories, never an authoritative orphan campaign.
        storage.ensure_campaign_dirs(cid)
        return _document(session, campaign)


def _locked_campaign(session, cid, *, trashed=False):
    storage._check_id(cid, "campaign_id")
    campaign = CampaignRepository(session).get(cid, for_update=True)
    if campaign is None or (campaign.trashed_at is not None) != trashed:
        raise ValueError("no such campaign")
    return campaign


def _require_actor(session, cid, actor_user_id):
    """Recheck route authorization after taking the shared campaign lock.

    Trusted internal callers may omit an actor. HTTP callers always pass the
    validated account ID; a concurrent GM demotion cannot leave stale authority
    between a route's initial check and its mutation.
    """
    if actor_user_id is None:
        return
    actor = session.get(User, actor_user_id)
    if actor is None:
        raise CampaignPermissionDenied("actor user does not exist")
    if actor.is_admin:
        return
    membership = TransactionalStore._locked_membership(session, cid, actor_user_id)
    if membership is None or membership.role != "gm":
        raise CampaignPermissionDenied("campaign GM permission is required")


def save_campaign(doc, *, actor_user_id=None):
    with runtime.database().transaction() as session:
        campaign = _locked_campaign(session, doc["id"])
        _require_actor(session, campaign.id, actor_user_id)
        system = doc.get("system", campaign.system)
        if system not in storage.SUPPORTED_SYSTEMS:
            raise ValueError("unknown campaign system")
        campaign.system = system
        campaign.name = str(doc.get("name", campaign.name))
        campaign.slug = str(doc.get("slug", campaign.slug))
        session_number = int(doc.get("session_number", campaign.session_number))
        if session_number < 0:
            raise ValueError("session number must not be negative")
        campaign.session_number = session_number
        campaign.settings = _metadata(doc)
        campaign.source_payload = _metadata(doc)
        campaign.source_checksum = None
        # Members, creator, timestamps, and trash state cannot be replaced by
        # a request-local campaign document from before a concurrent mutation.
        session.flush()
        return _document(session, campaign)


def user_role(cid, user_id, *, trashed=False):
    if not cid or not user_id:
        return None
    storage._check_id(cid, "campaign_id")
    with runtime.database().session() as session:
        return session.scalar(select(CampaignMembership.role).join(
            Campaign, Campaign.id == CampaignMembership.campaign_id,
        ).where(
            CampaignMembership.campaign_id == cid,
            CampaignMembership.user_id == user_id,
            Campaign.trashed_at.is_not(None) if trashed else Campaign.trashed_at.is_(None),
        ))


def gm_count(cid):
    if not cid:
        return 0
    storage._check_id(cid, "campaign_id")
    with runtime.database().session() as session:
        return int(session.scalar(select(func.count()).select_from(CampaignMembership)
            .join(Campaign, Campaign.id == CampaignMembership.campaign_id)
            .where(CampaignMembership.campaign_id == cid,
                   CampaignMembership.role == "gm", Campaign.trashed_at.is_(None))) or 0)


def add_member(cid, user_id, role, character_id=None, *, actor_user_id=None):
    if role not in {"gm", "player"}:
        raise ValueError("membership role must be gm or player")
    with runtime.database().transaction() as session:
        campaign = _locked_campaign(session, cid)
        _require_actor(session, cid, actor_user_id)
        if session.get(User, user_id) is None:
            raise ValueError("user does not exist")
        if character_id is not None:
            character = session.scalar(select(Character).where(
                Character.id == character_id, Character.campaign_id == cid,
            ))
            if character is None:
                raise ValueError("character does not exist in campaign")
        existing = TransactionalStore._locked_membership(session, cid, user_id)
        existing_role = existing.role if existing is not None else None
        target_role = "gm" if "gm" in (existing_role, role) else "player"
        CampaignRepository(session).upsert_membership(
            campaign_id=cid, user_id=user_id, role=target_role,
            character_id=(character_id if character_id is not None else
                          existing.character_id if existing is not None else None),
        )
        session.add(AuditEvent(
            actor_user_id=actor_user_id,
            campaign_id=cid, action="membership.added" if existing is None else "membership.updated",
            target_type="user", target_id=user_id,
            details={"from": existing_role, "to": target_role, "character_id": character_id},
        ))
        return _document(session, campaign)


def _change_membership(cid, user_id, *, role=None, remove=False, actor_user_id=None):
    with runtime.database().transaction() as session:
        campaign = _locked_campaign(session, cid)
        _require_actor(session, cid, actor_user_id)
        member = TransactionalStore._locked_membership(session, cid, user_id)
        if member is None:
            return None
        if member.role == "gm" and (remove or role == "player"):
            try:
                TransactionalStore._reject_if_last_gm(session, cid)
            except LastGameMasterError:
                return None
        previous = member.role
        if remove:
            from core.character_workflows.lifecycle import invalidate_member, sql_transaction
            tx = sql_transaction(session, cid)
            invalidate_member(tx.context, user_id, transaction=tx)
            session.delete(member)
        else:
            member.role = role
            member.updated_at = utc_now()
        session.add(AuditEvent(
            actor_user_id=actor_user_id,
            campaign_id=cid,
            action="membership.removed" if remove else "membership.role_changed",
            target_type="user", target_id=user_id,
            details={"from": previous, "to": role},
        ))
        session.flush()
        return _document(session, campaign)


def remove_member(cid, user_id, *, actor_user_id=None):
    return _change_membership(cid, user_id, remove=True, actor_user_id=actor_user_id)


def set_member_role(cid, user_id, role, *, actor_user_id=None):
    if role not in {"gm", "player"}:
        raise ValueError("membership role must be gm or player")
    return _change_membership(cid, user_id, role=role, actor_user_id=actor_user_id)


def delete_campaign(cid, *, actor_user_id=None):
    with runtime.database().transaction() as session:
        campaign = _locked_campaign(session, cid)
        _require_actor(session, cid, actor_user_id)
        from core.character_workflows.lifecycle import assert_no_pending, sql_transaction
        assert_no_pending(sql_transaction(session, cid))
        campaign.trashed_at = utc_now()
        session.add(AuditEvent(actor_user_id=actor_user_id, campaign_id=cid, action="campaign.trashed",
                              target_type="campaign", target_id=cid, details={}))
    if storage.get_live_campaign_id() == cid:
        storage.set_live_campaign_id(None)


def restore_campaign(cid, *, actor_user_id=None):
    storage._check_id(cid, "campaign_id")
    with runtime.database().transaction() as session:
        campaign = CampaignRepository(session).get(cid, for_update=True)
        if campaign is None or campaign.trashed_at is None:
            return None
        _require_actor(session, cid, actor_user_id)
        # PR4A imported old trash from campaigns_trash. Copy before activating
        # it, retaining the source if a database commit or the process fails.
        # SQL-created trash already retains its active-path files.
        source, destination = storage.campaign_trash_dir(cid), storage.campaign_dir(cid)
        if os.path.isdir(source) and not os.path.exists(destination):
            os.makedirs(storage.CAMPAIGNS_DIR, exist_ok=True)
            staged = tempfile.mkdtemp(prefix=f".{cid}.restore-", dir=storage.CAMPAIGNS_DIR)
            try:
                shutil.copytree(source, staged, dirs_exist_ok=True)
                os.rename(staged, destination)
            finally:
                if os.path.exists(staged):
                    shutil.rmtree(staged)
        storage.ensure_campaign_dirs(cid)
        campaign.trashed_at = None
        session.add(AuditEvent(actor_user_id=actor_user_id, campaign_id=cid, action="campaign.restored",
                              target_type="campaign", target_id=cid, details={}))
        session.flush()
        return _document(session, campaign)


def purge_campaign(cid):
    storage._check_id(cid, "campaign_id")
    raise UnsupportedCampaignOperation(
        "Permanent SQL campaign deletion is unavailable; campaign assets are retained."
    )
