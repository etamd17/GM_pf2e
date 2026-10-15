"""Original Scadrial-flavored content for the Cosmere GM generators.

The short entries here were written for this project. Canon setting terms are
used as signposts, but no rules text or prose is sourced from game data.
"""
from __future__ import annotations

import random


LABELS = {
    'name': 'Name',
    'npc': 'NPC',
    'weather': 'Mists / Weather',
    'spheres': 'Coins',
    'loot': 'Loot & Metals',
    'location': 'Location',
    'hook': 'Plot Hook',
    'rumor': 'Rumor',
    'dressing': 'Scene Dressing',
}


NAMES_ERA_1 = {
    'Noble House': {
        'male': ['Arven', 'Bastel', 'Corden', 'Demer', 'Elric', 'Fenton',
                 'Gavren', 'Harl', 'Iven', 'Jasten', 'Kellor', 'Lestib'],
        'female': ['Alene', 'Brelle', 'Casta', 'Delra', 'Elianne', 'Fara',
                   'Gienne', 'Hesta', 'Iselle', 'Jana', 'Kessra', 'Loraine'],
        'family': ['Aldren', 'Belmark', 'Cettan', 'Dorrin', 'Elariel', 'Fadrex',
                   'Garran', 'Hasting', 'Ivron', 'Jastes', 'Krell', 'Luthane'],
    },
    'Skaa': {
        'male': ['Arl', 'Bren', 'Cask', 'Drel', 'Fen', 'Gorin',
                 'Hask', 'Jem', 'Kett', 'Marn', 'Nell', 'Vesh'],
        'female': ['Ari', 'Bessa', 'Crenna', 'Delle', 'Fenna', 'Gara',
                   'Hess', 'Jori', 'Kara', 'Melle', 'Nara', 'Vena'],
        'family': [],
    },
    'Terris': {
        'male': ['Aven', 'Bannis', 'Corren', 'Dafen', 'Eris',
                 'Ferren', 'Gannis', 'Horen', 'Ivenn', 'Jorren'],
        'female': ['Alis', 'Benna', 'Corra', 'Dessa', 'Erisel',
                   'Fenna', 'Gallis', 'Hessa', 'Iria', 'Jessa'],
        'family': [],
    },
}

NAMES_ERA_2 = {
    'Elendel Basin': {
        'male': ['Alden', 'Bram', 'Claren', 'Dorr', 'Edren', 'Farris',
                 'Giles', 'Harlan', 'Iven', 'Jorren', 'Kester', 'Mileson'],
        'female': ['Ada', 'Brenna', 'Clara', 'Dessa', 'Elise', 'Faye',
                   'Gretta', 'Hollis', 'Iris', 'Jorie', 'Kendra', 'Mariel'],
        'family': ['Arden', 'Breck', 'Canton', 'Dale', 'Evers',
                   'Farrow', 'Gant', 'Hollan', 'Irons', 'Jance'],
    },
    'Roughs': {
        'male': ['Ash', 'Bennet', 'Colm', 'Dax', 'Egan', 'Ford'],
        'female': ['Belle', 'Cora', 'Dawn', 'Essie', 'Fran', 'Gwen'],
        'family': ['Barrow', 'Creel', 'Duston', 'Flint', 'Rook', 'Vale'],
    },
}

NPC_ROLES_ERA_1 = [
    'a noble house steward', 'a skaa crew boss', 'an obligator',
    'a Ministry records clerk', 'a canal barge broker', 'an ash sweeper',
    'a metal-vial mixer', 'a coin courier', 'a plantation overseer',
    'a rebel contact', 'a hazekiller', 'a noble duelist',
    'a market informant', 'a metallurgist', 'a carriage driver',
    'a Terris steward', 'a safehouse keeper', 'a garrison watch officer',
]
NPC_ROLES_ERA_2 = [
    'an Elendel constable', 'a railway detective', 'a gunsmith',
    'a newspaper reporter', 'a canal inspector', 'a bank clerk',
    'a motorcar mechanic', 'a Roughs bounty hunter', 'a university metallurgist',
    'a union organizer', 'a telegraph operator', 'a political aide',
    'a shipping agent', 'a private investigator', 'a train conductor',
    'a claims lawyer', 'a factory foreman', 'a street photographer',
]
NPC_TRAITS = [
    'careful with every word', 'cheerful under pressure',
    'certain that everyone has a price', 'loyal to an old promise',
    'restless when the mists gather', 'formal even in danger',
    'curious about forbidden metals', 'protective of the people nearby',
    'quick to notice a lie', 'proud of humble work',
    'always planning a second exit', 'soft-spoken and stubborn',
]
NPC_HOOKS_ERA_1 = [
    'owes a favor to a rival house', 'carries a message for the rebellion',
    'saw someone move through the mists without fear',
    'is hiding a pouch of stolen boxings', 'knows which canal barge has a false hold',
    'can identify a rare metal by taste', 'was ordered to betray a trusted friend',
    'keeps a forbidden family ledger', 'heard footsteps on an empty rooftop',
    'needs help moving a skaa family before dawn',
]
NPC_HOOKS_ERA_2 = [
    'has the only copy of a missing rail schedule',
    'recognizes the gun used in an unsolved robbery',
    'owes money to a Roughs gang', 'found a coded note inside a bank ledger',
    'saw a metalborn flee across the rooftops', 'is protecting a corrupt constable',
    'lost evidence during a canal chase', 'needs discreet guards for a night train',
    'suspects a factory accident was deliberate',
    'can name the buyer of a stolen shipment',
]

