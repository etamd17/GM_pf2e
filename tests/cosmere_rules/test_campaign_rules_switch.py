"""MB-07: the GM's campaign rules switch (Stormlight, Mistborn Era 1, Mistborn Era 2)."""
import json

import pytest

from systems.cosmere.rules.paths import registry_path
from systems.cosmere.rules.schema import RulesValidationError

NEEDS_PRIVATE_DATA = pytest.mark.skipif(not registry_path().is_file(),
                                        reason='private Cosmere rules data is not installed')


def _has_mistborn():
    from systems.cosmere.rules.seeds import load_seed_registry
    return 'cosmere.mistborn.core' in load_seed_registry().packs


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    import app as host
    campaign_file = tmp_path / 'campaign.json'
    pcs = tmp_path / 'cosmere_pcs'
    monkeypatch.setattr(host, 'CAMPAIGN_FILE', str(campaign_file))
    monkeypatch.setattr(host, 'COSMERE_PC_DIR', str(pcs))
    monkeypatch.setattr(host, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(host, 'ACTIVE_CAMPAIGN_ID', None)
    monkeypatch.setattr(host, 'ACTIVE_ENCOUNTER', [])
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    host._save_campaign_config({'name': 'Rules switch fixture'})
    return host, campaign_file, pcs, host.app.test_client()


def _stored(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_new_campaign_reports_stormlight_rules_and_can_change(campaign):
    host, path, pcs, client = campaign
    status = client.get('/api/cosmere/rules').get_json()
    assert status['ok'] and status['rules'] == 'stormlight'
    assert status['can_change'] is True and status['blockers'] == []


def test_unknown_choice_is_refused_without_writing(campaign):
    host, path, pcs, client = campaign
    before = path.read_bytes()
    response = client.post('/api/cosmere/rules', json={'rules': 'roshar-era-9'})
    assert response.status_code == 400
    assert path.read_bytes() == before


def test_choosing_stormlight_on_a_stormlight_campaign_writes_nothing(campaign):
    host, path, pcs, client = campaign
    before = path.read_bytes()
    assert client.post('/api/cosmere/rules', json={'rules': 'stormlight'}).get_json()['ok']
    assert path.read_bytes() == before


@pytest.mark.skipif(_has_mistborn(), reason='checks the server without Mistborn data')
def test_mistborn_without_installed_data_says_to_install_it(campaign):
    host, path, pcs, client = campaign
    before = path.read_bytes()
    response = client.post('/api/cosmere/rules', json={'rules': 'era-1'})
    assert response.status_code == 422
    assert 'Install the Mistborn rules data first' in response.get_json()['error']
    assert client.get('/api/cosmere/rules').get_json()['installed_eras'] == []
    assert path.read_bytes() == before


@pytest.mark.parametrize('blocker', ['character', 'pending character', 'fight', 'stage', 'scene'])
def test_rules_are_fixed_once_anything_records_the_pins(campaign, monkeypatch, blocker):
    host, path, pcs, client = campaign
    if blocker in ('character', 'pending character'):
        pcs.mkdir()
        (pcs / ('ab' * 16 + '.json')).write_text('{}', encoding='utf-8')
    elif blocker == 'fight':
        monkeypatch.setattr(host, 'ACTIVE_ENCOUNTER', [object()])
    else:
        target = (host._cosmere_encounter_stage_path() if blocker == 'stage'
                  else host._cosmere_narrative_scene_path())
        with open(target, 'w', encoding='utf-8') as fh:
            json.dump({'version': 1, 'entries': ['x']}, fh)
    before = path.read_bytes()
    status = client.get('/api/cosmere/rules').get_json()
    assert status['can_change'] is False and status['blockers']
    response = client.post('/api/cosmere/rules', json={'rules': 'era-2'})
    assert response.status_code == 409
    assert 'before play starts' in response.get_json()['error']
    assert path.read_bytes() == before


def test_save_campaign_config_remove_deletes_only_optional_keys(campaign):
    host, path, pcs, client = campaign
    host._save_campaign_config({'rules_binding': {'x': 1}, 'cosmere_theme': 'mistborn'})
    host._save_campaign_config({}, remove=('rules_binding',))
    stored = _stored(path)
    assert 'rules_binding' not in stored and stored['cosmere_theme'] == 'mistborn'
    assert stored['name'] == 'Rules switch fixture'
    with pytest.raises(ValueError):
        host._save_campaign_config({}, remove=('name',))


def test_hub_renders_the_rules_control(campaign, monkeypatch):
    host, path, pcs, client = campaign
    monkeypatch.setattr(host, '_active_system', lambda: 'cosmere')
    html = client.get('/cosmere/gm').get_data(as_text=True)
    assert 'aria-label="Rules"' in html and "cosRules('era-1')" in html
    assert 'Mistborn Era 2' in html


@NEEDS_PRIVATE_DATA
@pytest.mark.parametrize('era', ['era-1', 'era-2'])
def test_mistborn_choice_pins_newest_release_and_forces_the_look(campaign, era):
    from systems.cosmere.rules.profiles import (default_registry, mistborn_campaign_binding,
                                                resolve_profile)
    host, path, pcs, client = campaign
    response = client.post('/api/cosmere/rules', json={'rules': era})
    assert response.status_code == 200 and response.get_json()['rules'] == era
    stored = _stored(path)
    assert stored['cosmere_theme'] == stored['cosmere_world'] == 'mistborn'
    binding = stored['rules_binding']
    assert binding['default_world'] == 'scadrial' and binding['allowed_eras'] == [era]
    pins = {pin['id']: pin['version'] for pin in binding['enabled_packs']}
    assert set(pins) == {'cosmere.mistborn.core', f'cosmere.mistborn.{era}'}
    assert len(set(pins.values())) == 1
    newest = max((p.version for p in default_registry().pack_versions.values()
                  if p.id == 'cosmere.mistborn.core'),
                 key=lambda v: tuple(map(int, v.split('.'))))
    assert pins['cosmere.mistborn.core'] == newest
    from systems.cosmere.rules.schema import to_dict
    assert binding == to_dict(mistborn_campaign_binding(era))
    profile = resolve_profile(host._load_campaign_config())
    assert type(profile).__name__ == 'MistbornRulesProfile'
    # The look cannot drift back to Stormlight under Mistborn rules.
    refused = client.post('/api/cosmere/world', json={'world': 'stormlight'})
    assert refused.status_code == 409
    assert _stored(path)['cosmere_world'] == 'mistborn'


@NEEDS_PRIVATE_DATA
def test_switching_back_to_stormlight_removes_the_binding_not_nulls_it(campaign):
    host, path, pcs, client = campaign
    assert client.post('/api/cosmere/rules', json={'rules': 'era-1'}).status_code == 200
    assert client.post('/api/cosmere/rules', json={'rules': 'era-2'}).status_code == 200
    assert _stored(path)['rules_binding']['allowed_eras'] == ['era-2']
    assert client.post('/api/cosmere/rules', json={'rules': 'stormlight'}).status_code == 200
    stored = _stored(path)
    assert 'rules_binding' not in stored
    assert client.get('/api/cosmere/rules').get_json()['rules'] == 'stormlight'
    # The look is a free choice again.
    assert client.post('/api/cosmere/world', json={'world': 'stormlight'}).status_code == 200


def test_mistborn_binding_helper_rejects_an_unknown_era():
    from systems.cosmere.rules.profiles import mistborn_campaign_binding
    with pytest.raises(RulesValidationError):
        mistborn_campaign_binding('era-3')


def test_mistborn_binding_helper_picks_newest_complete_release():
    from types import SimpleNamespace
    from systems.cosmere.rules import profiles

    def pack(pid, version):
        return SimpleNamespace(id=pid, version=version, package_hash=('a' * 63) + version[-1])
    core = pack('cosmere.core', '1.0.0')
    versions = [pack('cosmere.mistborn.core', '1.1.0'), pack('cosmere.mistborn.era-1', '1.1.0'),
                pack('cosmere.mistborn.core', '1.10.0'), pack('cosmere.mistborn.era-1', '1.10.0'),
                # A newer core without its era package is not a complete release.
                pack('cosmere.mistborn.core', '1.11.0')]
    resolved = []
    registry = SimpleNamespace(
        packs={'cosmere.core': core},
        pack_versions={(p.id, p.version, p.package_hash): p for p in versions},
        resolve_binding=lambda binding: resolved.append(binding))
    binding = profiles.mistborn_campaign_binding('era-1', registry)
    assert {pin.version for pin in binding.enabled_packs} == {'1.10.0'}
    assert resolved == [binding]
