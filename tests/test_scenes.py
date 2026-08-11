from __future__ import annotations

import json
import queue
from types import SimpleNamespace

import pytest

import app
from core import scenes, storage
from services import scene_sync


CID = 'a' * 32


@pytest.fixture
def scene_store(tmp_path, monkeypatch):
    campaigns = tmp_path / 'campaigns'
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(campaigns))
    storage.ensure_campaign_dirs(CID)
    return campaigns


def test_scene_create_is_campaign_scoped_and_not_yet_on_the_table(scene_store):
    # Creating a scene used to put it straight in front of the players, which
    # made prep during a live session impossible. A new scene is prep until the
    # GM pushes it -- see test_map_scene_lifecycle.py.
    scene = scenes.create_scene(CID, 'Vault of Ash', width=1200, height=800, grid_size=60)
    assert scene['campaign_id'] == CID
    assert scenes.load_scene(CID, scene['id'])['name'] == 'Vault of Ash'
    assert scenes.table_scene_id(CID) is None
    assert storage.scene_file(CID, scene['id']).startswith(str(scene_store))


def test_legacy_scene_store_works_without_campaign_id(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    scene = scenes.create_scene(None, 'Legacy Table')
    assert scene['campaign_id'] is None
    assert scenes.table_scene_id(None) is None
    # ...but the GM still lands somewhere when they open /map.
    assert scenes.default_open_scene_id(None) == scene['id']
    assert scenes.load_scene(None, scene['id'])['name'] == 'Legacy Table'
    assert storage.scene_file(None, scene['id']).startswith(str(tmp_path))


def test_scene_revision_increments_on_durable_change(scene_store):
    scene = scenes.create_scene(CID, 'Revision Test')
    prior = scene['revision']
    scenes.add_token(scene, name='Hero', x=70, y=70)
    scenes.save_scene(CID, scene)
    assert scenes.load_scene(CID, scene['id'])['revision'] == prior + 1


def test_old_scene_tokens_receive_new_map_defaults(scene_store):
    scene = scenes.create_scene(CID, 'Old Scene')
    scene['schema_version'] = 1
    scene['tokens'] = [{'id': 'old-token', 'name': 'Legacy', 'x': 10, 'y': 20}]
    storage.atomic_write_json(storage.scene_file(CID, scene['id']), scene, indent=2)
    loaded = scenes.load_scene(CID, scene['id'])
    token = loaded['tokens'][0]
    assert token['locked'] is False
    assert token['show_nameplate'] is True
    assert token['image_focus'] == {'x': 50, 'y': 50}
    # revealed_cells arrived with region fog (stage 4c); an old scene must
    # gain it empty rather than blowing up on load.
    assert loaded['fog'] == {'enabled': False, 'operations': [], 'revealed_cells': []}
    assert loaded['walls'] == []
    assert loaded['lights'] == []
    assert loaded['templates'] == []
    assert loaded['settings']['dynamic_lighting'] is False


def test_live_state_is_projected_not_persisted(scene_store):
    scene = scenes.create_scene(CID, 'Projection')
    token = scenes.add_token(scene, name='Hero', character_id='char-1')
    scenes.save_scene(CID, scene)
    projected = scene_sync.project_scene(scene, {}, {
        'char-1': {'name': 'Hero', 'current_hp': 19, 'max_hp': 30,
                   'conditions': {'frightened': 1}}
    })
    assert projected['tokens'][0]['live']['current_hp'] == 19
    stored = scenes.load_scene(CID, scene['id'])
    assert 'live' not in stored['tokens'][0]
    assert 'current_hp' not in stored['tokens'][0]
    assert stored['tokens'][0]['id'] == token['id']


def test_player_projection_removes_hidden_tokens_and_account_ids(scene_store):
    scene = scenes.create_scene(CID, 'Visibility')
    scenes.add_token(scene, name='Hero', controller_user_id='user-secret')
    scenes.add_token(scene, name='Hidden Foe', visible_to_players=False)
    projected = scene_sync.project_scene(scene, {}, {}, player=True)
    assert [token['name'] for token in projected['tokens']] == ['Hero']
    assert 'controller_user_id' not in projected['tokens'][0]


# ==========================================================================
# Player-facing health policy.
#
# The tracker has coarsened non-PC health for players since the hidden-NPC
# leak fix (ROADMAP.md, session-critical item 1): a monster gets an
# hp_status of "" / "Wounded" / "Dead" and never a number, while PC health
# stays exact because the party shares it. The map projects the same live
# state onto tokens, so it has to land on the same policy -- see
# app.py::_player_state_payload for the original.
# ==========================================================================

def test_player_projection_never_carries_exact_npc_hp(scene_store):
    scene = scenes.create_scene(CID, 'NPC Health')
    scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    live = {'ogre-1': {'name': 'Ogre', 'is_pc': False, 'system': 'pf2e',
                       'current_hp': 12, 'max_hp': 40, 'conditions': {},
                       'hp_status': 'Wounded', 'hp_color': 'text-orange-400'}}

    gm_token = scene_sync.project_scene(scene, live, {})['tokens'][0]
    assert gm_token['live']['current_hp'] == 12
    assert gm_token['live']['max_hp'] == 40

    player_token = scene_sync.project_scene(scene, live, {}, player=True)['tokens'][0]
    assert 'current_hp' not in player_token['live']
    assert 'max_hp' not in player_token['live']
    assert player_token['live']['hp_status'] == 'Wounded'
    # The raw numbers must not survive anywhere else in the payload either.
    assert '"current_hp"' not in json.dumps(player_token)


def test_player_projection_keeps_exact_pc_hp(scene_store):
    # PC health is shared knowledge at the table -- the tracker sends players
    # current_hp/max_hp for PCs, so the map must not coarsen those.
    scene = scenes.create_scene(CID, 'PC Health')
    scenes.add_token(scene, name='Hero', character_id='char-1', is_pc=True)
    live = {'char-1': {'name': 'Hero', 'is_pc': True, 'system': 'pf2e',
                       'current_hp': 19, 'max_hp': 30, 'conditions': {}}}
    player_token = scene_sync.project_scene(scene, {}, live, player=True)['tokens'][0]
    assert player_token['live']['current_hp'] == 19
    assert player_token['live']['max_hp'] == 30


@pytest.mark.parametrize('current_hp, max_hp, expected', [
    (40, 40, ''),            # healthy -> players are told nothing
    (21, 40, ''),            # just above the halfway mark
    (20, 40, 'Wounded'),     # exactly half is already Wounded
    (1, 40, 'Wounded'),
    (0, 40, 'Dead'),
])
def test_npc_hp_status_thresholds_match_the_tracker(current_hp, max_hp, expected):
    status, _color = app._npc_hp_status(current_hp, max_hp)
    assert status == expected


def test_scene_api_player_read_coarsens_npc_hp(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Wire Check')
    scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    scenes.save_scene(CID, scene)
    combatant = SimpleNamespace(
        instance_id='ogre-1', name='Ogre', is_pc=False, system='pf2e',
        current_hp=12, hp=40, conditions={}, visible_to_players=True,
    )
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [combatant])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setattr(app, '_is_gm', lambda: False)

    # The scene API is GM-only now (GM_API_PREFIXES), so a non-GM gets nothing
    # at all -- assert that first, because it is the actual boundary.
    assert scene_client.get(f'/api/scenes/{scene["id"]}').status_code == 403

    # The coarsening itself still has to hold: player_payload feeds the shared
    # table screen, so a regression here would surface there instead of here.
    body = json.dumps(app._scene_payload(scenes.load_scene(CID, scene['id']),
                                         player=True))
    assert 'Ogre' in body            # the token itself is still there
    assert '"current_hp"' not in body
    assert '"max_hp"' not in body
    assert '"hp_status": "Wounded"' in body.replace('"hp_status":"Wounded"',
                                                    '"hp_status": "Wounded"')


@pytest.fixture
def scene_client(scene_store, monkeypatch):
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_scene_character_records', lambda _cid: {})
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [])
    with app.app.test_client() as client:
        yield client


