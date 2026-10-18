"""Behavioral rules-profile dispatch, exact pins, and Stormlight compatibility."""
from dataclasses import replace

import pytest

from systems.cosmere.build import CosmereBuild
from systems.cosmere.rules.schema import RuleRecord, RulesValidationError, SourceRef, to_dict
from systems.cosmere.rules.seeds import load_seed_registry
from systems.cosmere.rules.paths import registry_path

NEEDS_PRIVATE_DATA = pytest.mark.skipif(not registry_path().is_file(),
                                        reason='private Cosmere rules data is not installed')


def binding(*packs, world='roshar', eras=()):
    registry = load_seed_registry()
    def pin(name):
        pack = registry.packs[name]
        return dict(id=pack.id, version=pack.version, package_hash=pack.package_hash)
    return dict(core_package=pin('cosmere.core'), enabled_packs=[pin(p) for p in packs],
                default_world=world, allowed_eras=list(eras))


def test_legacy_defaults_and_mistborn_theme_are_stormlight():
    plain = CosmereBuild()
    themed = CosmereBuild({'cosmere_world': 'mistborn'})
    assert plain.rules_binding.default_world == 'roshar'
    assert [p.id for p in plain.rules_binding.enabled_packs] == ['cosmere.stormlight.legacy']
    assert themed.rules_binding == plain.rules_binding
    assert themed.cosmere_theme == 'mistborn'
    assert themed.health_max() == plain.health_max()
    assert themed.to_dict()['rules_binding'] == to_dict(plain.rules_binding)
    assert CosmereBuild(themed.to_dict()).cosmere_theme == 'mistborn'


@pytest.mark.parametrize('explicit', [None, {}, 'mistborn'])
def test_explicit_invalid_binding_fails_closed(explicit):
    with pytest.raises(RulesValidationError):
        CosmereBuild({'rules_binding': explicit})


def test_pin_hash_mismatch_fails_closed():
    chosen = binding('cosmere.stormlight.legacy')
    chosen['enabled_packs'][0]['package_hash'] = '0' * 64
    with pytest.raises(RulesValidationError, match='hash mismatch'):
        CosmereBuild({'rules_binding': chosen})


