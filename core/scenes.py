"""Campaign-scoped tactical scene storage.

Scenes own map presentation state only: background, grid, token placement and
visibility. Character HP/conditions and encounter turns remain authoritative in
the existing sheet/tracker state and are projected onto tokens at read time.
"""
from __future__ import annotations

import copy
import os
import time

from core import storage


SCHEMA_VERSION = 3
DEFAULT_WIDTH = 1400
DEFAULT_HEIGHT = 900
DEFAULT_GRID = 70


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
    if cid:
        storage.ensure_campaign_dirs(cid)
    else:
        os.makedirs(storage.scene_assets_dir(None), exist_ok=True)
    scene = new_scene(cid, name, **kwargs)
    storage.atomic_write_json(storage.scene_file(cid, scene['id']), scene, indent=2)
    if not active_scene_id(cid):
        set_active_scene(cid, scene['id'])
    return scene


def active_scene_id(cid):
    index = storage.load_json(storage.scenes_index_file(cid), default={}) or {}
    sid = index.get('active_scene_id')
    if sid and load_scene(cid, sid):
        return sid
    all_scenes = list_scenes(cid)
    return all_scenes[0]['id'] if all_scenes else None


def set_active_scene(cid, scene_id):
    if not load_scene(cid, scene_id):
        raise ValueError('unknown scene')
    storage.atomic_write_json(storage.scenes_index_file(cid), {
        'schema_version': SCHEMA_VERSION,
        'active_scene_id': scene_id,
        'updated_at': _now(),
    }, indent=2)


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


def sanitize_for_player(scene):
    safe = copy.deepcopy(scene)
    safe['tokens'] = [t for t in safe.get('tokens', []) if t.get('visible_to_players', True)]
    for token in safe['tokens']:
        token.pop('controller_user_id', None)
        if not token.get('is_pc'):
            token.pop('sheet_url', None)
    safe['lights'] = [light for light in safe.get('lights', [])
                      if light.get('visible_to_players', True)]
    for wall in safe.get('walls', []):
        if wall.get('kind') == 'door' and wall.get('secret') and not wall.get('open'):
            wall['kind'] = 'wall'
            wall.pop('secret', None)
        elif wall.get('kind') == 'door':
            wall.pop('secret', None)
    return safe
