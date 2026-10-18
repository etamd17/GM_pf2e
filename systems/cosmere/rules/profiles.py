"""Exact package-bound mechanics, with an adapter for legacy Stormlight builds."""
from copy import deepcopy
from functools import lru_cache
from collections.abc import Mapping, Sequence
from typing import Protocol

from .schema import (CosmereRulesBinding, PackagePin, RulesScope, RulesValidationError,
                     SPOILER_RANK, from_dict, require, to_dict)
from .seeds import load_seed_registry


class CosmereRulesProfile(Protocol):
    binding: CosmereRulesBinding

    def catalog(self) -> Mapping: ...

    def level_grants(self, level: int, build=None) -> Mapping: ...

    def validate_build(self, choices: Mapping, existing=None, transition=None) -> Sequence: ...

    def project_actor(self, document: Mapping) -> Mapping: ...

    def normalize_runtime_state(self, state: Mapping, target_id: str | None = None,
                                *, build=None, prior_build=None) -> Mapping: ...

    def apply_action(self, state: Mapping, action: Mapping) -> Mapping: ...

    def encounter_budget(self, actors: Sequence, adversaries: Sequence) -> Mapping: ...

    def tracker_block(self, actor: Mapping) -> Mapping: ...

    def create_build(self, document: Mapping, **kwargs): ...

    def builder_context(self, build, homebrew=None) -> Mapping: ...


@lru_cache(maxsize=1)
def default_registry():
    """Deployed packages are immutable; verify the checked-in registry once."""
    return load_seed_registry()


def package_pin(pack):
    return PackagePin(pack.id, pack.version, pack.package_hash)


def normalize_theme(document=None):
    document = document or {}
    theme = document.get('cosmere_theme', document.get('cosmere_world', 'stormlight'))
    return 'mistborn' if theme == 'mistborn' else 'stormlight'


def _needs_mistborn_package(document):
    if not isinstance(document, Mapping):
        return False
    if document.get('origin_world') == 'scadrial' or document.get('default_world') == 'scadrial':
        return True
    binding = document.get('rules_binding')
    if isinstance(binding, CosmereRulesBinding):
        return binding.default_world == 'scadrial' or any(
            pin.id.startswith('cosmere.mistborn.') for pin in binding.enabled_packs)
    if isinstance(binding, Mapping):
        return binding.get('default_world') == 'scadrial' or any(
            isinstance(pin, Mapping) and str(pin.get('id', '')).startswith('cosmere.mistborn.')
            for pin in binding.get('enabled_packs', ()))
    return False


def normalize_binding(document=None, *, registry=None):
    """Only a missing binding opts into legacy rules; malformed pins are errors."""
    registry = registry or default_registry()
    if (_needs_mistborn_package(document)
            and 'cosmere.mistborn.core' not in registry.packs):
        raise RulesValidationError('Mistborn rules data is not installed on this server yet.')
    document = document or {}
    if 'rules_binding' in document:
        explicit = document['rules_binding']
        binding = explicit if isinstance(explicit, CosmereRulesBinding) else from_dict(CosmereRulesBinding, explicit)
    else:
        binding = CosmereRulesBinding(package_pin(registry.packs['cosmere.core']),
                                     (package_pin(registry.packs['cosmere.stormlight.legacy']),),
                                     'roshar', ())
    registry.resolve_binding(binding)
    return CosmereRulesBinding(binding.core_package,
                               tuple(sorted(binding.enabled_packs, key=lambda pin: pin.id)),
                               binding.default_world, tuple(sorted(binding.allowed_eras)),
                               tuple(sorted(binding.allowed_worlds)))


