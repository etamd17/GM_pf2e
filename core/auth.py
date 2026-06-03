"""core/auth.py -- accounts, passwords, sessions, and invite codes.

The identity layer for the multi-campaign platform. Standalone: it uses
flask.session + werkzeug + core.storage and does NOT import app (so app can
import it freely). Per-campaign ROLE authorization (require_campaign_role,
require_owner_or_gm) lives in core.campaigns, which knows campaign membership.

Stores:
    users.json     {"users": {user_id: {id, username, display_name,
                                         password_hash, is_admin,
                                         created_at, last_login}}}
    invites.json   {"invites": {CODE: {code, campaign_id, role, character_id,
                                       creates_account, uses_left, created_by,
                                       expires_at}}}
"""
import os
import time
import secrets
import functools

from flask import session, request, jsonify, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from core import storage

INVITES_FILE = os.path.join(storage.DATA_DIR, 'invites.json')
REMEMBER_DAYS = 60
# scrypt (Werkzeug's newer default) isn't available on all Python builds; pbkdf2
# uses hashlib.pbkdf2_hmac, which always is. check_password_hash auto-detects.
_PW_METHOD = 'pbkdf2:sha256'
_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no ambiguous 0/O/1/I


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------
def _load_users():
    return storage.load_json(storage.USERS_FILE, default={'users': {}}) or {'users': {}}


def _save_users(data):
    storage.atomic_write_json(storage.USERS_FILE, data)


def get_user(user_id):
    if not user_id:
        return None
    return _load_users()['users'].get(user_id)


def get_user_by_username(username):
    uname = (username or '').strip().lower()
    if not uname:
        return None
    for u in _load_users()['users'].values():
        if u['username'].lower() == uname:
            return u
    return None


def any_users_exist():
    return bool(_load_users()['users'])


def create_user(username, password, display_name=None, is_admin=False):
    username = (username or '').strip()
    if not username or not password:
        raise ValueError('username and password are required')
    if len(password) < 6:
        raise ValueError('password must be at least 6 characters')
    if get_user_by_username(username):
        raise ValueError('username already taken')
    data = _load_users()
    uid = storage.new_id()
    data['users'][uid] = {
        'id': uid,
        'username': username,
        'display_name': (display_name or username).strip()[:60],
        'password_hash': generate_password_hash(password, method=_PW_METHOD),
        'is_admin': bool(is_admin),
        'created_at': _now(),
        'last_login': None,
    }
    _save_users(data)
    return data['users'][uid]


def verify_credentials(username, password):
    u = get_user_by_username(username)
    if u and password and check_password_hash(u['password_hash'], password):
        return u
    return None


def set_password(user_id, new_password):
    if not new_password or len(new_password) < 6:
        raise ValueError('password must be at least 6 characters')
    data = _load_users()
    u = data['users'].get(user_id)
    if not u:
        raise ValueError('no such user')
    u['password_hash'] = generate_password_hash(new_password, method=_PW_METHOD)
    _save_users(data)


def _touch_login(user_id):
    data = _load_users()
    u = data['users'].get(user_id)
    if u:
        u['last_login'] = _now()
        _save_users(data)


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------
def login_user(user, remember=True):
    session['user_id'] = user['id']
    session.permanent = bool(remember)
    _touch_login(user['id'])


def logout_user():
    for k in ('user_id', 'active_campaign_id'):
        session.pop(k, None)


def current_user():
    return get_user(session.get('user_id'))


def login_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            if request.path.startswith('/api/'):
                return jsonify({'error': 'login required'}), 401
            return redirect(url_for('login', next=request.path))
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Invite codes
# --------------------------------------------------------------------------
def _load_invites():
    return storage.load_json(INVITES_FILE, default={'invites': {}}) or {'invites': {}}


def _save_invites(data):
    storage.atomic_write_json(INVITES_FILE, data)


def _gen_code():
    return '-'.join(
        ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(2)
    )


def create_invite(campaign_id, role, *, character_id=None, created_by=None,
                  uses=1, ttl_days=14, creates_account=True):
    assert role in ('gm', 'player'), role
    data = _load_invites()
    code = _gen_code()
    while code in data['invites']:
        code = _gen_code()
    data['invites'][code] = {
        'code': code,
        'campaign_id': campaign_id,
        'role': role,
        'character_id': character_id,
        'creates_account': bool(creates_account),
        'uses_left': int(uses),
        'created_by': created_by,
        'expires_at': time.time() + ttl_days * 86400,
    }
    _save_invites(data)
    return code


def get_invite(code):
    """Return a valid invite, or None if missing/expired/exhausted."""
    inv = _load_invites()['invites'].get((code or '').strip().upper())
    if not inv:
        return None
    if inv['uses_left'] <= 0:
        return None
    if inv.get('expires_at') and time.time() > inv['expires_at']:
        return None
    return inv


def consume_invite(code):
    """Decrement an invite's remaining uses; returns the invite or None."""
    data = _load_invites()
    inv = data['invites'].get((code or '').strip().upper())
    if not inv or inv['uses_left'] <= 0:
        return None
    if inv.get('expires_at') and time.time() > inv['expires_at']:
        return None
    inv['uses_left'] -= 1
    _save_invites(data)
    return inv


def active_invite_for_character(campaign_id, character_id):
    """An existing still-valid invite for this campaign+character, or None -- so a
    GM's invites page is idempotent instead of minting a new code on every load."""
    for inv in _load_invites()['invites'].values():
        if (inv.get('campaign_id') == campaign_id and inv.get('character_id') == character_id
                and inv['uses_left'] > 0
                and (not inv.get('expires_at') or time.time() <= inv['expires_at'])):
            return inv
    return None


# --------------------------------------------------------------------------
# CSRF (per-session token; checked on state-changing requests)
# --------------------------------------------------------------------------
def csrf_token():
    tok = session.get('_csrf')
    if not tok:
        tok = secrets.token_urlsafe(32)
        session['_csrf'] = tok
    return tok


def check_csrf():
    sent = request.headers.get('X-CSRF-Token')
    if sent is None and request.form:
        sent = request.form.get('_csrf')
    expected = session.get('_csrf', '')
    return bool(sent) and bool(expected) and secrets.compare_digest(sent, expected)
