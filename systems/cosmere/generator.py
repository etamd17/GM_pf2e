"""Cosmere (Stormlight) GM generators -- random tables for at-the-table prep.

Mirrors the PF2e generator pattern: each ``gen_*`` returns a small HTML snippet,
and ``GENERATORS`` maps a type key -> (label, fn) for the route + reroll API.
Content is Rosharan-flavored (Stormlight Archive); it's GM color, not canon
mechanics, so it's free to be evocative. No app/Foundry dependency.
"""
from __future__ import annotations

import random

# ── Names by Rosharan culture ──────────────────────────────────────────────
# Recognizably-flavored given names + lighteyed/family names per culture.
NAMES = {
    'Alethi': {
        'male': ['Kaladin', 'Dalinar', 'Adolin', 'Renarin', 'Elhokar', 'Teleb', 'Moash',
                 'Aladar', 'Hatham', 'Bethab', 'Teft', 'Skar', 'Drehy', 'Leyten', 'Bisig',
                 'Cael', 'Beld', 'Naroh', 'Hobber', 'Torol'],
        'female': ['Navani', 'Jasnah', 'Aesudan', 'Ialai', 'Danlan', 'Malasha', 'Hashal',
                   'Tarah', 'Relana', 'Minali', 'Khard'],
        'family': ['Kholin', 'Sadeas', 'Aladar', 'Roion', 'Hatham', 'Bethab', 'Thanadal',
                   'Vamah', 'Ruthar', 'Khal'],
    },
    'Veden': {
        'male': ['Helaran', 'Balat', 'Wikim', 'Jushu', 'Taravangian', 'Redin'],
        'female': ['Shallan', 'Eylita', 'Malise', 'Adrotagia', 'Laral'],
        'family': ['Davar', 'Hasweth', 'Valum', 'Saemeic'],
    },
    'Herdazian': {
        'male': ['Lopen', 'Punio', 'Huio', 'Rod', 'Triel'],
        'female': ['Tashni', 'Cabra', 'Esme'],
        'family': ['Herda'],
    },
    'Thaylen': {
        'male': ['Vstim', 'Kylrm', 'Tozbek', 'Yalb', 'Kdralk', 'Mraize'],
        'female': ['Rysn', 'Yokska', 'Babsk'],
        'family': ['Vendis'],
    },
    'Azish': {
        'male': ['Gawx', 'Yanagawn', 'Mihahn', 'Kadasixes'],
        'female': ['Noura', 'Dalksi', 'Mecira'],
        'family': ['Vizier', 'Scion'],
    },
    'Singer': {
        'male': ['Rlain', 'Demid', 'Thude', 'Klade', 'Gavashaw'],
        'female': ['Eshonai', 'Venli', 'Bila', 'Mathana'],
        'family': [],
    },
    'Unkalaki': {  # Horneaters
        'male': ['Numuhukumakiaki’aialunamor', 'Theylendaughter', 'Gadol'],
        'female': ['Cord', 'Song', 'Star'],
        'family': [],
    },
    'Shin': {
        'male': ['Szeth', 'Vallano', 'Neturo'],
        'female': ['Elhe', 'Shubreth'],
        'family': [],
    },
}
_CULTURES = list(NAMES.keys())

