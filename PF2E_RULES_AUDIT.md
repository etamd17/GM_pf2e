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

### Player Core 2 classes (audited 2026-06-10, once PC2 was uploaded)
| Class | Key corrections |
|---|---|
| **Alchemist** | Fully reworked to the remaster: weapon expertise L5→**L7**; Fort→Master via Chemical Hardiness **L11** (no Legendary); Medium Armor Expertise L11→**L13**; weapon Master L13→**L15** + Explosion Dodger (Reflex Master); +class DC Master **L17**; armor Master L17→**L19**; removed phantom Will-Master & Perception-Master (both cap Expert). |
| **Barbarian** | Juggernaut Fort Master L9→**L7**; +Greater Juggernaut Fort Legendary **L13**; class DC Expert L9→**L11** + Master (Devastator) **L19**; Reflex Expertise **L9**; Perception Master **L17**; removed phantom Reflex/Will/Fort bumps. |
| **Monk** | **L1 unarmed: Expert→Trained** (base fix; Expert via Expert Strikes L5); weapons cap **Master** L13 (removed phantom Legendary L19); Graceful Mastery unarmored Master **L13**; Graceful Legend unarmored Legendary + class DC Master **L17**. (Path-to-Perfection saves are player-choice — left unmodeled.) |
| **Investigator** | weapon expertise spurious L3 removed; Vigilant Senses L7; Dogged Will L11; Incredible Senses (Perception Legendary) **L13**; Savvy Reflexes L15; Greater Dogged Will L17; +Master Detective class DC **L19**; removed phantom Fort-Master/Reflex-Legendary. |
| **Oracle** | Mysterious Resolve Will Master L9→**L7**; Magical Fortitude L3→**L9**; Oracular Senses + weapon expertise L11; Premonition's Reflexes + Light Armor Expertise **L13**; was built from a generic caster template. |
| **Swashbuckler** | Fortitude Expertise L3 (Will was wrong); Confident Evasion Reflex Master **L7**; Perception Mastery **L11**; Assured Evasion Reflex Legendary + armor **L13**; Reinforced Ego Will Master **L17**; +Eternal Confidence class DC Master **L19**; removed phantom Fort-Master & Perception-Legendary. |

### Content (vs compendium)
- Ancestry sizes: **centaur → Large, minotaur → Large, poppet → Small** (defaulted to Medium).

**Tests:** `test_party_class_progression_fixes.py` (6) + `test_pf2e_nonparty_progression_fixes.py` (5) + `test_pf2e_pc2_progression_fixes.py` (6), rulebook-cited. Snapshots regenerated. **477 pass.**

---

## RECOMMENDATIONS (not fixed — need a decision or a source)

### Need the class's source book to audit at all
**10 encoded classes** still can't be math-audited without their books (5 books):
**magus, summoner** (Secrets of Magic); **psychic, thaumaturge** (Dark Archive);
**gunslinger, inventor** (Guns & Gears); **animist, exemplar** (War of Immortals);
**commander, guardian** (Battlecry!). War of Immortals + Battlecry! are clean
remaster; the other three are the only (legacy) source for those classes, so
usable with a remaster-errata cross-check. Upload any of these and I'll run the
same per-class audit + fix.

*Resolved:* the 6 Player Core 2 classes (alchemist, barbarian, monk, investigator,
oracle, swashbuckler) are now FIXED — see the table above.

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
