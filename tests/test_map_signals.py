"""Stage 7c: the small findings, which were mostly things that only looked fine.

Four of the five here are cases where the UI was actively reassuring rather than
merely thin: a connection indicator that could not report a disconnection, two
CSS rules that had never matched anything, an error that looked like a success,
and a table screen whose comment claimed it scaled things it did not.

The fifth is faction: PC versus NPC was carried by green against red, which is
the one axis a red-green colourblind viewer cannot use at all.
"""
from __future__ import annotations

import os


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()
_HUB = open(os.path.join(_ROOT, 'templates', '_sse_hub.html'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- the live indicator can now report bad news -----------------------------

def test_the_live_dot_is_driven_by_the_socket():
    """It was a green dot with no JS behind it at all -- permanently
    reassuring, including while the stream was dead, which is worse than having
    no indicator."""
    assert 'watchLiveSync' in _JS
    body = _JS[_JS.index('function watchLiveSync()'):]
    body = body[:body.index('})();')]
    assert '__appSSE.isConnected()' in body
    assert "'Reconnecting...'" in body


def test_the_indicator_is_polled_because_there_is_no_disconnect_event():
    """The hub nulls its socket and retries on a backoff; nothing announces the
    drop, so the only way to notice it is to ask."""
    body = _JS[_JS.index('function watchLiveSync()'):]
    body = body[:body.index('})();')]
    assert 'setInterval(paint, 2000)' in body


def test_the_hub_reports_open_rather_than_merely_constructed():
    """Between `new EventSource` and onopen the socket exists but carries
    nothing, and the map reads this to decide whether to claim it is in sync."""
    assert 'return !!es && es.readyState === 1;' in _HUB


def test_the_dropped_state_has_its_own_styling():
    assert '.map-live.is-dropped .map-live-dot' in _CSS


# --- two rules that had never matched anything ------------------------------

def test_the_canvas_rules_target_the_id_it_actually_has():
    """The element is <canvas id="map-canvas"> with no class, so
    `.map-canvas.is-drop-target` and `.map-page.is-previewing .map-canvas`
    matched nothing. The preview frame is the only cue that the GM's canvas is
    deliberately showing an incomplete view, and it had never once rendered."""
    assert '.map-canvas.is-drop-target' not in _CSS
    assert '.map-page.is-previewing .map-canvas ' not in _CSS
    assert '#map-canvas.is-drop-target' in _CSS
    assert '.map-page.is-previewing #map-canvas' in _CSS
    assert 'class="map-canvas"' not in _HTML, (
        'if the canvas ever gains the class, revisit -- do not let both exist')


# --- an error stops looking like a success ----------------------------------

def test_a_failure_stays_on_screen_longer_than_a_success():
    """Both used to vanish on the same 2.6 seconds, so an error that landed
    while the GM was looking at the map was simply gone."""
    body = _fn('toast')
    assert 'error ? 7000 : 2600' in body


def test_an_error_toast_is_more_than_a_border_colour():
    rule = _CSS[_CSS.index('.map-toast.is-error'):]
    rule = rule[:rule.index('}')]
    assert 'background' in rule and 'border-left' in rule


# --- the table screen scales more than the nameplate ------------------------

def test_there_is_one_place_that_scales_canvas_type_for_the_room():
    assert 'function tableType(px) { return isTableView() ? Math.round(px * 1.7) : px; }' in _JS


def test_the_health_bar_and_conditions_scale_with_the_name():
    """The nameplate was the only isTableView()-aware font in the file, despite
    its comment claiming the health bar came with it. So the room could read WHO
    a token was but not that it was Frightened or Prone -- which is the entire
    reason conditions are painted under the token rather than left in the
    sidebar."""
    body = _JS[_JS.index('const barWidth = radius * 1.7;'):]
    body = body[:body.index('if (!isTableView() && token.visible_to_players === false)')]
    assert 'tableType(5)' in body, 'the HP bar'
    assert 'tableType(10)' in body, 'the condition text'


def test_conditions_stack_on_the_table_rather_than_running_wide():
    """Three condition names joined by ' / ' at 17px runs wider than the token
    and collides with whatever is beside it."""
    body = _JS[_JS.index('const conditions = Object.keys(live.conditions || {});'):]
    body = body[:body.index('if (!isTableView() && token.visible_to_players === false)')]
    assert "const lines = isTableView() ? names : [names.join(' / ')];" in body


# --- faction is no longer a single hue --------------------------------------

def test_party_tokens_carry_a_shape_cue_as_well_as_a_colour():
    """#4f8a62 against #a84b45 is green against red -- the one axis a red-green
    colourblind viewer cannot use."""
    body = _JS[_JS.index('if (token.is_pc) {'):]
    body = body[:body.index('ctx.textAlign')]
    assert 'ctx.arc(x, y, Math.max(2, radius - 6)' in body
    assert 'ctx.stroke();' in body


def test_the_cue_marks_the_party_not_the_monsters():
    """There are four PCs and a screen full of monsters; marking the smaller set
    keeps the map quiet."""
    assert 'if (token.is_pc) {' in _JS
    assert 'if (!token.is_pc) {' not in _JS


# --- the map stops inventing its own gold and its own radius scale ----------

def test_no_raw_gilt_triplet_survives_in_map_css():
    """`rgba(224,182,90,a)` was the BASE value of --gilt-200, but the live token
    is #e8b66a under system-pf2e and #9fd2f2 under system-cosmere. So a primary
    button stayed gold on a Cosmere map while the active tool beside it turned
    blue -- in the same toolstrip."""
    assert '224,182,90' not in _CSS.replace(' ', '')
    assert 'color-mix(in srgb, var(--gilt-200)' in _CSS


def test_radii_come_from_the_shared_scale():
    """Six radii in one file, none of them the two the rest of the app uses."""
    import re
    raw = re.findall(r'border-radius:\s*(\d+)px', _CSS)
    assert not raw, 'pixel radii left in map.css: %s' % raw
    assert 'var(--r-sm)' in _CSS and 'var(--r-md)' in _CSS


def test_the_circle_and_the_squared_table_screen_are_left_alone():
    """--r-sm/--r-md are a rounding scale; a dot is a circle and the table
    screen is deliberately squared off to the edges of the TV."""
    assert 'border-radius:50%' in _CSS.replace(' ', '')
    assert 'border-radius:0' in _CSS.replace(' ', '')


def test_the_reds_and_green_come_from_the_brand_ramp():
    """Snapped on request. Unlike the gold, this buys no multi-system fix --
    --ruby-* and --success are identical under system-pf2e and system-cosmere --
    so it is consistency, not a bug fix. The reds all gained contrast doing it
    (#f2aaa3 9.29 -> --ruby-100 10.37, #d35d53 4.59 -> --ruby-200 5.23)."""
    for gone in ('#f2aaa3', '#ffc9c2', '#d35d53', '#c2564c', '#d97b71', '#58b37a'):
        assert gone not in _CSS, gone
    for family in ('--ruby-50', '--ruby-100', '--ruby-200', '--ruby-300', '--success'):
        assert 'var(%s)' % family in _CSS, family


def test_the_hp_bar_uses_the_states_the_tokens_document_themselves_as():
    """system.css says --success is "used SPARINGLY -- only HP-full state" and
    --warn "only HP-low state". The map's HP bar is literally that."""
    assert "brand('--success'" in _JS and "brand('--warn'" in _JS and "brand('--danger'" in _JS


def test_the_gm_only_overlays_keep_their_working_colours():
    """Door markers and draft strokes are GM-authoring affordances with no token
    counterpart, and painting a door with --success would break that token's
    own stated scope. Left literal, deliberately."""
    doors = _JS[_JS.index("wall.open ? '#58b37a'"):][:200]
    assert "'#58b37a'" in doors and "'#d6a24d'" in doors


# --- the toolstrip stops inverting interaction frequency ---------------------

def test_the_tools_match_the_buttons_they_sit_beside():
    """Tool switching is the map's highest-frequency action in a fight, and it
    was the SMALLEST text and SHORTEST hit target on the page -- while .map-btn,
    pressed far less often, was 11px and 34px."""
    import re
    tool = _CSS[_CSS.index('.map-tool {'):]
    tool = tool[:tool.index('}')]
    btn = _CSS[_CSS.index('.map-btn {'):]
    btn = btn[:btn.index('}')]
    assert 'min-height:34px' in tool and 'min-height:34px' in btn
    assert '700 11px/1 var(--font-ui)' in tool and '700 11px/1 var(--font-ui)' in btn


def test_the_labels_are_not_shouted():
    """All-caps defeats word-shape recognition, which is exactly what scanning a
    strip of eighteen labels depends on."""
    tool = _CSS[_CSS.index('.map-tool {'):]
    tool = tool[:tool.index('}')]
    assert 'text-transform' not in tool
    assert 'letter-spacing' not in tool


def test_the_group_dividers_are_visible_enough_to_group():
    """--border-card is a 14%-opacity hairline; three groups separated by it
    read as one undifferentiated stripe."""
    divider = _CSS[_CSS.index('.map-tool-divider {'):]
    divider = divider[:divider.index('}')]
    assert 'var(--border-rule)' in divider
    assert 'margin:0 6px' in divider


def test_the_readout_yields_before_the_tools_do():
    """It is help text. As flex:0 0 auto it pushed the strip into horizontal
    overflow and got clipped mid-phrase at 1024 while every tool still fitted --
    so the one thing that could safely shrink was the one thing that would not."""
    readout = _CSS[_CSS.index('.map-tool-readout {'):]
    readout = readout[:readout.index('}')]
    assert 'flex:0 1 auto' in readout
    assert 'min-width:0' in readout
    assert 'text-overflow:ellipsis' in readout


# --- the fog mask cache has to survive a token move --------------------------

def test_the_fog_cache_is_not_invalidated_by_every_token_move():
    """scene.revision bumps on ANY save, a token move included, so keying on it
    alone re-blurred the whole canvas every time a creature took a step.
    Measured at 2560x1440 with 540 revealed cells: 2.7ms on the first frame
    after every move, down to 0.2ms with the two-level key -- paid on the most
    frequent action in a fight, and avoidable.

    terrainEntry already documents this exact trap; this is the same fix."""
    body = _fn('drawFogOverlay')
    assert 'fogMaskFast === fast' in body, 'the cheap revision check comes first'
    assert "(fogState.revealed_cells || []).join(',')" in body, (
        'and a content signature decides whether the blur is actually redone')
    assert body.index('fogMaskFast === fast') < body.index('fogMaskKey === key'), (
        'the cheap key must be checked before the expensive one is built')


# --- the one syscall gevent cannot yield around -----------------------------

def test_the_shared_atomic_writer_can_skip_fsync():
    """app._atomic_write_json has had this flag and the reasoning in its
    docstring for a long time; core/storage's copy did not, so every caller
    under core/ fsynced unconditionally."""
    src = open(os.path.join(_ROOT, 'core', 'storage.py'), encoding='utf-8').read()
    assert 'def atomic_write_json(path, obj, indent=2, fsync=True):' in src, (
        'default True so every existing caller keeps the durability it had')
    body = src[src.index('def atomic_write_json'):]
    body = body[:body.index('\ndef ')]
    assert 'if fsync:' in body and 'os.fsync' in body


def test_the_hot_scene_write_skips_it_and_scene_creation_does_not():
    """save_scene is the map's hottest path -- every token move, wall run, fog
    reveal, terrain paint, light and undo step. Measured at 3.7 ms of blocking
    fsync on a 16.5 KB scene, which freezes every player's SSE. Creating a scene
    happens once, so it stays durable."""
    src = open(os.path.join(_ROOT, 'core', 'scenes.py'), encoding='utf-8').read()
    save = src[src.index('def save_scene('):src.index('def create_scene(')]
    create = src[src.index('def create_scene('):]
    create = create[:create.index('\ndef ')]
    assert 'fsync=False' in save
    assert 'fsync=False' not in create


def test_accounts_and_campaigns_keep_their_fsync():
    """Presentation state is re-derivable; a user record is not."""
    for module in ('auth.py', 'campaigns.py'):
        src = open(os.path.join(_ROOT, 'core', module), encoding='utf-8').read()
        for line in src.splitlines():
            if 'atomic_write_json(' in line:
                assert 'fsync=False' not in line, '%s: %s' % (module, line.strip())


# --- live-state frames stop refetching a scene nobody edited ----------------

def test_live_state_frames_are_coalesced_into_one_refetch():
    """pc_update, encounter_update and connected all mean "live state moved",
    not "the scene changed" -- the scene arrives on its own scene_update. They
    refetch because the map paints HP and conditions from the live projection.

    One area effect on four targets emits four pc_update frames plus an
    encounter_update. Uncoalesced that was six full scene fetches, on BOTH the
    GM page and the TV, for a scene nobody edited -- on the single worker that
    is also serving those same SSE streams."""
    body = _JS[_JS.index('function refetchLiveState()'):]
    body = body[:body.index('\n        }')]
    assert 'if (liveRefetch) return;' in body, 'a burst must collapse to one fetch'
    assert 'fetchScene();' in body

    for event in ('pc_update', 'connected'):
        handler = _JS[_JS.index("appSSE('%s'" % event):][:80]
        assert 'refetchLiveState' in handler, event


def test_the_picker_rebuild_is_not_on_the_turn_advance_path():
    """refreshPickers parses every scene file on disk. It exists to notice a
    combatant being added or renamed, which is rare and never urgent."""
    body = _JS[_JS.index('function refreshPickersSoon()'):]
    body = body[:body.index('\n        }')]
    assert 'setTimeout(refreshPickers, 2000)' in body


def test_the_campaign_memo_never_keys_on_a_malformed_id():
    """A crafted request can send a list or dict as a campaign id. Those used to
    fall through and be rejected cleanly; using one as a dict key raises
    TypeError and turns that rejection into a 500.

    Caught by test_malformed_campaign_id_types_are_rejected, which a narrower
    test filter had not been running."""
    src = open(os.path.join(_ROOT, 'core', 'campaigns.py'), encoding='utf-8').read()
    body = src[src.index('def get_campaign(cid):'):]
    body = body[:body.index('\ndef ')]
    assert 'isinstance(cid, str)' in body, 'only a real string id may be memoized'
    assert body.index('isinstance(cid, str)') < body.index('cid in memo'), (
        'the type check has to happen before the dict lookup')