def test_scene_api_create_add_move_and_read(scene_client):
    created = scene_client.post('/api/scenes', json={
        'name': 'API Scene', 'width': 1000, 'height': 700, 'grid_size': 50,
    })
    assert created.status_code == 201
    scene = created.get_json()['scene']
    sid = scene['id']

    added = scene_client.post(f'/api/scenes/{sid}/tokens', json={
        'name': 'Goblin', 'x': 100, 'y': 150,
    })
    assert added.status_code == 201
    token = added.get_json()['token']

    moved = scene_client.patch(f'/api/scenes/{sid}/tokens/{token["id"]}', json={
        'x': 250, 'y': 300,
    })
    assert moved.status_code == 200
    moved_token = moved.get_json()['scene']['tokens'][0]
    assert (moved_token['x'], moved_token['y']) == (250, 300)

    fetched = scene_client.get(f'/api/scenes/{sid}')
    assert fetched.status_code == 200
    assert fetched.get_json()['scene']['tokens'][0]['name'] == 'Goblin'


def test_scene_api_updates_grid_and_token_inspector_fields(scene_client):
    scene = scenes.create_scene(CID, 'Inspector')
    token = scenes.add_token(scene, name='Goblin')
    scenes.save_scene(CID, scene)

    grid_response = scene_client.patch(f'/api/scenes/{scene["id"]}', json={
        'grid': {'size': 64, 'offset_x': 81, 'offset_y': -7},
    })
    assert grid_response.status_code == 200
    grid = grid_response.get_json()['scene']['grid']
    assert grid['offset_x'] == 17
    assert grid['offset_y'] == 57

    token_response = scene_client.patch(
        f'/api/scenes/{scene["id"]}/tokens/{token["id"]}', json={
            'name': 'Scout', 'size': 2, 'color': '#123abc',
            'visible_to_players': False, 'locked': True,
            'show_nameplate': False, 'controller_character_id': '',
        })
    assert token_response.status_code == 200
    updated = token_response.get_json()['scene']['tokens'][0]
    assert updated['name'] == 'Scout'
    assert updated['size'] == 2
    assert updated['locked'] is True
    assert updated['show_nameplate'] is False

    blocked_move = scene_client.patch(
        f'/api/scenes/{scene["id"]}/tokens/{token["id"]}', json={'x': 400, 'y': 400})
    assert blocked_move.status_code == 403


