"""Cosmere builder + leveler UI (Phase 4) — routes end-to-end.

Drives the builder routes through the Flask test client: the form renders, a
posted build is saved + rendered as a sheet, over-application (over a cap or
budget) is BLOCKED for players (the GM can override), and the leveler pre-bumps
the level. Created PCs are cleaned from the runtime store afterward.
"""
from __future__ import annotations

import os

import app
from systems.cosmere import origins as O

_VALID = {
    'name': 'Test Radiant', 'level': 1, 'path': 'warrior',
    'attributes': {'str': 2, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 3, 'pre': 0},  # 12 pts
    'skills': {'ath': 2, 'hwp': 2, 'prc': 1},                                    # 5 ranks (Athletics = starting skill)
    'talents': [O.path_key_talent('warrior')],                                   # Vigilant Stance (path key talent)
    'expertises': ['Alethi', 'Soldiering'],
}


def _client():
    return app.app.test_client()


def _cleanup(pid):
    p = app._cosmere_pc_path(pid)
    if p and os.path.isfile(p):
        os.remove(p)


def test_builder_form_renders():
    r = _client().get('/cosmere/builder')
    assert r.status_code == 200
    body = r.data.decode()
    assert 'Heroic Path' in body and 'Attributes' in body and 'Save Character' in body


def test_builder_post_saves_valid_build_and_sheet_renders():
    c = _client()
    r = c.post('/cosmere/builder', json={'id': None, 'build': _VALID})
    assert r.status_code == 200
    d = r.get_json()
    assert d['ok'] and d['url'].startswith('/cosmere/pc/')
    pid = d['id']
    try:
        assert d['issues'] == []                       # on-budget -> no warnings
        s = c.get(d['url'])
        assert s.status_code == 200
        body = s.data.decode()
        assert 'Test Radiant' in body
        assert 'Physical' in body and '15' in body     # phy defense 10+STR(2)+SPD(3)
        assert 'Warrior' in body                       # path chip on the PC sheet
        lst = c.get('/cosmere/pcs')
        assert lst.status_code == 200 and b'Test Radiant' in lst.data
    finally:
        _cleanup(pid)


def test_over_budget_build_is_blocked_gm_can_override():
    # Over-application (here: >12 points AND attrs >3 at creation) now BLOCKS the
    # save -- rules are enforced, not merely flagged. The GM may override.
    c = _client()
    bad = dict(_VALID, attributes={'str': 5, 'spd': 5, 'int': 3, 'wil': 2, 'awa': 2, 'pre': 2})  # >12, >3
    r = c.post('/cosmere/builder', json={'build': bad})
    assert r.status_code == 400
    d = r.get_json()
    assert d['blocked'] is True and any('Attributes' in h for h in d['hard'])
    # the GM overrides with force=true
    d2 = c.post('/cosmere/builder', json={'build': bad, 'force': True}).get_json()
    assert d2['ok'] is True
    _cleanup(d2['id'])


def test_leveler_prebumps_level():
    c = _client()
    pid = c.post('/cosmere/builder', json={'build': _VALID}).get_json()['id']
    try:
        up = c.get('/cosmere/builder?pc=%s&levelup=1' % pid)
        assert up.status_code == 200
        # the level stepper now shows 2
        assert 'id="f-level" type="number" min="1" max="30" value="2"' in up.data.decode()
    finally:
        _cleanup(pid)


def test_builder_preview_returns_engine_stats():
    """The walkthrough's live 'character so far' panel is fed by the engine."""
    p = _client().post('/cosmere/builder/preview', json={'build': _VALID}).get_json()
    assert p['defenses']['phy'] == 15          # 10 + STR 2 + SPD 3
    assert p['health'] == 12                    # 10 + STR 2 at L1
    assert p['budgets']['attr'] == [12, 12]     # on budget
    assert p['issues'] == []                    # _VALID is a clean build


def test_handbook_content_wired_into_builder():
    """Stage 2: the ingested handbook content feeds the builder's pickers --
    more cultures + items + heroic talents, with the path key talent still
    correctly singled out (handbook tree roots are prereq-less, so 'no
    prerequisite' is not the key-talent discriminator)."""
    cultures = app._cosmere_cultures()
    assert len(cultures) >= 13 and 'Reshi' in cultures            # handbook-only culture
    import systems.cosmere.items as it
    assert len(it.catalog()) >= 150                                # base + handbook items
    warrior = app._cosmere_path_talents()['warrior']
    assert len(warrior) >= 20                                      # enriched talent trees
    assert [t['name'] for t in warrior if t['key']] == ['Vigilant Stance']


def test_unknown_pc_sheet_is_404():
    assert _client().get('/cosmere/pc/deadbeef').status_code == 404
