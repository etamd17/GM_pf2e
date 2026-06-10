# PF2e Engine Audit — vs. the Rulebooks + Foundry Compendium

Audit of the PF2e level-up engine (`class_matrix.py`) against Player Core, the
Core Rulebook, GM Core, and Rage of Elements, plus the Foundry compendium as
structured ground truth. Done 2026-06-09. Priority order (as requested):
progression/proficiency math vs the books → content vs compendium → party classes.

Proficiency encoding: `2`=Trained, `4`=Expert, `6`=Master, `8`=Legendary.

## Method & completeness (read this)
Ran as a multi-agent workflow; it **died before synthesis twice** (host slept
overnight), so findings were harvested from the agent transcripts. The
**class-progression math is complete and verified** for every class whose source
book is available, plus ABP and the ancestry-stat content. The original
all-27-class sweep and the skill-feat / class-feature content chunks were only
partially auto-verified; I re-checked those directly. Net: the high-value math is
done; remaining items are recommendations below.

The single biggest pattern: **per-level proficiency *timing* was systematically
off** (mostly L11+), plus a copy-pasted phantom `L17: {will:8, fortitude:6}`
capstone applied to classes that don't get it. The Pathbuilder ground-truth tests
(L10/L11 endpoints) didn't catch these because the errors are intermediate-level
or above the fixtures; `test_pb_ground_truth` still passes after every fix.

---

## FIXED (verified vs the books; pinned by tests)

### Party classes
| Class | Key corrections |
|---|---|
| **Kineticist** (Gavin) | Was a generic template. Fort Master L9→**L7**, +Fort Legendary **L15**; Perception L7→**L9**; weapons L5→**L11** (removed phantom weapon-Master L13); armor Master L17→**L19**; +class DC Master **L15** / Legendary **L19**; removed phantom Will-Master & Reflex-Legendary. (Rage of Elements) |
| **Druid** (Kyle) | Fort Expert L5→**L3**; **added** Weapon Expertise (L11) + Medium Armor Expertise (L13); removed phantom L17 capstone. (Player Core) |
| **Cleric/Cloistered** (Go'el) | +4th-Doctrine weapon expertise **L11**; removed phantom L17 saves. |
| **Warpriest** | 5th Doctrine L15 = **Fort→Master** (was spell→Master); Final Doctrine L19 spell→**Master** not Legendary; removed phantom L17. |
| **Champion** (Amadeus) | Audited **clean** — no change. |

### Non-party classes (Player Core / GM Core — remaster-authoritative)
| Class | Key corrections |
|---|---|
| **Bard** | Fort Expertise L7→**L9**; weapon expertise L5→**L11**; Perception Master L9→**L11**; armor L11→**L13**; removed nonexistent Reflex-Master. |
| **Ranger** | Reflex Master L9→**L7**; Fort Master L15→**L11**; +Reflex Legendary & Perception Legend **L15**; +class DC Master **L17**; armor Mastery L17→**L19**. |
| **Rogue** | class DC L9→**L11**; Perception Legendary L19→**L13**; +Light Armor Expertise L13; Slippery Mind L15→**L17**; removed phantom Fort-Master & late Perception. |
| **Fighter** | class DC (Fighter Expertise) L9→**L11**. |
| **ABP table** | Perception potency L9/L15 → **L7/L13/L19** (GM Core). |

### Content (vs compendium)
- Ancestry sizes: **centaur → Large, minotaur → Large, poppet → Small** (defaulted to Medium).

**Tests:** `tests/test_party_class_progression_fixes.py` (6) + `tests/test_pf2e_nonparty_progression_fixes.py` (5), rulebook-cited. Snapshots regenerated. **477 pass.**

---

## RECOMMENDATIONS (not fixed — need a decision or a source)

### Need Player Core 2 (remaster) to fix safely
The engine is remaster-aligned, but **alchemist, barbarian, monk** are remastered
in **Player Core 2** (not uploaded). The pre-remaster Core Rulebook flags these,
but applying CRB values to a remaster engine is risky, so they're left for you:
- **Monk** — caps weapons at Legendary (L19) but should be **Master**; missing class DC Master (Graceful Legend, L17); unarmored timing (Graceful Mastery/Legend).
- **Alchemist** — missing class DC Master (L17); weapon-Master timing (L13 vs L15).
- **Barbarian** — missing class DC Master at L19 (Devastator).

### Need the class's source book to audit at all
13 encoded classes can't be math-audited without their books: **magus, summoner**
(Secrets of Magic); **psychic, thaumaturge** (Dark Archive); **gunslinger,
inventor** (Guns & Gears); **animist, exemplar** (War of Immortals); **commander,
guardian** (Battlecry!); **investigator, oracle, swashbuckler** (APG / Player Core 2).

### Content (low priority / source-conflicting)
- **ABP** doesn't model **skill potency** or the **L17 Ability apex** — these are
  player-choice/multi-skill and don't fit the flat per-type model. Left as-is; the
  GM applies them manually if the ABP variant is used.
- **Exotic ancestry speeds** (athamaru 25 vs 20, awakened-animal, etc.) and a few
  sizes (kashrishi) disagree with the compendium — but the **compendium itself has
  errors** (it lists centaur speed 30; PF2e centaurs are 40), so these need the
  Lost Omens ancestry sources to resolve, not a blanket engine→compendium sync.
  *No party-relevant ancestry had any discrepancy.*
- **Class-feature display labels** (`CLASS_LEVEL_FEATURES`, e.g. the cleric L15/L17
  doctrine labels) are not doctrine-aware in a couple of spots — cosmetic; the
  underlying proficiency math is now correct.

### Practical impact for your table (L2→L3)
None of the fixed bugs affected your players' *current* sheets — almost all bite at
**L5+**, and the party-class endpoints already matched Pathbuilder. The value is in
correct level-ups going forward.
