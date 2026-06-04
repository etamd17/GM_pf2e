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

# Ancestry bonus-talent rules (rulebook Ch.1/2; not in the Foundry data).
ANCESTRY_INFO = {
    'Human':  {'bonus': 'A bonus heroic-path talent at the start of each tier (levels 1, 6, 11, 16, 21).'},
    'Singer': {'bonus': 'Change Form (key talent) plus one connected form talent; a bonus talent at each tier start.'},
}


def path_info(key):
    return PATH_INFO.get((key or '').lower())


def path_start_skill(key):
    p = path_info(key)
    return p['start_skill'] if p else None


def path_key_talent(key):
    p = path_info(key)
    return {'id': p['key_talent_id'], 'name': p['key_talent']} if p else None
