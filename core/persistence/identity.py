"""SQL account and invitation adapter for the existing authentication API.

Only relational columns confer identity or invitation authority. Legacy source
payloads retain presentation fields, never password, role, or session authority.
"""

from contextlib import contextmanager
from functools import wraps
import os

from sqlalchemy import select, text

from . import runtime
from .models import AuditEvent, Campaign, Character, Invitation, User, new_id, utc_now
from .services import InviteUnavailableError, TransactionalStore


def _store_operation(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        return runtime.store_call(fn, *args, **kwargs)
    return wrapped


@contextmanager
def _transaction(*, account_creation=False):
    """Serialize SQLite writes and PostgreSQL's initially rowless bootstrap."""
    database = runtime.database()
    with database.session() as db_session:
        if database.engine.dialect.name == 'sqlite':
            db_session.execute(text('BEGIN IMMEDIATE'))
        elif account_creation and database.engine.dialect.name == 'postgresql':
            # A row lock cannot protect the absence of the first account.
            db_session.execute(text('SELECT pg_advisory_xact_lock(73420401)'))
        yield db_session
        db_session.commit()


def _timestamp(value):
    return value.replace(tzinfo=None).isoformat(timespec='seconds') if value else None


def user_document(user):
    document = dict(user.source_payload or {})
    document.update(
        id=user.id, username=user.username, display_name=user.display_name,
        password_hash=user.password_hash, is_admin=user.is_admin,
        created_at=_timestamp(user.created_at), last_login=_timestamp(user.last_login_at),
        session_version=user.session_version,
    )
    if user.last_campaign_id is not None or 'last_campaign_id' in document:
        document['last_campaign_id'] = user.last_campaign_id
    return document


def invite_document(invitation):
    return {
        'code': invitation.code, 'campaign_id': invitation.campaign_id,
        'role': invitation.role, 'character_id': invitation.character_id,
        'creates_account': invitation.creates_account,
        'uses_left': invitation.remaining_uses,
        'created_by': invitation.created_by_user_id,
        'expires_at': invitation.expires_at,
    }


def _lock_invitation(db_session, code):
    """Match the campaign-before-invite lock order of redemption and deletion."""
    locator = db_session.get(Invitation, code)
    if locator is None:
        return None
    db_session.scalar(select(Campaign).where(Campaign.id == locator.campaign_id).with_for_update())
    return db_session.scalar(select(Invitation).where(Invitation.code == code)
                             .execution_options(populate_existing=True).with_for_update())


def _check_empty_store(db_session):
    from core import auth, storage
    try:
        # Existence is initialization evidence even for deployments predating
        # the marker. Never inspect its contents or grant JSON authority here.
        os.lstat(storage.USERS_FILE)
        legacy_accounts_exist = True
    except FileNotFoundError:
        legacy_accounts_exist = False
    if (legacy_accounts_exist or auth._marker_exists() or auth._has_account_campaign_evidence()
            or db_session.scalar(select(Campaign.id).limit(1)) is not None):
        raise runtime.StoreUnavailable('account store is unavailable')


@_store_operation
def load_users():
    from core import auth
    with runtime.database().session() as db_session:
        users = {user.id: user_document(user) for user in db_session.scalars(select(User))}
        if not users:
            _check_empty_store(db_session)
        return auth._validate_users_document({'users': users})


def compare_users_shadow(legacy):
    """Compare effective account authority, ignoring timestamp/optional-field formatting."""
    def authority(document):
        return {
            user_id: {
                'username': user['username'].strip().lower(),
                'password_hash': user['password_hash'],
                'is_admin': bool(user.get('is_admin', False)),
                'session_version': user.get('session_version', 0),
            }
            for user_id, user in (document or {'users': {}})['users'].items()
        }
    runtime.compare_shadow('users', authority(legacy), lambda: authority(load_users()))


def compare_invites_shadow(legacy):
    """Observe effective invite grants, excluding notes and file envelopes."""
    if runtime.backend() != 'shadow':
        return legacy

    def authority(document):
        if not isinstance(document, dict) or not isinstance(document.get('invites'), dict):
            return document
        result = {}
        for code, invitation in document['invites'].items():
            if not isinstance(invitation, dict):
                result[code] = invitation
                continue
            result[code] = {
                field: invitation.get(field)
                for field in ('code', 'campaign_id', 'role', 'character_id', 'created_by')
            }
            result[code].update(
                creates_account=invitation.get('creates_account', True),
                uses_left=invitation.get('uses_left'),
                # Legacy zero expires_at explicitly means no expiration.
                expires_at=invitation.get('expires_at') or None,
            )
        return result

    runtime.compare_shadow('invites', authority(legacy), lambda: authority(load_invites()))
    return legacy


@_store_operation
def load_invites():
    with runtime.database().session() as db_session:
        invitations = db_session.scalars(
            select(Invitation).join(Campaign).where(
                Invitation.revoked_at.is_(None), Campaign.trashed_at.is_(None),
            )
        )
        return {'invites': {inv.code: invite_document(inv) for inv in invitations}}


def _insert_user(db_session, username, password_hash, display_name=None, is_admin=False):
    normalized = username.strip().lower()
    if db_session.scalar(select(User.id).where(User.normalized_username == normalized)) is not None:
        raise ValueError('username already taken')
    user = User(id=new_id(), username=username.strip(), normalized_username=normalized,
                display_name=(display_name or username).strip()[:60],
                password_hash=password_hash, is_admin=bool(is_admin),
                session_version=0, created_at=utc_now(), source_payload={})
    db_session.add(user)
    db_session.flush()
    db_session.add(AuditEvent(actor_user_id=user.id, action='account.created',
                              target_type='user', target_id=user.id, details={}))
    return user


@_store_operation
def create_user(username, password_hash, display_name=None, is_admin=False, *, first_admin=False):
    from core import auth
    with _transaction(account_creation=True) as db_session:
        existing = db_session.scalar(select(User.id).limit(1))
        if first_admin and existing is not None:
            raise auth.AccountBootstrapComplete('account setup is already complete')
        if existing is None:
            _check_empty_store(db_session)
        user = _insert_user(db_session, username, password_hash, display_name, is_admin)
        document = user_document(user)
    return document


@_store_operation
def set_password(user_id, password_hash):
    with _transaction() as db_session:
        user = db_session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None:
            raise ValueError('no such user')
        user.password_hash = password_hash
        user.session_version += 1
        db_session.add(AuditEvent(actor_user_id=user_id, action='account.password_changed',
                                  target_type='user', target_id=user_id,
                                  details={'session_version': user.session_version}))
        return user.session_version


@_store_operation
def touch_login(user_id):
    with _transaction() as db_session:
        user = db_session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is not None:
            user.last_login_at = utc_now()


@_store_operation
def set_last_campaign(user_id, campaign_id):
    with _transaction() as db_session:
        user = db_session.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is not None:
            user.last_campaign_id = campaign_id


@_store_operation
def create_invite(campaign_id, role, *, character_id=None, created_by=None,
                  uses=1, ttl_days=14, creates_account=True):
    from core import auth
    from .campaign_runtime import _require_actor
    with _transaction() as db_session:
        campaign = db_session.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
        if campaign is None or campaign.trashed_at is not None:
            raise ValueError('campaign is unavailable')
        _require_actor(db_session, campaign_id, created_by)
        if character_id is not None:
            character = db_session.get(Character, character_id)
            if character is None or character.campaign_id != campaign_id:
                raise ValueError('character does not belong to campaign')
        if int(uses) < 0:
            raise ValueError('invite uses must not be negative')
        code = auth._gen_code()
        while db_session.get(Invitation, code) is not None:
            code = auth._gen_code()
        db_session.add(Invitation(code=code, campaign_id=campaign_id, role=role,
                                  character_id=character_id, creates_account=bool(creates_account),
                                  remaining_uses=int(uses), created_by_user_id=created_by,
                                  expires_at=utc_now().timestamp() + ttl_days * 86400))
        db_session.add(AuditEvent(actor_user_id=created_by, campaign_id=campaign_id,
                                  action='invite.created', target_type='invite', target_id=code,
                                  details={'role': role, 'character_id': character_id}))
    return code


@_store_operation
def revoke_invite(code, *, actor_user_id=None):
    from .campaign_runtime import _require_actor
    code = (code or '').strip().upper()
    with _transaction() as db_session:
        invitation = _lock_invitation(db_session, code)
        if invitation is None or invitation.revoked_at is not None:
            return False
        _require_actor(db_session, invitation.campaign_id, actor_user_id)
        invitation.revoked_at = utc_now()
        db_session.add(AuditEvent(actor_user_id=actor_user_id,
                                  campaign_id=invitation.campaign_id, action='invite.revoked',
                                  target_type='invite', target_id=code, details={}))
        return True


@_store_operation
def consume_invite(code):
    code = (code or '').strip().upper()
    with _transaction() as db_session:
        invitation = _lock_invitation(db_session, code)
        if (invitation is None or invitation.revoked_at is not None or invitation.remaining_uses <= 0
                or (invitation.expires_at and utc_now().timestamp() > invitation.expires_at)):
            return None
        campaign = db_session.get(Campaign, invitation.campaign_id)
        if campaign is None or campaign.trashed_at is not None:
            return None
        invitation.remaining_uses -= 1
        return invite_document(invitation)


@_store_operation
def redeem_invite(code, user_id=None, *, username=None, password=None, display_name=None):
    """Commit account creation, membership, ownership, use, and audit together."""
    from core import auth
    from werkzeug.security import generate_password_hash
    password_hash = None
    if user_id is None:
        username = auth._validate_new_user_credentials(username, password)
        password_hash = generate_password_hash(password, method=auth._PW_METHOD)
    with _transaction(account_creation=user_id is None) as db_session:
        if user_id is None:
            if db_session.scalar(select(User.id).limit(1)) is None:
                _check_empty_store(db_session)
            invitation = _lock_invitation(db_session, (code or '').strip().upper())
            if invitation is None or not invitation.creates_account:
                raise InviteUnavailableError('invite does not allow account creation')
            user = _insert_user(db_session, username, password_hash, display_name)
            user_id = user.id
        else:
            user = db_session.get(User, user_id)
            if user is None:
                raise ValueError('no such user')
        result = TransactionalStore(runtime.database().session_factory).redeem_invite(
            code=code, user_id=user_id, session=db_session,
        )
        document = user_document(user)
    auth._invalidate_sql_user_memo()
    auth._write_account_mode_marker()
    return document, result
