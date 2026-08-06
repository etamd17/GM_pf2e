# Cosmere Rules Audit — vs. Stormlight RPG Core Rulebook

Audit of the Cosmere player builder / level-up engine and content against
`Stormlight_Rules.txt` (the Stormlight RPG core rulebook). Report-only; no code
changed. Citations are `Stormlight_Rules.txt:<line>`.

Status legend: [PASS] verified correct · [FINDING] likely real · [NOTE] by-design

---

## Part 1 — Creation & advancement engine (`systems/cosmere/build.py`, `actor.py`)

Cross-checked against the **Character Advancement table** (`:1966`–`:2016`) and
the creation steps (`:1846`–`:2094`). The engine is faithful.

| Rule | Rulebook | Code | Status |
|---|---|---|---|
| Attribute points | 12 at L1; +1 at L3/6/9/12/15/18 (`:1969`,`:2034`) | `CREATION_ATTR_POINTS=12`, `ATTR_INCREASE_LEVELS=(3,6,9,12,15,18)` | [PASS] |
| Creation cap / hard cap | max 3 at creation, never above 5 (`:2037`) | `CREATION_ATTR_MAX=3`, `ATTR_HARD_CAP=5` | [PASS] |
| Health | 10+STR; +5/+4/+3/+2/+1 per tier; STR re-added at L6/11/16 (`:1969`–`:1990`) | `cosmere_max_health` (`actor.py:44`) | [PASS] (L20/STR2 → 83) |
| Skill ranks | 4 (+1 path) then +2/level; L21+ = 1 skill **or** talent (`:1994`,`:2048`) | `free_skill_ranks`, `total_skill_ranks` | [PASS] |
| Max skill rank | tier 2/3/4/5/5 (`:1968`–`:1990`) | `MAX_SKILL_RANK_BY_TIER` | [PASS] |
| Talents | 1 + 1/level; ancestry bonus at L1/6/11/16/21 (`:2067`–`:2075`) | `base_talents`, `ancestry_bonus_talents` | [PASS] (but see [FINDING] Singer) |
| Expertises | 2 (culture) + Intellect (`:1599`) | `expertises_total = 2 + INT` | [PASS] |
| Focus | 2 + Willpower (`:1860`) | `focus_max` | [PASS] |
| Investiture | 2 + max(AWA, PRE), Radiant only (`:3977`) | `investiture_max` | [PASS] |
| Defenses | Phy 10+STR+SPD, Cog 10+INT+WIL, Spi 10+AWA+PRE (`:1879`–`:1893`) | `defenses` | [PASS] |
| Tiers | T1–5 = L1-5/6-10/11-15/16-20/21+ (`:1932`–`:1957`) | `tier_of` | [PASS] |
| Radiant min level | First Ideal talent requires L2+ (`:8889`) | `RADIANT_MIN_LEVEL=2` | [PASS] |
| Radiant two-step | talent → Investiture + 3 actions; **speaking** Ideal → surges (`:8900`–`:8921`) | `is_radiant` vs `surges_unlocked()` | [PASS] |

## Part 2 — Core lookup tables

- [PASS] **All 28 skill → attribute mappings** (`SKILL_ATTR`) match the skill list
  (`:4432`–`:4832`) and the surge titles (`:14989`–`:15008`). 18 basic + 10 surge,
  including Heavy Weaponry = Strength (`:4557`).
- [PASS] **All 9 playable Radiant orders** (`radiant.py RADIANT_ORDERS`) — spren + both
  surges — match the Radiant Orders table (`:9509`–`:9517`). Bondsmiths correctly
  flagged non-playable.
- [PASS] **All 6 heroic paths** (`origins.py PATH_INFO`) — key talent + starting skill —
  match (Agent/Opportunist/Insight `:5547`; Envoy/Rousing Presence/Discipline
  `:6082`; Hunter/Seek Quarry/Perception `:6584`; Leader/Decisive Command/Leadership;
  Scholar/Erudition/Lore; Warrior/Vigilant Stance/Athletics).
- [PASS] **Singer forms' stat changes** (`origins.py SINGER_FORMS`) spot-checked against
  `:2349`–`:2404`: nimbleform Spd+1/Foc+2, direform Str+2/Defl+2, stormform
  Str+1/Spd+1/Defl+1, warform Str+1/Defl+1, etc. — all matched.
- [PASS] **Deflect logic** (`items.py`): armor doesn't stack → take highest (`:2472`);
  impact/keen/energy deflectable, spirit/vital/heal bypass (`:3708`).

## Part 3 — Findings