class ReferenceRulesProfile:
    """Metadata remains usable while an enabled world lacks mechanical automation."""

    def __init__(self, binding, registry, *, homebrew=None, spoiler_scope='player',
                 scope=None):
        require(spoiler_scope in SPOILER_RANK, 'invalid catalog spoiler scope')
        self.binding = binding
        self.registry = registry
        self.packages = registry.resolve_binding(binding)
        self.homebrew = homebrew or {}
        self.spoiler_scope = spoiler_scope
        self.scope = scope

    def _source_visible(self, artifact_id):
        return SPOILER_RANK[self.registry.artifacts[artifact_id].spoiler_scope] <= SPOILER_RANK[self.spoiler_scope]

    def _entry_visible(self, entry):
        worlds = ({self.scope.world} if self.scope is not None
                  else set(self.binding.allowed_worlds))
        eras = ({self.scope.era} if self.scope is not None and self.scope.era is not None
                else set(self.binding.allowed_eras))
        return (('cosmere' in entry['worlds'] or bool(set(entry['worlds']) & worlds))
                and (not entry['eras'] or bool(set(entry['eras']) & eras))
                and SPOILER_RANK[entry['spoiler_scope']] <= SPOILER_RANK[self.spoiler_scope]
                and all(self._source_visible(citation['artifact_id']) for citation in entry.get('citations', []))
                and all(self._source_visible(source) for source in entry.get('provenance', {}).get('source_candidates', [])))

    def catalog(self):
        catalogs = {}
        for pack in self.packages:
            for record in pack.records:
                entry = to_dict(record)
                entry['package'] = {**to_dict(package_pin(pack)),
                                    'dependencies': list(pack.dependencies)}
                if not self._entry_visible(entry):
                    continue
                catalogs.setdefault(record.kind, []).append(entry)
        sources = sorted({source for pack in self.packages for source in pack.sources if self._source_visible(source)})
        return {'rules_binding': to_dict(self.binding),
                'packages': [{**to_dict(package_pin(pack)), 'kind': pack.kind,
                              'dependencies': list(pack.dependencies),
                              'worlds': list(pack.worlds), 'eras': list(pack.eras),
                              'spoiler_scope': pack.spoiler_scope,
                              'audit_status': pack.audit_status} for pack in self.packages],
                'catalogs': catalogs, 'automation': {'build': 'blocked'},
                'source_references': [to_dict(self.registry.artifacts[source]) for source in sources],
                'eligible': False, 'blocked_reasons': ['No mechanical profile is installed for this binding.']}

    def create_build(self, document):
        raise RulesValidationError('No mechanical profile is installed for this binding.')

    def builder_context(self, build, homebrew=None):
        raise RulesValidationError('Builder context requires an installed mechanical profile.')

    def level_grants(self, level, build=None):
        raise RulesValidationError('Level grants require an installed mechanical profile.')

    def validate_build(self, choices, existing=None, transition=None):
        return self.catalog()['blocked_reasons']

    def project_actor(self, document):
        raise RulesValidationError('Actor projection requires an installed mechanical profile.')

    def normalize_runtime_state(self, state, target_id=None, *, build=None,
                                prior_build=None):
        raise RulesValidationError('Runtime state requires an installed mechanical profile.')

    def apply_action(self, state, action):
        raise RulesValidationError('Actions require an installed mechanical profile.')

    def encounter_budget(self, actors, adversaries):
        return {'automation': 'gm_adjudicated', 'blocked_reasons': ['Encounter budgeting requires GM adjudication.']}

    def tracker_block(self, actor):
        raise RulesValidationError('Tracker projection requires an installed mechanical profile.')


