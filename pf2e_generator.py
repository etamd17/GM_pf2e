import random


class RobustPF2eGenerator:
    def __init__(self, db_path=None):
        self.db_path = db_path

        # ── PF2e DC by Level Table (Core Rulebook) ──────────────────────
        self._dc_table = {
            0: 14, 1: 15, 2: 16, 3: 18, 4: 19, 5: 20, 6: 22, 7: 23,
            8: 24, 9: 26, 10: 27, 11: 28, 12: 30, 13: 31, 14: 32,
            15: 34, 16: 35, 17: 36, 18: 38, 19: 39, 20: 40,
        }

        # ── Wealth-by-level guidelines (total party treasure in gp) ─────
        self._wealth_by_level = {
            1: 175, 2: 300, 3: 500, 4: 850, 5: 1350, 6: 2000, 7: 2900,
            8: 4000, 9: 5700, 10: 8000, 11: 11500, 12: 16500, 13: 25000,
            14: 36500, 15: 54500, 16: 82500, 17: 128000, 18: 208000,
            19: 355000, 20: 490000,
        }

        # ── XP budget reference (4-player party) ───────────────────────
        self._xp_budgets = {
            "Trivial": 40, "Low": 60, "Moderate": 80,
            "Severe": 120, "Extreme": 160,
        }

        # ── Ancestries (core + uncommon + versatile heritages) ─────────
        self.ancestries = [
            "Human", "Dwarf", "Elf", "Halfling", "Gnome", "Orc", "Goblin",
            "Leshy", "Tengu", "Kobold", "Catfolk", "Ratfolk", "Kitsune",
            "Hobgoblin", "Lizardfolk", "Automaton", "Fetchling", "Fleshwarp",
            "Sprite", "Shoony", "Grippli", "Strix", "Nagaji", "Vanara",
            "Conrasu", "Goloma", "Kashrishi", "Poppet", "Shisk", "Anadi",
            "Azarketi", "Gnoll", "Android", "Reflection",
        ]

        self.versatile_heritages = [
            "Dhampir", "Changeling", "Tiefling", "Aasimar", "Duskwalker",
            "Beastkin", "Ganzi", "Aphorite", "Ardande", "Talos",
            "Nephilim", "Reflection",
        ]

        self.first_names = [
            "Aldric", "Bren", "Caelith", "Dorn", "Elden", "Fael", "Grem",
            "Hask", "Iona", "Jael", "Kael", "Lyra", "Mira", "Nyx", "Orin",
            "Pella", "Quinn", "Rhea", "Sera", "Thane", "Uma", "Vale", "Wren",
            "Xara", "Yara", "Zev", "Alara", "Brynn", "Corva", "Delith",
            "Eryn", "Fenn", "Gael", "Havra", "Idris", "Juna", "Kai", "Lira",
            "Mael", "Nira", "Oskar", "Petra", "Riven", "Sable", "Tova",
            "Ulric", "Vex", "Wyra", "Zariel", "Ash", "Thorn", "Isolde",
            "Gavrel", "Morwen", "Kyros", "Hesper", "Dazen", "Rilka", "Eska",
            "Tarek", "Nym", "Sovan", "Velka", "Jorek", "Calixta", "Rowan",
            "Fen", "Brix", "Kaida", "Lumin", "Zara", "Drest", "Anika",
            "Borric", "Thessaly", "Kavi", "Ondri", "Meris", "Thane", "Ylva",
        ]

        self.last_names = [
            "Ashford", "Blackthorn", "Copperfield", "Duskwalker", "Ember",
            "Frostmantle", "Greymist", "Hollowbone", "Ironhand", "Jadescale",
            "Keenedge", "Longstride", "Moonveil", "Nightwhisper", "Oakenheart",
            "Prowl", "Quicksilver", "Redclaw", "Stormcrow", "Thornwall",
            "Underhill", "Voidgazer", "Windsong", "Ashenmoor", "Brightforge",
        ]

        self.professions = [
            "Alchemist", "City Guard Captain", "Smuggler", "Scholar",
            "Bounty Hunter", "Priest", "Street Urchin", "Merchant",
            "Fallen Noble", "Blacksmith", "Tavern Keeper", "Mercenary",
            "Graverobber", "Herbalist", "Scribe", "Courier", "Miner",
            "Cartographer", "Fence", "Hedge Witch", "Sailor", "Teamster",
            "Performer", "Gladiator", "Hermit", "Spy", "Architect",
            "Barrister", "Candlemaker", "Debt Collector", "Embalmer",
            "Fisherfolk", "Gemcutter", "Horse Trainer", "Ink Brewer",
            "Jailer", "Kennelmaster", "Lamplighter", "Midwife",
            "Navigator", "Oracle-for-Hire", "Pathfinder Agent",
            "Quartermaster", "Ratcatcher", "Shipwright", "Tanner",
            "Undertaker", "Vintner", "Wandering Healer", "Zephyr Guard",
            "Runesmith", "Hellknight Armiger", "Monster Hunter",
            "Diplomat", "Archaeologist", "Pirate", "Librarian",
        ]

        self.traits = [
            "suspicious", "boisterous", "paranoid", "overly formal",
            "distracted", "gossipy", "gruff", "sycophantic", "melancholy",
            "hyperactive", "arrogant", "cowardly", "ruthless",
            "charmingly naive", "dead-eyed", "soft-spoken", "fiercely loyal",
            "perpetually nervous", "eerily calm", "sardonic",
            "warm and grandmotherly", "cold and calculating",
            "jovial but watchful", "world-weary", "desperately optimistic",
            "theatrically dramatic", "quietly intense", "absent-minded",
            "stoically silent", "infectiously cheerful", "grimly pragmatic",
            "serenely detached", "aggressively friendly",
            "hauntingly sorrowful", "razor-witted",
        ]

        self.quirks = [
            "constantly shuffling a deck of Harrow cards",
            "chews on a piece of raw ginger",
            "has a clicking clockwork prosthetic arm",
            "speaks with an exaggerated theatrical accent",
            "keeps a tiny glowing familiar in a jar",
            "never breaks eye contact",
            "is covered in esoteric tattoos",
            "always speaks in the third person",
            "has a pet rat on their shoulder",
            "obsessively cleans their spectacles",
            "taps their foot to an unheard rhythm",
            "smells faintly of copper and blood",
            "wears a heavy cloak despite the heat",
            "is missing their left ring finger",
            "whistles the same three notes repeatedly",
            "collects small bones in a pouch",
            "has one eye that is a noticeably different color",
            "scratches tally marks into surfaces",
            "carries an ornate but empty scabbard",
            "talks to an imaginary companion",
            "habitually counts coins under the table",
            "always stands with their back to a wall",
            "flinches at sudden loud noises",
            "compulsively corrects others' grammar",
            "draws tiny sigils on any paper they touch",
            "hums a lullaby when nervous",
            "carries a vial of dirt from their homeland",
            "sniffs food suspiciously before eating",
            "keeps one hand on a concealed weapon at all times",
            "blinks exactly twice after every sentence",
        ]

        self.appearances = [
            "scarred face with a milky blind eye",
            "immaculately groomed with oiled hair",
            "weather-beaten skin and calloused hands",
            "unnervingly youthful face for their apparent age",
            "a prominent brand or tattoo on the neck",
            "muscular build barely contained by their clothes",
            "gaunt frame with hollowed cheeks",
            "striking heterochromia: one blue eye, one amber",
            "hair streaked prematurely white",
            "burn scars covering their left arm",
            "tall and willowy with an aristocratic bearing",
            "short and stocky with an iron grip",
            "ink-stained fingers and rumpled clothes",
            "elaborate face paint in a cultural pattern",
            "a missing ear replaced with a silver prosthetic",
            "freckled and sun-bronzed with an easy smile",
            "gaunt and pale, as if they rarely see sunlight",
            "covered in fine dust from constant travel",
            "a fresh wound, poorly bandaged",
            "expensive clothes slightly too large, as if borrowed",
        ]

        self.voice_notes = [
            "speaks in clipped, military cadences",
            "has a lilting accent from a distant land",
            "whispers constantly, forcing others to lean in",
            "booms with a deep baritone that carries across rooms",
            "stutters when lying",
            "speaks with exaggerated precision, as if reciting text",
            "uses far too many nautical metaphors",
            "pauses mid-sentence, as if listening to someone else",
            "speaks rapidly, barely pausing for breath",
            "uses an archaic dialect peppered with old-world oaths",
            "has a raspy voice, as if recovering from illness",
            "laughs nervously after every other sentence",
            "drops their voice to a conspiratorial murmur",
            "speaks through clenched teeth when angry",
            "has a melodic, almost hypnotic cadence",
        ]

        self.combat_capabilities = [
            "Capable combatant; favors a short sword and shield",
            "Non-combatant; will flee or surrender immediately",
            "Dangerous spellcaster; knows at least 3rd-rank offensive spells",
            "Skilled archer; will fight from range and retreat",
            "Brawler type; grapples and trips rather than strikes",
            "Poisoner; coats blades with lethal substances",
            "Has bodyguards nearby (2-4 trained fighters)",
            "Carries a concealed magical weapon (+1 striking dagger)",
            "Surprisingly skilled; trained to Expert in Athletics and Intimidation",
            "Relies on traps and ambush tactics, never fights fair",
            "Weak individually but has powerful allies to call upon",
            "Dual-wields; fights with frightening speed (Flurry of Blows equivalent)",
        ]

        self.npc_connections = [
            "is the sibling of a local crime lord",
            "owes a life debt to a powerful cleric",
            "is secretly married to a rival faction leader",
            "trained under the same master as a famous adventurer",
            "has a child who was taken by the Fey",
            "corresponds with a dragon via coded letters",
            "was once saved by Pathfinder agents and remains loyal to them",
            "is being watched by agents of the Aspis Consortium",
            "is a former member of the Bellflower Network",
            "holds a favor from a ranking Hellknight",
        ]

        self.secrets = [
            "is desperately trying to pay off a debt to a crime syndicate",
            "is actually a spy for a neighboring nation",
            "witnessed a murder and is being hunted",
            "is a retired adventurer hiding from their past",
            "stole something valuable and is looking for a buyer",
            "has a terminal illness and is searching for a cure",
            "is a lycanthrope struggling to control their condition",
            "is secretly funding a rebellion",
            "knows the location of a hidden dungeon entrance",
            "is being blackmailed by a powerful figure",
            "has been replaced by a doppelganger",
            "carries a cursed item they cannot get rid of",
            "murdered their business partner and inherited everything",
            "is an exiled member of a royal family",
            "is feeding information to an undead cabal",
            "has made a pact with a devil for worldly success",
            "is running an underground railroad for escaped slaves",
            "discovered a vein of rare ore and is hiding it from the authorities",
            "is slowly being possessed by an entity in their dreams",
            "once betrayed their adventuring party and is the sole survivor",
        ]

        self.motivations = [
            "Searching for a lost family heirloom",
            "Wants revenge against a specific person",
            "Needs protection from an unknown threat",
            "Collecting rare ingredients for a ritual",
            "Trying to reunite with an estranged family member",
            "Building a case against a corrupt official",
            "Seeking passage to a dangerous location",
            "Looking for adventurers to test a new invention",
            "Wants to clear their name of a false accusation",
            "Needs someone to deliver a package, no questions asked",
            "Hoping to buy or steal a specific magical artifact",
            "Recruiting members for an expedition into the Darklands",
            "Searching for a cure for a rare magical disease",
            "Investigating disappearances in the local area",
            "Trying to broker peace between two warring factions",
            "Needs bodyguards for a dangerous negotiation",
            "Wants to sabotage a rival's business",
            "Collecting debts owed by dangerous people",
            "Seeking a translator for an ancient text",
            "Desperate to find their missing familiar or animal companion",
        ]

        # ── Biomes (9 total) ──────────────────────────────────────────
        self.biomes = {
            "City": {
                "flavor": [
                    "smog-choked alleyways", "bustling merchant squares",
                    "rain-slicked cobblestones", "shadowy rooftops",
                    "gilded noble districts", "the fish market docks",
                    "a crumbling tenement block", "a lamplit plaza at dusk",
                    "a sewer grate billowing steam", "a cathedral courtyard",
                ],
                "hazards": [
                    "Rogue Alchemist's Cart", "Collapsing Scaffolding",
                    "Sewer Gas Pocket", "Glyph of Warding",
                    "Runaway clockwork sweeper", "Collapsing sewer tunnel",
                    "Poisoned well", "Unstable balcony", "Riot crowd surge",
                ],
                "terrain_features": [
                    "Narrow alley (squeeze rules)", "Rooftop with 20-ft drop",
                    "Market stalls (difficult terrain)", "Slippery wet cobbles",
                    "Sewer grate (cover)", "Balcony 15 ft up (greater cover)",
                    "Overturned cart (standard cover)", "Crowded street (concealment)",
                ],
                "monsters_by_level": {
                    1: ["Pickpocket Gang (4)", "Giant Rats (3)", "Sewer Ooze"],
                    2: ["Thieves Guild Thugs (3)", "Wererat", "Animated Broom Swarm"],
                    4: ["Doppelganger", "Vampire Spawn", "Corrupt Guard Patrol (4)"],
                    6: ["Invisible Stalker", "Gargoyle (2)", "Hellknight Squad (3)"],
                    8: ["Vampire", "Guild Assassin (2)", "Onidoshi"],
                    10: ["Rakshasa", "Adult Vampire Lord", "Doppelganger Ring (3)"],
                    14: ["Marilith", "Ancient Vampire", "Corrupt Archon"],
                    20: ["Pit Fiend", "Shoggoth"],
                },
            },
            "Forest": {
                "flavor": [
                    "ancient moss-draped trees", "dense thorny undergrowth",
                    "mist-shrouded clearings", "canopies blocking the sun",
                    "glowing bioluminescent fungi", "a circle of standing stones",
                    "a stream choked with fallen logs", "a hollow ancient oak",
                    "a grove of petrified trees", "a dense carpet of ferns",
                ],
                "hazards": [
                    "Razor-Vine Patch", "Toxic Spore Cloud", "Hidden Sinkhole",
                    "Fairy Ring Enchantment", "Angry Treant Roots",
                    "Quicksand pit", "Spider web snare", "Falling dead tree",
                ],
                "terrain_features": [
                    "Dense undergrowth (difficult terrain)",
                    "Fallen tree trunk (standard cover, balance DC 15)",
                    "Stream (difficult terrain, 10 ft wide)",
                    "Tree canopy (greater cover from above, 30 ft climb)",
                    "Thick roots (DC 15 Acrobatics or fall prone)",
                    "Bramble patch (difficult + 1 piercing damage on entry)",
                ],
                "monsters_by_level": {
                    1: ["Wolf Pack (4)", "Giant Spider", "Twig Blight (3)"],
                    2: ["Owlbear", "Cockatrice", "Satyr"],
                    4: ["Green Hag", "Arboreal Warden", "Ettercap & Spider Swarm"],
                    6: ["Hydra", "Shambling Mound", "Fey Trickster Coven (3)"],
                    8: ["Treant", "Annis Hag Coven", "Basilisk (2)"],
                    10: ["Young Green Dragon", "Dryad Queen & Guards", "Wendigo"],
                    14: ["Adult Green Dragon", "Mu Spore", "Tane Beast"],
                    20: ["Ancient Green Dragon", "Terotricus"],
                },
            },
            "Dungeon": {
                "flavor": [
                    "damp echoing stone corridors", "crumbling catacombs",
                    "obsidian walls pulsing with dark magic",
                    "dusty tombs untouched for centuries",
                    "blood-stained sacrificial altars",
                    "a vast underground lake", "an impossibly deep vertical shaft",
                    "a room filled with broken statues",
                    "corridors lined with empty niches",
                    "a collapsed hallway choked with rubble",
                ],
                "hazards": [
                    "Spear Launcher Trap", "Crushing Wall", "Acid Pit",
                    "Restless Poltergeist Haunt", "Poison Dart Gallery",
                    "Blood-Siphon Rune", "False floor trap", "Alarm ward",
                    "Rolling boulder", "Teleportation trap",
                ],
                "terrain_features": [
                    "Rubble (difficult terrain)",
                    "Pit trap (20 ft deep, DC 20 Reflex)",
                    "Narrow corridor (squeeze, single file)",
                    "Elevated ledge (10 ft, standard cover)",
                    "Flooded room (waist-deep water, difficult terrain)",
                    "Pillar (standard cover)",
                    "Darkness (no natural light, darkvision required)",
                ],
                "monsters_by_level": {
                    1: ["Skeleton Warriors (3)", "Giant Rat Swarm", "Zombie Shamblers (4)"],
                    2: ["Gelatinous Cube", "Skeletal Champion (2)", "Mimic"],
                    4: ["Wraith", "Flesh Golem", "Minotaur"],
                    6: ["Mummy Guardian (2)", "Greater Shadow", "Clay Golem"],
                    8: ["Mohrg", "Devourer", "Stone Golem"],
                    10: ["Lich", "Iron Golem", "Greater Wraith (2)"],
                    14: ["Demilich", "Adamantine Golem", "Nightwalker"],
                    20: ["Ravener", "Tarn Linnorm"],
                },
            },
            "Desert": {
                "flavor": [
                    "scorching sands", "jagged wind-scoured rocks",
                    "ancient half-buried ruins", "shimmering heat waves",
                    "salt-crusted dry lakebeds", "a sandstone canyon",
                    "an oasis surrounded by bleached bones",
                    "a towering dust devil on the horizon",
                    "a glass-smooth expanse of melted sand",
                    "dunes shifting under relentless wind",
                ],
                "hazards": [
                    "Quicksand", "Sudden Sandstorm", "Mirage Trap",
                    "Cursed Tomb Ward", "Flash Flood", "Collapsing dune",
                    "Heat exhaustion zone", "Scorpion nest", "Buried ruin collapse",
                ],
                "terrain_features": [
                    "Loose sand (difficult terrain)",
                    "Sand dune crest (higher ground, standard cover)",
                    "Rocky outcrop (greater cover)",
                    "Extreme heat (DC 20 Fort every hour or fatigued)",
                    "Sandstorm (concealed, difficult terrain)",
                    "Dry riverbed (normal terrain, lower elevation)",
                ],
                "monsters_by_level": {
                    1: ["Giant Scorpion", "Hyena Pack (4)", "Dust Mephit"],
                    2: ["Mummy", "Giant Ant Swarm", "Sand Lurker"],
                    4: ["Criosphinx", "Lamia", "Young Purple Worm"],
                    6: ["Young Blue Dragon", "Div Patrol", "Sand Elemental"],
                    8: ["Gynosphinx", "Mummy Lord", "Greater Lamia"],
                    10: ["Adult Blue Dragon", "Phoenix", "Sepid Div"],
                    14: ["Ancient Blue Dragon", "Elder Sand Elemental", "Efreeti Noble"],
                    20: ["Jabberwock", "Elder Wyrm"],
                },
            },
            "Swamp": {
                "flavor": [
                    "fetid stagnant pools", "thick buzzing clouds of insects",
                    "twisted mangrove roots", "glowing will-o'-wisps in the mist",
                    "sinking mud flats", "a sunken stone ruin",
                    "a rotting wooden walkway", "a fogbound river delta",
                    "a collapsed fisherman's hut", "islands of reeds and cattails",
                ],
                "hazards": [
                    "Mire Trap", "Methane Gas Explosion", "Leech Swarm",
                    "Hag Curse Ward", "Choking Miasma", "Quickmud",
                    "Disease spore cloud", "Drowning pit", "Rot gas pocket",
                ],
                "terrain_features": [
                    "Waist-deep water (difficult terrain, no 5-ft step)",
                    "Mud flat (difficult terrain, DC 18 Athletics to move at full speed)",
                    "Mangrove roots (difficult terrain, standard cover)",
                    "Fog bank (concealed beyond 30 ft)",
                    "Dry hummock (normal terrain, higher ground)",
                    "Rotting log bridge (DC 15 Acrobatics or fall into water)",
                ],
                "monsters_by_level": {
                    1: ["Crocodile", "Bog Strider (3)", "Leech Swarm"],
                    2: ["Will-o'-Wisp", "Giant Frog (3)", "Merrow"],
                    4: ["Young Black Dragon", "Sea Hag", "Slithering Tracker"],
                    6: ["Hydra", "Marsh Giant", "Bog Mummy (2)"],
                    8: ["Froghemoth", "Nuckelavee", "Hag Coven"],
                    10: ["Adult Black Dragon", "Elder Froghemoth", "Swamp Lich"],
                    14: ["Ancient Black Dragon", "Elder Nuckelavee", "Swamp Horror"],
                    20: ["Swamp Titan", "Wyrm Black Dragon"],
                },
            },
            "Mountains": {
                "flavor": [
                    "sheer icy cliffs", "howling biting winds",
                    "snow-choked passes", "ancient dwarven ruins carved into rock",
                    "thin freezing air", "a volcanic vent billowing sulfur",
                    "a frozen waterfall", "a wind-carved natural bridge",
                    "a mountain meadow above the treeline",
                    "a cairn marking a forgotten grave",
                ],
                "hazards": [
                    "Sudden Avalanche", "Thin Ice over a Chasm",
                    "Freezing Winds", "Rockslide", "Geothermal Vent Eruption",
                    "Altitude sickness zone", "Unstable bridge",
                    "Lightning strike zone", "Crumbling ledge",
                ],
                "terrain_features": [
                    "Steep slope (difficult terrain, DC 20 Athletics to climb)",
                    "Narrow ledge (squeeze, 500-ft drop)",
                    "Scree field (difficult terrain, DC 15 Acrobatics or slide 10 ft)",
                    "Boulder (greater cover)",
                    "Frozen stream (DC 18 Acrobatics or fall prone)",
                    "High altitude (DC 15 Fort per hour or fatigued)",
                ],
                "monsters_by_level": {
                    1: ["Wolf Pack (4)", "Kobold Trappers (4)", "Mountain Goat Stampede"],
                    2: ["Yeti", "Harpy", "Young Wyvern"],
                    4: ["Frost Giant Scout", "Young Roc", "Stone Giant"],
                    6: ["Young White Dragon", "Frost Giant Raider (2)", "Wyvern"],
                    8: ["Cloud Giant", "Roc", "Frost Worm"],
                    10: ["Adult White Dragon", "Storm Giant", "Frost Giant Jarl"],
                    14: ["Ancient White Dragon", "Elder Roc", "Mountain Titan"],
                    20: ["Tarn Linnorm", "Storm Giant King"],
                },
            },
            "Coastal": {
                "flavor": [
                    "salt-sprayed cliffs", "tide-pool caves exposed at low tide",
                    "a barnacle-encrusted shipwreck on the shore",
                    "a fishing village battered by storms",
                    "a lighthouse on a crumbling promontory",
                    "a smuggler's cove hidden behind sea-stacks",
                    "a coral reef visible through turquoise water",
                    "a beach of black volcanic sand",
                    "a pier creaking in the wind",
                    "a grotto carved by centuries of waves",
                ],
                "hazards": [
                    "Riptide", "Sudden Storm Surge", "Crumbling Sea Cliff",
                    "Jagged Coral Reef", "Siren Song Haunt",
                    "Capsizing Wave", "Sea Mine (alchemical)",
                    "Whirlpool", "Toxic Algae Bloom",
                ],
                "terrain_features": [
                    "Wet rocks (difficult terrain, DC 15 Acrobatics or prone)",
                    "Tide pool (difficult terrain, knee-deep water)",
                    "Sea cave (darkness, echoing acoustics +2 to Perception DCs)",
                    "Cliff face (DC 20 Athletics to climb, 40 ft)",
                    "Ship deck (difficult terrain if rocking)",
                    "Sandy beach (normal terrain, no cover)",
                ],
                "monsters_by_level": {
                    1: ["Giant Crab (2)", "Sea Serpent Hatchling", "Sahuagin Scout (3)"],
                    2: ["Grindylow Pack (4)", "Bunyip", "Merfolk Raider (3)"],
                    4: ["Young Bronze Dragon", "Scylla Spawn", "Sea Hag"],
                    6: ["Aboleth", "Charybdis Spawn", "Sahuagin Baron & Guards"],
                    8: ["Kraken Spawn", "Sea Serpent", "Marid"],
                    10: ["Adult Bronze Dragon", "Kraken", "Aboleth Master"],
                    14: ["Ancient Bronze Dragon", "Elder Kraken", "Leviathan Spawn"],
                    20: ["Leviathan", "Charybdis"],
                },
            },
            "Arctic": {
                "flavor": [
                    "endless white tundra stretching to the horizon",
                    "a frozen lake groaning under the weight of ice",
                    "wind-blasted permafrost dotted with lichen",
                    "a glacier slowly crushing ancient ruins",
                    "the eerie shimmer of the aurora overhead",
                    "an ice cave glittering with frozen stalactites",
                    "a hot spring steaming in sub-zero air",
                    "a blizzard reducing visibility to arm's length",
                    "a cairn of frozen skulls marking a boundary",
                    "an abandoned research outpost half-buried in snow",
                ],
                "hazards": [
                    "Blinding Whiteout", "Thin Ice (10-ft fall into frigid water)",
                    "Hypothermia Zone", "Calving Glacier", "Ice Storm",
                    "Crevasse (hidden, 40 ft deep)", "Frostbite Wind",
                    "Avalanche Trigger Zone", "Frozen Methane Pocket",
                ],
                "terrain_features": [
                    "Deep snow (difficult terrain, greater difficult if over 3 ft)",
                    "Ice sheet (DC 18 Acrobatics or fall prone, no 5-ft step)",
                    "Snow drift (standard cover, collapses if disturbed)",
                    "Frozen river (thin ice, DC 20 Perception to detect weak spots)",
                    "Blizzard (concealed beyond 20 ft, difficult terrain)",
                    "Ice wall (AC 10, Hardness 5, HP 30 per 5-ft section)",
                ],
                "monsters_by_level": {
                    1: ["Ice Mephit", "Arctic Wolf Pack (4)", "Frost Skeleton (3)"],
                    2: ["Yeti", "Ice Troll", "Frost Wight"],
                    4: ["Young White Dragon", "Frost Giant Scout", "Winter Wolf (2)"],
                    6: ["Frost Giant Raider (2)", "Ice Linnorm Spawn", "Wendigo"],
                    8: ["Frost Worm", "Glacier Elemental", "Adult White Dragon"],
                    10: ["Frost Giant Jarl", "Ice Linnorm", "Remorhaz"],
                    14: ["Ancient White Dragon", "Elder Frost Giant", "Frost Titan"],
                    20: ["Tarn Linnorm", "Winter Fey Lord"],
                },
            },
            "Underground": {
                "flavor": [
                    "lightless caverns echoing with dripping water",
                    "a vast fungal forest of towering mushrooms",
                    "crystal-studded cave walls pulsing with faint light",
                    "an underground river carving through limestone",
                    "a drow outpost watching from the darkness",
                    "the ruins of a dwarven city long abandoned",
                    "a chasm so deep the bottom cannot be seen",
                    "tunnels carved by massive burrowing creatures",
                    "a lake of perfectly still black water",
                    "stalactites the size of castle towers",
                ],
                "hazards": [
                    "Cave-In", "Toxic Gas Pocket", "Glowing Spore Cloud",
                    "Underground River Flash Flood", "Unstable Stalactite",
                    "Darklands Haunt", "Magnetic Anomaly (disrupts compasses)",
                    "Psychic Pressure Zone", "Collapsing Floor",
                ],
                "terrain_features": [
                    "Stalagmite field (difficult terrain, standard cover)",
                    "Underground stream (difficult terrain, 15 ft wide)",
                    "Narrow tunnel (squeeze, single file)",
                    "Elevated shelf (15 ft up, greater cover)",
                    "Total darkness (darkvision or light source required)",
                    "Slippery cave floor (DC 15 Acrobatics or prone)",
                    "Chasm (30 ft wide, 100+ ft deep)",
                ],
                "monsters_by_level": {
                    1: ["Giant Cave Spider", "Darkmantle (2)", "Kobold Scouts (4)"],
                    2: ["Choker", "Morlock Pack (3)", "Cave Fisher"],
                    4: ["Drider", "Roper", "Duergar Raiders (3)"],
                    6: ["Umber Hulk", "Mind Flayer", "Drow Priestess & Guards"],
                    8: ["Purple Worm", "Cloaker", "Gugs (2)"],
                    10: ["Elder Purple Worm", "Aboleth", "Drow Matron & Elite Guard"],
                    14: ["Neothelid", "Elder Umber Hulk", "Darklands Horror"],
                    20: ["Shoggoth", "Voidworm Titan"],
                },
            },
        }

        # ── Consumables, permanents, art objects by tier ───────────────
        self.consumables_by_tier = {
            "low": [
                ("Minor Healing Potion", "4 gp"), ("Scroll of Heal (1st)", "4 gp"),
                ("Tanglefoot Bag (Lesser)", "3 gp"), ("Alchemist's Fire (Lesser)", "3 gp"),
                ("Antidote (Lesser)", "3 gp"), ("Feather Token (Ladder)", "3 gp"),
                ("Owlbear Claw Talisman", "4 gp"), ("Smokestick (Lesser)", "3 gp"),
                ("Silversheen", "6 gp"), ("Holy Water", "3 gp"),
                ("Scroll of Magic Weapon (1st)", "4 gp"),
                ("Bottled Lightning (Lesser)", "3 gp"),
                ("Potency Crystal", "4 gp"),
            ],
            "mid": [
                ("Moderate Healing Potion", "12 gp"), ("Scroll of Fireball (3rd)", "12 gp"),
                ("Alchemist's Fire (Moderate)", "10 gp"), ("Elixir of Life (Moderate)", "25 gp"),
                ("Scroll of Haste (3rd)", "12 gp"), ("Bravo's Brew (Moderate)", "10 gp"),
                ("Cheetah's Elixir", "10 gp"), ("Potion of Invisibility", "12 gp"),
                ("Scroll of Dispel Magic", "12 gp"), ("Scroll of Fly (4th)", "18 gp"),
                ("Potion of Resistance (Moderate)", "16 gp"),
                ("Oil of Mending (Moderate)", "9 gp"),
            ],
            "high": [
                ("Greater Healing Potion", "50 gp"), ("Scroll of Heal (6th)", "50 gp"),
                ("Elixir of Life (Greater)", "60 gp"), ("Alchemist's Fire (Greater)", "30 gp"),
                ("Potion of Flying", "50 gp"), ("Scroll of Chain Lightning", "50 gp"),
                ("Phoenix Flask", "75 gp"), ("Scroll of True Seeing", "50 gp"),
                ("Potion of Haste", "50 gp"), ("Scroll of Regenerate (7th)", "70 gp"),
                ("Elixir of Rejuvenation", "80 gp"),
            ],
        }

        self.permanents_by_tier = {
            "low": [
                ("+1 Weapon", "35 gp", "Common"),
                ("+1 Armor", "160 gp", "Common"),
                ("Bag of Holding (Type I)", "75 gp", "Common"),
                ("Hat of Disguise", "45 gp", "Common"),
                ("Boots of Elvenkind", "35 gp", "Common"),
                ("Goggles of Night", "150 gp", "Common"),
                ("Handwraps of Mighty Blows +1", "35 gp", "Common"),
                ("Wayfinder", "28 gp", "Uncommon"),
                ("+1 Striking Weapon", "100 gp", "Common"),
                ("Bracers of Armor I", "160 gp", "Common"),
                ("Cloak of Resistance (+1)", "160 gp", "Common"),
            ],
            "mid": [
                ("+1 Striking Weapon", "100 gp", "Common"),
                ("+1 Resilient Armor", "340 gp", "Common"),
                ("Winged Boots", "850 gp", "Common"),
                ("Ring of Energy Resistance", "245 gp", "Common"),
                ("Cloak of Elvenkind", "300 gp", "Uncommon"),
                ("Bracers of Missile Deflection", "52 gp", "Common"),
                ("+2 Striking Weapon", "1060 gp", "Common"),
                ("Wand of Manifold Missiles (3rd)", "160 gp", "Common"),
                ("Staff of Healing", "90 gp", "Common"),
                ("Ring of the Ram", "220 gp", "Common"),
            ],
            "high": [
                ("+2 Greater Striking Weapon", "4300 gp", "Common"),
                ("+2 Greater Resilient Armor", "4300 gp", "Common"),
                ("Ring of Spell Turning", "16000 gp", "Uncommon"),
                ("Staff of Power", "10000 gp", "Rare"),
                ("Belt of Giant Strength", "17000 gp", "Uncommon"),
                ("+3 Greater Striking Weapon", "16500 gp", "Common"),
                ("Aeon Stone (Lavender and Green Ellipsoid)", "16000 gp", "Uncommon"),
                ("Cloak of the Bat", "10000 gp", "Uncommon"),
                ("Helm of Brilliance", "19000 gp", "Rare"),
            ],
        }

        self.art_objects_by_tier = {
            "low": [
                "a silver chalice (10 gp)", "an ivory comb (5 gp)",
                "a set of gold-inlaid bone dice (8 gp)",
                "a velvet pouch of polished agates (12 gp)",
                "a brass compass with celestial markings (15 gp)",
                "a carved jade figurine of a horse (20 gp)",
                "a silk scarf with gold thread (7 gp)",
                "an ornate pewter mug with gemstone inlay (9 gp)",
            ],
            "mid": [
                "a gold-inlaid obsidian dagger (50 gp)",
                "a ruby-studded silver brooch (80 gp)",
                "a masterwork silk tapestry (60 gp)",
                "a set of mithral chess pieces (100 gp)",
                "an orichalcum holy symbol (75 gp)",
                "a crystal decanter with gold stopper (45 gp)",
                "a carved dragonbone flute (90 gp)",
                "a jeweled music box that plays itself (120 gp)",
            ],
            "high": [
                "a diamond-studded platinum crown (500 gp)",
                "a painting by a legendary artist (300 gp)",
                "a chest of raw adamantine ingots (800 gp)",
                "a fist-sized star sapphire (1,000 gp)",
                "an ancient gold scepter of office (750 gp)",
                "a set of dragon-scale armor ornaments (600 gp)",
                "a mythril-framed mirror of perfect reflection (450 gp)",
            ],
        }

        self.gem_types = {
            "low": [
                ("agate", 5), ("turquoise", 5), ("moonstone", 10),
                ("onyx", 10), ("jasper", 8), ("lapis lazuli", 12),
                ("malachite", 8), ("tiger eye", 5),
            ],
            "mid": [
                ("amethyst", 50), ("garnet", 75), ("topaz", 100),
                ("aquamarine", 80), ("peridot", 50), ("tourmaline", 60),
                ("pearl", 75), ("citrine", 50),
            ],
            "high": [
                ("ruby", 500), ("sapphire", 500), ("emerald", 750),
                ("diamond", 1000), ("black opal", 600), ("fire opal", 400),
                ("star ruby", 800), ("jacinth", 1000),
            ],
        }

        # ── Tavern data ───────────────────────────────────────────────
        self.tavern_names = [
            "The Rusty Pick", "The Leaping Leshy", "The Thirsty Tengu",
            "The Clockwork Pint", "The Gilded Goblin", "The Crimson Chalice",
            "The Broken Anvil", "The Wandering Wyrm", "The Drunken Dragon",
            "The Silver Serpent", "The Smoking Cauldron", "The Last Lantern",
            "The Hanged Man", "The Copper Kettle", "The Pegasus & Crown",
            "The Blind Basilisk", "The Salty Dog", "The Prancing Pony",
            "The Sleeping Griffin", "The Iron Flagon", "The Fey Court",
            "The Skeleton Key", "The Alchemist's Flask", "The Velvet Curtain",
        ]

        self.drinks = [
            ("Mutagen Stout", "1 sp", "Thick and bitter"),
            ("Alchemical Absinthe", "5 sp", "Glows faintly green"),
            ("Fey-Touched Mead", "3 sp", "Sweet with a floral finish"),
            ("Dragon's Breath Whiskey", "1 gp", "Burns going down. Literally."),
            ("Grave-Dust Porter", "2 sp", "Smoky and dark"),
            ("Sunburst Cider", "1 sp", "Light and crisp"),
            ("Witch's Brew Ale", "2 sp", "Changes color every few minutes"),
            ("Dwarven Triple-Malt", "5 sp", "Will knock a human flat"),
            ("Leshy Lemonade", "1 sp", "Non-alcoholic, faintly sparkles"),
            ("Hellknight Red", "8 sp", "A Chelaxian vintage, bold and dry"),
            ("Goblin Gutrot", "1 cp", "Made from... best not to ask"),
            ("Kyonin Elderflower Wine", "1 gp", "Elven wine, centuries-old recipe"),
        ]

        self.specialty_dishes = [
            ("Owlbear Steak, Rare", "8 sp", "+1 circumstance bonus to Athletics checks for 1 hour"),
            ("Fey Mushroom Risotto", "5 sp", "Gain low-light vision for 1 hour"),
            ("Fire-Pepper Stew", "3 sp", "+1 circumstance bonus to saves vs cold effects for 4 hours"),
            ("Troll-Blood Broth", "1 gp", "Gain Fast Healing 1 for 10 minutes (does not stack)"),
            ("Ironskin Dumplings", "6 sp", "+1 circumstance bonus to saves vs poison for 1 hour"),
            ("Dreamer's Tea", "4 sp", "+1 circumstance bonus to Perception checks for 1 hour"),
            ("Giant's Portion Roast", "1 gp", "Counts as full day's rations; +2 temporary HP for 8 hours"),
            ("Undine Sashimi", "1 gp", "Hold breath for twice as long for 4 hours"),
        ]

        self.tavern_events = [
            "A high-stakes game of cards has just turned violent.",
            "A terrible bard is singing off-key, and patrons are threatening them.",
            "The city guard is conducting a tense search for a fugitive.",
            "A cloaked figure is quietly offering coin for 'discreet muscle'.",
            "The tavern keeper is complaining about a monster in the cellar.",
            "Two adventuring parties are arguing over a bounty.",
            "A drunken wizard is making objects float uncontrollably.",
            "Someone just collapsed face-first into their soup — poisoned?",
            "A recruitment poster has been defaced with anti-government slogans.",
            "The entire tavern goes silent when the party walks in.",
            "A fistfight has broken out between a dwarf and a half-orc over a gambling debt.",
            "A traveling merchant is auctioning off a mysterious locked box.",
            "A local celebrity just walked in and everyone is staring.",
            "The tavern cat has knocked something off the mantle, revealing a hidden compartment.",
            "A group of Hellknights just entered and is demanding to see everyone's papers.",
            "The evening entertainment — a puppet show — has taken a disturbingly violent turn.",
        ]

        self.food_items = [
            "Roasted Boar with Cave Moss (5 sp)", "Spiced Beetle Skewers (2 sp)",
            "Hearty Troll-Bone Stew (4 sp)", "Fried Manticore Bites (8 sp)",
            "Slab of Mystery Meat (1 sp)", "Garlic and Root Mash (3 sp)",
            "Grilled River Trout with Herbs (4 sp)",
            "Fire-Roasted Mushroom Platter (3 sp)",
            "Smoked Sausage on Black Bread (2 sp)",
            "Cheese and Pickle Board (3 sp)",
        ]

        self.tavern_room_descriptions = [
            "A dimly lit common room with rough-hewn benches and a roaring fireplace.",
            "A surprisingly clean establishment with polished wood and brass fittings.",
            "A smoky den with low ceilings and candle-lit alcoves for private meetings.",
            "A raucous open-floor tavern with sawdust on the floor and questionable stains on the walls.",
            "An upscale wine bar with velvet seating and a crystal chandelier (chipped, but still impressive).",
            "A converted warehouse with long communal tables and a fighting pit in the corner.",
        ]

        self.tavern_entertainment = [
            "A halfling juggler tossing flaming knives",
            "A melancholy elven harpist playing ballads of the old world",
            "An arm-wrestling tournament (DC 20 Athletics to win, prize: 5 gp)",
            "A goblin comedian with surprisingly good material",
            "A darts tournament using actual daggers (DC 18 Ranged attack)",
            "A fortune teller (Harrow reading) in the back corner",
            "A storytelling circle — buy a round and share a tale for free drinks",
            "A mechanical clockwork band playing popular tavern tunes",
        ]

        # ── Shop data ─────────────────────────────────────────────────
        self.shop_types = [
            ("Alchemist's Apothecary", "cons", "potions, elixirs, and alchemical items"),
            ("Weaponsmith & Forge", "perm", "weapons and shields"),
            ("Arcane Curiosities", "perm", "wands, staves, and enchanted items"),
            ("General Adventuring Gear", "mixed", "rope, rations, and adventuring supplies"),
            ("Shady Pawn Shop", "mixed", "secondhand goods of questionable origin"),
            ("Armor & Outfitter", "perm", "armor, clothing, and protective gear"),
            ("Scroll & Tome Emporium", "cons", "scrolls, spellbooks, and maps"),
            ("Temple Provisions", "cons", "holy water, healing supplies, religious texts"),
            ("Exotic Imports", "mixed", "rare goods from distant lands"),
            ("Siege Surplus", "perm", "military-grade equipment and siege tools"),
            ("Jeweler & Gemcutter", "mixed", "gems, jewelry, and precious metals"),
            ("Trapmaker's Workshop", "cons", "snares, traps, and alarm devices"),
        ]

        self.shop_names = [
            "Iron & Anvil", "The Adventurer's Satchel", "Mystic Weaves",
            "Potions & Poisons", "The Dusty Shelf", "Silvermark Trading",
            "The Gilt Grimoire", "Hammer & Tongs", "The Rune Barrel",
            "Copperkettle Supplies", "The Enchanted Emporium",
            "Brassbell's Bazaar", "The Wanderer's Rest", "Stoneheart Smithy",
            "The Third Eye", "Grimjaw's Goods",
        ]

        self.shop_services = [
            ("Identify Magic Item", "Arcana check or pay 5 gp per item level"),
            ("Repair Item", "Crafting check or pay 50% of item's value"),
            ("Commission Custom Item", "Item cost + 10-25% surcharge, 1d4 weeks"),
            ("Appraise Valuables", "DC {dc} Society check or 1 gp fee"),
            ("Enchant an Item (+1 potency)", "Cost: item's next tier price + 50 gp"),
            ("Silvering a Weapon", "2 gp, takes 1 day"),
            ("Cold Iron Coating", "5 gp, takes 2 days"),
        ]

        # ── Magic item data ───────────────────────────────────────────
        self.magic_item_origins = [
            "bears the hallmarks of ancient {biome} craftsmanship",
            "has a faint inscription in a dead language",
            "radiates an aura of abjuration magic",
            "was reportedly looted from a dragon's hoard",
            "is warm to the touch even in freezing conditions",
            "hums faintly when danger is near",
            "is decorated with the symbol of a forgotten deity",
            "appears mundane until invested",
            "was forged during a solar eclipse, according to legend",
            "was once wielded by a famous Pathfinder agent",
            "has a maker's mark from the dwarven Sky Citadel of Janderhoff",
            "is crafted from an unknown greenish metal",
        ]

        self.magic_item_quirks = [
            "It glows softly in the dark, shedding dim light in a 5-ft radius.",
            "It whispers warnings in Sylvan when fiends are nearby.",
            "It feels heavier than it should (no mechanical effect).",
            "Its previous owner's initials are scratched into the surface.",
            "It occasionally produces the scent of wildflowers.",
            "When drawn, nearby flames flicker and dim.",
            "It turns ice-cold in the presence of undead within 60 ft.",
            "It hums a low note when pointed north.",
            "Small illusory butterflies appear around it when unused for 1 hour.",
            "It leaves a faint trail of sparks when swung.",
            "It changes color to match its wielder's mood.",
            "Tiny runes scroll across its surface in an endless loop.",
        ]

        self.magic_item_curses = [
            "The item cannot be willingly removed once invested (DC {dc} Will save to unattune).",
            "The wielder becomes convinced this item is the most valuable thing they own (no save, roleplay effect).",
            "Once per day at an inconvenient moment, the item activates on its own (GM's discretion).",
            "The wielder takes a -1 status penalty to saves vs fear while invested.",
            "The item whispers dark suggestions during rest, imposing a -1 penalty to the next day's first Will save.",
            "The item slowly corrupts: after 1 week, the wielder must succeed at a DC {dc} Will save or gain a minor alignment shift toward evil.",
        ]

        self.magic_item_traits = [
            "Invested", "Magical", "Evocation", "Transmutation",
            "Abjuration", "Divination", "Enchantment", "Illusion",
            "Necromancy", "Conjuration",
        ]

        self.activation_actions = [
            "Interact (1 action)", "Command (1 action)",
            "Envision (1 action)", "Cast a Spell (varies)",
            "Interact (2 actions)", "Envision + Command (2 actions)",
            "Free action (trigger-based)",
        ]

        # ── Puzzle data ───────────────────────────────────────────────
        self.riddles = [
            ("I have cities, but no houses. I have mountains, but no trees. I have water, but no fish. What am I?", "A map"),
            ("The more you take, the more you leave behind. What am I?", "Footsteps"),
            ("I speak without a mouth and hear without ears. I have no body, but I come alive with the wind.", "An echo"),
            ("I can be cracked, made, told, and played. What am I?", "A joke"),
            ("What has roots nobody sees, is taller than trees, up up it goes, yet never grows?", "A mountain"),
            ("Forward I am heavy, but backward I am not. What am I?", "A ton"),
            ("I have keys but no locks. I have space but no room. You can enter but can't go inside. What am I?", "A keyboard... or a harpsichord in Golarion"),
            ("What can fill a room but takes up no space?", "Light (or darkness)"),
        ]

        self.logic_puzzles = [
            "Three levers control three doors. Each lever toggles exactly two doors. The party must open all three doors simultaneously. <b>Solution:</b> Pull levers 1 and 3 (each toggles doors in a specific combination). DC {dc} Logic Lore or DC {dc_hard} Intelligence check to deduce the pattern.",
            "Four colored crystals must be placed on four pedestals. A clue reads: 'Red must not face blue. Green stands beside gold. Blue is never first.' <b>Solution:</b> Green, Gold, Red, Blue. DC {dc} Arcana or Perception to notice the clue inscribed on each pedestal.",
            "A chessboard floor has safe and trapped squares. The party must cross (8 squares). Trapped squares deal {dd}d6 damage. <b>Solution:</b> Follow the knight's path (L-shaped moves only). DC {dc} Society or Games Lore to recognize the pattern. DC {dc_hard} Perception to spot which squares are worn from use.",
            "A sequence of runes must be activated in order: Sun, Moon, Star, Crown. The room shows them shuffled as Moon, Crown, Sun, Star. <b>Solution:</b> Activate in the original order. DC {dc} Arcana or Religion to read the creation myth that gives the order.",
        ]

        self.physical_puzzles = [
            "A heavy stone door requires simultaneous pressure on two plates 30 ft apart. <b>Solution:</b> Two characters press simultaneously, or use a heavy object on one plate. DC {dc} Athletics to hold the plate. Failing causes the door to slam: {dd}d6 bludgeoning damage (DC {dc} Reflex for half).",
            "A flooded room has a drain blocked by debris. The water level rises 1 ft per round. <b>Solution:</b> DC {dc} Athletics to clear the drain (3 successes needed). DC {dc} Crafting to improvise a tool for a +2 bonus. If the water reaches 10 ft, characters must swim (DC {dc} Athletics).",
            "A rotating cylinder bridge must be crossed while it spins. <b>Solution:</b> DC {dc} Acrobatics to cross (2 checks needed). DC {dc_hard} Athletics to stop the rotation. Falling deals {dd}d6 damage into a pit below.",
            "A series of weighted platforms must balance to open a vault. <b>Solution:</b> Total weight on both sides must equal 500 lbs. DC {dc} Crafting or Society to calculate. DC {dc_easy} Perception to notice the weight markings.",
        ]

        self.magical_puzzles = [
            "An illusion conceals the true path. Three doors appear identical but only one is real. <b>Solution:</b> DC {dc} Perception (disbelieve) or DC {dc_easy} Arcana to detect illusion school. Touching a false door deals {dd}d6 force damage (basic Reflex DC {dc}).",
            "A ward requires a specific spell tradition to open. Four runes glow: Arcane (blue), Divine (gold), Occult (purple), Primal (green). <b>Solution:</b> Cast any spell of the matching tradition near the correct rune. DC {dc} Arcana/Religion/Occultism/Nature to identify which rune matches. Non-spellcasters can use DC {dc_hard} Thievery to bypass.",
            "A mirror dimension duplicates the party. Copies mimic movements inversely. The party must perform actions their copies cannot mirror. <b>Solution:</b> Speak a palindrome (automatic), perform an asymmetric action (DC {dc} Performance), or break the mirror (AC 15, Hardness 5, HP {level * 5}). The copies attack if provoked: use party's stats at -2 to all DCs.",
            "A sentient door demands a 'worthy offering.' It rejects gold, gems, and weapons. <b>Solution:</b> Offer a memory (Will save DC {dc}, lose a skill proficiency for 24 hours), a secret (Diplomacy DC {dc} to convince it of value), or blood (take {dd}d4 damage, no save). DC {dc_hard} Arcana to find a loophole in the enchantment.",
        ]

        self.puzzle_rewards = [
            "a hidden cache containing {reward_gp} gp and {consumable}",
            "access to the next chamber, which contains {permanent}",
            "a blessing: +1 status bonus to all saves for 24 hours",
            "a shortcut that bypasses the next {level} rooms",
            "an ancient map revealing the location of a hidden treasure vault",
            "a boon from a grateful spirit: one free raise dead or restoration within 1 week",
        ]

        # ── Quest data ────────────────────────────────────────────────
        self.quest_complications = [
            "A rival party is pursuing the same objective.",
            "The patron is not who they claim to be.",
            "The target location has been recently claimed by a new threat.",
            "An innocent person will be harmed if the quest succeeds as planned.",
            "A powerful faction opposes the quest's completion.",
            "The reward is cursed or comes with strings attached.",
            "A key informant has been murdered; the trail has gone cold.",
            "The quest requires entering territory belonging to a dangerous group.",
            "Weather or natural disaster threatens the timeline.",
            "A party member has a personal connection to the villain.",
            "The local authorities have outlawed the party's mission.",
            "An ally betrays the party at a critical moment.",
        ]

        self.quest_factions = [
            "the Pathfinder Society", "the Aspis Consortium",
            "the Hellknights (Order of the Nail)", "the Eagle Knights of Andoran",
            "the Whispering Way", "the Red Mantis Assassins",
            "the Firebrands", "the Knights of Lastwall",
            "the Bellflower Network", "a local thieves' guild",
            "a merchant consortium", "a druidic circle",
            "a noble house", "a temple of Pharasma",
            "the Magaambya", "a dwarven mining clan",
        ]

        self.quest_urgency = [
            ("Immediate", "Must be completed within 24 hours or consequences escalate."),
            ("Urgent", "3-5 days before the situation becomes critical."),
            ("Standard", "1-2 weeks; the patron is patient but expects results."),
            ("Long-term", "Ongoing investigation; no hard deadline, but periodic check-ins."),
            ("Countdown", "A specific event triggers in {days} days. The party must act before then."),
        ]

        # ── Weather data ──────────────────────────────────────────────
        self.weather_conditions = {
            "City": [
                ("Clear skies, mild temperature", "No mechanical effects."),
                ("Light rain", "Outdoor fires require shelter. Ranged attacks beyond 60 ft take -1 penalty."),
                ("Heavy fog", "Concealed beyond 30 ft. -2 to Perception checks relying on sight."),
                ("Overcast and windy", "Gusts: DC 15 Athletics to maintain flight. Ranged projectiles beyond 30 ft take -1."),
                ("Scorching heat wave", "DC 18 Fortitude save each hour of strenuous activity or gain fatigued condition."),
                ("Thunderstorm", "Concealed beyond 60 ft. Lightning strikes: 1% chance per round in open areas, 6d6 electricity damage."),
                ("Snowfall", "Difficult terrain outdoors after 2 hours. -2 to Survival checks for tracking."),
            ],
            "Forest": [
                ("Dappled sunlight through the canopy", "No mechanical effects."),
                ("Morning mist", "Concealed beyond 60 ft until midday. +2 to Stealth in forested areas."),
                ("Steady rain", "Difficult terrain (mud). Fires require shelter. -1 to Perception (sound masked)."),
                ("Humid and still", "DC 15 Fort save per hour of heavy activity or fatigued."),
                ("Windstorm", "Greater difficult terrain. Falling branches: 10% chance per hour, 2d6 bludgeoning."),
                ("Magical aurora (fey influence)", "All Enchantment spells gain +1 DC. Fey creatures gain +1 to attack rolls."),
            ],
            "Dungeon": [
                ("Stale, still air", "No mechanical effects."),
                ("Dripping moisture, high humidity", "Metal items begin to corrode after 24 hours if not maintained. -1 to Crafting checks."),
                ("Cold drafts from deeper levels", "Without cold protection, fatigued after 4 hours."),
                ("Toxic fumes in low areas", "DC {dc} Fort save each hour in low areas or sickened 1."),
                ("Magical darkness", "Even darkvision is suppressed. Only magical light of 4th level or higher functions."),
                ("Echoing acoustics", "-2 to Stealth (sound). +2 to Perception (hearing)."),
            ],
            "Desert": [
                ("Clear sky, blazing sun", "DC 20 Fort save each hour or fatigued. Water consumption doubled."),
                ("Sandstorm", "Concealed beyond 20 ft. 1d4 slashing damage per round unprotected. Difficult terrain."),
                ("Hot wind (sirocco)", "DC 18 Fort save per hour or fatigued. Wind extinguishes non-magical flames."),
                ("Cool desert night", "Temperature drops. Without warmth, DC 15 Fort per hour or fatigued."),
                ("Mirages", "DC 22 Perception to distinguish real from mirage. Navigation DCs increase by 5."),
                ("Dust devil", "DC 20 Reflex in affected area or knocked prone + 2d6 bludgeoning. Moves randomly."),
            ],
            "Swamp": [
                ("Muggy and overcast", "No mechanical effects beyond discomfort."),
                ("Dense fog", "Concealed beyond 15 ft. Navigation DC +5. Ambush chance increased."),
                ("Torrential rain", "Water rises 1 ft per hour. Terrain becomes greater difficult terrain."),
                ("Insect swarm weather", "DC 15 Fort per hour or sickened 1 (insect bites/disease exposure)."),
                ("Methane bubbles", "Open flames have 25% chance per round to ignite gas: 4d6 fire in 15-ft burst."),
                ("Eerie calm", "Supernaturally still. +2 to Perception, but Will saves vs fear effects take -1."),
            ],
            "Mountains": [
                ("Clear but bitterly cold", "Without cold protection, DC 18 Fort per hour or fatigued."),
                ("Blizzard", "Concealed beyond 10 ft. Difficult terrain. DC 22 Survival to navigate."),
                ("High winds", "DC 20 Athletics to maintain balance on exposed surfaces. Flight impossible."),
                ("Thin air (high altitude)", "DC 15 Fort per hour or fatigued. All physical activity DCs +2."),
                ("Avalanche conditions", "Loud noises (DC 15 flat check) trigger avalanche: 8d6 bludgeoning, buried."),
                ("Freezing rain", "All surfaces become icy: DC 18 Acrobatics or prone. Difficult terrain."),
            ],
            "Coastal": [
                ("Sunny with a sea breeze", "No mechanical effects. Pleasant sailing."),
                ("Thick sea fog", "Concealed beyond 30 ft. Ship navigation DC +5."),
                ("Tropical storm", "Ship checks DC +5. 4d6 bludgeoning from waves if on deck. Difficult terrain."),
                ("High tide", "Low-lying areas flood. Coastal caves become submerged."),
                ("Rogue wave conditions", "DC 20 flat check per hour at sea: 6d6 bludgeoning to all on deck."),
                ("Calm seas, scorching sun", "DC 18 Fort per hour or fatigued (heat). Perfect visibility."),
            ],
            "Arctic": [
                ("Blinding whiteout blizzard", "Concealed beyond 5 ft. Greater difficult terrain. DC 25 Survival to navigate. DC 20 Fort per hour or fatigued."),
                ("Clear and extremely cold (-40F)", "DC 22 Fort per hour or take 2d6 cold damage. Water freezes in 10 minutes."),
                ("Light snowfall", "Concealed beyond 120 ft. Normal terrain. Tracking DC -5 (fresh tracks visible)."),
                ("Aurora borealis (magical)", "Primal and divine spells gain +1 DC. Supernatural unease: DC 15 Will or frightened 1."),
                ("Ice storm", "2d6 cold + 2d6 bludgeoning per round in open areas. Difficult terrain."),
                ("Unseasonable thaw", "Ice becomes thin (DC 20 Perception to spot). Crevasses open (DC 22 Reflex or fall 40 ft)."),
            ],
            "Underground": [
                ("Stable cave temperature", "No mechanical effects (naturally cool, ~55F)."),
                ("Flooding from surface rain", "Water levels rise 1 ft per hour in low tunnels. Escape routes may be cut off."),
                ("Phosphorescent bloom", "Fungal glow provides dim light throughout. +2 to Nature checks in the area."),
                ("Tremor activity", "DC 18 Reflex per tremor (1d4 per hour) or prone. Loose rocks deal 2d6 bludgeoning."),
                ("Gas pocket detected", "DC {dc} Survival or Crafting to detect. Open flames ignite: 6d6 fire in 20-ft burst."),
                ("Psychic static zone", "Occult spells take -1 DC. Mental skill checks take -1. Source unknown."),
            ],
        }

        # ── Trap data ─────────────────────────────────────────────────
        self.trap_types = [
            {
                "name": "Spear Launcher",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap"],
                "description": "A pressure plate triggers concealed spears that thrust from the wall.",
                "trigger": "A creature steps on the pressure plate.",
                "damage_type": "piercing",
                "damage_dice": "d8",
                "save": "Reflex",
                "reset": "Manual (10 minutes to reset)",
            },
            {
                "name": "Poison Dart Gallery",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap", "Poison"],
                "description": "A tripwire triggers a barrage of darts coated in poison from hidden wall ports.",
                "trigger": "A creature crosses the tripwire.",
                "damage_type": "piercing + poison",
                "damage_dice": "d4",
                "save": "Reflex",
                "reset": "Manual (requires reloading darts)",
            },
            {
                "name": "Flame Jet",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap", "Fire"],
                "description": "Concealed nozzles spray alchemical fire across a 15-ft cone.",
                "trigger": "A creature opens the trapped door or chest.",
                "damage_type": "fire",
                "damage_dice": "d6",
                "save": "Reflex",
                "reset": "Automatic (refills from a reservoir after 1 minute)",
            },
            {
                "name": "Crushing Wall",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap"],
                "description": "The walls of a 10-ft section begin closing, crushing everything inside.",
                "trigger": "Pressure plate at the center of the room.",
                "damage_type": "bludgeoning",
                "damage_dice": "d10",
                "save": "Reflex",
                "reset": "Automatic (1 hour to fully retract)",
            },
            {
                "name": "Glyph of Warding",
                "type": "Magical Trap",
                "traits": ["Magical", "Trap", "Abjuration"],
                "description": "A concealed magical glyph explodes when triggered, dealing energy damage.",
                "trigger": "A creature enters the warded area without speaking the password.",
                "damage_type": "force",
                "damage_dice": "d6",
                "save": "Reflex",
                "reset": "None (single use unless re-cast)",
            },
            {
                "name": "Spectral Grasp Haunt",
                "type": "Haunt",
                "traits": ["Haunt", "Necromancy"],
                "description": "Ghostly hands reach from the walls, attempting to drag creatures into the stone.",
                "trigger": "A living creature enters the haunted area.",
                "damage_type": "negative",
                "damage_dice": "d8",
                "save": "Fortitude",
                "reset": "Automatic (1 hour, or permanently destroyed by consecrate ritual)",
            },
            {
                "name": "Acid Pit",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap"],
                "description": "A false floor drops creatures into a 20-ft pit filled with acid.",
                "trigger": "Weight exceeds 50 lbs on the false floor.",
                "damage_type": "bludgeoning (fall) + acid",
                "damage_dice": "d6",
                "save": "Reflex",
                "reset": "Manual (must replace false floor)",
            },
            {
                "name": "Phantasmal Killer Rune",
                "type": "Magical Trap",
                "traits": ["Magical", "Trap", "Illusion", "Fear", "Mental", "Death"],
                "description": "A rune projects the target's worst fear. The image attacks the creature's mind.",
                "trigger": "A creature reads the rune or steps within 5 ft.",
                "damage_type": "mental",
                "damage_dice": "d10",
                "save": "Will",
                "reset": "Automatic (24 hours)",
            },
            {
                "name": "Electrified Floor",
                "type": "Mechanical Trap",
                "traits": ["Mechanical", "Trap", "Electricity"],
                "description": "Metal floor plates discharge stored electrical energy when touched.",
                "trigger": "A creature in contact with the metal floor when the circuit completes.",
                "damage_type": "electricity",
                "damage_dice": "d6",
                "save": "Reflex",
                "reset": "Automatic (recharges in 1 round)",
            },
            {
                "name": "Soulbound Sentinel",
                "type": "Haunt",
                "traits": ["Haunt", "Enchantment", "Mental"],
                "description": "The spirit of a dead guardian possesses a creature, forcing them to attack allies.",
                "trigger": "A creature touches the sentinel's remains.",
                "damage_type": "mental (possession)",
                "damage_dice": "d4",
                "save": "Will",
                "reset": "Automatic (1 hour, or permanently destroyed by a ritual)",
            },
        ]

        # ── Rumor data ────────────────────────────────────────────────
        self.rumor_templates_true = [
            "A caravan carrying a shipment of {item} was attacked by {monster} on the road to {location}. The goods are still scattered at the site.",
            "The old {building} on the edge of town has lights in the windows at night. Locals say a cult of {deity} has moved in.",
            "The local lord's {relative} has gone missing. The guards are searching quietly to avoid a scandal.",
            "A sinkhole opened near {location}, revealing what appears to be an ancient {dungeon_type}.",
            "A group of {ancestry} refugees arrived last week, fleeing {threat} from the east.",
            "The {profession} guild is secretly stockpiling weapons. They're preparing for something.",
        ]

        self.rumor_templates_partial = [
            "They say a dragon was seen flying over {location}. (Partially true: it was actually a wyvern, but it IS terrorizing travelers.)",
            "The mayor is supposedly a vampire. (Partially true: the mayor is being blackmailed BY a vampire, not one themselves.)",
            "There's treasure buried under the old {building}. (Partially true: there's a sealed vault, but it's trapped and mostly empty — one valuable item remains.)",
            "The new {profession} in town is a spy. (Partially true: they're actually a bounty hunter tracking someone in the town.)",
            "Plague is coming from the south. (Partially true: a disease is spreading, but it's magical, not natural, and it's curable.)",
        ]

        self.rumor_templates_false = [
            "The king is dead and the government is covering it up. (False: the king is alive but ill. The rumor was started by political rivals.)",
            "Drinking water from the eastern well grants visions of the future. (False: the well is mildly contaminated with hallucinogenic fungi.)",
            "A lich has taken up residence in the graveyard. (False: a prankster necromancer is animating skeletons as a joke. Creature Level 3 at most.)",
            "The {profession} guild is run by a devil in disguise. (False: the guild leader is corrupt but entirely mortal.)",
            "There's a bounty of 1,000 gp on anyone who enters the {location}. (False: the bounty was posted as a dare by drunk adventurers.)",
        ]

        # ── Settlement data ───────────────────────────────────────────
        self.settlement_sizes = [
            ("Thorpe", "1-20 residents", "No formal government"),
            ("Hamlet", "21-60 residents", "Village elder or council"),
            ("Village", "61-200 residents", "Mayor or council, part-time militia"),
            ("Small Town", "201-2,000 residents", "Mayor, sheriff, small council"),
            ("Large Town", "2,001-5,000 residents", "Town council, full-time guard"),
            ("Small City", "5,001-10,000 residents", "City council and lord mayor"),
            ("Large City", "10,001-25,000 residents", "Full bureaucracy, multiple districts"),
            ("Metropolis", "25,001+ residents", "Complex government, guilds, factions"),
        ]

        self.government_types = [
            "Autocracy (ruled by a single powerful figure)",
            "Council (elected or appointed representatives)",
            "Theocracy (ruled by a religious order)",
            "Magocracy (ruled by spellcasters)",
            "Military Junta (martial law by a general or warlord)",
            "Democracy (town-hall style direct vote)",
            "Plutocracy (wealthiest merchants control policy)",
            "Anarchy (no formal government; factions vie for power)",
            "Hereditary Monarchy (noble family lineage)",
            "Tribal Council (elders from prominent families)",
        ]

        self.settlement_locations = [
            "a bustling marketplace with exotic goods",
            "a temple district with shrines to multiple deities",
            "an ancient library rumored to hold forbidden texts",
            "a fighting arena hosting weekly gladiatorial bouts",
            "a notorious tavern district known for crime",
            "a walled noble quarter with manicured gardens",
            "an artisan's row with smiths, weavers, and alchemists",
            "a harbor with both legitimate merchants and smugglers",
            "a crumbling wizard's tower at the edge of town",
            "a memorial park honoring fallen adventurers",
            "an underground black market accessible through a bakery",
            "a bathhouse that doubles as a spy network hub",
        ]

        self.settlement_problems = [
            "A series of disappearances in the poorer districts",
            "Tensions between two rival guilds threatening violence",
            "A monster sighting near the main trade road",
            "Crop failure threatening famine within the month",
            "A disease outbreak with no known cure",
            "Corruption in the local guard — criminals operate openly",
            "A cult has been recruiting from the vulnerable population",
            "Tax collectors are extorting far beyond legal rates",
            "An ancestral tomb has been desecrated and undead are stirring",
            "Water supply has been poisoned — source unknown",
            "A powerful merchant is buying up all property, displacing residents",
            "Strange magical phenomena: objects float, lights flicker, etc.",
        ]

        self.settlement_name_parts = {
            "prefix": [
                "River", "Stone", "Iron", "Gold", "Silver", "Black", "White",
                "Red", "Green", "Frost", "Storm", "Bright", "Dark", "Old",
                "High", "Deep", "Copper", "Thorn", "Oak", "Wolf", "Eagle",
                "Raven", "Dragon", "Crown", "Moon", "Sun", "Star", "Shadow",
            ],
            "suffix": [
                "haven", "hold", "gate", "ford", "bridge", "wick", "bury",
                "dale", "vale", "stead", "keep", "watch", "port", "hollow",
                "crest", "fall", "march", "stone", "field", "wall", "tower",
                "reach", "rest", "moor", "wood", "glen",
            ],
        }

        # ── Random event data ─────────────────────────────────────────
        self.travel_events = [
            "A merchant caravan approaches from the opposite direction, heavily guarded and willing to trade.",
            "The party discovers a wounded traveler on the roadside, barely conscious.",
            "A natural landmark is visible: a massive ancient tree, a strange rock formation, or a crystal-clear spring.",
            "The road ahead has been washed out. A detour through dangerous territory is needed.",
            "A patrol of soldiers approaches, asking for identification and travel papers.",
            "The party stumbles upon a recently abandoned campsite. Food is still warm.",
            "A wild animal blocks the path — not hostile, but not moving either.",
            "Weather changes suddenly and dramatically.",
            "A signpost at a crossroads has been vandalized; all directions have been changed.",
            "The party hears screaming in the distance — it could be a fight, a monster, or a trick.",
            "A traveling performer or troupe offers to share the road for safety.",
            "An earthquake or tremor shakes the ground briefly.",
        ]

        self.downtime_events = [
            "A letter arrives from an old contact with urgent news.",
            "A local festival begins, drawing crowds and opportunities.",
            "A fire breaks out in the neighborhood — the party can help or be affected.",
            "An old enemy (or their agent) is spotted in the same city.",
            "A job offer arrives: simple guard duty that pays well. Too well.",
            "A mysterious package is delivered, addressed to a party member by a name they don't use.",
            "Local authorities announce a new law that directly affects the party.",
            "A friend or ally sends word that they're in trouble.",
            "A rare eclipse or celestial event occurs, with both scientific and superstitious significance.",
            "Property damage: the party's lodgings are vandalized, robbed, or cursed overnight.",
            "An NPC the party helped previously returns with a gift — or a problem.",
            "A public execution is scheduled; the condemned claims innocence.",
        ]

        self.discovery_events = [
            "An ancient ruin partially exposed by erosion or a landslide",
            "A hidden cave behind a waterfall or dense brush",
            "A battlefield from a long-forgotten war, with rusted weapons and bones",
            "A fey crossing — the boundary between the Material Plane and the First World",
            "An abandoned mine with signs of recent activity",
            "A monolith covered in runes that glow when touched",
            "A grove of petrified trees surrounding a central altar",
            "A shipwreck in an impossible location (mountaintop, deep forest)",
            "A perfectly preserved campsite from centuries ago, untouched by time",
            "A doorway standing alone in an open field, leading nowhere — or somewhere",
        ]

    # ── Helper methods ────────────────────────────────────────────────

    def _tier(self, level):
        return "low" if level <= 4 else "mid" if level <= 10 else "high"

    def _dc(self, level):
        return self._dc_table.get(min(max(level, 0), 20), 14 + level)

    def _dc_easy(self, level):
        """DC adjustment: easy (-2)."""
        return self._dc(level) - 2

    def _dc_hard(self, level):
        """DC adjustment: hard (+2)."""
        return self._dc(level) + 2

    def _dc_very_hard(self, level):
        """DC adjustment: very hard (+5)."""
        return self._dc(level) + 5

    def _monster(self, level, biome):
        bd = self.biomes.get(biome, self.biomes["City"])
        lvls = sorted(bd["monsters_by_level"].keys())
        chosen = lvls[0]
        for l in lvls:
            if l <= level:
                chosen = l
        return random.choice(bd["monsters_by_level"][chosen])

    def _currency(self, level):
        """Generate currency appropriate to party level using wealth-by-level."""
        base = self._wealth_by_level.get(min(max(level, 1), 20), 175)
        # A single treasure find is roughly 5-15% of total party wealth for level
        gp = int(base * random.uniform(0.05, 0.15))
        sp = random.randint(0, gp * 2)
        cp = random.randint(0, 50) if level <= 5 else 0
        parts = []
        if gp > 0:
            parts.append(f"{gp} gp")
        if sp > 0:
            parts.append(f"{sp} sp")
        if cp > 0:
            parts.append(f"{cp} cp")
        return ", ".join(parts) if parts else "a few scattered coins"

    def _npc_name(self):
        """Generate a full NPC name."""
        first = random.choice(self.first_names)
        if random.random() < 0.6:
            return f"{first} {random.choice(self.last_names)}"
        return first

    def _ancestry(self):
        """Return an ancestry, occasionally with a versatile heritage."""
        base = random.choice(self.ancestries)
        if random.random() < 0.15:
            heritage = random.choice(self.versatile_heritages)
            return f"{heritage} {base}"
        return base

    def _settlement_name(self):
        """Generate a settlement name."""
        prefix = random.choice(self.settlement_name_parts["prefix"])
        suffix = random.choice(self.settlement_name_parts["suffix"])
        return f"{prefix}{suffix}"

    def _xp_for_encounter(self, level):
        """Return a random encounter difficulty and XP budget."""
        difficulty = random.choice(["Low", "Moderate", "Severe"])
        xp = self._xp_budgets[difficulty]
        return difficulty, xp

    def _consumable_str(self, tier):
        """Return a consumable as a formatted string with price."""
        item = random.choice(self.consumables_by_tier[tier])
        return f"{item[0]} ({item[1]})"

    def _permanent_str(self, tier):
        """Return a permanent item as a formatted string with price and rarity."""
        item = random.choice(self.permanents_by_tier[tier])
        rarity_tag = f" [{item[2]}]" if item[2] != "Common" else ""
        return f"{item[0]} ({item[1]}){rarity_tag}"

    # ── Generator methods ─────────────────────────────────────────────

    def get_npc(self, level=1, biome="City"):
        n = self._npc_name()
        a = self._ancestry()
        p = random.choice(self.professions)
        t = random.choice(self.traits)
        q = random.choice(self.quirks)
        s = random.choice(self.secrets)
        m = random.choice(self.motivations)
        app = random.choice(self.appearances)
        voice = random.choice(self.voice_notes)
        combat = random.choice(self.combat_capabilities)
        connection = random.choice(self.npc_connections)
        bf = random.choice(self.biomes.get(biome, self.biomes["City"])["flavor"])
        disposition = random.choice(["Hostile", "Unfriendly", "Indifferent", "Friendly", "Helpful"])
        dc = self._dc(level)

        templates = [
            # Template 1: Full stat-block style
            (
                f"<b>{n}, {a} {p}</b><br>"
                f"<b>Appearance:</b> {app.capitalize()}.<br>"
                f"<b>Personality:</b> A {t} individual who {q}.<br>"
                f"<b>Voice:</b> {voice.capitalize()}.<br>"
                f"<b>Location:</b> Near {bf}.<br>"
                f"<b>Disposition:</b> {disposition} (DC {dc} Diplomacy to shift).<br>"
                f"<b>Secret:</b> {n.split()[0]} {s}.<br>"
                f"<b>Wants:</b> {m}.<br>"
                f"<b>Combat:</b> {combat}.<br>"
                f"<b>Connection:</b> {n.split()[0]} {connection}."
            ),
            # Template 2: Plot-hook focused
            (
                f"<b>{n} the {t.title()} ({a} {p})</b><br>"
                f"<em>{app.capitalize()}. {voice.capitalize()}.</em><br>"
                f"{n.split()[0]} {q}. Despite their {t} demeanor, they seem to know everyone in the area.<br>"
                f"<b>Plot Hook:</b> {n.split()[0]} {s} and desperately needs the party's help.<br>"
                f"<b>Motivation:</b> {m}.<br>"
                f"<b>Reward:</b> {max(level, 1) * 10} gp or valuable information.<br>"
                f"<b>Connection:</b> {n.split()[0]} {connection}."
            ),
            # Template 3: Social encounter style
            (
                f"<b>{n}, {a} {p}</b><br>"
                f"Found near {bf}, this {t} NPC {q}.<br>"
                f"<b>Appearance:</b> {app.capitalize()}.<br>"
                f"<b>Motivation:</b> {m}.<br>"
                f"<b>Complication:</b> {n.split()[0]} {s}.<br>"
                f"<b>Disposition:</b> {disposition} (DC {dc} Diplomacy to shift).<br>"
                f"<b>Social Skills:</b> DC {dc} Deception to lie to them. DC {self._dc_hard(level)} Sense Motive to read their intent.<br>"
                f"<b>Combat:</b> {combat}."
            ),
            # Template 4: Mysterious stranger
            (
                f"<b>A {t} {a} stranger</b><br>"
                f"<em>{app.capitalize()}.</em> This figure lurks near {bf}.<br>"
                f"<b>Voice:</b> {voice.capitalize()}.<br>"
                f"<b>Quirk:</b> {q.capitalize()}.<br>"
                f"<b>True Identity:</b> {n}, a {p.lower()} who {s}.<br>"
                f"<b>Discovering the Truth:</b> DC {self._dc_hard(level)} Society or DC {dc} Diplomacy (Gather Information, 1 hour).<br>"
                f"<b>Wants:</b> {m}.<br>"
                f"<b>If Confronted:</b> {combat}."
            ),
            # Template 5: Faction agent
            (
                f"<b>{n} ({a} {p})</b> &mdash; Agent of {random.choice(self.quest_factions)}<br>"
                f"<b>Appearance:</b> {app.capitalize()}. {voice.capitalize()}.<br>"
                f"<b>Personality:</b> {t.capitalize()} and {q}.<br>"
                f"<b>Mission:</b> {m}.<br>"
                f"<b>Secret:</b> {n.split()[0]} {s}.<br>"
                f"<b>Connection:</b> {n.split()[0]} {connection}.<br>"
                f"<b>Disposition:</b> {disposition}. Will become Helpful if the party aids their faction (DC {self._dc_easy(level)} Diplomacy).<br>"
                f"<b>Combat:</b> {combat}."
            ),
            # Template 6: Quick reference
            (
                f"<b>{n}</b> &mdash; {a} {p} &mdash; <em>{t}</em><br>"
                f"{app.capitalize()}. {q.capitalize()}.<br>"
                f"<b>Wants:</b> {m}.<br>"
                f"<b>Knows:</b> {n.split()[0]} {s} (DC {dc} Diplomacy or Intimidation to learn).<br>"
                f"<b>Voice:</b> {voice}.<br>"
                f"<b>Combat:</b> {combat}."
            ),
        ]
        return random.choice(templates)

    def get_tavern(self, level=1, biome="City"):
        name = random.choice(self.tavern_names)
        bf = random.choice(self.biomes.get(biome, self.biomes["City"])["flavor"])
        room = random.choice(self.tavern_room_descriptions)
        entertainment = random.choice(self.tavern_entertainment)
        event = random.choice(self.tavern_events)

        # Drinks
        drink_pool = random.sample(self.drinks, min(3, len(self.drinks)))
        drinks_html = "".join(
            f"<li><b>{d[0]}</b> ({d[1]}) &mdash; <em>{d[2]}</em></li>"
            for d in drink_pool
        )

        # Specialty dish
        dish = random.choice(self.specialty_dishes)
        dish_html = (
            f"<b>{dish[0]}</b> ({dish[1]}) &mdash; <em>{dish[2]}</em>"
        )

        # Regular food
        foods = random.sample(self.food_items, min(2, len(self.food_items)))
        food_html = ", ".join(foods)

        # Staff NPC
        keeper_name = self._npc_name()
        keeper_ancestry = self._ancestry()
        keeper_trait = random.choice(self.traits)
        keeper_quirk = random.choice(self.quirks)

        # Rumors (2-3)
        num_rumors = random.randint(2, 3)
        rumors = []
        for _ in range(num_rumors):
            rtype = random.choice(["true", "partial", "false"])
            if rtype == "true":
                template = random.choice(self.rumor_templates_true)
                rumor = self._fill_rumor_template(template, level, biome)
                rumors.append(f"{rumor}")
            elif rtype == "partial":
                rumors.append(random.choice(self.rumor_templates_partial).format(
                    location=bf, building=random.choice(["mill", "warehouse", "chapel", "tower", "manor"]),
                    profession=random.choice(self.professions).lower(),
                ))
            else:
                rumors.append(random.choice(self.rumor_templates_false).format(
                    profession=random.choice(self.professions).lower(),
                    location=bf,
                ))
        rumors_html = "".join(f"<li>{r}</li>" for r in rumors)

        atmosphere = random.choice(["bustling", "quiet", "rowdy", "tense", "welcoming", "dimly lit and suspicious"])

        return (
            f"<b>{name}</b><br>"
            f"A {atmosphere} establishment near {bf}.<br>"
            f"<em>{room}</em><br><br>"
            f"<b>Tavern Keeper:</b> {keeper_name}, a {keeper_trait} {keeper_ancestry} who {keeper_quirk}.<br><br>"
            f"<b>Entertainment:</b> {entertainment}<br><br>"
            f"<b>Happening Now:</b> {event}<br><br>"
            f"<b>Drinks:</b><ul>{drinks_html}</ul>"
            f"<b>Specialty Dish:</b> {dish_html}<br>"
            f"<b>Kitchen:</b> {food_html}<br><br>"
            f"<b>Rumor Board:</b><ul>{rumors_html}</ul>"
        )

    def get_shop(self, level=1, biome="City"):
        tier = self._tier(level)
        dc = self._dc(level)
        stype_data = random.choice(self.shop_types)
        stype, cat, desc = stype_data
        sname = random.choice(self.shop_names)

        # Shopkeeper
        keeper_name = self._npc_name()
        keeper_ancestry = self._ancestry()
        keeper_trait = random.choice(self.traits)
        keeper_quirk = random.choice(self.quirks)

        # Stock items with prices
        c = self.consumables_by_tier[tier]
        p = self.permanents_by_tier[tier]
        if cat == "cons":
            cons_items = random.sample(c, min(4, len(c)))
            perm_items = random.sample(p, min(1, len(p)))
        elif cat == "perm":
            cons_items = random.sample(c, min(2, len(c)))
            perm_items = random.sample(p, min(3, len(p)))
        else:
            cons_items = random.sample(c, min(3, len(c)))
            perm_items = random.sample(p, min(2, len(p)))

        items_html = ""
        for item in cons_items:
            items_html += f"<li>{item[0]} &mdash; {item[1]}</li>"
        for item in perm_items:
            rarity_tag = f" <em>[{item[2]}]</em>" if item[2] != "Common" else ""
            items_html += f"<li>{item[0]} &mdash; {item[1]}{rarity_tag}</li>"

        # Negotiation DCs
        haggle_dc = dc
        negotiate = (
            f"<b>Haggle:</b> DC {haggle_dc} Diplomacy for 10% discount. "
            f"DC {self._dc_hard(level)} for 20% discount. "
            f"Critical failure: offended, prices increase 10%."
        )

        # Services
        services = random.sample(self.shop_services, min(3, len(self.shop_services)))
        services_html = "".join(
            f"<li>{s[0]}: {s[1].format(dc=dc)}</li>" for s in services
        )

        # Shop rumor
        rumors = [
            f"The shopkeeper will offer 20% off if the party clears {self._monster(level, biome)} from their supply route.",
            f"The shopkeeper whispers about a rare item they can source &mdash; for {max(level, 1) * 50} gp and 1d4 weeks of waiting.",
            f"There is a forged item in stock. DC {dc} Crafting reveals it as a fake.",
            f"The shopkeeper is buying monster parts: {max(level, 1) * 5} gp per trophy.",
            f"A rival shop is undercutting prices using stolen goods. The shopkeeper wants proof.",
            f"The shopkeeper received a shipment with a mysterious unlabeled crate. They'll sell it unopened for {max(level, 1) * 25} gp.",
        ]

        return (
            f"<b>{sname}</b> ({stype})<br>"
            f"<em>Specializes in {desc}.</em><br><br>"
            f"<b>Shopkeeper:</b> {keeper_name}, a {keeper_trait} {keeper_ancestry} who {keeper_quirk}.<br><br>"
            f"<b>Stock Highlights:</b><ul>{items_html}</ul>"
            f"{negotiate}<br><br>"
            f"<b>Services Available:</b><ul>{services_html}</ul>"
            f"<b>Rumor:</b> {random.choice(rumors)}"
        )

    def get_loot(self, level=1, biome="City"):
        tier = self._tier(level)
        co = self._currency(level)
        dc = self._dc(level)
        dd = max(1, level // 2)

        # Build items
        c1 = self._consumable_str(tier)
        c2 = self._consumable_str(tier)
        p1 = self._permanent_str(tier)
        a1 = random.choice(self.art_objects_by_tier[tier])
        a2 = random.choice(self.art_objects_by_tier[tier])

        # Skill challenges
        challenges = [
            f"<b>Skill Challenge:</b> DC {dc} Perception to notice a hidden compartment with additional treasure ({self._currency(level)}).",
            f"<b>Skill Challenge:</b> DC {dc} Thievery to unlock without triggering a trap ({dd}d6 damage, Reflex DC {dc}).",
            f"<b>Skill Challenge:</b> DC {self._dc_easy(level)} Arcana to identify a magical residue that reveals the loot's origin.",
            f"<b>Skill Challenge:</b> DC {dc} Society to recognize a maker's mark that increases the art object's value by 50%.",
            f"<b>Skill Challenge:</b> DC {self._dc_hard(level)} Survival to track the original owner, who offers a reward for the item's return.",
        ]

        templates = [
            (
                f"<b>Scattered Treasure</b><br>"
                f"<ul><li><b>Coin:</b> {co}</li>"
                f"<li>{c1}</li><li>{c2}</li>"
                f"<li>{a1}</li></ul>"
                f"{random.choice(challenges)}"
            ),
            (
                f"<b>Hidden Cache</b> (DC {dc} Perception to find)<br>"
                f"<ul><li><b>Coin:</b> {co}</li>"
                f"<li>{p1}</li><li>{a1}</li><li>{c1}</li></ul>"
                f"{random.choice(challenges)}"
            ),
            (
                f"<b>Monster Hoard</b><br>"
                f"The lair of {self._monster(level, biome)} contains:<br>"
                f"<ul><li><b>Coin:</b> {co}</li>"
                f"<li>{p1}</li><li>{c1}</li>"
                f"<li>{a1}</li><li>{a2}</li></ul>"
                f"{random.choice(challenges)}"
            ),
            (
                f"<b>Locked Chest</b> (DC {dc} Thievery to open)<br>"
                f"<ul><li><b>Coin:</b> {co}</li>"
                f"<li>{p1}</li><li>{c1}</li><li>{c2}</li></ul>"
                f"<em>Trap: DC {dc} Reflex or {dd}d6 damage. Critical failure: also poisoned (DC {dc} Fort or sickened 2).</em><br>"
                f"{random.choice(challenges)}"
            ),
            (
                f"<b>Treasure Bundle</b> (Level {level} Encounter Reward)<br>"
                f"Based on PF2e wealth guidelines for a party of 4:<br>"
                f"<ul><li><b>Coin:</b> {co}</li>"
                f"<li>{p1}</li><li>{c1}</li><li>{c2}</li>"
                f"<li>{a1}</li></ul>"
                f"<em>Total approximate value: ~{int(self._wealth_by_level.get(min(max(level, 1), 20), 175) * random.uniform(0.08, 0.15))} gp</em>"
            ),
            (
                f"<b>Guarded Vault</b> (DC {self._dc_hard(level)} Thievery, 3 successes to unlock)<br>"
                f"<ul><li><b>Coin:</b> {co} and {self._currency(level)}</li>"
                f"<li>{p1}</li><li>{self._permanent_str(tier)}</li>"
                f"<li>{c1}</li><li>{a1}</li><li>{a2}</li></ul>"
                f"<em>Alarm: Failure on any Thievery check alerts {self._monster(level, biome)} within 1d4 rounds.</em>"
            ),
        ]
        return random.choice(templates)

    def get_magic_item(self, level=1, biome="City"):
        tier = self._tier(level)
        item_data = random.choice(self.permanents_by_tier[tier])
        item_name = item_data[0]
        item_price = item_data[1]
        item_rarity = item_data[2]
        dc = self._dc(level)

        origin = random.choice(self.magic_item_origins).format(biome=biome.lower())
        quirk = random.choice(self.magic_item_quirks)
        activation = random.choice(self.activation_actions)

        # Rarity tag styling
        rarity_colors = {"Common": "#446644", "Uncommon": "#886622", "Rare": "#2244AA", "Unique": "#8822AA"}
        rarity_color = rarity_colors.get(item_rarity, "#446644")

        # Traits
        num_traits = random.randint(2, 4)
        traits = random.sample(self.magic_item_traits, num_traits)
        traits_str = ", ".join(traits)

        # Investment
        invested = "Invested" in traits
        invest_note = "Yes (counts toward your 10-item limit)" if invested else "No"

        # Possible curse (15% chance)
        curse_html = ""
        if random.random() < 0.15:
            curse = random.choice(self.magic_item_curses).format(dc=dc)
            curse_html = f"<br><b>Curse:</b> <em>{curse}</em>"

        return (
            f"<b>{item_name}</b> "
            f"<span style='color:{rarity_color}; font-weight:bold;'>[{item_rarity}]</span><br>"
            f"<b>Price:</b> {item_price} &nbsp;|&nbsp; <b>Traits:</b> {traits_str}<br>"
            f"<b>Investment Required:</b> {invest_note}<br>"
            f"<b>Activation:</b> {activation}<br><br>"
            f"This item {origin}.<br>"
            f"<b>Quirk:</b> {quirk}<br>"
            f"<b>Identify:</b> DC {dc} Arcana or appropriate tradition (Occultism, Religion, Nature).{curse_html}<br>"
            f"<b>Identify (Rarity):</b> {'Standard Identify Magic activity.' if item_rarity == 'Common' else f'Requires a successful DC {self._dc_hard(level)} check or access to specialized resources.'}"
        )

    def get_puzzle(self, level=1, biome="City"):
        dc = self._dc(level)
        dc_easy = self._dc_easy(level)
        dc_hard = self._dc_hard(level)
        dd = max(1, level // 2)
        tier = self._tier(level)
        hz = random.choice(self.biomes.get(biome, self.biomes["City"])["hazards"])

        # Reward
        reward_gp = int(self._wealth_by_level.get(min(max(level, 1), 20), 175) * random.uniform(0.03, 0.08))
        consumable = self._consumable_str(tier)
        permanent = self._permanent_str(tier)
        reward_template = random.choice(self.puzzle_rewards).format(
            reward_gp=reward_gp, consumable=consumable, permanent=permanent, level=level
        )

        puzzle_type = random.choice(["riddle", "logic", "physical", "magical", "trap", "haunt", "environmental"])

        if puzzle_type == "riddle":
            riddle, answer = random.choice(self.riddles)
            return (
                f"<b>Riddle Puzzle</b><br>"
                f"A voice booms through the chamber, or an inscription reads:<br>"
                f"<blockquote><em>\"{riddle}\"</em></blockquote>"
                f"<b>Answer:</b> <span style='color:#888;'>{answer}</span><br>"
                f"<b>Alternate Solutions:</b><ul>"
                f"<li>DC {dc} Arcana or Occultism to magically divine the answer</li>"
                f"<li>DC {dc_hard} Society or appropriate Lore to recall a similar riddle</li>"
                f"<li>DC {dc} Thievery to bypass the mechanism entirely</li></ul>"
                f"<b>Failure Consequence:</b> {dd}d6 damage ({random.choice(['fire', 'cold', 'electricity', 'force'])}), basic Reflex DC {dc}.<br>"
                f"<b>Reward:</b> {reward_template}"
            )

        elif puzzle_type == "logic":
            puzzle = random.choice(self.logic_puzzles).format(dc=dc, dc_hard=dc_hard, dd=dd)
            return (
                f"<b>Logic Puzzle</b><br>{puzzle}<br>"
                f"<b>Alternate Solutions:</b><ul>"
                f"<li>DC {dc_hard} Thievery to bypass the mechanism</li>"
                f"<li>DC {dc} Perception to notice wear patterns that reveal the answer</li></ul>"
                f"<b>Failure Consequence:</b> {dd}d6 damage, basic Reflex DC {dc}. Resets after 1 minute.<br>"
                f"<b>Reward:</b> {reward_template}"
            )

        elif puzzle_type == "physical":
            puzzle = random.choice(self.physical_puzzles).format(dc=dc, dc_hard=dc_hard, dc_easy=dc_easy, dd=dd)
            return (
                f"<b>Physical Puzzle</b><br>{puzzle}<br>"
                f"<b>Reward:</b> {reward_template}"
            )

        elif puzzle_type == "magical":
            puzzle = random.choice(self.magical_puzzles).format(dc=dc, dc_hard=dc_hard, dd=dd, level=level)
            return (
                f"<b>Magical Puzzle</b><br>{puzzle}<br>"
                f"<b>Reward:</b> {reward_template}"
            )

        elif puzzle_type == "trap":
            return (
                f"<b>Mechanical Trap: {hz}</b><br>"
                f"<b>Detect:</b> DC {dc} Perception<br>"
                f"<b>Disable:</b> DC {dc} Thievery (2 successes) or DC {dc_hard} Athletics to force<br>"
                f"<b>Trigger:</b> A creature enters the area.<br>"
                f"<b>Effect:</b> {dd}d6 damage ({random.choice(['piercing', 'bludgeoning', 'slashing'])}), basic Reflex DC {dc}. "
                f"Critical failure: also knocked prone and {dd}d4 persistent bleed.<br>"
                f"<b>Reset:</b> Automatic after 1 minute.<br>"
                f"<b>Skill Challenge Options:</b><ul>"
                f"<li>DC {dc} Crafting to permanently disable</li>"
                f"<li>DC {dc_easy} Perception to find a safe path around</li>"
                f"<li>DC {dc_hard} Arcana if the trap has a magical component</li></ul>"
                f"<b>Reward (after disabling):</b> {reward_template}"
            )

        elif puzzle_type == "haunt":
            return (
                f"<b>Haunt: Echoes of the Past</b><br>"
                f"<b>Detect:</b> DC {dc} Religion or Perception (master proficiency)<br>"
                f"<b>Disable:</b> DC {dc} Religion (2 successes) or DC {dc_hard} Diplomacy (lay the spirit to rest)<br>"
                f"<b>Trigger:</b> A living creature enters the area.<br>"
                f"<b>Effect:</b> {dd}d4 mental damage + Frightened {min(level // 4 + 1, 3)} (basic Will DC {dc}).<br>"
                f"<b>Routine:</b> Each round, the haunt replays a traumatic event. Creatures who witness it must save again.<br>"
                f"<b>Permanent Destruction:</b> Complete the spirit's unfinished business or use a {random.choice(['consecrate', 'rest eternal'])} ritual.<br>"
                f"<b>Reward:</b> {reward_template}"
            )

        else:  # environmental
            return (
                f"<b>Environmental Puzzle</b><br>"
                f"The path is blocked by {hz.lower()}. The environment near {random.choice(self.biomes.get(biome, self.biomes['City'])['flavor'])} "
                f"presents a challenge.<br>"
                f"<b>Solution A:</b> DC {dc} Crafting to engineer a bypass (10 minutes).<br>"
                f"<b>Solution B:</b> DC {dc_easy} Athletics to clear the obstacle (requires 2 characters, 30 minutes).<br>"
                f"<b>Solution C:</b> DC {dc_hard} Survival to find an alternate route (adds 1 hour of travel).<br>"
                f"<b>Solution D:</b> DC {dc} Nature to use the environment creatively (e.g., redirect water, coax plants).<br>"
                f"<b>Failure:</b> {dd}d6 damage, path blocked for 1 hour. Alternative: 2-hour detour through dangerous terrain.<br>"
                f"<b>Reward:</b> {reward_template}"
            )

    def get_quest(self, level=1, biome="City"):
        m = self._monster(level, biome)
        bd = self.biomes.get(biome, self.biomes["City"])
        n = self._npc_name()
        patron_desc = f"{self._ancestry()} {random.choice(self.professions).lower()}"
        gp = int(self._wealth_by_level.get(min(max(level, 1), 20), 175) * random.uniform(0.05, 0.15))
        dc = self._dc(level)
        fl = random.choice(bd["flavor"])
        complication = random.choice(self.quest_complications)
        faction = random.choice(self.quest_factions)
        urgency = random.choice(self.quest_urgency)
        urgency_str = f"<b>Urgency:</b> {urgency[0]} &mdash; {urgency[1].format(days=random.randint(3, 14))}"

        templates = [
            # 1: Bounty Hunt
            (
                f"<b>Bounty: Hunt the {m}</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> Backed by {faction}.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> A <b>{m}</b> is terrorizing {fl}. The creature must be stopped.<br>"
                f"<b>Objective 1:</b> Track the creature (DC {dc} Survival).<br>"
                f"<b>Objective 2:</b> Defeat or capture {m}.<br>"
                f"<b>Twist:</b> The creature is protecting something &mdash; its young, a sacred site, or trapped civilians.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> <b>{gp} gp</b>. Bonus: +50% if captured alive."
            ),
            # 2: Missing Persons
            (
                f"<b>Missing Persons</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> {faction} may have information.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> Someone vanished near {fl}. A strange token links to <b>{m}</b>.<br>"
                f"<b>Objective 1:</b> Investigate the disappearance (DC {dc} Society or Diplomacy &mdash; Gather Information).<br>"
                f"<b>Objective 2:</b> Track the missing person (DC {dc} Survival).<br>"
                f"<b>Objective 3:</b> Rescue or recover them from {m}.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + a favor from {n.split()[0]}."
            ),
            # 3: Retrieval
            (
                f"<b>The Retrieval</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> The item belongs to {faction}.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> Recover a specific item from deep within {fl}. <b>{m}</b> guards the area.<br>"
                f"<b>Objective 1:</b> Locate the item (DC {dc} Perception or Arcana).<br>"
                f"<b>Objective 2:</b> Bypass or defeat the guardian.<br>"
                f"<b>Objective 3:</b> Return the item safely.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + one item from the patron's collection."
            ),
            # 4: Escort Mission
            (
                f"<b>Escort Mission</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> {n.split()[0]} needs safe passage through {fl}. <b>{m}</b> stalks the route.<br>"
                f"<b>Objective 1:</b> Plan the safest route (DC {dc} Survival).<br>"
                f"<b>Objective 2:</b> Protect {n.split()[0]} during travel (1-3 encounter checks).<br>"
                f"<b>Objective 3:</b> Arrive at the destination safely.<br>"
                f"<b>Complication:</b> {n.split()[0]} {random.choice(self.secrets)}.<br>"
                f"<b>Reward:</b> {gp} gp on safe arrival."
            ),
            # 5: Investigation
            (
                f"<b>Investigation: The {random.choice(['Murders', 'Thefts', 'Disappearances', 'Poisonings', 'Arsons'])} of {fl.title()}</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> {faction} wants this resolved quietly.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> A series of crimes has been traced to the area near {fl}. Evidence points to {m} &mdash; but is that the real culprit?<br>"
                f"<b>Objective 1:</b> Gather clues at the crime scenes (DC {dc} Perception, Society, or Medicine).<br>"
                f"<b>Objective 2:</b> Interview witnesses (DC {dc} Diplomacy or Intimidation).<br>"
                f"<b>Objective 3:</b> Confront the true perpetrator.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + reputation with {faction}."
            ),
            # 6: Heist
            (
                f"<b>The Heist</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> {faction} is the target (or the employer).<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> Break into a secure location near {fl} and acquire a specific item.<br>"
                f"<b>Objective 1:</b> Case the location (DC {dc} Stealth and Perception).<br>"
                f"<b>Objective 2:</b> Bypass security ({self._monster(level, biome)} guards, DC {dc} Thievery for locks).<br>"
                f"<b>Objective 3:</b> Escape undetected (DC {dc} Stealth or combat encounter).<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + the item itself may have additional value."
            ),
            # 7: Diplomacy
            (
                f"<b>Diplomatic Mission</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> Representing {faction}.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> Negotiate a treaty, trade deal, or ceasefire near {fl}. The other party is represented by a {self._ancestry()} {random.choice(self.professions).lower()}.<br>"
                f"<b>Objective 1:</b> Attend the negotiation (DC {dc} Diplomacy, 3 successes before 2 failures).<br>"
                f"<b>Objective 2:</b> Uncover the opposing side's true agenda (DC {dc} Sense Motive or Deception).<br>"
                f"<b>Objective 3:</b> Secure favorable terms or prevent violence.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + political influence."
            ),
            # 8: Defense
            (
                f"<b>Defend the {random.choice(['Village', 'Bridge', 'Outpost', 'Caravan', 'Temple', 'Mine'])}</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> {faction} has a stake in the outcome.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> <b>{m}</b> is planning an assault on a location near {fl}. The party must prepare defenses and hold the line.<br>"
                f"<b>Objective 1:</b> Fortify the location (DC {dc} Crafting or Athletics, 3 hours).<br>"
                f"<b>Objective 2:</b> Survive {random.randint(3, 5)} waves of attackers.<br>"
                f"<b>Objective 3:</b> Defeat or repel the leader.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + gratitude of the locals (free lodging and supplies for 1 month)."
            ),
            # 9: Exploration
            (
                f"<b>Expedition into the Unknown</b><br>"
                f"<b>Patron:</b> {n}, a {patron_desc}<br>"
                f"<b>Faction:</b> Funded by {faction}.<br>"
                f"{urgency_str}<br>"
                f"<b>Brief:</b> Explore an uncharted area near {fl}. Rumors speak of {m} and ancient treasure.<br>"
                f"<b>Objective 1:</b> Navigate to the location (DC {dc} Survival, 3 checks over 3 days).<br>"
                f"<b>Objective 2:</b> Map the area and document findings (DC {dc} Cartography Lore or Society).<br>"
                f"<b>Objective 3:</b> Return with proof of what was found.<br>"
                f"<b>Complication:</b> {complication}<br>"
                f"<b>Reward:</b> {gp} gp + naming rights + {self._permanent_str(self._tier(level))}."
            ),
        ]
        return random.choice(templates)

    def get_encounter(self, level=1, biome="City"):
        m = self._monster(level, biome)
        bd = self.biomes.get(biome, self.biomes["City"])
        fl = random.choice(bd["flavor"])
        dc = self._dc(level)
        dd = max(1, level // 2)
        tier = self._tier(level)
        difficulty, xp = self._xp_for_encounter(level)

        # Terrain
        terrain = random.choice(bd.get("terrain_features", ["Open ground (no special terrain)"]))

        # Environmental effect
        env_effects = [
            "None.",
            f"Heavy fog: Concealed beyond 30 ft (DC 5 flat check to target).",
            f"Dim light: Creatures without darkvision are off-guard to creatures with darkvision.",
            f"Difficult terrain: Full speed requires DC {dc} Acrobatics.",
            f"Hazardous terrain: {max(1, level // 3)} damage on entry to hazardous squares.",
            f"Unstable ground: DC {self._dc_easy(level)} Reflex at start of turn or fall prone.",
            f"Environmental countdown: area collapses/floods/ignites in {random.randint(5, 8)} rounds.",
            "Innocent bystanders are caught in the area (2d4 commoners).",
        ]

        setup = random.choice([
            f"The party is ambushed by <b>{m}</b> near {fl}.",
            f"The party stumbles upon <b>{m}</b> mid-activity near {fl}.",
            f"<b>{m}</b> blocks the only path through {fl}.",
            f"A dying NPC warns about <b>{m}</b> ahead &mdash; too late.",
            f"<b>{m}</b> has taken hostages near {fl}.",
            f"The party discovers <b>{m}</b> fighting a third party near {fl}. Both sides turn hostile.",
            f"<b>{m}</b> emerges from hiding as the party examines a point of interest near {fl}.",
        ])

        tactic = random.choice([
            "Focus fire on the most visible caster.",
            "Flank the weakest-looking party member.",
            "Hit-and-run: retreat after each Strike, using terrain.",
            "Grapple and drag targets toward hazardous terrain.",
            "Split the party using terrain and forced movement.",
            "Use ranged attacks from elevated positions.",
            "Demoralize first (Intimidation), then attack the frightened target.",
            "Coordinate: one enemy Aids while others Strike.",
        ])

        morale = random.choice([
            "Fight to the death.",
            "Flee below 25% HP.",
            "Surrender if outmatched (DC {dc} Intimidation speeds this up).",
            "Retreat and regroup with reinforcements in 10 minutes.",
            "Fight until their leader falls, then scatter.",
        ]).format(dc=dc)

        # Treasure for defeating
        treasure = (
            f"<b>Treasure:</b> {self._currency(level)}"
            f", {self._consumable_str(tier)}"
            f", {random.choice(self.art_objects_by_tier[tier])}"
        )

        return (
            f"<b>Combat Encounter</b> &mdash; <em>{difficulty} Threat (XP Budget: {xp})</em><br>"
            f"{setup}<br><br>"
            f"<b>Terrain:</b> {terrain}<br>"
            f"<b>Environment:</b> {random.choice(env_effects)}<br>"
            f"<b>Tactics:</b> {tactic}<br>"
            f"<b>Morale:</b> {morale}<br><br>"
            f"<b>Tactical Suggestion:</b> The enemies will try to use {terrain.split('(')[0].strip().lower()} to their advantage. "
            f"Players who use the terrain creatively should receive a +1 or +2 circumstance bonus.<br><br>"
            f"{treasure}<br>"
            f"<b>XP Reference:</b> Trivial={self._xp_budgets['Trivial']}, Low={self._xp_budgets['Low']}, "
            f"Moderate={self._xp_budgets['Moderate']}, Severe={self._xp_budgets['Severe']}, Extreme={self._xp_budgets['Extreme']}"
        )

    # ── NEW GENERATORS ────────────────────────────────────────────────

    def get_weather(self, level=1, biome="City"):
        """Generate weather and environmental conditions with mechanical effects."""
        conditions = self.weather_conditions.get(biome, self.weather_conditions["City"])
        condition, effect = random.choice(conditions)
        effect = effect.format(dc=self._dc(level))

        # Temperature
        temp_ranges = {
            "City": (40, 95), "Forest": (35, 85), "Dungeon": (45, 60),
            "Desert": (50, 130), "Swamp": (55, 100), "Mountains": (-10, 60),
            "Coastal": (45, 90), "Arctic": (-60, 20), "Underground": (45, 60),
        }
        low, high = temp_ranges.get(biome, (40, 85))
        temp = random.randint(low, high)

        # Visibility
        visibility_options = [
            ("Clear", "Normal visibility (no penalties)."),
            ("Hazy", "Concealed beyond 120 ft."),
            ("Foggy", "Concealed beyond 60 ft. -2 to visual Perception checks."),
            ("Dense Fog/Blizzard", "Concealed beyond 20 ft. -4 to visual Perception checks."),
            ("Whiteout/Zero Visibility", "Concealed beyond 5 ft. Navigation nearly impossible."),
        ]
        # Weight toward clear for most biomes, poor for arctic/swamp
        if biome in ("Arctic", "Swamp", "Underground"):
            vis = random.choice(visibility_options[1:])
        elif biome == "Desert":
            vis = random.choice(visibility_options[:3] + [visibility_options[3]])
        else:
            vis = random.choice(visibility_options[:3])

        # Wind
        wind_options = [
            ("Calm", "No wind effects."),
            ("Light Breeze", "Ranged attacks unaffected."),
            ("Moderate Wind", "Ranged attacks beyond 60 ft take -1 penalty."),
            ("Strong Wind", "Ranged attacks beyond 30 ft take -2 penalty. DC 15 Athletics to maintain flight."),
            ("Gale Force", "Ranged attacks impossible beyond 30 ft. DC 20 Athletics to stand. Flight impossible."),
        ]
        wind = random.choice(wind_options[:4]) if biome not in ("Mountains", "Coastal", "Arctic") else random.choice(wind_options[1:])

        # Terrain difficulty modifier
        terrain_mod = random.choice([
            "Normal terrain.",
            "Muddy/slippery: treat as difficult terrain in unpaved areas.",
            "Flooded: low-lying areas are underwater (difficult terrain + swim checks).",
            "Icy: DC 15 Acrobatics to avoid falling prone when moving at full speed.",
        ])

        return (
            f"<b>Weather Conditions</b> ({biome})<br>"
            f"<b>Conditions:</b> {condition}<br>"
            f"<b>Temperature:</b> {temp}&deg;F ({(temp - 32) * 5 // 9}&deg;C)<br>"
            f"<b>Wind:</b> {wind[0]} &mdash; {wind[1]}<br>"
            f"<b>Visibility:</b> {vis[0]} &mdash; {vis[1]}<br>"
            f"<b>Terrain Modifier:</b> {terrain_mod}<br><br>"
            f"<b>Mechanical Effects:</b> {effect}"
        )

    def get_trap(self, level=1, biome="City"):
        """Generate a detailed PF2e trap/hazard stat block."""
        trap = random.choice(self.trap_types)
        dc = self._dc(level)
        dd = max(1, level // 2)

        # Scale stats by level
        ac = 10 + level + random.randint(0, 4)
        hp = level * random.randint(8, 15)
        hardness = max(0, level * 2 + random.randint(-2, 4))
        stealth_dc = dc + random.choice([-2, 0, 0, 2])
        disable_dc = dc
        save_dc = dc

        # Damage calculation
        num_dice = max(1, dd + random.randint(0, 1))
        damage_str = f"{num_dice}{trap['damage_dice']}"

        if "poison" in trap["damage_type"].lower():
            damage_str += f" {trap['damage_type'].split('+')[0].strip()} + DC {dc} Fort or {max(1, dd-1)}d6 poison (3 rounds)"
        else:
            damage_str += f" {trap['damage_type']}"

        # Traits string
        traits_str = ", ".join(trap["traits"])

        # Complexity
        is_complex = random.random() < 0.25
        complexity = "Complex" if is_complex else "Simple"
        routine = ""
        if is_complex:
            routine = (
                f"<b>Routine:</b> (1 action) The trap "
                f"{random.choice(['attacks the nearest creature', 'fires at a random target in range', 'activates an additional effect in the area'])}. "
                f"Attack bonus: +{level + random.randint(5, 10)} vs AC.<br>"
            )

        return (
            f"<b>{trap['name']}</b> &mdash; <em>Hazard {level}</em><br>"
            f"<b>Type:</b> {trap['type']} ({complexity})<br>"
            f"<b>Traits:</b> {traits_str}<br>"
            f"<em>{trap['description']}</em><br><br>"
            f"<b>Stealth:</b> DC {stealth_dc} (to detect before triggering)<br>"
            f"<b>AC:</b> {ac} &nbsp;|&nbsp; <b>Hardness:</b> {hardness} &nbsp;|&nbsp; <b>HP:</b> {hp}<br>"
            f"<b>Disable:</b> DC {disable_dc} Thievery (trained) to disable. "
            f"{'DC ' + str(self._dc_hard(level)) + ' Arcana to suppress magically. ' if 'Magical' in trap['traits'] or 'Haunt' in trap['traits'] else ''}"
            f"{'DC ' + str(dc) + ' Religion to disrupt. ' if 'Haunt' in trap['traits'] else ''}<br>"
            f"<b>Trigger:</b> {trap['trigger']}<br>"
            f"<b>Effect:</b> {damage_str} (basic {trap['save']} DC {save_dc}). "
            f"Critical failure: double damage{random.choice([' and knocked prone', ' and stunned 1', ' and sickened 2', ' and grabbed (escape DC ' + str(dc) + ')'])}.<br>"
            f"{routine}"
            f"<b>Reset:</b> {trap['reset']}<br>"
            f"<b>Countermeasures:</b> {random.choice([f'A DC {dc} Crafting check can jam the mechanism permanently.', f'Destroying the trap requires overcoming Hardness {hardness} and dealing {hp} damage.', f'A successful DC {self._dc_hard(level)} Perception check reveals a safe path through the area.', f'Casting dispel magic (DC {dc}) on the trigger disables it for 1 hour.' if 'Magical' in trap['traits'] else f'A DC {dc} Athletics check can force the mechanism out of alignment.'])}"
        )

    def get_rumor(self, level=1, biome="City"):
        """Generate tavern/street rumors with truth values and investigation DCs."""
        dc = self._dc(level)
        bd = self.biomes.get(biome, self.biomes["City"])
        fl = random.choice(bd["flavor"])

        num_rumors = random.randint(3, 5)
        rumors = []

        for i in range(num_rumors):
            roll = random.random()
            if roll < 0.4:
                # True rumor
                template = random.choice(self.rumor_templates_true)
                text = self._fill_rumor_template(template, level, biome)
                truth = "TRUE"
                investigate = f"DC {self._dc_easy(level)} Diplomacy (Gather Information) or DC {dc} Society to confirm."
                truth_color = "#228822"
            elif roll < 0.7:
                # Partially true
                text = random.choice(self.rumor_templates_partial).format(
                    location=fl,
                    building=random.choice(["mill", "warehouse", "chapel", "tower", "manor", "lighthouse", "barracks"]),
                    profession=random.choice(self.professions).lower(),
                )
                truth = "PARTIALLY TRUE"
                investigate = f"DC {dc} Diplomacy (Gather Information) to learn the full truth. DC {self._dc_hard(level)} Perception or Society to spot the inconsistency."
                truth_color = "#AA8822"
            else:
                # False
                text = random.choice(self.rumor_templates_false).format(
                    profession=random.choice(self.professions).lower(),
                    location=fl,
                )
                truth = "FALSE"
                investigate = f"DC {dc} Society or DC {self._dc_hard(level)} Diplomacy (Gather Information) to debunk."
                truth_color = "#AA2222"

            rumors.append(
                f"<li><b>Rumor {i + 1}</b> "
                f"<span style='color:{truth_color};'>[{truth}]</span><br>"
                f"{text}<br>"
                f"<em>Investigation: {investigate}</em></li>"
            )

        source = random.choice([
            "a drunk patron at the tavern",
            "a street urchin looking for coin",
            "an overheard conversation between guards",
            "graffiti on a wall in the market district",
            "a nervous merchant who pulls the party aside",
            "a gossipy innkeeper",
            "a notice board posting (anonymous)",
            "a traveling bard's song lyrics",
        ])

        return (
            f"<b>Rumors &amp; Hearsay</b> ({biome})<br>"
            f"<b>Source:</b> {source.capitalize()}<br><br>"
            f"<ul>{''.join(rumors)}</ul>"
            f"<em>GM Note: Truth values are hidden from players. Let them investigate to determine accuracy.</em>"
        )

    def get_settlement(self, level=1, biome="City"):
        """Generate a settlement with name, size, government, notable locations, and problems."""
        name = self._settlement_name()
        dc = self._dc(level)

        # Scale settlement size somewhat with level
        if level <= 3:
            size_options = self.settlement_sizes[:4]
        elif level <= 8:
            size_options = self.settlement_sizes[2:6]
        elif level <= 14:
            size_options = self.settlement_sizes[3:7]
        else:
            size_options = self.settlement_sizes[5:]
        size_name, population, gov_default = random.choice(size_options)

        government = random.choice(self.government_types)

        # Notable locations (3-5)
        num_locations = random.randint(3, 5)
        locations = random.sample(self.settlement_locations, min(num_locations, len(self.settlement_locations)))
        locations_html = "".join(f"<li>{loc.capitalize()}</li>" for loc in locations)

        # Local problems (2-3)
        num_problems = random.randint(2, 3)
        problems = random.sample(self.settlement_problems, min(num_problems, len(self.settlement_problems)))
        problems_html = "".join(f"<li>{p}</li>" for p in problems)

        # Key NPCs (3-4)
        npcs = []
        roles = random.sample([
            "Leader/Mayor", "Guard Captain", "High Priest/Priestess",
            "Merchant Guild Master", "Tavern Owner", "Crime Lord (rumored)",
            "Healer/Alchemist", "Local Sage/Wizard", "Blacksmith",
            "Harbormaster", "Sheriff", "Spy/Informant",
        ], min(4, 12))
        for role in roles:
            npc_name = self._npc_name()
            npc_ancestry = self._ancestry()
            npc_trait = random.choice(self.traits)
            npcs.append(f"<li><b>{role}:</b> {npc_name}, {npc_ancestry} &mdash; <em>{npc_trait}</em></li>")
        npcs_html = "".join(npcs)

        # Economy
        economies = [
            "Farming and livestock", "Mining and metalwork",
            "Trade hub (crossroads location)", "Fishing and maritime trade",
            "Logging and woodcraft", "Magical research and services",
            "Military garrison (strategic location)", "Pilgrimage destination (religious)",
            "Artisan crafts (textiles, pottery, glasswork)",
        ]
        economy = random.choice(economies)

        # Defenses
        defenses = [
            "Wooden palisade with guard towers",
            "Stone walls with iron gates",
            "Natural defenses (river, cliffs, dense forest)",
            "Magical wards maintained by local spellcasters",
            "None (open and vulnerable)",
            "Militia of {pop} trained commoners".format(pop=random.randint(10, 50)),
            "Mercenary company on retainer",
        ]
        defense = random.choice(defenses)

        return (
            f"<b>{name}</b> &mdash; <em>{size_name}</em><br>"
            f"<b>Population:</b> {population}<br>"
            f"<b>Government:</b> {government}<br>"
            f"<b>Economy:</b> {economy}<br>"
            f"<b>Defenses:</b> {defense}<br><br>"
            f"<b>Notable Locations:</b><ul>{locations_html}</ul>"
            f"<b>Key NPCs:</b><ul>{npcs_html}</ul>"
            f"<b>Local Problems:</b><ul>{problems_html}</ul>"
            f"<b>Settlement Modifier:</b> DC {dc} Diplomacy to Gather Information. "
            f"DC {self._dc_easy(level)} to find basic supplies. "
            f"DC {self._dc_hard(level)} to find rare or illegal goods."
        )

    def get_treasure_hoard(self, level=1, biome="City"):
        """Generate a full treasure hoard per PF2e wealth-by-level guidelines."""
        tier = self._tier(level)
        level = min(max(level, 1), 20)
        base_wealth = self._wealth_by_level.get(level, 175)

        # A hoard represents roughly 25-50% of total level wealth
        hoard_value = int(base_wealth * random.uniform(0.25, 0.50))

        # Distribute: ~40% coins, ~20% gems/art, ~25% consumables, ~15% permanent items
        coin_value = int(hoard_value * 0.40)
        gem_art_value = int(hoard_value * 0.20)
        consumable_value = int(hoard_value * 0.25)
        permanent_value = int(hoard_value * 0.15)

        # Coins breakdown
        gp = int(coin_value * 0.70)
        sp = int(coin_value * 0.25 * 10)  # convert to sp
        cp = int(coin_value * 0.05 * 100)  # convert to cp
        coins_html = f"{gp} gp, {sp} sp"
        if cp > 0 and level <= 5:
            coins_html += f", {cp} cp"

        # Gems
        gem_pool = self.gem_types[tier]
        num_gems = random.randint(2, 5)
        gems = []
        for _ in range(num_gems):
            gem_name, gem_val = random.choice(gem_pool)
            multiplier = random.choice([0.5, 1, 1, 1, 1.5, 2])
            actual_val = int(gem_val * multiplier)
            gems.append(f"{gem_name} ({actual_val} gp)")
        gems_html = "".join(f"<li>{g}</li>" for g in gems)

        # Art objects
        art_objects = random.sample(self.art_objects_by_tier[tier], min(2, len(self.art_objects_by_tier[tier])))
        art_html = "".join(f"<li>{a}</li>" for a in art_objects)

        # Consumables
        num_consumables = random.randint(2, 4)
        consumables = [self._consumable_str(tier) for _ in range(num_consumables)]
        cons_html = "".join(f"<li>{c}</li>" for c in consumables)

        # Permanent items (1-2)
        num_permanent = random.randint(1, 2)
        permanents = [self._permanent_str(tier) for _ in range(num_permanent)]
        perm_html = "".join(f"<li>{p}</li>" for p in permanents)

        # Total approximate value
        total_approx = coin_value + gem_art_value + consumable_value + permanent_value

        return (
            f"<b>Treasure Hoard</b> &mdash; <em>Party Level {level}</em><br>"
            f"<b>Total Approximate Value:</b> ~{total_approx} gp "
            f"(Wealth-by-level total for level {level}: {base_wealth} gp)<br><br>"
            f"<b>Coins:</b> {coins_html}<br>"
            f"<b>Gems:</b><ul>{gems_html}</ul>"
            f"<b>Art Objects:</b><ul>{art_html}</ul>"
            f"<b>Consumables:</b><ul>{cons_html}</ul>"
            f"<b>Permanent Items:</b><ul>{perm_html}</ul>"
            f"<em>Note: Adjust quantities up or down to match your campaign's pacing. "
            f"PF2e recommends distributing treasure across multiple encounters per level.</em>"
        )

    def get_random_event(self, level=1, biome="City"):
        """Generate a random event during travel or downtime."""
        dc = self._dc(level)
        bd = self.biomes.get(biome, self.biomes["City"])
        fl = random.choice(bd["flavor"])

        event_type = random.choice(["travel", "downtime", "discovery", "natural", "npc_encounter", "merchant"])

        if event_type == "travel":
            event = random.choice(self.travel_events)
            consequence = random.choice([
                f"If the party investigates, DC {dc} Perception reveals additional details.",
                f"Ignoring this event has a 50% chance of causing problems later (GM's discretion).",
                f"This leads to a potential side quest if pursued.",
                f"DC {dc} Survival to avoid a complication (getting lost, ambush, etc.).",
            ])
            return (
                f"<b>Travel Event</b> ({biome})<br>"
                f"<em>Near {fl}...</em><br><br>"
                f"{event}<br><br>"
                f"<b>Follow-up:</b> {consequence}"
            )

        elif event_type == "downtime":
            event = random.choice(self.downtime_events)
            consequence = random.choice([
                f"This requires immediate attention or the situation worsens.",
                f"The party can choose to ignore this, but it may come back later.",
                f"DC {dc} Society or Diplomacy to learn more about the situation.",
                f"This event may provide an opportunity for Earn Income or other downtime activities.",
            ])
            return (
                f"<b>Downtime Event</b><br>"
                f"{event}<br><br>"
                f"<b>Follow-up:</b> {consequence}"
            )

        elif event_type == "discovery":
            discovery = random.choice(self.discovery_events)
            investigation = random.choice([
                f"DC {dc} Arcana or Nature to understand the site's significance.",
                f"DC {dc} Perception to find hidden entrances or clues.",
                f"DC {self._dc_hard(level)} Society or appropriate Lore to recall historical context.",
                f"DC {dc} Survival to determine how recently the site was disturbed.",
            ])
            treasure = random.choice([
                "Nothing of value, but the information itself is worth sharing.",
                f"A hidden cache: {self._currency(level)} and {self._consumable_str(self._tier(level))}.",
                f"A clue leading to a larger adventure.",
                f"A permanent item partially buried: {self._permanent_str(self._tier(level))}.",
            ])
            return (
                f"<b>Discovery</b> ({biome})<br>"
                f"<em>Near {fl}, the party discovers...</em><br><br>"
                f"{discovery}.<br><br>"
                f"<b>Investigation:</b> {investigation}<br>"
                f"<b>Potential Treasure:</b> {treasure}"
            )

        elif event_type == "natural":
            disasters = [
                (f"Earthquake", f"DC {dc} Reflex or fall prone. Structures take {max(1, level // 3)}d6 damage. Aftershocks for 1d4 hours."),
                (f"Flash Flood", f"DC {dc} Athletics to avoid being swept away. {max(1, level // 2)}d6 bludgeoning damage. Equipment may be lost."),
                (f"Wildfire", f"Fire spreads at 30 ft per round. {max(1, level // 2)}d6 fire damage per round in the area. DC {dc} Survival to find escape route."),
                (f"Landslide", f"DC {dc} Reflex to avoid. {max(1, level // 2)}d8 bludgeoning damage. Buried creatures are restrained (DC {dc} Athletics to escape)."),
                (f"Magical Storm", f"Random magical effects each round (roll 1d6): 1-2 faerie fire on all creatures, 3-4 {max(1, level // 3)}d6 electricity, 5 confusion (Will DC {dc}), 6 silence in 60-ft radius."),
                (f"Sinkhole", f"DC {dc} Reflex or fall {random.randint(2, 6) * 10} ft. {max(1, level // 3)}d6 bludgeoning damage from the fall."),
            ]
            disaster_name, disaster_effect = random.choice(disasters)
            return (
                f"<b>Natural Disaster: {disaster_name}</b> ({biome})<br>"
                f"<em>The ground shakes / sky darkens / air changes near {fl}...</em><br><br>"
                f"<b>Effect:</b> {disaster_effect}<br><br>"
                f"<b>Duration:</b> {random.choice(['Instantaneous', '1d4 rounds', '1d10 minutes', '1d4 hours'])}.<br>"
                f"<b>Aftermath:</b> {random.choice(['Terrain is now difficult terrain in the affected area.', 'A new passage or cave has been revealed.', 'Local wildlife has been disturbed and is aggressive.', 'A previously hidden structure is now exposed.'])}"
            )

        elif event_type == "npc_encounter":
            npc_name = self._npc_name()
            npc_ancestry = self._ancestry()
            npc_profession = random.choice(self.professions).lower()
            npc_trait = random.choice(self.traits)
            npc_need = random.choice(self.motivations)
            disposition = random.choice(["Hostile", "Unfriendly", "Indifferent", "Friendly", "Helpful"])
            return (
                f"<b>Wandering NPC Encounter</b> ({biome})<br>"
                f"Near {fl}, the party encounters...<br><br>"
                f"<b>{npc_name}</b>, a {npc_trait} {npc_ancestry} {npc_profession}.<br>"
                f"<b>Disposition:</b> {disposition} (DC {dc} Diplomacy to improve).<br>"
                f"<b>Need:</b> {npc_need}.<br>"
                f"<b>Secret:</b> This NPC {random.choice(self.secrets)}.<br><br>"
                f"<b>If Helped:</b> {random.choice([f'Offers {max(level, 1) * 5} gp as payment.', 'Shares valuable information about the area.', 'Gives a consumable item: ' + self._consumable_str(self._tier(level)) + '.', 'Becomes a recurring ally.', 'Warns the party about a danger ahead.'])}<br>"
                f"<b>If Ignored/Attacked:</b> {random.choice(['Flees and may report the party to authorities.', 'Fights back with surprising skill.', 'Curses the party (minor inconvenience, GM discretion).', 'Becomes a recurring antagonist.'])}"
            )

        else:  # merchant
            npc_name = self._npc_name()
            npc_ancestry = self._ancestry()
            tier = self._tier(level)
            num_items = random.randint(3, 5)
            items = []
            for _ in range(num_items):
                if random.random() < 0.6:
                    items.append(self._consumable_str(tier))
                else:
                    items.append(self._permanent_str(tier))
            items_html = "".join(f"<li>{item}</li>" for item in items)

            return (
                f"<b>Merchant Caravan Encounter</b> ({biome})<br>"
                f"Near {fl}, the party encounters a traveling merchant caravan.<br><br>"
                f"<b>Merchant:</b> {npc_name}, a {random.choice(self.traits)} {npc_ancestry} trader.<br>"
                f"<b>Guards:</b> {random.randint(2, 6)} armed escorts (level {max(1, level - 2)}).<br><br>"
                f"<b>Wares for Sale:</b><ul>{items_html}</ul>"
                f"<b>Haggle:</b> DC {dc} Diplomacy for 10% discount. DC {self._dc_hard(level)} for 15% discount.<br>"
                f"<b>Special Offer:</b> {random.choice([f'The merchant is looking for an escort to the next town ({max(level, 1) * 15} gp).', 'The merchant has a treasure map for sale (' + str(max(level, 1) * 50) + ' gp, legitimacy unknown).', 'The merchant offers to buy monster parts and trophies at fair prices.', 'The merchant knows a shortcut through the area (saves 1d4 hours of travel).'])}"
            )

    # ── Helper for rumor template filling ─────────────────────────────

    def _fill_rumor_template(self, template, level, biome):
        """Fill in rumor template variables."""
        bd = self.biomes.get(biome, self.biomes["City"])
        replacements = {
            "{item}": random.choice(["alchemical reagents", "weapons", "healing potions", "rare spices", "gold ore", "magical components"]),
            "{monster}": self._monster(level, biome),
            "{location}": random.choice(bd["flavor"]),
            "{building}": random.choice(["mill", "warehouse", "chapel", "tower", "manor", "barracks", "lighthouse"]),
            "{deity}": random.choice(["Norgorber", "Urgathoa", "Zon-Kuthon", "Lamashtu", "Rovagug", "Asmodeus"]),
            "{relative}": random.choice(["spouse", "heir", "advisor", "champion", "sibling"]),
            "{dungeon_type}": random.choice(["burial vault", "dwarven stronghold", "temple", "laboratory", "prison"]),
            "{ancestry}": random.choice(self.ancestries).lower(),
            "{threat}": random.choice(["a warlord's army", "a plague", "a dragon", "religious persecution", "famine"]),
            "{profession}": random.choice(self.professions).lower(),
        }
        result = template
        for key, value in replacements.items():
            result = result.replace(key, value)
        return result
