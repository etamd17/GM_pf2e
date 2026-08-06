"""Cosmere world-skin foundation (theming PR-A).

A Cosmere campaign picks a visual world — `stormlight` (cool storm-slate + cyan +
Cormorant Garamond, default) or `mistborn` (ash-charcoal + brushed pewter +
Playfair Display) — selectable per campaign by the GM. Pure theming: the ruleset
is unchanged. Each skin is a self-contained CSS token set layered on
`body.system-cosmere` via a `cosmere-<world>` body class.

Structural + wiring guards. Both skins were verified live in a browser on a
seeded Cosmere PC: Stormlight bg #0b0f15 / accent rgb(95,168,224) / Cormorant;
Mistborn bg #0d0d0e / accent rgb(200,205,213) / Playfair.
"""
from __future__ import annotations

import os
import pathlib

import app as A

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    return pathlib.Path(_REPO, rel).read_text()


def test_world_setting_is_a_campaign_config_key():
    assert A.CAMPAIGN_DEFAULT.get('cosmere_world') == 'stormlight'


def test_world_helper_defaults_and_clamps():
    assert A._cosmere_world() in ('stormlight', 'mistborn')


def test_world_toggle_route_exists():
    rules = [r.rule for r in A.app.url_map.iter_rules()]
    assert '/api/cosmere/world' in rules


def test_world_injected_into_template_context():
    # The context processor must expose cosmere_world so base.html can class the body.
    src = _read('app.py')
    assert "'cosmere_world': _cosmere_world()" in src


def test_base_html_sets_world_body_class_and_loads_fonts():
    h = _read('templates/base.html')
    assert 'cosmere-{{ cosmere_world }}' in h
    assert 'Cormorant+Garamond' in h and 'Playfair+Display' in h


def test_both_skins_defined_in_css():
    css = _read('static/css/system.css')
    # Stormlight (the base system-cosmere block) cools the surface + sets Cormorant.
    assert '#0b0f15' in css and "'Cormorant Garamond'" in css
    # Mistborn is its own scoped skin: pewter accent + Playfair + ash surface.
    assert 'body.system-cosmere.cosmere-mistborn' in css
    assert '#c8cdd5' in css and "'Playfair Display'" in css and '#0d0d0e' in css


def test_gm_hub_has_world_toggle():
    h = _read('templates/cosmere_gm.html')
    assert 'cosWorld(' in h and '/api/cosmere/world' in h
