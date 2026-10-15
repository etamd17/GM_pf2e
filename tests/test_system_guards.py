"""Shared PF2e routes must not render PF2e content for another system reached
by a bookmarked/typed URL: Cosmere tables use their equivalent when one exists,
and D&D tables return to their GM hub. The PF2e player nav's Combat tab points
at the phone-optimized /mobile view, not the heavy GM /tracker page.
"""
from __future__ import annotations

import pathlib
from urllib.parse import urlsplit

import app


def _allow_test_gm(monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: True)


def test_shared_pf2e_routes_redirect_in_cosmere_mode(monkeypatch):
    _allow_test_gm(monkeypatch)
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    client = app.app.test_client()
    for path, dest in [('/gmscreen', '/cosmere/gmscreen'),
                       ('/generator', '/cosmere/generator'),
                       ('/gm/loot', '/cosmere/loot'),
                       ('/player', '/cosmere/player')]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302), f"{path} did not redirect"
        assert urlsplit(r.headers.get('Location', '')).path == dest, (
            f"{path} -> {r.headers.get('Location')}"
        )


def test_pf2e_only_tools_redirect_in_dnd5e_mode(monkeypatch):
    _allow_test_gm(monkeypatch)
    monkeypatch.setattr(app, '_active_system', lambda: 'dnd5e')
    client = app.app.test_client()
    for path in ('/generator', '/gm/loot'):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302), f"{path} did not redirect"
        assert r.headers.get('Location', '').endswith('/dnd5e/gm'), (
            f"{path} -> {r.headers.get('Location')}"
        )


def test_shared_routes_unchanged_in_pf2e_mode(monkeypatch):
    _allow_test_gm(monkeypatch)
    monkeypatch.setattr(app, '_active_system', lambda: 'pf2e')

    class _Generator:
        def __getattr__(self, name):
            if name.startswith('get_'):
                return lambda level, biome: f'{name}:{level}:{biome}'
            raise AttributeError(name)

    # This test owns only the route/system contract. Keep unrelated random
    # generator content from making the page-render assertion intermittent.
    monkeypatch.setattr(app, 'pf2e_gen', _Generator())
    client = app.app.test_client()
    # PF2e mode still serves the PF2e screens (no redirect to /cosmere/*)
    for path in ('/gmscreen', '/generator', '/gm/loot'):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} returned {r.status_code}"
        assert not r.headers.get('Location'), f"{path} -> {r.headers.get('Location')}"


def test_pf2e_generator_and_loot_apis_refuse_other_systems_without_mutation(monkeypatch):
    _allow_test_gm(monkeypatch)
    mutations = []
    monkeypatch.setattr(app, '_mutate_loot_ledger', lambda fn: mutations.append(('ledger', fn)))
    monkeypatch.setattr(app, '_save_pinned_generators', lambda pins: mutations.append(('pins', pins)))
    monkeypatch.setattr(
        app,
        '_deposit_loot_to_pc',
        lambda *args, **kwargs: mutations.append(('deposit', args, kwargs)),
    )

    class _FailIfGenerated:
        def __getattr__(self, name):
            raise AssertionError(f"PF2e generator called in another system: {name}")

    monkeypatch.setattr(app, 'pf2e_gen', _FailIfGenerated())
    requests = [
        ('get', '/api/loot_ledger', None),
        ('post', '/api/loot_ledger/add', {'recipient': 'Party', 'coins': {'gp': 1}}),
        ('post', '/api/loot_ledger/delete', {'id': 'entry-1'}),
        ('post', '/api/loot_ledger/entry-1/award', {}),
        ('post', '/api/send_loot', {'target': 'Alyx', 'coins': {'gp': 1}}),
        ('post', '/api/generate/npc', {'level': 1, 'biome': 'City'}),
        ('get', '/api/generator/pins', None),
        ('post', '/api/generator/pin', {'type': 'npc', 'content': 'result'}),
        ('delete', '/api/generator/pin/pin-1', None),
    ]

    client = app.app.test_client()
    for system in ('cosmere', 'dnd5e'):
        monkeypatch.setattr(app, '_active_system', lambda system=system: system)
        for method, path, payload in requests:
            response = getattr(client, method)(path, json=payload)
            assert response.status_code == 400, f"{system} {method.upper()} {path}"
            assert response.get_json().get('error') == 'pf2e only'

    assert mutations == []


def test_pf2e_generator_and_loot_apis_still_work_in_pf2e(monkeypatch):
    _allow_test_gm(monkeypatch)
    monkeypatch.setattr(app, '_active_system', lambda: 'pf2e')
    monkeypatch.setattr(app, '_load_loot_ledger', lambda: {'entries': []})
    monkeypatch.setattr(app, '_load_pinned_generators', lambda: [])
    monkeypatch.setattr(app, 'PARTY_LIBRARY', {})

    class _Generator:
        _wealth_by_level = {1: 175}

        @staticmethod
        def get_npc(level, biome):
            return f'{level}:{biome}'

    monkeypatch.setattr(app, 'pf2e_gen', _Generator())
    client = app.app.test_client()

    ledger = client.get('/api/loot_ledger')
    assert ledger.status_code == 200
    assert ledger.get_json() == {
        'entries': [],
        'party_wealth': [],
        'expected_wealth_per_pc': 175,
        'party_level': 1,
    }
    generated = client.post('/api/generate/npc', json={'level': 3, 'biome': 'Forest'})
    assert generated.status_code == 200
    assert generated.get_json() == {'html': '3:Forest'}
    pins = client.get('/api/generator/pins')
    assert pins.status_code == 200
    assert pins.get_json() == {'pins': []}


def test_player_nav_combat_links_to_mobile():
    html = pathlib.Path('templates/_player_nav.html').read_text(encoding='utf-8')
    assert 'href="/mobile"' in html
    assert 'href="/tracker"' not in html
