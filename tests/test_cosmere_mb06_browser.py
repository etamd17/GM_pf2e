"""World-specific reference chrome; no mechanical data or saved state changes."""
import pytest
import app


@pytest.mark.parametrize('world', ['mistborn', 'stormlight'])
def test_reference_screen_only_shows_world_appropriate_sections(monkeypatch, world):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_cosmere_world', lambda: world)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_world', world)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_theme', world)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    with app.app.test_request_context('/cosmere/gmscreen'):
        html = app.cosmere_gmscreen.__wrapped__()
    assert 'id="damage"' in html and 'id="conditions"' in html
    if world == 'mistborn':
        for text in ['id="surges"', 'id="orders"', 'href="#surges"',
                     'href="#orders"', '0 until Radiant', 'infused spheres',
                     'Stormlight rules at a glance', 'Surge skills unlock']:
            assert text not in html
        assert 'Cosmere rules at a glance' in html
        assert 'consult the character sheet for Metallic Arts' in html
    else:
        for text in ['id="surges"', 'id="orders"', 'href="#surges"',
                     'href="#orders"', '0 until Radiant', 'infused spheres',
                     'Stormlight rules at a glance', 'Surge skills unlock']:
            assert text in html


@pytest.mark.parametrize('world', ['mistborn', 'stormlight'])
def test_homebrew_examples_follow_world(monkeypatch, world):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_cosmere_world', lambda: world)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_world', world)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_theme', world)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_cosmere_homebrew_store', lambda: {})
    with app.app.test_request_context('/cosmere/homebrew'):
        html = app.cosmere_homebrew.__wrapped__()
    if world == 'mistborn':
        assert 'placeholder="e.g. Steady Nerves"' in html
        assert 'placeholder="Terris&#10;Military"' in html
        assert 'placeholder="Alethi' not in html
        assert 'placeholder="e.g. Stoneward' not in html
    else:
        assert 'placeholder="e.g. Stoneward' in html
        assert 'placeholder="Alethi&#10;Military"' in html

@pytest.mark.parametrize('world', ['mistborn', 'stormlight'])
def test_hub_tile_wording_follows_world(monkeypatch, world):
    from flask import render_template
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_world', world)
    monkeypatch.setitem(app.CAMPAIGN_DEFAULT, 'cosmere_theme', world)
    with app.app.test_request_context('/cosmere/gm'):
        html = render_template('cosmere_gm.html', char_count=2, adv_count=29,
                               encounter_count=0, party=[], ready_count=0, ready_total=2,
                               rules_status={'rules': 'stormlight', 'installed_eras': [],
                                             'can_change': True, 'blockers': []})
    if world == 'mistborn':
        assert 'Mistborn Command &amp; Tools' in html
        assert 'Boxings, clips &amp; spoils' in html
        assert 'Scadrial names' in html
        assert 'Radiant Ideals &amp; surges' not in html
        assert 'The Stormlight adversaries' not in html
    else:
        assert 'Stormlight Command &amp; Tools' in html
        assert 'Spheres &amp; spoils' in html
        assert 'Rosharan names' in html
        assert 'Radiant Ideals &amp; surges' in html
        assert 'The Stormlight adversaries' in html
