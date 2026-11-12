"""SE-01: the PF2e sheet edit layer (build['sheet_edits']).

Spec: docs/superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md. The engine
keeps computing every stat; the layer only adds GM adjustments and meta
overrides, and it must survive a Pathbuilder re-import.
"""
import pytest

from core.sheet_edits import SheetEditError, merge_patch, validate_patch


# ---------------------------------------------------------------- Task 1: pure module

def test_adjust_requires_gm():
    with pytest.raises(SheetEditError) as e:
        validate_patch({'adjust': {'ac': 1}}, is_gm=False)
    assert e.value.status == 403


def test_owner_may_edit_meta():
    clean = validate_patch({'meta': {'deity': '  Ragathiel '}}, is_gm=False)
    assert clean == {'meta': {'deity': 'Ragathiel'}}


@pytest.mark.parametrize('bad', [
    {'adjust': {'ac': '2'}}, {'adjust': {'ac': 21}}, {'adjust': {'ac': -21}},
    {'adjust': {'nope': 1}}, {'adjust': {'ac': True}}, {'adjust': {'skill:': 1}},
    {'adjust': {'skill:flying': 1}}, {'adjust': []},
    {'meta': {'size': 'Colossal'}}, {'meta': {'languages': 'Common'}},
    {'meta': {'languages': ['x'] * 31}}, {'meta': {'languages': ['x' * 41]}},
    {'meta': {'deity': 'x' * 81}}, {'meta': {'deity': 3}}, {'meta': {'bogus': 'x'}},
    {'feats': {'added': []}}, {'features': {}}, {'other': 1}, {},
    [], 'str', None,
])
def test_invalid_patches_400(bad):
    with pytest.raises(SheetEditError) as e:
        validate_patch(bad, is_gm=True)
    assert e.value.status == 400


def test_skill_and_lore_keys_accepted():
    clean = validate_patch({'adjust': {'skill:Athletics': 2, 'skill:lore:warfare': -1}}, is_gm=True)
    assert clean['adjust'] == {'skill:athletics': 2, 'skill:lore:warfare': -1}


def test_merge_clears_and_keeps():
    cur = {'meta': {'deity': 'Ragathiel', 'size': 'Small'}, 'adjust': {'ac': 1}}
    out = merge_patch(cur, validate_patch({'meta': {'deity': ''}, 'adjust': {'ac': 0}}, is_gm=True))
    assert out == {'meta': {'size': 'Small'}}
    assert cur['meta']['deity'] == 'Ragathiel'   # input not mutated


def test_merge_empty_list_clears_and_list_dedupes():
    out = merge_patch({'meta': {'languages': ['Common']}},
                      validate_patch({'meta': {'languages': [], 'senses': ['Darkvision', 'darkvision', ' ']}},
                                     is_gm=False))
    assert out == {'meta': {'senses': ['Darkvision']}}


def test_merge_from_none():
    assert merge_patch(None, validate_patch({'adjust': {'hp_max': 5}}, is_gm=True)) == {'adjust': {'hp_max': 5}}
