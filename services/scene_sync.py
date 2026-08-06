"""Projection helpers between durable scenes and live tracker/sheet state."""
from __future__ import annotations

import copy


def project_scene(scene, combatants, characters, *, player=False):
    """Overlay authoritative live state without persisting duplicate HP data."""
    projected = copy.deepcopy(scene)
    kept = []
    for token in projected.get('tokens', []):
        live = None
        if token.get('combatant_id'):
            live = combatants.get(token['combatant_id'])
        if live is None and token.get('character_id'):
            live = characters.get(token['character_id'])
        if live:
            token['live'] = copy.deepcopy(live)
            if live.get('visible_to_players') is False:
                token['visible_to_players'] = False
        if player and not token.get('visible_to_players', True):
            continue
        if player:
            token.pop('controller_user_id', None)
        kept.append(token)
    projected['tokens'] = kept
    if player:
        projected['lights'] = [light for light in projected.get('lights', [])
                               if light.get('visible_to_players', True)]
        projected['templates'] = [item for item in projected.get('templates', [])
                                  if item.get('visible_to_players', True)]
        for wall in projected.get('walls', []):
            if wall.get('kind') == 'door' and wall.get('secret') and not wall.get('open'):
                wall['kind'] = 'wall'
                wall.pop('secret', None)
            elif wall.get('kind') == 'door':
                wall.pop('secret', None)
    return projected


def player_payload(scene, combatants, characters):
    return project_scene(scene, combatants, characters, player=True)
