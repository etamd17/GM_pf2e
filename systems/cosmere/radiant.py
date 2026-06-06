"""Canon Radiant / Surgebinding data (Stormlight core rulebook Ch.5-6).

The order -> spren / surges / philosophy mapping is VERBATIM from the "Radiant
Orders" table (Ch.5). The Ideals framework, Stormlight actions, and surge
scaling are taken directly from the rules text; surge one-line descriptions are
faithful summaries of each surge's fundamental force.

Becoming Radiant: choose a Radiant order (a First Ideal key talent) at level 2+.
That grants Investiture (Stormlight) and the three Stormlight actions. Swearing
the order's First Ideal then unlocks the order's two surge skills (a free rank
in each). All ten orders have five Ideals; the Fifth is unreachable in play.
"""
from __future__ import annotations

# The First Ideal is shared by every order.
FIRST_IDEAL = ("Life before death. Strength before weakness. "
               "Journey before destination.")
RADIANT_MIN_LEVEL = 2     # a First Ideal key talent requires level 2+
IDEAL_COUNT = 5           # five Ideals per order; the 5th is unreachable in play

# The ten surges, each governing a fundamental force. Codes match SKILL_ATTR /
# the surge skills. Descriptions are concise, canon-faithful summaries.
SURGES = {
    'adh': {'name': 'Adhesion',       'desc': 'Bind objects together; create pressure and vacuum (Full Lashings).'},
    'grv': {'name': 'Gravitation',    'desc': 'Change the direction and strength of gravity (Basic Lashings).'},
    'dvs': {'name': 'Division',        'desc': 'Cause decay, destruction, and burning.'},
    'abr': {'name': 'Abrasion',        'desc': 'Control friction, making surfaces slick or sticky.'},
    'prg': {'name': 'Progression',     'desc': 'Accelerate growth and healing in living things.'},
    'ill': {'name': 'Illumination',    'desc': 'Craft illusions of light, sound, and the other senses.'},
    'trs': {'name': 'Transformation',  'desc': 'Soulcast, changing one substance into another.'},
    'trp': {'name': 'Transportation',  'desc': 'Travel through Shadesmar, the Cognitive Realm.'},
    'chs': {'name': 'Cohesion',        'desc': 'Manipulate the molecular bonds of solid matter.'},
    'tsn': {'name': 'Tension',         'desc': 'Make flexible objects rigid and unyielding.'},
}

# Nine PLAYER-PLAYABLE orders, verbatim from the Radiant Orders table (Ch.5).
RADIANT_ORDERS = {
    'windrunners':   {'name': 'Windrunners',   'spren': 'Honorspren',      'surges': ('adh', 'grv'), 'philosophy': 'Protect the innocent and the defenseless.'},
    'skybreakers':   {'name': 'Skybreakers',   'spren': 'Highspren',       'surges': ('dvs', 'grv'), 'philosophy': 'Enforce the law and strive for justice.'},
    'dustbringers':  {'name': 'Dustbringers',  'spren': 'Ashspren',        'surges': ('abr', 'dvs'), 'philosophy': 'Great power requires strong discipline.'},
    'edgedancers':   {'name': 'Edgedancers',   'spren': 'Cultivationspren','surges': ('abr', 'prg'), 'philosophy': 'Remember and serve those who others forget.'},
    'truthwatchers': {'name': 'Truthwatchers', 'spren': 'Mistspren',       'surges': ('ill', 'prg'), 'philosophy': 'Search for fundamental truth and share it.'},
    'lightweavers':  {'name': 'Lightweavers',  'spren': 'Cryptic',         'surges': ('ill', 'trs'), 'philosophy': 'Separate truth from lies.'},
    'elsecallers':   {'name': 'Elsecallers',   'spren': 'Inkspren',        'surges': ('trs', 'trp'), 'philosophy': 'Strive to reach your true potential.'},
    'willshapers':   {'name': 'Willshapers',   'spren': 'Lightspren',      'surges': ('chs', 'trp'), 'philosophy': 'Seek freedom and choice for all peoples.'},
    'stonewards':    {'name': 'Stonewards',    'spren': 'Peakspren',       'surges': ('chs', 'tsn'), 'philosophy': 'Be the support on which others can depend.'},
}

# The tenth order — not available to player characters (kept for reference).
BONDSMITHS = {'name': 'Bondsmiths', 'spren': 'Unique spren', 'surges': ('adh', 'tsn'),
              'philosophy': 'Unite before you divide, and strive for peace before engaging in war.',
              'playable': False}

# Granted by a First Ideal talent (rulebook Ch.5). Embedded onto a Radiant's
# sheet as actions.
STORMLIGHT_ACTIONS = (
    {'name': 'Breathe Stormlight', 'description': 'Draw Stormlight from infused spheres within 5 feet to recover Investiture up to your maximum.'},
    {'name': 'Enhance',            'description': 'Spend 1 Investiture to become Enhanced [Strength +1] and Enhanced [Speed +1] until the end of your next turn.'},
    {'name': 'Regenerate',         'description': 'Spend 1 Investiture to recover health equal to 1d6 + your current tier.'},
)

# Surge scaling by rank (die size / max effect size). Rulebook Ch.6.
SURGE_SCALING = {1: 'd4 / Small', 2: 'd6 / Medium', 3: 'd8 / Large',
                 4: 'd10 / Huge', 5: 'd12 / Gargantuan'}


# UI accent colors per order (thematic, not canon mechanics) for character cards.
ORDER_COLORS = {
    'windrunners': '#38bdf8',    # sky blue
    'skybreakers': '#818cf8',    # indigo
    'dustbringers': '#ef4444',   # ember red
    'edgedancers': '#34d399',    # cultivation green
    'truthwatchers': '#2dd4bf',  # mist teal
    'lightweavers': '#c084fc',   # cryptic violet
    'elsecallers': '#60a5fa',    # ink blue
    'willshapers': '#fb923c',    # freedom orange
    'stonewards': '#d6a86a',     # peak stone
}
DEFAULT_ACCENT = '#5fa8e0'       # Stormlight blue (non-Radiant); matches --storm-300


def order_color(key) -> str:
    return ORDER_COLORS.get((key or '').lower(), DEFAULT_ACCENT)


def order_keys() -> tuple:
    return tuple(RADIANT_ORDERS.keys())


def order(key):
    return RADIANT_ORDERS.get((key or '').lower())


def surge_name(code) -> str:
    return SURGES.get(code, {}).get('name', code)
