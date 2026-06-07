"""Canon non-Radiant creation grants (Stormlight rulebook Ch.1/2/4), derived
from the Foundry cosmere-rpg data and cross-referenced with the core rulebook.

- Each heroic PATH grants a KEY TALENT (its talent-tree root — the talent whose
  Foundry ``prerequisites`` are empty) and a STARTING SKILL (+1 rank). Both are
  taken verbatim from the Foundry path/talent docs.
- Each CULTURE grants its eponymous cultural EXPERTISE; at creation you choose
  TWO cultural expertises, then Intellect more general ones (Ch.1/2).
- ANCESTRY bonus talents (Human's heroic-path talents; the Singer's Change Form)
  are rulebook-only — not encoded in the Foundry data — so they're described
  here for the builder.
"""
from __future__ import annotations

# path id -> key talent (_id + name) and starting skill code.
PATH_INFO = {
    'agent':   {'name': 'Agent',   'key_talent_id': 'ScqSKbkewDAfSizP', 'key_talent': 'Opportunist',      'start_skill': 'ins'},
    'envoy':   {'name': 'Envoy',   'key_talent_id': 'l4wRQT2MH0SsxmRQ', 'key_talent': 'Rousing Presence', 'start_skill': 'dis'},
    'hunter':  {'name': 'Hunter',  'key_talent_id': '3nfGn0BOeLq5qgEr', 'key_talent': 'Seek Quarry',      'start_skill': 'prc'},
    'leader':  {'name': 'Leader',  'key_talent_id': 'UEaOm4P2UFcrP1Z8', 'key_talent': 'Decisive Command', 'start_skill': 'lea'},
    'scholar': {'name': 'Scholar', 'key_talent_id': '8FYBJZC8PBjneOim', 'key_talent': 'Erudition',        'start_skill': 'lor'},
    'warrior': {'name': 'Warrior', 'key_talent_id': '8mJC2HnsN3OQ2J20', 'key_talent': 'Vigilant Stance',  'start_skill': 'ath'},
}

# The six Rosharan cultures (each grants its eponymous cultural expertise).
CULTURES = ('Alethi', 'Azish', 'Herdazian', 'Thaylen', 'Unkalaki', 'Veden')

# A short, builder-facing note per culture. Each culture grants its own cultural
# Expertise (the homeland's language + customs); the flavor helps a player pick.
CULTURE_INFO = {
    'Alethi':    "Roshar's dominant warrior society — rigid dahn/nahn ranks and a martial tradition.",
    'Azish':     'A vast bureaucratic empire that prizes law, scribing, and earned office.',
    'Herdazian': 'Scrappy and resourceful — chull-drovers and crem-stone craftspeople.',
    'Thaylen':   'Master merchants and sailors, marked by their long white eyebrows.',
    'Unkalaki':  'The Horneater peaks folk — proud cooks, climbers, and keepers of old lore.',
    'Veden':     'Vorin rivals of Alethkar, known for fierce pride and red-tinged hair.',
}

# Ancestry bonus-talent rules (rulebook Ch.1/2; not in the Foundry data).
ANCESTRY_INFO = {
    'Human':  {'bonus': 'A bonus heroic-path talent at the start of each tier (levels 1, 6, 11, 16, 21).'},
    'Singer': {'bonus': 'Change Form (key talent) plus one connected form talent; a bonus talent at each tier start.'},
}


