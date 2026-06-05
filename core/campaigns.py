"""core/campaigns.py -- campaign CRUD, membership, and per-campaign authorization.

Owns campaign DATA (campaign.json, members, the live-slot pointer). The
in-memory reload on a live-campaign switch lives in app.load_campaign(); this
module never imports app. Authorization primitives live here because they read
campaign membership.
"""
import os
import time
import functools

from flask import session, request, jsonify, redirect, url_for, abort

from core import storage, auth


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------
def get_campaign(cid):
    return storage.load_json(storage.campaign_file(cid)) if cid else None


def save_campaign(doc):
    storage.atomic_write_json(storage.campaign_file(doc['id']), doc)
    return doc


def list_campaigns():
    return [c for c in (get_campaign(cid) for cid in storage.list_campaign_ids()) if c]


def create_campaign(name, system, created_by):
    cid = storage.new_id()
    storage.ensure_campaign_dirs(cid)
    doc = storage.new_campaign(cid, name, system, created_by, created_at=_now())
    doc['members'] = [storage.campaign_member(created_by, 'gm')]
    return save_campaign(doc)


# --------------------------------------------------------------------------
# Membership / roles
# --------------------------------------------------------------------------
def user_role(campaign, user_id):
    if not campaign or not user_id:
        return None
    for m in campaign.get('members', []):
        if m.get('user_id') == user_id:
            return m.get('role')
    return None


def is_gm(campaign, user_id):
    return user_role(campaign, user_id) == 'gm'


def add_member(cid, user_id, role, character_id=None):
    assert role in ('gm', 'player'), role
    doc = get_campaign(cid)
    if not doc:
        raise ValueError('no such campaign')
    members = doc.setdefault('members', [])
    existing = next((m for m in members if m.get('user_id') == user_id), None)
    if existing:
        existing['role'] = role
        if character_id is not None:
            existing['character_id'] = character_id
    else:
        members.append(storage.campaign_member(user_id, role, character_id))
    return save_campaign(doc)


def campaigns_for_user(user_id):
    """Campaigns where the user is a member (GM or player) -- for 'My Campaigns'."""
    return [c for c in list_campaigns() if user_role(c, user_id)]


# --------------------------------------------------------------------------
# Characters across campaigns (ownership) -- for 'My Characters' + claim flow
# --------------------------------------------------------------------------
def _character_name(doc):
    # flat-additive envelope: native fields (build/name) live at the top level
    return (doc.get('build') or {}).get('name') or doc.get('name') or '?'


def characters_for_user(user_id):
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


def claim_character(cid, file_name, user_id):
    """Set owner_user_id on a campaign character file (invite-code claim flow)."""
    path = os.path.join(storage.party_dir(cid), os.path.basename(file_name))
    doc = storage.load_json(path)
    if not storage.is_wrapped(doc):
        raise ValueError('character not found')
    doc['owner_user_id'] = user_id
    storage.atomic_write_json(path, doc, indent=4)
    return doc


def can_act_on_character(user, campaign, char_doc):
    """A user may act on a character if they're admin, the campaign GM, or its owner."""
    if not user:
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
