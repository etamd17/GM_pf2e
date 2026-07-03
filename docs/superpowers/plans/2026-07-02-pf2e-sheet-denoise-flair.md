# PF2e Sheet De-noise + Ember/Rune Flair Implementation Plan (PR2b)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the PF2e sheet's at-rest redundancy (4-surface conditions, always-on prose, Speed/Init duplication, dead code) and port the tracker's ember/rune vocabulary (HP ghost+pops, hero flames+sparks, rune watermark, diamond cost pips, quiet ember ambient, dying pulse, level-up pop), plus the escaping-guard hardening.

**Architecture:** Templates live in `templates/_pc_sheet/*` partials included by `templates/player_sheet.html` (which holds ~3k lines CSS + ~7.4k lines inline JS). New flourish CSS goes in a "sheet ports" subsection appended to `static/css/system.css`'s COMBAT IDENTITY section, REUSING the existing keyframes (trkPop/trkSparkRise/trkFlame/trkEmber/trkCondIn) with sheet-scoped selectors — never modify the `.trk`-scoped rules. JS hooks attach at the sheet's existing paint sites (targeted-patch model — transitions survive; audit H4).

**Tech Stack:** Jinja, vanilla JS, CSS keyframes reuse, pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-pf2e-sheet-denoise-flair-design.md`
**Line-reference map (REQUIRED READING for every implementer):** `.superpowers/sdd/pf2e-sheet-audit.md` — finding IDs (R1, N1, F2, H1...) below refer to it. Trust its line refs over this plan's approximations; anchor on code snippets.

## Global Constraints

- No emojis anywhere.
- Branch: `feat/pf2e-sheet-denoise-flair` (created by controller). Commit per task; NEVER push. Trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- `python3 tools/check_templates.py` must pass after every template edit (`python` does not exist; use `python3`).
- Snapshot/correctness guards MUST stay green untouched: `tests/test_pc_snapshots.py`, `tests/test_pb_import_correctness.py` (this pass changes markup/CSS/JS only — never stat derivation).
- Every new animation inert under `@media (prefers-reduced-motion: reduce)`; JS particle/pop spawns gated by `matchMedia('(prefers-reduced-motion: reduce)')`. New decorative elements print-suppressed per the file's existing `@media print` conventions.
- Particles: ≤6 per burst, absolutely positioned, self-removing (animationend + timeout backstop).
- Inline-handler escaping: any `{{ ... }}` interpolated into an onclick JS string must carry `|replace("'", "\\'")` (or avoid inline handlers).
- The sheet renders owner-interactive AND GM-view variants — keep both working.

---

### Task 1: De-noise — conditions consolidation, prose folds, Speed/Init dedup, dead code

**Files:**
- Modify: `templates/_pc_sheet/` partials per audit S5/S8/S16 (condition strip stays; left-rail Quick Conditions folds; Combat-tab Conditions Matrix removed; left-rail Speed line removed; Initiative merged into Perception tile per audit refs `_header.html:148-355` defense grid)
- Modify: `templates/player_sheet.html` — delete `#cond-adder` panel (lines ~3062-3083, audit R1.4) + its JS references; delete `.action-badge-*` CSS (~914-926, audit N6); delete `_levelup_drawer.html` include + its ~70 CSS lines + `openLevelUpDrawer()`/`closeLevelUpDrawer()` (audit S/dead-code findings, `_header.html:100-108` comment confirms the button is a plain link)
- Delete: `templates/_pc_sheet/_levelup_drawer.html`
- Modify: Combat-tab partial (crit/MAP reminder, audit N1) + Magic-tab partial (caster mental-model paragraph, audit N2) — each wrapped in the Feats tab's existing `class-cheatsheet` `<details>` pattern (copy that exact pattern's classes; audit S-ref line 116 names it)

