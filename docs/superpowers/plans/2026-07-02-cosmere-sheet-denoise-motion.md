# Cosmere Sheet De-noise + Stormlight Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Cosmere sheet's at-rest redundancy (vitals bar duplicating header/spheres; four always-expanded low-signal blocks) and add the full Stormlight motion vocabulary (sphere change-pulses, spend-motes, breathe-inhale, crest breathe).

**Architecture:** Single-template pass on `templates/cosmere_sheet.html` (its own `extra_head` CSS + inline JS). Motion hooks at the one paint point `_orb()` that every mutation path already funnels through. No backend changes.

**Tech Stack:** Jinja, vanilla JS (IntersectionObserver), CSS keyframes.

**Spec:** `docs/superpowers/specs/2026-07-02-cosmere-sheet-denoise-motion-design.md`

## Global Constraints

- No emojis anywhere.
- Commit per task on the branch `feat/cosmere-sheet-denoise-motion`. NEVER push. Trailer exactly: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Every new animation inert under `@media (prefers-reduced-motion: reduce)`; JS particle spawns check `matchMedia('(prefers-reduced-motion: reduce)')`.
- No layout shift from flourishes; particles absolutely positioned, self-removing (animationend + timeout backstop), ≤6 per burst.
- After template edits: `python3 tools/check_templates.py` must pass.
- The sheet template also renders bestiary adversaries (`pc` falsy) and a read-only variant (`interactive` falsy) — every edit must keep those variants working.
- Existing tests that touch this DOM (keep them green without weakening): `tests/test_cosmere_sheet_redesign.py:30,45,56` (orb ids, orb-ctrl toggle, cs-vbar presence), `tests/test_cosmere_print.py:33` (cs-vbar print-hidden).

### Verified codebase facts (do not re-derive)

- Header tag row: `templates/cosmere_sheet.html:336-349`. Build-notes banner: lines 369-373 (`{% if pc and warnings %}`). Vitals bar markup: lines 374-383 (inside `{% if interactive %}`); its CSS at lines 256-265. Conditions block: lines 396-408 (interactive only; chips carry `data-cond`, toggled `.on`; `cosCond()` is the click handler). Goals block: lines 413-419 (`#cos-goals` is JS-rendered). Sphere bank `.cs-orbs`: lines 433-488. Rest row: 489-495.
- `_orb(key, val, max)` (single gauge painter) at ~line 928; `syncVbar()` ~938; `paint()` ~945 calls `_orb` for all three keys + `syncVbar()`. Every mutation path (cosAdjust/cosApply/cosBreathe/cosEnhance/cosRegenerate/cosSurge, rest, GM-pushed state) ends in `paint()`.
- `cosBreathe()` at ~line 969 (refills investiture to max, calls `paint()`).
- Existing reduced-motion block ~lines 272-275; print block ~309-318; `.cs-crest` renders only when `crest_glyph` (Radiant) at line 333.
- `.orb` clips its children (the fill is a bottom-anchored layer) — particles must be appended to the WRAPPER `.orb-w`, not the orb itself.

---

### Task 1: De-noise — vitals bar scroll-reveal, tag diet, folds, build-notes

**Files:**
- Modify: `templates/cosmere_sheet.html` (CSS lines ~256-265; markup lines 336-349, 369-383, 396-408, 413-419; JS near `paint()`)

**Interfaces:**
- Produces: `#cs-vbar` gains/loses class `gated` (hidden state); `#cos-conditions` gains class `folded` + `#cond-fold-btn` toggle + `#cond-none` placeholder; `syncCondFold()` called from `syncVbar()`. Task 2 does not depend on these; Task 3 verifies them.

- [ ] **Step 1: Vitals bar — JS-gated visibility (graceful when JS/IO absent)**

CSS: after the existing `.cs-vbar { ... }` rule (line ~256), add:

```css
  /* Scroll-reveal: the bar duplicates the header + spheres at rest, so JS
     gates it to appear only once the sphere bank leaves the viewport. With
     no IntersectionObserver (or JS dead) it stays always-visible as before. */
  .cs-vbar { transition:opacity .25s ease, transform .25s ease; }
  .cs-vbar.gated { opacity:0; transform:translateY(-8px); pointer-events:none; }
```

And inside the existing `@media (prefers-reduced-motion: reduce)` block (~line 272), add `.cs-vbar { transition:none; }`.

Markup (line 377): DELETE the name span — `<span class="vb-name">{{ a.name }}</span>` — and its CSS rule `.cs-vbar .vb-name { ... }` (line ~260).

JS: immediately after the `paint()` function definition (~line 951), add:

```javascript
    // Scroll-reveal for the vitals bar: hidden while the sphere bank is on
    // screen (it would duplicate the spheres), revealed once scrolled past.
    (function(){
      var vbar = document.getElementById('cs-vbar'),
          orbs = document.querySelector('.cs-orbs');
      if(!vbar || !orbs || !('IntersectionObserver' in window)) return;
      new IntersectionObserver(function(entries){
        vbar.classList.toggle('gated', entries[0].isIntersecting);
      }, { rootMargin: '-40px 0px 0px 0px' }).observe(orbs);
    })();
```

- [ ] **Step 2: Header tag diet (PC view only; adversary view unchanged except Cosmere RPG drops)**

Replace lines 336-349 (`<div class="cs-sub">` ... `</div>`) with:

```jinja
      <div class="cs-sub">
        {% if not pc %}
        <span class="cs-tag">{{ a.type|capitalize }}</span>
        {% if a.tier is not none %}<span class="cs-tag">Tier {{ a.tier }}</span>{% endif %}
        {% if a.role %}<span class="cs-tag">{{ a.role|capitalize }}</span>{% endif %}
        {% if a.size %}<span class="cs-tag">{{ a.size|capitalize }}</span>{% endif %}
        {% else %}
        {# PC tag diet: identity that matters at a glance. Type/role are implied
           by the page; size only when rules-relevant (non-Medium); the system
           tag is implied by the campaign. #}
        {% if a.tier is not none %}<span class="cs-tag">Tier {{ a.tier }}</span>{% endif %}
        {% if a.size and a.size|lower != 'medium' %}<span class="cs-tag">{{ a.size|capitalize }}</span>{% endif %}
        {% if build %}
          {% if build.path %}<span class="cs-tag">{{ build.path|capitalize }}</span>{% endif %}
          {% if build.culture %}<span class="cs-tag">{{ build.culture }}</span>{% endif %}
          {% if build.ancestry %}<span class="cs-tag">{{ build.ancestry }}</span>{% endif %}
          {% if singer_form %}<span class="cs-tag" title="{{ singer_form.note }}">Form: {{ singer_form.name }}</span>{% endif %}
          {% if build.is_radiant %}<span class="cs-tag">Radiant</span>{% endif %}
        {% endif %}
        {% endif %}
        {% if pc and ready_to_level %}<a class="cs-tag" href="{{ edit_url }}" style="background:rgba(110,231,168,.16); border-color:rgba(110,231,168,.5); color:#6ee7a8; text-decoration:none;">&#9650; Ready to level up</a>{% endif %}
      </div>
```

(The original `{% if pc and build %}` inner block moves into the `{% else %}` branch as `{% if build %}` — `pc` is already true there. The `Cosmere RPG` tag is gone from both branches.)

- [ ] **Step 3: Build-notes banner → one-line expandable**

Replace lines 369-373 with:

```jinja
  {% if pc and warnings %}
  <details class="cs-buildnotes" style="margin-bottom:1rem;">
    <summary style="cursor:pointer; padding:.4rem .8rem; border-radius:8px; background:rgba(245,158,11,.10); border:1px solid rgba(245,158,11,.4); color:#fbbf24; font-size:12.5px; list-style:none;"><b>Build notes</b> ({{ warnings|length }}) <span style="opacity:.65;">&#9656; tap to review</span></summary>
    <div style="padding:.5rem .8rem; color:#fbbf24; font-size:12.5px; border:1px solid rgba(245,158,11,.25); border-top:none; border-radius:0 0 8px 8px;">{{ warnings|join(' &middot; ')|safe }}</div>
  </details>
  {% endif %}
```