# ── NPC building blocks ─────────────────────────────────────────────────────
NPC_ROLES = [
    'a darkeyed foot soldier', 'a lighteyed officer', 'an ardent of the Vorin church',
    'a caravan merchant', 'a winehouse keeper', 'a Soulcaster’s assistant',
    'a bridgeman', 'a stormwarden charting the highstorms', 'a spanreed clerk',
    'a market spice-trader', 'a deserter turned sellsword', 'a scribe and stormwarden',
    'a chasm scavenger after a storm', 'a fabrial artifabrian', 'a slave with a glyphward brand',
    'a parshman laborer (newly awakened)', 'a Thaylen ship captain', 'an Azish bureaucrat',
]
NPC_TRAITS = [
    'gruff but unshakably loyal', 'quick with a joke, slow to trust', 'haunted by a past storm',
    'proud of a minor noble lineage', 'devout, quoting The Way of Kings', 'sharp-eyed and calculating',
    'nervous, always glancing skyward', 'warm and motherly', 'bitter about a broken oath',
    'ambitious past their station', 'soft-spoken, with steel underneath', 'reckless and storm-brave',
]
NPC_HOOKS = [
    'owes a dangerous debt to a wealthy lighteyes', 'secretly hears a spren no one else can see',
    'is hiding from a writ of execution', 'carries a message they were told never to read',
    'lost family in the chasms and wants revenge', 'is quietly siphoning Stormlight for a cause',
    'knows where a Shardblade was buried', 'is not who they claim to be',
    'serves a master in the Diagram', 'fled a Singer warform and won’t say why',
]

# ── Weather / highstorms ────────────────────────────────────────────────────
WEATHER = [
    ('Highstorm &mdash; <b>violent</b>', 'The stormwall is enormous and close; anything not lashed down is lost. Stormlight will be abundant after &mdash; if anyone survives the open.'),
    ('Highstorm &mdash; <b>strong</b>', 'The stormwall hits within the hour. Take shelter on the leeward side and charge your spheres; the riddens after will be calm.'),
    ('Highstorm &mdash; <b>weak</b>', 'A lesser storm, easily weathered behind stone. Brief but enough to renew Stormlight in exposed gems.'),
    ('The <b>riddens</b>', 'The gentle tail of a passing highstorm: warm rain, drifting rainspren, and the world washed clean.'),
    ('The <b>Weeping</b>', 'Weeks of grey, windless drizzle. No highstorms, no Stormlight renewal &mdash; ration your spheres. Somewhere, it is the day of the Light Day.'),
    ('Clear between storms', 'Crem-dusted skies and a steady highstorm wind from the east. Lifespren bob over the rockbuds.'),
    ('The <b>Everstorm</b>', 'A wall of red lightning rolling the wrong way &mdash; west to east. It wakes the Singers to forms of power and infuses spheres with a corrupted Light.'),
    ('Stormcharged dawn', 'A highstorm passed in the night. Spheres glow bright in every pouch and the air hums with renewal.'),
]

# ── Spheres (Rosharan currency) ─────────────────────────────────────────────
_SPHERE_GEMS = ['diamond', 'garnet', 'ruby', 'sapphire', 'smokestone', 'emerald']
_SPHERE_DENOM = [('chip', 1), ('mark', 5), ('broam', 20)]

# ── Loot / fabrials / finds ─────────────────────────────────────────────────
LOOT = [
    'a <b>warmth fabrial</b> &mdash; a ruby that radiates gentle heat while infused',
    'a <b>pairing fabrial</b> &mdash; two conjoined rubies; move one and its twin mirrors the motion',
    'a <b>painrial</b> &mdash; a fabrial that stores and inflicts pain',
    'a <b>half-shard shield</b> &mdash; nearly unbreakable while its fabrial holds Stormlight',
    'a fistful of uncut <b>gemstones</b>, worth a small fortune if Soulcast-grade',
    'a stormwarden’s <b>highstorm almanac</b>, hand-charted and very current',
    'a soldier’s spanreed, its ruby still paired to someone far away',
    'a glyphward of protection, freshly painted and still smelling of paint',
    'a Soulcaster’s gem, cracked and nearly spent',
    'an Alethi officer’s knots of rank &mdash; and the writ that grants them',
    'a sphere pouch fat with <b>Stormlight</b>, glowing through the cloth',
    'a sled of Soulcast grain stamped with a warcamp seal',
    'a rare book of poetry in the women’s script',
    'a chasmfiend carapace shard, prized by artifabrians',
]
RARE_LOOT = [
    'a dead <b>Shardblade</b> &mdash; summon it in ten heartbeats; it kills souls, not bodies',
    'a suit of <b>Shardplate</b>, cracked at one shoulder and leaking Stormlight',
    'a living spren in a gem, bound against its will',
]