WEATHER = [
    ('A <b>heavy ashfall</b>', 'Dark flakes gather on roofs and turn every footstep into a trail.'),
    ('<b>Thick mists</b>', 'The streets vanish beyond a few paces, muffling voices and lamps.'),
    ('A <b>red, hazy noon</b>', 'The sun hangs dim above the smoke and the air tastes of soot.'),
    ('A brief <b>clear wind</b>', 'Fresh air cuts through the city and sends old ash spinning into alleys.'),
    ('<b>Warm rain</b>', 'Black water runs from the eaves and exposes yesterday\'s tracks.'),
    ('An <b>early mist</b>', 'Silver fog gathers before sunset and empties the market quickly.'),
    ('A <b>still night</b>', 'No breeze stirs the haze; every distant sound seems close.'),
    ('A <b>cold grey dawn</b>', 'The mists retreat slowly, leaving metal and stone beaded with water.'),
]

LOOT_ERA_1 = [
    'a leather case holding three sealed metal vials',
    'a weighted coin pouch prepared for a Coinshot',
    'a noble signet wrapped in plain cloth',
    'a canal key stamped with a Ministry mark',
    'a hazekiller baton with a hidden compartment',
    'a ledger of quiet payments between two houses',
    'a smoked-glass vial with a trace of pewter',
    'a silver hairpin shaped like a mistwraith',
    'a map of maintenance tunnels beneath a manor',
    'a packet of forged work orders',
    'a copper earring in a velvet box',
    'a set of dueling canes with lead-cored tips',
    'a pouch of iron filings and spare glass vials',
    'an obligator\'s seal with one corner filed away',
]
RARE_LOOT_ERA_1 = [
    'a bead of <b>atium</b> in a lead-lined locket',
    'an unkeyed metalmind with no record of its maker',
    'a map marking a cache beneath Kredik Shaw',
]
LOOT_ERA_2 = [
    'a compact revolver with an empty secret chamber',
    'a railway strongbox with its lock neatly removed',
    'a metal vial kit disguised as a fountain pen case',
    'a stack of banknotes marked with invisible ink',
    'a constable badge bearing a false service number',
    'a motorcar key tied to a canal district address',
    'a photographer\'s plate showing a blurred rooftop figure',
    'a telegraph slip with half the message burned away',
    'a gunsmith\'s receipt for a custom aluminum round',
    'a pocket watch whose case is a small metalmind',
    'a newspaper proof naming a prominent investor',
    'a rail pass valid under three different names',
    'a vial bandolier hidden inside a formal waistcoat',
    'a survey map of an abandoned Roughs mine',
]
RARE_LOOT_ERA_2 = [
    'a sealed case of experimental aluminum ammunition',
    'an unkeyed metalmind hidden inside a lawman\'s badge',
    'a pristine atium bead with no known provenance',
]

LOCATIONS_ERA_1 = [
    ('A Luthadel canal landing', 'Ash-dark barges unload beneath the watch of bored guards.'),
    ('A noble keep', 'Tall stone walls hide a bright ballroom and several quieter bargains.'),
    ('A skaa tenement', 'Families share thin walls, one stove, and news from every work crew.'),
    ('A Ministry archive', 'Steel cabinets divide orderly records from carefully missing ones.'),
    ('An ash-choked market', 'Canvas awnings sag while traders brush soot from their wares.'),
    ('A hidden safehouse', 'A cold cellar holds spare clothes, coded ledgers, and one narrow exit.'),
    ('A canal warehouse', 'Crates form blind corners around a locked office overlooking the water.'),
    ('A Terris steward\'s quarters', 'Everything has a place, including a letter that should not exist.'),
    ('A plantation manor', 'Formal gardens fade into fields patrolled after dark.'),
    ('A metalworks', 'Heat and hammer blows cover conversations that must stay private.'),
    ('A rooftop above the mists', 'Chimneys and spires become islands over a silver sea.'),
]
LOCATIONS_ERA_2 = [
    ('Elendel Central Station', 'Glass, steel, and hurried travelers surround a platform under guard.'),
    ('A canal-side hotel', 'Polished brass faces the water while deals happen in the service halls.'),
    ('A Roughs rail stop', 'One platform, one telegraph, and miles of dry country in every direction.'),
    ('A constabulary office', 'Case boards compete with ringing phones and cooling cups of tea.'),
    ('A motorcar garage', 'Oil-stained tools frame a vehicle with fresh bullet marks.'),
    ('A newspaper pressroom', 'Hot presses thunder while an editor hides tomorrow\'s front page.'),
    ('A Basin bank', 'Marble counters and steel doors promise more safety than they provide.'),
    ('A university laboratory', 'Metal samples sit beside instruments built for an unapproved study.'),
    ('A crowded rail carriage', 'Strangers share close seats while the city slides past the windows.'),
    ('An Elendel canal tunnel', 'Maintenance lamps reveal fresh bootprints beside the dark water.'),
    ('A Roughs mining camp', 'Dusty tents circle a locked assay shed and a dry well.'),
]

