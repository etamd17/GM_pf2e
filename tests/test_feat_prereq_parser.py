"""Structured feat-prerequisite parsing (spec 2026-07-03, audit E1/E2/G4).

The client previously regex-scraped prerequisites out of description HTML and
only caught ability scores + skill ranks -- feat-chain and class-feature
prereqs silently passed eligibility. This parser runs ONCE at data load and
ships a structured field; anything unclassifiable lands in raw only (advisory,
never a false block)."""
import glob
import json
import os

import pytest

from app import parse_feat_prereqs, parse_feat_prereq_clauses, BUILDER_FEATS


def test_empty_and_none():
    for v in (None, '', '   '):
        p = parse_feat_prereqs(v)
        assert p == {'level': None, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}


def test_ability_score():
    p = parse_feat_prereqs('Strength 16')
    assert p['abilities'] == {'str': 16}


def test_skill_rank():
    p = parse_feat_prereqs('expert in Athletics')
    assert p['skills'] == {'athletics': 'expert'}
    p2 = parse_feat_prereqs('trained in Occultism')
    assert p2['skills'] == {'occultism': 'trained'}


def test_feat_chain():
    # NOTE: the brief's original example ("Power Attack") is not present as a
    # standalone feat entry in this app's compiled pf2e_database.db (confirmed
    # by inspection: BUILDER_FEATS has no feat named "Power Attack" at all in
    # this data source), so this uses "Titan Wrestler" -- confirmed present --
    # per the task brief's instruction to adjust heuristics/tests to the REAL
    # dataset. Feat-chain matching itself is unchanged: exact case-insensitive
    # clause match against the loaded feat-name set.
    p = parse_feat_prereqs('Titan Wrestler')
    assert p['feats'] == ['Titan Wrestler']


def test_compound():
    p = parse_feat_prereqs('Strength 14, expert in Athletics, Titan Wrestler')
    assert p['abilities'] == {'str': 14}
    assert p['skills'] == {'athletics': 'expert'}
    assert p['feats'] == ['Titan Wrestler']


def test_unparsable_lands_in_raw_only():
    txt = 'ability to cast focus spells'
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['abilities'] == {} and p['skills'] == {} and p['feats'] == [] and p['features'] == []


def test_master_legendary_ranks():
    assert parse_feat_prereqs('master in Religion')['skills'] == {'religion': 'master'}
    assert parse_feat_prereqs('legendary in Stealth')['skills'] == {'stealth': 'legendary'}


# --- Real shapes sampled from pf2e_database.db's `feats` table (106 rows with
# a regex-matched "Prerequisites" label; 21 with a clean HTML-bounded clause
# after tightening the boundary regex). Sampled 2026-07-03, see
# .superpowers/sdd/lvl-task-1-report.md for the full sample.

def test_real_shape_skill_rank_lowercase_label():
    # "Repair Huntergate" — 'trained in Crafting or Thievery'. RAW alternatives
    # (Crafting OR Thievery) collapse to raw-only for now rather than a false
    # single-skill block; the unbendable contract is "never a false blocking
    # field" and an "or" is exactly the ambiguous case that must not silently
    # pick one skill and claim the other is required too.
    p = parse_feat_prereqs('trained in Crafting or Thievery')
    assert p['raw'] == 'trained in Crafting or Thievery'
    assert p['skills'] == {}


def test_real_shape_capitalized_trained_no_in():
    # "Encouraging Words" — 'Trained in Diplomacy'
    p = parse_feat_prereqs('Trained in Diplomacy')
    assert p['skills'] == {'diplomacy': 'trained'}


