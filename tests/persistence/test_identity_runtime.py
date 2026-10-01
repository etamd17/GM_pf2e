"""SQL authority and transaction boundaries for live account workflows."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask, session
from sqlalchemy import func, select

from core import auth, storage
from core.persistence import runtime
from core.persistence.database import Database
from core.persistence.models import (
    AuditEvent, Campaign, CampaignMembership, Character, CharacterAssignment,
    Invitation, InviteRedemption, User,
)
from core.persistence.services import CharacterAlreadyClaimedError, InviteUnavailableError


@pytest.fixture
def sql_identity(sqlite_database, tmp_path, monkeypatch):
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'sql')
    monkeypatch.setattr(runtime, 'database', lambda: sqlite_database)
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(storage, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(auth, 'INVITES_FILE', str(tmp_path / 'invites.json'))
    monkeypatch.setattr(storage, 'list_campaign_ids', lambda: [])
    monkeypatch.setattr(storage, 'list_trashed_campaign_ids', lambda: [])
    return sqlite_database


def seed_campaign(database, owner):
    with database.transaction() as db_session:
        db_session.add(Campaign(id='campaign', slug='campaign', name='Campaign',
                                system='pf2e', created_by_user_id=owner['id']))
        db_session.flush()
        db_session.add(CampaignMembership(campaign_id='campaign', user_id=owner['id'], role='gm'))
        db_session.add(Character(id='hero', campaign_id='campaign', system='pf2e',
                                 display_name='Hero', legacy_storage='party_data',
                                 legacy_file='hero.json', content_checksum='a' * 64))


def test_sql_auth_ignores_poisoned_json_and_source_payload(sql_identity):
    user = auth.create_first_admin('real', 'real-password')
    with sql_identity.transaction() as db_session:
        record = db_session.get(User, user['id'])
        record.is_admin = False
        record.source_payload = dict(user, is_admin=True, password_hash='poisoned')
    storage.atomic_write_json(storage.USERS_FILE, {'users': {'attacker': dict(user, id='attacker', username='attacker')}})
    assert auth.verify_credentials('attacker', 'real-password') is None
    assert auth.verify_credentials('real', 'real-password')['is_admin'] is False
    assert auth.get_user(user['id'])['password_hash'] != 'poisoned'


def test_shadow_identity_keeps_json_authority_and_sanitizes_comparison(sql_identity, monkeypatch, caplog):
    user = auth.create_first_admin('real', 'real-password')
    legacy = dict(user)
    legacy.pop('session_version')  # Legacy optional field means version zero.
    storage.atomic_write_json(storage.USERS_FILE, {'users': {user['id']: legacy}})
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'shadow')
    assert auth.get_user(user['id'])['is_admin'] is True
    assert 'mismatch' not in caplog.text.lower()
    legacy['is_admin'] = False
    storage.atomic_write_json(storage.USERS_FILE, {'users': {user['id']: legacy}})
    assert auth.get_user(user['id'])['is_admin'] is False
    assert 'mismatch' in caplog.text.lower()
    assert user['password_hash'] not in caplog.text
    assert 'real-password' not in caplog.text


def test_sql_empty_store_cannot_reopen_initialized_accounts(sql_identity):
    auth._write_account_mode_marker()
    assert auth.account_store_state() == auth.ACCOUNT_STORE_UNAVAILABLE
    with pytest.raises(auth.AccountStoreUnavailable):
        auth.create_first_admin('attacker', 'password')


def test_sql_empty_store_refuses_premarker_legacy_user_evidence(sql_identity, monkeypatch):
    storage.atomic_write_json(storage.USERS_FILE, {'users': {'old-user': {}}})
    monkeypatch.setattr(auth, '_read_users_document', lambda: pytest.fail('SQL read legacy accounts'))
    assert not auth._marker_exists()
    assert auth.account_store_state() == auth.ACCOUNT_STORE_UNAVAILABLE
    with pytest.raises(auth.AccountStoreUnavailable):
        auth.create_first_admin('attacker', 'password')
    with sql_identity.session() as db_session:
        assert db_session.scalar(select(func.count()).select_from(User)) == 0


def test_sql_outage_never_falls_back_to_json(sql_identity, monkeypatch):
    user = auth.create_first_admin('real', 'real-password')
    storage.atomic_write_json(storage.USERS_FILE, {'users': {user['id']: user}})
    from sqlalchemy.exc import OperationalError
    def unavailable():
        raise OperationalError('SELECT users', {}, RuntimeError('secret connection diagnostics'))
    monkeypatch.setattr(runtime, 'database', unavailable)
    assert auth.account_store_state() == auth.ACCOUNT_STORE_UNAVAILABLE
    with pytest.raises(auth.AccountStoreUnavailable) as failure:
        auth.verify_credentials('real', 'real-password')
    assert 'secret connection diagnostics' not in str(failure.value)


def test_sql_concurrent_bootstrap_and_password_resets(sql_identity, tmp_path, monkeypatch):
    database = Database(f'sqlite+pysqlite:///{tmp_path / "concurrency.sqlite"}',
                        engine_options={'connect_args': {'check_same_thread': False}})
    database.create_schema()
    monkeypatch.setattr(runtime, 'database', lambda: database)
    def bootstrap(name):
        try:
            return auth.create_first_admin(name, 'password')
        except auth.AccountBootstrapComplete:
            return None
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(bootstrap, ['first', 'second']))
        winners = [result for result in results if result is not None]
        assert len(winners) == 1
        assert len(auth.list_users()) == 1
        user = winners[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            versions = list(pool.map(lambda password: auth.set_password(user['id'], password),
                                     ['new-password-one', 'new-password-two']))
        assert sorted(versions) == [1, 2]
        assert auth.get_user(user['id'])['session_version'] == 2
    finally:
        database.dispose()


def test_sql_bootstrap_is_one_time(sql_identity):
    user = auth.create_first_admin('first', 'password')
    assert user['is_admin'] is True
    with pytest.raises(auth.AccountBootstrapComplete):
        auth.create_first_admin('second', 'password')
    assert len(auth.list_users()) == 1


def test_sql_password_epoch_invalidates_sessions_and_refreshes_request_memo(sql_identity):
    user = auth.create_first_admin('owner', 'old-password')
    app = Flask(__name__)
    app.secret_key = 'test-key'
    with app.test_request_context('/'):
        auth.login_user(user)
        assert auth.current_user()['id'] == user['id']
        assert auth.set_password(user['id'], 'new-password') == 1
        assert auth.current_user() is None
        assert 'user_id' not in session
    assert auth.verify_credentials('owner', 'old-password') is None
    assert auth.verify_credentials('owner', 'new-password')['session_version'] == 1


def test_sql_mutations_do_not_write_legacy_documents(sql_identity, monkeypatch):
    user = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, user)
    monkeypatch.setattr(auth, '_read_users_document', lambda: pytest.fail('read legacy users'))
    monkeypatch.setattr(auth, '_save_users', lambda _: pytest.fail('write legacy users'))
    monkeypatch.setattr(auth, '_save_invites', lambda _: pytest.fail('write legacy invites'))
    auth.set_last_campaign(user['id'], 'campaign')
    auth._touch_login(user['id'])
    code = auth.create_invite('campaign', 'player', created_by=user['id'])
    assert auth.get_invite(code)['uses_left'] == 1
    assert auth.revoke_invite(code)
    assert auth.get_invite(code) is None
    assert not auth.revoke_invite(code)
    assert auth.get_user(user['id'])['last_campaign_id'] == 'campaign'
    assert auth.get_user(user['id'])['last_login'] is not None


def test_sql_invite_signup_and_claim_are_atomic_and_retryable(sql_identity):
    from core.persistence import identity
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    code = auth.create_invite('campaign', 'player', character_id='hero', created_by=owner['id'])
    user, result = identity.redeem_invite(code, username='new-player', password='password')
    assert result.remaining_uses == 0
    assert result.character_id == 'hero'
    _, retry = identity.redeem_invite(code, user_id=user['id'])
    assert retry.already_redeemed
    with sql_identity.session() as db_session:
        assert db_session.get(CampaignMembership, ('campaign', user['id'])).role == 'player'
        assert db_session.get(CharacterAssignment, ('campaign', 'hero', user['id'])).role == 'owner'
        assert db_session.scalar(select(func.count()).select_from(InviteRedemption)) == 1
        assert db_session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'invite.redeemed')) == 1


def test_sql_failed_signup_rolls_back_user_membership_and_invite(sql_identity):
    from core.persistence import identity
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    with sql_identity.transaction() as db_session:
        db_session.add(CharacterAssignment(campaign_id='campaign', character_id='hero', user_id=owner['id'], role='owner'))
    code = auth.create_invite('campaign', 'player', character_id='hero')
    with pytest.raises(CharacterAlreadyClaimedError):
        identity.redeem_invite(code, username='new-player', password='password')
    assert auth.get_user_by_username('new-player') is None
    assert auth.get_invite(code)['uses_left'] == 1
    with sql_identity.session() as db_session:
        assert db_session.scalar(select(func.count()).select_from(InviteRedemption)) == 0
        assert db_session.scalar(select(func.count()).select_from(CampaignMembership)) == 1


@pytest.mark.parametrize('invalid', ['no_signup', 'expired', 'exhausted', 'trashed'])
def test_sql_invalid_invite_cannot_create_an_account(sql_identity, invalid):
    from core.persistence import identity
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    code = auth.create_invite('campaign', 'player', creates_account=invalid != 'no_signup',
                              ttl_days=-1 if invalid == 'expired' else 14,
                              uses=0 if invalid == 'exhausted' else 1)
    if invalid == 'trashed':
        from core.persistence.models import utc_now
        with sql_identity.transaction() as db_session:
            db_session.get(Campaign, 'campaign').trashed_at = utc_now()
    with pytest.raises(InviteUnavailableError):
        identity.redeem_invite(code, username='new-player', password='password')
    assert auth.get_user_by_username('new-player') is None


def test_sql_invite_mutations_recheck_actor_after_route_preflight(sql_identity):
    from core.persistence import identity
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    former_gm = auth.create_user('former-gm', 'password')
    with sql_identity.transaction() as db_session:
        db_session.add(CampaignMembership(
            campaign_id='campaign', user_id=former_gm['id'], role='gm',
        ))
    code = auth.create_invite('campaign', 'player', created_by=owner['id'])
    # The route has seen a GM; another transaction commits the demotion before
    # the adapter can take its campaign lock. Neither operation may use that
    # request's old permission decision.
    with sql_identity.transaction() as db_session:
        member = db_session.get(CampaignMembership, ('campaign', former_gm['id']))
        assert member.role == 'gm'
        member.role = 'player'
    with pytest.raises(ValueError, match='permission'):
        identity.create_invite('campaign', 'gm', created_by=former_gm['id'])
    with pytest.raises(ValueError, match='permission'):
        identity.revoke_invite(code, actor_user_id=former_gm['id'])
    with sql_identity.session() as db_session:
        assert db_session.scalar(select(func.count()).select_from(Invitation)) == 1
        assert db_session.get(Invitation, code).revoked_at is None


def test_sql_invite_http_calls_cannot_omit_or_forge_actor(sql_identity):
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    player = auth.create_user('player', 'password')
    with sql_identity.transaction() as db_session:
        db_session.add(CampaignMembership(
            campaign_id='campaign', user_id=player['id'], role='player',
        ))
    code = auth.create_invite('campaign', 'player', created_by=owner['id'])
    app = Flask(__name__)
    app.secret_key = 'test-key'
    with app.test_request_context('/'):
        with pytest.raises(ValueError):
            auth.create_invite('campaign', 'gm')
        with pytest.raises(ValueError):
            auth.revoke_invite(code)
        auth.login_user(player)
        with pytest.raises(ValueError):
            auth.create_invite('campaign', 'gm', created_by=owner['id'])
        with pytest.raises(ValueError):
            auth.revoke_invite(code)
    assert auth.get_invite(code) is not None


def test_shadow_invites_ignore_metadata_and_normalize_legacy_defaults(
    sql_identity, monkeypatch, caplog
):
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    code = auth.create_invite('campaign', 'player', created_by=owner['id'])
    with sql_identity.transaction() as db_session:
        db_session.get(Invitation, code).expires_at = None
    legacy = auth._load_invites()
    legacy['format_version'] = 7
    legacy['invites'][code].update(note='private invite note', expires_at=0)
    legacy['invites'][code].pop('creates_account')
    storage.atomic_write_json(auth.INVITES_FILE, legacy)
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'shadow')
    assert auth._load_invites() == legacy
    assert 'ownership_shadow_mismatch' not in caplog.text
    assert 'ownership_shadow_unavailable' not in caplog.text


@pytest.mark.parametrize(('field', 'replacement'), [
    ('role', 'gm'), ('campaign_id', 'different-campaign'),
    ('character_id', 'different-character'), ('creates_account', False),
    ('uses_left', 20), ('expires_at', 1), ('created_by', 'different-user'),
])
def test_shadow_invites_still_detect_authority_changes(
    sql_identity, monkeypatch, caplog, field, replacement
):
    owner = auth.create_first_admin('owner', 'password')
    seed_campaign(sql_identity, owner)
    code = auth.create_invite('campaign', 'player', created_by=owner['id'])
    legacy = auth._load_invites()
    legacy['invites'][code][field] = replacement
    storage.atomic_write_json(auth.INVITES_FILE, legacy)
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'shadow')
    assert auth._load_invites() == legacy
    assert 'ownership_shadow_mismatch' in caplog.text
    assert code not in caplog.text
