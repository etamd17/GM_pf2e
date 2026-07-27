"""Guards for the Cosmere severity read (which condition chips render ruby).

The severity list used to be hand-copied into four places -- two Jinja `set`
blocks, two JS `new Set([...])` literals -- plus a Python set in app.py. Nothing
kept them aligned, so changing a condition's severity would have shown ruby on
some screens and calm on others, and a GM scanning the vitals board would have
been told the wrong thing about their party.

These tests pin the single source of truth (systems.cosmere.SEVERE_CONDITIONS,
injected into templates as `cos_severe`) and fail if a new hardcoded copy
reappears.
"""
import re
from pathlib import Path

import pytest

import systems.cosmere as cosmere

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / 'templates'

# Every Cosmere surface that tints a condition chip by severity.
SEVERITY_TEMPLATES = [
    'cosmere_player.html',
    'cosmere_combat.html',
    'cosmere_gm_vitals.html',
]


def test_severe_conditions_are_real_conditions():
    """A typo'd or renamed entry would silently never match, so the condition
    would quietly stop reading as severe."""
    unknown = sorted(cosmere.SEVERE_CONDITIONS - set(cosmere.CONDITION_INFO))
    assert not unknown, (
        f"SEVERE_CONDITIONS contains entries that are not real conditions: {unknown}. "
        f"Valid keys: {sorted(cosmere.CONDITION_INFO)}"
    )


def test_severe_conditions_are_lowercase():
    """Every consumer lowercases the incoming condition key before the lookup."""
    bad = sorted(c for c in cosmere.SEVERE_CONDITIONS if c != c.lower())
    assert not bad, f"SEVERE_CONDITIONS entries must be lowercase: {bad}"


def test_buffs_are_not_marked_severe():
    """Buffs must stay on the calm accent -- flagging them ruby would cry wolf."""
    buffs = {'determined', 'empowered', 'enhanced', 'focused'}
    wrong = sorted(buffs & cosmere.SEVERE_CONDITIONS)
    assert not wrong, f"These are buffs and must not read as debilitating: {wrong}"


def test_app_reuses_the_canonical_set():
    """app.py must not keep its own copy."""
    import app
    assert app._COSMERE_SEVERE_CONDS is cosmere.SEVERE_CONDITIONS, (
        "app._COSMERE_SEVERE_CONDS should BE systems.cosmere.SEVERE_CONDITIONS, "
        "not a separate set that can drift."
    )


@pytest.mark.parametrize('name', SEVERITY_TEMPLATES)
def test_templates_have_no_hardcoded_severity_list(name):
    """The regression this whole module exists to prevent: a fresh hand-copied
    list appearing in a template instead of using the injected `cos_severe`."""
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    # A literal list/set naming two or more known severe conditions in quotes.
    quoted = re.findall(r"""['"]([a-z]+)['"]""", text)
    hits = [q for q in quoted if q in cosmere.SEVERE_CONDITIONS]
    # 'exhausted' etc. may legitimately appear alone (e.g. a single lookup), but
    # a cluster of them is a copied severity list.
    assert len(set(hits)) < 3, (
        f"{name} looks like it hardcodes the severity list ({sorted(set(hits))}). "
        f"Use the injected `cos_severe` (systems.cosmere.SEVERE_CONDITIONS) instead."
    )


@pytest.mark.parametrize('name', SEVERITY_TEMPLATES)
def test_templates_consume_the_injected_list(name):
    """Each severity surface must actually read `cos_severe`."""
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    assert 'cos_severe' in text, (
        f"{name} tints conditions by severity but never reads `cos_severe`."
    )


def test_context_processor_exposes_cos_severe():
    """The templates above are useless if the injection stops happening."""
    import app
    with app.app.test_request_context('/'):
        ctx = app._inject_cosmere_conditions()
    assert 'cos_severe' in ctx
    assert set(ctx['cos_severe']) == set(cosmere.SEVERE_CONDITIONS)
    # Sorted so the rendered JSON/Jinja output is stable between requests.
    assert ctx['cos_severe'] == sorted(ctx['cos_severe'])