def test_real_shape_three_way_or_skill():
    # "Rally" — 'trained in Diplomacy, Intimidation, or Performance'
    # Comma-split makes this look like 3 clauses ("trained in Diplomacy",
    # "Intimidation", "or Performance"); the literal word "or" only appears in
    # the LAST fragment, so a naive per-clause check would let the first
    # fragment slip through as a false single-skill requirement even though
    # the real prereq is any ONE of the three. The whole-string "or" check
    # must catch this: nothing gets classified, everything stays in raw.
    p = parse_feat_prereqs('trained in Diplomacy, Intimidation, or Performance')
    assert p['raw'] == 'trained in Diplomacy, Intimidation, or Performance'
    assert p['skills'] == {}
    assert 'Intimidation' not in p['feats']
    assert 'Performance' not in p['feats'] and 'or Performance' not in p['feats']


def test_real_shape_feat_chain_exact_name():
    # "Intensify Vulnerability" — 'Exploit Vulnerability' (an exact feat name
    # in the dataset)
    p = parse_feat_prereqs('Exploit Vulnerability')
    assert p['feats'] == ['Exploit Vulnerability']


def test_real_shape_class_restriction_is_raw_only():
    # "Vindicator"/"War Magic"/"Runelord" — 'You must be a wizard.' etc. Not a
    # feat/skill/ability/level clause; must not be misclassified.
    for txt in ('You must be a ranger.', 'You must be a wizard.', 'You must be a fighter.'):
        p = parse_feat_prereqs(txt)
        assert p['raw'] == txt
        assert p['abilities'] == {} and p['skills'] == {} and p['feats'] == [] and p['features'] == []


def test_real_shape_freeform_narrative_is_raw_only():
    # "Glass Skin" — a narrative/story prerequisite, not mechanical at all.
    txt = ('You were present at the death of the medusa Alethsia, whose vitrumantic '
           'powers were passed on to you in the wake of her destruction.')
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['abilities'] == {} and p['skills'] == {} and p['feats'] == [] and p['features'] == []


def test_real_shape_relic_prereq_is_raw_only():
    # "Divine Retribution" — 'The relic is a weapon.'
    txt = 'The relic is a weapon.'
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['feats'] == []


def test_real_shape_empty_prereq_string():
    # "Battle Creed" — matched an empty Prerequisites clause in the source data.
    p = parse_feat_prereqs('')
    assert p == {'level': None, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}


def test_level_clause():
    # Synthetic (PF2e gates general/class feats via the `level` column, not a
    # prose "Level N" prereq clause in the sampled data) but the brief and the
    # parser contract both name level-gating explicitly, so it must work when
    # a compendium entry (or future data source) does spell it out.
    p = parse_feat_prereqs('Level 8')
    assert p['level'] == 8


def test_level_clause_compound():
    p = parse_feat_prereqs('Level 8, expert in Athletics')
    assert p['level'] == 8
    assert p['skills'] == {'athletics': 'expert'}


def test_case_insensitive_feat_chain_canonical_casing():
    # Clause matching is case-insensitive but the output preserves the
    # dataset's canonical casing, not whatever casing appeared in the prereq text.
    p = parse_feat_prereqs('exploit vulnerability')
    assert p['feats'] == ['Exploit Vulnerability']


def test_run_on_prose_does_not_false_positive_feat_chain():
    # Regression: "Verduran Shadow Dedication"'s prereq text is a real
    # extraction artifact -- the upstream (pre-existing, untouched) HTML
    # regex matches an unrelated lowercase "prerequisite" mention buried in
    # flavor text, so `prerequisites_raw` ends up as a 10-clause run-on
    # sentence. Two of the comma-split fragments ("Hide", "Sneak") happen to
    # be real feat/action names in the dataset, which without a clause-count
    # guard would silently register as false feat-chain requirements. The
    # unbendable contract (never a false blocking field) requires this to
    # land entirely in raw.
    txt = (
        'and gaining additional benefits from feats for being an expert, '
        'master, or legendary in Stealth; however, unless you also fulfill '
        'the Stealth prerequisite, you can only use those feats in forest '
        'terrain. While in forests, you can use your Survival modifier in '
        'place of your Stealth modifier when you Avoid Notice, Hide, Sneak, '
        'or would use Stealth to roll initiative.'
    )
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['feats'] == []
    assert p['skills'] == {}
    assert p['abilities'] == {}


