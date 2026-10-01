"""core/campaigns.py -- campaign CRUD, membership, and per-campaign authorization.

Owns campaign metadata, membership, and the live-slot pointer. JSON remains the
default authority; the SQL backend delegates metadata and membership to the
transactional store while character sheets/assets remain files. The in-memory
reload on a live-campaign switch lives in app.load_campaign(); this module never
imports app.
"""
import os
import time
import functools
import threading

from flask import session, request, jsonify, redirect, url_for, abort, g, has_request_context

from core import storage, auth
from core.persistence import runtime as _runtime
from core.persistence import campaign_runtime as _sql_campaigns


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _sql_actor_id():
    if not has_request_context():
        return None
    user = auth.current_user()
    if not user or not user.get('id'):
        raise _sql_campaigns.CampaignPermissionDenied('a valid account is required')
    return user['id']


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
# Per-REQUEST memo, same reasoning as core.auth._load_users: _is_gm() re-enters
# _active_campaign_id() which re-reads the campaign doc, and _is_gm() itself runs
# twice on every GM-gated request. Keyed by cid because one request can touch
# more than one campaign.
_CAMPAIGN_MEMO = '_campaign_memo'
_CAMPAIGN_STORE_LOCK = threading.RLock()


def get_campaign(cid, *, refresh=False):
    if not cid:
        return None
    if _runtime.sql_enabled():
        # SQL security reads are deliberately fresh; a request-local document
        # must not preserve a removed membership or a trashed campaign.
        return _runtime.store_call(_sql_campaigns.get_campaign, cid)
    # Only a real (string) id is memoized. A malformed cid -- a list or dict
    # from a crafted request -- has to fall straight through to the path below,
    # which rejects it. Using it as a dict key raises TypeError and turns a
    # clean rejection into a 500; that is exactly what
    # test_malformed_campaign_id_types_are_rejected caught.
    memoizable = isinstance(cid, str) and has_request_context()
    if memoizable:
        memo = getattr(g, _CAMPAIGN_MEMO, None)
        if memo is None:
            memo = {}
            setattr(g, _CAMPAIGN_MEMO, memo)
        if not refresh and cid in memo:
            return memo[cid]
    doc = storage.load_json(storage.campaign_file(cid))
    doc = _sql_campaigns.compare_shadow(cid, doc)
    if memoizable:
        getattr(g, _CAMPAIGN_MEMO)[cid] = doc
    return doc


def save_campaign(doc):
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.save_campaign, doc, actor_user_id=_sql_actor_id())
    with _CAMPAIGN_STORE_LOCK:
        storage.atomic_write_json(storage.campaign_file(doc['id']), doc)
    if isinstance(doc.get('id'), str) and has_request_context():
        memo = getattr(g, _CAMPAIGN_MEMO, None)
        if memo is not None:
            memo[doc['id']] = doc
    return doc


def list_campaigns():
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.list_campaigns)
    return [c for c in (get_campaign(cid) for cid in storage.list_campaign_ids()) if c]


def create_campaign(name, system, created_by):
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.create_campaign, name, system, created_by)
    cid = storage.new_id()
    storage.ensure_campaign_dirs(cid)
    doc = storage.new_campaign(cid, name, system, created_by, created_at=_now())
    doc['members'] = [storage.campaign_member(created_by, 'gm')]
    return save_campaign(doc)


TRASH_TTL_DAYS = 30   # JSON trash is auto-purged; SQL retains assets until cleanup exists


def delete_campaign(cid):
    """Soft delete and free the live slot. SQL retains files in their original
    location; JSON moves them to trash, restorable for TRASH_TTL_DAYS."""
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.delete_campaign, cid, actor_user_id=_sql_actor_id())
    if storage.get_live_campaign_id() == cid:
        storage.set_live_campaign_id(None)
    doc = get_campaign(cid)
    if doc:
        doc['_trashed_at'] = _now()
        save_campaign(doc)
    storage.trash_campaign_dir(cid)


def get_trashed_campaign(cid):
    if cid and _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.get_campaign, cid, trashed=True)
    return storage.load_json(storage.trashed_campaign_file(cid)) if cid else None


def list_trashed():
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.list_campaigns, trashed=True)
    return [c for c in (get_trashed_campaign(cid) for cid in storage.list_trashed_campaign_ids()) if c]


def trashed_for_user(user_id):
    """Trashed campaigns the user was GM of (so a player can't see/restore others')."""
    return [c for c in list_trashed() if user_role(c, user_id) == 'gm']