### [FINDING] F1 — Singer L1 talent budget is short by 1
At creation a Singer gains **Change Form *plus* one starting-forms talent** as
ancestry talents (`:1620`–`:1622`), i.e. **2** ancestry talents at L1 — vs Human's
1. But `ancestry_bonus_talents(level)` returns a flat 1 for every ancestry at L1
(`build.py:70`), so a Singer's `talents_available()` at L1 is 2 (1 path key + 1
ancestry) when the rules entitle them to 3 (path key + Change Form + forms talent).
`validate()`/`hard_violations()` then *block a player* from taking the entitled
forms talent as "over budget." The engine's own `ANCESTRY_INFO['Singer']` text
("Change Form … plus one connected form talent") contradicts the budget.
*Scope: only Singer PCs, only at L1, magnitude 1 talent.*

### [NOTE] F2 — Mateform not in the form catalog
Change Form starts a Singer with **dullform and mateform** (`:2334`). `SINGER_FORMS`
includes dullform but not mateform. Mateform is the non-combat reproductive form
and grants no play benefit, so this is plausibly an intentional omission — flagged
for completeness, not as a bug.

## Part 4 — Not yet audited (the exhaustive content layer)

The per-item *values* are ingested from the Foundry `cosmere-rpg` data and have
**not** been verified line-by-line against the rulebook. This is the bulk of the
"exhaustive, item-by-item" request:

| Pack | Records | What to verify vs rulebook |
|---|---|---|
| `heroic-paths.json` | 157 | talent names, prerequisites, tier placement, effects (Ch.4) |
| `handbook-radiant-paths.json` | 218 | surge/order talent trees, Ideal gates (Ch.5) |
| `handbook-surges.json` | 112 | surge powers, scaling by rank (Ch.6) |
| `items.json` + `handbook-items.json` | 124 + 196 | weapon damage/traits/range, armor deflect, prices (Ch.7) |
| `actions.json` | 21 | action costs & effects (Ch.10) |
| `cultures.json` / `ancestries.json` | 6 / 1+ | expertises, ancestry features |

Plus builder-wiring checks: that the leveler exposes & prereq-gates every talent
tree, applies Singer forms / homebrew correctly, and that `radiant_talents.py`'s
curated `SURGE_TALENTS`/`ORDER_TALENTS` match the full Ch.6 trees.

> **This layer has now been audited — see Part 5.**

---

## Part 5 — Content audit results

**Method.** A multi-agent fan-out diffed every content pack against the rulebook:
19 chunks (6 heroic paths, weapons, armor, base+handbook equipment/loot, actions,
surge powers, 2 surge-talent sets, 2 order-talent sets, cultures, ancestries/forms,
starting kits), each finder agent compared its slice to the relevant rulebook
chapter, then **every flagged discrepancy got an independent adversarial
fact-check**. 62 agents total. **42 flagged → 28 confirmed, 14 false-positive, 0
uncertain.** The 14 false-positives (OCR noise, flavor wording, legitimate
handbook-expansion content) are themselves evidence the data is largely faithful.
I directly re-verified the highest-severity claim (the Strategize/Turning Point
prerequisite swap) against both the rulebook and the pack JSON — confirmed.

**Headline.** Content is **largely faithful**; no corrupted combat/derived stats
were found. 24 of 28 issues are **heroic-path talent prerequisites**, concentrated
in the ingested **`handbook-heroic-paths.json`**. Impact is **gating/guidance
fidelity**, not wrong numbers:

> In this engine, heroic-path talent prerequisites are **advisory** — `talents.py`
> surfaces unmet prereqs as *warnings* in `validate()`, not as save-blocking
> `hard_violations()`. So a **null** prereq → the builder gives *no* warning when a
> player picks a talent they haven't earned; a **swapped/wrong** prereq → the
> builder shows a *wrong* warning. Weapons, armor, actions, surge powers, cultures
> and forms are otherwise clean. (The Radiant picker consumes its own
> `{ideal|talent|text}` prereqs client-side.)

### Themes & root causes

| # | Theme | Root cause | Findings |
|---|---|---|---|
| T1 | **Missing per-item prereqs** (null where the rulebook gates) | Upstream Foundry stored these prereqs on talent-*tree* graph nodes, not on the talent items; the ingest/picker only read item-level prereqs | F7-F10, F14-F21, F25-F27 (16 talents) |
| T2 | **Crossed / wrong prereqs** (actively misleading) | Bad references in the pack | F1-F4, F22 (Strategize↔Turning Point swap; Composed→phantom "Predict"; Wound Regeneration; Unleashed Entropy) |
| T3 | **Stale display labels** | Pack label/id out of date; UUID still resolves right | F11-F12 ("Valiant Stand" → Resolute Stand) |
| T4 | **Misfiled path** | `system.path` corrupted | F13 (Customary Garb Officer: `path:"champion"` → `leader`) |
| T5 | **Action text drops an option** | Ingest truncation | F5 (Reactive Strike: "or unarmed attack" missing) |
| T6 | **Missing content** | Dropped on ingest | F23 Mateform, F24 Kharbranthian (+ Iriali/Listener) culture, F28 Unarmed Attack weapon |