# ── Locations ────────────────────────────────────────────────────────────────
LOCATIONS = [
    ('An Alethi warcamp', 'Ringed by Soulcast barracks and the smell of curry; lighteyes scheme over wine while bridge crews drill.'),
    ('The Shattered Plains', 'A maze of wind-scoured plateaus and bottomless chasms, bridged by the ambitious and the doomed.'),
    ('A chasm bottom', 'Damp, choked with greatshell bones and chasmfiend pupae; the only way out is up before the storm.'),
    ('A Thaylen port city', 'Tiered against the cliffs, banks and counting-houses everywhere, eyebrows worn long and braided with gems.'),
    ('An Azish tiered city', 'Bureaucracy as architecture &mdash; nothing happens without the right stamp, in triplicate.'),
    ('A Horneater peak', 'Cold stone and hot stew; the Unkalaki guard a pure lake that is a Shardpool to the Cognitive Realm.'),
    ('A stormwall-scarred town', 'Half the buildings hunch away from the east; rockbuds bloom in the gutters between storms.'),
    ('A roadside winehouse', 'Spheres for light, a hearth of Soulcast logs, and rumor traded as freely as the orange wine.'),
    ('Shadesmar', 'The Cognitive Realm: a sea of glass beads under a black sky with tiny suns, spren walking the obsidian shores.'),
    ('A Soulcaster’s workshop', 'Reeking gemstones, a wary ardent, and the faint nausea of matter unmade and remade.'),
    ('A devotary of the Vorin church', 'Burning prayers, glyphwards, and ardents who will tutor anyone &mdash; for the right Calling.'),
    ('A caravan camp on the trade routes', 'Chull wagons circled against the wind, axehound sentries, and a stormwarden watching the east.'),
]

# ── Plot hooks / complications ───────────────────────────────────────────────
HOOKS = [
    'A spanreed goes silent mid-message &mdash; the last words were a plea for help.',
    'A highstorm arrives hours early, trapping the party with someone they do not trust.',
    'A caravan never reached the next town; only its chulls wandered back, unharmed.',
    'A Soulcaster has run dry, and a city of thousands has three days of grain left.',
    'A child claims a spren is whispering to them &mdash; and the spren is afraid.',
    'A dead Shardblade surfaces on the black market; three powers already want it.',
    'A red-lightning storm woke the local parshmen, and they have simply&hellip; walked away.',
    'An ardent begs the party to recover a heretical book before the church burns it.',
    'A lighteyed duel of honor is rigged, and the loser is meant to be someone the party loves.',
    'Someone is murdering stormwardens the night before each highstorm.',
    'A Thaylen ship offers passage &mdash; for a favor that smells of the Ghostbloods.',
    'A glyphward bought at market turns out to be a map drawn in the women’s script.',
]

# ── Tavern / road rumors ─────────────────────────────────────────────────────
RUMORS = [
    'They say a chasmfiend was seen far from the Plains, hunting by night.',
    'A man in white killed a king and walked away across the stone, untouched.',
    'The Weeping ran long this year &mdash; an ill omen, the ardents whisper.',
    'A lighteyes is paying in broams for anyone who has dreamed of a wall of red light.',
    'There’s a winehouse where the keeper never charges &mdash; she only asks for stories.',
    'Soldiers swear the bridge crews have a man who cannot be killed.',
    'A spren the size of a building was seen standing in a storm, watching.',
    'Someone is buying up every spent Soulcaster gem in the warcamps. Nobody knows why.',
    'The Heralds have returned, they say &mdash; or madmen claiming to be them.',
    'A Horneater came down from the peaks asking the way to Shadesmar.',
]

