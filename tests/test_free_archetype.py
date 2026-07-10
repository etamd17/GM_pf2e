"""Free Archetype toggle (queue #3): standard FA variant, per-campaign.

Spec: docs/superpowers/specs/2026-07-07-free-archetype-design.md
Plan: docs/superpowers/plans/2026-07-07-free-archetype.md (Task 1)

GM Core standard Free Archetype: every EVEN level grants one extra feat
slot that may only hold archetype feats. The slot rides the same
progression machinery as everything else via a single wrapper --
`required_slots_with_variants` -- so the wizard payload, the level-up
save backstop, and the rail counts can never fork. FA OFF must be
byte-identical to the raw class_matrix tables (this arc touches the
level-up flow, the highest-risk surface).

Classification is TRAIT-based, never name-based: the pack data includes
'Unassuming Dedication', a halfling ancestry feat with no archetype
trait -- the classic trap for name matching.
"""
from __future__ import annotations

import pytest

import app as app_module
from class_matrix import get_required_slots_at_level


@pytest.fixture
def fa_on(monkeypatch):
    monkeypatch.setattr(app_module, '_load_campaign_config',
                        lambda: {'free_archetype': True})


@pytest.fixture
def fa_off(monkeypatch):
    monkeypatch.setattr(app_module, '_load_campaign_config',
                        lambda: {'free_archetype': False})


# ==========================================================================
# Toggle read helper
# ==========================================================================

def test_toggle_defaults_off(monkeypatch):
    monkeypatch.setattr(app_module, '_load_campaign_config', lambda: {})
    assert app_module._free_archetype_enabled() is False


def test_toggle_reads_config(fa_on):
    assert app_module._free_archetype_enabled() is True