def restore_campaign(cid):
    """Restore a trashed campaign. Returns the document, or None."""
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.restore_campaign, cid, actor_user_id=_sql_actor_id())
    if not storage.restore_campaign_dir(cid):
        return None
    doc = get_campaign(cid)
    if doc and '_trashed_at' in doc:
        doc.pop('_trashed_at', None)
        save_campaign(doc)
    return doc


def purge_campaign(cid):
    """Permanently delete a TRASHED campaign and all its data."""
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.purge_campaign, cid)
    storage.purge_campaign_dir(cid)


def purge_expired_trash(ttl_days=TRASH_TTL_DAYS):
    """Permanently remove trashed campaigns older than ttl_days (by trashed-dir
    mtime, which is tz-safe). Returns the number purged. Called lazily on the
    account home so the trash self-cleans without a cron."""
    if _runtime.sql_enabled():
        # SQL soft trash intentionally retains assets; do not run the legacy
        # filesystem-only sweeper against relational campaigns.
        return 0
    cutoff = time.time() - ttl_days * 86400
    n = 0
    for cid in storage.list_trashed_campaign_ids():
        mt = storage.trashed_dir_mtime(cid)
        if mt and mt < cutoff:
            storage.purge_campaign_dir(cid)
            n += 1
    return n


# --------------------------------------------------------------------------
# Membership / roles
# --------------------------------------------------------------------------
def user_role(campaign, user_id):
    if not campaign or not user_id:
        return None
    if _runtime.sql_enabled():
        return _runtime.store_call(
            _sql_campaigns.user_role, campaign.get('id'), user_id,
            trashed=bool(campaign.get('_trashed_at')),
        )
    for m in campaign.get('members', []):
        if m.get('user_id') == user_id:
            return m.get('role')
    return None


def is_gm(campaign, user_id):
    return user_role(campaign, user_id) == 'gm'


def add_member(cid, user_id, role, character_id=None):
    """Add membership or raise its privilege; never demote an existing GM.

    Invite redemption is the only caller. A GM may legitimately follow a
    player or character-specific invite while testing onboarding, and that
    action must not bypass the explicit last-GM safeguards in
    ``set_member_role`` by silently lowering their stored role.
    """
    assert role in ('gm', 'player'), role
    if _runtime.sql_enabled():
        return _runtime.store_call(
            _sql_campaigns.add_member, cid, user_id, role, character_id,
            actor_user_id=_sql_actor_id(),
        )
    with _CAMPAIGN_STORE_LOCK:
        # A join request may have memoized the campaign while validating its
        # invite, before the live-dispatch lock was acquired. Reload inside the
        # read/modify/write lock so a second concurrent invite cannot overwrite
        # the first member with that stale request-local document.
        doc = get_campaign(cid, refresh=True)
        if not doc:
            raise ValueError('no such campaign')
        members = doc.setdefault('members', [])
        existing = next((m for m in members if m.get('user_id') == user_id), None)
        if existing:
            existing['role'] = (
                'gm' if 'gm' in (existing.get('role'), role) else 'player'
            )
            if character_id is not None:
                existing['character_id'] = character_id
        else:
            members.append(storage.campaign_member(user_id, role, character_id))
        return save_campaign(doc)


def gm_count(campaign):
    if _runtime.sql_enabled():
        return _runtime.store_call(_sql_campaigns.gm_count, (campaign or {}).get('id'))
    return sum(1 for m in (campaign or {}).get('members', []) if m.get('role') == 'gm')


def remove_member(cid, user_id):
    """Remove a member from a campaign. Refuses to remove the last GM (a campaign
    must always keep a GM). Returns the updated doc, or None if not removed."""
    if _runtime.sql_enabled():
        return _runtime.store_call(
            _sql_campaigns.remove_member, cid, user_id, actor_user_id=_sql_actor_id(),
        )
    with _CAMPAIGN_STORE_LOCK:
        doc = get_campaign(cid, refresh=True)
        if not doc:
            raise ValueError('no such campaign')
        target = next((m for m in doc.get('members', []) if m.get('user_id') == user_id), None)
        if not target:
            return None
        if target.get('role') == 'gm' and gm_count(doc) <= 1:
            return None   # never strand a campaign without a GM
        doc['members'] = [m for m in doc['members'] if m.get('user_id') != user_id]
        return save_campaign(doc)


