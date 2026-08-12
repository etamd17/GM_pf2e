"""Stage 6e: environmental terrain -- lava, water, poison, blood.

The scope question was asked and answered before any of this was written:

    "it is more environmental than anything just to increase immersion of the map"

So this is COSMETIC, deliberately and entirely. No damage on entry, no difficult
terrain, no conditions, no turn-boundary evaluation. The tests below guard that
boundary as hard as they guard the rendering, because "lava that does nothing" is
the kind of thing a future reader mistakes for an unfinished feature and helpfully
finishes.

The visual design rests on one observation: hue is the first thing a television
across a room destroys. At low alpha over an arbitrary battlemap, lava and blood
are both a red patch. So each substance is separated on LUMINANCE DIRECTION and on
MOTION TYPE as well as colour -- lava is the only thing on the map that brightens
it, poison is the only one made of discrete countable objects, blood is the only
one that does not move at all.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'e6' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


@pytest.fixture
def gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    return app.app.test_client()


def _paint(client, scene_id, **payload):
    body = {'action': 'paint_terrain'}
    body.update(payload)
    return client.post('/api/scenes/%s/elements' % scene_id, json=body)


def _layers(cid, scene_id):
    return {layer['kind']: layer['cells']
            for layer in scenes.load_scene(cid, scene_id).get('terrain', [])}


# --- it is cosmetic, and that is load-bearing ------------------------------

def test_terrain_carries_no_mechanical_fields():
    """The whole scope decision, pinned. A kind is a rendering recipe and
    nothing else -- the moment one carries a damage value or a movement cost it
    needs server-side rules, turn-boundary evaluation and player feedback, which
    is a different feature with a different risk profile."""
    source = open(os.path.join(_ROOT, 'core', 'scenes.py'), encoding='utf-8').read()
    block = source[source.index('TERRAIN_KINDS'):source.index('def fit_scene_dimensions')]
    for banned in ('damage', 'difficult', 'condition', 'save_dc', 'movement'):
        assert banned not in block.lower()


def test_the_kinds_are_an_allowlist():
    """Free text would let a typo paint nothing at all, which reads as data
    loss rather than as a rejected value."""
    assert scenes.TERRAIN_KINDS == ('lava', 'water', 'poison', 'blood')


# --- painting ---------------------------------------------------------------

def test_flooding_a_room_stores_cells(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    response = _paint(gm, scene['id'], kind='lava', cells=['1,1', '1,2', '2,1'])
    assert response.status_code == 200
    assert _layers(CID, scene['id']) == {'lava': ['1,1', '1,2', '2,1']}


def test_painting_the_same_room_twice_is_idempotent(gm):
    """A cell set, not an append-only log -- so re-flooding a room the GM has
    already done costs nothing instead of another 30 entries."""
    scene = scenes.create_scene(CID, 'Cavern')
    _paint(gm, scene['id'], kind='water', cells=['3,3', '3,4'])
    _paint(gm, scene['id'], kind='water', cells=['3,3', '3,4'])
    assert _layers(CID, scene['id']) == {'water': ['3,3', '3,4']}


def test_a_square_holds_one_substance(gm):
    """Two layers stacked on the same pixels turn to sludge, and multiply-blood
    under screen-lava is the worst pairing available. Exclusivity is enforced
    where the data lives rather than hoped for in the renderer."""
    scene = scenes.create_scene(CID, 'Cavern')
    _paint(gm, scene['id'], kind='water', cells=['5,5', '5,6'])
    _paint(gm, scene['id'], kind='lava', cells=['5,5'])
    assert _layers(CID, scene['id']) == {'water': ['5,6'], 'lava': ['5,5']}


def test_draining_removes_whatever_was_there(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    _paint(gm, scene['id'], kind='poison', cells=['7,7', '7,8'])
    _paint(gm, scene['id'], mode='clear', cells=['7,7'])
    assert _layers(CID, scene['id']) == {'poison': ['7,8']}


def test_an_emptied_kind_is_dropped_entirely(gm):
    """So the client can treat presence as "this scene has lava" rather than
    having to check for an empty cell list everywhere."""
    scene = scenes.create_scene(CID, 'Cavern')
    _paint(gm, scene['id'], kind='blood', cells=['9,9'])
    _paint(gm, scene['id'], mode='clear', cells=['9,9'])
    assert scenes.load_scene(CID, scene['id'])['terrain'] == []


def test_clear_terrain_wipes_the_scene(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    _paint(gm, scene['id'], kind='lava', cells=['1,1'])
    _paint(gm, scene['id'], kind='water', cells=['2,2'])
    gm.post('/api/scenes/%s/elements' % scene['id'], json={'action': 'clear_terrain'})
    assert scenes.load_scene(CID, scene['id'])['terrain'] == []


# --- validation -------------------------------------------------------------

def test_an_unknown_kind_is_refused(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    assert _paint(gm, scene['id'], kind='acid', cells=['1,1']).status_code == 400


def test_malformed_cells_are_refused(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    assert _paint(gm, scene['id'], kind='lava', cells=['nope']).status_code == 400
    assert _paint(gm, scene['id'], kind='lava', cells=[{'col': 1}]).status_code == 400


def test_an_empty_paint_is_refused(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    assert _paint(gm, scene['id'], kind='lava', cells=[]).status_code == 400


def test_a_pathological_region_is_refused(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    huge = ['%d,%d' % (i, i) for i in range(scenes.MAX_TERRAIN_CELLS_PER_REQUEST + 1)]
    assert _paint(gm, scene['id'], kind='lava', cells=huge).status_code == 400


def test_terrain_is_gm_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    with app.app.test_client() as client:
        response = client.post('/api/scenes/whatever/elements',
                               json={'action': 'paint_terrain', 'kind': 'lava', 'cells': ['1,1']})
        assert response.status_code in (302, 403)


# --- schema -----------------------------------------------------------------

def test_an_old_scene_backfills_terrain():
    """Scenes written before this stage must not KeyError in the renderer."""
    assert scenes.normalize_scene({'id': 'x'})['terrain'] == []


def test_terrain_reaches_the_table_view():
    """It is cosmetic, so it is deliberately NOT filtered out of the player
    projection -- the shared screen is the only place it matters."""
    from services import scene_sync
    scene = scenes.normalize_scene({'id': 'x', 'tokens': []})
    scene['terrain'] = [{'kind': 'lava', 'cells': ['1,1']}]
    assert scene_sync.player_payload(scene, {}, {})['terrain'] == scene['terrain']


# --- rendering: identity survives the room ----------------------------------

def test_each_substance_is_separated_by_luminance_not_only_hue():
    """The core visual decision. Lava is the only thing on the map that makes
    it brighter; water and blood darken it; poison lays a haze over it. Two
    substances therefore differ by more than colour on a bad screen."""
    block = _JS[_JS.index('const TERRAIN_PASSES = {'):]
    block = block[:block.index('\n    };')]
    assert "lava: [{op: 'multiply'" in block and "{op: 'screen'" in block
    assert "water: [{op: 'multiply'" in block
    assert "blood: [{op: 'multiply'" in block
    assert "poison: [{op: 'source-over'" in block


def test_lava_has_a_dark_crust_under_the_heat():
    """Without it an additive orange layer is just an amber filter over the
    floor rather than incandescent rock."""
    assert 'function paintLavaCrust(' in _JS
    assert 'function paintLavaHeat(' in _JS


def test_water_moves_by_interference_not_by_wobble():
    """Two band sets at different angles and speeds, so the bright knots where
    they cross TRAVEL. A standing oscillation reads as heat haze."""
    body = _fn('paintWaterSurface')
    assert 'for (let pass = 0; pass < 2; pass++)' in body
    assert 'c.rotate(pass ? -0.58 : 0.4);' in body


def test_water_highlights_are_broken_along_the_band():
    """Found by looking at it rather than by measuring it. Full-width stripes
    measured as motion perfectly well and rendered as hard parallel diagonals --
    hatching, not water. Modulating ALONG the band as well as across it leaves
    short bright knots that drift."""
    body = _fn('paintWaterSurface')
    assert 'for (let along' in body
    assert 'const knot =' in body


def test_the_feather_cannot_swallow_the_gaps_between_pools():
    """Also found by looking. At 0.42 the blur spread about 120px, so two pools
    with a full empty row between them merged into one puddle and the gap the
    GM deliberately left simply vanished."""
    body = _fn('terrainEntry')
    assert 'g.size * 0.26' in body


def test_water_is_kept_clear_of_the_template_palette():
    """drawTemplate strokes #75c5eb; a burst dropped on a lake has to stay
    readable against it."""
    assert "c.fillStyle = '#2f6270';" in _fn('paintWaterBody')


def test_poison_bubbles_are_rings_and_do_not_travel():
    """A disc is a dot; a ring is a bubble. And seen from above a bubble swells
    and bursts in place -- making it rise up the screen would read as a side-on
    view fighting the map."""
    body = _fn('paintPoison')
    assert 'c.stroke();' in body
    assert 'c.fill();' not in body


def test_blood_does_not_move_at_all():
    """Stillness IS the cue: a blood pool that bubbles reads as poison and one
    that shimmers reads as wine."""
    body = _fn('paintBlood')
    assert 't' not in [p.strip() for p in body[body.index('('):body.index(')')].split(',')], (
        'paintBlood must not take the animation clock')
    for moving in ('Math.sin', 'animationClock'):
        assert moving not in body


def test_a_blood_only_scene_never_starts_the_animation_loop():
    """The direct payoff for choosing stillness: the table screen stays at
    stage 6a's event-driven idle."""
    body = _fn('terrainAnimates')
    assert "layer.kind !== 'blood'" in body
    assert 'terrainAnimates()' in _fn('animationsWanted')


