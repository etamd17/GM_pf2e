"""Flask persistence adapters for Cosmere transactions and portable files."""
from copy import deepcopy
import json
import threading
import uuid

import pytest

from systems.cosmere.origins import path_key_talent


def base_build():
    return {
        "name": "API Hero", "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1, "prc": 1, "sur": 1},
        "talents": [path_key_talent("warrior"), {"id": "test.creation-bonus", "name": "Bonus"}],
        "expertises": [], "epic_choices": [],
    }


def level_two(build):
    result = deepcopy(build)
    result["level"] = 2
    result["skills"]["agi"] += 1
    result["skills"]["hwp"] += 1
    result["talents"].append({"id": "test.level-two", "name": "Level Two"})
    return result


def homebrew_store():
    return {
        "surge": [{"type": "surge", "name": "Sandmastery", "code": "snd",
                   "attribute": "wil"}],
        "radiant_path": [{"type": "radiant_path", "name": "Sandbearer",
                           "slug": "sandbearer", "spren": "Sandspren",
                           "surges": ["snd", "grv"], "philosophy": "Endure."}],
        "talent": [{"type": "talent", "id": "hb:sand-heart",
                    "name": "Sand Heart", "path": "any",
                    "effects": [{"target": "health", "value": 3}]}],
    }


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    import app as host
    directory = tmp_path / "cosmere_pcs"
    directory.mkdir()
    monkeypatch.setattr(host, "COSMERE_PC_DIR", str(directory))
    monkeypatch.setattr(host, "_is_gm", lambda: True)
    monkeypatch.setattr(host, "ACTIVE_CAMPAIGN_ID", None)
    pid = uuid.uuid4().hex
    host._save_cosmere_pc({"id": pid, "system": "cosmere", "name": "API Hero",
                           "build": base_build(), "play_state": {"health": 4}})
    return host, host.app.test_client(), directory, pid


def test_builder_storage_and_workflow_serializer_preserve_canonical_rules_state(api_env):
    host, _client, _directory, pid = api_env
    stored = host._load_cosmere_pc(pid)
    assert stored["schema_version"] == 2 and stored["revision"] == 0
    assert stored["runtime_state"] == stored["play_state"]
    assert stored["runtime_state"] == {"health": 4, "conditions": {}, "effects": []}
    stored["advancement_history"] = []
    rebuilt = host._build_cosmere_document({"build": {**stored["build"], "notes": "changed"}}, stored)
    assert rebuilt["revision"] == 1
    assert rebuilt["rules_binding"] == stored["rules_binding"]
    assert rebuilt["runtime_state"] == stored["runtime_state"]
    assert rebuilt["advancement_history"] == stored["advancement_history"]


def test_storage_and_publication_keep_homebrew_skills_and_derived_values(api_env, monkeypatch):
    host, _client, _directory, _pid = api_env
    monkeypatch.setattr(host, "_cosmere_homebrew_store", lambda cid=None: homebrew_store())
    build = base_build()
    build.update(radiant_order="sandbearer", ideals_sworn=1, is_radiant=True)
    build["skills"].update(snd=1, grv=1)
    build["talents"][-1] = {"id": "hb:sand-heart", "name": "Sand Heart"}
    doc = {"id": uuid.uuid4().hex, "system": "cosmere", "build": build}

    host._save_cosmere_pc(doc)
    stored = host._load_cosmere_pc(doc["id"])
    rebuilt = host._build_cosmere_document({"build": deepcopy(stored["build"])}, stored)

    assert stored["build"]["skills"]["snd"] == 1
    assert rebuilt["build"]["skills"]["snd"] == 1
    assert rebuilt["derived"] == stored["derived"]


def test_builder_cannot_forge_or_erase_server_owned_advancement_grants(api_env):
    from core.character_workflows.types import WorkflowError

    host, _client, _directory, _pid = api_env
    forged = base_build()
    forged["talents"].append({"id": "test.forged", "name": "Forged Talent"})
    forged["advancement_bonus_grants"] = {"talents": 1}
    with pytest.raises(WorkflowError, match="exceeds"):
        host._build_cosmere_document({"build": forged}, None)

    legitimate = base_build()
    legitimate["talents"].append({"id": "test.reward", "name": "Reward Talent"})
    legitimate["advancement_bonus_grants"] = {"talents": 1}
    existing = host._build_cosmere_document({"build": base_build()}, None)
    existing.update(id=uuid.uuid4().hex, revision=1)
    existing["build"] = legitimate
    browser_payload = deepcopy(legitimate)
    browser_payload.pop("advancement_bonus_grants")

    rebuilt = host._build_cosmere_document({"build": browser_payload}, existing)

    assert rebuilt["build"]["advancement_bonus_grants"]["talents"] == 1
    assert any(talent["id"] == "test.reward" for talent in rebuilt["build"]["talents"])