def set_member_role(cid, user_id, role):
    """Change a member's role (gm/player). Refuses to demote the last GM."""
    assert role in ('gm', 'player'), role
    if _runtime.sql_enabled():
        return _runtime.store_call(
            _sql_campaigns.set_member_role, cid, user_id, role, actor_user_id=_sql_actor_id(),
        )
    with _CAMPAIGN_STORE_LOCK:
        doc = get_campaign(cid, refresh=True)
        if not doc:
            raise ValueError('no such campaign')
        target = next((m for m in doc.get('members', []) if m.get('user_id') == user_id), None)
        if not target:
            return None
        if target.get('role') == 'gm' and role == 'player' and gm_count(doc) <= 1:
            return None   # don't demote the only GM
        target['role'] = role
        return save_campaign(doc)


def campaigns_for_user(user_id):
    """Campaigns where the user is a member (GM or player) -- for 'My Campaigns'."""
    if _runtime.sql_enabled():
        if not user_id:
            return []
        return _runtime.store_call(_sql_campaigns.list_campaigns, user_id=user_id)
    return [c for c in list_campaigns() if user_role(c, user_id)]


# --------------------------------------------------------------------------
# Characters across campaigns (ownership) -- for 'My Characters' + claim flow
# --------------------------------------------------------------------------
def _character_name(doc):
    # flat-additive envelope: native fields (build/name) live at the top level
    return (doc.get('build') or {}).get('name') or doc.get('name') or '?'


def characters_for_user(user_id):
    if _runtime.sql_enabled():
        from core.persistence import ownership
        return ownership.characters_for_user(user_id)
    out = []
    for c in list_campaigns():
        cid = c['id']
        # PF2e PCs (flat-additive party_data wrappers).
        pdir = storage.party_dir(cid)
        if os.path.isdir(pdir):
            for fn in os.listdir(pdir):
                if not fn.endswith('.json'):
                    continue
                doc = storage.load_json(os.path.join(pdir, fn))
                if storage.is_wrapped(doc) and doc.get('owner_user_id') == user_id:
                    out.append({
                        'campaign_id': cid,
                        'campaign_name': c.get('name'),
                        'system': c.get('system'),
                        'file': fn,
                        'id': doc.get('id'),
                        'name': _character_name(doc),
                    })
        # Cosmere PCs (campaign-scoped cosmere_pcs/ store; name lives at the top).
        cdir = storage.cosmere_pc_dir(cid)
        if os.path.isdir(cdir):
            for fn in os.listdir(cdir):
                if not fn.endswith('.json'):
                    continue
                doc = storage.load_json(os.path.join(cdir, fn))
                if isinstance(doc, dict) and doc.get('owner_user_id') == user_id:
                    out.append({
                        'campaign_id': cid,
                        'campaign_name': c.get('name'),
                        'system': c.get('system') or 'cosmere',
                        'file': fn,
                        'id': doc.get('id'),
                        'name': doc.get('name') or (doc.get('build') or {}).get('name') or '?',
                    })
    return out


def can_act_on_character(user, campaign, char_doc):
    """A user may act on a character if they're admin, the campaign GM, or its owner."""
    if not user:
        return False
    if _runtime.sql_enabled():
        from core.persistence import ownership
        campaign = get_campaign((campaign or {}).get('id'))
        if not campaign or not char_doc:
            return False
        try:
            char_doc = ownership.authoritative_document(
                campaign['id'], char_doc, require_identity=True,
            )
        except ValueError:
            return False
        if not char_doc:
            return False
    if user.get('is_admin') or is_gm(campaign, user['id']):
        return True
    return bool(char_doc) and char_doc.get('owner_user_id') == user['id']


# --------------------------------------------------------------------------
# Live slot
# --------------------------------------------------------------------------
def get_live_campaign_id():
    return storage.get_live_campaign_id()


# --------------------------------------------------------------------------
# Authorization decorators (campaign resolved from route kwarg `cid`, else the
# session's active campaign, else the live slot).
# --------------------------------------------------------------------------
def _resolve_cid(kwargs):
    return kwargs.get('cid') or session.get('active_campaign_id') or get_live_campaign_id()


def _deny(code, msg):
    if request.path.startswith('/api/'):
        return jsonify({'error': msg}), code
    if code == 401:
        return redirect(url_for('login', next=request.path))
    abort(code)


def require_campaign_role(role):
    """`role`='gm' requires GM; 'player' requires any membership (gm or player).
    Site admins always pass."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = auth.current_user()
            if not user:
                return _deny(401, 'login required')
            campaign = get_campaign(_resolve_cid(kwargs))
            r = user_role(campaign, user['id'])
            ok = (r == 'gm') if role == 'gm' else (r in ('gm', 'player'))
            if not ok and not user.get('is_admin'):
                return _deny(403, 'not authorized for this campaign')
            return fn(*args, **kwargs)
        return wrapper
    return deco
