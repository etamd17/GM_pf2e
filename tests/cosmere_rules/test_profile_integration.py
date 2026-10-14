"""Persisted campaign authority and complete catalog audience/provenance contracts."""
import json

import pytest

from systems.cosmere.rules.profiles import resolve_profile
from systems.cosmere.rules.schema import SPOILER_RANK
from systems.cosmere.rules.seeds import load_seed_registry
from systems.cosmere.rules.paths import registry_path

NEEDS_PRIVATE_DATA = pytest.mark.skipif(not registry_path().is_file(),
                                        reason='private Cosmere rules data is not installed')


def pinned_binding(*packs, world='roshar', eras=()):
    registry = load_seed_registry()
    def pin(name):
        package = registry.packs[name]
        return {'id': name, 'version': package.version, 'package_hash': package.package_hash}
    return {'core_package': pin('cosmere.core'), 'enabled_packs': [pin(p) for p in packs],
            'default_world': world, 'allowed_eras': list(eras)}


@pytest.fixture
def persisted_campaign(tmp_path, monkeypatch):
    import app as host
    campaign_file = tmp_path / 'campaign.json'
    monkeypatch.setattr(host, 'CAMPAIGN_FILE', str(campaign_file))
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    # Exercise the real load/save whitelist and atomic JSON writer.
    host._save_campaign_config({'name': 'Profile integration fixture'})
    return host, campaign_file, host.app.test_client()


@NEEDS_PRIVATE_DATA
def test_saved_mistborn_binding_and_canonical_theme_survive_real_persistence(persisted_campaign):
    host, path, client = persisted_campaign
    chosen = pinned_binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-1',))
    host._save_campaign_config({'rules_binding': chosen, 'cosmere_theme': 'mistborn'})
    stored = json.loads(path.read_text(encoding='utf-8'))
    assert stored['rules_binding'] == chosen
    assert stored['cosmere_theme'] == 'mistborn'
    assert host._load_campaign_config()['rules_binding'] == chosen
    assert host._load_campaign_config_at(str(path))['cosmere_theme'] == 'mistborn'
    catalog = client.get('/api/cosmere/rules/catalog').get_json()
    assert catalog['rules_binding'] == chosen
    assert catalog['cosmere_theme'] == 'mistborn'
    preview = client.post('/cosmere/builder/preview', json={'build': {'name': 'Vin'}}).get_json()
    assert preview['rules_binding'] == catalog['rules_binding']
    assert preview['eligible'] is False


@pytest.mark.parametrize('explicit', [None, {}, 'unknown'])
def test_persisted_invalid_explicit_binding_is_not_dropped(persisted_campaign, explicit):
    host, path, client = persisted_campaign
    host._save_campaign_config({'rules_binding': explicit})
    assert 'rules_binding' in host._load_campaign_config()
    assert host._load_campaign_config()['rules_binding'] == explicit
    assert json.loads(path.read_text(encoding='utf-8'))['rules_binding'] == explicit
    assert client.get('/api/cosmere/rules/catalog').status_code == 400
    assert client.post('/cosmere/builder/preview', json={'build': {'name': 'Bad pins'}}).status_code == 400
    assert client.post('/cosmere/builder', json={'build': {'name': 'Bad pins'}, 'force': True}).status_code == 400


def test_saved_hash_mismatch_fails_closed_through_real_config(persisted_campaign):
    host, path, client = persisted_campaign
    chosen = pinned_binding('cosmere.stormlight.legacy')
    chosen['enabled_packs'][0]['package_hash'] = '0' * 64
    host._save_campaign_config({'rules_binding': chosen})
    assert host._load_campaign_config()['rules_binding'] == chosen
    assert client.get('/api/cosmere/rules/catalog').status_code == 400
    assert client.post('/cosmere/builder/preview', json={'build': {}}).status_code == 400


def test_theme_endpoint_persists_canonical_field_without_creating_binding(persisted_campaign):
    host, path, client = persisted_campaign
    assert 'rules_binding' not in host._load_campaign_config()
    assert 'cosmere_theme' not in host._load_campaign_config()
    assert client.post('/api/cosmere/world', json={'world': 'mistborn'}).status_code == 200
    loaded = host._load_campaign_config()
    assert loaded['cosmere_theme'] == 'mistborn'
    assert 'rules_binding' not in loaded
    assert json.loads(path.read_text(encoding='utf-8'))['cosmere_theme'] == 'mistborn'
    assert client.get('/api/cosmere/rules/catalog').get_json()['rules_binding']['default_world'] == 'roshar'


@NEEDS_PRIVATE_DATA
def test_unbound_campaign_legacy_default_cannot_be_overridden_by_submitted_pin(persisted_campaign):
    host, path, client = persisted_campaign
    assert 'rules_binding' not in host._load_campaign_config()
    foreign = pinned_binding('cosmere.mistborn.era-1', world='scadrial', eras=('era-1',))
    authoritative = client.get('/api/cosmere/rules/catalog').get_json()['rules_binding']
    assert authoritative['default_world'] == 'roshar'
    for route in ('/cosmere/builder/preview', '/cosmere/builder'):
        response = client.post(route, json={'build': {'name': 'Override', 'rules_binding': foreign}, 'force': True})
        assert response.status_code == 400
        assert 'match the campaign' in response.get_json()['error']
    legacy = client.post('/cosmere/builder/preview', json={'build': {'rules_binding': authoritative}})
    assert legacy.status_code == 200
    assert legacy.get_json()['rules_binding'] == authoritative
    assert 'rules_binding' not in json.loads(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('audience', ['none', 'player', 'gm'])
def test_catalog_audience_filters_legacy_entries_and_source_metadata(audience):
    result = resolve_profile(spoiler_scope=audience).catalog()
    for source in result['source_references']:
        assert SPOILER_RANK[source['spoiler_scope']] <= SPOILER_RANK[audience]
    source_ids = {source['product_id'] for source in result['source_references']}
    if audience == 'none':
        assert all(not entries for entries in result['catalogs'].values())
        assert source_ids == set()
    else:
        assert result['catalogs']['skills']
        assert result['catalogs']['paths']
        assert result['catalogs']['resources']
        assert result['catalogs']['items']
        assert 'stormlight.handbook' in source_ids
        assert ('stormlight.world-guide' in source_ids) == (audience == 'gm')
        for entries in result['catalogs'].values():
            entries = entries.values() if isinstance(entries, dict) else entries
            for entry in entries:
                assert isinstance(entry, dict)
                assert entry['worlds'] == ['roshar'] and entry['eras'] == []
                assert SPOILER_RANK[entry['spoiler_scope']] <= SPOILER_RANK[audience]
                assert entry['audit_status'] == 'provisional'
                assert entry['automation'] in {'reference', 'calculated', 'tracked', 'gm_adjudicated'}
                assert entry['citation_state'] == 'unverified'
                assert entry['citations'] == []
                provenance = entry['provenance']
                assert provenance['status'] == 'provisional'
                assert provenance['page_status'] == 'unverified'
                assert provenance['verification_required'] is True
                assert provenance['source_candidates']
                assert set(provenance['source_candidates']) <= source_ids