### Confirmed — High severity (actively wrong, not merely absent)

| Item | Path/Spec | Pack says | Rulebook says | Cite |
|---|---|---|---|---|
| **Strategize** | Scholar/Strategist | Deduction **3** + Contingency | Deduction **1+** + Erudition | SL:8107 |
| **Turning Point** | Scholar/Strategist | Deduction **1** + Erudition | Deduction **3+** + Contingency | SL:8121 |
| **Composed** | Scholar/Strategist | requires **Predict** (no such talent) | requires **Strategize** | SL:8052 |
| **Wound Regeneration** | Lightweaver order | `{ideal:1}` (no-prereq entry) | requires **Invested** talent | SL:11673 |

*Strategize/Turning Point are literally swapped (self-verified). The bond-talent
`{ideal:1}` fallback in `radiant_talents._build_order_talents` likely affects
Invested/Deepened Bond/Wound Regeneration across orders — worth a sweep.*

### Confirmed — Medium severity

| Item | Path/Spec | Field | Should be | Cite |
|---|---|---|---|---|
| Practical Demonstration | Envoy/Mentor | skill prereq has no `rank` (→0) | Leadership **1+** | SL:6426 |
| Feral Connection | Hunter/Tracker | prereqs null | Survival 2+ & Protective Bond | SL:7008 |
| Deadly Trap | Hunter/Tracker | prereqs null | Survival 1+ & Seek Quarry | SL:6953 |
| Hunter's Edge | Hunter/Tracker | prereqs null | Survival 3+ & Experienced Trapper | SL:7023 |
| Pack Hunting | Hunter/Tracker | prereqs null | Perception 3+ & Protective Bond | SL:7035 |
| Fine Handiwork | Scholar/Artifabrian | prereqs null | Efficient Engineer | SL:7867 |
| Overcharge | Scholar/Artifabrian | prereqs null | Crafting 3+ & Prized Acquisition | SL:7885 |
| Overwhelm with Details | Scholar/Artifabrian | prereqs null | Lore 3+ & Experimental Tinkering | SL:7908 |
| Bloodstance | Warrior/Shardbearer | prereqs null | Athletics 2+ & (Mighty *or* Shard Training) | SL:8618 |
| Meteoric Leap | Warrior/Shardbearer | prereqs null | Athletics 3+ & Bloodstance | SL:8634 |
| Windstance | Warrior/Shardbearer | prereqs null | Perception 1+ & Shard Training | SL:8743 |
| Shattering Blow | Warrior/Shardbearer | prereqs null | Perception 2+ & Windstance | SL:8706 |
| Precise Parry | Warrior/Shardbearer | prereqs null | **Perception (prc) 3+** & Shattering Blow | SL:8659 |
| Resilient Hero | Leader/Champion | label "Valiant Stand" | **Resolute Stand** (Athletics 3+) | SL:7378 |
| Demonstrative Command | Leader/Champion | label "Valiant Stand" | **Resolute Stand** (Leadership 2+) | SL:7338 |
| Customary Garb (Officer) | Leader/Officer | `path:"champion"` | `path:"leader"` | SL:7450 |
| Unleashed Entropy | Division surge | Gout of Flame *or* **Inescapable Spark** | Gout of Flame *or* **Spark Sending** | SL:15814 |
| Reactive Strike | action | "melee weapon attack" only | "...weapon attack **or unarmed attack**" | SL:21844 |

> **Correction to the auto-synthesis:** for *Precise Parry* the verifier wrote
> "Awareness 3+ (awa)". That is wrong — the rulebook prereq "Perception 3+" is the
> **Perception skill (`prc`) at rank 3** (matching its tree siblings Windstance/
> Shattering Blow), not the Awareness attribute. The other 27 findings reproduced
> cleanly.

### Confirmed — Low severity

