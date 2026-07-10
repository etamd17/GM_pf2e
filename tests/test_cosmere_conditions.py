"""Cosmere valued-condition magnitude handling (readiness audit fix).

Exhausted / Afflicted / Enhanced are the _VALUED Cosmere conditions: they
carry an integer magnitude and stack cumulatively (Ch.9). The shared
condition core overwrote that magnitude with boolean True (=1) on a manual
'add', so an injury-set Exhausted -2 dropped to -1 when the GM clicked
'+ Exhausted' expecting -3. Valued conditions must increment their integer
magnitude; boolean conditions keep on/off behavior.
"""
from __future__ import annotations

import pytest

import app as app_module


class _CosMon:
    def __init__(self, name='Kaladin', conditions=None):
        self.instance_id = name + '-1'
        self.name = name
        self.is_pc = False
        self.system = 'cosmere'
        self.conditions = dict(conditions or {})


@pytest.fixture
def cos(monkeypatch):
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_bump_campaign_stat', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_combat_log', lambda *a, **k: None)
    c = _CosMon()
    app_module.ACTIVE_ENCOUNTER[:] = [c]
    yield c
    app_module.ACTIVE_ENCOUNTER[:] = []


def test_exhausted_add_increments_existing_magnitude(cos):
    cos.conditions['exhausted'] = 2          # from an injury effect
    app_module._apply_cosmere_condition_change(cos.instance_id, 'exhausted', 'add')
    assert cos.conditions['exhausted'] == 3, 'manual +Exhausted must stack, not reset to 1'


def test_exhausted_add_from_absent_is_one(cos):
    app_module._apply_cosmere_condition_change(cos.instance_id, 'exhausted', 'add')
    assert cos.conditions['exhausted'] == 1


def test_exhausted_remove_clears(cos):
    cos.conditions['exhausted'] = 3
    app_module._apply_cosmere_condition_change(cos.instance_id, 'exhausted', 'remove')
    assert 'exhausted' not in cos.conditions


def test_exhausted_toggle_off_when_present(cos):
    cos.conditions['exhausted'] = 2
    app_module._apply_cosmere_condition_change(cos.instance_id, 'exhausted', 'toggle')
    assert 'exhausted' not in cos.conditions


def test_exhausted_toggle_on_when_absent(cos):
    app_module._apply_cosmere_condition_change(cos.instance_id, 'exhausted', 'toggle')
    assert cos.conditions['exhausted'] == 1


def test_boolean_condition_still_boolean(cos):
    """Non-valued conditions (e.g. disoriented) keep True/absent semantics."""
    app_module._apply_cosmere_condition_change(cos.instance_id, 'disoriented', 'add')
    assert cos.conditions['disoriented'] is True
    app_module._apply_cosmere_condition_change(cos.instance_id, 'disoriented', 'remove')
    assert 'disoriented' not in cos.conditions


def test_enhanced_and_afflicted_are_valued(cos):
    for cond in ('enhanced', 'afflicted'):
        cos.conditions[cond] = 1
        app_module._apply_cosmere_condition_change(cos.instance_id, cond, 'add')
        assert cos.conditions[cond] == 2, cond