class StormlightRulesProfile(ReferenceRulesProfile):
    """Retain the existing Stormlight engine and its public derived results."""

    def catalog(self):
        from systems import cosmere
        from systems.cosmere import radiant, origins, items
        result = super().catalog()
        legacy = {
            'skills': [{'code': code, 'name': name, 'attribute': cosmere.SKILL_ATTR[code]}
                       for code, name in cosmere.SKILL_NAMES.items()],
            'paths': [{'id': key, **deepcopy(origins.PATH_INFO[key])} for key in cosmere.PATHS],
            'radiant_orders': [{'id': key, 'name': value['name']} for key, value in radiant.RADIANT_ORDERS.items()],
            'ancestries': [{'id': key.lower(), 'name': key} for key in ('Human', 'Singer')],
            'resources': [{'id': key, 'name': key.title()} for key in ('health', 'focus', 'investiture')],
            'items': deepcopy(items.catalog()),
            'path_info': deepcopy(origins.PATH_INFO),
        }
        for kind, entries in legacy.items():
            if isinstance(entries, Mapping):
                normalized = {key: self._legacy_entry({**value, 'id': key}) for key, value in entries.items()}
                result['catalogs'][kind] = {key: entry for key, entry in normalized.items() if self._entry_visible(entry)}
            else:
                normalized = [self._legacy_entry(entry) for entry in entries]
                result['catalogs'][kind] = [entry for entry in normalized if self._entry_visible(entry)]
        result.update(automation={'build': 'calculated', 'runtime': 'tracked',
                                  'encounter_budget': 'gm_adjudicated'},
                      eligible=True, blocked_reasons=[])
        return result

    def _legacy_entry(self, entry):
        """Expose legacy content without turning a source candidate into a verified citation."""
        owner = next(pack for pack in self.packages
                     if pack.id == 'cosmere.stormlight.legacy')
        return {**entry, 'worlds': ['roshar'], 'eras': [], 'spoiler_scope': 'player',
                'automation': 'reference', 'audit_status': 'provisional',
                'citations': [], 'citation_state': 'unverified',
                 'package': {**to_dict(package_pin(owner)),
                             'dependencies': list(owner.dependencies)},
                'provenance': {'status': 'provisional', 'page_status': 'unverified',
                               'verification_required': True,
                               'source_candidates': ['stormlight.handbook']}}

    def create_build(self, document):
        from systems.cosmere.stormlight_build import StormlightBuild
        return StormlightBuild(document, homebrew=self.homebrew)

    def builder_context(self, build, homebrew=None):
        from systems.cosmere.stormlight_catalog import builder_context
        return builder_context(build, self.homebrew if homebrew is None else homebrew)

    def level_grants(self, level, build=None):
        from systems.cosmere.stormlight_build import level_grants
        attributes = (getattr(build, 'attributes', None) if build is not None else None)
        if attributes is None and isinstance(build, Mapping):
            attributes = build.get('attributes')
        strength = attributes.get('str', 0) if isinstance(attributes, Mapping) else 0
        return level_grants(level, strength)

    def validate_build(self, choices, existing=None, transition=None):
        build = self.create_build(choices) if isinstance(choices, Mapping) else choices
        return build.validate()

    def project_actor(self, document):
        build = self.create_build(document) if isinstance(document, Mapping) else document
        return build.to_actor_doc()

    def normalize_runtime_state(self, state, target_id=None, *, build=None,
                                prior_build=None):
        from systems.cosmere.effects import normalize_runtime_state
        require(isinstance(target_id, str) and bool(target_id),
                'Runtime effect normalization requires an actor identity.')
        return normalize_runtime_state(state, target_id)

    def apply_action(self, state, action):
        from systems.cosmere.combat import apply_damage, DAMAGE_TYPES
        require(action.get('type') == 'damage', 'Unsupported Stormlight profile action.')
        damage_type = action.get('damage_type', 'impact')
        require(damage_type in DAMAGE_TYPES, 'Invalid Stormlight damage type.')
        result = self.normalize_runtime_state(
            state, action.get('target_id') or 'cosmere.actor.runtime')
        health, taken, dropped = apply_damage(result.get('health', 0), action.get('amount', 0),
                                             damage_type, result.get('deflect', 0))
        result.update(health=health, damage_taken=taken, reduced_to_zero=dropped)
        return result

    def tracker_block(self, actor):
        from systems.cosmere.actor import CosmereActor
        return CosmereActor(actor).tracker_block()


_PROFILE_FACTORIES = {'cosmere.stormlight.legacy': StormlightRulesProfile}


def register_profile(package_id, factory):
    """Register server-owned mechanics for an exact, validated world package."""
    require(package_id in default_registry().packs, 'unknown mechanical profile package')
    require(callable(factory), 'invalid mechanical profile factory')
    _PROFILE_FACTORIES[package_id] = factory


def _scope(document, binding):
    document = document or {}
    origin = document.get('origin_world')
    era = document.get('era')
    if not origin:
        if len(binding.allowed_worlds) != 1:
            return None
        origin = binding.allowed_worlds[0]
    require(origin in binding.allowed_worlds, 'actor world outside binding scope')
    if origin == 'scadrial':
        if not era and len(binding.allowed_eras) == 1:
            era = binding.allowed_eras[0]
        require(era in binding.allowed_eras, 'actor era outside binding scope')
    else:
        require(era in (None, ''), 'only a Scadrial actor may select an era')
        era = None
    return RulesScope(origin, era)