HOOKS_ERA_1 = [
    'A noble house offers double pay to recover a stolen ledger before the Ministry sees it.',
    'A canal barge arrives on time with its cargo intact and every crew member missing.',
    'Someone is replacing trusted servants with strangers who know their routines.',
    'A rebel cache contains genuine supplies and one map designed to lead pursuers there.',
    'An obligator quietly asks for proof that another office has altered the records.',
    'A skaa work crew refuses to enter a cellar where metal keeps moving by itself.',
    'A noble heir vanished during the evening mist and left both shoes behind.',
    'Counterfeit boxings point to a mint that officially closed years ago.',
    'A metallurgist will identify a rare sample only if the party steals back her notes.',
    'The garrison seals a district after someone leaps across its rooftops unseen.',
    'A house war is about to begin over a letter that neither side actually wrote.',
    'An ash-cleaning crew uncovers a door beneath a public square.',
]
HOOKS_ERA_2 = [
    'A night train reaches Elendel with its safe open and no sign of forced entry.',
    'A constable needs outsiders to test whether evidence is disappearing from the station.',
    'Three banks receive the same robbery note, each naming a different hour.',
    'A newspaper prints tomorrow\'s crime report before the crime occurs.',
    'A new rail spur crosses land claimed by a Roughs town that does not appear on maps.',
    'Someone buys every aluminum round in the district using flawless banknotes.',
    'A factory whistle sounds at midnight from a building locked for the week.',
    'A canal chase ends at a motorcar registered to a respected judge.',
    'A gunsmith recognizes a weapon from a case the constabulary buried.',
    'Passengers on the eastern line report losing the same twelve minutes.',
    'A university demonstration exposes a metalborn talent the speaker denies having.',
    'A bounty poster changes overnight while the suspect remains in custody.',
]

RUMORS_ERA_1 = [
    'A house near the north canal pays its servants in freshly minted boxings.',
    'The mists avoid one alley, even when they fill every street around it.',
    'A skaa crew found a metal door under the ash and was reassigned the next morning.',
    'Someone at the palace is buying empty glass vials by the crate.',
    'A noble duelist won without drawing steel or taking a single step.',
    'The Ministry keeps a second set of records beneath the public archive.',
    'A Terris steward has been asking dockworkers about a missing shipment.',
    'A silent carriage circles the same district each night before dawn.',
    'Canal thieves refuse to touch cargo marked with a white triangle.',
    'A rooftop crew swears it saw a figure standing on falling coins.',
]
RUMORS_ERA_2 = [
    'A constable in the Fourth District closes every case involving the same family.',
    'The late train carries one passenger whose ticket is always dated tomorrow.',
    'A new motorcar can outrun a steelrunner, according to its very nervous inventor.',
    'Someone is paying reporters to ignore a string of canal disappearances.',
    'The mint recalled a banknote plate that was never officially made.',
    'A Roughs prospector returned rich and refuses to say which metal she found.',
    'A gunsmith sells aluminum rounds only after asking about childhood dreams.',
    'One Elendel hotel room is booked under the same name every night of the year.',
    'Rail workers found a locked carriage attached between stations.',
    'A university professor can identify metalborn by the sound of their footsteps.',
]

