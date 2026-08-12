"""Stage 7b: undo that covers what it looks like it covers.

The UI audit found Ctrl+Z bound to a movement-only undo that fired after ANY
action -- so painting lava over the wrong room and pressing it silently moved a
token back instead, then reported "Movement undone." The cross-cutting note in
docs/map/AUDIT.md had already called for an inverse per action as each one
landed; stages 4a, 4c, 4d, 5a and 6e all shipped without one.

The design follows the rule this project settled for the Obsidian pane's undo:
an INVERSE, not a restore. Every entry is a list of ordinary map actions sent
back through the same endpoints, so undo can never write a shape the normal path
would have rejected -- a second write path is a second set of bugs.

Two things are load-bearing and easy to lose:

  * the inverse is computed by DIFFING the scene before and after, not from the
    request. Painting lava over water changes cells the request never named, and
    only the diff knows what was underneath.
  * restoring an erased element reuses its ID. Without that it comes back as a
    new object and every earlier entry still referencing the old id fails, so
    undoing one erase silently ended the whole undo history.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'b7' * 16
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


def _act(client, scene_id, **body):
    return client.post('/api/scenes/%s/elements' % scene_id, json=body)


# --- restoring an element keeps its identity --------------------------------

def test_a_restored_light_keeps_its_id(gm):
    """Without this, undoing an erase ends the undo history: the light comes
    back as a different object and every earlier entry that named the old id
    fails."""
    scene = scenes.create_scene(CID, 'Cavern')
    _act(gm, scene['id'], action='add_light', x=100, y=100, radius=350)
    light = scenes.load_scene(CID, scene['id'])['lights'][0]
    _act(gm, scene['id'], action='delete_light', id=light['id'])
    assert scenes.load_scene(CID, scene['id'])['lights'] == []

    _act(gm, scene['id'], action='add_light', restore_id=light['id'],
         x=light['x'], y=light['y'], radius=light['radius'],
         color=light['color'], intensity=light['intensity'])
    restored = scenes.load_scene(CID, scene['id'])['lights']
    assert len(restored) == 1
    assert restored[0]['id'] == light['id']


def test_a_restored_wall_keeps_its_id(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    _act(gm, scene['id'], action='add_wall', x1=0, y1=0, x2=100, y2=0)
    wall = scenes.load_scene(CID, scene['id'])['walls'][0]
    _act(gm, scene['id'], action='delete_wall', id=wall['id'])
    _act(gm, scene['id'], action='add_wall', restore_id=wall['id'],
         x1=wall['x1'], y1=wall['y1'], x2=wall['x2'], y2=wall['y2'], kind=wall['kind'])
    assert scenes.load_scene(CID, scene['id'])['walls'][0]['id'] == wall['id']


def test_a_restored_template_keeps_its_id(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    _act(gm, scene['id'], action='add_template', kind='burst', x1=50, y1=50, radius=100)
    item = scenes.load_scene(CID, scene['id'])['templates'][0]
    _act(gm, scene['id'], action='delete_template', id=item['id'])
    _act(gm, scene['id'], action='add_template', restore_id=item['id'], kind='burst',
         x1=item['x1'], y1=item['y1'], radius=item['radius'])
    assert scenes.load_scene(CID, scene['id'])['templates'][0]['id'] == item['id']


# --- and the id is guarded, not trusted -------------------------------------

def test_a_restore_id_that_is_not_an_id_is_ignored(gm):
    """It has to look like an id this code mints. A rejected value falls back to
    a fresh one rather than failing the action -- refusing the restore outright
    would lose the element the GM is trying to get back."""
    scene = scenes.create_scene(CID, 'Cavern')
    for bogus in ('../../etc/passwd', 'x' * 32, 'short', 12345, None):
        response = _act(gm, scene['id'], action='add_light', restore_id=bogus,
                        x=10, y=10, radius=100)
        assert response.status_code == 200
    for light in scenes.load_scene(CID, scene['id'])['lights']:
        assert len(light['id']) == 32
        int(light['id'], 16)


def test_a_colliding_restore_id_is_ignored(gm):
    """Two elements sharing an id would make every later delete ambiguous."""
    scene = scenes.create_scene(CID, 'Cavern')
    _act(gm, scene['id'], action='add_light', x=10, y=10, radius=100)
    existing = scenes.load_scene(CID, scene['id'])['lights'][0]['id']
    _act(gm, scene['id'], action='add_light', restore_id=existing, x=20, y=20, radius=100)
    ids = [light['id'] for light in scenes.load_scene(CID, scene['id'])['lights']]
    assert len(ids) == 2 and len(set(ids)) == 2


# --- the inverse is a diff, not a guess -------------------------------------

def test_the_inverse_is_computed_from_before_and_after():
    """Painting lava over water changes cells the request never named."""
    assert 'function inverseOps(before, after)' in _JS
    body = _fn('mapElementAction')
    assert 'undoSnapshot(scene)' in body
    assert 'inverseOps(before, undoSnapshot(data.scene))' in body


def test_terrain_is_inverted_per_cell_with_its_previous_owner():
    body = _fn('inverseOps')
    assert 'ownersBefore' in body and 'ownersAfter' in body
    assert 'restoreByKind' in body


def test_cells_that_were_empty_are_drained_first():
    """Repainting a cell claims it away from other kinds, but a cell that should
    end up EMPTY has to be cleared explicitly or it keeps what the forward
    action gave it."""
    body = _fn('inverseOps')
    assert body.index("mode: 'clear'") < body.index("mode: 'paint'")


def test_fog_is_inverted_in_both_directions():
    body = _fn('inverseOps')
    assert "mode: 'hide'" in body and "mode: 'reveal'" in body


def test_a_toggled_door_inverts_to_a_toggle():
    body = _fn('inverseOps')
    assert "action: 'toggle_door'" in body


def test_an_edited_light_inverts_to_its_old_values():
    body = _fn('inverseOps')
    assert "action: 'update_light'" in body


# --- the stack ---------------------------------------------------------------

def test_undo_covers_every_element_action_by_construction():
    """Recorded in mapElementAction rather than at each call site, so an action
    added later inherits undo instead of quietly not having it."""
    body = _fn('mapElementAction')
    assert 'pushUndo(' in body


def test_an_undo_does_not_record_its_own_inverse():
    """Otherwise the stack never empties and Ctrl+Z ping-pongs."""
    assert 'const before = scene && !undoing ? undoSnapshot(scene) : null;' in _JS
    body = _fn('undoLast')
    assert 'undoing = true;' in body
    assert 'undoing = false;' in body


def test_movement_shares_the_one_stack():
    """Two stacks is how Ctrl+Z came to mean something different from the
    button."""
    body = _fn('undoLast')
    assert 'entry.move' in body
    assert 'undoStack.pop()' in body


def test_the_button_says_what_it_will_undo():
    """It read "Undo move" whatever the GM had just done."""
    body = _fn('updateUndoButton')
    assert "'Undo ' + next.label" in body
    assert 'Undo move' not in _HTML


def test_a_failed_inverse_refuses_by_name_and_clears_the_stack():
    """Half-applying is the dangerous outcome: the entries beneath describe a
    scene that no longer exists, so applying them would move things the GM never
    touched."""
    body = _fn('undoLast')
    assert 'undoStack.length = 0;' in body
    assert "'Could not undo ' + entry.label" in body


def test_the_stack_is_bounded():
    assert 'UNDO_LIMIT' in _JS
    assert 'undoStack.length > UNDO_LIMIT' in _JS


def test_ctrl_z_runs_the_same_path_as_the_button():
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert 'undoLast();' in keydown
    assert "document.getElementById('map-undo').addEventListener('click', undoLast);" in _JS


# --- destructive controls stop looking like safe ones ------------------------
#
# The GM's call was separation over dialogs: a confirm costs a keystroke every
# time a dead mook comes off the board, which in a big fight is most of them.

def test_removing_a_token_is_separated_from_hiding_it():
    """They were 6px apart -- the two things a GM reaches for in the same
    moment, one recoverable and one not."""
    assert 'map-danger-zone' in _HTML
    block = _HTML[_HTML.index('id="map-token-visibility"'):]
    block = block[:block.index('</div>')]
    assert 'map-danger-zone' in block, 'removal must sit in its own zone'


def test_the_danger_zone_is_visibly_fenced():
    css = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()
    rule = css[css.index('.map-danger-zone {'):]
    rule = rule[:rule.index('}')]
    assert 'border-top' in rule and 'margin-top' in rule


def test_the_irreversible_sidebar_actions_are_styled_as_such():
    for control in ('map-delete-token', 'map-reset-fog', 'map-clear-templates'):
        block = _HTML[_HTML.index('id="%s"' % control):]
        block = block[:block.index('>')]
        assert 'map-btn--danger' in block, control


def test_no_confirm_dialog_was_added():
    """Deliberate: the cost of a dialog lands on the most frequent action, and
    undo now covers the ones that used to be unrecoverable."""
    for control in ('map-reset-fog', 'map-clear-templates'):
        handler = _JS[_JS.index("getElementById('%s')" % control):][:400]
        assert 'window.confirm' not in handler
