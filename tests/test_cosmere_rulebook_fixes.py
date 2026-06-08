"""Regression guards for the Cosmere rulebook-fidelity fixes (audit vs the
Stormlight core rulebook). Each test cites the rulebook line it enforces.

These run in CI: they read only the committed content packs + engine code, no
live PCs. See COSMERE_RULES_AUDIT.md for the full findings.
"""
import systems.cosmere.talents as talents
import systems.cosmere.radiant_talents as rt
import systems.cosmere.items as items
import systems.cosmere.origins as origins
import systems.cosmere.build as build


# --- helpers ---------------------------------------------------------------
def _id_for(path, slug):
    talents._build()
    for rec in talents._BY_ID.values():
        if rec['path'] == path and rec['slug'] == slug:
            return rec['id']
    raise AssertionError(f"talent not found: ({path}, {slug})")


def _prereqs(path, slug):
    """Resolved prereqs as {'skills': {code: rank}, 'attrs': {a: v}, 'talents': [set,...]}."""
    pr = talents.resolved_prereqs(_id_for(path, slug))
    skills, attrs, tal_groups = {}, {}, []
    for g in pr.values():
        if g['type'] == 'skill':
            skills[g['skill']] = g['rank']
        elif g['type'] == 'attribute':
            attrs[g['attribute']] = g['value']
        elif g['type'] == 'talent':
            tal_groups.append({o['label'] for o in g['talents']})
    return {'skills': skills, 'attrs': attrs, 'talents': tal_groups}


# --- heroic-path prerequisites (talent-tree resolver) ----------------------
def test_strategize_turning_point_unswapped():
    # SL:8107 Strategize = Deduction 1+ / Erudition;  SL:8121 Turning Point =
    # Deduction 3+ / Contingency. (Item-level shipped them swapped.)
    s = _prereqs('scholar', 'strategize')
    assert s['skills'].get('ded') == 1 and {'Erudition'} in s['talents']
    tp = _prereqs('scholar', 'turning-point')
    assert tp['skills'].get('ded') == 3 and {'Contingency'} in tp['talents']


def test_composed_requires_strategize_not_predict():
    # SL:8052 Composed (Strategist) requires Strategize (item said "Predict").
    c = _prereqs('scholar', 'composed')
    assert {'Strategize'} in c['talents']
    assert not any('Predict' in g for g in c['talents'])


def test_precise_parry_is_perception_skill_not_awareness():
    # SL:8659 Precise Parry = Perception 3+ (the prc SKILL) + Shattering Blow.
    p = _prereqs('warrior', 'precise-parry')
    assert p['skills'].get('prc') == 3 and 'awa' not in p['attrs']
    assert {'Shattering Blow'} in p['talents']


def test_practical_demonstration_skill_rank_present():
    # SL:6426 Leadership 1+ (item omitted the rank, leaving an unenforced gate).
    p = _prereqs('envoy', 'practical-demonstration')
    assert p['skills'].get('lea') == 1


def test_feral_connection_falls_back_to_item_prereq():
    # SL:7008 Survival 2+ / Protective Bond. Tree node is empty here, so the
    # resolver must fall back to the (correct) item-level prereq.
    f = _prereqs('hunter', 'feral-connection')
    assert f['skills'].get('sur') == 2 and {'Protective Bond'} in f['talents']


def test_null_prereq_talents_now_gated():
    # A representative sweep of formerly-null handbook prereqs (SL cites in the
    # audit): each must now resolve to its rulebook gate.
    assert _prereqs('warrior', 'bloodstance')['skills'].get('ath') == 2
    assert _prereqs('warrior', 'windstance')['skills'].get('prc') == 1
    assert _prereqs('scholar', 'overcharge')['skills'].get('cra') == 3
    assert _prereqs('hunter', 'deadly-trap')['skills'].get('sur') == 1


def test_resolute_stand_label_fixed():
    # SL:7378 / SL:7338 -- the prereq is Resolute Stand, not the stale
    # "Valiant Stand" label the item shipped.
    for slug in ('resilient-hero', 'demonstrative-command'):
        g = _prereqs('leader', slug)['talents']
        assert {'Resolute Stand'} in g
        assert not any('Valiant Stand' in s for s in g)