DRESSING_ERA_1 = [
    'Ash whispers across the stones and gathers against every doorway.',
    'A clipped boxing spins on the counter long after anyone touched it.',
    'Canal water taps softly against a barge hidden by the mist.',
    'A servant brushes soot from an immaculate coat for the third time.',
    'Glass vials clink inside a passerby\'s concealed pocket.',
    'A distant hammer keeps an exact rhythm, then stops mid-beat.',
    'Bootprints appear in the ash beside the party and end at a wall.',
    'A carriage lantern becomes a pale halo in the gathering mist.',
    'Two obligators exchange sealed notes without breaking stride.',
    'A rooftop tile clicks once under weight that cannot be seen.',
    'The air smells of hot metal, wet stone, and chimney smoke.',
    'A skaa child hides a bright coin before the patrol turns the corner.',
]
DRESSING_ERA_2 = [
    'A train whistle cuts across the canal and drowns out half a sentence.',
    'Motorcar tires hiss over wet stone beneath electric streetlights.',
    'A newspaper page tumbles past with one headline carefully removed.',
    'Telegraph wires hum overhead despite the still air.',
    'A constable checks a pocket watch, then the rooftops, then the watch again.',
    'Fresh rail dust marks the cuffs of someone claiming to have walked here.',
    'A camera flash turns the mist white for an instant.',
    'Coins rattle inside a drain where no hand can reach them.',
    'A canal lock groans open without an operator at the wheel.',
    'A vendor folds banknotes while watching a stranger across the street.',
    'The smell of oil and hot brakes lingers after the motorcar is gone.',
    'A distant revolver shot is followed by the bell of an arriving train.',
]


def _muted(value):
    return '<span class="muted">%s</span>' % value


def _era_two(era):
    return era == 'era-2'


def _name_groups(era):
    if _era_two(era):
        return NAMES_ERA_2
    return NAMES_ERA_1


def _capitalize_first(value):
    return value[:1].upper() + value[1:]


def gen_name(culture=None, *, era='era-1'):
    groups = _name_groups(era)
    culture = culture if culture in groups else random.choice(list(groups))
    pool = groups[culture]
    gender = random.choice(('male', 'female'))
    given = random.choice(pool[gender])
    family = pool.get('family') or []
    if family and random.random() < 0.5:
        given = '%s %s' % (given, random.choice(family))
    return '<b>%s</b> %s' % (
        given, _muted('&middot; %s, %s' % (culture, gender)))


def gen_npc(*, era='era-1'):
    groups = _name_groups(era)
    culture = random.choice(list(groups))
    pool = groups[culture]
    gender = random.choice(('male', 'female'))
    name = random.choice(pool[gender])
    roles = NPC_ROLES_ERA_2 if _era_two(era) else NPC_ROLES_ERA_1
    hooks = NPC_HOOKS_ERA_2 if _era_two(era) else NPC_HOOKS_ERA_1
    return '<b>%s</b> %s<br>%s.<br>%s' % (
        name, _muted('&middot; %s, %s' % (culture, random.choice(roles))),
        random.choice(NPC_TRAITS).capitalize(),
        _muted('Hook: ' + random.choice(hooks) + '.'))


def gen_weather(*, era='era-1'):
    title, body = random.choice(WEATHER)
    return '%s<br>%s' % (title, _muted(body))


def gen_coins(*, era='era-1'):
    if _era_two(era):
        notes = random.randint(1, 8)
        note_value = random.choice((1, 2, 5, 10, 20))
        clips = random.randint(0, 9)
        return ('A wallet with <b>%d banknote%s worth %d boxing%s</b> and '
                '<b>%d clip%s</b>.<br>%s' % (
                    notes, '' if notes == 1 else 's', notes * note_value,
                    '' if notes * note_value == 1 else 's', clips,
                    '' if clips == 1 else 's',
                    _muted('The notes carry several banks\' stamps.')))
    boxings = random.randint(1, 12)
    clips = random.randint(0, 9)
    return 'A purse holding <b>%d boxing%s</b> and <b>%d clip%s</b>.' % (
        boxings, '' if boxings == 1 else 's', clips, '' if clips == 1 else 's')


def gen_loot(*, era='era-1'):
    rare = RARE_LOOT_ERA_2 if _era_two(era) else RARE_LOOT_ERA_1
    common = LOOT_ERA_2 if _era_two(era) else LOOT_ERA_1
    if random.random() < 0.12:
        return _capitalize_first(random.choice(rare)) + '.<br>' + _muted(
            'A rare find that will attract determined buyers.')
    return _capitalize_first(random.choice(common)) + '.'


def gen_location(*, era='era-1'):
    locations = LOCATIONS_ERA_2 if _era_two(era) else LOCATIONS_ERA_1
    title, body = random.choice(locations)
    return '<b>%s</b><br>%s' % (title, _muted(body))


def gen_hook(*, era='era-1'):
    return random.choice(HOOKS_ERA_2 if _era_two(era) else HOOKS_ERA_1)


def gen_rumor(*, era='era-1'):
    rumor = random.choice(RUMORS_ERA_2 if _era_two(era) else RUMORS_ERA_1)
    return _muted('&ldquo;') + rumor + _muted('&rdquo;')


def gen_dressing(*, era='era-1'):
    return random.choice(DRESSING_ERA_2 if _era_two(era) else DRESSING_ERA_1)
