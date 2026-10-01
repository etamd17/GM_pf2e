"""Faults after file publication must retain evidence, never create duplicates."""

from contextlib import contextmanager
import json

import pytest

from core import storage
from core.character_workflows.types import WorkflowError
from test_publication import imported, setup_workflow, cosmere_env


def interrupt_completion(workflow, drafts, monkeypatch):
    """Reject final record persistence after the publishing intent committed."""
    real_transaction = drafts.store.transaction
    @contextmanager
    def fault(context):
        with real_transaction(context) as tx:
            real_put = tx.put_draft
            def put(draft):
                if draft.state == 'committed':
                    raise OSError('simulated completion-record failure')
                return real_put(draft)
            monkeypatch.setattr(tx, 'put_draft', put)
            yield tx
    monkeypatch.setattr(drafts.store, 'transaction', fault)


def test_pending_create_is_not_visible(workflow_env, monkeypatch):
    from core.character_workflows.recovery import assert_character_available, assert_no_pending_workflows
    from core.persistence.character_files import authoritative_document
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError) as exc:
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    assert exc.value.code == 'publication_repair_required'
    pending = drafts.get(env.context(), draft.id)
    assert pending.state == 'publishing'
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    path = env.path.parent / (receipt.target_id + '.json')
    assert path.exists()
    assert authoritative_document(str(path), json.loads(path.read_text(encoding='utf-8'))) is None
    with pytest.raises(WorkflowError):
        assert_character_available(env.cid, receipt.target_id, write=True)
    with pytest.raises(WorkflowError):
        assert_no_pending_workflows(env.cid)
    assert files.refreshed == files.notified == []


def test_commit_uncertainty_preserves_receipt(workflow_env, monkeypatch):
    from core.character_workflows.recovery import recover_publication
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    assert receipt.state == 'publishing'
    recovered = recover_publication(env.context(), receipt.id, store=drafts.store, files=files)
    assert recovered.character_id == receipt.target_id
    assert drafts.get(env.context(), draft.id).state == 'committed'
    retry = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    assert retry == recovered
    assert len(list(env.path.parent.glob('*.json'))) == 2


def test_recovery_never_overwrites_divergent_character(workflow_env, monkeypatch):
    from core.character_workflows.recovery import recover_publication
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    path = env.path.parent / (receipt.target_id + '.json')
    document = json.loads(path.read_text(encoding='utf-8'))
    document['build']['level'] = 10
    storage.atomic_write_json(str(path), document)
    with pytest.raises(WorkflowError) as exc:
        recover_publication(env.context(), receipt.id, store=drafts.store, files=files)
    assert exc.value.code == 'publication_repair_required'
    assert json.loads(path.read_text(encoding='utf-8'))['build']['level'] == 10
    assert drafts.get(env.context(), draft.id).state == 'publishing'


def test_pending_index_does_not_open_nested_write_transaction(workflow_env, monkeypatch):
    if workflow_env.mode != 'sql':
        pytest.skip('SQL row-lock boundary')
    from core.character_workflows.recovery import invalidate_pending_index, pending_receipts
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    with drafts.store.transaction(env.context()):
        invalidate_pending_index(env.cid)
        def forbidden_transaction():
            pytest.fail('A nested write transaction can deadlock on the held campaign row')
        monkeypatch.setattr(env.database, 'transaction', forbidden_transaction)
        assert pending_receipts(env.cid) == ()


def test_pending_create_excluded_from_identity_and_account_cards(workflow_env, monkeypatch):
    from core import campaigns
    from core.character_workflows.identity import resolve_character, resolve_legacy_name
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    assert receipt.target_id not in [card['id'] for card in campaigns.characters_for_user(env.users['owner'])]
    assert resolve_legacy_name(env.cid, 'New Hero') is None
    with pytest.raises(WorkflowError) as exc:
        resolve_character(env.cid, receipt.target_id)
    assert exc.value.status == 404


def test_pending_update_blocks_mutation_and_keeps_dirty_marker(workflow_env, monkeypatch):
    import app
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', env.cid)
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, '_loaded_party_dir', lambda: str(env.path.parent))
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {'Hero': env.path.name})
    actor = app.Character(env.document, env.path.name)
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {'Hero': actor})
    monkeypatch.setattr(app, '_PC_PERSIST_DIRTY', {'Hero'})
    draft = drafts.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    before = actor.current_hp
    with pytest.raises(WorkflowError):
        app.apply_pc_delta('Hero', lambda pc: setattr(pc, 'current_hp', 1))
    assert actor.current_hp == before
    assert app._flush_pc_dirty_unlocked('Hero') is False
    assert 'Hero' in app._PC_PERSIST_DIRTY