# --- rendering: cost --------------------------------------------------------

def test_the_outline_is_built_once_per_change_not_per_frame():
    """A blur() over the painted area every frame would spend most of what
    stage 6a bought."""
    body = _fn('terrainEntry')
    assert 'terrainCache.get(layer.kind)' in body
    assert 'if (cached && cached.fast === fast) return cached;' in body


def test_the_cache_key_holds_the_grid_as_well_as_the_revision():
    """The calibration drag moves grid.offset_* locally with no save and so no
    revision bump -- keying on the revision alone would leave the pool sitting
    where the squares used to be while the GM drags."""
    body = _fn('terrainEntry')
    assert 'const fast = [scene.revision, g.size, g.ox, g.oy].join' in body


def test_nothing_time_varying_enters_the_cache_key():
    """Stage 6c's rule, applied here."""
    body = _fn('terrainEntry')
    assert 'animationClock' not in body


def test_drawing_is_bounded_to_the_painted_area():
    """A pool is normally one room; compositing four full-canvas layers per
    frame to paint four rooms is most of a frame for nothing visible."""
    body = _fn('terrainEntry')
    assert 'minC' in body and 'maxR' in body, 'the mask is sized to the cell bounding box'
    assert 'entry.x, entry.y, entry.w, entry.h' in _fn('drawTerrain')