def test_sync_encounter_is_idempotent(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Encounter Sync')
    combatant = SimpleNamespace(
        instance_id='goblin-1', name='Goblin Warrior', is_pc=False,
        size='small', visible_to_players=True, system='pf2e',
        current_hp=20, hp=20, conditions={}, image=None, portrait=None,
    )
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [combatant])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)

    first = scene_client.post(f'/api/scenes/{scene["id"]}/sync-encounter', json={})
    assert first.status_code == 200
    assert first.get_json()['added'] == 1
    assert first.get_json()['encounter_count'] == 1

    second = scene_client.post(f'/api/scenes/{scene["id"]}/sync-encounter', json={})
    assert second.status_code == 200
    assert second.get_json()['added'] == 0
    assert second.get_json()['linked'] == 0
    assert len(second.get_json()['scene']['tokens']) == 1
    assert second.get_json()['scene']['tokens'][0]['combatant_id'] == 'goblin-1'


def test_map_combat_action_uses_authoritative_combatant_helpers(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Combat Controls')
    token = scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    scenes.save_scene(CID, scene)
    combatant = SimpleNamespace(
        instance_id='ogre-1', name='Ogre', is_pc=False, system='pf2e',
        current_hp=40, hp=40, conditions={}, visible_to_players=True,
    )
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [combatant])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setattr(app, '_maybe_auto_remove_defeated', lambda *_args: None)

    calls = []
    def apply_hp(instance_id, amount, action, damage_type):
        calls.append((instance_id, amount, action, damage_type))
        old = combatant.current_hp
        combatant.current_hp -= amount
        return old
    monkeypatch.setattr(app, '_apply_hp_delta', apply_hp)

    response = scene_client.post(
        f'/api/scenes/{scene["id"]}/combatants/{token["combatant_id"]}',
        json={'action': 'damage', 'amount': 7, 'damage_type': 'fire'},
    )
    assert response.status_code == 200
    assert calls == [('ogre-1', 7, 'damage', 'fire')]
    assert response.get_json()['result']['net'] == 7

    monkeypatch.setattr(app, '_is_gm', lambda: False)
    forbidden = scene_client.post(
        f'/api/scenes/{scene["id"]}/combatants/{token["combatant_id"]}',
        json={'action': 'damage', 'amount': 1},
    )
    assert forbidden.status_code == 403


