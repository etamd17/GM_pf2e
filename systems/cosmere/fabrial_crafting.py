"""Inventing Unique Fabrials (Stormlight Handbook, Ch.7, pp.267-269).

The mined content packs hold the fabrial DEVICE catalog but not the crafting
rules, so this module encodes the RAW crafting machinery (paraphrased game
mechanics): the 14 craftable effects with their tiers/charges and each effect's
own upgrade + drawback, the general d8 upgrade/drawback table, the advanced
features, the per-tier material cost + trap-spren Lore DC, and the Crafting-test
result bands. The guided "Fabrial Workshop" on the sheet drives the 5-step flow:
choose effect -> materials -> trap a spren (Lore test) -> Crafting test ->
apply upgrades/drawbacks.
"""
from __future__ import annotations

# Per-tier material cost (marks) and trap-the-spren Lore DC.
TIER_COST = {1: 100, 2: 200, 3: 400, 4: 800}
TRAP_DC = {1: 15, 2: 20, 3: 25, 4: 30}

# Crafting-test result bands (raise the stakes): (min_total, label, upgrades, drawbacks, failed).
CRAFT_BANDS = [
    (26, 'Exceptional Creation', 3, 0, False),
    (21, 'Quality Creation', 2, 1, False),
    (11, 'Typical Creation', 1, 1, False),
    (6, 'Shoddy Creation', 0, 1, False),
    (0, 'Utter Failure', 0, 0, True),
]


def craft_result(total: int) -> dict:
    """The crafting-test outcome for a d20+Crafting (raise-the-stakes) total."""
    for lo, label, up, dn, failed in CRAFT_BANDS:
        if total >= lo:
            return {'total': total, 'label': label, 'upgrades': up, 'drawbacks': dn, 'failed': failed}
    return {'total': total, 'label': 'Utter Failure', 'upgrades': 0, 'drawbacks': 0, 'failed': True}