# Singer forms (rulebook Ch.2). While assumed, a form grants stat changes
# (these can raise stats above the normal maximum). At level 1 a Singer gains
# Change Form plus one connected Forms talent (a pair), then learns more forms
# via the Singer talent tree as they level.
SINGER_CHANGE_FORM = {'id': 'singer-change-form', 'name': 'Change Form (Singer Key)'}
SINGER_FORMS = {
    'dullform':      {'name': 'Dullform',      'group': 'Base',        'attrs': {},                 'deflect': 0, 'focus': 0, 'note': 'The default, unremarkable form.'},
    'artform':       {'name': 'Artform',       'group': 'Finesse',     'attrs': {'awa': 1},         'deflect': 0, 'focus': 0, 'note': 'Painting & Music expertises; advantage on Crafting and entertaining.'},
    'nimbleform':    {'name': 'Nimbleform',    'group': 'Finesse',     'attrs': {'spd': 1},         'deflect': 0, 'focus': 2, 'note': 'Agile and focused.'},
    'mediationform': {'name': 'Mediationform', 'group': 'Wisdom',      'attrs': {'pre': 1},         'deflect': 0, 'focus': 0, 'note': 'Aid without spending focus.'},
    'scholarform':   {'name': 'Scholarform',   'group': 'Wisdom',      'attrs': {'int': 1},         'deflect': 0, 'focus': 0, 'note': 'A temporary expertise and a cognitive skill rank.'},
    'warform':       {'name': 'Warform',       'group': 'Resolve',     'attrs': {'str': 1},         'deflect': 1, 'focus': 0, 'note': 'Carapace armor; enhanced jumping.'},
    'workform':      {'name': 'Workform',      'group': 'Resolve',     'attrs': {'wil': 1},         'deflect': 0, 'focus': 0, 'note': 'Ignore Exhausted; disguise as a parshman.'},
    'direform':      {'name': 'Direform',      'group': 'Destruction', 'attrs': {'str': 2},         'deflect': 2, 'focus': 0, 'note': 'Reactive Strikes can Grapple.'},
    'stormform':     {'name': 'Stormform',     'group': 'Destruction', 'attrs': {'str': 1, 'spd': 1}, 'deflect': 1, 'focus': 0, 'note': 'Unleash Lightning (ranged energy attack).'},
    'envoyform':     {'name': 'Envoyform',     'group': 'Expansion',   'attrs': {'int': 1, 'pre': 1}, 'deflect': 0, 'focus': 0, 'note': 'Know all languages; insight into intentions.'},
    'relayform':     {'name': 'Relayform',     'group': 'Expansion',   'attrs': {'spd': 2},         'deflect': 0, 'focus': 0, 'note': 'Ignore Slowed; advantage on Agility/Stealth/Thievery.'},
    'decayform':     {'name': 'Decayform',     'group': 'Mystery',     'attrs': {'wil': 2},         'deflect': 0, 'focus': 0, 'note': 'Prevent a target from recovering health or focus.'},
    'nightform':     {'name': 'Nightform',     'group': 'Mystery',     'attrs': {'awa': 1, 'int': 1}, 'deflect': 0, 'focus': 2, 'note': 'Preroll d20s to replace rolls.'},
}


# Starting kits (rulebook Ch.7, chosen at creation step 6). Armor/weapon names
# map to the item catalog where possible; equipment + marks are narrative and
# shown as a note. Weapon choices default to a sensible catalog option.
STARTING_KITS = {
    'academic':   {'name': 'Academic',   'armor': 'Uniform', 'weapons': ['Staff'],
                   'equipment': 'Backpack, writing supplies, a reference book, vials, and a dose of weak poison.',
                   'marks': '3d12', 'bonus': 'Gain the Literature expertise (free).'},
    'artisan':    {'name': 'Artisan',    'armor': 'Leather', 'weapons': ['Hammer'],
                   'equipment': 'A chest of tools, surgical supplies, and a musical instrument.',
                   'marks': '4d8', 'bonus': ''},
    'military':   {'name': 'Military',   'armor': 'Chain', 'weapons': ['Longsword', 'Shortspear'],
                   'equipment': 'A uniform, waterskin, whetstone, blanket, and 10 days of rations.',
                   'marks': '2d6', 'bonus': ''},
    'courtier':   {'name': 'Courtier',   'armor': None, 'weapons': ['Sidesword'],
                   'equipment': 'Fine clothing and a bottle of violet wine.',
                   'marks': '4d20', 'bonus': 'A noble patron supports your standard of living (Connection).'},
    'prisoner':   {'name': 'Prisoner',   'armor': None, 'weapons': [],
                   'equipment': 'Manacles and ragged clothing.',
                   'marks': '0', 'bonus': 'A Radiant spren has begun bonding you (two First-Ideal milestones already marked).'},
    'underworld': {'name': 'Underworld', 'armor': 'Leather', 'weapons': ['Knife', 'Shortspear'],
                   'equipment': 'A crowbar, lockpick, 50 ft of rope, and an oil lantern.',
                   'marks': '1d20', 'bonus': ''},
}


def singer_form(key):
    return SINGER_FORMS.get((key or '').lower())


def path_info(key):
    return PATH_INFO.get((key or '').lower())


def path_start_skill(key):
    p = path_info(key)
    return p['start_skill'] if p else None


def path_key_talent(key):
    p = path_info(key)
    return {'id': p['key_talent_id'], 'name': p['key_talent']} if p else None
