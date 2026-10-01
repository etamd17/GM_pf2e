"""Thin adapters around the application's existing rule engines and parsers."""

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable

from .types import DraftInput, DraftKind, JSON, WorkflowError


AUTHORITY_KEYS = frozenset({'id', 'campaign_id', 'owner_user_id', 'owner_user_ids',
                           'editor_user_ids', 'viewer_user_ids', 'schema_version',
                           'system', 'target_id', 'base_fingerprint'})


def _invalid():
    return WorkflowError('invalid_character_input', 'Check the character input format.', 422)


def _object(value):
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise _invalid()
    return value


def _strip_authority(document):
    result = deepcopy(_object(document))
    for key in AUTHORITY_KEYS:
        result.pop(key, None)
    if isinstance(result.get('build'), dict):
        for key in AUTHORITY_KEYS:
            result['build'].pop(key, None)
    return result


def _fields(data, names, expected, item_type=None):
    for name in names:
        if name not in data:
            continue
        value = data[name]
        if not isinstance(value, expected):
            raise _invalid()
        if item_type and any(not isinstance(item, item_type) for item in value):
            raise _invalid()


def _validate_shape(system, kind, data):
    builder = kind == 'pf2e_builder'
    build = data if builder else _object(data.get('build', data))
    _text(build.get('name'))
    if system == 'pf2e':
        _text(build.get('class_name' if builder else 'class'))
        _text(build.get('ancestry'))
        _fields(build, ('abilities', 'attributes', 'proficiencies', 'mods'), dict)
        _fields(build, ('spellCasters', 'weapons', 'armor'), list, dict)
        _fields(build, ('skills', 'languages'), list, str)
        _fields(build, ('feats', 'equipment'), list, dict if builder else (dict, list))
        for caster in build.get('spellCasters', []):
            _fields(caster, ('spells',), list, dict)
            for spell_level in caster.get('spells', []):
                _fields(spell_level, ('list',), list, str)
    else:
        # Missing path is existing soft guidance, not a hard rules violation.
        _fields(build, ('path',), str)
        _fields(build, ('attributes', 'skills', 'stat_bonuses'), dict)
        _fields(build, ('talents', 'custom_weapons'), list, dict)
        _fields(build, ('expertises', 'ideal_words', 'fabrials', 'epic_choices',
                        'infected_arts'), list, str)


@dataclass(frozen=True)
class SystemAdapter:
    system: str
    build: Callable
    pdf: Callable
    validate: Callable | None = None
    merge: Callable | None = None

    def normalize(self, inputs: DraftInput, current: JSON | None, *, override: bool) -> JSON:
        if (not isinstance(inputs, DraftInput) or inputs.payload_version != 1
                or inputs.kind not in (f'{self.system}_builder', f'{self.system}_import')):
            raise _invalid()
        data = _strip_authority(inputs.submission)
        _validate_shape(self.system, inputs.kind, data)
        # A stored force flag is just user input. Authority is a separate argument.
        data.pop('force_save', None)
        data['force'] = bool(override)
        try:
            if self.system == 'pf2e':
                if inputs.kind == 'pf2e_builder':
                    if current is not None:
                        raise WorkflowError('unsupported_update', 'Use import to update this character.', 422)
                    issues = self.validate(data)
                    if issues and not override:
                        raise WorkflowError('rules_violation', 'This build exceeds the rules.', 422,
                                            tuple(issues))
                    result = self.build(data)
                else:
                    imported = _object(data.get('build', data))
                    imported.pop('force', None)
                    if current is not None:
                        old = _object(current.get('build', current))
                        if old.get('name') != imported['name']:
                            raise WorkflowError('rename_not_supported',
                                                'Keep the existing character name when importing.', 409)
                        result = self.merge(current, {'build': imported})
                    else:
                        result = {'success': True, 'build': imported}
            else:
                result = self.build(data, current)
                if inputs.kind == 'cosmere_import':
                    result['imported_from'] = 'pdf'
                    result['import_extras'] = deepcopy(data.get('import_extras', {}))
                    if current is None and isinstance(data.get('play_state'), dict):
                        result['play_state'] = deepcopy(data['play_state'])
            return _strip_authority(result)
        except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
            raise _invalid() from None

    def parse_import(self, content: bytes, filename: str) -> DraftInput:
        if not isinstance(content, bytes) or not content or len(content) > 2 * 1024 * 1024:
            raise _invalid()
        try:
            is_pdf = content.startswith(b'%PDF') or filename.lower().endswith('.pdf')
            if self.system == 'pf2e':
                if is_pdf:
                    build, _play = self.pdf(content)
                    data = {'build': build}
                else:
                    data = _object(json.loads(content.decode('utf-8')))
                    data = {'build': _object(data.get('build', data))}
            else:
                if not is_pdf:
                    raise _invalid()
                build, play, extras = self.pdf(content)
                data = {'build': build, 'play_state': play,
                        'import_extras': {k: extras.get(k) for k in ('weapons', 'equipment', 'spheres')}}
            data = _strip_authority(data)
            _validate_shape(self.system, f'{self.system}_import', data)
        except WorkflowError:
            raise
        except Exception:
            # PDF readers have multiple parser-specific error classes; none of
            # their messages or internals are safe public responses.
            raise WorkflowError('invalid_import', 'That character file could not be parsed.', 422) from None
        return DraftInput(f'{self.system}_import', 1, deepcopy(data), {}, deepcopy(data))

    def fingerprint(self, kind: DraftKind, document: JSON) -> str:
        if kind not in (f'{self.system}_builder', f'{self.system}_import'):
            raise _invalid()
        if self.system == 'pf2e':
            # Project through the same import key policy: HP/notes/currency do
            # not enter a build fingerprint, so live ticks cannot stale a draft.
            projection = self.merge({}, _strip_authority(document))
        else:
            projection = {key: document.get(key) for key in ('build', 'name', 'house_metal')}
        return sha256(json.dumps(projection, sort_keys=True, separators=(',', ':'),
                                 ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def build_system_adapters(*, pf2e_build: Callable, pf2e_validate: Callable,
                          pf2e_merge: Callable, pf2e_pdf: Callable,
                          cosmere_build: Callable, cosmere_pdf: Callable) -> dict[str, SystemAdapter]:
    return {'pf2e': SystemAdapter('pf2e', pf2e_build, pf2e_pdf, pf2e_validate, pf2e_merge),
            'cosmere': SystemAdapter('cosmere', cosmere_build, cosmere_pdf)}
