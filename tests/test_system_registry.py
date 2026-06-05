"""System registry (Phase 2) — the seam that lets the platform host more than
PF2e.

Most assertions exercise the pure registry (no app import). The final two
import ``app`` to prove (a) the pf2e condition catalog the registry advertises
stays in sync with the live ``app.CONDITION_REFERENCE`` (drift guard) and
(b) the host actually binds the actor factory + dispatch seam at startup.
"""
from __future__ import annotations

import pytest

import systems
from systems.base import GameSystem, CombatProfile, Condition, SystemUI, NavLink

# A minimal valid UI (both hubs) for constructing throwaway systems in tests.
_UI = SystemUI(gm_home='/gm', player_home='/player', brand='TMP')


def test_pf2e_registered_with_expected_identity():
    pf2e = systems.get('pf2e')
    assert isinstance(pf2e, GameSystem)
    assert pf2e.key == 'pf2e'
    assert pf2e.label
    assert systems.is_registered('pf2e')
    assert 'pf2e' in [s.key for s in systems.all_systems()]


def test_get_is_case_insensitive_and_strict():
    pf2e = systems.get('pf2e')
    assert systems.get('PF2E') is pf2e
    assert systems.get('  pf2e ') is pf2e
    with pytest.raises(systems.UnknownSystemError):
        systems.get('nope')


def test_system_for_doc_defaults_to_pf2e():
    pf2e = systems.get('pf2e')
    assert systems.system_for_doc({}) is pf2e               # legacy / unstamped
    assert systems.system_for_doc(None) is pf2e             # not even a dict
    assert systems.system_for_doc({'system': 'pf2e'}) is pf2e
    assert systems.system_for_doc({'system': 'PF2E'}) is pf2e  # case-insensitive


def test_system_for_doc_is_strict_about_unknowns():
    # A stamped-but-unregistered system must raise, never silently fall back
    # to pf2e (that would mis-load a Cosmere PC as a Pathfinder one).
    with pytest.raises(systems.UnknownSystemError):
        systems.system_for_doc({'system': 'dnd5e'})
    # An unregistered default is equally strict.
    with pytest.raises(systems.UnknownSystemError):
        systems.system_for_doc({}, default='dnd5e')


def test_register_rejects_non_systems():
    with pytest.raises(TypeError):
        systems.register(object())


def test_actor_factory_dispatch_roundtrips():
    """`actor_for_doc` resolves by `system` then calls the bound factory."""
    pf2e = systems.get('pf2e')
    sentinel = object()
    seen = {}

    def fake_factory(doc, file_path=''):
        seen['doc'] = doc
        seen['fp'] = file_path
        return sentinel

    prev = pf2e._actor_factory
    try:
        pf2e.bind_actor_factory(fake_factory)
        out = systems.actor_for_doc({'system': 'pf2e', 'x': 1}, 'foo.json')
        assert out is sentinel
        assert seen == {'doc': {'system': 'pf2e', 'x': 1}, 'fp': 'foo.json'}
    finally:
        pf2e._actor_factory = prev


def test_make_actor_without_factory_raises():
    sys_ = GameSystem(key='tmp', label='Tmp', combat=systems.get('pf2e').combat, ui=_UI)
    with pytest.raises(RuntimeError):
        sys_.make_actor({})


def test_every_system_must_declare_both_hubs():
    """The GM-side / player-side invariant is STRUCTURAL: a system cannot be
    constructed (hence cannot be registered) without BOTH a gm_home and a
    player_home. This is the guard that keeps the rule true as systems are added."""
    cp = systems.get('pf2e').combat
    # missing the whole UI -> rejected
    with pytest.raises(TypeError):
        GameSystem(key='x', label='X', combat=cp)
    # a UI missing either hub -> rejected
    with pytest.raises(ValueError):
        GameSystem(key='x', label='X', combat=cp,
                   ui=SystemUI(gm_home='/gm', player_home='', brand='X'))
    with pytest.raises(ValueError):
        GameSystem(key='x', label='X', combat=cp,
                   ui=SystemUI(gm_home='', player_home='/player', brand='X'))


def test_registered_systems_expose_both_hubs():
    """Every system actually registered in the app declares both hubs + nav."""
    for key in ('pf2e', 'cosmere'):
        ui = systems.get(key).ui
        assert ui.gm_home and ui.player_home and ui.brand
        assert ui.gm_nav and ui.player_nav        # both roles get a nav


def test_combat_profile_describes_pf2e():
    cp = systems.get('pf2e').combat
    assert cp.defenses == ('ac', 'fort', 'ref', 'will')
    assert cp.action_model == 'three_action'
    assert cp.action_count == 3 and cp.reaction_count == 1
    assert cp.initiative_stat == 'perception' and cp.initiative_higher_first is True
    assert cp.damage_pool == 'hp' and cp.down_condition == 'dying'
    assert cp.stacking_rule == 'typed_best_worst'
    assert {'status', 'circumstance', 'item', 'untyped'} <= set(cp.bonus_types)
    keys = cp.condition_keys()
    assert 'frightened' in keys and cp.is_valued('frightened') is True
    assert 'prone' in keys and cp.is_valued('prone') is False


# -- host-integration guards (import app) -----------------------------------

def _norm(k):
    return k.lower().replace('_', '-')


def test_pf2e_condition_catalog_matches_app_reference():
    """Drift guard: every condition the registry advertises must exist in the
    live app's CONDITION_REFERENCE (normalizing separators)."""
    import app  # conftest puts repo root on sys.path
    ref = {_norm(k) for k in app.CONDITION_REFERENCE}
    reg = {_norm(k) for k in systems.get('pf2e').combat.condition_keys()}
    missing = reg - ref
    assert not missing, f"registry conditions absent from app.CONDITION_REFERENCE: {sorted(missing)}"


def test_app_binds_actor_factory_and_stamps_system():
    """The host wires the dispatch seam at startup, and a loaded Character
    exposes its `system` (defaulting to pf2e for an unstamped build)."""
    import app
    assert callable(getattr(app, 'make_actor', None))
    assert systems.get('pf2e')._actor_factory is not None
    # An unstamped PC build defaults to pf2e; an explicit stamp is honored.
    actor = app.make_actor({'build': {'name': 'Probe', 'level': 1}}, 'probe.json')
    assert actor.system == 'pf2e'
    stamped = app.make_actor({'system': 'PF2E', 'build': {'name': 'P2', 'level': 1}}, 'p2.json')
    assert stamped.system == 'pf2e'
