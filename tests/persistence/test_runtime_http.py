"""Exercise SQL authority through the real Flask boundary in isolated processes."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[2]


def run_sql(tmp_path, body, *, schema=True, backend="sql"):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    environment = dict(os.environ)
    environment.update(
        DATA_DIR=str(data_dir),
        DATABASE_URL="sqlite+pysqlite:///" + str(tmp_path / "identity.db"),
        OWNERSHIP_BACKEND=backend,
        SECRET_KEY="isolated-sql-http-test-only",
        GM_PASSWORD="",
        APP_ENV="development",
        PYTHONDONTWRITEBYTECODE="1",
    )
    script = "import os, json\nfrom pathlib import Path\n"
    if schema:
        script += "from core.persistence import Database\nDatabase(os.environ['DATABASE_URL']).create_schema()\n"
    script += "import app as application\nfrom core import auth, campaigns, storage\n"
    script += "application.app.config.update(TESTING=True)\n"
    script += textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=environment,
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("backend", ["sql", "invalid"])
def test_database_outage_never_opens_legacy_gm_access(tmp_path, backend):
    run_sql(tmp_path, """
        client = application.app.test_client()
        assert client.get('/live').status_code == 200
        assert client.get('/ready').status_code == 503
        assert client.get('/health').status_code == 503
        assert client.get('/setup').status_code == 503
        assert client.post('/api/clear_encounter').status_code == 503
    """, schema=False, backend=backend)


def test_sql_character_reads_ignore_forged_json_ownership(tmp_path):
    run_sql(tmp_path, """
        from core.persistence import ownership
        gm = auth.create_first_admin('gm', 'secret1')
        owner = auth.create_user('owner', 'secret1')
        attacker = auth.create_user('attacker', 'secret1')
        camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
        cid = camp['id']
        campaigns.add_member(cid, owner['id'], 'player')
        campaigns.add_member(cid, attacker['id'], 'player')
        doc = storage.wrap_character(storage.new_id(), cid, 'pf2e',
            {'build': {'name': 'Hero'}}, owner_user_id=owner['id'])
        path = Path(storage.party_dir(cid)) / 'hero.json'
        ownership.register_character(cid, 'party_data', path.name, doc,
            owner_user_id=owner['id'])
        forged = dict(doc, owner_user_id=attacker['id'],
            editor_user_ids=[attacker['id']], viewer_user_ids=[attacker['id']])
        storage.atomic_write_json(str(path), forged)
        # Stale files cannot make an account an admin or a campaign GM.
        storage.atomic_write_json(storage.USERS_FILE, {'users': {
            attacker['id']: dict(attacker, is_admin=True)}})
        storage.atomic_write_json(storage.campaign_file(cid), dict(camp,
            members=[{'user_id': attacker['id'], 'role': 'gm'}]))
        with application.app.test_request_context('/'):
            from flask import session
            session['user_id'] = attacker['id']
            session['auth_session_version'] = 0
            session['active_campaign_id'] = cid
            assert not application._is_gm()
            assert not application._user_owns_pc(attacker['id'], 'Hero')
            assert application._user_owns_pc(owner['id'], 'Hero')
            assert application._my_pc_names(attacker['id']) == []
            assert application._chronicle_owned_pc_slugs(attacker['id']) == set()
            assert application._scene_character_records(cid)[doc['id']]['owner_user_id'] == owner['id']
            context = application._authorization_character_context(cid, 'Hero',
                application._CharacterOwnerSource.ROUTE_PC_NAME)
            assert context.owner_user_id == owner['id']
            assert attacker['id'] not in context.editor_user_ids
        assert json.loads(path.read_text()) == forged
    """)


def test_join_commits_sql_membership_claim_and_invite_without_json_mutation(tmp_path):
    run_sql(tmp_path, """
        from core.persistence import ownership, runtime, Invite, InviteRedemption
        from sqlalchemy import select, func
        gm = auth.create_first_admin('gm', 'secret1')
        camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
        cid = camp['id']
        doc = storage.wrap_character(storage.new_id(), cid, 'pf2e',
            {'build': {'name': 'Hero'}}, owner_user_id=None)
        path = Path(storage.party_dir(cid)) / 'hero.json'
        storage.atomic_write_json(str(path), doc)
        ownership.register_character(cid, 'party_data', path.name, doc)
        code = auth.create_invite(cid, 'player', character_id=doc['id'], created_by=gm['id'])
        before = path.read_bytes()
        client = application.app.test_client()
        response = client.post('/join', data={
            'code': code, 'username': 'new-player', 'password': 'secret1'})
        assert response.status_code == 302, response.data
        user = auth.get_user_by_username('new-player')
        assert campaigns.user_role(campaigns.get_campaign(cid), user['id']) == 'player'
        assert ownership.authoritative_document(cid, doc)['owner_user_id'] == user['id']
        assert auth.get_invite(code) is None
        with runtime.database().session() as db:
            assert db.scalar(select(func.count()).select_from(InviteRedemption)) == 1
        assert path.read_bytes() == before
        assert not Path(auth.INVITES_FILE).exists()
        retry = client.post('/join', data={'code': code})
        assert retry.status_code == 302, retry.data
        with runtime.database().session() as db:
            assert db.scalar(select(func.count()).select_from(InviteRedemption)) == 1
    """)


def test_cosmere_character_write_and_release_use_sql_authority(tmp_path):
    run_sql(tmp_path, """
        from core.persistence import ownership
        from flask import session
        gm = auth.create_first_admin('gm', 'secret1')
        player = auth.create_user('player', 'secret1')
        camp = campaigns.create_campaign('Table', 'cosmere', gm['id'])
        cid = camp['id']
        campaigns.add_member(cid, player['id'], 'player')
        application._bind_campaign_paths(cid)
        with application.app.test_request_context('/'):
            session['user_id'] = player['id']
            session['auth_session_version'] = 0
            session['active_campaign_id'] = cid
            doc = {'name': 'Hero', 'build': {'name': 'Hero', 'level': 1},
                'system': 'cosmere', 'owner_user_id': gm['id']}
            pid = application._save_cosmere_pc(doc)
            saved = application._load_cosmere_pc(pid)
            assert saved['owner_user_id'] == player['id']
        ownership.release_character(cid, pid, gm['id'])
        assert application._load_cosmere_pc(pid)['owner_user_id'] is None
    """)


@pytest.mark.parametrize('fault', ['malformed', 'infrastructure'])
def test_sql_import_distinguishes_bad_archives_from_store_outages(tmp_path, fault):
    run_sql(tmp_path, f'fault = {fault!r}\n' + textwrap.dedent("""
        import io, logging, zipfile
        from core.persistence import campaign_import, runtime
        gm = auth.create_first_admin('gm', 'secret1')
        client = application.app.test_client()
        with client.session_transaction() as browser:
            browser['user_id'] = gm['id']
            browser['auth_session_version'] = 0
            browser['_csrf'] = 'request-token'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('campaign.json', json.dumps({
                'id': 'a' * 32, 'name': 'Restored', 'system': 'pf2e'}))
            if fault == 'malformed':
                archive.writestr('scenes/bad.json', '{invalid-json')
        if fault == 'infrastructure':
            def unavailable(*args, **kwargs):
                raise runtime.StoreUnavailable('SECRET_DATABASE_CONNECTION_DIAGNOSTIC')
            campaign_import.import_staged_campaign = unavailable
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        application.app.logger.addHandler(handler)
        response = client.post('/campaign/import', data={
            '_csrf': 'request-token', 'backup': (io.BytesIO(buffer.getvalue()), 'backup.zip')})
        application.app.logger.removeHandler(handler)
        expected = 400 if fault == 'malformed' else 503
        assert response.status_code == expected, (response.status_code, response.data)
        assert 'SECRET_DATABASE_CONNECTION_DIAGNOSTIC' not in response.get_data(as_text=True)
        assert 'SECRET_DATABASE_CONNECTION_DIAGNOSTIC' not in logs.getvalue()
        assert campaigns.list_campaigns() == []
        assert list(Path(storage.CAMPAIGNS_DIR).iterdir()) == []
    """))


def test_sql_export_returns_not_found_when_campaign_disappears(tmp_path):
    run_sql(tmp_path, """
        gm = auth.create_first_admin('gm', 'secret1')
        camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
        client = application.app.test_client()
        with client.session_transaction() as browser:
            browser['user_id'] = gm['id']
            browser['auth_session_version'] = 0
        application._backups.write_campaign_archive = lambda *args, **kwargs: False
        response = client.get('/campaign/' + camp['id'] + '/export')
        assert response.status_code == 404, (response.status_code, response.data[:200])
        assert response.mimetype != 'application/zip'
    """)


def test_sql_database_constraint_errors_are_sanitized_conflicts(tmp_path):
    run_sql(tmp_path, """
        import io, logging
        from core.persistence import runtime, User
        from sqlalchemy.exc import DataError
        gm = auth.create_first_admin('gm', 'secret1')
        camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
        client = application.app.test_client()
        with client.session_transaction() as browser:
            browser['user_id'] = gm['id']
            browser['auth_session_version'] = 0
            browser['_csrf'] = 'request-token'
        secret = 'PASSWORD_HASH_MUST_NEVER_REACH_LOGS'
        def integrity_fault(*args, **kwargs):
            with runtime.database().transaction() as database_session:
                database_session.add(User(id='duplicate', username='gm', normalized_username='gm',
                    display_name='Duplicate', password_hash=secret))
                database_session.flush()
        def data_fault(*args, **kwargs):
            raise DataError('INSERT INTO users VALUES (:password_hash)',
                {'password_hash': secret}, RuntimeError('sensitive driver diagnostic ' + secret))
        for fault in (integrity_fault, data_fault):
            auth.create_invite = fault
            logs = io.StringIO()
            handler = logging.StreamHandler(logs)
            application.app.logger.addHandler(handler)
            response = client.post('/campaign/' + camp['id'] + '/invite',
                data={'_csrf': 'request-token', 'role': 'player'})
            application.app.logger.removeHandler(handler)
            assert response.status_code == 409, (response.status_code, response.data)
            assert secret not in response.get_data(as_text=True)
            assert secret not in logs.getvalue()
            assert 'INSERT INTO' not in logs.getvalue()
            assert auth.get_user_by_username('gm')['id'] == gm['id']
    """)


def test_sql_export_import_roundtrip_creates_new_unclaimed_identities(tmp_path):
    run_sql(tmp_path, """
        import io, zipfile
        from core.persistence import ownership, runtime, Character, CharacterAssignment
        from sqlalchemy import select, func
        gm = auth.create_first_admin('gm', 'secret1')
        owner = auth.create_user('owner', 'secret1')
        camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
        cid = camp['id']
        campaigns.add_member(cid, owner['id'], 'player')
        doc = storage.wrap_character(storage.new_id(), cid, 'pf2e',
            {'build': {'name': 'Hero'}}, owner_user_id=owner['id'])
        path = Path(storage.party_dir(cid)) / 'hero.json'
        ownership.register_character(cid, 'party_data', path.name, doc,
            owner_user_id=owner['id'])
        storage.atomic_write_json(str(path), dict(doc, owner_user_id='forged-owner',
            editor_user_ids=['forged-editor'], viewer_user_ids=['forged-viewer']))
        storage.atomic_write_json(storage.campaign_file(cid), dict(camp,
            created_by='forged-creator', members=[{'user_id': 'forged-member', 'role': 'gm'}]))
        scene = Path(storage.scenes_dir(cid)) / (storage.new_id() + '.json')
        storage.atomic_write_json(str(scene), {'characters': {doc['id']: {'character_id': doc['id']}}})
        client = application.app.test_client()
        with client.session_transaction() as browser:
            browser['user_id'] = gm['id']
            browser['auth_session_version'] = 0
            browser['_csrf'] = 'request-token'
        exported = client.get('/campaign/' + cid + '/export')
        assert exported.status_code == 200, exported.data
        with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
            exported_hero = json.loads(archive.read('party_data/hero.json'))
            assert exported_hero['owner_user_id'] == owner['id']
            assert exported_hero['editor_user_ids'] == []
            assert exported_hero['viewer_user_ids'] == []
        restored = client.post('/campaign/import', data={'_csrf': 'request-token',
            'backup': (io.BytesIO(exported.data), 'campaign.zip')})
        assert restored.status_code == 200, restored.data
        new_cid = restored.get_json()['id']
        assert new_cid != cid
        new_campaign = campaigns.get_campaign(new_cid)
        assert new_campaign['members'] == [{'user_id': gm['id'], 'role': 'gm'}]
        assert new_campaign['created_by'] == gm['id']
        with runtime.database().session() as database_session:
            character = database_session.scalar(select(Character).where(Character.campaign_id == new_cid))
            assert character is not None and character.id != doc['id']
            assert database_session.get(Character, doc['id']).campaign_id == cid
            assert database_session.scalar(select(func.count()).select_from(CharacterAssignment)
                .where(CharacterAssignment.campaign_id == new_cid)) == 0
            imported_hero = storage.load_json(str(Path(storage.party_dir(new_cid)) / character.legacy_file))
            assert imported_hero['owner_user_id'] is None
            assert imported_hero['editor_user_ids'] == []
            assert imported_hero['viewer_user_ids'] == []
            imported_scene = storage.load_json(str(Path(storage.scenes_dir(new_cid)) / scene.name))
            assert imported_scene['characters'] == {character.id: {'character_id': character.id}}
        assert ownership.authoritative_document(cid, doc)['owner_user_id'] == owner['id']
    """)
