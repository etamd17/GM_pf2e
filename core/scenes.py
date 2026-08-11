"""Campaign-scoped tactical scene storage.

Scenes own map presentation state only: background, grid, token placement and
visibility. Character HP/conditions and encounter turns remain authoritative in
the existing sheet/tracker state and are projected onto tokens at read time.
"""
from __future__ import annotations

import os
import time

from core import storage


SCHEMA_VERSION = 3
DEFAULT_WIDTH = 1400
DEFAULT_HEIGHT = 900
DEFAULT_GRID = 70

# Mirrors the clamps new_scene() applies. MAX_DIMENSION is a sanity bound, not a
# rendering guarantee: 8000x8000 is 64 megapixels and iOS Safari blanks a canvas
# past ~16.7 MP. The table screen is a laptop driving a TV, so that is not a
# constraint today -- revisit if the map ever has to run on a tablet.
MIN_WIDTH = 320
MIN_HEIGHT = 240
MAX_DIMENSION = 8000


def fit_scene_dimensions(image_width, image_height):
    """Scene dimensions for an uploaded background, preserving its aspect ratio.

    The scene used to keep whatever size it was created with while the image was
    drawn to fill it, so a 4096x2304 battlemap was squashed into 1400x900. The
    image IS the map; the scene should take its shape.

    Scales DOWN only -- a small image is not blown up, it just gets a small
    scene, and zoom handles the rest. The minimums are applied last and are the
    one case where aspect is not preserved: a 40x10 sliver would otherwise
    produce a scene too thin to interact with.

    Returns (width, height) as ints, or None if the size is unusable.
    """
    try:
        width = float(image_width)
        height = float(image_height)
    except (TypeError, ValueError):
        return None
    if not (width > 0 and height > 0):
        return None
    scale = min(1.0, MAX_DIMENSION / width, MAX_DIMENSION / height)
    width = int(round(width * scale))
    height = int(round(height * scale))
    return max(MIN_WIDTH, width), max(MIN_HEIGHT, height)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