def test_buffers_are_half_resolution():
    """Everything here is a blurred blob or a soft gradient, so there is no
    detail at full res to lose -- and it quarters the backing store, which is
    what keeps a flooded 60x40 map from allocating tens of megabytes a kind."""
    assert 'const TERRAIN_SCALE = 0.5;' in _JS


def test_no_canvas_is_allocated_per_frame():
    """The bug the file already carries a comment about."""
    assert 'createElement' not in _fn('drawTerrain')
    assert 'createElement' not in _fn('drawTerrainGlow')


# --- rendering: where it sits ------------------------------------------------

def test_terrain_is_under_the_grid_but_its_glow_is_over_it():
    """Terrain is on the floor, so the GM can still count squares across a lake
    and the grid does not come out tinted. The glow is LIGHT, so it belongs
    with the lights."""
    render = _JS[_JS.index('function renderScene()'):]
    render = render[:render.index('\n    function drawGrid')]
    assert render.index('drawTerrain();') < render.index('drawGrid();')
    assert render.index('drawAmbientLights();') < render.index('drawTerrainGlow();')


def test_terrain_is_hidden_by_fog_like_everything_else():
    """An unexplored lava pool must not glow through the dark."""
    render = _JS[_JS.index('function renderScene()'):]
    render = render[:render.index('\n    function drawGrid')]
    assert render.index('drawTerrainGlow();') < render.index('drawFogOverlay();')