def test_pending_targets_block_legacy_batch_before_any_publication(workflow_env, monkeypatch):
    import app
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    before = env.path.read_bytes()
    with pytest.raises(WorkflowError):
        app._save_and_reload_character_batch([('Hero', env.document, str(env.path))])
    assert env.path.read_bytes() == before


def test_pending_create_excluded_from_pf2e_loader_caches(workflow_env, monkeypatch):
    import app
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {})
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {})
    monkeypatch.setattr(app, '_PARTY_DIR_MTIME_CACHE', {})
    monkeypatch.setattr(app, '_PARTY_DIR_LISTING_MTIME', 0)
    app._build_pc_file_cache()
    assert 'New Hero' not in app._PC_FILE_CACHE
    app._sync_party_from_disk()
    assert 'New Hero' not in app.PARTY_LIBRARY


def test_pending_update_keeps_trusted_filename_alias(workflow_env, monkeypatch):
    import app
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {'Hero': env.path.name})
    app._build_pc_file_cache()
    assert app._PC_FILE_CACHE['Hero'] == env.path.name
    assert app.get_pc_file_path('Hero') == str(env.path)


def test_pending_create_removes_cached_actor_before_mtime_shortcut(workflow_env, monkeypatch):
    import app
    from types import SimpleNamespace
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    filename = receipt.metadata['filename']
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {'New Hero': filename})
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {'New Hero': SimpleNamespace(file_path=filename)})
    monkeypatch.setattr(app, '_PARTY_DIR_MTIME_CACHE', {filename: 0})
    monkeypatch.setattr(app, '_PARTY_DIR_LISTING_MTIME', env.path.parent.stat().st_mtime)
    monkeypatch.setattr(app, '_PARTY_WORKFLOW_PENDING_SIGNATURE', None, raising=False)
    app._sync_party_from_disk()
    assert 'New Hero' not in app.PARTY_LIBRARY and 'New Hero' not in app._PC_FILE_CACHE
    assert app.reload_single_character(str(env.path.parent / (receipt.target_id + '.json'))) is False
    assert 'New Hero' not in app.PARTY_LIBRARY


def test_real_app_adapter_prepares_actor_before_digest(workflow_env, monkeypatch):
    import app
    from core.character_workflows.publication import WorkflowService
    from test_drafts import service as draft_service
    env = workflow_env
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', env.cid)
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {})
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {})
    monkeypatch.setattr(app, '_PC_PERSIST_DIRTY', set())
    monkeypatch.setattr(app, '_PARTY_DIR_MTIME_CACHE', {})
    monkeypatch.setattr(app, '_PARTY_DIR_LISTING_MTIME', 0)
    drafts = draft_service(env)
    files = app._WorkflowCharacterFiles()
    workflow = WorkflowService(drafts, drafts.store, drafts.adapters, files)
    # The legacy constructor aliases an existing weapons array. Without the
    # key its default list is actor-only and does not mutate the document.
    draft = drafts.create(env.context(), imported(weapons=[]), request_key='create')
    result = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    document = json.loads((env.path.parent / (result.character_id + '.json')).read_text(encoding='utf-8'))
    assert any(w['name'] == 'Fist' for w in document['build']['weapons'])
    assert 'New Hero' in app.PARTY_LIBRARY
    from core.character_workflows.drafts import digest
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
    assert receipt.metadata['expected_digest'] == digest(document)


@pytest.fixture
def quarantined_combat(workflow_env, monkeypatch):
    import app
    from copy import deepcopy
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported('Hero'), request_key='create', target_id=env.chid)
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    hero = app.Character(deepcopy(env.document), env.path.name)
    hero.instance_id = 'hero-in-combat'
    hero.current_hp = 4
    hero.conditions = {'frightened': 1}
    hero.reaction_used = True
    npcs = []
    for index in range(2):
        npc = app.Character({'build': {'name': f'NPC {index}', 'class': 'Fighter',
                            'ancestry': 'Human'}}, f'npc{index}.json')
        npc.is_pc = False
        npc.instance_id = f'npc-{index}'
        npcs.append(npc)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', env.cid)
    monkeypatch.setattr(app, 'PARTY_DIR', str(env.path.parent))
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {'Hero': hero})
    monkeypatch.setattr(app, '_PC_FILE_CACHE', {'Hero': env.path.name})
    monkeypatch.setattr(app, '_PC_PERSIST_DIRTY', set())
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [*npcs, hero])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setattr(app, 'ROUND_NUMBER', 1)
    monkeypatch.setattr(app, 'ROUND_EVENTS', [])
    monkeypatch.setattr(app, 'TURN_REMINDERS', [])
    for name in ('_persist_encounter_state', '_broadcast_encounter_state', '_combat_log',
                 '_broadcast_pc_state', '_bump_campaign_stat'):
        monkeypatch.setattr(app, name, lambda *args, **kwargs: None)
    monkeypatch.setattr(app, '_is_ajax', lambda: False)
    return app, hero, npcs


