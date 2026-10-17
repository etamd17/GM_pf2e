"""Frame rules a JSON schema cannot express, checked after schema validation.

Amendment S5 (handoff 017) removed ``uniqueItems`` from the entity arrays: Ajv
compiles object uniqueness to a quadratic deep-equality helper (886 ms for a
2,048-token table snapshot against 4.9 ms without), and byte-identical
duplicates were all it ever caught. Identity in ADR-0002 section 4 is
``(kind, id)``, so uniqueness is enforced here on that key, at both boundaries.
The client mirrors these rules in ``frontend/vtt/src/protocol/semantics.ts``,
and ``tools/vtt/semantics.mjs`` is the reference Node copy; the shared fixture
corpus keeps all three in agreement.

Rules (each a ``ProtocolValidationError``):

* snapshot ``entities``, patch ``upserted`` and patch ``removed``: no repeated
  ``(kind, id)``;
* a patch never upserts and removes the same key;
* a snapshot carries at most one ``scene`` entity, and it is ``sceneId``'s;
  ``sceneId: null`` means no entities at all;
* GM ``controllerIds`` and ``capabilities`` hold no repeats;
* a GM wall is not zero-length;
* a ``fog`` entity (batch parity-1, GM and table): ``cols * rows`` is at most
  262,144, and ``revealed`` is the canonical unpadded base64url of exactly
  ``ceil(cols * rows / 8)`` bytes (decoding and re-encoding gives the same
  string back, so lenient decoders cannot disagree), whose bits past
  ``cols * rows`` are zero.

Commands (``command``, batch commands-1):

* ``expected`` holds no repeated ``(kind, id)``;
* an update, move, delete, ``table.show`` or ``table.clear`` names its target's
  expected version: ``("scene", sceneId)`` for scene and table commands, the
  payload's entity otherwise;
* ``controllerIds`` hold no repeats;
* a created wall, or an updated one naming all four ends, is not zero-length;
* ``fog.update`` and ``fog.reset`` (parity-1) name ``("fog", "fog")``.
"""

from __future__ import annotations

import base64
import binascii

FOG_MAX_CELLS = 262144


class SemanticError(ValueError):
    """Raised for a schema-valid frame that breaks a frame rule."""


def _keys(items):
    return [(item.get('kind'), item.get('id')) for item in items]


def _no_repeats(values, what):
    if len(set(values)) != len(values):
        raise SemanticError(f'repeated {what}')


_TARGETED = {
    'scene.update': 'scene', 'table.show': 'scene', 'table.clear': 'scene',
    'token.update': 'token', 'token.move': 'token', 'light.update': 'light', 'wall.update': 'wall',
    'entity.delete': None,
    'fog.update': 'fog', 'fog.reset': 'fog',
}


def command_target(message) -> tuple[str, str] | None:
    """The ``(kind, id)`` a schema-valid command must carry an expected version
    for, or None for a create."""
    kind = _TARGETED.get(message.get('type'), False)
    payload = message.get('payload') or {}
    if kind is False:
        return None
    if kind == 'scene':
        return ('scene', message.get('sceneId'))
    if kind == 'fog':
        return ('fog', 'fog')
    if kind is None:
        return (payload.get('kind'), payload.get('id'))
    return (kind, payload.get('id'))


def check_command(message) -> None:
    expected = message.get('expected') or []
    keys = _keys(expected)
    _no_repeats(keys, 'expected key')
    target = command_target(message)
    if target is not None and target not in keys:
        raise SemanticError('missing expected version for the target')
    payload = message.get('payload') or {}
    _no_repeats(list(payload.get('controllerIds') or []), 'controller')
    if message.get('type') in ('wall.create', 'wall.update') and all(
            k in payload for k in ('x1', 'y1', 'x2', 'y2')):
        if (payload['x1'], payload['y1']) == (payload['x2'], payload['y2']):
            raise SemanticError('zero-length wall')


def fog_ok(item) -> bool:
    """The parity-1 fog bitset rule (see the module docstring)."""
    cols, rows, text = item.get('cols'), item.get('rows'), item.get('revealed')
    if not isinstance(cols, int) or not isinstance(rows, int) or not isinstance(text, str):
        return False
    cells = cols * rows
    if cells > FOG_MAX_CELLS:
        return False
    try:
        data = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
    except (binascii.Error, ValueError):
        return False
    if len(data) != -(-cells // 8):
        return False
    if base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii') != text:
        return False
    spare = cells % 8
    return spare == 0 or data[-1] >> spare == 0


def check_frame(schema_key: str, message) -> None:
    """Raise SemanticError when a schema-valid message breaks a rule. Only
    gm/table frames and commands have extra rules."""
    if schema_key == 'command' and isinstance(message, dict):
        check_command(message)
        return
    if schema_key not in ('gm', 'table') or not isinstance(message, dict):
        return
    frame = message.get('frame')
    if frame == 'snapshot':
        entities = message.get('entities') or []
        _no_repeats(_keys(entities), 'entity key')
        scenes = [e for e in entities if e.get('kind') == 'scene']
        if message.get('sceneId') is None:
            if entities:
                raise SemanticError('entities without a scene')
        elif len(scenes) > 1 or any(s.get('id') != message.get('sceneId') for s in scenes):
            raise SemanticError('scene entity does not match sceneId')
        if schema_key == 'gm':
            _no_repeats(list(message.get('capabilities') or []), 'capability')
        items = entities
    elif frame == 'patch':
        upserted = message.get('upserted') or []
        removed = message.get('removed') or []
        up, rm = _keys(upserted), _keys(removed)
        _no_repeats(up, 'upserted key')
        _no_repeats(rm, 'removed key')
        if set(up) & set(rm):
            raise SemanticError('key both upserted and removed')
        items = upserted
    else:
        return
    for item in items:
        if item.get('kind') == 'fog' and not fog_ok(item):
            raise SemanticError('malformed fog bitset')
    if schema_key == 'gm':
        for item in items:
            if item.get('kind') == 'token':
                _no_repeats(list(item.get('controllerIds') or []), 'controller')
            elif item.get('kind') == 'wall':
                if (item.get('x1'), item.get('y1')) == (item.get('x2'), item.get('y2')):
                    raise SemanticError('zero-length wall')