def _profile_for_scope(binding, registry, scope, *, homebrew=None,
                       spoiler_scope='player'):
    packages = registry.resolve_binding(binding)
    package_ids = {pack.id for pack in packages}
    if scope.world == 'roshar' and 'cosmere.stormlight.legacy' in package_ids:
        factory = StormlightRulesProfile
    elif scope.world == 'scadrial':
        era_suffixes = {scope.era, scope.era.replace('-', '')}
        era_ids = {f'cosmere.mistborn.{suffix}' for suffix in era_suffixes}
        if 'cosmere.mistborn.core' in package_ids and package_ids & era_ids:
            from systems.cosmere.mistborn_profile import MistbornRulesProfile
            factory = MistbornRulesProfile
        else:
            factory = ReferenceRulesProfile
    else:
        factory = ReferenceRulesProfile
    return factory(binding, registry, homebrew=homebrew,
                   spoiler_scope=spoiler_scope, scope=scope)


class CompositeRulesProfile(ReferenceRulesProfile):
    """Campaign catalog plus per-actor dispatch for a mixed-world binding."""

    def _delegate(self, document):
        scope = _scope(document, self.binding)
        if scope is None:
            default = self.binding.default_world
            era = (self.binding.allowed_eras[0]
                   if default == 'scadrial' and len(self.binding.allowed_eras) == 1
                   else None)
            scope = RulesScope(default, era)
        return _profile_for_scope(self.binding, self.registry, scope,
                                  homebrew=self.homebrew,
                                  spoiler_scope=self.spoiler_scope)

    def catalog(self):
        result = super().catalog()
        result.update(automation={'build': 'actor_scoped', 'runtime': 'actor_scoped'},
                      eligible=True, blocked_reasons=[])
        return result

    def create_build(self, document, **kwargs):
        return self._delegate(document).create_build(document, **kwargs)

    def builder_context(self, build, homebrew=None):
        return self._delegate(build.to_dict() if hasattr(build, 'to_dict') else build).builder_context(
            build, homebrew)

    def level_grants(self, level, build=None):
        source = build.to_dict() if hasattr(build, 'to_dict') else build or {}
        return self._delegate(source).level_grants(level, build)

    def validate_build(self, choices, existing=None, transition=None):
        source = choices.to_dict() if hasattr(choices, 'to_dict') else choices
        return self._delegate(source).validate_build(choices, existing, transition)

    def project_actor(self, document):
        source = document.to_dict() if hasattr(document, 'to_dict') else document
        return self._delegate(source).project_actor(document)

    def normalize_runtime_state(self, state, target_id=None, *, build=None,
                                prior_build=None):
        require(build is not None, 'Mixed-world runtime normalization requires build context.')
        source = build.to_dict() if hasattr(build, 'to_dict') else build
        return self._delegate(source).normalize_runtime_state(
            state, target_id, build=build, prior_build=prior_build)

    def apply_action(self, state, action):
        return self._delegate(action).apply_action(state, action)

    def encounter_budget(self, actors, adversaries):
        rows = [row for row in (*actors, *adversaries)
                if isinstance(row, Mapping)]
        scopes = set()
        for row in rows:
            system = row.get('system', row)
            world = system.get('origin_world') or system.get('world')
            if world in (None, 'cosmere'):
                continue
            scope = _scope({'origin_world': world, 'era': system.get('era')},
                           self.binding)
            scopes.add((scope.world, scope.era))
        require(len(scopes) <= 1,
                'A mixed-world encounter must be staged for one world and era.')
        if not scopes:
            return super().encounter_budget(actors, adversaries)
        world, era = next(iter(scopes))
        return self._delegate({'origin_world': world, 'era': era}).encounter_budget(
            actors, adversaries)

    def tracker_block(self, actor):
        system = actor.get('system', actor) if isinstance(actor, Mapping) else {}
        return self._delegate(system).tracker_block(actor)


def resolve_profile(document=None, *, registry=None, homebrew=None, spoiler_scope='player'):
    registry = registry or default_registry()
    binding = normalize_binding(document, registry=registry)
    scope = _scope(document, binding)
    if scope is None:
        return CompositeRulesProfile(binding, registry, homebrew=homebrew,
                                     spoiler_scope=spoiler_scope, scope=None)
    return _profile_for_scope(binding, registry, scope, homebrew=homebrew,
                              spoiler_scope=spoiler_scope)
