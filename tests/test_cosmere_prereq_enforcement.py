"""Cosmere builder: hard prerequisite enforcement.

A talent without its prerequisite (a predecessor talent, a skill rank, or an
attribute floor) is now an illegal build — it lands in hard_violations() and the
save route blocks it (the GM can still force-save). Narrative gates (goals,
Ideals, level) stay advisory. Verified live: taking "Feinting Strike" without
Flamestance + Intimidation 2 blocks the save.
"""
from __future__ import annotations

import app as A
import systems.cosmere.build as cb


def _warrior_talent_with_prereq():
    for t in A._cosmere_path_talents().get('warrior', []):
        if t.get('prereq') and not t.get('key'):
            return t
    return None


def test_unmet_prereq_is_a_hard_violation():
    t = _warrior_talent_with_prereq()
    assert t, 'expected a warrior talent that has a prerequisite'
    b = cb.CosmereBuild({'level': 10, 'path': 'warrior',
                         'attributes': {'str': 3, 'spd': 2, 'int': 1, 'wil': 2, 'awa': 2, 'pre': 2},
                         'talents': [{'id': t['id'], 'name': t['name']}]})
    hv = b.hard_violations()
    assert any('needs' in h for h in hv), 'unmet prereq must block the save'
    assert b.unmet_prereqs(), 'unmet_prereqs() should report it'


def test_clean_build_has_no_prereq_block():
    b = cb.CosmereBuild({'level': 3, 'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2}})
    assert b.unmet_prereqs() == []
    assert not any('needs' in h for h in b.hard_violations())


def test_save_route_comment_and_method_wired():
    src = __import__('pathlib').Path(A.__file__).read_text()
    # The save route blocks on hard_violations (which now includes prereqs).
    assert 'build.hard_violations()' in src and "'blocked': True" in src