def test_run_on_prose_does_not_false_positive_from_short_reference_list():
    # Regression: "Three Clear Breaths" -- another false-label extraction
    # ("you must meet the prerequisites for these feats as normal") whose
    # captured text lists unrelated feat names ("Fleet") as part of a
    # "choose one of these instead" clause, not an actual requirement.
    txt = (
        'for these feats as normal. For each of these feats you already '
        'have, you can instead gain a different feat from the following '
        'list: Canny Acumen, Fleet, and Toughness.'
    )
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['feats'] == []


def test_all_builder_feats_have_prereqs_struct_after_load():
    # Stamped additively at data-load time; always present, always the full
    # key set, even for feats with no prerequisites at all.
    all_feats = BUILDER_FEATS['class'] + BUILDER_FEATS['skill'] + BUILDER_FEATS['general'] + BUILDER_FEATS['ancestry']
    if not all_feats:
        pytest.skip('BUILDER_FEATS not populated in this test environment (no compendium DB load)')
    for f in all_feats:
        assert 'prereqs_struct' in f
        struct = f['prereqs_struct']
        assert set(struct.keys()) == {'level', 'abilities', 'skills', 'feats', 'features', 'raw'}


# --- T1b: join structured prerequisites from the local Foundry feats pack ---
#
# T1's regex scrape of `description` HTML only recovers a clean prereq clause
# for ~21 of the DB's 8,590 feats (feat-chains/Dedications largely 0%
# coverage) because the compiled pf2e_database.db dropped the source pack's
# `system.prerequisites.value` column during compilation. The pack itself
# (compendium_data/feats/**/*.json, 5,845 docs, 3,610 with a structured
# `system.prerequisites.value` list of clean single clauses) still has it.
# `parse_feat_prereq_clauses` takes that list directly -- no comma-splitting,
# no clause-count cap needed, since each list entry is already one clause --
# and shares classification internals with `parse_feat_prereqs`.
#
# Verified against the real pack on 2026-07-03 (see lvl-task-1b-report.md):
#   - compendium_data/feats/skill/level-1/battle-medicine.json
#     (_id wYerMk6F1RZb0Fwt): prerequisites.value == [{'value': 'trained in Medicine'}]
#   - compendium_data/feats/skill/level-7/paragon-battle-medicine.json
#     (_id xOMwuKCf02aFzyp3): prerequisites.value ==
#     [{'value': 'Battle Medicine'}, {'value': 'master in Medicine'}]
#     -- this is the real feat-chain-onto-Battle-Medicine example (NOT
#     Continual Recovery, whose only prereq is 'expert in Medicine' with no
#     feat-chain clause at all; swapped per the brief's own instruction to
#     verify real shapes and adjust to the actual dataset).
#   - compendium_data/feats/archetype/exemplar/exemplar-dedication.json
#     (_id qvWmW5JWpVBDyGqe, traits include 'dedication'): prerequisites.value
#     == [{'value': 'Strength +2 or Dexterity +2'}] -- single whole-clause OR
#     ability prereq, must stay raw-only.


def test_parse_feat_prereq_clauses_empty():
    assert parse_feat_prereq_clauses([]) == {
        'level': None, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}
    assert parse_feat_prereq_clauses(None) == {
        'level': None, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}


def test_parse_feat_prereq_clauses_single_skill_rank():
    p = parse_feat_prereq_clauses(['trained in Medicine'])
    assert p['skills'] == {'medicine': 'trained'}
    assert p['raw'] == 'trained in Medicine'