**Interfaces:**
- Consumes: audit findings R1, N1, N2, N6, S16, dead-drawer findings.
- Produces: exactly TWO condition surfaces (strip + folded left-rail editor). The `pc_update` conditions paint path must keep painting both; matrix paint calls removed WITH the matrix (grep the matrix's element ids in the JS and remove those paint branches — never leave getElementById(null) hot paths).

- [ ] **Step 1:** Read audit findings R1, N1, N2, N6 + the dead-drawer finding for exact locations. Make the removals/folds. For the left-rail Quick Conditions fold, reuse this pattern (mirror of the Cosmere sheet's conditions fold):

```html
<details class="qc-fold">
  <summary>Conditions <span id="qc-active-count"></span></summary>
  <!-- existing Quick Conditions rows unchanged inside -->
</details>
```

```css
.qc-fold > summary { cursor:pointer; list-style:none; font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--text-on-dark-3); }
.qc-fold > summary::-webkit-details-marker { display:none; }
#qc-active-count { color: var(--accent); }
```

With a tiny sync in the conditions paint path: `#qc-active-count` shows `(N active)` when any condition is set, empty otherwise. Anchor on where the strip repaints.

- [ ] **Step 2:** Initiative→Perception merge: the Perception tile in the defense grid gains a small sub-line `Initiative` (they are the same number by default in PF2e; the audit confirms the sheet renders the same value twice). Remove the standalone left-rail Initiative row and Speed line. If the sheet has an initiative-override feature wired to that row (check the JS for the row's id before deleting), keep the FUNCTION reachable from the Perception tile's sub-line instead — report as a concern if this turns out to be load-bearing beyond display.

- [ ] **Step 3:** Verify: `python3 tools/check_templates.py`; `python3 -m pytest tests/test_pc_snapshots.py tests/test_inline_handler_escaping.py -q`; then full `python3 -m pytest -q`.

- [ ] **Step 4:** Commit: `PF2e sheet de-noise: conditions strip+editor, prose folds, Speed/Init dedup, dead drawer/matrix/badge removal` (+ trailer).

---

### Task 2: Flair core — sheet-ports CSS, HP ghost+pops, hero flames+sparks, rune watermark

**Files:**
- Modify: `static/css/system.css` — append inside/after the COMBAT IDENTITY section:

```css
/* ── COMBAT IDENTITY: player-sheet ports ─────────────────────────────
   The tracker rules above are .trk-scoped. The PF2e sheet reuses the SAME
   keyframes with sheet-scoped selectors -- keep keyframes single-sourced. */
.hp-gauge-bar { position:relative; overflow:visible; }
.hp-gauge-bar .hp-ghost {
    position:absolute; left:0; top:0; bottom:0; border-radius:inherit;
    background:var(--ruby-200); opacity:.55; z-index:0;
    transition: width .7s cubic-bezier(.3,0,.2,1);
}
.hp-gauge-bar > :not(.hp-ghost) { position:relative; z-index:1; }
.hp-wrap-anchor { position:relative; }
.sheet-dmgpop {
    position:absolute; right:0; top:-6px; z-index:5;
    font-family:var(--font-display); font-weight:700; font-size:15px;
    color:var(--ruby-200); text-shadow:0 2px 6px rgba(0,0,0,.7);
    opacity:0; pointer-events:none;
}
.sheet-dmgpop.heal { color:#5fbf6a; }
.sheet-dmgpop.pop { animation: trkPop 1.1s ease-out forwards; }

.sheet-hero-flames { display:inline-flex; gap:4px; align-items:center; }
.sheet-hero-flames .hero-flame { width:13px; height:15px; position:relative; }
.sheet-hero-flames .hero-flame svg { width:100%; height:100%; }
.sheet-hero-flames .hero-flame.active svg { fill:#e8993a; filter:drop-shadow(0 0 4px rgba(232,153,58,.75)); animation: trkFlame 1.8s ease-in-out infinite; }
.sheet-hero-flames .hero-flame.spent svg { fill:transparent; stroke:rgba(201,163,78,0.3); stroke-width:1.5; }
.sheet-hero-flames .spark { position:absolute; left:50%; top:50%; width:3px; height:3px; border-radius:50%;
    background:#ffd79a; box-shadow:0 0 6px 1px rgba(255,180,90,.9); pointer-events:none; opacity:0; }
.sheet-hero-flames .spark.rise { animation: trkSparkRise 1.1s ease-out forwards; }

.sheet-port-glyph { position:absolute; inset:0; width:100%; height:100%; opacity:.26; color:var(--gilt-300); }

@media (prefers-reduced-motion: reduce) {
    .hp-gauge-bar .hp-ghost { transition:none; width:0 !important; }
    .sheet-dmgpop { display:none; }
    .sheet-hero-flames .hero-flame.active svg { animation:none; }
    .sheet-hero-flames .spark { display:none; }
}
@media print {
    .hp-gauge-bar .hp-ghost, .sheet-dmgpop, .sheet-hero-flames .spark { display:none; }
    .sheet-hero-flames .hero-flame.active svg { animation:none; filter:none; }
    .sheet-port-glyph { display:none; }
}
```

- Modify: `templates/player_sheet.html` — add `{% include "_pf2e_class_glyphs.html" %}` (audit F5 confirms absent) near the top of the content, and the JS hooks below
- Modify: `templates/_pc_sheet/_header.html` — portrait badge (lines ~36-38) gains the rune watermark behind the monogram:

```jinja
{% set _rune = {'champion':'rn-champion','kineticist':'rn-kineticist','cleric':'rn-cleric','druid':'rn-druid'}.get((pc.class_name or '').split(' ')[0].split('(')[0].lower(), 'rn-adventurer') %}
<svg class="sheet-port-glyph" viewBox="0 0 24 24" aria-hidden="true"><use href="#{{ _rune }}"></use></svg>
```
(inside the existing badge element, which must be/become `position:relative; overflow:hidden` — check its CSS; monogram spans get `position:relative`.)

- Modify: hero-point pips (`_header.html:62-71`): each pip becomes `<span class="hero-flame {{ 'active' if i < pc.hero_points else 'spent' }}"><svg viewBox="0 0 14 16"><use href="#rn-flame"></use></svg></span>` inside a `.sheet-hero-flames` wrapper — keep the existing +/- controls and ids the JS patches; the JS that toggles pip active-state now toggles `active`/`spent` classes on the flames (find the hero-point paint site per audit F2, ~lines 9935-10549 region).

**JS hooks (add near the sheet's HP/hero paint sites; complete code):**

```javascript
const _RM_PS = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
let _prevHp = null, _prevHero = null;
function sheetHpFlourish(cur, max){
    if(_prevHp !== null && cur !== _prevHp && !(_RM_PS && _RM_PS.matches)){
        const bar = document.querySelector('.hp-gauge-bar');
        const anchor = bar && bar.closest('.hp-wrap-anchor') || (bar && bar.parentElement);
        if(bar){
            const delta = cur - _prevHp;
            const pop = document.createElement('div');
            pop.className = 'sheet-dmgpop' + (delta > 0 ? ' heal' : '');
            pop.textContent = (delta > 0 ? '+' : '') + delta;
            (anchor || bar).appendChild(pop);
            void pop.offsetWidth; pop.classList.add('pop');
            pop.addEventListener('animationend', () => pop.remove());
            setTimeout(() => pop.remove(), 2400);
            if(delta < 0 && max > 0){
                let ghost = bar.querySelector('.hp-ghost');
                if(!ghost){ ghost = document.createElement('div'); ghost.className = 'hp-ghost'; bar.prepend(ghost); }
                ghost.style.transition = 'none';
                ghost.style.width = Math.round(_prevHp / max * 100) + '%';
                void ghost.offsetWidth; ghost.style.transition = '';
                setTimeout(() => { ghost.style.width = Math.round(cur / max * 100) + '%'; }, 120);
            }
        }
    }
    _prevHp = cur;
}
function sheetHeroFlourish(cur){
    if(_prevHero !== null && cur < _prevHero && !(_RM_PS && _RM_PS.matches)){
        const flames = document.querySelectorAll('.sheet-hero-flames .hero-flame');
        const snuffed = flames[Math.max(0, Math.min(flames.length - 1, cur))];
        if(snuffed){
            for(let i = 0; i < 5; i++){
                const s = document.createElement('div');
                s.className = 'spark';
                s.style.setProperty('--dx', (Math.random() * 26 - 13).toFixed(1) + 'px');
                snuffed.appendChild(s);
                setTimeout(() => s.classList.add('rise'), i * 50);
                s.addEventListener('animationend', () => s.remove());
                setTimeout(() => s.remove(), 2000);
            }
        }
    }
    _prevHero = cur;
}
```

Wire `sheetHpFlourish(cur, max)` into the ONE function that patches the HP number/bar (both local edits and `pc_update` SSE go through it — locate via audit F1 refs and grep for the element id the HP paint touches), and `sheetHeroFlourish(n)` into the hero-point paint site. First paint seeds silently (`_prev* === null`).

**Interfaces:**
- Consumes: sprite ids from `_pf2e_class_glyphs.html` (`rn-*`, `rn-flame`); existing keyframes trkPop/trkSparkRise/trkFlame.
- Produces: `.sheet-hero-flames`, `.hp-ghost`, `.sheet-dmgpop`, `.sheet-port-glyph` used by Task 4's verification.

- [ ] **Step 1:** CSS block into system.css (brace-balance check: `python3 -c "css=open('static/css/system.css').read(); assert css.count('{')==css.count('}'); print('ok')"`).
- [ ] **Step 2:** Sprite include + portrait watermark + flame markup (audit F4/F5/F2 refs).
- [ ] **Step 3:** JS hooks wired at paint sites.
- [ ] **Step 4:** Verify: check_templates; `python3 -m pytest tests/test_pc_snapshots.py -q`; full `python3 -m pytest -q`.
- [ ] **Step 5:** Commit: `PF2e sheet flair core: HP ghost+pops, hero flames+sparks, class-rune watermark (COMBAT IDENTITY sheet ports)` (+ trailer).

---

### Task 3: Flair secondary — pip unification, ember ambient, dying pulse, level-up pop + H1 hardening

**Files:**
- Modify: `templates/player_sheet.html` + affected `_pc_sheet/` partials — action-cost unification per audit N5/F3: locate the Jinja `action_pips()` macro (audit names it) and the `.action-cost` badge emitters; converge ALL cost displays on one diamond-pip cluster styled like the tracker's (`.action-pips`/`.action-pip` visual language — define sheet-scoped equivalents in the sheet-ports CSS section if the `.trk`-scoped ones don't reach; do NOT modify `.trk` rules). Remove the `.action-cost[data-cost]` hardcoded-hex rules (player_sheet.html ~1448-1470) once nothing emits that markup. Reaction/free render as the tracker does (`⟳` glyph / hollow pip).
- Ember ambient: the sheet's main content wrapper gets a `::after` glow reusing `trkEmber`, at reduced intensity:

```css
.sheet-ember-host { position:relative; overflow:hidden; }
.sheet-ember-host::after {
    content:""; position:absolute; top:-30%; bottom:-20%; left:-6%; width:60%;
    background:radial-gradient(ellipse at 30% 60%, rgba(232,153,58,0.55), transparent 62%);
    opacity:.04; animation: trkEmber 3.4s steps(6) infinite; pointer-events:none; z-index:0;
}
.sheet-ember-host > * { position:relative; z-index:1; }
@media (prefers-reduced-motion: reduce){ .sheet-ember-host::after { animation:none; } }
@media print { .sheet-ember-host::after { display:none; } }
```
(add the class to the sheet's main wrapper element; pick the outermost content container that doesn't already create a conflicting stacking context — report which element you chose.)

- Dying pulse: when the conditions paint path sets Dying > 0, add `dying-active` to the HP/Resources card:

```css
.hp-card.dying-active { animation: sheetDying 2.4s ease-in-out infinite; }
@keyframes sheetDying { 0%,100%{ box-shadow:0 0 0 rgba(220,60,60,0); } 50%{ box-shadow:0 0 22px rgba(220,60,60,.4); } }
@media (prefers-reduced-motion: reduce){ .hp-card.dying-active { animation:none; box-shadow:0 0 0 2px rgba(220,60,60,.5); } }
```
(adapt `.hp-card` to the Resources card's real class; reduced-motion keeps a STATIC red ring — dying must stay visible without motion.)

- Level-up pop: the ready-to-level banner/button (audit F7) gets `animation: trkCondIn .5s cubic-bezier(.2,1.4,.4,1)` when it first appears (class added at its paint/reveal site; reduced-motion: none).

- **H1 hardening (TDD):** First tighten `tests/test_inline_handler_escaping.py`'s regex per audit H1 (the greedy match spanning multiple interpolations masks an unescaped one adjacent to an escaped one) — run it and confirm it now FAILS against current `_header.html:136`. Then fix `_header.html:136`: `stat.label` gains `|replace("'", "\\'")`. Re-run: passes.

**Interfaces:**
- Consumes: Task 2's sheet-ports CSS section (append to it), `trkEmber`/`trkCondIn` keyframes.

- [ ] **Step 1:** H1 first (RED → fix → GREEN) — this is the only TDD-able piece; do it before the visual work.
- [ ] **Step 2:** Pip unification (audit N5/F3 refs), ambient, dying pulse, level-up pop.
- [ ] **Step 3:** Verify: check_templates; `python3 -m pytest tests/test_inline_handler_escaping.py tests/test_pc_snapshots.py -q`; full suite.
- [ ] **Step 4:** Commit: `PF2e sheet flair: unified cost pips, quiet ember ambient, dying pulse, level-up pop; escaping-guard hardening` (+ trailer).

---

### Task 4: Verification pass (controller-driven, live browser)

- [ ] Full `python3 -m pytest -q` + `python3 tools/check_templates.py` on final HEAD.
- [ ] Dev server with imported fixtures (Go'el Thrall — apostrophe; Amadeus): conditions = strip + folded editor only (matrix gone, orphan gone, count badge syncs); prose folded; header slots intact; Speed/Init dedup; rune watermark behind monogram; hero flames + sparks on spend; HP damage → ghost drain + red pop, heal → green pop; unified pips on strikes/skill-actions/spells; ember ambient subtle; dying > 0 → pulse (and static ring under reduced-motion emulation at code level); level-up banner pop.
- [ ] All ability-check buttons still roll (H1 fix didn't break the handler); GM view renders.
- [ ] Print preview sanity; no console errors.
