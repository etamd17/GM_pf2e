"""Creation polish (Phase 4) — talent prerequisite enforcement (from the Foundry
data) and the Singer ancestry + forms.
"""
from __future__ import annotations

import os

import app
from systems.cosmere import origins as O, load_pack
from systems.cosmere.build import CosmereBuild
from systems.cosmere.actor import CosmereActor

_WARRIOR_KEY = O.path_key_talent('warrior')


def _cautious_advance_id():
    return next(d['_id'] for d in load_pack('heroic-paths') if d.get('name') == 'Cautious Advance')


def _base_attrs():
    return {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2}


# -- talent prerequisite enforcement (guided) -------------------------------

def test_unmet_talent_prereq_is_flagged():
    ca = _cautious_advance_id()    # needs Vigilant Stance + Discipline rank 1
    b = CosmereBuild({'level': 2, 'path': 'warrior', 'attributes': _base_attrs(),
                      'skills': {'ath': 1},
                      'talents': [_WARRIOR_KEY, {'id': ca, 'name': 'Cautious Advance'}]})
    issues = ' '.join(b.validate())
    assert 'Cautious Advance needs' in issues and 'Discipline rank 1' in issues


def test_met_talent_prereq_clears():
    ca = _cautious_advance_id()
    b = CosmereBuild({'level': 2, 'path': 'warrior', 'attributes': _base_attrs(),
                      'skills': {'ath': 1, 'dis': 1},     # Discipline rank 1 met; Vigilant Stance present
                      'talents': [_WARRIOR_KEY, {'id': ca, 'name': 'Cautious Advance'}]})
    assert not any('Cautious Advance needs' in i for i in b.validate())


# -- Singer ancestry + forms ------------------------------------------------

def test_singer_form_cascades_into_derived_stats():
    b = CosmereBuild({'level': 1, 'ancestry': 'Singer', 'path': 'warrior', 'singer_form': 'warform',
                      'attributes': _base_attrs(), 'skills': {'ath': 1},
                      'talents': [_WARRIOR_KEY, O.SINGER_CHANGE_FORM]})
    assert b.is_singer and b.eff_attributes()['str'] == 3       # STR 2 + Warform 1
    assert b.defenses()['phy'] == 15 and b.health_max() == 13 and b.deflect_value() == 1
    actor = CosmereActor(b.to_actor_doc())                       # the form flows to the rendered actor
    assert actor.defenses['phy'] == 15 and actor.health_max == 13
    assert actor.deflect['value'] == 1 and actor.skills['ath']['mod'] == 4   # rank 1 + eff STR 3


def test_nimbleform_focus_bonus():
    b = CosmereBuild({'level': 1, 'ancestry': 'Singer', 'singer_form': 'nimbleform',
                      'attributes': _base_attrs()})
    assert b.focus_max() == 6                                    # 2 + WIL 2 + Nimbleform focus 2


def test_singer_must_take_change_form():
    b = CosmereBuild({'level': 1, 'ancestry': 'Singer', 'path': 'warrior', 'singer_form': 'warform',
                      'attributes': _base_attrs(), 'skills': {'ath': 1}, 'talents': [_WARRIOR_KEY]})
    assert any('Change Form' in i for i in b.validate())


def test_forms_ignored_for_non_singers():
    b = CosmereBuild({'level': 1, 'ancestry': 'Human', 'singer_form': 'warform',
                      'attributes': _base_attrs()})
    assert b.eff_attributes()['str'] == 2 and b.deflect_value() == 0   # Human: form ignored


# -- routes -----------------------------------------------------------------

def test_builder_offers_singer_forms():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Singer Starting Form' in body
    assert 'Warform' in body and 'Nimbleform' in body


def test_singer_pc_sheet_shows_form():
    c = app.app.test_client()
    build = {'name': 'Venli', 'level': 1, 'ancestry': 'Singer', 'path': 'warrior',
             'singer_form': 'nimbleform', 'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 3, 'pre': 1},
             'skills': {'ath': 1}, 'talents': [_WARRIOR_KEY, O.SINGER_CHANGE_FORM]}
    d = c.post('/cosmere/builder', json={'build': build}).get_json()
    try:
        body = c.get(d['url']).data.decode()
        assert 'Nimbleform' in body and 'Singer' in body
    finally:
        p = app._cosmere_pc_path(d['id'])
        if p and os.path.isfile(p):
            os.remove(p)
