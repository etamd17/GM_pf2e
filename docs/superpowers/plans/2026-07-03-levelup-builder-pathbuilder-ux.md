# Level-up + Builder Pathbuilder-Grade UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Both PF2e wizards (level-up + creation) get the Pathbuilder-grade experience: a per-level gains checklist, every option list showing ALL options with ineligible ones greyed + the reason, a visible GM "ignore prerequisites" override, and light ceremony — with ZERO changes to stat derivation.

**Architecture:** One shared choice-picker partial replaces the duplicated-and-drifted `#uni-modal` in both templates (levelup's newer copy is the base). Server-side, an ADDITIVE structured-prereq parse at data load replaces the client's lossy description-regex as the eligibility source (raw fallback retained). The gains rail is driven by the `reqs` structure the levelup route already computes from class_matrix. Ceremony reuses the shipped COMBAT IDENTITY vocabulary.

**Tech Stack:** Flask/Jinja, vanilla JS, pytest (TDD for the parser + validate parity).

**Spec:** `docs/superpowers/specs/2026-07-03-levelup-builder-pathbuilder-ux-design.md`
**Line-reference map (REQUIRED READING per task):** `.superpowers/sdd/levelup-builder-audit.md` — findings W (levelup wizard), B (builder), E (eligibility data), G (gaps), H (hazards). Trust its refs; anchor on snippets.

## Global Constraints

- Branch `feat/levelup-builder-pathbuilder-ux`; commit per task; NEVER push. Trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. No emojis.
- **HIGH-RISK SURFACE:** `Character.__init__`, `class_matrix.py`, and `submit_levelup`'s derivation path are UNTOUCHABLE. `tests/test_pc_snapshots.py` + PB ground-truth tests must stay green UNMODIFIED. Backend changes are additive payload + read-only validation only.
- `python3 tools/check_templates.py` after template edits (python3, not python). Full `python3 -m pytest -q` before each commit (expect ~954 passed / ~25 skipped; on MASS failures suspect your environment — stray servers, exported DATA_DIR — before the code).
- Inline-handler escaping rule for any user string in onclick JS strings; extend `tests/test_inline_handler_escaping.py` coverage to any new inline handler you add.
- All new animation reduced-motion-inert + print-suppressed; particles ≤6/burst self-removing; greyed options keep an opacity floor of ~0.5 (locked = readable).
- Both wizards must work for EVERY class in BUILDER_DATA, both sheet variants, and with JS-disabled graceful degradation no worse than today.
- Do all work yourself in your turn — no background agents, no monitors (prior dispatches stalled in delegation loops).

---

### Task 1: Additive eligibility backbone (backend, TDD)

**Files:**
- Create: `tests/test_feat_prereq_parser.py`
- Modify: `app.py` — where BUILDER_FEATS is loaded/normalized (find via audit E-findings + grep `BUILDER_FEATS =`); the builder route (add the missing `skill_feat_prereqs` context the levelup route already passes — audit E/B plumbing gap)

**Interfaces:**
- Produces: each feat dict in BUILDER_FEATS gains `prereqs_struct: {level:int|None, abilities:{'str':16,...}, skills:{'athletics':'expert',...}, feats:[str,...], features:[str,...], raw:str}` (all keys always present; empty when nothing parsed; `raw` = the original prerequisite text, '' if none). Tasks 2-4 consume it client-side as `feat.prereqs_struct`.

- [ ] **Step 1 (RED):** Write `tests/test_feat_prereq_parser.py` covering the parser function `parse_feat_prereqs(text) -> dict`:

```python
"""Structured feat-prerequisite parsing (spec 2026-07-03, audit E1/E2/G4).

The client previously regex-scraped prerequisites out of description HTML and
only caught ability scores + skill ranks -- feat-chain and class-feature
prereqs silently passed eligibility. This parser runs ONCE at data load and
ships a structured field; anything unclassifiable lands in raw only (advisory,
never a false block)."""
import pytest

from app import parse_feat_prereqs


def test_empty_and_none():
    for v in (None, '', '   '):
        p = parse_feat_prereqs(v)
        assert p == {'level': None, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}


def test_ability_score():
    p = parse_feat_prereqs('Strength 16')
    assert p['abilities'] == {'str': 16}


def test_skill_rank():
    p = parse_feat_prereqs('expert in Athletics')
    assert p['skills'] == {'athletics': 'expert'}
    p2 = parse_feat_prereqs('trained in Occultism')
    assert p2['skills'] == {'occultism': 'trained'}


def test_feat_chain():
    p = parse_feat_prereqs('Power Attack')
    assert p['feats'] == ['Power Attack']


def test_compound():
    p = parse_feat_prereqs('Strength 14, expert in Athletics, Titan Wrestler')
    assert p['abilities'] == {'str': 14}
    assert p['skills'] == {'athletics': 'expert'}
    assert p['feats'] == ['Titan Wrestler']


def test_unparsable_lands_in_raw_only():
    txt = 'ability to cast focus spells'
    p = parse_feat_prereqs(txt)
    assert p['raw'] == txt
    assert p['abilities'] == {} and p['skills'] == {} and p['feats'] == [] and p['features'] == []


def test_master_legendary_ranks():
    assert parse_feat_prereqs('master in Religion')['skills'] == {'religion': 'master'}
    assert parse_feat_prereqs('legendary in Stealth')['skills'] == {'stealth': 'legendary'}
```

Adjust the exact classification heuristics to the REAL prerequisite strings in the data (sample 30+ from the feats data file the audit identifies; add tests for real shapes you find — e.g. "Level 8", class-feature names). The contract that cannot bend: unclassifiable text NEVER produces a blocking field, only `raw`.

Run: `python3 -m pytest tests/test_feat_prereq_parser.py -q` → FAILS (no function).

- [ ] **Step 2 (GREEN):** Implement `parse_feat_prereqs` in app.py near the BUILDER_FEATS load. Classification: split on commas/semicolons; each clause → ability (`(Strength|Dexterity|...)\s+(\d+)`), skill rank (`(trained|expert|master|legendary) in <Skill>` — match against the real skill list), level (`level (\d+)`), feat-chain (clause exactly matches another feat name in the dataset — build a name set first), class-feature (clause matches a known feature name list if one exists in BUILDER_DATA; else leave for raw), else → raw-only. Then, at load time, stamp `prereqs_struct` onto every feat dict (source text: the feat's existing prerequisites field if the data has one, else the description-extracted text the client used — audit E1 names where it lives). Keep it pure + cheap (runs once at boot).

- [ ] **Step 3:** Plumb `skill_feat_prereqs=SKILL_FEAT_PREREQS` into the builder route's render_template (mirroring the levelup route — audit B/E plumbing gap).

- [ ] **Step 4:** `python3 -m pytest tests/test_feat_prereq_parser.py tests/test_pc_snapshots.py -q` green; full suite green. Commit: `Feat prereqs: structured server-side parse (additive) + builder enrichment plumbing` (+ trailer).

---

### Task 2: Shared choice picker with grey-out-with-reason

**Files:**
- Create: `templates/_choice_picker.html` (markup + JS + minimal CSS for the picker, extracted from player_levelup.html's `#uni-modal` — the NEWER copy with the spell-heightening fix; audit W/B name the exact ranges)
- Modify: `templates/player_levelup.html` — replace its inline modal + picker JS with the include; `templates/player_builder.html` — same replacement (deleting its drifted copy)

**Interfaces:**
- Consumes: `feat.prereqs_struct` (Task 1), each wizard's existing "current PC state" objects (known feats, skill ranks, ability scores, level — audit W/B name the client variables).
- Produces: `openChoicePicker(config)` with the same call contract the wizards use today (keep the existing function names/signatures the steps call — the extraction must not change call sites beyond the include). A global `window.ignorePrereqs` boolean the picker reads (Task 3/4 wire the visible toggle to it; default false; the old per-modal checkbox is removed).

Behavior (locked): all options render; ineligible greyed (opacity ≥.5, no hover-lift) with a compact reason chip (first failing requirement: "Lv 8+" / "Requires Expert Athletics" / "Requires Power Attack" / "Already known") + full reasons on hover (title) — clicking a greyed option plays the existing shake keyframe and flashes the reason, never selects; already-taken renders greyed "Already known"; `raw`-only prereqs render an advisory "Verify: <raw>" line and do NOT grey; eligible-first sort, then alphabetical; search filters both groups; `window.ignorePrereqs` true → ineligible become selectable with an amber border + persistent reason chip.

- [ ] Steps: extract (levelup copy as base) → parameterize the two templates' differences (audit B lists the drift points — the spell-heightening fix stays; anything builder-only becomes a config flag) → wire `prereqs_struct` into `meetsPrereqs` (structured first, legacy regex fallback) with reason-string generation → replace both templates' inline copies with `{% include %}` → delete the dead copies entirely.
- [ ] Verify: `python3 tools/check_templates.py`; full suite; BOTH wizards render and their pickers open (dev-server smoke: import `tests/fixtures/goel_l10.json`, open /player/levelup/<name>; builder at /player/builder). Grep-proof: zero duplicated `renderModalList`/`meetsPrereqs` definitions remain outside the partial.
- [ ] Commit: `Shared choice picker: grey-out-with-reason, one implementation for levelup + builder` (+ trailer).

---

### Task 3: Level-up wizard — gains rail, GM override, ceremony

**Files:**
- Modify: `templates/player_levelup.html` (+ small additions to its own CSS block)

**Interfaces:**
- Consumes: the route's existing `reqs` structure (audit W: exact shape from class_matrix `base_prog`), `window.ignorePrereqs` (Task 2), COMBAT IDENTITY keyframes (trkCondIn, trkSparkRise) from system.css.

- [ ] **Gains rail:** persistent rail (sidebar ≥900px, top strip below) listing this level's gains from `reqs` ("Class Feat", "Skill Increase", ...), each with state (empty ring → gilt tick, tick pops in via trkCondIn on completion) and click-to-jump to the step. Completion state derives from the SAME per-step completion checks the wizard already uses for its step-nav gating (audit W names the function) — do not invent a second completion source.
- [ ] **Override toggle:** one visible switch in the rail header — "GM override: ignore prerequisites" (amber when on) → sets `window.ignorePrereqs`, re-renders any open picker, and feeds the existing `force_save` retry path so server behavior is unchanged. Remove the old per-modal checkbox markup.
- [ ] **Confirm ceremony:** on successful submit (the existing success handler, BEFORE redirect): a compact overlay card — "Level {{N}}" + the picks list + stat deltas the response already contains (audit W: what submit returns; use only fields already present) — with one ember spark burst (≤6 particles, spawn pattern from the sheet's `_orbMotes`-style helper) and the card popping in via trkCondIn. Auto-dismiss → redirect after ~2.5s or on click. Reduced-motion: static card, no particles, same info.
- [ ] Verify: templates parse; full suite; dev-server walk Go'el L10→L11 (rail lists Cleric 11 gains and matches the step set; ticks pop; override unlocks a greyed feat with amber styling; ceremony shows then redirects; revert_level returns the PC to L10). Extend the escaping test if any new inline handler carries user strings.
- [ ] Commit: `Level-up wizard: gains checklist rail, visible GM override, confirm ceremony` (+ trailer).

---

### Task 4: Builder parity + minimal server backstop

**Files:**
- Modify: `templates/player_builder.html`; `app.py` (`save_new_character` route only — validation wrapper, no derivation changes)
- Test: extend `tests/test_feat_prereq_parser.py` or new `tests/test_builder_validate.py`

**Interfaces:**
- Consumes: shared picker (Task 2), `prereqs_struct`, the builder's existing step structure (audit B).

- [ ] **Rail parity:** the builder's existing steps get the same rail treatment (its step list is static per class — derive from its current step registry, audit B names it). Same tick/pop/jump/override vocabulary; override feeds the picker + the new `force` flag.
- [ ] **Server backstop (TDD):** `save_new_character` gains levelup-parity validation: a read-only eligibility check over the submitted picks (reusing the same structures levelup_validate uses — audit W describes it) that returns a 409-style warning payload listing violations unless `force` is set. Client: on warning, show the violations with "Save anyway (GM override)" when `window.ignorePrereqs`/user confirms. Tests: a build violating a feat prereq is rejected without force and accepted with force; a clean build passes untouched; NO change to the accepted payload shape or the created character (assert the created doc for a clean build is byte-identical to pre-change behavior — snapshot-style).
- [ ] Verify: templates parse; full suite incl. new tests; dev-server from-scratch Champion L1 walk with the shared vocabulary end-to-end.
- [ ] Commit: `Builder: gains-rail parity, shared picker adoption, levelup-parity save validation with GM force` (+ trailer).

---

### Task 5: Verification pass (controller-driven, live browser)

- [ ] Full suite + check_templates on final HEAD; snapshot/ground-truth tests confirmed UNMODIFIED (git diff on tests/ shows only the new/extended files named above).
- [ ] Browser: Go'el L10→L11 full walk (rail, greyed reasons incl. a feat-chain case — verify a feat with a feat-prereq actually greys now, the headline data fix; override; ceremony; revert). Builder Champion walk. An unparsable-prereq feat shows the advisory, not a block. Reduced-motion code check. Apostrophe-name PC throughout.
- [ ] Cross-class spot check: open the levelup wizard for a class OUTSIDE the party (e.g. a freshly-built Rogue) to confirm the rail renders from class_matrix generically.
