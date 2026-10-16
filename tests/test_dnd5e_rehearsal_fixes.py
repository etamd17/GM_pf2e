"""Bugs found in the 2026-10-15 rehearsal (a D&D Beyond Paladin 9 / Warlock 1
plus a sweep of every class and subclass to level 20)."""
import uuid

import pytest

from core import storage
from systems.dnd5e import content, service, store
from systems.dnd5e.rules.character import compute

pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')

ABIL = ('str', 'dex', 'con', 'int', 'wis', 'cha')


def _doc(levels, choices=None, edition='one'):
    return {'name': 'R', 'state': {}, 'build': {
        'edition': edition, 'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
        'levels': levels, 'choices': choices or {}, 'sources': []}}


def _paladin_warlock(invocation):
    return _doc([{'class': 'paladin|xphb'}] * 9 + [{'class': 'warlock|xphb'}],
                {'opt:warlock|xphb:Eldritch Invocations': [invocation]})


def test_invocation_class_level_prerequisites_are_checked():
    # Agonizing Blast needs Warlock 2 and Ascendant Step Warlock 5: character
    # level 10 is not enough.
    for inv in ('agonizing blast|xphb', 'ascendant step|xphb'):
        s = compute(_paladin_warlock(inv))
        assert any('prerequisite not met' in w and w.split(':')[0].lower() == inv.split('|')[0]
                   for w in s['warnings']), (inv, s['warnings'])
    s = compute(_paladin_warlock('armor of shadows|xphb'))
    assert not [w for w in s['warnings'] if 'Armor of Shadows' in w]


def test_invocation_needing_another_invocation():
    # Thirsting Blade (2024) needs Warlock 5 and Pact of the Blade.
    levels = [{'class': 'warlock|xphb'}] * 5
    alone = compute(_doc(levels, {'opt:warlock|xphb:Eldritch Invocations': ['thirsting blade|xphb']}))
    assert any(w.startswith('Thirsting Blade: prerequisite') for w in alone['warnings'])
    paired = compute(_doc(levels, {'opt:warlock|xphb:Eldritch Invocations':
                                   ['pact of the blade|xphb', 'thirsting blade|xphb']}))
    assert not [w for w in paired['warnings'] if w.startswith('Thirsting Blade')]


def test_spell_choice_filters_respect_school_and_class_lists():
    from systems.dnd5e.rules.spells import spells_for_filter
    evocation = spells_for_filter('level=0;1;2|class=Wizard|school=V', 'one')
    assert evocation and {r['school'] for r in evocation} == {'V'}
    assert 'Fire Bolt' in {r['name'] for r in evocation}
    assert 'Blade Ward' not in {r['name'] for r in evocation}
    lore = {r['name'] for r in spells_for_filter('level=0;1;2;3|class=Cleric;Druid;Wizard', 'one')}
    assert {'Guidance', 'Shillelagh', 'Fire Bolt'} <= lore     # one from each list


def test_engine_rejects_a_spell_outside_the_filter():
    levels = [{'class': 'wizard|xphb'}] * 3
    base = {'subclass:wizard|xphb': ['evoker|xphb|wizard|xphb']}
    s = compute(_doc(levels, base))
    savant = next(c for c in s['choices'] if c['label'].startswith('Evoker: choose a spell'))
    good = compute(_doc(levels, dict(base, **{savant['id']: ['fire bolt|xphb']})))
    assert good['errors'] == []
    bad = compute(_doc(levels, dict(base, **{savant['id']: ['blade ward|xphb']})))
    assert any('Blade Ward is not a valid choice' in e for e in bad['errors'])


def test_free_once_per_long_rest_casts_are_tracked():
    s = compute(_doc([{'class': 'paladin|xphb'}] * 5,
                     {'subclass:paladin|xphb': ['oath of vengeance|xphb|paladin|xphb']}))
    res = {r['name']: (r['max'], r['recharge']) for r in s['resources']}
    assert res.get("Paladin's Smite") == (1, 'long')
    assert res.get('Faithful Steed') == (1, 'long')


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    store.forget_cache()
    yield uuid.uuid4().hex
    store.forget_cache()


