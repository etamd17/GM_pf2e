"""Focused regressions for session revocation and invite-store transactions."""

from __future__ import annotations

import threading

from flask import Flask, session
import pytest
from werkzeug.security import generate_password_hash

from core import auth, storage


@pytest.fixture
def isolated_auth_store(tmp_path, monkeypatch):
    """Point every identity-store path at one pristine temporary directory."""
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(storage, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    monkeypatch.setattr(
        storage,
        'CAMPAIGNS_TRASH_DIR',
        str(tmp_path / 'campaigns_trash'),
    )
    monkeypatch.setattr(auth, 'INVITES_FILE', str(tmp_path / 'invites.json'))
    return tmp_path


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='auth-session-version-test')
    return app


def _legacy_user(user_id='legacy-user'):
    return {
        'id': user_id,
        'username': 'legacy',
        'display_name': 'Legacy User',
        'password_hash': 'pbkdf2:sha256:1$placeholder$hash',
        'is_admin': False,
        'created_at': '2026-01-01T00:00:00',
        'last_login': None,
    }


def test_legacy_user_and_cookie_default_to_session_version_zero(
    isolated_auth_store,
    flask_app,
):
    user = _legacy_user()
    storage.atomic_write_json(storage.USERS_FILE, {'users': {user['id']: user}})

    # A cookie issued before session versions existed contains only user_id.
    # It must remain valid until a password change explicitly bumps the user.
    with flask_app.test_request_context('/'):
        session['user_id'] = user['id']
        assert auth.current_user()['id'] == user['id']
        assert auth._SESSION_VERSION_KEY not in session

    assert auth.set_password(user['id'], 'new-password') == 1
    persisted = storage.load_json(storage.USERS_FILE)['users'][user['id']]
    assert persisted['session_version'] == 1

    with flask_app.test_request_context('/'):
        session['user_id'] = user['id']
        session['active_campaign_id'] = 'old-campaign'
        session['player_name'] = 'Old Hero'
        assert auth.current_user() is None
        assert 'user_id' not in session
        assert 'active_campaign_id' not in session
        assert 'player_name' not in session


def test_invalid_utf8_users_store_is_classified_unavailable(isolated_auth_store):
    with open(storage.USERS_FILE, 'wb') as handle:
        handle.write(b'\xff\xfe\xfa')

    assert auth.account_store_state() == auth.ACCOUNT_STORE_UNAVAILABLE


def test_json_integer_limit_is_classified_as_account_store_unavailable(
    isolated_auth_store,
):
    with open(storage.USERS_FILE, 'w', encoding='utf-8') as handle:
        handle.write('{"users": ' + ('9' * 5000) + '}')

    assert auth.account_store_state() == auth.ACCOUNT_STORE_UNAVAILABLE


def test_password_change_revokes_other_sessions_but_can_refresh_current_one(
    isolated_auth_store,
    flask_app,
):
    user = auth.create_user('alice', 'old-password')

    def new_signed_session():
        with flask_app.test_request_context('/'):
            auth.login_user(auth.get_user(user['id']), remember=True)
            return dict(session)

    first_cookie = new_signed_session()
    second_cookie = new_signed_session()
    assert first_cookie[auth._SESSION_VERSION_KEY] == 0
    assert second_cookie[auth._SESSION_VERSION_KEY] == 0

    # The self-service request may deliberately keep this one browser alive.
    with flask_app.test_request_context('/'):
        session.update(first_cookie)
        assert auth.current_user()['id'] == user['id']
        assert auth.set_password(user['id'], 'new-password') == 1
        assert auth.refresh_session_version(user['id']) is True
        assert auth.current_user()['id'] == user['id']
        refreshed_cookie = dict(session)

    # A second 60-day cookie still carrying version zero is rejected and its
    # identity-bound actor/campaign selections are removed with it.
    with flask_app.test_request_context('/'):
        session.update(second_cookie)
        session['campaign_stopped'] = True
        session['player_name'] = 'Alice Hero'
        assert auth.current_user() is None
        assert 'user_id' not in session
        assert auth._SESSION_VERSION_KEY not in session
        assert 'campaign_stopped' not in session
        assert 'player_name' not in session

    with flask_app.test_request_context('/'):
        session.update(refreshed_cookie)
        assert auth.current_user()['id'] == user['id']
        assert session[auth._SESSION_VERSION_KEY] == 1

    assert auth.verify_credentials('alice', 'old-password') is None
    assert auth.verify_credentials('alice', 'new-password')['id'] == user['id']


