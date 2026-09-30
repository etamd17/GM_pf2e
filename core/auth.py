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
import json
import time
import secrets
import functools
import threading

from flask import session, request, jsonify, redirect, url_for, g, has_request_context
from werkzeug.security import generate_password_hash, check_password_hash

from core import storage

# Key for the per-REQUEST users memo. Deliberately not a module-level cache:
# scoping it to one request means a later request, or anything that writes the
# file from outside, is still seen. Nothing inside a single request can
# legitimately observe users.json changing underneath it.
_USERS_MEMO = '_auth_users_memo'
_ACCOUNT_STORE_STATE_MEMO = '_auth_account_store_state_memo'

ACCOUNT_MODE_MARKER_NAME = '.account-mode-initialized'
ACCOUNT_STORE_UNINITIALIZED = 'uninitialized'
ACCOUNT_STORE_READY = 'ready'
ACCOUNT_STORE_UNAVAILABLE = 'unavailable'

# users.json is one security-bearing document.  Every mutation must serialize
# its fresh disk read through the atomic replace; locking only the final write
# would still allow two requests to save snapshots derived from the same old
# document and silently discard one another's account changes.  RLock keeps
# _save_users() safe when called by the transaction helpers below while also
# protecting any future direct internal save.
_USERS_LOCK = threading.RLock()


class AccountStoreUnavailable(RuntimeError):
    """The deploy was initialized for accounts but its identity store is unsafe."""


class AccountBootstrapComplete(RuntimeError):
    """Another request has already completed the one-time account bootstrap."""

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
def _account_mode_marker_path():
    """An existence-only, never-cleared record that account mode was initialized.

    This deliberately does not live in ``server_state.json``: that file has
    unrelated writers and lenient recovery semantics.  Losing or corrupting
    those fields must never silently reopen the legacy trust model or /setup.
    """
    return os.path.join(storage.DATA_DIR, ACCOUNT_MODE_MARKER_NAME)


def _request_memo_get(name):
    if not has_request_context():
        return None
    return g.get(name) if hasattr(g, 'get') else getattr(g, name, None)


def _request_memo_set(name, value):
    if has_request_context():
        setattr(g, name, value)


def _marker_exists():
    """Return whether initialization was recorded; any stat error fails closed."""
    try:
        # lstat makes a dangling symlink or other existing filesystem entry
        # authoritative too. Marker contents are intentionally never parsed.
        os.lstat(_account_mode_marker_path())
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise AccountStoreUnavailable('account initialization marker is unreadable') from exc


def _write_account_mode_marker():
    try:
        storage.atomic_write_json(
            _account_mode_marker_path(),
            {'schema_version': 1, 'initialized': True},
        )
    except OSError as exc:
        raise AccountStoreUnavailable('account initialization marker could not be saved') from exc


def _validate_users_document(data):
    """Validate the security-bearing shape instead of defaulting corruption empty."""
    if not isinstance(data, dict) or not isinstance(data.get('users'), dict):
        raise AccountStoreUnavailable('users store has an invalid schema')
    usernames = set()
    for user_id, user in data['users'].items():
        if not isinstance(user_id, str) or not user_id:
            raise AccountStoreUnavailable('users store has an invalid user id')
        if not isinstance(user, dict) or user.get('id') != user_id:
            raise AccountStoreUnavailable('users store has an invalid user record')
        username = user.get('username')
        password_hash = user.get('password_hash')
        if not isinstance(username, str) or not username.strip():
            raise AccountStoreUnavailable('users store has an invalid username')
        if not isinstance(password_hash, str) or not password_hash:
            raise AccountStoreUnavailable('users store has an invalid password hash')
        normalized = username.strip().lower()
        if normalized in usernames:
            raise AccountStoreUnavailable('users store has duplicate usernames')
        usernames.add(normalized)
    return data