def test_level_down_trims_picks_over_the_lower_limit(cid):
    doc = store.new_pc(cid, name='Mastery', edition='one')
    doc['build'].update({'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                         'levels': [{'class': 'fighter|xphb', 'hp': None}] * 4})
    s = compute(doc)
    mastery = next(c for c in s['choices'] if c['kind'] == 'weapon_mastery')
    assert mastery['count'] == 4                       # 2024 Fighter: 3 masteries, 4 at level 4
    doc['build']['choices'][mastery['id']] = ['longsword', 'greatsword', 'shortbow', 'spear']
    store.save_pc(cid, doc)
    doc2, s2 = service.level_down(cid, doc['id'])
    assert s2['errors'] == [], s2['errors']
    assert doc2['build']['choices'][mastery['id']] == ['longsword', 'greatsword', 'shortbow']


def test_a_feature_free_cast_is_not_counted_twice():
    s = compute(_doc([{'class': 'warlock|xphb'}] * 10,
                     {'subclass:warlock|xphb': ['the fathomless|tce|warlock|xphb']}))
    names = [r['name'] for r in s['resources']]
    assert "Evard's Black Tentacles (free cast)" in names
    assert 'Grasping Tentacles' not in names


def test_2024_mystic_arcanum_is_one_use_per_spell_level():
    s = compute(_doc([{'class': 'warlock|xphb'}] * 17))
    arc = sorted(r['name'] for r in s['resources'] if r['name'].startswith('Mystic Arcanum'))
    assert arc == [f'Mystic Arcanum (level {n})' for n in (6, 7, 8, 9)]
    s = compute(_doc([{'class': 'warlock|xphb'}] * 12))
    assert [r['name'] for r in s['resources'] if r['name'].startswith('Mystic Arcanum')] == \
        ['Mystic Arcanum (level 6)']


def test_level_down_drops_spells_the_lower_level_cannot_know(cid):
    doc = store.new_pc(cid, name='Book', edition='one')
    doc['build'].update({'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                         'levels': [{'class': 'wizard|xphb', 'hp': None}] * 5})
    book = 'spells:wizard|xphb:known'
    doc['build']['choices'][book] = ['magic missile|xphb', 'shield|xphb', 'misty step|xphb', 'fireball|xphb']
    assert compute(doc)['errors'] == []
    store.save_pc(cid, doc)
    doc2, s2 = service.level_down(cid, doc['id'])
    assert s2['level'] == 4 and s2['errors'] == [], s2['errors']
    assert doc2['build']['choices'][book] == ['magic missile|xphb', 'shield|xphb', 'misty step|xphb']


# --- casting marks off the right thing (slot, free use, or nothing) ----------

def _rows(doc):
    from systems.dnd5e.routes import _sheet_spells
    s = compute(doc)
    assert s['errors'] == [], s['errors']
    return s, {(r['name'], r.get('caster')): r for r in _sheet_spells(doc, s)}


def test_mystic_arcanum_is_chosen_and_cast_from_its_own_use():
    doc = _doc([{'class': 'warlock|xphb'}] * 11, {'spells:warlock|xphb:arcanum:6': ['mass suggestion|xphb']})
    s, rows = _rows(doc)
    choice = next(c for c in s['choices'] if c['id'] == 'spells:warlock|xphb:arcanum:6')
    assert choice['count'] == 1 and choice['query']['levels'] == [6]
    assert not [c for c in s['choices'] if c['id'].endswith(':arcanum:7')]       # Warlock 13
    row = rows[('Mass Suggestion', 'warlock|xphb')]
    assert row['free'] == {'resource': 'mystic_arcanum_6', 'name': 'Mystic Arcanum (level 6)', 'only': True}
    assert 'mystic_arcanum_6' in {r['key'] for r in s['resources']}
    bad = compute(_doc([{'class': 'warlock|xphb'}] * 11, {'spells:warlock|xphb:arcanum:6': ['fireball|xphb']}))
    assert any('Fireball: level 3 not available' in e for e in bad['errors'])


def _all_rows(doc):
    from systems.dnd5e.routes import _sheet_spells
    s = compute(doc)
    assert s['errors'] == [], s['errors']
    return s, _sheet_spells(doc, s)


def test_at_will_and_feat_free_casts_never_spend_a_slot():
    doc = _doc([{'class': 'warlock|xphb'}] * 4, {
        'opt:warlock|xphb:Eldritch Invocations': ['armor of shadows|xphb'],
        'asi:warlock|xphb:4': {'feat': 'fey-touched|xphb'},
    })
    s = compute(doc)
    pick = next(c for c in s['choices'] if c['label'].startswith('Feat (Fey-Touched): choose a spell'))
    doc['build']['choices'][pick['id']] = ['bless|xphb']
    s, rows = _rows(doc)
    assert rows[('Mage Armor', None)].get('at_will') is True             # Armor of Shadows: genuinely at will
    assert rows[('Misty Step', None)]['free']['resource'] == 'spell:misty step|xphb@feat-fey-touched'
    assert rows[('Bless', None)]['free']['resource'] == 'spell:bless|xphb@feat-fey-touched'   # the chosen one too
    keys = {r['key'] for r in s['resources']}
    assert {'spell:misty step|xphb@feat-fey-touched', 'spell:bless|xphb@feat-fey-touched'} <= keys


@pytest.mark.parametrize('race,spell', [('aarakocra|mpmm', 'Gust of Wind'),
                                        ('genasi (earth)|mpmm', 'Pass without Trace')])
def test_plain_innate_species_spells_keep_their_trait_limit(race, spell):
    # The data lists these as plain innate spells; the trait limits the free
    # cast to once per long rest. Not at will, and not "proficiency bonus"
    # times (Merge with Stone's other paragraph is about Blade Ward).
    doc = _doc([{'class': 'fighter|xphb'}] * 5)
    doc['build']['race'] = race
    s, rows = _all_rows(doc)
    row = next(r for r in rows if r['name'] == spell)
    assert not row.get('at_will')
    res = next(r for r in s['resources'] if r['key'] == row['free']['resource'])
    assert (res['max'], res['recharge']) == (1, 'long')


def test_a_ki_cost_discipline_says_so_instead_of_being_free():
    doc = _doc([{'class': 'monk|phb'}] * 6, {
        'subclass:monk|phb': ['way of the four elements|phb|monk|phb'],
        'opt:way of the four elements|phb|monk|phb:Elemental Disciplines': ['clench of the north wind|phb'],
    }, edition='classic')
    s, rows = _all_rows(doc)
    row = next(r for r in rows if r['name'] == 'Hold Person')
    assert not row.get('at_will') and not row.get('free')
    assert row['cost_note'] == 'Clench of the North Wind: spend its cost by hand'


def test_feature_free_cast_and_oath_spell_dc():
    doc = _doc([{'class': 'paladin|xphb'}] * 5, {'subclass:paladin|xphb': ['oath of vengeance|xphb|paladin|xphb'],
                                                  'spells:paladin|xphb:prepared': ['aid|xphb']})
    s, rows = _rows(doc)
    smite = rows[('Divine Smite', None)]
    assert smite['free']['name'] == "Paladin's Smite"
    assert rows[('Find Steed', None)]['free']['name'] == 'Faithful Steed'
    # Faithful Steed's text says "the aid of an otherworldly steed": not a grant of Aid
    assert not rows[('Aid', 'paladin|xphb')].get('free')
    bane = rows[('Bane', None)]
    assert bane['dc'] == s['spellcasting']['casters'][0]['dc'] and bane['ability'] == 'cha'


@pytest.mark.parametrize('cls,sub,levels,spell,feature', [
    ('cleric|phb', 'knowledge domain|phb|cleric|phb', 6, 'Command', 'Read Thoughts'),
    ('bard|xphb', 'college of glamour|xphb|bard|xphb', 6, 'Charm Person', 'Beguiling Magic'),
])
def test_features_that_merely_mention_a_spell_grant_no_free_cast(cls, sub, levels, spell, feature):
    doc = _doc([{'class': cls}] * levels, {f'subclass:{cls}': [sub]},
               edition='classic' if cls.endswith('|phb') else 'one')
    s, rows = _all_rows(doc)
    for r in rows:
        if r['name'] == spell:
            assert feature not in str((r.get('free') or {}).get('name', '')), r


def test_free_cast_detector_reads_the_grant_not_mentions():
    from systems.dnd5e.rules.effects import free_cast_spells
    assert free_cast_spells(["You always have the {@spell Divine Smite|XPHB} spell prepared. In addition, you can "
                             "cast it without expending a spell slot, but you must finish a Long Rest before you "
                             "can cast it in this way again."]) == ['divine smite']
    assert free_cast_spells(["You can call on the aid of an otherworldly steed. You always have the "
                             "{@spell Find Steed|XPHB} spell prepared. You can also cast the spell once without "
                             "expending a spell slot."]) == ['find steed']
    assert free_cast_spells(["When you cast {@spell Command} using a spell slot, you can target one more creature. "
                             "You regain a use of this feature when you expend a spell slot."]) == []


def test_tracker_shows_the_gm_a_pcs_slots_but_not_players(cid):
    doc = store.new_pc(cid, name='Caster', edition='one')
    doc['build'].update({'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                         'levels': [{'class': 'wizard|xphb', 'hp': None}] * 3})
    store.save_pc(cid, doc)
    service.combat_action(cid, 'add_pc', {'pc_id': doc['id']})
    service.resource_action(cid, doc['id'], 'slot', {'level': 2, 'delta': 1})
    gm = service.encounter_view(cid)['combatants'][0]
    assert gm['slots'] == {'1': {'max': 4, 'used': 0}, '2': {'max': 2, 'used': 1}}
    player = service.encounter_view(cid, for_player=True)['combatants'][0]
    assert 'slots' not in player and 'resources' not in player


# --- spellbook rituals ---------------------------------------------------------------

def _cast_js():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / 'templates' / 'dnd5e' / 'sheet.html').read_text(encoding='utf-8')
    return text.split('// ---- cast planning (pure; tests run these under node) ----', 1)[1].split(
        '// ---- end cast planning ----', 1)[0]


def _node(script):
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('node is required for the sheet cast-planning test')
    r = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr or r.stdout


@pytest.mark.parametrize('edition,cls,detect', [('one', 'wizard|xphb', 'detect magic|xphb'),
                                                ('classic', 'wizard|phb', 'detect magic|phb')])
def test_unprepared_spellbook_ritual_is_castable_as_a_ritual_only(edition, cls, detect):
    import json
    mm = 'magic missile|' + cls.split('|')[1]
    doc = _doc([{'class': cls}] * 3, {f'spells:{cls}:known': [detect, mm]}, edition=edition)
    s, rows = _all_rows(doc)
    dm = next(r for r in rows if r['id'] == detect)
    missile = next(r for r in rows if r['id'] == mm)
    assert dm['prepared'] is False and dm['ritual_book'] is True
    assert not missile.get('ritual_book')                         # not a ritual: no way round preparing it
    caster = s['spellcasting']['casters'][0]
    live = {'slots': {'1': {'max': 4, 'used': 0}}, 'pact': None, 'resources': []}
    dm['_row'], missile['_row'] = 0, 1
    script = (
        "const assert = require('node:assert/strict');\n"
        "const D5 = {esc: s => String(s)};\n"
        + _cast_js() + "\n"
        + 'const dm = %s, mm = %s, live = %s, caster = %s;\n' % (
            json.dumps(dm), json.dumps(missile), json.dumps(live), json.dumps(caster))
        + "const b = castButtonHTML(dm, '1');\n"
        "assert.ok(b.includes('>Ritual'), b);\n"
        "assert.ok(b.includes('data-row=\"0\"'));\n"
        "assert.deepEqual(castPlan(dm, 1, live, caster), {now: 'ritual from the spellbook, no slot'});\n"
        "assert.equal(castButtonHTML(mm, '1'), '', 'an unprepared non-ritual has no cast action');\n"
        "dm.prepared = true;\n"
        "const plan = castPlan(dm, 1, live, caster);\n"
        "assert.deepEqual(plan.opts.map(o => o.value.kind), ['ritual', 'slot']);\n"
        "assert.ok(castButtonHTML(dm, '1').includes('>Cast'));\n"
    )
    _node(script)


# --- invocation spell prerequisites ------------------------------------------------------

@pytest.mark.parametrize('edition,cls,cantrip,ok', [
    ('classic', 'warlock|phb', None, False),
    ('classic', 'warlock|phb', 'eldritch blast|phb', True),
    ('one', 'warlock|xphb', None, False),
    ('one', 'warlock|xphb', 'mage hand|xphb', False),          # a cantrip, but it deals no damage
    ('one', 'warlock|xphb', 'eldritch blast|xphb', True),
])
def test_agonizing_blast_needs_its_spell(edition, cls, cantrip, ok):
    choices = {f'opt:{cls}:Eldritch Invocations': [f'agonizing blast|{cls.split("|")[1]}']}
    if cantrip:
        choices[f'spells:{cls}:cantrips'] = [cantrip]
    s = compute(_doc([{'class': cls}] * 2, choices, edition=edition))
    warned = any(w.startswith('Agonizing Blast: prerequisite') for w in s['warnings'])
    assert warned is (not ok), s['warnings']


# --- separate grants of one spell keep separate uses ------------------------------------

def _elf_wizard(cid, name):
    doc = store.new_pc(cid, name=name, edition='one')
    doc['build'].update({'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL}, 'race': 'elf|xphb',
                         'levels': [{'class': 'wizard|xphb', 'hp': None}] * 5,
                         'choices': {'race:version': ['elf; high elf lineage|xphb'],
                                     'asi:wizard|xphb:4': {'feat': 'fey-touched|xphb'}}})
    return doc


def test_two_grants_of_misty_step_spend_their_own_use(cid):
    doc = _elf_wizard(cid, 'Elf')
    store.save_pc(cid, doc)
    s = compute(doc)
    from systems.dnd5e.routes import _sheet_spells
    misty = [r for r in _sheet_spells(doc, s) if r['name'] == 'Misty Step']
    keys = [r['free']['resource'] for r in misty]
    assert len(misty) == 2 and len(set(keys)) == 2
    slots_before = service.pc_live(store.load_pc(cid, doc['id']))['slots']
    live, used = None, {}
    for k in keys:
        live, _ = service.resource_action(cid, doc['id'], 'resource', {'key': k, 'delta': 1})
        used = {r['key']: r['used'] for r in live['resources']}
        assert used[k] == 1
    assert all(used[k] == 1 for k in keys)
    assert live['slots'] == slots_before                      # no slot was touched


def test_saved_usage_moves_to_the_new_resource_keys(cid):
    doc = _elf_wizard(cid, 'Old save')
    # usage saved under the old per-spell key, before grants had their own
    doc['state']['resources_used'] = {'spell:misty step|xphb': 1, 'something:else': 1}
    store.save_pc(cid, doc)
    live = service.pc_live(store.load_pc(cid, doc['id']))
    used = {r['key']: r['used'] for r in live['resources']}
    misty = [k for k in used if k.startswith('spell:misty step|xphb@')]
    assert sum(used[k] for k in misty) == 1                   # carried to exactly one grant
    assert used['spell:misty step|xphb@species-elf-high-elf-lineage'] == 1   # the first grant, as before
    # written back on the next change; keys that match nothing are kept
    service.resource_action(cid, doc['id'], 'slot', {'level': 1, 'delta': 1})
    saved = store.load_pc(cid, doc['id'])['state']['resources_used']
    assert 'spell:misty step|xphb' not in saved and saved.get('something:else') == 1


def test_2014_mystic_arcanum_usage_survives_the_key_change(cid):
    doc = store.new_pc(cid, name='Arcanum', edition='classic')
    doc['build'].update({'edition': 'classic', 'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                         'levels': [{'class': 'warlock|phb', 'hp': None}] * 11})
    doc['state']['resources_used'] = {'feature:mystic arcanum (6th level)': 1}
    store.save_pc(cid, doc)
    live = service.pc_live(store.load_pc(cid, doc['id']))
    assert {r['key']: r['used'] for r in live['resources']}['mystic_arcanum_6'] == 1


# --- recheck 2: cantrips through the real click handler; class ownership ---------------

def _cast_handler_js():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / 'templates' / 'dnd5e' / 'sheet.html').read_text(encoding='utf-8')
    return 'function castSpell(btn) {' + text.split('  function castSpell(btn) {', 1)[1].split(
        '// ---- cast planning (pure; tests run these under node) ----', 1)[0]


@pytest.mark.parametrize('name,numbers,rolls', [('Fire Bolt', {'dice': '1d10'}, 1), ('Mage Hand', {}, 0)])
def test_casting_a_cantrip_runs_the_real_handler_without_spending(name, numbers, rolls):
    import json
    row = {'id': name.lower() + '|xphb', 'name': name, 'level': 0, 'caster': 'wizard|xphb', 'prepared': True,
           'numbers': numbers, '_row': 0}
    script = (
        "const assert = require('node:assert/strict');\n"
        "const calls = {api: 0, roll: 0, toast: [], choose: 0};\n"
        "const D5 = {esc: s => String(s), toast: (m, k) => calls.toast.push([m, k]),\n"
        "  doRoll: () => { calls.roll++; }, choose: () => { calls.choose++; return Promise.resolve(null); }};\n"
        "const data = {spells: [%s]};\n"
        "const L = () => ({slots: {'1': {max: 2, used: 0}}, pact: null, resources: []});\n"
        "const casterOf = () => ({key: 'wizard|xphb', ritual_casting: true});\n"
        "const api = () => { calls.api++; return Promise.resolve(); };\n"
        "const vit = () => Promise.resolve();\n"
        "const diceWithBonus = (d, b) => d; const upcast = (d) => d;\n" % json.dumps(row)
        + _cast_handler_js() + "\n"
        + _cast_js() + "\n"
        "const attrs = {'data-cast': %s, 'data-row': '0', 'data-lvl': '0', 'data-conc': '0', 'data-name': %s};\n"
        % (json.dumps(row['id']), json.dumps(name))
        + "const btn = {getAttribute: k => (k in attrs ? attrs[k] : null), hasAttribute: k => k in attrs};\n"
        "castSpell(btn);\n"
        "assert.equal(calls.api, 0, 'a cantrip spends nothing');\n"
        "assert.equal(calls.choose, 0, 'and asks nothing');\n"
        "assert.equal(calls.roll, %d);\n" % rolls
        + "assert.ok(calls.toast.some(t => t[0].startsWith('Cast %s') && t[1] !== 'error'), JSON.stringify(calls.toast));\n"
        % name
    )
    _node(script)


@pytest.mark.parametrize('owner,ok', [('wizard', False), ('warlock', True)])
def test_2024_agonizing_blast_needs_a_warlock_cantrip_not_any_class(owner, ok):
    # Wizard 1 / Warlock 2. Chill Touch is on both lists; only a Warlock pick counts.
    levels = [{'class': 'wizard|xphb'}] + [{'class': 'warlock|xphb'}] * 2
    choices = {'opt:warlock|xphb:Eldritch Invocations': ['agonizing blast|xphb'],
               f'spells:{owner}|xphb:cantrips': ['chill touch|xphb']}
    s = compute(_doc(levels, choices))
    warned = any(w.startswith('Agonizing Blast: prerequisite') for w in s['warnings'])
    assert warned is (not ok), s['warnings']


def test_2014_agonizing_blast_accepts_eldritch_blast_from_any_class_pick():
    # 2014 asks for the eldritch blast cantrip itself; the class it came from does not matter.
    levels = [{'class': 'warlock|phb'}] * 2
    s = compute(_doc(levels, {'opt:warlock|phb:Eldritch Invocations': ['agonizing blast|phb'],
                              'spells:warlock|phb:cantrips': ['eldritch blast|phb']}, edition='classic'))
    assert not any(w.startswith('Agonizing Blast') for w in s['warnings'])
