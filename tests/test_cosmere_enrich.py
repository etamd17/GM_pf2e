"""Foundry inline enrichers in Cosmere adversary abilities must render as
readable prose, not raw markup. The mined `cosmere-rpg` packs store ability
descriptions full of `[[damage 1d8 Keen average]]`, `[[lookup @actor.name]]{...}`,
`[[test skill=agi dc=16]]`, and `@UUID[...]{Label}` enrichers; the tracker/stat
block showed them verbatim (unreadable) until `_enrich` cleaned them at the
source (CosmereActor action descriptions).
"""
import os
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
os.environ.setdefault('GM_PASSWORD', '')

from systems.cosmere.actor import CosmereActor, _enrich


def test_damage_enricher_drops_average_keeps_formula_and_type():
    assert _enrich("Hit [[damage 1d8 + 12 Keen average]].") == "Hit 1d8 + 12 Keen."
    assert _enrich("recovers [[damage 1d6 + 3 Healing average]].") == "recovers 1d6 + 3 Healing."
    assert _enrich("takes [[damage 2d10 + 9]] damage") == "takes 2d10 + 9 damage"


def test_test_enricher_becomes_named_skill_and_dc():
    assert _enrich("make a [[test skill=agi dc=16]] to grab") == "make a Agility test (DC 16) to grab"
    assert _enrich("[[test skill=grv dc=14]]") == "Gravitation test (DC 14)"


def test_lookup_actor_name_uses_the_actors_name():
    assert _enrich("The [[lookup @actor.name]]{Actor Name} strikes.", "Heavenly One") \
        == "The Heavenly One strikes."


def test_uuid_content_link_becomes_its_label():
    assert _enrich("uses their @UUID[Actor.x.Item.y]{Raysium Lance} action") \
        == "uses their Raysium Lance action"


def test_sentence_and_clause_spacing_is_repaired():
    assert _enrich("on a success.If a Lashed object") == "on a success. If a Lashed object"
    assert _enrich("Graze [[damage 1d8 Keen average]];Hit next") == "Graze 1d8 Keen; Hit next"


def test_actor_action_descriptions_are_enriched_at_the_source():
    """The cleaning is applied where CosmereActor builds its `actions`, so every
    downstream serializer (tracker state, stat modal) gets readable text."""
    doc = {'name': 'Heavenly One', 'type': 'adversary', 'system': {}, 'items': [
        {'type': 'action', 'name': 'Regenerate', 'system': {'description': {'value':
            '<p>The [[lookup @actor.name]]{Actor Name} recovers '
            '[[damage 1d6 + 3 Healing average]].</p>'}}},
        {'type': 'action', 'name': 'Strike: Raysium Lance', 'system': {'description': {'value':
            'Graze [[damage 1d8 Keen average]];Hit [[damage 1d8 + 12 Keen average]].'}}},
    ]}
    a = CosmereActor(doc)
    by = {x['name']: x['description'] for x in a.actions}
    assert by['Regenerate'] == 'The Heavenly One recovers 1d6 + 3 Healing.'
    assert by['Strike: Raysium Lance'] == 'Graze 1d8 Keen; Hit 1d8 + 12 Keen.'
    joined = ' '.join(by.values())
    for token in ('[[', ']]', '@UUID', 'lookup @actor', 'average'):
        assert token not in joined, ('residual enricher markup: %s' % token)