@NEEDS_PRIVATE_DATA
def test_world_and_era_pins_cannot_leak_stormlight_mechanics():
    from systems.cosmere.rules.profiles import resolve_profile
    chosen = binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-1',))
    profile = resolve_profile({'rules_binding': chosen})
    catalog = profile.catalog()
    assert [p['id'] for p in catalog['packages']] == ['cosmere.core', 'cosmere.mistborn.era-1']
    assert catalog['catalogs'].get('radiant_orders', []) == []
    assert catalog['automation']['build'] == 'blocked'
    with pytest.raises(RulesValidationError, match='profile'):
        CosmereBuild({'rules_binding': chosen, 'radiant_order': 'windrunner'})
    with pytest.raises(RulesValidationError, match='world'):
        resolve_profile({'rules_binding': binding('cosmere.stormlight.legacy', world='scadrial')})
    with pytest.raises(RulesValidationError, match='era'):
        resolve_profile({'rules_binding': binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-2',))})


@NEEDS_PRIVATE_DATA
def test_catalog_filters_enabled_packs_world_era_and_spoiler():
    from systems.cosmere.rules.profiles import resolve_profile
    registry = load_seed_registry()
    def record(key, world, era, spoiler='player'):
        return RuleRecord(id='test.' + key, version='1.0.0', kind='path', name=key,
                          summary='Catalog eligibility fixture.', citations=(SourceRef('mistborn.handbook', 1, 1),),
                          worlds=(world,), eras=era, spoiler_scope=spoiler,
                          automation='reference', audit_status='provisional', parameters={})
    records = (record('eligible', 'scadrial', ('era-1',)), record('wrong-era', 'scadrial', ('era-2',)),
               record('wrong-world', 'roshar', ()), record('secret', 'scadrial', ('era-1',), 'gm'))
    packs = dict(registry.packs)
    packs['cosmere.mistborn.era-1'] = replace(packs['cosmere.mistborn.era-1'], records=records)
    packs['cosmere.mistborn.era-2'] = replace(packs['cosmere.mistborn.era-2'], records=(record('disabled', 'scadrial', ('era-1',)),))
    registry = replace(registry, packs=packs)
    chosen = binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-1',))
    result = resolve_profile({'rules_binding': chosen}, registry=registry).catalog()
    assert [r['id'] for r in result['catalogs']['path']] == ['test.eligible']
    assert result['catalogs']['path'][0]['citations'][0]['artifact_id'] == 'mistborn.handbook'


@pytest.mark.parametrize('level,health', [(1, 12), (6, 38), (11, 59), (16, 75), (21, 84)])
def test_stormlight_golden_projection(level, health):
    build = CosmereBuild({'level': level, 'path': 'warrior',
                          'attributes': dict(str=2, spd=3, int=2, wil=2, awa=3, pre=0),
                          'skills': {'ath': 2, 'hwp': 2, 'prc': 1}})
    from systems.cosmere.actor import CosmereActor
    actor = CosmereActor(build.to_actor_doc())
    assert build.defenses() == actor.defenses == dict(phy=15, cog=14, spi=13)
    assert build.health_max() == actor.health_max == health
    assert build.focus_max() == actor.focus_max == 4
    assert build.profile.tracker_block(build.to_actor_doc())['health']['max'] == health
    assert build.profile.level_grants(6)['health'] == 4
    assert not {'ac', 'fortitude', 'reflex', 'will', 'proficiency'} & set(build.profile.catalog()['catalogs'])


def test_catalog_route_authorization_and_campaign_binding(monkeypatch):
    import app as host
    monkeypatch.setattr(host, '_is_gm', lambda: False)
    assert host.app.test_client().get('/api/cosmere/rules/catalog').status_code == 403
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    monkeypatch.setattr(host, '_load_campaign_config', lambda: {'cosmere_world': 'mistborn'})
    response = host.app.test_client().get('/api/cosmere/rules/catalog')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['rules_binding']['default_world'] == 'roshar'
    assert payload['cosmere_theme'] == 'mistborn'
    assert payload['catalogs']['radiant_orders']
    assert payload['packages'][1]['id'] == 'cosmere.stormlight.legacy'
    assert payload['source_references']
    bad = binding('cosmere.stormlight.legacy')
    bad['core_package']['package_hash'] = '0' * 64
    monkeypatch.setattr(host, '_load_campaign_config', lambda: {'rules_binding': bad})
    assert host.app.test_client().get('/api/cosmere/rules/catalog').status_code == 400


def test_preview_metadata_and_bad_binding(monkeypatch):
    import app as host
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    monkeypatch.setattr(host, '_load_campaign_config', lambda: {'cosmere_world': 'mistborn'})
    client = host.app.test_client()
    response = client.post('/cosmere/builder/preview', json={'build': {'attributes': {'str': 2}}})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['health'] == 12
    assert payload['packages'][1]['id'] == 'cosmere.stormlight.legacy'
    assert payload['rules_binding']['default_world'] == 'roshar'
    assert 'eligible' in payload and 'blocked_reasons' in payload
    assert client.post('/cosmere/builder/preview', json={'build': {'rules_binding': {}}}).status_code == 400
    # Campaign pins are authoritative even if the request asks for another profile.
    bad = binding('cosmere.stormlight.legacy')
    bad['enabled_packs'][0]['package_hash'] = '0' * 64
    monkeypatch.setattr(host, '_load_campaign_config', lambda: {'rules_binding': bad})
    assert client.post('/cosmere/builder/preview', json={'build': {'rules_binding': binding('cosmere.stormlight.legacy')}}).status_code == 400


@NEEDS_PRIVATE_DATA
def test_reference_only_preview_reports_package_identity_without_derived_mechanics(monkeypatch):
    import app as host
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    chosen = binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-1',))
    monkeypatch.setattr(host, '_load_campaign_config', lambda: {'rules_binding': chosen})
    response = host.app.test_client().post('/cosmere/builder/preview', json={'build': {'name': 'Vin'}})
    payload = response.get_json()
    assert payload['rules_binding'] == chosen
    assert payload['packages'][1]['id'] == 'cosmere.mistborn.era-1'
    assert payload['eligible'] is False and payload['blocked_reasons']
    assert 'health' not in payload and 'is_radiant' not in payload


def test_actor_roundtrip_preserves_pins_and_theme_and_rejects_bad_system_pin():
    hero = CosmereBuild({'cosmere_theme': 'mistborn', 'cosmere_world': 'stormlight', 'level': 6})
    doc = hero.to_actor_doc()
    loaded = CosmereBuild.from_actor_doc(doc)
    assert loaded.rules_binding == hero.rules_binding
    assert loaded.cosmere_theme == 'mistborn'
    loaded.level += 1
    assert loaded.level == 7 and loaded._implementation.level == 7
    doc['system']['rules_binding']['core_package']['package_hash'] = '0' * 64
    with pytest.raises(RulesValidationError, match='hash mismatch'):
        CosmereBuild.from_actor_doc(doc)


def test_builder_context_world_catalogs_are_owned_by_profile():
    hero = CosmereBuild()
    context = hero.profile.builder_context(hero, {})
    assert context['paths'] == ['agent', 'envoy', 'hunter', 'leader', 'scholar', 'warrior']
    assert 'windrunners' in context['radiant_orders']
    assert context['catalog']
    assert context['build']['rules_binding'] == to_dict(hero.rules_binding)


@NEEDS_PRIVATE_DATA
def test_binding_normalizes_equivalent_pack_and_era_order():
    from systems.cosmere.rules.profiles import normalize_binding
    chosen = binding('cosmere.mistborn.era-2', 'cosmere.mistborn.era-1',
                     world='scadrial', eras=('era-2', 'era-1'))
    normalized = normalize_binding({'rules_binding': chosen})
    assert [pin.id for pin in normalized.enabled_packs] == ['cosmere.mistborn.era-1', 'cosmere.mistborn.era-2']
    assert normalized.allowed_eras == ('era-1', 'era-2')


def test_theme_route_writes_canonical_cosmetic_field_only(monkeypatch):
    import app as host
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    updates = []
    monkeypatch.setattr(host, '_save_campaign_config', lambda update: updates.append(update))
    response = host.app.test_client().post('/api/cosmere/world', json={'world': 'mistborn'})
    assert response.status_code == 200
    assert updates == [{'cosmere_theme': 'mistborn', 'cosmere_world': 'mistborn'}]
    assert 'rules_binding' not in updates[0]


def test_template_theme_prefers_canonical_field(monkeypatch):
    import app as host
    monkeypatch.setattr(host, '_account_mode', lambda: False)
    monkeypatch.setattr(host, 'CAMPAIGN_DEFAULT', {'cosmere_theme': 'mistborn', 'cosmere_world': 'stormlight'})
    with host.app.test_request_context('/'):
        assert host._inject_account_ctx()['cosmere_world'] == 'mistborn'


def test_stormlight_profile_damage_delegates_existing_engine_without_mutating_state():
    profile = CosmereBuild().profile
    state = {'health': 12, 'deflect': 2, 'conditions': []}
    applied = profile.apply_action(state, {'type': 'damage', 'amount': 5, 'damage_type': 'keen'})
    assert applied['health'] == 9
    assert state == {'health': 12, 'deflect': 2, 'conditions': []}
    applied['conditions']['prone'] = True
    assert state['conditions'] == []
    assert profile.apply_action(state, {'type': 'damage', 'amount': 5, 'damage_type': 'spirit'})['health'] == 7
    assert profile.encounter_budget([], [])['automation'] == 'gm_adjudicated'
    with pytest.raises(RulesValidationError):
        profile.apply_action(state, {'type': 'pf2e-strike'})


def test_legacy_actor_reader_accepts_non_document_input():
    # The historical from_actor_doc helper treated non-document input as empty.
    assert CosmereBuild.from_actor_doc(['unrecognized']).name == 'New Hero'


def test_profile_catalog_is_detached_from_shared_stormlight_items():
    profile = CosmereBuild().profile
    first = profile.catalog()['catalogs']['items'][0]
    original_name = first['name']
    first['name'] = 'Changed in a caller-owned catalog'
    assert profile.catalog()['catalogs']['items'][0]['name'] == original_name