@pytest.mark.parametrize('operation', ['heal', 'damage', 'condition', 'start_turn'])
def test_shared_combat_helpers_reject_pending_before_mutation(quarantined_combat, operation):
    app, hero, _ = quarantined_combat
    from copy import deepcopy
    before = (hero.current_hp, deepcopy(hero.conditions), hero.reaction_used)
    with app.app.test_request_context('/tracker'), pytest.raises(WorkflowError):
        if operation in ('heal', 'damage'):
            app._apply_hp_delta(hero.instance_id, 1, operation)
        elif operation == 'condition':
            app._apply_condition_change(hero.instance_id, 'frightened', 'add')
        else:
            app._apply_start_of_turn(hero)
    assert (hero.current_hp, hero.conditions, hero.reaction_used) == before


def test_turn_preflight_rejects_before_outgoing_tick_and_index_change(quarantined_combat, monkeypatch):
    app, hero, npcs = quarantined_combat
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [npcs[0], hero])
    npcs[0].conditions['frightened'] = 2
    with app.app.test_request_context('/tracker'), pytest.raises(WorkflowError):
        app.cycle_turn('next')
    assert app.TURN_INDEX == 0 and app.ROUND_NUMBER == 1
    assert npcs[0].conditions['frightened'] == 2
    assert hero.reaction_used is True


def test_round_event_preflight_does_not_consume_pending_target_event(quarantined_combat, monkeypatch):
    app, hero, npcs = quarantined_combat
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [npcs[0], hero, npcs[1]])
    monkeypatch.setattr(app, 'TURN_INDEX', 2)
    event = {'id': 'event', 'round': 2, 'title': 'Heal', 'payload': {
             'damage': [{'target_ids': [hero.instance_id], 'kind': 'heal', 'dice': '1'}]}}
    app.ROUND_EVENTS.append(event)
    with app.app.test_request_context('/tracker'), pytest.raises(WorkflowError):
        app.cycle_turn('next')
    assert app.TURN_INDEX == 2 and app.ROUND_NUMBER == 1
    assert 'last_fired_round' not in event and hero.current_hp == 4


def test_unrelated_npc_turn_remains_usable(quarantined_combat):
    app, hero, _ = quarantined_combat
    with app.app.test_request_context('/tracker'):
        response = app.cycle_turn('next')
    assert response.status_code == 302 and app.TURN_INDEX == 1
    assert hero.current_hp == 4 and hero.reaction_used is True


@pytest.mark.parametrize('undo', [False, True])
def test_obsidian_preflights_all_targets_before_any_mutation(quarantined_combat, monkeypatch, undo):
    from services import obsidian_sync
    app, hero, npcs = quarantined_combat
    seen = []
    def apply(target_id, *args):
        app._workflow_assert_combatant_mutable(target_id)
        seen.append(target_id)
        return 1
    adapter = {
        'find_combatant': app._find_active_combatant,
        'apply_hp': apply,
        'preflight_targets': lambda ids: [app._workflow_assert_combatant_mutable(i) for i in ids],
        'maybe_remove_defeated': lambda *args: False,
        'persist_encounter': lambda: None,
        'broadcast_encounter': lambda: None,
    }
    ids = [npcs[0].instance_id, hero.instance_id]
    with pytest.raises(WorkflowError):
        if undo:
            monkeypatch.setattr(obsidian_sync, '_last_undoable_event', lambda *args: {
                'command_type': 'adjust_hp', 'result': {'targets': [
                    {'target_id': i, 'old_hp': 5, 'new_hp': 4} for i in ids]}})
            obsidian_sync._undo_last(adapter, 'test', {})
        else:
            obsidian_sync._dispatch(adapter, 'adjust_hp', {
                'target_ids': ids, 'amount': 1, 'action': 'damage'}, runtime={}, campaign_id='test')
    assert seen == []


def test_pending_legacy_http_mutation_returns_safe_503(quarantined_combat, monkeypatch):
    app, hero, _ = quarantined_combat
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_loaded_party_dir', lambda: app.PARTY_DIR)
    hero.actions_remaining = 3
    with app.app.test_client() as client:
        response = client.post('/api/use_action/' + hero.instance_id, json={'cost': 1})
    assert response.status_code == 503
    assert response.get_json()['error'] == 'publication_repair_required'
    assert hero.actions_remaining == 3