def test_private_projection_redacts_nested_advancement_audit_values():
    from core.character_workflows.privacy import without_owner_private_build_fields

    document = {"build": {"notes": "OWNER SECRET", "session_notes": ["SESSION"]},
        "advancement_history": [{"choices": {"operations": [
            {"path": ["build", "notes"], "before": "OLDER SECRET",
             "after": "OWNER SECRET"},
            {"path": ["build", "level"], "before": 1, "after": 2},
        ]}, "prior_snapshot": {"build": {"notes": "SNAPSHOT SECRET"}}}]}

    projected = without_owner_private_build_fields(document)
    encoded = json.dumps(projected, sort_keys=True)

    assert "SECRET" not in encoded and "SESSION" not in encoded
    assert projected["advancement_history"][0]["choices"]["operations"] == [
        {"path": ["build", "level"], "before": 1, "after": 2}]


def test_advancement_routes_are_revisioned_pure_then_atomic_and_revert(api_env):
    host, client, _directory, pid = api_env
    current = host._load_cosmere_pc(pid)
    build = level_two(current["build"])
    payload = {"pid": pid, "expected_revision": 0, "target_level": 2,
               "build": build, "goal_ids": [], "request_id": uuid.uuid4().hex}
    preview = client.post("/api/cosmere/advancement/preview", json=payload)
    assert preview.status_code == 200 and preview.get_json()["eligible"] is True
    assert host._load_cosmere_pc(pid)["revision"] == 0
    committed = client.post("/api/cosmere/advancement/commit", json=payload)
    assert committed.status_code == 200, committed.get_json()
    body = committed.get_json()
    assert body["revision"] == 1 and body["document"]["build"]["level"] == 2
    assert host._load_cosmere_pc(pid)["revision"] == 1

    stale = client.post("/api/cosmere/advancement/commit", json={
        **payload, "request_id": uuid.uuid4().hex})
    assert stale.status_code == 409 and stale.get_json()["code"] == "stale_revision"
    reverted = client.post("/api/cosmere/advancement/revert", json={
        "pid": pid, "expected_revision": 1, "request_id": uuid.uuid4().hex})
    assert reverted.status_code == 200
    assert reverted.get_json()["document"]["build"]["level"] == 1
    assert host._load_cosmere_pc(pid)["revision"] == 2


def test_mutation_helper_defers_broadcast_and_uses_atomic_character_writer(api_env, monkeypatch):
    host, _client, _directory, pid = api_env
    broadcasts = []
    writes = []
    atomic_write = host._atomic_write_json
    monkeypatch.setattr(host, "sse_broadcast", lambda *args, **kwargs: broadcasts.append((args, kwargs)))
    monkeypatch.setattr(host, "_atomic_write_json", lambda path, value, **options: (
        writes.append((path, deepcopy(value), options)),
        atomic_write(path, value, **options),
    )[1])
    changed = host._mutate_cosmere_pc(
        pid, 0,
        lambda doc: {**doc, "runtime_state": {**doc["runtime_state"], "health": 3}},
    )
    assert changed["revision"] == 1 and host._load_cosmere_pc(pid)["runtime_state"]["health"] == 3
    assert broadcasts == []
    assert len(writes) == 1 and writes[0][0].endswith(pid + ".json")


def test_concurrent_mutations_allow_one_winner_and_one_stale_writer(api_env):
    from systems.cosmere.documents import CharacterRevisionError

    host, _client, _directory, pid = api_env
    ready = threading.Barrier(3)
    results = []

    def mutate(health):
        ready.wait()
        try:
            changed = host._mutate_cosmere_pc(
                pid, 0,
                lambda doc: {**doc, "runtime_state": {
                    **doc["runtime_state"], "health": health}},
            )
            results.append(("ok", changed["revision"], health))
        except CharacterRevisionError as error:
            results.append((error.code, error.status, health))

    threads = [threading.Thread(target=mutate, args=(health,)) for health in (2, 3)]
    for thread in threads:
        thread.start()
    ready.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert sorted(result[0] for result in results) == ["ok", "stale_revision"]
    stored = host._load_cosmere_pc(pid)
    assert stored["revision"] == 1 and stored["runtime_state"]["health"] in {2, 3}


