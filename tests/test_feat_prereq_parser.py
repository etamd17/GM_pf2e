"""Structured feat-prerequisite parsing (spec 2026-07-03, audit E1/E2/G4).

The client previously regex-scraped prerequisites out of description HTML and
only caught ability scores + skill ranks -- feat-chain and class-feature
prereqs silently passed eligibility. This parser runs ONCE at data load and
ships a structured field; anything unclassifiable lands in raw only (advisory,
never a false block)."""
import pytest

from app import parse_feat_prereqs, BUILDER_FEATS


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