def test_scene_vector_elements_persist_and_filter_for_players(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Vision Tools')
    sid = scene['id']
    fog = scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'fog_ops',
        'operations': [{'mode': 'reveal', 'x': 120, 'y': 130, 'radius': 80}],
    })
    assert fog.status_code == 200
    door = scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_wall', 'kind': 'door', 'secret': True,
        'x1': 100, 'y1': 100, 'x2': 200, 'y2': 100,
    })
    assert door.status_code == 200
    light = scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_light', 'x': 150, 'y': 150, 'radius': 300,
        'visible_to_players': False,
    })
    assert light.status_code == 200
    template = scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_template', 'kind': 'burst', 'x1': 200, 'y1': 200,
        'radius': 140,
    })
    assert template.status_code == 200

    monkeypatch.setattr(app, '_is_gm', lambda: False)
    assert scene_client.get(f'/api/scenes/{sid}').status_code == 403
    player = app._scene_payload(scenes.load_scene(CID, sid), player=True)
    assert player['walls'][0]['kind'] == 'wall'
    assert player['walls'][0]['secret'] is False
    assert player['lights'] == []
    assert player['fog']['operations'][0]['mode'] == 'reveal'
    assert player['templates'][0]['kind'] == 'burst'


def test_masked_secret_door_is_indistinguishable_from_a_real_wall(scene_client, monkeypatch):
    # Masking a closed secret door to kind='wall' is not enough on its own:
    # add_wall writes 'secret': False on every genuine wall, so if the mask
    # POPS the key instead of setting it, the walls MISSING it in the player
    # payload are exactly the secret doors. Compare the key sets, not just kind.
    scene = scenes.create_scene(CID, 'Secret Doors')
    sid = scene['id']
    assert scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_wall', 'kind': 'wall',
        'x1': 0, 'y1': 0, 'x2': 100, 'y2': 0,
    }).status_code == 200
    assert scene_client.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_wall', 'kind': 'door', 'secret': True, 'open': False,
        'x1': 0, 'y1': 200, 'x2': 100, 'y2': 200,
    }).status_code == 200

    monkeypatch.setattr(app, '_is_gm', lambda: False)
    assert scene_client.get(f'/api/scenes/{sid}').status_code == 403
    walls = app._scene_payload(scenes.load_scene(CID, sid), player=True)['walls']
    assert len(walls) == 2
    real_wall, masked_door = walls
    assert masked_door['kind'] == 'wall'
    # Same keys, and the same value for the one that gave it away.
    assert set(real_wall) == set(masked_door)
    assert real_wall['secret'] == masked_door['secret'] is False
    assert real_wall['open'] == masked_door['open'] is False


def test_bulk_map_damage_uses_linked_targets_only(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Bulk Targets')
    scenes.add_token(scene, name='One', combatant_id='one')
    scenes.add_token(scene, name='Two', combatant_id='two')
    scenes.save_scene(CID, scene)
    one = SimpleNamespace(instance_id='one', name='One', is_pc=False, system='pf2e',
                          current_hp=20, hp=20, conditions={}, visible_to_players=True)
    two = SimpleNamespace(instance_id='two', name='Two', is_pc=False, system='pf2e',
                          current_hp=20, hp=20, conditions={}, visible_to_players=True)
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [one, two])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setattr(app, '_maybe_auto_remove_defeated', lambda *_args: None)

    def apply_hp(instance_id, amount, action, _damage_type):
        combatant = one if instance_id == 'one' else two
        old = combatant.current_hp
        combatant.current_hp -= amount
        return old
    monkeypatch.setattr(app, '_apply_hp_delta', apply_hp)
    response = scene_client.post(f'/api/scenes/{scene["id"]}/bulk-combat', json={
        'combatant_ids': ['one', 'two', 'not-linked'],
        'action': 'damage', 'amount': 4, 'damage_type': 'slashing',
    })
    assert response.status_code == 200
    assert [row['net'] for row in response.get_json()['results']] == [4, 4]