def test_toggle_survives_config_error(monkeypatch):
    monkeypatch.setattr(app_module, '_load_campaign_config',
                        lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    assert app_module._free_archetype_enabled() is False


# ==========================================================================
# Slot wrapper: single source for every consumer
# ==========================================================================

def test_wrapper_off_is_byte_identical_for_all_levels(fa_off):
    for cls in ('Champion', 'Kineticist', 'Cleric', 'Druid', 'Fighter'):
        for level in range(1, 21):
            assert app_module.required_slots_with_variants(cls, level) == \
                get_required_slots_at_level(cls, level), (cls, level)


def test_wrapper_on_adds_archetype_slot_on_even_levels(fa_on):
    for level in (2, 4, 12, 20):
        slots = app_module.required_slots_with_variants('Champion', level)
        assert slots.get('archetype_feat') == 1, level
        base = get_required_slots_at_level('Champion', level)
        base['archetype_feat'] = 1
        assert slots == base, level


def test_wrapper_on_leaves_odd_levels_untouched(fa_on):
    for level in (1, 3, 11, 19):
        assert app_module.required_slots_with_variants('Champion', level) == \
            get_required_slots_at_level('Champion', level), level


def test_wrapper_does_not_mutate_matrix_tables(fa_on):
    before = dict(get_required_slots_at_level('Champion', 12))
    app_module.required_slots_with_variants('Champion', 12)
    app_module.required_slots_with_variants('Champion', 12)
    assert get_required_slots_at_level('Champion', 12) == before, \
        'wrapper leaked archetype_feat into the shared progression table'


# ==========================================================================
# Trait classification (never name-based)
# ==========================================================================

def _find_feat(name):
    for bucket in app_module.BUILDER_FEATS.values():
        if isinstance(bucket, list):
            for f in bucket:
                if isinstance(f, dict) and f.get('name') == name:
                    return f
    return None


def test_gladiator_dedication_is_archetype_and_dedication():
    f = _find_feat('Gladiator Dedication')
    assert f is not None
    assert app_module._feat_is_archetype(f) is True
    assert app_module._feat_is_dedication(f) is True


def test_unassuming_dedication_is_neither():
    """The halfling ancestry feat named '...Dedication' -- name matching
    would misclassify it; traits must decide."""
    f = _find_feat('Unassuming Dedication')
    assert f is not None
    assert app_module._feat_is_archetype(f) is False
    assert app_module._feat_is_dedication(f) is False


def test_plain_class_feat_is_not_archetype():
    f = next(f for f in app_module.BUILDER_FEATS['class']
             if isinstance(f, dict) and not app_module._feat_is_archetype(f))
    assert app_module._feat_is_dedication(f) is False


# ==========================================================================
# Level-up backstop: the FA slot is required when on, invisible when off
# ==========================================================================

def _build(feats, cls='Champion'):
    return {'class': cls, 'feats': feats, 'level_history': {},
            'proficiencies': {}}


def test_missing_progression_requires_archetype_feat_when_on(fa_on):
    missing = app_module._missing_progression_for_level(_build([]), 12)
    assert any('archetype' in m.lower() for m in missing), missing


def test_missing_progression_satisfied_by_tagged_entry(fa_on):
    feats = [['Gladiator Dedication', None, 'Archetype Feat', 12],
             ['Sudden Charge', None, 'Class Feat', 12],
             ['Canny Acumen', None, 'Skill Feat', 12]]
    missing = app_module._missing_progression_for_level(_build(feats), 12)
    assert not any('archetype' in m.lower() for m in missing), missing


def test_missing_progression_off_never_mentions_archetype(fa_off):
    missing = app_module._missing_progression_for_level(_build([]), 12)
    assert not any('archetype' in m.lower() for m in missing), missing


def test_non_archetype_feat_in_fa_slot_flagged(fa_on):
    """A client mislabeling a non-archetype feat as 'Archetype Feat' must
    surface a violation (trait check, unknown names skipped for homebrew)."""
    feats = [['Sudden Charge', None, 'Archetype Feat', 12]]
    missing = app_module._missing_progression_for_level(_build(feats), 12)
    assert any('not an archetype feat' in m.lower() for m in missing), missing


def test_homebrew_unknown_name_in_fa_slot_not_flagged_as_wrong_type(fa_on):
    feats = [['My Homebrew Wonder', None, 'Archetype Feat', 12]]
    missing = app_module._missing_progression_for_level(_build(feats), 12)
    assert not any('not an archetype feat' in m.lower() for m in missing), missing


# ==========================================================================
# Builder backstop parity
# ==========================================================================

def test_builder_validator_flags_non_archetype_in_fa_slot(fa_on):
    data = {'starting_level': 2, 'feats': [
        {'name': 'Sudden Charge', 'type': 'Archetype Feat'}]}
    violations = app_module._validate_new_character_feats(data)
    assert any('not an archetype feat' in v.lower() for v in violations), violations


def test_builder_validator_accepts_real_archetype_in_fa_slot(fa_on):
    data = {'starting_level': 2, 'feats': [
        {'name': 'Gladiator Dedication', 'type': 'Archetype Feat'}]}
    violations = app_module._validate_new_character_feats(data)
    assert not any('not an archetype feat' in v.lower() for v in violations), violations


# ==========================================================================
# Toggle setter route
# ==========================================================================

def test_toggle_route_writes_config(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, '_save_campaign_config',
                        lambda updates: saved.update(updates))
    client = app_module.app.test_client()
    r = client.post('/api/campaign/free_archetype', json={'enabled': True})
    assert r.status_code == 200, r.data
    assert r.get_json()['enabled'] is True
    assert saved == {'free_archetype': True}
    r = client.post('/api/campaign/free_archetype', json={'enabled': False})
    assert saved == {'free_archetype': False}
    assert r.get_json()['enabled'] is False


def test_toggle_route_coerces_truthiness(monkeypatch):
    saved = {}
    monkeypatch.setattr(app_module, '_save_campaign_config',
                        lambda updates: saved.update(updates))
    client = app_module.app.test_client()
    client.post('/api/campaign/free_archetype', json={'enabled': 'yes'})
    assert saved == {'free_archetype': True}