def test_parse_feat_prereq_clauses_feat_chain_plus_skill_rank():
    # Paragon Battle Medicine's real clause list: a feat-chain reference to
    # Battle Medicine plus a skill-rank clause. No comma-splitting occurs --
    # each list entry is already one clause -- so this must classify both,
    # unlike a raw comma-joined string which T1's clause-count/">3 clauses"
    # guard is not designed to reason about here.
    lookup = {'battle medicine': 'Battle Medicine'}
    p = parse_feat_prereq_clauses(['Battle Medicine', 'master in Medicine'], lookup)
    assert p['feats'] == ['Battle Medicine']
    assert p['skills'] == {'medicine': 'master'}
    assert p['raw'] == 'Battle Medicine; master in Medicine'


def test_parse_feat_prereq_clauses_whole_clause_or_stays_raw_only():
    # Exemplar Dedication's real clause: a single list entry that itself
    # contains an "X or Y" alternative. The never-false-block contract means
    # this clause is skipped for structured classification entirely (advisory
    # raw only) -- NOT split or guessed at.
    p = parse_feat_prereq_clauses(['Strength +2 or Dexterity +2'])
    assert p['abilities'] == {}
    assert p['skills'] == {}
    assert p['feats'] == []
    assert p['raw'] == 'Strength +2 or Dexterity +2'


def test_parse_feat_prereq_clauses_no_clause_count_cap():
    # Unlike parse_feat_prereqs' raw-string ">3 clauses" run-on-prose guard,
    # a pack clause list has no such cap -- every entry is already a single,
    # pre-segmented clause (never narrative prose), so a real feat with more
    # than 3 structured prereqs must still classify all of them.
    p = parse_feat_prereq_clauses([
        'Level 8', 'Strength 16', 'expert in Athletics', 'trained in Acrobatics',
    ])
    assert p['level'] == 8
    assert p['abilities'] == {'str': 16}
    assert p['skills'] == {'athletics': 'expert', 'acrobatics': 'trained'}


