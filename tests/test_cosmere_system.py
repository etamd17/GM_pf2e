"""Cosmere RPG system (Phase 3) — registration, combat profile, the actor
math, and ingested content.

The actor assertions use the REAL ingested adversary 'Archer' as ground truth
(its Foundry overrides — health 12, focus 3, deflect 1 — happen to equal the
rulebook formulas 10+STR / 2+WIL, which cross-checks the compute paths), plus a
synthetic wrapped player-character envelope to exercise the computed (non-
override) path.
"""
from __future__ import annotations

import systems
from systems.cosmere import load_adversaries, load_pack, adversary_docs
from systems.cosmere.actor import CosmereActor, cosmere_max_health, tier_of


def test_module_bestiary_fill():
    """The ingested Foundry modules fill the bestiary far beyond the base 20,
    deduped by name, every doc a real adversary with an id (tracker-resolvable)."""
    docs = adversary_docs()
    assert len(docs) >= 100, len(docs)
    names = [(d.get('name') or '').lower() for d in docs]
    assert len(names) == len(set(names)), 'adversaries must be deduped by name'
    assert 'archer' in names                                   # base adversary kept
    assert all(d.get('type') == 'adversary' and d.get('_id') for d in docs)
    # every adversary constructs as a CosmereActor (tracker/bestiary contract)
    for d in docs:
        CosmereActor(d).to_summary()


def test_handbook_content_ingested():
    """The Stormlight Handbook canon content is ingested + loadable -- the real
    Radiant paths/talents, cultures, and surge powers for the walkthrough builder
    (replacing the earlier PDF-mined approximations)."""
    radiant = load_pack('handbook-radiant-paths')
    assert sum(1 for d in radiant if d.get('type') == 'path') >= 9        # Radiant orders
    assert sum(1 for d in radiant if d.get('type') == 'talent') >= 50
    assert len([d for d in load_pack('handbook-cultures') if d.get('type') == 'culture']) >= 10
    assert len([d for d in load_pack('handbook-surges') if d.get('type') == 'power']) >= 5
    assert load_pack('handbook-heroic-paths') and load_pack('handbook-ancestries')


# -- registration -----------------------------------------------------------

def test_cosmere_registered_and_dispatches():
    cosmere = systems.get('cosmere')
    assert cosmere.key == 'cosmere'
    assert cosmere.label
    assert 'cosmere' in [s.key for s in systems.all_systems()]
    assert systems.system_for_doc({'system': 'cosmere'}) is cosmere
    assert systems.system_for_doc({'system': 'COSMERE'}) is cosmere
    # Cosmere binds its own (self-contained) actor factory at registration.
    assert cosmere._actor_factory is CosmereActor


def test_pf2e_still_default_and_distinct():
    assert systems.system_for_doc({}).key == 'pf2e'
    assert systems.get('cosmere') is not systems.get('pf2e')


# -- combat profile ---------------------------------------------------------

def test_combat_profile_describes_cosmere():
    cp = systems.get('cosmere').combat
    assert cp.defenses == ('phy', 'cog', 'spi')
    assert cp.damage_pool == 'health'
    assert cp.down_condition == 'unconscious'
    assert cp.death_model == 'injuries'
    assert cp.action_model == 'fast_slow_phases'
    assert (cp.fast_actions, cp.slow_actions, cp.action_count) == (2, 3, 3)
    assert cp.phases == ('fast_pc', 'fast_npc', 'slow_pc', 'slow_npc')
    assert cp.initiative_stat == 'spd' and cp.initiative_higher_first is True
    assert cp.deflectable_damage == ('impact', 'keen', 'energy')
    assert cp.stacking_rule == 'named'
    keys = cp.condition_keys()
    assert len(keys) == 14
    assert cp.is_valued('afflicted') is True       # stackable
    assert cp.is_valued('exhausted') is True
    assert 'prone' in keys and cp.is_valued('prone') is False


def test_cosmere_profile_diverges_from_pf2e():
    """The contract really does describe two different systems."""
    c, p = systems.get('cosmere').combat, systems.get('pf2e').combat
    assert c.defenses != p.defenses
    assert c.action_model != p.action_model
    assert c.death_model != p.death_model
    assert p.deflectable_damage == ()  # PF2e has no Deflect; defaulted field unset


# -- health / tier formulas -------------------------------------------------

