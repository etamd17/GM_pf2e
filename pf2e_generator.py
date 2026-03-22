import random

class RobustPF2eGenerator:
    def __init__(self, db_path=None):
        self.db_path = db_path
        self.ancestries = ["Human","Dwarf","Elf","Halfling","Gnome","Orc","Goblin","Leshy","Tengu","Kobold","Catfolk","Ratfolk","Kitsune","Hobgoblin","Lizardfolk","Automaton","Fetchling","Fleshwarp","Sprite","Shoony","Grippli","Strix","Nagaji","Vanara"]
        self.first_names = ["Aldric","Bren","Caelith","Dorn","Elden","Fael","Grem","Hask","Iona","Jael","Kael","Lyra","Mira","Nyx","Orin","Pella","Quinn","Rhea","Sera","Thane","Uma","Vale","Wren","Xara","Yara","Zev","Alara","Brynn","Corva","Delith","Eryn","Fenn","Gael","Havra","Idris","Juna","Kai","Lira","Mael","Nira","Oskar","Petra","Riven","Sable","Tova","Ulric","Vex","Wyra","Zariel","Ash"]
        self.professions = ["Alchemist","City Guard Captain","Smuggler","Scholar","Bounty Hunter","Priest","Street Urchin","Merchant","Fallen Noble","Blacksmith","Tavern Keeper","Mercenary","Graverobber","Herbalist","Scribe","Courier","Miner","Cartographer","Fence","Hedge Witch","Sailor","Teamster","Performer","Gladiator","Hermit","Spy"]
        self.traits = ["suspicious","boisterous","paranoid","overly formal","distracted","gossipy","gruff","sycophantic","melancholy","hyperactive","arrogant","cowardly","ruthless","charmingly naive","dead-eyed","soft-spoken","fiercely loyal","perpetually nervous","eerily calm","sardonic","warm and grandmotherly","cold and calculating","jovial but watchful","world-weary","desperately optimistic"]
        self.quirks = ["constantly shuffling a deck of Harrow cards","chews on a piece of raw ginger","has a clicking clockwork prosthetic arm","speaks with an exaggerated theatrical accent","keeps a tiny glowing familiar in a jar","never breaks eye contact","is covered in esoteric tattoos","always speaks in the third person","has a pet rat on their shoulder","obsessively cleans their spectacles","taps their foot to an unheard rhythm","smells faintly of copper and blood","wears a heavy cloak despite the heat","is missing their left ring finger","whistles the same three notes repeatedly","collects small bones in a pouch","has one eye that is a noticeably different color","scratches tally marks into surfaces","carries an ornate but empty scabbard","talks to an imaginary companion"]
        self.secrets = ["is desperately trying to pay off a debt to a crime syndicate","is actually a spy for a neighboring nation","witnessed a murder and is being hunted","is a retired adventurer hiding from their past","stole something valuable and is looking for a buyer","has a terminal illness and is searching for a cure","is a lycanthrope struggling to control their condition","is secretly funding a rebellion","knows the location of a hidden dungeon entrance","is being blackmailed by a powerful figure","has been replaced by a doppelganger","carries a cursed item they cannot get rid of"]
        self.motivations = ["Searching for a lost family heirloom","Wants revenge against a specific person","Needs protection from an unknown threat","Collecting rare ingredients for a ritual","Trying to reunite with an estranged family member","Building a case against a corrupt official","Seeking passage to a dangerous location","Looking for adventurers to test a new invention","Wants to clear their name of a false accusation","Needs someone to deliver a package, no questions asked"]

        self.biomes = {
            "City": {"flavor":["smog-choked alleyways","bustling merchant squares","rain-slicked cobblestones","shadowy rooftops","gilded noble districts","the fish market docks","a crumbling tenement block"],"hazards":["Rogue Alchemist's Cart","Collapsing Scaffolding","Sewer Gas Pocket","Glyph of Warding","Runaway clockwork sweeper","Collapsing sewer tunnel","Poisoned well"],"monsters_by_level":{1:["Pickpocket Gang (4)","Giant Rats (3)","Sewer Ooze"],2:["Thieves Guild Thugs (3)","Wererat","Animated Broom Swarm"],4:["Doppelganger","Vampire Spawn","Corrupt Guard Patrol (4)"],6:["Invisible Stalker","Gargoyle (2)","Hellknight Squad (3)"],8:["Vampire","Guild Assassin (2)","Onidoshi"],10:["Rakshasa","Adult Vampire Lord","Doppelganger Ring (3)"],14:["Marilith","Ancient Vampire","Corrupt Archon"],20:["Pit Fiend","Shoggoth"]}},
            "Forest": {"flavor":["ancient moss-draped trees","dense thorny undergrowth","mist-shrouded clearings","canopies blocking the sun","glowing bioluminescent fungi","a circle of standing stones","a stream choked with fallen logs"],"hazards":["Razor-Vine Patch","Toxic Spore Cloud","Hidden Sinkhole","Fairy Ring Enchantment","Angry Treant Roots","Quicksand pit","Spider web snare"],"monsters_by_level":{1:["Wolf Pack (4)","Giant Spider","Twig Blight (3)"],2:["Owlbear","Cockatrice","Satyr"],4:["Green Hag","Arboreal Warden","Ettercap & Spider Swarm"],6:["Hydra","Shambling Mound","Fey Trickster Coven (3)"],8:["Treant","Annis Hag Coven","Basilisk (2)"],10:["Young Green Dragon","Dryad Queen & Guards","Wendigo"],14:["Adult Green Dragon","Mu Spore","Tane Beast"],20:["Ancient Green Dragon","Terotricus"]}},
            "Dungeon": {"flavor":["damp echoing stone corridors","crumbling catacombs","obsidian walls pulsing with dark magic","dusty tombs untouched for centuries","blood-stained sacrificial altars","a vast underground lake","an impossibly deep vertical shaft"],"hazards":["Spear Launcher Trap","Crushing Wall","Acid Pit","Restless Poltergeist Haunt","Poison Dart Gallery","Blood-Siphon Rune","False floor trap","Alarm ward"],"monsters_by_level":{1:["Skeleton Warriors (3)","Giant Rat Swarm","Zombie Shamblers (4)"],2:["Gelatinous Cube","Skeletal Champion (2)","Mimic"],4:["Wraith","Flesh Golem","Minotaur"],6:["Mummy Guardian (2)","Greater Shadow","Clay Golem"],8:["Mohrg","Devourer","Stone Golem"],10:["Lich","Iron Golem","Greater Wraith (2)"],14:["Demilich","Adamantine Golem","Nightwalker"],20:["Ravener","Tarn Linnorm"]}},
            "Desert": {"flavor":["scorching sands","jagged wind-scoured rocks","ancient half-buried ruins","shimmering heat waves","salt-crusted dry lakebeds","a sandstone canyon","an oasis surrounded by bleached bones"],"hazards":["Quicksand","Sudden Sandstorm","Mirage Trap","Cursed Tomb Ward","Flash Flood","Collapsing dune","Heat exhaustion zone"],"monsters_by_level":{1:["Giant Scorpion","Hyena Pack (4)","Dust Mephit"],2:["Mummy","Giant Ant Swarm","Sand Lurker"],4:["Criosphinx","Lamia","Young Purple Worm"],6:["Young Blue Dragon","Div Patrol","Sand Elemental"],8:["Gynosphinx","Mummy Lord","Greater Lamia"],10:["Adult Blue Dragon","Phoenix","Sepid Div"],14:["Ancient Blue Dragon","Elder Sand Elemental","Efreeti Noble"],20:["Jabberwock","Elder Wyrm"]}},
            "Swamp": {"flavor":["fetid stagnant pools","thick buzzing clouds of insects","twisted mangrove roots","glowing will-o'-wisps in the mist","sinking mud flats","a sunken stone ruin","a rotting wooden walkway"],"hazards":["Mire Trap","Methane Gas Explosion","Leech Swarm","Hag Curse Ward","Choking Miasma","Quickmud","Disease spore cloud"],"monsters_by_level":{1:["Crocodile","Bog Strider (3)","Leech Swarm"],2:["Will-o'-Wisp","Giant Frog (3)","Merrow"],4:["Young Black Dragon","Sea Hag","Slithering Tracker"],6:["Hydra","Marsh Giant","Bog Mummy (2)"],8:["Froghemoth","Nuckelavee","Hag Coven"],10:["Adult Black Dragon","Elder Froghemoth","Swamp Lich"],14:["Ancient Black Dragon","Elder Nuckelavee","Swamp Horror"],20:["Swamp Titan","Wyrm Black Dragon"]}},
            "Mountains": {"flavor":["sheer icy cliffs","howling biting winds","snow-choked passes","ancient dwarven ruins carved into rock","thin freezing air","a volcanic vent billowing sulfur","a frozen waterfall"],"hazards":["Sudden Avalanche","Thin Ice over a Chasm","Freezing Winds","Rockslide","Geothermal Vent Eruption","Altitude sickness zone","Unstable bridge"],"monsters_by_level":{1:["Wolf Pack (4)","Kobold Trappers (4)","Mountain Goat Stampede"],2:["Yeti","Harpy","Young Wyvern"],4:["Frost Giant Scout","Young Roc","Stone Giant"],6:["Young White Dragon","Frost Giant Raider (2)","Wyvern"],8:["Cloud Giant","Roc","Frost Worm"],10:["Adult White Dragon","Storm Giant","Frost Giant Jarl"],14:["Ancient White Dragon","Elder Roc","Mountain Titan"],20:["Tarn Linnorm","Storm Giant King"]}}
        }

        self.consumables_by_tier = {
            "low":["Minor Healing Potion","Scroll of Heal (1st)","Tanglefoot Bag (Lesser)","Alchemist's Fire (Lesser)","Antidote (Lesser)","Feather Token (Ladder)","Owlbear Claw Talisman","Smokestick (Lesser)","Silversheen"],
            "mid":["Moderate Healing Potion","Scroll of Fireball (3rd)","Alchemist's Fire (Moderate)","Elixir of Life (Moderate)","Scroll of Haste (3rd)","Bravo's Brew (Moderate)","Cheetah's Elixir","Potion of Invisibility","Scroll of Dispel Magic"],
            "high":["Greater Healing Potion","Scroll of Heal (6th)","Elixir of Life (Greater)","Alchemist's Fire (Greater)","Potion of Flying","Scroll of Chain Lightning","Phoenix Flask","Scroll of True Seeing"]
        }
        self.permanents_by_tier = {
            "low":["+1 Weapon","+1 Armor","Bag of Holding (Type I)","Hat of Disguise","Boots of Elvenkind","Goggles of Night","Handwraps +1","Wayfinder","+1 Striking Weapon"],
            "mid":["+1 Striking Weapon","+1 Resilient Armor","Winged Boots","Ring of Energy Resistance","Cloak of Elvenkind","Bracers of Missile Deflection","+2 Striking Weapon","Wand of Manifold Missiles"],
            "high":["+2 Greater Striking Weapon","+2 Greater Resilient Armor","Ring of Spell Turning","Staff of Power","Belt of Giant Strength","+3 Greater Striking Weapon","Aeon Stone (Lavender)"]
        }
        self.art_objects_by_tier = {
            "low":["a silver chalice (10 gp)","an ivory comb (5 gp)","a set of gold-inlaid bone dice (8 gp)","a velvet pouch of polished agates (12 gp)","a brass compass with celestial markings (15 gp)"],
            "mid":["a gold-inlaid obsidian dagger (50 gp)","a ruby-studded silver brooch (80 gp)","a masterwork silk tapestry (60 gp)","a set of mithral chess pieces (100 gp)","an orichalcum holy symbol (75 gp)"],
            "high":["a diamond-studded platinum crown (500 gp)","a painting by a legendary artist (300 gp)","a chest of raw adamantine ingots (800 gp)","a fist-sized star sapphire (1,000 gp)"]
        }
        self.tavern_names = ["The Rusty Pick","The Leaping Leshy","The Thirsty Tengu","The Clockwork Pint","The Gilded Goblin","The Crimson Chalice","The Broken Anvil","The Wandering Wyrm","The Drunken Dragon","The Silver Serpent","The Smoking Cauldron","The Last Lantern","The Hanged Man","The Copper Kettle","The Pegasus & Crown","The Blind Basilisk"]
        self.drinks = [("Mutagen Stout","1 sp","Thick and bitter"),("Alchemical Absinthe","5 sp","Glows faintly green"),("Fey-Touched Mead","3 sp","Sweet with a floral finish"),("Dragon's Breath Whiskey","1 gp","Burns going down. Literally."),("Grave-Dust Porter","2 sp","Smoky and dark"),("Sunburst Cider","1 sp","Light and crisp"),("Witch's Brew Ale","2 sp","Changes color every few minutes"),("Dwarven Triple-Malt","5 sp","Will knock a human flat")]
        self.tavern_events = ["A high-stakes game of cards has just turned violent.","A terrible bard is singing off-key.","The city guard is conducting a tense search.","A cloaked figure is quietly offering coin for 'discreet muscle'.","The tavern keeper is complaining about a monster in the cellar.","Two adventuring parties are arguing over a bounty.","A drunken wizard is making objects float.","Someone just collapsed face-first into their soup.","A recruitment poster has been defaced with anti-government slogans.","The entire tavern goes silent when the party walks in."]
        self.food_items = ["Roasted Boar with Cave Moss (5 sp)","Spiced Beetle Skewers (2 sp)","Hearty Troll-Bone Stew (4 sp)","Fried Manticore Bites (8 sp)","Slab of Mystery Meat (1 sp)","Garlic and Root Mash (3 sp)","Grilled River Trout with Herbs (4 sp)","Fire-Roasted Mushroom Platter (3 sp)"]

    def _tier(self, level): return "low" if level <= 4 else "mid" if level <= 10 else "high"
    def _dc(self, level):
        dcs = {0:14,1:15,2:16,3:18,4:19,5:20,6:22,7:23,8:24,9:26,10:27,11:28,12:30,13:31,14:32,15:34,16:35,17:36,18:38,19:39,20:40}
        return dcs.get(min(level,20), 14+level)
    def _monster(self, level, biome):
        bd = self.biomes.get(biome, self.biomes['City'])
        lvls = sorted(bd['monsters_by_level'].keys())
        chosen = lvls[0]
        for l in lvls:
            if l <= level: chosen = l
        return random.choice(bd['monsters_by_level'][chosen])
    def _currency(self, level):
        return f"{random.randint(5,15)*max(level,1)} gp, {random.randint(10,50)*max(level,1)} sp"

    def get_npc(self, level=1, biome="City"):
        n = random.choice(self.first_names); a = random.choice(self.ancestries); p = random.choice(self.professions)
        t = random.choice(self.traits); q = random.choice(self.quirks); s = random.choice(self.secrets); m = random.choice(self.motivations)
        bf = random.choice(self.biomes.get(biome, self.biomes['City'])['flavor'])
        templates = [
            f"<b>{n}, {a} {p}</b><br>A {t} individual who {q}.<br><b>Location:</b> Near {bf}<br><b>Secret:</b> {n} {s}.<br><b>Wants:</b> {m}.",
            f"<b>{n} the {t.title()} ({a} {p})</b><br>{n} {q}. Despite their {t} demeanor, they seem to know everyone.<br><b>Plot Hook:</b> {n} {s} and needs the party's help.<br><b>Reward:</b> {level*10} gp or valuable information.",
            f"<b>{n}, {a} {p}</b><br>Found near {bf}, this {t} NPC {q}.<br><b>Motivation:</b> {m}.<br><b>Complication:</b> {n} {s}.<br><b>Disposition:</b> {random.choice(['Hostile','Unfriendly','Indifferent','Friendly','Helpful'])} (DC {self._dc(level)} Diplomacy to shift).",
        ]
        return random.choice(templates)

    def get_tavern(self, level=1, biome="City"):
        d1 = random.choice(self.drinks); d2 = random.choice([d for d in self.drinks if d!=d1])
        return f"<b>{random.choice(self.tavern_names)}</b><br>A {'bustling' if random.random()>0.3 else 'quiet'} establishment near {random.choice(self.biomes.get(biome,self.biomes['City'])['flavor'])}.<br><br><b>Happening Now:</b> {random.choice(self.tavern_events)}<br><br><b>Drinks:</b><ul><li><b>{d1[0]}</b> ({d1[1]}) — <em>{d1[2]}</em></li><li><b>{d2[0]}</b> ({d2[1]}) — <em>{d2[2]}</em></li></ul><b>Kitchen:</b> {random.choice(self.food_items)}, {random.choice(self.food_items)}"

    def get_shop(self, level=1, biome="City"):
        tier = self._tier(level)
        stype, cat = random.choice([("Alchemist's Apothecary","cons"),("Weaponsmith & Forge","perm"),("Arcane Curiosities","perm"),("General Adventuring Gear","mixed"),("Shady Pawn Shop","mixed")])
        sname = random.choice(["Iron & Anvil","The Adventurer's Satchel","Mystic Weaves","Potions & Poisons","The Dusty Shelf","Silvermark Trading","The Gilt Grimoire","Hammer & Tongs"])
        c = self.consumables_by_tier[tier]; p = self.permanents_by_tier[tier]
        items = random.sample(c, min(3,len(c))) + random.sample(p, min(1,len(p))) if cat=="cons" else random.sample(p, min(2,len(p))) + random.sample(c, min(2,len(c))) if cat=="perm" else random.sample(c, min(2,len(c))) + random.sample(p, min(2,len(p)))
        ih = "".join(f"<li>{i}</li>" for i in items)
        rumor = random.choice([f"The shopkeeper will offer 20% off if the party clears {self._monster(level,biome)} from their supply route.",f"The shopkeeper whispers about a rare item they can source — for {level*50} gp.",f"There is a forged item in stock. DC {self._dc(level)} Crafting reveals it.",f"The shopkeeper is buying monster parts: {level*5} gp per trophy."])
        return f"<b>{sname} ({stype})</b><br><b>Stock Highlights:</b><ul>{ih}</ul><b>Rumor:</b> {rumor}"

    def get_loot(self, level=1, biome="City"):
        tier = self._tier(level); co = self._currency(level); c = self.consumables_by_tier[tier]; p = self.permanents_by_tier[tier]; a = self.art_objects_by_tier[tier]; dc = self._dc(level)
        dd = max(1, level//2)
        templates = [
            f"<b>Scattered Treasure</b><br><ul><li><b>Coin:</b> {co}</li><li>{random.choice(c)}</li><li>{random.choice(c)}</li><li>{random.choice(a)}</li></ul>",
            f"<b>Hidden Cache</b> (DC {dc} Perception)<br><ul><li><b>Coin:</b> {co}</li><li>{random.choice(p)}</li><li>{random.choice(a)}</li></ul>",
            f"<b>Monster Hoard</b><br><ul><li><b>Coin:</b> {co}</li><li>{random.choice(p)}</li><li>{random.choice(c)}</li><li>{random.choice(a)}</li><li>{random.choice(a)}</li></ul>",
            f"<b>Locked Chest</b> (DC {dc} Thievery)<br><ul><li><b>Coin:</b> {co}</li><li>{random.choice(p)}</li><li>{random.choice(c)}</li><li>{random.choice(c)}</li></ul><em>Trap: DC {dc} Reflex or {dd}d6 damage.</em>"
        ]
        return random.choice(templates)

    def get_magic_item(self, level=1, biome="City"):
        tier = self._tier(level); item = random.choice(self.permanents_by_tier[tier])
        origin = random.choice([f"bears the hallmarks of ancient {biome.lower()} craftsmanship","has a faint inscription in a dead language","radiates an aura of abjuration magic","was reportedly looted from a dragon's hoard","is warm to the touch even in freezing conditions","hums faintly when danger is near","is decorated with the symbol of a forgotten deity","appears mundane until attuned"])
        quirk = random.choice(["It glows softly in the dark.","It whispers warnings in Sylvan when fiends are nearby.","It feels heavier than it should.","Its previous owner's initials are scratched into the surface.","It occasionally produces the scent of wildflowers.","When drawn, nearby flames flicker and dim."])
        return f"<b>{item}</b><br>This item {origin}.<br><b>Quirk:</b> {quirk}<br><b>Identify:</b> DC {self._dc(level)} Arcana or appropriate tradition."

    def get_puzzle(self, level=1, biome="City"):
        dc = self._dc(level); hz = random.choice(self.biomes.get(biome,self.biomes['City'])['hazards']); dd = max(1,level//2)
        templates = [
            f"<b>Mechanical Trap: {hz}</b><br><b>Detect:</b> DC {dc} Perception<br><b>Disable:</b> DC {dc} Thievery (2 successes) or DC {dc+2} Athletics<br><b>Trigger:</b> A creature enters the area<br><b>Effect:</b> {dd}d6 damage (basic Reflex DC {dc}). Crit fail: also knocked prone.",
            f"<b>Magical Ward: {hz}</b><br><b>Detect:</b> DC {dc} Arcana or Occultism<br><b>Disable:</b> DC {dc} Arcana or DC {dc+2} Thievery<br><b>Trigger:</b> Touching the warded object<br><b>Effect:</b> {dd}d8 energy damage (basic Fort DC {dc}). Resets in 1 hour.",
            f"<b>Haunt: Echoes of the Past</b><br><b>Detect:</b> DC {dc} Religion or Perception (master)<br><b>Disable:</b> DC {dc} Religion or DC {dc+2} Diplomacy<br><b>Trigger:</b> A living creature enters<br><b>Effect:</b> {dd}d4 mental damage + Frightened {min(level//4+1,3)} (basic Will DC {dc}).",
            f"<b>Environmental Puzzle</b><br>Blocked by {hz.lower()}.<br><b>Solution A:</b> DC {dc} Crafting to bypass<br><b>Solution B:</b> DC {dc-2} Athletics to clear (10 min)<br><b>Solution C:</b> DC {dc+2} Survival for alternate route<br><b>Failure:</b> {dd}d6 damage, path blocked for 1 hour."
        ]
        return random.choice(templates)

    def get_quest(self, level=1, biome="City"):
        m = self._monster(level,biome); bd = self.biomes.get(biome,self.biomes['City']); n = random.choice(self.first_names); pat = f"{random.choice(self.ancestries)} {random.choice(self.professions).lower()}"; gp = level*random.randint(15,30)
        templates = [
            f"<b>Bounty: Hunt the {m}</b><br><b>Patron:</b> {n}, a {pat}<br><b>Brief:</b> A <b>{m}</b> is terrorizing {random.choice(bd['flavor'])}. Reward: <b>{gp} gp</b>.<br><b>Twist:</b> The creature is protecting something — its young, a sacred site, or trapped civilians.<br><b>Bonus:</b> +50% if captured alive.",
            f"<b>Missing Persons</b><br><b>Patron:</b> {n}, a {pat}<br><b>Brief:</b> Someone vanished near {random.choice(bd['flavor'])}. A strange token links to <b>{m}</b>.<br><b>Investigate:</b> DC {self._dc(level)} Society (token) / DC {self._dc(level)} Survival (tracking).<br><b>Reward:</b> {gp} gp + a favor from {n}.",
            f"<b>The Retrieval</b><br><b>Patron:</b> {n}, a {pat}<br><b>Brief:</b> Recover an item from deep within {random.choice(bd['flavor'])}. <b>{m}</b> guards the area.<br><b>Complication:</b> A rival party got there first.<br><b>Reward:</b> {gp} gp + one item from the patron's collection.",
            f"<b>Escort Mission</b><br><b>Patron:</b> {n}, a {pat}<br><b>Brief:</b> {n} needs safe passage through {random.choice(bd['flavor'])}. <b>{m}</b> stalks the route.<br><b>Complication:</b> {n} {random.choice(self.secrets)}.<br><b>Reward:</b> {gp} gp on safe arrival."
        ]
        return random.choice(templates)

    def get_encounter(self, level=1, biome="City"):
        m = self._monster(level,biome); bd = self.biomes.get(biome,self.biomes['City']); fl = random.choice(bd['flavor'])
        setup = random.choice([f"The party is ambushed by <b>{m}</b> near {fl}.",f"The party stumbles upon <b>{m}</b> mid-activity near {fl}.",f"<b>{m}</b> blocks the only path through {fl}.",f"A dying NPC warns about <b>{m}</b> ahead — too late.",f"<b>{m}</b> has taken hostages near {fl}."])
        comp = random.choice([f"Heavy fog: Concealed (DC 5 flat check).","Innocent bystanders caught in the crossfire.",f"Treacherous terrain: full speed requires DC {self._dc(level)} Acrobatics.","A third faction arrives round 2, hostile to everyone.","Dim light: creatures without Darkvision are off-guard.",f"Hazardous terrain: difficult + {max(1,level//3)} damage on entry.","Enemies are coordinated — flanking and Aid actions.",f"Environmental countdown: area collapses in {random.randint(5,8)} rounds."])
        tactic = random.choice(["Focus fire on the most visible caster.","Flank the weakest-looking party member.","Hit-and-run — retreat after each Strike.","Fight to the death.","Flee below 25% HP, return with reinforcements.","Split the party using terrain and forced movement."])
        morale = random.choice(["Fight to the death.","Flee below 25% HP.","Surrender if outmatched.","Retreat and regroup with allies."])
        return f"<b>Combat Encounter</b><br>{setup}<br><br><b>Complication:</b> {comp}<br><b>Tactics:</b> {tactic}<br><b>Morale:</b> {morale}"