def test_lava_glows_but_never_carves_vision():
    """Stage 6c's load-bearing rule restated. The moment a terrain-derived
    radius enters visionSignature() the cached mask either goes stale or misses
    every frame, dragging the raycast back into the frame budget."""
    body = _fn('drawTerrainGlow')
    assert 'visionSignature' not in body
    assert 'lightRadii' not in body
    signature = _fn('visionSignature')
    assert 'terrain' not in signature


def test_the_glow_is_baked_rather_than_blurred_per_frame():
    body = _fn('drawTerrainGlow')
    assert 'ctx.filter' not in body
    assert 'entry.glow' in body


def test_terrain_is_visible_but_frozen_on_the_gm_view():
    """The GM needs to see WHERE it is while prepping, not watch it move. One
    code path with a frozen clock, the same shape as flickerFactor's early
    return, so the two screens cannot drift apart on how terrain looks."""
    body = _fn('drawTerrain')
    assert 'const t = isTableView() ? animationClock : 0;' in body
    assert 'isTableView()' not in body[:body.index('const t =')], (
        'terrain must not be skipped outright on the GM view'
    )


# --- the fog mask cache this stage forced -----------------------------------

def test_the_fog_mask_is_cached():
    """Not gold-plating: terrain keeps the table screen's animation loop
    running on scenes that never animated before, so the full-canvas blur
    drawFogOverlay used to do once per change would now run at 60fps."""
    body = _fn('drawFogOverlay')
    assert 'fogMaskKey === key' in body
    assert 'return;' in body[:body.index('const fogScratch')]


def test_a_frame_can_be_rendered_at_a_chosen_moment():
    """The animation clock is only ever advanced by the rAF loop, so without
    this every forced frame in a headless pane is frame zero -- and nothing
    animated can be verified as MOVING rather than merely as present. This is
    what let stage 6e prove blood is still and lava is not."""
    seam = _JS[_JS.index('window.__mapRenderNow = function'):]
    seam = seam[:seam.index('\n    };')]
    assert "if (typeof clock === 'number') animationClock = clock;" in seam


def test_the_fog_cache_key_holds_the_view_and_the_grid():
    """Darkness differs between the GM view and the table, and grid calibration
    moves the punched squares without a save."""
    body = _fn('drawFogOverlay')
    key = body[body.index('const key = ['):body.index(".join('|');")]
    for part in ("isTableView() ? 't' : 'g'", 'scene.revision', 'geometry.size', 'geometry.ox'):
        assert part in key


# --- the GM's side of it -----------------------------------------------------

def test_every_kind_has_a_tool():
    for kind in scenes.TERRAIN_KINDS:
        assert 'data-map-tool="terrain-%s"' % kind in _HTML
    assert 'data-map-tool="terrain-clear"' in _HTML


def test_the_terrain_tools_are_gm_only():
    tools = _HTML[_HTML.index('data-map-tool="terrain-lava"'):]
    assert '{% endif %}' in tools[:tools.index('map-tool-readout')]


def test_one_click_floods_a_whole_room():
    """Painting a lake square by square is tedious enough that the GM would
    simply not do it, and an unused feature adds no atmosphere at all."""
    assert 'floodRegion(point)' in _JS[_JS.index("activeTool.startsWith('terrain-')"):][:900]


def test_alt_click_paints_a_single_square():
    """Flooding a room is right for water, poison and lava and wrong for blood,
    which is a spill."""
    block = _JS[_JS.index("activeTool.startsWith('terrain-')"):][:1400]
    assert 'event.altKey' in block