def test_sql_backed_revisioned_mutation_keeps_character_checksum_synchronized(tmp_path):
    from tests.persistence.test_runtime_http import run_sql

    run_sql(tmp_path, """
        import hashlib
        from core.persistence import Character, ownership, runtime
        from systems.cosmere.migration import migrate_character_directory

        gm = auth.create_first_admin('gm', 'secret1')
        camp = campaigns.create_campaign('Table', 'cosmere', gm['id'])
        cid = camp['id']
        application._bind_campaign_paths(cid)
        build = {
            'name': 'Checksum Hero', 'level': 1, 'path': 'warrior',
            'ancestry': 'Human',
            'attributes': {'str': 2, 'spd': 2, 'int': 2,
                           'wil': 2, 'awa': 2, 'pre': 2},
            'skills': {'ath': 1, 'agi': 1, 'hwp': 1, 'prc': 1, 'sur': 1},
            'talents': [
                {'id': 'path.warrior.key', 'name': 'Warrior Key'},
                {'id': 'test.creation-bonus', 'name': 'Bonus'},
            ],
            'expertises': [], 'epic_choices': [],
        }
        with application.app.test_request_context('/'):
            from flask import session
            session['user_id'] = gm['id']
            session['auth_session_version'] = gm.get('session_version', 0)
            session['active_campaign_id'] = cid
            pid = application._save_cosmere_pc({
                'system': 'cosmere', 'name': 'Checksum Hero', 'build': build,
                'runtime_state': {'health': 4},
            })
            changed = application._mutate_cosmere_pc(
                pid, 0, lambda doc: {**doc, 'runtime_state': {
                    **doc['runtime_state'], 'health': 3}})
        path = Path(storage.cosmere_pc_dir(cid)) / (pid + '.json')
        payload = json.loads(path.read_text(encoding='utf-8'))
        checksum = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode('utf-8')).hexdigest()
        with runtime.database().session() as db:
            row = db.get(Character, pid)
            assert row.content_checksum == checksum
        assert changed['revision'] == payload['revision'] == 1
        assert payload['runtime_state']['health'] == 3

        legacy = dict(payload)
        for key in ('schema_version', 'revision', 'rules_binding', 'origin_world',
                    'era', 'derived', 'runtime_state', 'advancement_history',
                    'cosmere_theme'):
            legacy.pop(key, None)
        ownership.register_character(
            cid, 'cosmere_pcs', path.name, legacy,
            write_document=lambda document: storage.atomic_write_json(str(path), document))
        with application.app.test_request_context('/'):
            from flask import session
            session['user_id'] = gm['id']
            session['auth_session_version'] = gm.get('session_version', 0)
            session['active_campaign_id'] = cid
            report = migrate_character_directory(
                storage.cosmere_pc_dir(cid), apply=True, run_id='sql-checksum-test',
                writer=lambda target, document: application._atomic_write_json(
                    str(target), document))
        assert report['changed'] == 1
        migrated = json.loads(path.read_text(encoding='utf-8'))
        migrated_checksum = hashlib.sha256(json.dumps(
            migrated, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode('utf-8')).hexdigest()
        with runtime.database().session() as db:
            row = db.get(Character, pid)
            assert row.content_checksum == migrated_checksum
        assert migrated['schema_version'] == 2
    """)