def test_handbook_talent_prereqs_are_enforced():
    # The checker must now load handbook talents (it previously loaded only the
    # base pack, leaving whole specialties ungated). Meteoric Leap (SL:8634)
    # needs Athletics 3 + Bloodstance -- an empty build fails both.
    tid = _id_for('warrior', 'meteoric-leap')
    miss = talents.unmet(tid, [], {}, {})
    assert any('Athletics' in m for m in miss) and any('Bloodstance' in m for m in miss)
    # Met build -> no complaints.
    assert talents.unmet(tid, ['Bloodstance'], {'ath': 3}, {}) == []


def test_customary_garb_officer_remapped_to_leader():
    # SL:7450 Customary Garb (Officer) is a Leader talent; its system.path
    # shipped as the corrupt value "champion" (a specialty, never a path).
    assert talents.norm_path('champion') == 'leader'
    assert _id_for('leader', 'customary-garb')  # resolvable under leader


# --- radiant talents -------------------------------------------------------
def test_unleashed_entropy_prereq_corrected():
    # SL:15816 Spark Sending OR Gout of Flame (handbook data said Inescapable
    # Spark, which both the doc and tree node carried).
    ue = [t for t in rt.SURGE_TALENTS['dvs'] if t['name'] == 'Unleashed Entropy']
    assert ue and ue[0]['prereq'] == {'talent': 'Spark Sending or Gout of Flame'}


def test_wound_regeneration_requires_invested():
    # SL:11674 Wound Regeneration requires the Invested talent (must not regress
    # to a bare First-Ideal entry).
    for order in ('lightweavers', 'windrunners'):
        wr = [t for t in rt.ORDER_TALENTS[order] if t['name'] == 'Wound Regeneration']
        assert wr and wr[0]['prereq'] == {'talent': 'Invested'}


def test_lightweaver_invested_is_a_first_ideal_entry():
    # SL:11568 Lightweaver Invested = "Speak the First Ideal" (an entry talent),
    # NOT a doc-level talent prereq. Guards against an over-eager "fix".
    inv = [t for t in rt.ORDER_TALENTS['lightweavers'] if t['name'] == 'Invested']
    assert inv and inv[0]['prereq'] == {'ideal': 1}


# --- content additions -----------------------------------------------------
def test_mateform_present():
    # SL:2334 Change Form grants dullform AND mateform.
    assert 'mateform' in origins.SINGER_FORMS
    assert origins.SINGER_FORMS['mateform']['attrs'] == {}  # no combat stat change


def test_unarmed_attack_in_catalog():
    # SL:17654 Special Weapons: Unarmed Attack -- Athletics, impact damage.
    ua = items.by_name('Unarmed Attack')
    assert ua and ua['kind'] == 'weapon'
    assert ua['damage'] and ua['damage']['skill'] == 'ath' and ua['damage']['type'] == 'impact'


def test_reactive_strike_allows_unarmed():
    # SL:21844 "melee weapon attack OR unarmed attack" -- value/chat dropped it.
    import json
    import os
    path = os.path.join(origins.__file__.rsplit('/', 1)[0], 'content', 'actions.json')
    docs = json.load(open(path, encoding='utf-8'))
    rs = next(d for d in docs if d.get('system', {}).get('id') == 'reactive_strike')
    for field in ('value', 'chat', 'short'):
        assert 'unarmed attack' in rs['system']['description'][field]


# --- Singer talent budget --------------------------------------------------
def test_singer_gets_extra_l1_talent():
    # SL:1620 Singer ancestry grants Change Form PLUS a starting-forms talent
    # (two L1 ancestry talents vs one) -> +1 to the talent budget at every level.
    for lvl in (1, 6, 11):
        hum = build.CosmereBuild({'level': lvl, 'ancestry': 'Human', 'path': 'warrior'})
        sng = build.CosmereBuild({'level': lvl, 'ancestry': 'Singer', 'path': 'warrior'})
        assert sng.talents_available() == hum.talents_available() + 1
