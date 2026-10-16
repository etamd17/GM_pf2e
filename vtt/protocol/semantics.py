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
* a GM wall is not zero-length.
"""

from __future__ import annotations


class SemanticError(ValueError):
    """Raised for a schema-valid frame that breaks a frame rule."""


def _keys(items):
    return [(item.get('kind'), item.get('id')) for item in items]


def _no_repeats(values, what):
    if len(set(values)) != len(values):
        raise SemanticError(f'repeated {what}')


def check_frame(schema_key: str, message) -> None:
    """Raise SemanticError when a schema-valid gm/table frame breaks a rule.
    Other schema keys have no extra rules."""
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
    if schema_key == 'gm':
        for item in items:
            if item.get('kind') == 'token':
                _no_repeats(list(item.get('controllerIds') or []), 'controller')
            elif item.get('kind') == 'wall':
                if (item.get('x1'), item.get('y1')) == (item.get('x2'), item.get('y2')):
                    raise SemanticError('zero-length wall')
