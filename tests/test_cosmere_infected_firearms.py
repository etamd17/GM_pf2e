"""Infected Arts homebrew system + firearms catalog (from the user's homebrew
docs). CI-safe: reads committed content packs + engine only.

Infected Arts pair a disease COST (concrete stat penalties, auto-applied) with
granted abilities (shown on the sheet). Firearms are catalog weapons that flow
into Strikes like any other weapon.
"""
import systems.cosmere.items as items
import systems.cosmere.infected as infected
import systems.cosmere.build as build


# --- firearms --------------------------------------------------------------
def test_firearms_in_catalog():
    expect = {
        'Rifle': ('hwp', '4d4', 'keen'),
        'Pistol': ('lwp', '2d4', 'keen'),
        'Soravar’s Electric Gauntlet': ('cra', '1d8', 'energy'),
        'Fire Bomb': ('cra', '2d6', 'energy'),
    }
    for name, (skill, formula, dtype) in expect.items():
        w = items.by_name(name)
        assert w is not None, name
        assert w['kind'] == 'weapon'
        assert w['damage']['skill'] == skill
        assert w['damage']['formula'] == formula
        assert w['damage']['type'] == dtype
    # The Bomb Launcher's damage is "by the bomb loaded" -> no fixed formula.
    bl = items.by_name('Soravar’s Bomb Launcher')
    assert bl and (bl['damage'] is None or not bl['damage'].get('formula'))


def test_firearm_traits_and_range():
    rifle = items.by_name('Rifle')
    assert 'Two Handed' in rifle['traits'] and 'Deadly' in rifle['traits'] and 'Pierce' in rifle['traits']
    assert rifle['range'] == {'long': 800, 'unit': 'ft', 'value': 200}
    assert 'Thrown' in items.by_name('Fire Bomb')['traits']


def test_equipped_firearm_becomes_a_strike():
    rifle = items.by_name('Rifle')
    inv = items.Inventory([{'id': rifle['id'], 'qty': 1, 'equipped': True}])
    strikes = inv.strikes()
    assert any(s['name'] == 'Rifle' and s['damage'] == '4d4' and s['skill'] == 'hwp' for s in strikes)
    assert len(inv.foundry_weapon_items()) == 1


# --- infected arts: data + resolver ----------------------------------------
def test_infected_catalog_loads():
    arts = infected.load_arts()
    assert len(arts) == 16
    # every art is well-formed
    for a in arts:
        assert a.get('id') and a.get('disease') and a.get('art')
        assert isinstance(a.get('abilities'), list) and a['abilities']


def test_resolve_adds_and_sets():
    adds, sets, recs = infected.resolve(['infected-chronic-pain'])
    assert adds.get('def:cog') == -4 and adds.get('def:spi') == -4
    assert sets.get('focus') == 0
    adds, sets, _ = infected.resolve(['infected-hypercoagulable'])
    assert sets.get('attr:str') == 9
    adds, sets, _ = infected.resolve(['infected-zombification'])
    assert adds.get('attr:str') == 2 and adds.get('attr:spd') == 2


# --- infected arts: stat calc on a build -----------------------------------
def _b(arts, **kw):
    base = dict(level=5, ancestry='Human', path='warrior',
                attributes={'str': 2, 'spd': 2, 'int': 1, 'wil': 2, 'awa': 1, 'pre': 1})
    base.update(kw)
    return build.CosmereBuild({**base, 'infected_arts': arts})


def test_chronic_pain_lowers_defenses_and_zeroes_focus():
    base, cp = _b([]), _b(['infected-chronic-pain'])
    assert cp.defenses()['cog'] == base.defenses()['cog'] - 4
    assert cp.defenses()['spi'] == base.defenses()['spi'] - 4
    assert cp.focus_max() == 0
    # the focus override is reflected in the Foundry actor doc too
    foc = cp.to_actor_doc()['system']['resources']['foc']['max']
    assert foc['useOverride'] and foc['override'] == 0


def test_hypercoagulable_sets_strength_and_boosts_physical_defense():
    h = _b(['infected-hypercoagulable'])
    assert h.eff_attributes()['str'] == 9
    assert h.defenses()['phy'] == 10 + 9 + h.eff_attributes()['spd']


def test_zombification_adds_strength_and_speed():
    base, z = _b([]), _b(['infected-zombification'])
    assert z.eff_attributes()['str'] == base.eff_attributes()['str'] + 2
    assert z.eff_attributes()['spd'] == base.eff_attributes()['spd'] + 2


def test_infected_overrides_do_not_break_the_attribute_budget():
    # A 'set' override is a DERIVED value -- the points actually SPENT are
    # unchanged, so a superhuman Infected stat never trips the save gate.
    h = _b(['infected-hypercoagulable'])
    assert h.attr_points_spent() == 9
    assert h.hard_violations() == []


def test_infected_round_trips_and_surfaces_on_sheet():
    h = _b(['infected-asthma', 'infected-zombification'])
    assert build.CosmereBuild(h.to_dict()).infected_arts == ['infected-asthma', 'infected-zombification']
    recs = h.infected_records()
    assert [r['id'] for r in recs] == ['infected-asthma', 'infected-zombification']
    assert h.to_actor_doc()['system']['infected_arts']  # stashed for the sheet
    # abilities are not duplicated into the generic action list
    actions = [i for i in h.to_actor_doc()['items'] if i.get('type') == 'action']
    assert not any('Infectious Bite' in (i.get('name') or '') for i in actions)


def test_no_infected_is_a_clean_noop():
    b = _b([])
    assert b.infected_arts == [] and b.infected_records() == []
    assert b.to_actor_doc()['system']['infected_arts'] == []