def test_gm_and_player_map_pages_render(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Rendered Scene')
    gm_page = scene_client.get(f'/map/{scene["id"]}')
    assert gm_page.status_code == 200
    assert b'id="map-canvas"' in gm_page.data
    # There is deliberately no player-facing map route: the map is GM-only
    # until the shared table screen exists. A 404 here is the feature.
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    assert scene_client.get('/player/map').status_code == 404


def test_legacy_map_home_does_not_redirect_to_gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(app, '_active_campaign_id', lambda: None)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    with app.app.test_client() as client:
        response = client.get('/map')
    assert response.status_code == 200
    assert b'Create or open a scene' in response.data


def test_player_scene_api_filters_hidden_token(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Player Filter')
    scenes.add_token(scene, name='Visible Hero')
    scenes.add_token(scene, name='Secret Enemy', visible_to_players=False)
    scenes.save_scene(CID, scene)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    assert scene_client.get(f'/api/scenes/{scene["id"]}').status_code == 403
    projected = app._scene_payload(scenes.load_scene(CID, scene['id']), player=True)
    assert [t['name'] for t in projected['tokens']] == ['Visible Hero']


def test_player_cannot_move_uncontrolled_token(scene_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Movement Guard')
    token = scenes.add_token(scene, name='Someone Else', controller_user_id='owner')
    scenes.save_scene(CID, scene)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app, '_scene_player_can_move', lambda _token, _scene: False)
    response = scene_client.patch(
        f'/api/scenes/{scene["id"]}/tokens/{token["id"]}', json={'x': 999, 'y': 999})
    assert response.status_code == 403
    stored = scenes.load_scene(CID, scene['id'])
    assert stored['tokens'][0]['x'] != 999


def test_campaign_scoped_sse_only_reaches_matching_subscribers(monkeypatch):
    q_a, q_b = queue.Queue(), queue.Queue()
    monkeypatch.setattr(app, '_sse_subscribers', [(q_a, True, CID), (q_b, True, 'b' * 32)])
    monkeypatch.setattr(app, '_sse_buffer', [])
    monkeypatch.setattr(app, '_sse_event_seq', 0)
    monkeypatch.setattr(app, '_sse_event_campaigns', {})
    app.sse_broadcast('scene_update', {'campaign_id': CID}, campaign_id=CID)
    assert 'scene_update' in q_a.get_nowait()
    assert q_b.empty()


def test_map_template_and_client_subscribe_to_scene_and_combat_updates():
    template = (app.Path(app.BASE_DIR) / 'templates' / 'map.html').read_text(encoding='utf-8')
    client = (app.Path(app.BASE_DIR) / 'static' / 'js' / 'map.js').read_text(encoding='utf-8')
    assert 'id="map-canvas"' in template
    assert "window.appSSE('scene_update'" in client
    assert "window.appSSE('scene_activated'" in client
    assert "window.appSSE('encounter_update'" in client
    assert "window.appSSE('pc_update'" in client
    assert 'id="map-calibrate-grid"' in template
    assert 'id="map-token-owner"' in template
    assert 'id="map-undo"' in template
    assert 'id="map-sync-encounter"' in template
    assert 'id="map-combat-actions"' in template
    assert 'id="map-follow-turn"' in template
    assert 'data-map-tool="measure"' in template
    assert 'data-map-tool="fog-reveal"' in template
    assert 'data-map-tool="wall"' in template
    assert 'data-map-tool="door"' in template
    assert 'data-map-tool="light"' in template
    assert "interaction.type === 'pan'" in client
    assert "focusActiveTurn" in client
    assert 'visibilityPolygon' in client
    assert 'templateContainsToken' in client


@pytest.fixture
def real_auth_player_client(scene_store, monkeypatch):
    """A player session that exercises the REAL _is_gm / _scene_member_allowed.

    Every other client fixture here monkeypatches _is_gm to always-True, which
    neuters @gm_required (it is just `if _is_gm()`) on every route it touches.
    Nothing that stubs the auth functions can prove a player is actually kept
    out. This one stubs only the mode switches: legacy mode with a GM_PASSWORD
    set, and a session holding a player_name but not gm_authenticated. That
    combination sends _is_gm() down its real False branch -- which matters,
    because with no GM_PASSWORD _is_gm() returns True for EVERYONE and a
    player-leak assertion would pass vacuously.
    """
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', 'not-the-player')
    monkeypatch.setattr(app, '_scene_character_records', lambda _cid: {})
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [])
    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['player_name'] = 'Kyle'
            sess.pop('gm_authenticated', None)
        yield client


def test_real_player_session_is_not_gm(real_auth_player_client):
    # Guards the fixture itself: if the session ever authenticates as GM, the
    # assertions in the tests below stop meaning anything -- a 200 anywhere here
    # means the fixture stopped producing a real player.
    #
    # The map is GM-only, so the whole /api/scenes prefix is closed to a player:
    # listing used to be allowed for any campaign member, which is precisely how
    # unreleased GM prep was readable. Reading and creating are both 403 now.
    assert real_auth_player_client.get('/api/scenes').status_code == 403
    assert real_auth_player_client.post(
        '/api/scenes', json={'name': 'Nope'}).status_code == 403


def test_real_player_session_receives_no_npc_hp(real_auth_player_client, monkeypatch):
    scene = scenes.create_scene(CID, 'Real Auth')
    scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    scenes.add_token(scene, name='Ambusher', combatant_id='hidden-1',
                     visible_to_players=False)
    scenes.save_scene(CID, scene)
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [
        SimpleNamespace(instance_id='ogre-1', name='Ogre', is_pc=False,
                        system='pf2e', current_hp=12, hp=40, conditions={},
                        visible_to_players=True),
        SimpleNamespace(instance_id='hidden-1', name='Ambusher', is_pc=False,
                        system='pf2e', current_hp=30, hp=30, conditions={},
                        visible_to_players=False),
    ])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)

    # A real player cannot read a scene at all any more -- the strongest form
    # of "no NPC hp leaks to players", since nothing is served.
    assert real_auth_player_client.get(
        f'/api/scenes/{scene["id"]}').status_code == 403

    # The projection still has to be correct, because the shared table screen
    # will serve exactly this payload. Asserted where it lives.
    body = json.dumps(app._scene_payload(scenes.load_scene(CID, scene['id']),
                                         player=True))
    assert 'Ogre' in body
    assert 'Ambusher' not in body      # hidden token dropped entirely
    assert '"current_hp"' not in body
    assert '"max_hp"' not in body
    live = json.loads(body)['tokens'][0]['live']
    assert live['hp_status'] == 'Wounded'
    assert set(live) & {'current_hp', 'max_hp'} == set()