Note: `warnings` entries are server-generated strings (not user input) — the existing banner already rendered them unescaped-equivalent via `join`; keep `|safe` off if the originals were auto-escaped: match the ORIGINAL escaping behavior exactly (`{{ warnings|join(' · ') }}` was auto-escaped, so drop the `|safe` and use the plain join form).

- [ ] **Step 4: Conditions fold (active-only by default)**

Replace the conditions block header line (398) and chips container (399-403) with:

```jinja
    <div style="font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--text-on-dark-3); margin-bottom:.35rem;">Conditions
      <button type="button" id="cond-fold-btn" onclick="condFoldToggle()" style="background:none; border:none; color:var(--accent); cursor:pointer; font-size:11px; letter-spacing:.05em; text-transform:none; padding:0 .2rem;">edit</button>
    </div>
    <div class="cs-chips folded" id="cos-conditions">
      <span id="cond-none" style="font-size:12px; color:var(--text-on-dark-3); font-style:italic;">No conditions</span>
      {% for code, info in conditions.items() %}
      <span class="cs-cond {{ 'on' if cur.conditions.get(code) }}" data-cond="{{ code }}" onclick='cosCond({{ code|tojson }})' title="{{ info }}">{{ code|capitalize }}</span>
      {% endfor %}
    </div>
```

CSS (add near the `.cs-chips` styles):

```css
  /* Folded conditions: show only ACTIVE chips; the full picker expands via
     the edit toggle. Purely display -- chip DOM and cosCond() are unchanged,
     so GM-pushed condition updates keep painting. */
  .cs-chips.folded .cs-cond:not(.on) { display:none; }
  #cond-none { display:none; }
  .cs-chips.folded.none-active #cond-none { display:inline; }
```

JS: add alongside the vbar observer:

```javascript
    window.condFoldToggle = function(){
      var box = document.getElementById('cos-conditions'),
          btn = document.getElementById('cond-fold-btn');
      if(!box) return;
      var folded = box.classList.toggle('folded');
      if(btn) btn.textContent = folded ? 'edit' : 'done';
      syncCondFold();
    };
    function syncCondFold(){
      var box = document.getElementById('cos-conditions');
      if(!box) return;
      var any = !!box.querySelector('.cs-cond.on');
      box.classList.toggle('none-active', !any);
    }
```

And add one call `syncCondFold();` inside `syncVbar()` (it runs on every `paint()`, which covers self-toggles and GM pushes). Guard: `syncCondFold` must be defined before `syncVbar` runs at init — place both function definitions ABOVE the first `paint()` call site; if `syncVbar` exists in a scope where `syncCondFold` isn't visible, define `syncCondFold` in the same scope.

- [ ] **Step 5: Goals empty-state compression**

The empty case renders in `paintGoals()` (template line ~1263-1266, verified):

```javascript
      if(!gs.length){ box.innerHTML = '<div class="goal-empty">No goals yet. Add one to start working toward a reward.</div>'; return; }
```

Replace that line with:

```javascript
      const wrap = box.closest('.cs-goals-wrap');
      if (wrap) wrap.classList.toggle('empty', !gs.length);
      if(!gs.length){ box.innerHTML = ''; return; }
```

(Note: the non-empty path must also clear `.empty` — the `classList.toggle` with the boolean second argument above handles both directions, and it runs before the early return.)

CSS:

```css
  .cs-goals-wrap.empty { margin-bottom:.4rem; }
```

The header row's label + "+ Add goal" button already communicate the empty state. Keep the add-goal flow untouched.

- [ ] **Step 6: Verify + fix affected tests**