def _load_pack_feat_docs():
    """Walk the real Foundry feats pack and return {lowercase name: doc}."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'compendium_data', 'feats')
    docs = {}
    for path in glob.glob(os.path.join(root, '**', '*.json'), recursive=True):
        if os.path.basename(path) == '_folders.json':
            continue
        with open(path, 'r', encoding='utf-8') as fh:
            doc = json.load(fh)
        name = doc.get('name')
        if name:
            docs[name.lower()] = doc


    return docs


def test_verified_pack_feats_exist_and_have_expected_clauses():
    # Sanity-check the fixtures this whole section leans on are real, so the
    # test doesn't silently pass against stale assumptions if the pack changes.
    docs = _load_pack_feat_docs()
    battle_medicine = docs['battle medicine']
    assert battle_medicine['_id'] == 'wYerMk6F1RZb0Fwt'
    assert [c['value'] for c in battle_medicine['system']['prerequisites']['value']] == ['trained in Medicine']

    paragon = docs['paragon battle medicine']
    assert paragon['_id'] == 'xOMwuKCf02aFzyp3'
    assert [c['value'] for c in paragon['system']['prerequisites']['value']] == [
        'Battle Medicine', 'master in Medicine']

    exemplar_dedication = docs['exemplar dedication']
    assert exemplar_dedication['_id'] == 'qvWmW5JWpVBDyGqe'
    assert 'dedication' in exemplar_dedication['system']['traits']['value']
    assert [c['value'] for c in exemplar_dedication['system']['prerequisites']['value']] == [
        'Strength +2 or Dexterity +2']


def test_integration_battle_medicine_has_trained_medicine_skill():
    all_feats = BUILDER_FEATS['class'] + BUILDER_FEATS['skill'] + BUILDER_FEATS['general'] + BUILDER_FEATS['ancestry']
    if not all_feats:
        pytest.skip('BUILDER_FEATS not populated in this test environment (no compendium DB load)')
    battle_medicine = next((f for f in all_feats if f['name'] == 'Battle Medicine'), None)
    assert battle_medicine is not None, 'Battle Medicine missing from loaded BUILDER_FEATS'
    assert battle_medicine['prereqs_struct']['skills'] == {'medicine': 'trained'}


def test_integration_paragon_battle_medicine_has_feat_chain_to_battle_medicine():
    all_feats = BUILDER_FEATS['class'] + BUILDER_FEATS['skill'] + BUILDER_FEATS['general'] + BUILDER_FEATS['ancestry']
    if not all_feats:
        pytest.skip('BUILDER_FEATS not populated in this test environment (no compendium DB load)')
    paragon = next((f for f in all_feats if f['name'] == 'Paragon Battle Medicine'), None)
    assert paragon is not None, 'Paragon Battle Medicine missing from loaded BUILDER_FEATS'
    assert 'Battle Medicine' in paragon['prereqs_struct']['feats']
    assert paragon['prereqs_struct']['skills'] == {'medicine': 'master'}


def test_integration_dedication_with_or_ability_prereq_stays_raw_only():
    all_feats = BUILDER_FEATS['class'] + BUILDER_FEATS['skill'] + BUILDER_FEATS['general'] + BUILDER_FEATS['ancestry']
    if not all_feats:
        pytest.skip('BUILDER_FEATS not populated in this test environment (no compendium DB load)')
    exemplar = next((f for f in all_feats if f['name'] == 'Exemplar Dedication'), None)
    assert exemplar is not None, 'Exemplar Dedication missing from loaded BUILDER_FEATS'
    struct = exemplar['prereqs_struct']
    assert struct['abilities'] == {}
    assert struct['skills'] == {}
    assert struct['feats'] == []
    assert 'Strength +2 or Dexterity +2' in struct['raw']


def test_pack_index_drops_ambiguous_duplicate_names():
    """Two pack docs sharing a lowercased name with DIFFERENT prereq clauses
    must vanish from the name-fallback index (first-glob-wins would let a
    sibling's prereqs bleed onto the wrong feat -- the real 'Keep Up the Good
    Fight' collision). Identical-clause duplicates may stay."""
    import json as _json
    import app as _app

    def _write(dirpath, fname, doc):
        with open(dirpath / fname, 'w', encoding='utf-8') as fh:
            _json.dump(doc, fh)

    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        _write(d, 'a.json', {'_id': 'AAAA', 'name': 'Same Name',
                             'system': {'prerequisites': {'value': [{'value': 'Feat One'}]}}})
        _write(d, 'b.json', {'_id': 'BBBB', 'name': 'Same Name',
                             'system': {'prerequisites': {'value': [{'value': 'Feat Two'}]}}})
        _write(d, 'c.json', {'_id': 'CCCC', 'name': 'Unique Name',
                             'system': {'prerequisites': {'value': [{'value': 'Feat Three'}]}}})
        idx = _app._build_feat_pack_prereq_index(pack_dir=str(d))
    assert 'same name' not in idx['by_name']          # ambiguous -> dropped
    assert idx['by_name']['unique name'] == ['Feat Three']
    assert idx['by_id']['AAAA'] == ['Feat One']        # id index unaffected
    assert idx['by_id']['BBBB'] == ['Feat Two']


def test_real_pack_collision_not_misattributed():
    """The one real duplicate-name pair in the shipped pack: the Guardian
    class feat 'Keep Up the Good Fight' has no prereqs and must NOT inherit
    the same-named Knight Vigilant archetype feat's Dedication prereq."""
    import pytest as _pytest
    from app import BUILDER_FEATS
    all_feats = []
    for v in BUILDER_FEATS.values():
        if isinstance(v, list):
            all_feats.extend(v)
    if not all_feats:
        _pytest.skip('BUILDER_FEATS not populated in this environment')
    kutgf = [f for f in all_feats if f.get('name') == 'Keep Up the Good Fight']
    if not kutgf:
        _pytest.skip('feat not present in this build of the DB')
    for f in kutgf:
        assert 'Knight Vigilant Dedication' not in (f.get('prereqs_struct') or {}).get('feats', [])