# The 14 craftable effects. Each: tier, charges, effect, and its OWN upgrade +
# drawback (chosen first when you earn upgrades/drawbacks for that fabrial).
EFFECTS = [
    {'key': 'bindrial_area', 'name': 'Bindrial (Area)', 'tier': 1, 'charges': 3,
     'effect': "Spend 1 charge: everything but you within 5 ft becomes Immobilized; 1 charge at the start of each of your turns while active.",
     'upgrade': "Affects everything within 10 ft.", 'drawback': "You're Immobilized by it too."},
    {'key': 'bindrial_self', 'name': 'Bindrial (Self)', 'tier': 1, 'charges': 5,
     'effect': "Spend 1 charge: climb any surface for 1 round (not Slowed); stay in place without an extra charge. At 0 charges you fall.",
     'upgrade': "Agility vs Physical to Immobilize a target for 1 round (1 charge).", 'drawback': "You're Slowed while climbing this way."},
    {'key': 'compressor', 'name': 'Compressor', 'tier': 1, 'charges': 3,
     'effect': "Agility vs Physical in reach; on a success spend N charges to make the target Slowed + disadvantage on physical tests for N rounds.",
     'upgrade': "Target is Immobilized instead of Slowed.", 'drawback': "While Slowed this way the target's Physical defense +2."},
    {'key': 'cremrial', 'name': 'Cremrial', 'tier': 1, 'charges': 5,
     'effect': "Spend 1 charge: turn a Medium stone area to soft clay for a turn; enemies must Avoid Danger or be Immobilized (DC 15 Athletics to escape).",
     'upgrade': "Affects a Large area instead.", 'drawback': "On a Complication near it the GM can make you sink + be Immobilized."},
    {'key': 'cultivator', 'name': 'Cultivator', 'tier': 1, 'charges': 3,
     'effect': "Spend 1 charge: grow touched plants to Medium size (cover, barriers, climbing).",
     'upgrade': "Grow plants to Large size.", 'drawback': "On a Complication near an affected plant the GM can Restrain you (DC 12 Athletics)."},
    {'key': 'accelerator', 'name': 'Accelerator', 'tier': 2, 'charges': 3,
     'effect': "Spend 1 charge: move up to your movement rate in a straight line.",
     'upgrade': "You needn't move in a straight line.", 'drawback': "DC 15 Agility each use or fall Prone at the end."},
    {'key': 'armor_augmenter', 'name': 'Armor Augmenter', 'tier': 2, 'charges': 2,
     'effect': "Attached to one non-Invested armor; spend 1 charge to raise its Deflect by 2 until the end of the scene.",
     'upgrade': "Spend an extra charge to also +1 Physical defense until end of scene.", 'drawback': "The armor gains Cumbersome [3] while active."},
    {'key': 'ascender', 'name': 'Ascender', 'tier': 2, 'charges': 5,
     'effect': "Spend 1 charge: flying rate 30 ft until end of your next turn; hover/lower for free; fall if it hits 0 charges aloft.",
     'upgrade': "Can also push a target 30 ft (Agility vs Physical, 1 charge).", 'drawback': "+1,000 mk counterweights that must be reset once per day of use."},
    {'key': 'drainer', 'name': 'Drainer', 'tier': 2, 'charges': 5,
     'effect': "Touch an Invested/infused target: it loses 1 Investiture/charge and this gains 1 charge. Empty via tuning fork, a Radiant, or 5 days.",
     'upgrade': "Drains 2 Investiture instead of 1.", 'drawback': "On a Complication near another Invested thing the GM can drain 1 Investiture."},
    {'key': 'liferial', 'name': 'Liferial', 'tier': 2, 'charges': 3,
     'effect': "Spend 1 charge: heal yourself or a target in reach 1d6 health.",
     'upgrade': "Spend 1 charge to remove an injury (with or instead of healing).", 'drawback': "Activation takes longer (one more action)."},
    {'key': 'lightrial', 'name': 'Lightrial', 'tier': 2, 'charges': 3,
     'effect': "Deception vs Cognitive within 30 ft; on success spend 1 charge to drain 3 focus (1 on failure). At 0 focus the target is Disoriented.",
     'upgrade': "Also deals 1d6 vital damage.", 'drawback': "The GM can make you Disoriented."},
    {'key': 'painrial', 'name': 'Painrial', 'tier': 2, 'charges': 3,
     'effect': "Melee weapon (Light Weaponry), 1d6 vital; on a hit spend 1 charge to add your skill modifier to damage again.",
     'upgrade': "Before taking damage, react to reduce it by 1d6 and regain 1 charge.", 'drawback': "The GM can make it lose a charge + deal you 1d6 vital."},
    {'key': 'projectile', 'name': 'Projectile', 'tier': 2, 'charges': 5,
     'effect': "Spend 1 charge: ranged [30/120] weapon (Light Weaponry, Offhand), 1d10 impact.",
     'upgrade': "Spend an extra charge to hit two targets.", 'drawback': "Has the Loaded [1] trait."},
    {'key': 'disruptor', 'name': 'Disruptor', 'tier': 3, 'charges': 4,
     'effect': "Spend 1 charge: destroy an unattended Small object (Agility vs Physical if held). Not on Invested/infused objects.",
     'upgrade': "Also vs characters: Discipline vs Spiritual, 2 charges, 2d6 spirit.", 'drawback': "Only unattended objects."},
    {'key': 'surge_fabrial', 'name': 'Surge Fabrial', 'tier': 4, 'charges': 4,
     'effect': "Choose one Radiant surge; activate it (its normal rules), spending charges in place of Investiture (count as 1 rank if you have none).",
     'upgrade': "(GM's choice for this experimental device).", 'drawback': "(GM's choice for this experimental device)."},
]

# General d8 upgrade/drawback table (chosen when the per-effect one doesn't fit).
GENERAL_UPGRADES = [
    'Amplified — attacks with it gain an advantage.',
    'Reliable — once per scene, ignore the first Complication on a test with it.',
    'Fine-Tuned — when you use it, regain 1d4 focus.',
    'Efficient — when you roll an Opportunity near it, regain 1 charge.',
    'Higher Capacity — increase its maximum charges by 1.',
    'Long Ranged — its range/radius is doubled.',
    'Faster — increase the effect’s movement rate by 50%.',
    'Greater Damage — increase damage dealt/healed by one die size.',
]
GENERAL_DRAWBACKS = [
    'Diminished — attacks with it gain a disadvantage.',
    'Delicate — the GM can deactivate it until repaired (DC 15 Crafting).',
    'Dangerous — the GM can make it deal 1d6 energy to you + everyone within 5 ft.',
    'Inefficient — the GM can make it expend an extra charge.',
    'Lower Capacity — decrease its maximum charges by 1.',
    'Short Ranged — its range/radius is halved.',
    'Slower — decrease the effect’s movement rate by 50%.',
    'Lesser Damage — decrease damage dealt/healed by one die size.',
]

ADVANCED_FEATURES = [
    'Expanded Capacity — increase maximum charges by 3.',
    'Wide Area — spend 2 focus to affect everyone within 5 ft of the target.',
    'Security Lock — usable only by those who know its secret mechanism.',
    'Quick Activation — once per scene, spend 1 focus to reduce its action cost.',
    'Timed Activation — set an internal timer with the Ready action.',
]


def effects() -> list:
    return EFFECTS


def effect(key) -> dict | None:
    return next((e for e in EFFECTS if e['key'] == key), None)