Run: `python3 tools/check_templates.py` → exit 0.
Run: `python3 -m pytest tests/test_cosmere_sheet_redesign.py tests/test_cosmere_print.py tests/test_cosmere_sheet_polish.py tests/test_cosmere_sheet_extras.py tests/test_cosmere_conditions_ui.py -q` → all pass. If a test asserts removed markup (e.g. the `Cosmere RPG` tag or `vb-name`), update THAT assertion to the new contract — do not re-add the noise.

- [ ] **Step 7: Commit**

```bash
git add templates/cosmere_sheet.html
git commit -m "Cosmere sheet de-noise: scroll-reveal vitals bar, PC tag diet, conditions fold, compact build notes + empty goals

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Stormlight motion — sphere pulses, spend-motes, breathe inhale, crest breathe

**Files:**
- Modify: `templates/cosmere_sheet.html` (extra_head CSS; `_orb()` JS ~line 928; `cosBreathe()` ~line 969; print + reduced-motion blocks)

**Interfaces:**
- Consumes: `_orb(key, val, max)`, `cosBreathe()`, `.orb-w` wrappers, `.cs-crest`.
- Produces: `_orbFlourish`, `_orbMotes` (internal); classes `pulse-up`/`pulse-down` on `.orb`, `.orb-mote` particles in `.orb-w`.

- [ ] **Step 1: CSS — pulses, motes, crest breathe**

Add to the sheet's extra_head CSS (near the orb styles):

```css
  /* ===== Stormlight motion: sphere change-pulses + spend-motes ===== */
  .orb-w { position:relative; }
  .orb.pulse-up { animation:orbPulseUp .8s ease-out; }
  .orb.pulse-down { animation:orbPulseDown .8s ease-out; }
  #orb-health.pulse-down { animation:orbWound .8s ease-out; }
  @keyframes orbPulseUp { 0%,100%{ } 25%{ box-shadow:0 0 26px var(--orb-glow), 0 0 60px var(--orb-glow); filter:brightness(1.28); } }
  @keyframes orbPulseDown { 0%,100%{ } 30%{ filter:brightness(.68) saturate(.7); } }
  @keyframes orbWound { 0%,100%{ } 25%{ box-shadow:0 0 22px rgba(220,60,60,.6); filter:brightness(.8) saturate(1.3) hue-rotate(-18deg); } }

  .orb-mote { position:absolute; left:50%; top:42%; width:4px; height:4px; border-radius:50%;
    opacity:0; pointer-events:none; z-index:5; }
  .orb-mote.focus { background:#fff3d6; box-shadow:0 0 7px 2px rgba(242,200,121,.85); }
  .orb-mote.inv { background:#f4fbff; box-shadow:0 0 7px 2px var(--order-accent,#7ec7ff); }
  .orb-mote.rise { animation:orbMoteRise 1.2s ease-out forwards; }
  .orb-mote.fall { animation:orbMoteFall 1.0s ease-in forwards; }
  @keyframes orbMoteRise {
    0%{ opacity:0; transform:translate(-50%,-50%); } 12%{ opacity:1; }
    100%{ opacity:0; transform:translate(-50%,-50%) translate(var(--dx,0px),-64px) scale(.4); } }
  @keyframes orbMoteFall {
    0%{ opacity:0; transform:translate(-50%,-50%) translate(var(--dx,0px),-64px) scale(.5); } 15%{ opacity:1; }
    100%{ opacity:0; transform:translate(-50%,-50%); } }

  /* Order crest breathes on the highstorm cadence (Radiant header glyph). */
  .cs-crest { animation:crestBreathe 10s ease-in-out infinite; }
  @keyframes crestBreathe {
    0%,100%{ filter:drop-shadow(0 0 2px transparent); opacity:.92; }
    50%{ filter:drop-shadow(0 0 9px var(--order-accent,#5fa8e0)); opacity:1; } }
```

Extend the existing reduced-motion block:

```css
    .orb.pulse-up, .orb.pulse-down, #orb-health.pulse-down { animation:none; }
    .orb-mote { display:none; }
    .cs-crest { animation:none; }
```

Extend the print block: `.orb-mote { display:none; } .cs-crest { animation:none; filter:none; }`

- [ ] **Step 2: JS — flourish hook inside `_orb()`**

Above `_orb` add:

```javascript
    const _RM_SHEET = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
    const _prevOrb = {};
    function _orbMotes(key, kind){
      if(_RM_SHEET && _RM_SHEET.matches) return;
      const o = document.getElementById('orb-' + key); if(!o) return;
      const w = o.closest('.orb-w'); if(!w) return;
      const cls = key === 'focus' ? 'focus' : 'inv';
      for(let i = 0; i < 5; i++){
        const m = document.createElement('div');
        m.className = 'orb-mote ' + cls;
        m.style.setProperty('--dx', (Math.random() * 36 - 18).toFixed(1) + 'px');
        w.appendChild(m);
        setTimeout(() => m.classList.add(kind), i * 60);
        m.addEventListener('animationend', () => m.remove());
        setTimeout(() => m.remove(), 2200 + i * 60);
      }
    }
    function _orbFlourish(key, prev, val){
      if(_RM_SHEET && _RM_SHEET.matches) return;
      const o = document.getElementById('orb-' + key); if(!o) return;
      const cls = val > prev ? 'pulse-up' : 'pulse-down';
      o.classList.remove('pulse-up', 'pulse-down'); void o.offsetWidth;
      o.classList.add(cls);
      o.addEventListener('animationend', () => o.classList.remove(cls), { once:true });
      if(val < prev && (key === 'focus' || key === 'investiture')) _orbMotes(key, 'rise');
    }
```

Inside `_orb(key, val, max)`, after the existing `o.classList.toggle('low', ...)` line, add:

```javascript
      if(_prevOrb[key] !== undefined && _prevOrb[key] !== val) _orbFlourish(key, _prevOrb[key], val);
      _prevOrb[key] = val;
```

(First paint seeds `_prevOrb` silently — no flourish storm on page load.)

- [ ] **Step 3: Breathe Stormlight — inhale motes**

In `cosBreathe()` (~line 969), immediately before its `paint()` call, add:

```javascript
      _orbMotes('investiture', 'fall');
```

(The refill also triggers `pulse-up` via the `_orb` hook; falling motes read as inhaling Stormlight.)

- [ ] **Step 4: Verify**

Run: `python3 tools/check_templates.py` → exit 0.
Run: `python3 -m pytest tests/test_cosmere_sheet_redesign.py tests/test_cosmere_print.py -q` → pass.
Eyeball the inserted JS for balanced braces (or `node --check` on the extracted block if node is available).

- [ ] **Step 5: Commit**

```bash
git add templates/cosmere_sheet.html
git commit -m "Cosmere sheet motion: sphere change-pulses, spend-motes, Breathe inhale, crest breathe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Verification pass (controller-driven, live browser)

- [ ] **Step 1:** Full `python3 -m pytest -q` + `python3 tools/check_templates.py`.
- [ ] **Step 2:** Dev server + seeded Radiant PC (create via /cosmere builder or fixture): verify at top of page the vitals bar is hidden; scroll past spheres → bar slides in; scroll back → hides. Tag row shows the dieted set. Conditions folded ("No conditions" → toggle a condition → chip appears folded; "edit" expands full picker). Build notes render as one line and expand. Empty goals = single row.
- [ ] **Step 3:** Damage → health sphere wound-pulse (red-tinted, no motes); heal → bright pulse; Focus/Investiture spend → dim pulse + rising motes in the right colors; Breathe Stormlight → falling motes + bright pulse; crest breathing visible. No console errors. Numbers never obscured.
- [ ] **Step 4:** Read-only sheet variant + a bestiary adversary sheet render correctly (no vbar/conditions JS errors; adversary tags intact).
- [ ] **Step 5:** Print preview: no motes/crest filter artifacts.