@pytest.mark.parametrize('endpoint,args,body', [
    ('pc_recovery_check', {'pc_name': 'Hero'}, {}),
    ('add_pc_effect', {'pc_name': 'Hero'}, {}),
    ('remove_pc_effect', {'pc_name': 'Hero', 'effect_id': 'x'}, {}),
    ('api_treat_wounds', {}, {'target': 'Hero'}),
    ('pc_treat_wounds', {'pc_name': 'Another healer'}, {'target': 'Hero'}),
    ('daily_preparations_all', {}, {}),
    ('api_rest_apply', {}, {'mode': 'short'}),
    ('api_award_xp', {}, {}),
    ('add_party', {}, {}),
    ('roll_all_initiative', {}, {}),
    ('approve_hero_nomination', {}, {'nominee': 'Hero'}),
    ('send_loot_to_player', {}, {'target': 'Hero'}),
    ('add_combatant', {}, {'type': 'pc', 'path': 'Hero'}),
])
def test_request_preflight_checks_actual_mutation_targets(quarantined_combat, endpoint, args, body):
    from types import SimpleNamespace
    from flask import request
    app, _, _ = quarantined_combat
    with app.app.test_request_context('/', method='POST', json=body):
        request.url_rule = SimpleNamespace(endpoint=endpoint)
        request.view_args = args
        with pytest.raises(WorkflowError):
            app._workflow_preflight_request()


@pytest.mark.parametrize('endpoint,body', [
    ('player_whisper', {}), ('session_journal_add', {}), ('log_roll', {}),
    ('log_spell_cast', {}), ('recall_knowledge', {'pc_name': 'Hero'}),
    ('multi_save_damage', {'target_ids': ['hero-in-combat']}),
    ('pc_treat_wounds', {'target': 'Healthy'}),
    ('add_combatant', {'type': 'monster', 'path': 'Hero'}),
])
def test_request_preflight_leaves_noncharacter_writes_usable(quarantined_combat, endpoint, body):
    from types import SimpleNamespace
    from flask import request
    app, _, _ = quarantined_combat
    with app.app.test_request_context('/', method='POST', json=body):
        request.url_rule = SimpleNamespace(endpoint=endpoint)
        request.view_args = {'pc_name': 'Hero'}
        app._workflow_preflight_request()


def test_authorization_scan_skips_unrelated_pending_files(workflow_env, monkeypatch):
    import app
    from core.route_policy import CharacterOwnerSource
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    character = app._authorization_character_context(
        env.cid, 'Hero', CharacterOwnerSource.ROUTE_PC_NAME)
    assert character.character_id == env.chid


def test_authorization_scan_skips_unrelated_pending_update(quarantined_combat, workflow_env):
    from core.route_policy import CharacterOwnerSource
    app, _, _ = quarantined_combat
    # A missing name must stay missing even if a different character is pending.
    assert app._authorization_character_context(
        workflow_env.cid, 'Not Hero', CharacterOwnerSource.ROUTE_PC_NAME) is None
    with pytest.raises(WorkflowError):
        app._authorization_character_context(
            workflow_env.cid, 'Hero', CharacterOwnerSource.ROUTE_PC_NAME)


def test_new_recovery_request_key_is_also_replayable(workflow_env, monkeypatch):
    env = workflow_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='first')
    recovered = workflow.publish(env.context(), draft.id, expected_revision=1, request_key='recover')
    assert workflow.publish(env.context(), draft.id, expected_revision=1, request_key='recover') == recovered


def test_scene_bulk_pending_target_cannot_partially_apply(quarantined_combat, monkeypatch):
    app, hero, npcs = quarantined_combat
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    ids = [npcs[0].instance_id, hero.instance_id]
    monkeypatch.setattr(app._scenes, 'load_scene', lambda *args: {
        'tokens': [{'combatant_id': i} for i in ids]})
    seen = []
    def apply(target_id, *args):
        app._workflow_assert_combatant_mutable(target_id)
        seen.append(target_id)
        return 1
    monkeypatch.setattr(app, '_apply_hp_delta', apply)
    monkeypatch.setattr(app, '_maybe_auto_remove_defeated', lambda *args: False)
    with app.app.test_request_context('/', method='POST', json={
        'action': 'damage', 'amount': 1, 'combatant_ids': ids}), pytest.raises(WorkflowError):
        app.api_scene_bulk_combat('scene')
    assert seen == []