def _clamp_number(value, low, high, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def new_scene(cid, name='Untitled Scene', *, width=DEFAULT_WIDTH,
              height=DEFAULT_HEIGHT, grid_size=DEFAULT_GRID):
    sid = storage.new_id()
    now = _now()
    return {
        'schema_version': SCHEMA_VERSION,
        'id': sid,
        'campaign_id': cid,
        'name': (str(name or '').strip() or 'Untitled Scene')[:100],
        'revision': 1,
        'created_at': now,
        'updated_at': now,
        'width': int(_clamp_number(width, 320, 8000, DEFAULT_WIDTH)),
        'height': int(_clamp_number(height, 240, 8000, DEFAULT_HEIGHT)),
        'grid': {
            'size': int(_clamp_number(grid_size, 20, 300, DEFAULT_GRID)),
            'offset_x': 0,
            'offset_y': 0,
            'visible': True,
        },
        'background': None,
        'settings': {
            'player_movement': True,
            'snap_to_grid': True,
            'dynamic_lighting': False,
            'default_vision': 700,
        },
        'tokens': [],
        'fog': {'enabled': False, 'operations': []},
        'walls': [],
        'lights': [],
        'templates': [],
    }


def normalize_scene(scene):
    """Backfill presentation defaults without requiring a data migration."""
    if not isinstance(scene, dict):
        return scene
    scene.setdefault('schema_version', SCHEMA_VERSION)
    grid = scene.setdefault('grid', {})
    grid.setdefault('size', DEFAULT_GRID)
    grid.setdefault('offset_x', 0)
    grid.setdefault('offset_y', 0)
    grid.setdefault('visible', True)
    settings = scene.setdefault('settings', {})
    settings.setdefault('player_movement', True)
    settings.setdefault('snap_to_grid', True)
    settings.setdefault('dynamic_lighting', False)
    settings.setdefault('default_vision', 700)
    fog = scene.setdefault('fog', {})
    fog.setdefault('enabled', False)
    fog.setdefault('operations', [])
    scene.setdefault('walls', [])
    scene.setdefault('lights', [])
    scene.setdefault('templates', [])
    for token in scene.setdefault('tokens', []):
        token.setdefault('size', 1)
        token.setdefault('color', '#4f8a62' if token.get('is_pc') else '#a84b45')
        token.setdefault('image', None)
        token.setdefault('image_focus', {'x': 50, 'y': 50})
        token.setdefault('locked', False)
        token.setdefault('show_nameplate', True)
        token.setdefault('visible_to_players', True)
        token.setdefault('controller_character_id', token.get('character_id'))
        token.setdefault('controller_name', token.get('name') if token.get('is_pc') else None)
        token.setdefault('sheet_url', None)
        token.setdefault('vision_radius', settings.get('default_vision', 700))
    return scene


def list_scenes(cid):
    root = storage.scenes_dir(cid)
    if not os.path.isdir(root):
        return []
    found = []
    for name in os.listdir(root):
        if not name.endswith('.json') or name == 'index.json':
            continue
        scene = storage.load_json(os.path.join(root, name))
        if isinstance(scene, dict) and scene.get('campaign_id') == cid:
            found.append(scene)
    found.sort(key=lambda s: (s.get('updated_at', ''), s.get('name', '')), reverse=True)
    return found


def scene_summaries(cid):
    return [{
        'id': scene['id'],
        'name': scene.get('name', 'Untitled Scene'),
        'revision': int(scene.get('revision', 1)),
        'updated_at': scene.get('updated_at'),
        'has_background': bool(scene.get('background')),
        'token_count': len(scene.get('tokens') or []),
    } for scene in list_scenes(cid)]


def load_scene(cid, scene_id):
    scene = storage.load_json(storage.scene_file(cid, scene_id))
    if not isinstance(scene, dict) or scene.get('campaign_id') != cid:
        return None
    return normalize_scene(scene)


def save_scene(cid, scene, *, bump_revision=True):
    if not isinstance(scene, dict) or scene.get('campaign_id') != cid:
        raise ValueError('scene does not belong to campaign')
    if bump_revision:
        scene['revision'] = int(scene.get('revision', 0)) + 1
    scene['schema_version'] = SCHEMA_VERSION
    scene['updated_at'] = _now()
    storage.atomic_write_json(storage.scene_file(cid, scene['id']), scene, indent=2)
    return scene


def create_scene(cid, name='Untitled Scene', **kwargs):
    """Create a scene. It is NOT put on the table -- see table_scene_id."""
    if cid:
        storage.ensure_campaign_dirs(cid)
    else:
        os.makedirs(storage.scene_assets_dir(None), exist_ok=True)
    scene = new_scene(cid, name, **kwargs)
    storage.atomic_write_json(storage.scene_file(cid, scene['id']), scene, indent=2)
    return scene


# --- What the table is showing, versus what the GM is looking at -----------
#
# These used to be the same thing: creating or selecting a scene made it
# "active", and every connected client navigated to it. That made prep during a
# live session impossible -- building next week's ambush pushed it onto the
# table mid-fight.
#
# The stored key stays 'active_scene_id' so no migration is needed, but its
# meaning is now precisely "the scene the table screen is showing". What the GM
# has open is just their URL, and is nobody else's business.

def table_scene_id(cid):
    """The scene currently pushed to the table, or None.

    Deliberately does NOT fall back to "the first scene that exists". Nothing on
    the table is a real and common state -- before a session, or between fights
    -- and the old fallback made it unrepresentable.
    """
    index = storage.load_json(storage.scenes_index_file(cid), default={}) or {}
    sid = index.get('active_scene_id')
    return sid if (sid and load_scene(cid, sid)) else None


def set_table_scene(cid, scene_id):
    """Push a scene to the table, or pass None to clear it."""
    if scene_id is not None and not load_scene(cid, scene_id):
        raise ValueError('unknown scene')
    storage.atomic_write_json(storage.scenes_index_file(cid), {
        'schema_version': SCHEMA_VERSION,
        'active_scene_id': scene_id,
        'updated_at': _now(),
    }, indent=2)


def default_open_scene_id(cid):
    """Which scene /map should open when the GM arrives without naming one.

    This is where the old "first scene" fallback belongs: it is a convenience
    for the GM's own landing, not a statement about the table. Prefers whatever
    is on the table, then the most recently updated scene.
    """
    on_table = table_scene_id(cid)
    if on_table:
        return on_table
    all_scenes = list_scenes(cid)
    if not all_scenes:
        return None

    # Ordered by file mtime, not the stored updated_at: that is a
    # second-resolution string, so two scenes touched in the same second tie and
    # the winner falls out of filesystem listing order. mtime is finer grained
    # and is the more honest answer to "which did I last work on" anyway.
    def last_written(summary):
        try:
            return os.path.getmtime(storage.scene_file(cid, summary['id']))
        except OSError:
            return 0.0

    return max(all_scenes, key=last_written)['id']


def delete_scene(cid, scene_id):
    """Remove a scene and its uploaded background. Returns True if it existed.

    Refuses while the scene is on the table: deleting what the players are
    currently looking at should be a deliberate two-step, not a misclick.
    """
    scene = load_scene(cid, scene_id)
    if not scene:
        return False
    if table_scene_id(cid) == scene_id:
        raise ValueError('scene is on the table')
    # Every asset this scene owns is prefixed with its id -- the background is
    # '<id><ext>' and token art is '<id>_token_<token><ext>' -- so the whole set
    # can be removed without walking the scene's tokens.
    assets_dir = storage.scene_assets_dir(cid)
    if os.path.isdir(assets_dir):
        for name in os.listdir(assets_dir):
            if name.startswith(scene_id + '_token_'):
                try:
                    os.remove(os.path.join(assets_dir, name))
                except OSError:
                    pass
    background = (scene.get('background') or {}).get('filename')
    if background:
        # Only ever a bare '<scene_id><ext>' written by the upload route, but
        # basename() it anyway: this joins a stored value onto a real path.
        asset = os.path.join(storage.scene_assets_dir(cid), os.path.basename(background))
        try:
            os.remove(asset)
        except OSError:
            pass
    try:
        os.remove(storage.scene_file(cid, scene_id))
    except OSError:
        return False
    return True


def add_token(scene, *, name, x=1, y=1, size=1, color='#b88a44',
              image=None, character_id=None, combatant_id=None,
              controller_user_id=None, controller_character_id=None,
              controller_name=None, is_pc=False, visible_to_players=True,
              image_focus=None, sheet_url=None, locked=False,
              show_nameplate=True):
    token = {
        'id': storage.new_id(),
        'name': (str(name or '').strip() or 'Token')[:100],
        'x': _clamp_number(x, 0, float(scene.get('width', DEFAULT_WIDTH)), 1),
        'y': _clamp_number(y, 0, float(scene.get('height', DEFAULT_HEIGHT)), 1),
        'size': _clamp_number(size, 0.5, 6, 1),
        'color': str(color or '#b88a44')[:20],
        'image': str(image)[:500] if image else None,
        'image_focus': image_focus if isinstance(image_focus, dict) else {'x': 50, 'y': 50},
        'character_id': character_id or None,
        'combatant_id': combatant_id or None,
        'controller_user_id': controller_user_id or None,
        'controller_character_id': controller_character_id or character_id or None,
        'controller_name': (str(controller_name)[:100] if controller_name else
                            (str(name)[:100] if is_pc else None)),
        'is_pc': bool(is_pc),
        'visible_to_players': bool(visible_to_players),
        'locked': bool(locked),
        'show_nameplate': bool(show_nameplate),
        'sheet_url': str(sheet_url)[:500] if sheet_url else None,
        'vision_radius': int(_clamp_number(
            (scene.get('settings') or {}).get('default_vision', 700), 0, 5000, 700)),
    }
    scene.setdefault('tokens', []).append(token)
    return token


def update_token_position(scene, token_id, x, y):
    token = next((t for t in scene.get('tokens', []) if t.get('id') == token_id), None)
    if not token:
        return None
    token['x'] = _clamp_number(x, 0, float(scene.get('width', DEFAULT_WIDTH)), token.get('x', 0))
    token['y'] = _clamp_number(y, 0, float(scene.get('height', DEFAULT_HEIGHT)), token.get('y', 0))
    return token


def remove_token(scene, token_id):
    before = len(scene.get('tokens', []))
    scene['tokens'] = [t for t in scene.get('tokens', []) if t.get('id') != token_id]
    return len(scene['tokens']) != before


# NOTE: a second player sanitizer, sanitize_for_player(), used to live here.
# It had zero callers and had already drifted from the real one -- it never
# filtered GM-only templates, and it masked closed secret doors by popping
# 'secret' (which is exactly what made them greppable in the player payload).
# Deleted rather than fixed: two sanitizers means one of them is wrong and
# nobody notices. The single player/GM boundary is
# services/scene_sync.py::project_scene(..., player=True).