def test_tier_of_levels():
    assert [tier_of(l) for l in (1, 5, 6, 10, 11, 16, 20, 21, 30)] == \
        [1, 1, 2, 2, 3, 4, 4, 5, 5]


def test_cosmere_max_health_curve():
    assert cosmere_max_health(1, 0) == 10
    assert cosmere_max_health(1, 2) == 12           # 10 + STR
    assert cosmere_max_health(5, 2) == 32           # +5 x4 levels in tier 1
    assert cosmere_max_health(6, 2) == 38           # +tier2 gain(4) + STR re-add(2)
    assert cosmere_max_health(11, 2) == 38 + (4 * 4) + (3 + 2)  # T2 L7-10, then L11 tier3+STR


# -- actor math on the real Archer adversary --------------------------------

def _archer():
    for a in load_adversaries():
        if a.name == 'Archer':
            return a
    raise AssertionError("Archer adversary not found in ingested bestiary")


def test_archer_attributes_and_defenses():
    a = _archer()
    assert a.type == 'adversary' and a.tier == 1 and a.role == 'minion'
    assert a.attributes == {'str': 2, 'spd': 1, 'int': 2, 'wil': 1, 'awa': 2, 'pre': 1}
    # Defense = 10 + the two governing attributes.
    assert a.defenses == {'phy': 13, 'cog': 13, 'spi': 13}


def test_archer_resources_and_deflect():
    a = _archer()
    assert a.health_max == 12 and a.health == 12     # override == 10 + STR(2)
    assert a.focus_max == 3                           # override == 2 + WIL(1)
    assert a.deflect['value'] == 1
    # Deflect reduces energy/impact/keen only.
    assert a.deflect['types'].get('impact') is True
    assert a.deflect['types'].get('keen') is True
    assert a.deflect['types'].get('energy') is True
    assert a.deflect['types'].get('spirit') is False
    assert a.deflect['types'].get('vital') is False


def test_archer_skill_mods():
    a = _archer()
    # mod = rank + governing attribute value (read from the skill's own attr).
    assert a.skills['agi']['mod'] == 3   # rank 2 + spd 1
    assert a.skills['prc']['mod'] == 4   # rank 2 + awa 2
    assert a.skills['hwp']['mod'] == 4   # rank 2 + str 2
    # The 10 Surge skills are present but locked for a non-Radiant.
    assert a.skills['grv']['unlocked'] is False


# -- computed (non-override) path via a wrapped PC envelope -----------------

def test_wrapped_character_envelope_computes_stats():
    env = {
        'system': 'cosmere',
        'campaign_id': 'x', 'owner_user_id': 'u',
        'system_data': {
            'name': 'Probe', 'type': 'character',
            'system': {
                'attributes': {
                    'str': {'value': 3}, 'spd': {'value': 2}, 'int': {'value': 1},
                    'wil': {'value': 2}, 'awa': {'value': 2}, 'pre': {'value': 2},
                },
                'defenses': {'phy': {}, 'cog': {}, 'spi': {}},
                'resources': {'hea': {'max': {}}, 'foc': {'max': {}}, 'inv': {'max': {}}},
                'skills': {'agi': {'attribute': 'spd', 'rank': 1}},
                'deflect': {},
            },
        },
    }
    # Dispatch through the registry, exactly as a PC load would.
    a = systems.actor_for_doc(env, 'probe.json')
    assert isinstance(a, CosmereActor)
    assert a.name == 'Probe' and a.is_pc is True
    assert a.defenses == {'phy': 15, 'cog': 13, 'spi': 14}  # 10 + governing pair
    assert a.health_max == 13                                # L1 -> 10 + STR(3)
    assert a.focus_max == 4                                  # 2 + WIL(2)
    assert a.investiture_max == 0                            # not Radiant
    assert a.skills['agi']['mod'] == 3                       # rank 1 + spd 2


# -- content ----------------------------------------------------------------

def test_ingested_content_present():
    advs = load_adversaries()
    assert len(advs) >= 15
    assert any(a.name == 'Archer' for a in advs)
    paths = [d for d in load_pack('heroic-paths') if d.get('type') == 'path']
    assert len(paths) == 6           # Agent/Envoy/Hunter/Leader/Scholar/Warrior
    assert load_pack('ancestries')   # Human (starter set)