def test_editor_advancement_preserves_but_never_returns_owner_private_fields(tmp_path):
    from tests.persistence.test_runtime_http import run_sql

    run_sql(tmp_path, """
        import uuid
        from core.persistence import CharacterAssignment, runtime
        from systems.cosmere.origins import path_key_talent

        gm = auth.create_first_admin('gm', 'secret1')
        owner = auth.create_user('owner', 'secret1')
        editor = auth.create_user('editor', 'secret1')
        camp = campaigns.create_campaign('Table', 'cosmere', gm['id'])
        cid = camp['id']
        campaigns.add_member(cid, owner['id'], 'player')
        campaigns.add_member(cid, editor['id'], 'player')
        application._bind_campaign_paths(cid)
        build = {
            'name': 'Private Hero', 'level': 1, 'path': 'warrior',
            'ancestry': 'Human',
            'attributes': {'str': 2, 'spd': 2, 'int': 2,
                           'wil': 2, 'awa': 2, 'pre': 2},
            'skills': {'ath': 1, 'agi': 1, 'hwp': 1, 'prc': 1, 'sur': 1},
            'talents': [path_key_talent('warrior'),
                        {'id': 'test.creation-bonus', 'name': 'Bonus'}],
            'expertises': [], 'epic_choices': [],
            'notes': 'OWNER PRIVATE NOTE',
            'session_notes': [{'date': 'today', 'text': 'OWNER SESSION SECRET'}],
        }
        with application.app.test_request_context('/'):
            from flask import session
            session['user_id'] = owner['id']
            session['auth_session_version'] = owner.get('session_version', 0)
            session['active_campaign_id'] = cid
            pid = application._save_cosmere_pc({
                'system': 'cosmere', 'name': 'Private Hero', 'build': build,
                'runtime_state': {'health': 4},
            })
        with runtime.database().transaction() as db:
            db.add(CharacterAssignment(campaign_id=cid, character_id=pid,
                                       user_id=editor['id'], role='editor'))

        client = application.app.test_client()
        with client.session_transaction() as state:
            state['user_id'] = editor['id']
            state['auth_session_version'] = editor.get('session_version', 0)
            state['active_campaign_id'] = cid
        current = application._load_cosmere_pc(pid)
        proposed = dict(current['build'])
        proposed.pop('notes', None)
        proposed.pop('session_notes', None)
        proposed['level'] = 2
        proposed['skills'] = dict(proposed['skills'])
        proposed['skills']['agi'] += 1
        proposed['skills']['hwp'] += 1
        proposed['talents'] = list(proposed['talents']) + [
            {'id': 'test.level-two', 'name': 'Level Two'}]
        request = {'pid': pid, 'expected_revision': 0, 'target_level': 2,
                   'build': proposed, 'goal_ids': [],
                   'request_id': uuid.uuid4().hex}

        preview = client.post('/api/cosmere/advancement/preview', json=request)
        assert preview.status_code == 200, preview.data
        assert 'OWNER PRIVATE' not in preview.get_data(as_text=True)
        committed = client.post('/api/cosmere/advancement/commit', json=request)
        assert committed.status_code == 200, committed.data
        assert 'OWNER PRIVATE' not in committed.get_data(as_text=True)
        assert 'OWNER SESSION SECRET' not in committed.get_data(as_text=True)
        saved = application._load_cosmere_pc(pid)
        assert saved['build']['notes'] == 'OWNER PRIVATE NOTE'
        assert saved['build']['session_notes'][0]['text'] == 'OWNER SESSION SECRET'
        assert not any(operation['path'][:2] in (
            ['build', 'notes'], ['build', 'session_notes'])
            for operation in saved['advancement_history'][-1]['choices']['operations'])

        reverted = client.post('/api/cosmere/advancement/revert', json={
            'pid': pid, 'expected_revision': 1, 'request_id': uuid.uuid4().hex})
        assert reverted.status_code == 200, reverted.data
        assert 'OWNER PRIVATE' not in reverted.get_data(as_text=True)
        assert 'OWNER SESSION SECRET' not in reverted.get_data(as_text=True)
        saved = application._load_cosmere_pc(pid)
        assert saved['build']['notes'] == 'OWNER PRIVATE NOTE'
    """)


def test_portable_export_import_and_migration_routes(api_env):
    host, client, directory, pid = api_env
    exported = client.get(f"/api/cosmere/pc/{pid}/export")
    assert exported.status_code == 200
    payload = exported.get_json()
    assert payload["format"] == "cosmere-character"
    imported = client.post("/api/cosmere/pc/import", json=payload)
    assert imported.status_code == 200, imported.get_json()
    imported_id = imported.get_json()["id"]
    assert imported_id != pid and host._load_cosmere_pc(imported_id)["revision"] == 0

    # Put one actual legacy document in the store so the real migration route
    # reports and applies a change through the checksum-aware writer.
    legacy_id = uuid.uuid4().hex
    legacy_path = directory / f"{legacy_id}.json"
    legacy_path.write_text(json.dumps({"id": legacy_id, "system": "cosmere",
        "name": "Legacy", "build": base_build(), "play_state": {"health": 2}}), encoding="utf-8")
    dry = client.post("/api/cosmere/migration/characters", json={"apply": False})
    assert dry.status_code == 200 and dry.get_json()["changed"] == 1
    invalid = client.post("/api/cosmere/migration/characters", json={
        "apply": True, "run_id": "../escape"})
    assert invalid.status_code == 422
    assert invalid.get_json()["code"] == "invalid_migration"
    assert json.loads(legacy_path.read_text(encoding="utf-8")).get("schema_version") is None
    applied = client.post("/api/cosmere/migration/characters", json={"apply": True,
                                                                     "run_id": "api-test"})
    assert applied.status_code == 200 and applied.get_json()["changed"] == 1
    assert json.loads(legacy_path.read_text(encoding="utf-8"))["schema_version"] == 2
    again = client.post("/api/cosmere/migration/characters", json={"apply": True,
                                                                   "run_id": "unused"})
    assert again.status_code == 200 and again.get_json()["changed"] == 0