def test_failed_password_validation_does_not_advance_session_version(
    isolated_auth_store,
):
    user = auth.create_user('bob', 'valid-password')

    with pytest.raises(ValueError, match='at least 6 characters'):
        auth.set_password(user['id'], 'short')

    assert auth.get_user(user['id'])['session_version'] == 0
    assert auth.verify_credentials('bob', 'valid-password')['id'] == user['id']


def test_legacy_long_credentials_remain_usable_but_new_limits_still_apply(
    isolated_auth_store,
):
    # Both remain comfortably below the HTTP auth-form cap, but exceed the
    # post-PR3 creation policy by enough to catch an accidental shared limit.
    username = 'legacy-' + ('u' * 2048)
    password = 'p' * 8192
    user = _legacy_user('long-credential-user')
    user['username'] = username
    user['password_hash'] = generate_password_hash(password, method=auth._PW_METHOD)
    storage.atomic_write_json(storage.USERS_FILE, {'users': {user['id']: user}})

    assert auth.verify_credentials(username, password)['id'] == user['id']
    with pytest.raises(ValueError, match='64 characters or fewer'):
        auth.create_user(username, 'valid-password')
    with pytest.raises(ValueError, match='256 characters or fewer'):
        auth.create_user('new-user', password)


@pytest.mark.parametrize('mutation', ['consume', 'revoke'])
def test_invite_mutations_serialize_reload_through_save(
    isolated_auth_store,
    monkeypatch,
    mutation,
):
    """A stale creator cannot undo a concurrent consume or revocation.

    The mutator is paused immediately after its fresh disk read.  While paused,
    the module RLock must be held and a creator must be unable to finish from a
    stale snapshot.  Once released, both operations commit without resurrecting
    the victim or losing the newly-created code.
    """
    victim = auth.create_invite('campaign-1', 'player', uses=1)
    real_load = auth._load_invites
    first_loaded = threading.Event()
    release_first = threading.Event()
    create_started = threading.Event()
    create_done = threading.Event()
    load_counter_lock = threading.Lock()
    load_count = 0
    errors = []
    created = []

    def gated_load():
        nonlocal load_count
        data = real_load()
        with load_counter_lock:
            index = load_count
            load_count += 1
        if index == 0:
            first_loaded.set()
            if not release_first.wait(5):
                raise AssertionError('test did not release the paused invite mutation')
        return data

    monkeypatch.setattr(auth, '_load_invites', gated_load)
    monkeypatch.setattr(auth, '_gen_code', lambda: 'NEWW-CODE')

    def mutate():
        try:
            if mutation == 'consume':
                auth.consume_invite(victim)
            else:
                auth.revoke_invite(victim)
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)

    def create():
        create_started.set()
        try:
            created.append(auth.create_invite('campaign-1', 'gm', uses=2))
        except BaseException as exc:  # surface thread failures in the test
            errors.append(exc)
        finally:
            create_done.set()

    mutator = threading.Thread(target=mutate, name=f'invite-{mutation}')
    creator = threading.Thread(target=create, name='invite-create')
    mutator.start()
    assert first_loaded.wait(2), 'invite mutation never reached its disk reload'

    # This is deterministic proof that the mutator acquired the transaction
    # lock before reading; unlike sleep-based race tests, it does not depend on
    # a particular thread schedule.
    lock_was_free = auth._INVITES_LOCK.acquire(blocking=False)
    if lock_was_free:
        auth._INVITES_LOCK.release()

    creator.start()
    assert create_started.wait(2)
    creator_finished_while_mutation_was_paused = create_done.wait(0.1)
    release_first.set()
    mutator.join(5)
    creator.join(5)

    assert not mutator.is_alive()
    assert not creator.is_alive()
    assert errors == []
    assert lock_was_free is False
    assert creator_finished_while_mutation_was_paused is False
    assert created == ['NEWW-CODE']

    invites = real_load()['invites']
    assert invites['NEWW-CODE']['uses_left'] == 2
    if mutation == 'consume':
        assert invites[victim]['uses_left'] == 0
    else:
        assert victim not in invites