# ── Scene / sensory dressing ──────────────────────────────────────────────────
DRESSING = [
    'Lifespren drift like green motes over the rockbuds, scattering when touched.',
    'The wind carries the rhythm of a distant Singer, attuned to something wary.',
    'Crem dries in pale streaks down every eastward wall, soft as wet clay before a storm.',
    'A rockbud cracks open as you pass, vines tasting the air for the next storm.',
    'Spheres glow in a beggar’s cup &mdash; too much Stormlight for a beggar to have honestly.',
    'Fearspren wriggle up from the stone, violet and writhing, around someone nearby.',
    'An axehound lifts its head, all six legs tensing toward the east.',
    'The distant boom of a chasmfiend’s call rolls up out of the plateaus.',
    'Gloryspren spiral golden around a child who just won a game of breakneck.',
    'Stormlight leaks visibly from a cracked gem, curling away like luminous smoke.',
    'Flamespren dance in the hearth, taking the shapes of the things spoken near them.',
    'The light shifts violet and wrong for a heartbeat &mdash; and is ordinary again.',
]


# ── helpers + generators ──────────────────────────────────────────────────────
def _muted(s):
    return '<span class="muted">%s</span>' % s


def gen_name(culture=None):
    culture = culture if culture in NAMES else random.choice(_CULTURES)
    pool = NAMES[culture]
    gender = random.choice(['male', 'female'])
    given = random.choice(pool[gender] or pool['male'])
    fam = pool.get('family') or []
    # Lighteyed cultures sometimes carry a family/house name.
    if fam and random.random() < 0.5:
        given = '%s %s' % (given, random.choice(fam))
    return '<b>%s</b> %s' % (given, _muted('&middot; %s, %s' % (culture, gender)))


def gen_npc():
    culture = random.choice(_CULTURES)
    pool = NAMES[culture]
    gender = random.choice(['male', 'female'])
    name = random.choice(pool[gender] or pool['male'])
    return ('<b>%s</b> %s<br>%s.<br>%s' % (
        name, _muted('&middot; %s, %s' % (culture, random.choice(NPC_ROLES))),
        random.choice(NPC_TRAITS).capitalize(),
        _muted('Hook: ' + random.choice(NPC_HOOKS) + '.')))


def gen_weather():
    title, body = random.choice(WEATHER)
    return '%s<br>%s' % (title, _muted(body))


def gen_spheres():
    bits = []
    total = 0
    for _ in range(random.randint(2, 3)):
        gem = random.choice(_SPHERE_GEMS)
        denom, val = random.choice(_SPHERE_DENOM)
        n = random.randint(1, 9)
        total += n * val
        bits.append('%d %s %s%s' % (n, gem, denom, '' if n == 1 else 's'))
    return ('A pouch of <b>%s</b>.<br>%s' % (
        ', '.join(bits), _muted('Roughly %d clearchips’ worth, if the gems were spent for their Light.' % total)))


def gen_loot():
    if random.random() < 0.12:
        return random.choice(RARE_LOOT) + '.<br>' + _muted('A rare, dangerous find &mdash; everyone will want it.')
    return random.choice(LOOT).capitalize() + '.'


def gen_location():
    title, body = random.choice(LOCATIONS)
    return '<b>%s</b><br>%s' % (title, _muted(body))


def gen_hook():
    return random.choice(HOOKS)


def gen_rumor():
    return _muted('&ldquo;') + random.choice(RUMORS) + _muted('&rdquo;')


def gen_dressing():
    return random.choice(DRESSING)


# type key -> (display label, generator fn). Order = display order.
GENERATORS = {
    'name':     ('Name',          gen_name),
    'npc':      ('NPC',           gen_npc),
    'weather':  ('Highstorm / Weather', gen_weather),
    'spheres':  ('Spheres',       gen_spheres),
    'loot':     ('Loot & Fabrials', gen_loot),
    'location': ('Location',      gen_location),
    'hook':     ('Plot Hook',     gen_hook),
    'rumor':    ('Rumor',         gen_rumor),
    'dressing': ('Scene Dressing', gen_dressing),
}


def generate(gtype):
    """Return a fresh HTML snippet for a generator type (or '' if unknown)."""
    entry = GENERATORS.get(gtype)
    return entry[1]() if entry else ''