def _read_users_document():
    """Strict read: missing is distinct from malformed or unreadable."""
    try:
        with open(storage.USERS_FILE, encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountStoreUnavailable('users store is unavailable') from exc
    return _validate_users_document(data)


def _has_account_campaign_evidence():
    """Whether durable campaign documents prove this is not pristine bootstrap."""
    try:
        return bool(storage.list_campaign_ids() or storage.list_trashed_campaign_ids())
    except OSError as exc:
        raise AccountStoreUnavailable('campaign store cannot be inspected safely') from exc


def account_store_state():
    """Return ``uninitialized``, ``ready``, or fail-closed ``unavailable``.

    A valid non-empty users store is also the upgrade path for deployments
    created before the sentinel existed: the first request records the marker.
    Once either the marker or a campaign document proves prior initialization,
    an empty/missing users store is an outage, never a legacy-mode fallback.
    """
    cached = _request_memo_get(_ACCOUNT_STORE_STATE_MEMO)
    if cached in {
        ACCOUNT_STORE_UNINITIALIZED,
        ACCOUNT_STORE_READY,
        ACCOUNT_STORE_UNAVAILABLE,
    }:
        return cached

    try:
        marker = _marker_exists()
        data = _read_users_document()
        if data is not None and data['users']:
            if not marker:
                _write_account_mode_marker()
            state = ACCOUNT_STORE_READY
            _request_memo_set(_USERS_MEMO, data)
        elif marker or _has_account_campaign_evidence():
            state = ACCOUNT_STORE_UNAVAILABLE
        else:
            # A genuinely fresh install (or a legacy flat install) has no
            # sentinel, no account campaign document, and no users.
            state = ACCOUNT_STORE_UNINITIALIZED
            _request_memo_set(_USERS_MEMO, data or {'users': {}})
    except AccountStoreUnavailable:
        state = ACCOUNT_STORE_UNAVAILABLE

    _request_memo_set(_ACCOUNT_STORE_STATE_MEMO, state)
    return state


def account_mode_initialized():
    """True after initialization, including while the store is unavailable.

    Treating ``unavailable`` as initialized is deliberate: callers must never
    reinterpret a storage outage as permission to enter legacy mode or /setup.
    """
    return account_store_state() != ACCOUNT_STORE_UNINITIALIZED


def _load_users():
    """Read users.json, at most once per request.

    _is_gm() alone used to cost four reads of this file plus two of the campaign
    doc, because _account_mode() and current_user() each re-entered it and
    _active_campaign_id() repeated the pair. It runs twice per gated request --
    once in the check_gm_access before_request, once in @gm_required -- so a
    single beacon POST read users.json ten times and campaign.json five, for
    4.46 ms of which 97% was this. During a ruler drag that is 11 of those a
    second on the one worker that also serves every player's SSE.

    Mutations deliberately do not use this memo: they take ``_USERS_LOCK`` and
    call ``_load_users_for_update()`` so their read-modify-write base is fresh.
    """
    state = account_store_state()
    if state == ACCOUNT_STORE_UNAVAILABLE:
        raise AccountStoreUnavailable('account store is unavailable')
    cached = _request_memo_get(_USERS_MEMO)
    if cached is not None:
        return cached
    data = _read_users_document() or {'users': {}}
    _request_memo_set(_USERS_MEMO, data)
    return data


def _load_users_for_update():
    """Strictly reload users.json for a mutation while ``_USERS_LOCK`` is held.

    Request memoization is intentionally bypassed.  A cached document is a
    valid read optimization, but it is never a safe read-modify-write base
    because another request may have committed a newer document meanwhile.
    """
    marker = _marker_exists()
    data = _read_users_document()
    if data is not None and data['users']:
        if not marker:
            _write_account_mode_marker()
        return data
    if marker or _has_account_campaign_evidence():
        raise AccountStoreUnavailable('account store is unavailable')
    return data or {'users': {}}


def _save_users(data):
    with _USERS_LOCK:
        data = _validate_users_document(data)
        storage.atomic_write_json(storage.USERS_FILE, data)
        if data['users']:
            # Saving the non-empty store first is safe: a process interruption before
            # this marker write still leaves valid users, which the next request
            # recognizes as account mode and backfills. Marker failure is surfaced,
            # never translated into bootstrap.
            _write_account_mode_marker()
        # Keep the memo pointing at what is now on disk, so a read later in the
        # same request cannot serve the pre-write copy.
        _request_memo_set(_USERS_MEMO, data)
        _request_memo_set(
            _ACCOUNT_STORE_STATE_MEMO,
            ACCOUNT_STORE_READY if data['users'] else ACCOUNT_STORE_UNINITIALIZED,
        )


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


def list_users():
    return list(_load_users()['users'].values())


def create_user(username, password, display_name=None, is_admin=False):
    username = (username or '').strip()
    if not username or not password:
        raise ValueError('username and password are required')
    if len(password) < 6:
        raise ValueError('password must be at least 6 characters')
    password_hash = generate_password_hash(password, method=_PW_METHOD)
    with _USERS_LOCK:
        data = _load_users_for_update()
        normalized = username.lower()
        if any(u['username'].lower() == normalized for u in data['users'].values()):
            raise ValueError('username already taken')
        uid = storage.new_id()
        data['users'][uid] = {
            'id': uid,
            'username': username,
            'display_name': (display_name or username).strip()[:60],
            'password_hash': password_hash,
            'is_admin': bool(is_admin),
            'created_at': _now(),
            'last_login': None,
        }
        _save_users(data)
        return data['users'][uid]


def create_first_admin(username, password, display_name=None):
    """Atomically create the sole first administrator on a pristine install.

    The /setup request-level readiness check is only an early UX redirect. Two
    requests can both pass it before either commits, so bootstrap authority must
    be decided again from fresh durable state while holding the users-store lock.
    """
    username = (username or '').strip()
    if not username or not password:
        raise ValueError('username and password are required')
    if len(password) < 6:
        raise ValueError('password must be at least 6 characters')
    password_hash = generate_password_hash(password, method=_PW_METHOD)
    with _USERS_LOCK:
        marker = _marker_exists()
        data = _read_users_document()
        if data is not None and data['users']:
            raise AccountBootstrapComplete('account setup is already complete')
        if marker or _has_account_campaign_evidence():
            raise AccountStoreUnavailable('account store is unavailable')
        data = data or {'users': {}}
        uid = storage.new_id()
        data['users'][uid] = {
            'id': uid,
            'username': username,
            'display_name': (display_name or username).strip()[:60],
            'password_hash': password_hash,
            'is_admin': True,
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
    password_hash = generate_password_hash(new_password, method=_PW_METHOD)
    with _USERS_LOCK:
        data = _load_users_for_update()
        u = data['users'].get(user_id)
        if not u:
            raise ValueError('no such user')
        u['password_hash'] = password_hash
        _save_users(data)


def _touch_login(user_id):
    with _USERS_LOCK:
        data = _load_users_for_update()
        u = data['users'].get(user_id)
        if u:
            u['last_login'] = _now()
            _save_users(data)


def set_last_campaign(user_id, cid):
    """Remember the user's most-recently-selected campaign on their account, so a
    fresh login resumes THEIR table rather than inheriting whatever campaign holds
    the server-wide live slot. Persisted; replaced only by selecting another."""
    with _USERS_LOCK:
        data = _load_users_for_update()
        u = data['users'].get(user_id)
        if u is not None and u.get('last_campaign_id') != cid:
            u['last_campaign_id'] = cid
            _save_users(data)


# --------------------------------------------------------------------------
# Session
# --------------------------------------------------------------------------
def login_user(user, remember=True):
    # Actor/campaign selections belong to one authenticated identity. A shared
    # browser signing in as someone else must not inherit the prior user's PC.
    if session.get('user_id') != user['id']:
        for key in ('active_campaign_id', 'campaign_stopped', 'player_name'):
            session.pop(key, None)
    session['user_id'] = user['id']
    session.permanent = bool(remember)
    _touch_login(user['id'])


def logout_user():
    for k in ('user_id', 'active_campaign_id', 'campaign_stopped', 'player_name'):
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


def list_active_invites(campaign_id):
    """All still-valid invites for a campaign (for the manage page's open-codes
    list -- generic player + co-GM invites that aren't tied to a character)."""
    now = time.time()
    return [inv for inv in _load_invites()['invites'].values()
            if inv.get('campaign_id') == campaign_id and inv['uses_left'] > 0
            and (not inv.get('expires_at') or now <= inv['expires_at'])]


def revoke_invite(code):
    """Delete an invite outright (a GM cancelling an open code)."""
    data = _load_invites()
    if (code or '').strip().upper() in data['invites']:
        del data['invites'][(code or '').strip().upper()]
        _save_invites(data)
        return True
    return False


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