def test_real_player_session_cannot_reach_gm_map_routes(real_auth_player_client):
    scene = scenes.create_scene(CID, 'Locked Down')
    sid = scene['id']
    token = scenes.add_token(scene, name='Ogre')
    scenes.save_scene(CID, scene)
    forbidden = [
        ('post', f'/api/scenes/{sid}/activate', {}),
        ('post', f'/api/scenes/{sid}/tokens', {'name': 'Mine'}),
        ('post', f'/api/scenes/{sid}/sync-encounter', {}),
        ('post', f'/api/scenes/{sid}/combatants/ogre-1', {'action': 'damage', 'amount': 1}),
        ('post', f'/api/scenes/{sid}/elements', {'action': 'fog_reset'}),
        ('post', f'/api/scenes/{sid}/bulk-combat',
         {'combatant_ids': ['ogre-1'], 'action': 'damage', 'amount': 1}),
        ('patch', f'/api/scenes/{sid}', {'name': 'Renamed'}),
        ('delete', f'/api/scenes/{sid}/tokens/{token["id"]}', None),
    ]
    for method, path, payload in forbidden:
        send = getattr(real_auth_player_client, method)
        response = send(path, json=payload) if payload is not None else send(path)
        assert response.status_code == 403, f'{method.upper()} {path} returned {response.status_code}'

    # The scene survived every one of them.
    stored = scenes.load_scene(CID, sid)
    assert stored['name'] == 'Locked Down'
    assert len(stored['tokens']) == 1