| Item | Category | Issue | Cite |
|---|---|---|---|
| Stonestance | Warrior/Shardbearer | prereqs null → Vigilant Stance key talent (already required to reach the specialty) | SL:8730 |
| Shard Training | Warrior/Shardbearer | prereqs null → Vigilant Stance key (+ narrative Shard access, not machine-checkable) | SL:8682 |
| Inventive Design | Scholar/Artifabrian | prereqs null → Crafting 2+ & Prized Acquisition | SL:7877 |
| Mateform | Singer form | absent from `SINGER_FORMS`; Change Form grants dullform **AND** mateform (no stat changes → nil combat impact). Source data has it (`handbook-ancestries.json`). | SL:2333 |
| Kharbranthian | culture | absent (`cultures.json` ships 6); a full core culture w/ its expertise. Iriali & Listener also appear omitted. | SL:2934 |
| Unarmed Attack | weapon | absent; Special Weapons table row (Athletics, Str-scaled Unique dmg, Momentum/Weightless). Improvised Weapon (its table-sibling) *is* modeled. | SL:17654 |

### Recommended remediation order (if/when you want fixes)

1. **T2 (High) — correct the 5 crossed/wrong prereqs.** Small, surgical, and the
   only ones that *actively mislead* a player. Includes a sweep of the order
   bond-talent `{ideal:1}` fallback.
2. **T1 (systematic) — backfill the ~16 null prereqs.** Two routes: (a) write the
   rulebook prereqs onto the affected talent items, or (b) teach the picker/checker
   to read prereqs from the `talent_tree` node graph the upstream data already
   carries. (b) is more robust and self-maintaining.
3. **T3-T5** — relabel "Valiant Stand"→Resolute Stand, fix Customary Garb's path,
   restore Reactive Strike's unarmed clause. Trivial data edits.
4. **T6 + Part 3 F1** — add Mateform & the missing cultures & Unarmed Attack; fix
   the Singer L1 talent-budget undercount. Optional / low urgency.

Each fix should land with a regression test (the test architecture already
supports synthetic Cosmere builds).

---

## Part 6 — Fixes applied (2026-06-07)

All confirmed findings addressed; guarded by `tests/test_cosmere_rulebook_fixes.py`
(16 tests). Full suite: **442 passed**. Implementation re-verified every finding
against the rulebook before changing code, which corrected several audit calls.

### How each theme was fixed

- **T1/T2 heroic prerequisites — a talent-tree resolver** (`systems/cosmere/talents.py`).
  The checker now loads **both** packs (it previously loaded only the base pack,
  so handbook-only specialties were never gated at all) and resolves each
  talent's prereqs from the **talent-tree node graph** (authoritative), backfilling
  any skill/attribute floor a node omits from the item. This single change fixed:
  the Strategize/Turning Point swap, Composed→Strategize, all ~16 null prereqs,
  the Practical Demonstration rank, and the Resolute Stand labels — and is
  self-maintaining across re-ingest. The picker (`app._cosmere_path_talents`)
  now shows the resolved prereqs. *Bonus fixes the resolver caught:* Watchful Eye
  (Deduction 0→1), a "Lessions in Patience"→"Lessons in Patience" prereq typo,
  and Know Your Moment's Deduction 2 is **preserved** (the tree node had dropped it).
- **Customary Garb path** — `champion` (a Leader specialty, never a path) is
  remapped to `leader` via `talents.PATH_FIX`, so the talent now appears under
  Leader. (Code-layer, clobber-proof.)
- **Unleashed Entropy** (`radiant_talents.py`) — explicit override to the rulebook
  prereq (Spark Sending or Gout of Flame); the handbook's own doc *and* tree node
  both shipped "Inescapable Spark", so resolution alone couldn't fix it.
- **Reactive Strike** (`actions.json`) — restored "or unarmed attack" to the
  `value` and `chat` text.
- **Mateform** (`origins.py`) — added to `SINGER_FORMS`.
- **Unarmed Attack** (`items.json`) — added to the weapon catalog (Athletics,
  Strength-scaled impact, with the Unarmed Damage table in its description).
- **Singer L1 talent budget** (`build.py`) — `talents_available()` is now +1 for
  Singers (Change Form **plus** a starting-forms talent).

### Audit corrections found during implementation

Verifying against live behavior (not just the static packs) overturned three
flags and sharpened one:

- **Wound Regeneration** — *over-flag.* It already resolved to `{talent: Invested}`
  correctly; no change needed.
- **Kharbranthian / "missing cultures"** — *over-flag.* The builder already merges
  `handbook-cultures.json`; all 12 rulebook cultures (Alethi…Veden + Iriali,
  Kharbranthian, Listener, Natan, Reshi, Shin) are offered. The agent only
  inspected `cultures.json` (6).
- **Lightweaver Invested** — investigated as a possible bug, but the rulebook
  (SL:11568) confirms it IS a First-Ideal entry talent (`{ideal:1}` is correct);
  the data's stray doc-prereq was the wrong one. No change (a guard test locks
  this in).
- **Precise Parry** — corrected from the auto-synthesis's "Awareness 3" to the
  Perception **skill** (`prc`) rank 3 (its tree siblings confirm).
