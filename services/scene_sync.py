"""Projection helpers between durable scenes and live tracker/sheet state."""
from __future__ import annotations

import copy


def _coarsen_live_for_player(live):
    """Strip a non-PC's exact HP, leaving only the coarse status.

    ``hp_status``/``hp_color`` are precomputed upstream by
    ``app._npc_hp_status`` so the map and the tracker cannot disagree about
    where "Wounded" begins. PC health is left alone -- the party already
    shares it, and the tracker sends players exact PC numbers.
    """
    if live.get('is_pc'):
        return live
    live.pop('current_hp', None)
    live.pop('max_hp', None)
    return live


def project_scene(scene, combatants, characters, *, player=False):
    """Overlay authoritative live state without persisting duplicate HP data.

    With ``player=True`` this is the whole player/GM boundary for the map: it
    drops hidden tokens, strips account ids, coarsens NPC health, filters
    GM-only lights and templates, and masks closed secret doors. A new token
    field is visible to players unless it is handled here.
    """
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
            if token.get('live'):
                token['live'] = _coarsen_live_for_player(token['live'])
        kept.append(token)
    projected['tokens'] = kept
    if player:
        projected['lights'] = [light for light in projected.get('lights', [])
                               if light.get('visible_to_players', True)]
        projected['templates'] = [item for item in projected.get('templates', [])
                                  if item.get('visible_to_players', True)]
        for wall in projected.get('walls', []):
            if wall.get('kind') != 'door':
                continue
            if wall.get('secret') and not wall.get('open'):
                wall['kind'] = 'wall'
            # Set False rather than popping the key. Every genuine wall is
            # written with 'secret': False present, so a masked secret door
            # that is MISSING the key is the one wall in the payload shaped
            # differently from the rest -- trivially greppable.
            wall['secret'] = False
    return projected


def player_payload(scene, combatants, characters):
    return project_scene(scene, combatants, characters, player=True)