def test_pf2e_cone_templates_use_a_ninety_degree_spread():
    # A PF2e cone is a quarter circle, so the half-angle either side of the
    # aim vector is 45 degrees (PI/4). PI/6 would draw a 60-degree cone and,
    # worse, auto-target the wrong squares. Both the renderer and the hit
    # test have to agree -- there is no JS harness, so assert on the source.
    client = (app.Path(app.BASE_DIR) / 'static' / 'js' / 'map.js').read_text(encoding='utf-8')
    assert 'const spread = Math.PI / 4;' in client
    assert 'Math.abs(difference) <= Math.PI / 4' in client
    assert 'Math.PI / 6' not in client


def test_load_stage_requires_gm(monkeypatch):
    # /api/save_stage is prefix-gated but its GET counterpart was not, leaving
    # the GM's prepped monster lists readable by anyone who could guess a name.
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    with app.app.test_client() as client:
        response = client.get('/api/load_stage/Ambush')
    assert response.status_code == 403


def test_encounter_builder_has_safe_additive_map_handoff():
    template = (app.Path(app.BASE_DIR) / 'templates' / 'encounter_builder.html').read_text(encoding='utf-8')
    assert 'id="btn-load-map"' in template
    assert 'async function loadToMap()' in template
    assert 'clear_first: false' in template
    assert '/sync-encounter' in template

def test_the_whole_scene_prefix_is_gm_gated():
    """The boundary is the PREFIX, not the per-route checks.

    Every scene route also calls _scene_member_allowed(), and that is exactly
    what shipped the hole: it returns True for any campaign member, so GET
    /api/scenes/<id> served the player projection of ANY scene -- including
    prep the GM had never activated. Its own _is_gm() check guarded only PATCH.

    Asserting the prefix rather than enumerating today's routes is the point:
    a scene endpoint added later inherits the gate instead of quietly becoming
    player-readable.
    """
    assert '/api/scenes' in app.GM_API_PREFIXES
    scene_rules = [r.rule for r in app.app.url_map.iter_rules()
                   if r.rule.startswith('/api/scenes')]
    assert scene_rules, 'no scene routes registered at all'
    unguarded = [r for r in scene_rules
                 if not any(r.startswith(p) for p in app.GM_API_PREFIXES)]
    assert not unguarded, unguarded


def test_there_is_no_player_facing_map_route():
    """The map is GM-only until the shared table screen exists. Its absence is
    deliberate, so it is asserted rather than left to be noticed."""
    rules = {r.rule for r in app.app.url_map.iter_rules()}
    assert '/map' in rules and '/map/<scene_id>' in rules
    assert '/player/map' not in rules

def test_the_helper_is_equivalent_to_the_inline_copies_it_replaced():
    """_npc_hp_status replaced two hand-inlined copies of this policy -- one in
    the encounter SSE frame, one in /api/player_state. Consolidating a rule that
    decides what players learn about a monster is only safe if it is exactly
    equivalent, so the originals are replayed here rather than trusted.

    The subtle case is max_hp <= 0: the originals computed `pct = cur/mx if
    mx > 0 else 0`, so pct fell to 0 and a living creature read as Wounded.
    The helper reproduces that with an explicit branch instead of by accident.
    """
    def original_sse_frame(cur, mx):
        pct = cur / mx if mx > 0 else 0
        if cur == 0:
            return 'Dead'
        elif pct <= 0.5:
            return 'Wounded'
        return ''

    def original_player_state(cur, mx):
        pct = cur / mx if mx > 0 else 0
        if cur == 0:
            status = 'Dead'
        elif pct <= 0.5:
            status = 'Wounded'
        else:
            status = ''
        color = ('text-red-400' if cur == 0
                 else 'text-orange-400' if pct <= 0.5 else '')
        return status, color

    for max_hp in (0, 1, 7, 40, 50, 999):
        for current_hp in range(0, max_hp + 3):
            status, color = app._npc_hp_status(current_hp, max_hp)
            assert status == original_sse_frame(current_hp, max_hp), (current_hp, max_hp)
            assert (status, color) == original_player_state(current_hp, max_hp), (current_hp, max_hp)