def test_cosmere_pending_update_does_not_break_unrelated_list_or_credit_ledger(cosmere_env, monkeypatch):
    import app
    from flask import request
    from types import SimpleNamespace
    from test_system_adapters import cos_payload, inputs
    env = cosmere_env
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), inputs('cosmere_builder', cos_payload()), request_key='create')
    result = workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
    update = cos_payload()
    update['build']['name'] = 'Renamed'
    draft = drafts.create(env.context(), inputs('cosmere_builder', update),
                          target_id=result.character_id, request_key='edit')
    with monkeypatch.context() as patch:
        interrupt_completion(workflow, drafts, patch)
        with pytest.raises(WorkflowError):
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='edit-publish')
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', env.cid)
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', storage.cosmere_pc_dir(env.cid))
    assert app._list_cosmere_pcs() == []
    with pytest.raises(WorkflowError):
        app._load_cosmere_pc(result.character_id)
    for name in ('Workflow Hero', 'Renamed'):
        with app.app.test_request_context('/', method='POST', json={'recipient': name}):
            request.url_rule = SimpleNamespace(endpoint='api_cosmere_loot_add')
            request.view_args = {}
            with pytest.raises(WorkflowError):
                app._workflow_preflight_request()


@pytest.mark.parametrize('boundary', ['before_commit', 'after_commit', 'after_replace'])
def test_fault_boundaries_recover_in_a_new_process(workflow_env, monkeypatch, boundary):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from sqlalchemy import select
    from core.persistence.models import Character, CharacterWorkflowReceipt, AuditEvent
    env = workflow_env
    if boundary != 'after_replace' and env.mode != 'sql':
        pytest.skip('SQL commit boundary; JSON completion journal tested separately')
    workflow, drafts, files = setup_workflow(env)
    draft = drafts.create(env.context(), imported(), request_key='create')
    with monkeypatch.context() as patch:
        if boundary == 'after_replace':
            write = files.write
            def interrupted_write(record, document):
                write(record, document)
                raise OSError('lost acknowledgment after replace')
            patch.setattr(files, 'write', interrupted_write)
        else:
            transaction = env.database.transaction
            @contextmanager
            def interrupted_transaction():
                completed = False
                with transaction() as session:
                    yield session
                    session.flush()
                    completed = session.scalar(select(CharacterWorkflowReceipt).where(
                        CharacterWorkflowReceipt.operation == 'publish',
                        CharacterWorkflowReceipt.state == 'committed')) is not None
                    if completed and boundary == 'before_commit':
                        raise OSError('connection lost before commit')
                if completed and boundary == 'after_commit':
                    raise OSError('commit acknowledgment lost')
            patch.setattr(env.database, 'transaction', interrupted_transaction)
        with pytest.raises(WorkflowError) as exc:
            workflow.publish(env.context(), draft.id, expected_revision=0, request_key='publish')
        assert exc.value.code == 'publication_repair_required'
    with drafts.store.transaction(env.context()) as tx:
        receipt = next(r for r in tx.list_receipts() if r.operation == 'publish')
        assert receipt.state == ('committed' if boundary == 'after_commit' else 'publishing')
    if env.mode == 'sql':
        with env.database.session() as session:
            assert (session.get(Character, receipt.target_id) is not None) == (boundary == 'after_commit')
    # A real interpreter has no parent's locks, caches, fixture monkeypatches,
    # or open sessions. Recovery must use only durable files/rows.
    script = '''
import json, sys
from pathlib import Path
from core.character_workflows.recovery import default_store, recover_publication
from core.request_context import Principal, resolve_campaign_context
from core import storage
sys.path.insert(0, str(Path.cwd() / 'tests' / 'character_workflows'))
from test_publication import DiskFiles
cid, author, receipt_id = sys.argv[1:]
campaign = json.loads(Path(storage.campaign_file(cid)).read_text(encoding='utf-8'))
ctx = resolve_campaign_context(Principal.user(author), campaign_id=cid,
    campaign=campaign, live_campaign_id=cid)
result = recover_publication(ctx, receipt_id, store=default_store(), files=DiskFiles())
print(result.character_id)
'''
    child_env = dict(os.environ, DATA_DIR=str(env.root.parent), OWNERSHIP_BACKEND=env.mode,
                     DATABASE_URL=env.database.url)
    result = subprocess.run([sys.executable, '-c', script, env.cid, env.users['owner'], receipt.id],
                            env=child_env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == receipt.target_id
    assert drafts.get(env.context(), draft.id).state == 'committed'
    assert len(list(env.path.parent.glob('*.json'))) == 2
    if env.mode == 'sql':
        with env.database.session() as session:
            assert len(list(session.scalars(select(AuditEvent).where(
                AuditEvent.action == 'character.workflow_published')))) == 1
