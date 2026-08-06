"""Cosmere builder depth #2: real Radiant Ideal progression.

The bare 0-5 Ideals counter is replaced with rulebook-faithful progression
(Ch.5): each order's named Words, three milestones toward speaking the next
Ideal, ordered swearing, surge unlock at the First Ideal, and the Fourth Ideal
gated to level 13.

Verified live: an Edgedancer shows First Ideal sworn ("Life before death…") and
Second Ideal in progress with its oath ("I will remember those who have been
forgotten."); swearing unlocks surges; the Fourth shows "Requires level 13" at
low level.
"""
from __future__ import annotations

import os
import pathlib

import systems.cosmere.build as cb
import systems.cosmere.radiant as rad

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_ideal_text_per_order():
    assert rad.ideal_text('windrunners', 1) == rad.FIRST_IDEAL          # shared First
    assert 'protect those who cannot' in rad.ideal_text('windrunners', 2).lower()
    assert 'forgotten' in rad.ideal_text('edgedancers', 2).lower()
    assert rad.ideal_text('windrunners', 4) == ''                       # Fourth is personalized
    assert {'lightweavers', 'elsecallers'} <= set(rad.IDEAL_PERSONAL)


def test_progression_gates():
    b = cb.CosmereBuild({'radiant_order': 'windrunners', 'ideals_sworn': 1, 'ideal_progress': 2, 'level': 5})
    assert b.next_ideal() == 2 and not b.can_speak_next_ideal()         # only 2 milestones
    b.ideal_progress = 3
    assert b.can_speak_next_ideal()                                     # 3 milestones, no level gate on 2nd
    # Fourth Ideal needs level 13.
    b4 = cb.CosmereBuild({'radiant_order': 'windrunners', 'ideals_sworn': 3, 'ideal_progress': 3, 'level': 10})
    assert not b4.next_ideal_level_ok() and not b4.can_speak_next_ideal()
    b4.level = 13
    assert b4.can_speak_next_ideal()


def test_fourth_ideal_level_validation_and_roundtrip():
    b = cb.CosmereBuild({'radiant_order': 'windrunners', 'ideals_sworn': 4, 'level': 10})
    assert any('Fourth Ideal' in i for i in b.validate())
    rt = cb.CosmereBuild(b.to_dict())
    assert rt.ideals_sworn == 4
    assert cb.CosmereBuild({'ideal_progress': 3}).to_dict()['ideal_progress'] == 3


def test_ideal_states_view():
    b = cb.CosmereBuild({'radiant_order': 'edgedancers', 'ideals_sworn': 1, 'ideal_words': [rad.FIRST_IDEAL]})
    states = b.ideal_states()
    assert len(states) == 4 and states[0]['sworn'] and not states[1]['sworn']
    assert 'forgotten' in states[1]['suggested'].lower()


def test_builder_and_sheet_wiring():
    b = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()
    assert 'renderIdeals' in b and 'function speakIdeal' in b and 'RADIANT_IDEALS' in b
    assert 'f-ideal-progress' in b
    s = pathlib.Path(_REPO, 'templates', 'cosmere_sheet.html').read_text()
    assert 'ideal_states' in s
