# Tracker Visual Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the combat tracker a per-system visual identity — PF2e torchlit/rune-carved (ember flicker, class-rune portrait watermarks, Hero Point flames with spark particles) and Cosmere Stormlight-luminous (storm breathe, Radiant order-glyph watermarks, breathing Investiture sphere with light motes) — plus shared combat juice (HP ghost-bar drain, damage pops, row flash/shake, condition pulse-in) on every state change.

**Architecture:** All work extends existing patterns. Two small backend payload additions (Cosmere `tracker_block` + sheet-sync mirror). One new SVG sprite partial (`_pf2e_class_glyphs.html`, parallel to the existing `_cosmere_glyphs.html`). A new CSS component section at the end of `static/css/system.css`. A client-side flourish layer in `templates/tracker.html` that diffs vitals across renders (the tracker re-renders rows wholesale via `innerHTML`, so flourishes are applied *after* render to fresh nodes, driven by a `_prevVitals` map).

**Tech Stack:** Flask/Jinja, vanilla JS, CSS keyframe animations, SVG sprites, pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-tracker-visual-identity-design.md`

## Global Constraints

- No emojis anywhere (code, comments, commits, UI).
- Commit per task, on this branch. NEVER push; never commit to `main`. Co-author trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Every animation must be disabled under `@media (prefers-reduced-motion: reduce)`; JS-spawned particles/pops must check `matchMedia('(prefers-reduced-motion: reduce)')` and no-op.
- Any user-controlled string interpolated into an inline `onclick="..."` JS string must be escaped: `.replace(/\\/g,'\\\\').replace(/'/g,"\\'")`. Prefer avoiding inline handlers entirely.
- After editing any `.html`: run `python tools/check_templates.py`.
- Original glyph artwork only — monoline `currentColor` strokes in the style of `templates/_cosmere_glyphs.html`; no copies of published symbols.
- The two systems must not visually converge: PF2e motion is warm/irregular (ember, steps() timing), Cosmere is cool/smooth (storm, ease timing).

### Verified codebase facts (do not re-derive)

- `_get_tracker_state` (app.py:6896) already ships per-combatant `system`, `class_name`+`hero_points` (PC branch), and `entry['cosmere'] = c.tracker_block()` for Cosmere combatants. The SSE frame (`_do_broadcast_encounter_state`) ships `hero_points` and `cosmere` too.
- The client SSE patch path (templates/tracker.html:4359-4374) merges `hero_points` but NOT the `cosmere` sub-object — Task 4 adds that one line.
- `renderInitList()` (templates/tracker.html:2318) rebuilds all rows with `list.innerHTML = ...` on every render. `_renderNow()` (templates/tracker.html:2108) is the single post-render hook point.
- Row markup: portrait `<div class="port ${portCls}">${initials(c.name)}</div>` at tracker.html:2454; HP cell `.c-hp` with `.c-hp-bar`/`.c-hp-fill` at tracker.html:2464-2470. `.trk .c-name` is `display:flex; align-items:center; gap:9px` (tracker.html:453). `.trk .port` is already `position:relative; overflow:hidden` (tracker.html:454-459).
- `CosmereActor.tracker_block()` is at systems/cosmere/actor.py:252 and already emits `investiture_max`.
- `_cosmere_combatant(actor_id)` (app.py:7112) builds tracker combatants; the PC branch loads the saved doc (`_load_cosmere_pc`) which has `build.radiant_order` and `play_state.investiture`.
- `_sync_cosmere_combatant_state(name, ps)` (app.py:8092) mirrors health/injuries/conditions from a sheet save onto the live combatant — investiture is currently dropped.
- Radiant order slugs in `build.radiant_order` are lowercase plural (`windrunners`, `skybreakers`, ...) and match the existing glyph symbol ids `cg-order-<slug>` in `templates/_cosmere_glyphs.html` exactly.
- Test construction pattern for Cosmere combatants + the sheet-state POST fixture: `tests/test_cosmere_sync.py` (`_combatant()` helper + `pc` fixture). Copy that pattern.
- Inspector spell rows (tracker.html:2917-2930) receive `{name, actions}` per spell but currently render names only.
- Local dev server: `DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 FLASK_DEBUG=true python app.py`. CSS is browser-cached — cache-bust with `?cb=` when testing.

---

### Task 1: Cosmere payload — Investiture + Radiant order reach the tracker

**Files:**
- Test: `tests/test_tracker_visual_payload.py` (create)
- Modify: `systems/cosmere/actor.py:252-271` (`tracker_block`)
- Modify: `app.py:7121-7128` (`_cosmere_combatant` PC branch)
- Modify: `app.py:8092-8113` (`_sync_cosmere_combatant_state`)

**Interfaces:**
- Produces: `tracker_block()` dict gains `'investiture_current': int` (defaults to `investiture_max` when never spent) and `'radiant_order': str` (lowercase plural slug, `''` when none). Tasks 4-5 read `c.cosmere.investiture_current`, `c.cosmere.investiture_max`, `c.cosmere.radiant_order` client-side.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracker_visual_payload.py`:

```python
"""Tracker visual-identity payload: Investiture + Radiant order on Cosmere
combatants (spec 2026-07-02-tracker-visual-identity-design.md).

The tracker's Investiture sphere and order-glyph watermark render from
tracker_block(); a player's sheet save must mirror investiture onto the live
combatant the same way health already mirrors."""
from __future__ import annotations

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _combatant(name, spd=3):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': spd}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name + '-1'
    a.system = 'cosmere'
    a.is_pc = True
    return a


@pytest.fixture
def pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    pid = 'cd' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 3, 'path': 'warrior',
                     'radiant_order': 'windrunners', 'first_ideal_sworn': True,
                     'attributes': {'str': 3, 'spd': 3, 'wil': 1, 'awa': 2}, 'skills': {'hwp': 2}},
           'play_state': {'investiture': 1}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    app.ACTIVE_ENCOUNTER[:] = [_combatant('Kaladin')]
    app.TURN_INDEX = 0
    yield pid
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0


def test_tracker_block_defaults():
    """A bare combatant reports full investiture and no order — never KeyErrors."""
    blk = _combatant('Szeth').tracker_block()
    assert blk['investiture_current'] == blk['investiture_max']
    assert blk['radiant_order'] == ''


def test_tracker_block_reflects_spend_and_order():
    c = _combatant('Kaladin')
    c.radiant_order = 'windrunners'
    c.current_investiture = 1
    blk = c.tracker_block()
    assert blk['investiture_current'] == 1
    assert blk['radiant_order'] == 'windrunners'


def test_cosmere_combatant_seeds_order_and_investiture(pc):
    """Adding a saved PC to the tracker carries order + spent investiture,
    so the sphere is correct even before the next sheet save."""
    c = app._cosmere_combatant(pc)
    assert c is not None
    blk = c.tracker_block()
    assert blk['radiant_order'] == 'windrunners'
    assert blk['investiture_current'] == 1


def test_sheet_save_mirrors_investiture_to_live_combatant(pc):
    r = app.app.test_client().post(
        '/cosmere/pc/' + pc + '/state', json={'investiture': 0})
    assert r.get_json()['ok']
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    assert me.tracker_block()['investiture_current'] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tracker_visual_payload.py -v`
Expected: 4 FAIL — `KeyError: 'investiture_current'` (first three) and the last asserting `investiture_current == 0` against a full-investiture block.

- [ ] **Step 3: Implement — `tracker_block` additions**

In `systems/cosmere/actor.py`, inside `tracker_block()` (line 252), add two keys after `'investiture_max': self.investiture_max,`:

```python
            'investiture_max': self.investiture_max,
            # Live Investiture + Radiant order for the tracker's sphere and
            # order-glyph watermark. current defaults to max (a fresh
            # combatant is fully Invested); order is set by _cosmere_combatant
            # for PCs and stays '' for adversaries.
            'investiture_current': int(getattr(self, 'current_investiture',
                                               self.investiture_max) or 0),
            'radiant_order': str(getattr(self, 'radiant_order', '') or ''),
```

- [ ] **Step 4: Implement — seed at combatant creation**

In `app.py`, `_cosmere_combatant` (line 7112), the PC branch currently reads:

```python
    pc = _load_cosmere_pc(actor_id)
    if pc:
        import systems.cosmere.build as _cb
        actor = systems.cosmere.CosmereActor(
            _cb.CosmereBuild(pc.get('build') or {}, homebrew=_cosmere_homebrew_store()).to_actor_doc())
        actor.name = pc.get('name') or actor.name
        actor.restore_id = actor_id        # so the encounter autosave can rehydrate it
        return actor
```

Change to:

```python
    pc = _load_cosmere_pc(actor_id)
    if pc:
        import systems.cosmere.build as _cb
        actor = systems.cosmere.CosmereActor(
            _cb.CosmereBuild(pc.get('build') or {}, homebrew=_cosmere_homebrew_store()).to_actor_doc())
        actor.name = pc.get('name') or actor.name
        actor.restore_id = actor_id        # so the encounter autosave can rehydrate it
        # Tracker visual identity: the order-glyph watermark + Investiture
        # sphere need the PC's order and CURRENT investiture at add time —
        # otherwise the sphere shows full until the player's next sheet save.
        actor.radiant_order = str((pc.get('build') or {}).get('radiant_order') or '').lower()
        _ps = pc.get('play_state') or {}
        if 'investiture' in _ps:
            try:
                actor.current_investiture = max(0, int(_ps['investiture']))
            except (TypeError, ValueError):
                pass
        return actor
```

- [ ] **Step 5: Implement — mirror investiture on sheet save**

In `app.py`, `_sync_cosmere_combatant_state` (line 8092), after the `'injuries'` block add:

```python
            if 'investiture' in ps:
                try: c.current_investiture = max(0, int(ps['investiture']))
                except (TypeError, ValueError): pass
```

(Same style as the `health`/`injuries` mirrors above it.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_tracker_visual_payload.py -v`
Expected: 4 PASS

- [ ] **Step 7: Run adjacent suites to catch regressions**

Run: `pytest tests/test_cosmere_sync.py tests/test_tracker_cosmere_render.py tests/test_cosmere_adversary_tracker.py -q`
Expected: PASS (tracker_block gained keys; nothing consumed removed keys)

- [ ] **Step 8: Commit**

```bash
git add tests/test_tracker_visual_payload.py systems/cosmere/actor.py app.py
git commit -m "Tracker payload: live Investiture + Radiant order on Cosmere combatants

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: PF2e class-rune sprite partial + sprite includes on the tracker

**Files:**
- Create: `templates/_pf2e_class_glyphs.html`
- Modify: `templates/tracker.html` (include both sprite partials near the existing flourish includes — search for `{% include "_boss_reveal.html" %}` and add the two includes adjacent)

**Interfaces:**
- Produces: SVG symbol ids `rn-champion`, `rn-kineticist`, `rn-cleric`, `rn-druid`, `rn-adventurer` (fallback), `rn-flame` (Hero Point flame shape), referenced as `<svg class="rg"><use href="#rn-..."></use></svg>`. Task 4's `classRuneId()` maps `class_name` → these ids. Cosmere ids `cg-order-<slug>` come from the existing `_cosmere_glyphs.html` include.

- [ ] **Step 1: Create the sprite partial**

Create `templates/_pf2e_class_glyphs.html`:

```html
{# ════════════════════════════════════════════════════════════════════════════
   PF2E CLASS RUNE SPRITE
   Original monoline "carved rune" marks (ring + inner mark) for the party's
   classes, plus a generic adventurer fallback so an unmapped class never
   renders empty. One shared geometric language, parallel to the Cosmere
   glyph sprite (_cosmere_glyphs.html) — NOT copies of published iconography.

   Include once per page; reference with:
     <svg class="rg"><use href="#rn-champion"></use></svg>
   Strokes inherit currentColor; size via CSS on .rg.
   rn-flame is the Hero Point flame (filled, not stroked).
   ════════════════════════════════════════════════════════════════════════════ #}
<svg width="0" height="0" style="position:absolute;width:0;height:0;overflow:hidden;" aria-hidden="true" focusable="false">
  <defs>
    <symbol id="rn-champion" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6l4.5 1.8V12c0 3.2-2 5.3-4.5 6.5C9.5 17.3 7.5 15.2 7.5 12V7.8z"/><path d="M12 9.5v5M9.8 12h4.4"/></symbol>
    <symbol id="rn-kineticist" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6.5l3 3-3 3-3-3z"/><path d="M7 16c1.6-1.2 3.2.8 5 0s3.4-1.2 5 0"/></symbol>
    <symbol id="rn-cleric" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 7.5v9M7.5 12h9"/><path d="M9 9L7.6 7.6M15 9l1.4-1.4M9 15l-1.4 1.4M15 15l1.4 1.4"/></symbol>
    <symbol id="rn-druid" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M8 16c0-5 3-8 8-8 0 5-3 8-8 8z"/><path d="M9.5 14.5c2-2 4-4 6-6"/></symbol>
    <symbol id="rn-adventurer" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><path d="M12 6l1.8 4.2L18 12l-4.2 1.8L12 18l-1.8-4.2L6 12l4.2-1.8z"/></symbol>
    <symbol id="rn-flame" viewBox="0 0 14 16"><path d="M7 0c1 3-3 4-3 7.5A3.5 3.5 0 0 0 7 11a2.5 2.5 0 0 0 1-4.8C9.3 7.5 10 9 10 10a3.5 3.5 0 0 1-7 0c0-1.2.5-2 1-3C4.6 5.7 6 4 7 0z"/></symbol>
  </defs>
</svg>
<style>
  .rg { width:1em; height:1em; display:inline-block; vertical-align:-0.15em; }
</style>
```

- [ ] **Step 2: Include both sprites in the tracker**

In `templates/tracker.html`, find the flourish includes (search `_boss_reveal.html`) and add adjacent to them:

```jinja
{% include "_pf2e_class_glyphs.html" %}
{% include "_cosmere_glyphs.html" %}
```

- [ ] **Step 3: Verify templates parse**

Run: `python tools/check_templates.py`
Expected: exit 0, no errors

- [ ] **Step 4: Verify the symbols resolve in a served page**

Run: `DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 python app.py &` then
`sleep 3 && curl -s localhost:5057/tracker | grep -c 'rn-champion\|cg-order-windrunners'; kill %1`
Expected: `2` or more (both sprites present in the page)

- [ ] **Step 5: Commit**

```bash
git add templates/_pf2e_class_glyphs.html templates/tracker.html
git commit -m "PF2e class-rune sprite partial; include both glyph sprites on the tracker

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: CSS component section in system.css

**Files:**
- Modify: `static/css/system.css` (append a new section immediately before the final `/* End of system.css */` line, currently line 4592)

**Interfaces:**
- Produces CSS classes consumed by Tasks 4-6: `.trk-ambient` (on `.grid-wrap`), `.port-glyph`, `.port-init`, `.hero-flames`, `.hero-flame` (+`.active`/`.spent`), `.spark`, `.cos-sphere-wrap`, `.cos-sphere` (+`.dim`), `.mote`, `.c-hp-ghost`, `.trk-dmgpop` (+`.pop`, `.heal`), `.trk-row-hit`, `.trk-cond-in`, `.action-pips`, `.action-pip` (+`.reaction`, `.free`).

- [ ] **Step 1: Append the component section**

Append to `static/css/system.css` (before `/* End of system.css */`):

```css
/* ════════════════════════════════════════════════════════════════════
   COMBAT IDENTITY — tracker flourishes + per-system atmosphere
   (spec: docs/superpowers/specs/2026-07-02-tracker-visual-identity-design.md)
   PF2e reads torchlit (warm, irregular ember timing); Cosmere reads
   Invested (cool, smooth storm timing). Rows re-render wholesale via
   innerHTML, so idle loops restart on state changes — all idle keyframes
   start and end at their rest state to make restarts invisible.
   ════════════════════════════════════════════════════════════════════ */

/* Ambient panel light. Ember on the PF2e side; the Cosmere override below
   swaps it for the smooth storm-breathe (motif shared with the PC sheet's
   highstorm effect). Sits behind rows: the panel keeps its own stacking
   context and the glow is a non-interactive underlay. */
.trk .grid-wrap { position:relative; overflow:hidden; }
.trk .grid-wrap::after {
    content:""; position:absolute; top:-30%; bottom:-20%; left:-6%; width:60%;
    background:radial-gradient(ellipse at 30% 60%, rgba(232,153,58,0.55), transparent 62%);
    opacity:.06; animation: trkEmber 3.4s steps(6) infinite;
    pointer-events:none; z-index:0;
}
.trk .grid-wrap > * { position:relative; z-index:1; }
@keyframes trkEmber {
    0%{opacity:.05;} 15%{opacity:.09;} 30%{opacity:.04;} 45%{opacity:.10;}
    60%{opacity:.06;} 75%{opacity:.11;} 90%{opacity:.05;} 100%{opacity:.05;}
}
body.system-cosmere .trk .grid-wrap::after {
    left:auto; right:-10%; width:70%; top:-40%;
    background:radial-gradient(ellipse at 70% 30%, rgba(95,168,224,0.5), transparent 62%);
    opacity:.05; animation: trkStorm 9s ease-in-out infinite;
}
@keyframes trkStorm { 0%,100%{ opacity:.04; } 50%{ opacity:.11; } }

/* Portrait glyph watermark: the class rune / Radiant order glyph sits behind
   the initials. .port is already position:relative + overflow:hidden. */
.trk .port .port-glyph { position:absolute; inset:0; width:100%; height:100%; opacity:.26; }
.trk .port .port-init { position:relative; }

/* Hero Points — flame cluster (PF2e PC rows). Third flex child of .c-name. */
.trk .hero-flames { display:inline-flex; gap:4px; flex-shrink:0; align-items:center; }
.trk .hero-flame { width:12px; height:14px; position:relative; }
.trk .hero-flame svg { width:100%; height:100%; }
.trk .hero-flame.active svg { fill:#e8993a; filter:drop-shadow(0 0 4px rgba(232,153,58,.75)); animation: trkFlame 1.8s ease-in-out infinite; }
.trk .hero-flame.spent svg { fill:transparent; stroke:rgba(201,163,78,0.3); stroke-width:1.5; }
@keyframes trkFlame { 0%,100%{transform:scaleY(1) scaleX(1);} 30%{transform:scaleY(1.08) scaleX(.94);} 60%{transform:scaleY(.95) scaleX(1.05);} }

/* Investiture sphere (Cosmere PC rows). Breathes at rest; .dim when empty. */
.trk .cos-sphere-wrap { position:relative; width:20px; height:20px; flex-shrink:0; }
.trk .cos-sphere {
    width:20px; height:20px; border-radius:50%;
    border:1.5px solid var(--storm-300, #5fa8e0);
    background:radial-gradient(circle at 38% 32%, rgba(255,255,255,.6), transparent 55%),
               radial-gradient(circle, var(--storm-200, #9fd2f2) 0%, var(--storm-300, #5fa8e0) 55%, transparent 100%);
    transition: opacity .5s ease, filter .5s ease;
    animation: trkSphere 3.2s ease-in-out infinite;
}
@keyframes trkSphere { 0%,100%{ box-shadow:0 0 7px rgba(95,168,224,.55);} 50%{ box-shadow:0 0 14px rgba(159,210,242,.85);} }
.trk .cos-sphere.dim { opacity:.22; filter:grayscale(.6); animation:none; box-shadow:none; }

/* Particles. Sparks (PF2e hero-point spend) and motes (Cosmere Surge) share
   the rise mechanic; color/shape/travel differ so they never read the same.
   Nodes are spawned by JS, capped per burst, removed on animationend. */
.trk .spark, .trk .mote {
    position:absolute; left:50%; top:50%; width:3px; height:3px; border-radius:50%;
    pointer-events:none; opacity:0;
}
.trk .spark { background:#ffd79a; box-shadow:0 0 6px 1px rgba(255,180,90,.9); }
.trk .mote  { background:#e7f4ff; box-shadow:0 0 6px 1px rgba(159,210,242,.9); }
.trk .spark.rise { animation: trkSparkRise 1.1s ease-out forwards; }
.trk .mote.rise  { animation: trkMoteRise 1.3s ease-out forwards; }
@keyframes trkSparkRise {
    0%{opacity:0; transform:translate(-50%,-50%) translate(0,0) scale(1);}
    12%{opacity:1;}
    100%{opacity:0; transform:translate(-50%,-50%) translate(var(--dx,0px), -38px) scale(.3);}
}
@keyframes trkMoteRise {
    0%{opacity:0; transform:translate(-50%,-50%) translate(0,0) scale(1);}
    12%{opacity:1;}
    100%{opacity:0; transform:translate(-50%,-50%) translate(var(--dx,0px), -46px) scale(.4);}
}

/* HP ghost bar: a trailing red afterimage behind .c-hp-fill. The fill snaps
   to the new width at render; JS sets the ghost to the OLD width and then
   transitions it down after a beat. Heals skip the ghost. */
.trk .c-hp-bar { position:relative; overflow:visible; }
.trk .c-hp-ghost {
    position:absolute; left:0; top:0; bottom:0; border-radius:4px;
    background:var(--t-red-b); opacity:.55; z-index:0;
    transition: width .7s cubic-bezier(.3,0,.2,1);
}
.trk .c-hp-fill { position:relative; z-index:1; }

/* Damage / heal pop: floats up out of the HP cell and fades. */
.trk .c-hp { position:relative; }
.trk .trk-dmgpop {
    position:absolute; right:0; top:-6px; z-index:5;
    font-family:var(--font-display); font-weight:700; font-size:15px;
    color:var(--t-red-b); text-shadow:0 2px 6px rgba(0,0,0,.7);
    opacity:0; pointer-events:none;
}
.trk .trk-dmgpop.heal { color:#5fbf6a; }
.trk .trk-dmgpop.pop { animation: trkPop 1.1s ease-out forwards; }
@keyframes trkPop {
    0%{opacity:0; transform:translateY(0) scale(.7);}
    15%{opacity:1; transform:translateY(-6px) scale(1.15);}
    30%{transform:translateY(-10px) scale(1);}
    100%{opacity:0; transform:translateY(-34px) scale(1);}
}

/* Row hit feedback: one flash + shake beat on damage. Rows aren't positioned
   today (verified: .trk .combatant at tracker.html:343 has no position, and
   the row's only absolute descendant, .c-of-menu, anchors to its own
   .c-of-wrap) — so positioning the row here is required for the ::after
   flash to cover the ROW, and safe. */
.trk .combatant { position:relative; }
.trk .combatant.trk-row-hit { animation: trkShake .32s ease; }
.trk .combatant.trk-row-hit::after {
    content:""; position:absolute; inset:0; border-radius:inherit; pointer-events:none;
    background:radial-gradient(ellipse at center, rgba(200,50,50,0.26), transparent 70%);
    animation: trkHitFlash .55s ease-out forwards;
}
@keyframes trkShake {
    0%,100%{transform:translateX(0);} 20%{transform:translateX(-4px);}
    40%{transform:translateX(4px);} 60%{transform:translateX(-3px);} 80%{transform:translateX(2px);}
}
@keyframes trkHitFlash { 0%{opacity:1;} 100%{opacity:0;} }

/* Condition chip pop-in (overshoot). JS tags only NEWLY-added chips. */
.trk .cp.trk-cond-in { animation: trkCondIn .5s cubic-bezier(.2,1.4,.4,1); }
@keyframes trkCondIn { 0%{transform:scale(0); opacity:0;} 60%{transform:scale(1.25); opacity:1;} 100%{transform:scale(1);} }

/* Action-COST pips (what an ability costs — never a spent/remaining meter).
   1-3 diamonds; reaction is a ruby arc glyph slot; free is hollow. */
.trk .action-pips { display:inline-flex; gap:3px; align-items:center; vertical-align:middle; }
.trk .action-pip {
    width:9px; height:9px; transform:rotate(45deg);
    border:1.2px solid var(--t-gold); background:var(--t-gold);
    box-shadow:0 0 4px rgba(201,163,78,.45);
}
.trk .action-pip.free { background:transparent; box-shadow:none; }
.trk .action-pip.reaction {
    transform:none; border:none; background:none; box-shadow:none;
    width:auto; height:auto; color:var(--t-red-b); font-weight:700; font-size:11px; line-height:1;
}

/* Reduced motion: kill every loop, flourish, and particle in this section.
   State changes land instantly; information is never motion-only. */
@media (prefers-reduced-motion: reduce) {
    .trk .grid-wrap::after,
    .trk .hero-flame.active svg,
    .trk .cos-sphere,
    .trk .combatant.trk-row-hit,
    .trk .combatant.trk-row-hit::after,
    .trk .cp.trk-cond-in,
    .trk .trk-dmgpop.pop { animation:none; }
    .trk .trk-dmgpop { display:none; }
    .trk .c-hp-ghost { transition:none; width:0 !important; }
    .trk .spark, .trk .mote { display:none; }
}
```

- [ ] **Step 2: Sanity-check the CSS parses (no template involvement)**

Run: `python -c "css=open('static/css/system.css').read(); assert css.count('{')==css.count('}'), 'brace mismatch'; print('ok', css.count('{'))"`
Expected: `ok <n>` (balanced braces)

- [ ] **Step 3: Commit**

```bash
git add static/css/system.css
git commit -m "Combat-identity CSS: ambient ember/storm, hero flames, Investiture sphere, HP ghost + pops, hit shake, cost pips

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Row markup — watermarks, flames, sphere, ghost bar, cosmere SSE merge

**Files:**
- Modify: `templates/tracker.html:2338-2496` (`renderInitList` row template), `templates/tracker.html:2454` (portrait), `templates/tracker.html:2469` (HP bar), `templates/tracker.html:4359-4374` (SSE patch merge)

**Interfaces:**
- Consumes: sprite ids from Task 2, CSS classes from Task 3, `c.cosmere.investiture_current`/`radiant_order` from Task 1.
- Produces: helper `classRuneId(className) -> '#rn-...'` and row DOM structure (`.port-glyph`, `.hero-flames` with one `.hero-flame` per point up to 3, `.cos-sphere-wrap`, `.c-hp-ghost`) that Task 5's flourish layer queries by class name. Hero flames cluster renders for `c.is_pc && c.system !== 'cosmere'`; sphere for `c.is_pc && c.system === 'cosmere'`.

- [ ] **Step 1: Add the helpers (near `hpBarClass`, tracker.html:~2210)**

```javascript
// Class -> carved-rune sprite id (see _pf2e_class_glyphs.html). Key on the
// first word lowercased so "Cleric (Warpriest)" and "Champion" both map.
const CLASS_RUNES = { champion:'rn-champion', kineticist:'rn-kineticist',
                      cleric:'rn-cleric', druid:'rn-druid' };
function classRuneId(className) {
    const key = String(className || '').trim().toLowerCase().split(/[\s(]/)[0];
    return '#' + (CLASS_RUNES[key] || 'rn-adventurer');
}

// Hero Point flame cluster (PF2e PCs, cap 3). Purely display — award/spend
// stays on party view / the inspector; the row just reflects state.
function heroFlamesHtml(c) {
    const pts = Math.max(0, Math.min(3, c.hero_points || 0));
    if (!c.is_pc || c.system === 'cosmere') return '';
    let out = '<div class="hero-flames" title="Hero Points: ' + pts + '">';
    for (let i = 0; i < 3; i++) {
        out += '<span class="hero-flame ' + (i < pts ? 'active' : 'spent') + '">' +
               '<svg viewBox="0 0 14 16"><use href="#rn-flame"></use></svg></span>';
    }
    return out + '</div>';
}

// Investiture sphere (Cosmere PCs). Dim when empty.
function investitureSphereHtml(c) {
    if (!c.is_pc || c.system !== 'cosmere' || !c.cosmere) return '';
    const cur = c.cosmere.investiture_current;
    const mx = c.cosmere.investiture_max || 0;
    const dim = (typeof cur === 'number' ? cur : mx) <= 0;
    return '<div class="cos-sphere-wrap" title="Investiture: ' +
           (typeof cur === 'number' ? cur : mx) + ' / ' + mx + '">' +
           '<div class="cos-sphere' + (dim ? ' dim' : '') + '"></div></div>';
}

// Action-COST pip cluster from a cost string ('1','2','3','r','f', or the
// glyph forms). Returns '' for unknown so rows never show a wrong cost.
function costPips(cost) {
    const s = String(cost == null ? '' : cost).trim().toLowerCase();
    if (s === 'r' || s === 'reaction' || s.indexOf('⟳') >= 0)
        return '<span class="action-pips"><span class="action-pip reaction">⟳</span></span>';
    if (s === 'f' || s === 'free' || s.indexOf('◈') >= 0 || s.indexOf('◇') >= 0)
        return '<span class="action-pips"><span class="action-pip free"></span></span>';
    const n = (s.match(/◆/g) || []).length || parseInt(s, 10);
    if (!(n >= 1 && n <= 3)) return '';
    let out = '<span class="action-pips">';
    for (let i = 0; i < n; i++) out += '<span class="action-pip"></span>';
    return out + '</span>';
}
```

- [ ] **Step 2: Portrait watermark (tracker.html:2454)**

Replace:
```javascript
                <div class="port ${portCls}">${initials(c.name)}</div>
```
with:
```javascript
                <div class="port ${portCls}">${
                    (c.is_pc && c.system !== 'cosmere' && c.class_name)
                        ? `<svg class="port-glyph" viewBox="0 0 24 24"><use href="${classRuneId(c.class_name)}"></use></svg>`
                    : (c.is_pc && c.system === 'cosmere' && c.cosmere && c.cosmere.radiant_order)
                        ? `<svg class="port-glyph" viewBox="0 0 24 24"><use href="#cg-order-${c.cosmere.radiant_order}"></use></svg>`
                    : ''
                }<span class="port-init">${initials(c.name)}</span></div>
```

- [ ] **Step 3: Resource cluster as third flex child of `.c-name` (after the `nm-wrap` closing `</div>`, tracker.html:2461)**

Directly after the `nm-wrap` div closes, inside `.c-name`:
```javascript
                ${heroFlamesHtml(c)}${investitureSphereHtml(c)}
```

- [ ] **Step 4: Ghost bar element (tracker.html:2469)**

Replace:
```javascript
                <div class="c-hp-bar"><div class="c-hp-fill ${hpBarClass(hpPct, c.is_pc)}" style="width:${hpPct}%"></div></div>
```
with:
```javascript
                <div class="c-hp-bar"><div class="c-hp-ghost" style="width:0"></div><div class="c-hp-fill ${hpBarClass(hpPct, c.is_pc)}" style="width:${hpPct}%"></div></div>
```

- [ ] **Step 5: Merge the cosmere block in the SSE patch path (tracker.html:4373, after the `visible_to_players` line)**

```javascript
                if (src.cosmere && typeof src.cosmere === 'object') dst.cosmere = src.cosmere;
```

- [ ] **Step 6: Verify**

Run: `python tools/check_templates.py`
Expected: exit 0

Run the dev server (`DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 FLASK_DEBUG=true python app.py`), open `/tracker`, add a PF2e party PC + a monster. Confirm: rune watermark behind the PC initial, flame cluster next to the name (points reflect state), monster row unchanged apart from ambient glow. No console errors.

- [ ] **Step 7: Commit**

```bash
git add templates/tracker.html
git commit -m "Tracker rows: class-rune/order-glyph watermarks, hero-flame cluster, Investiture sphere, ghost-bar slot, cosmere SSE merge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Flourish trigger layer — diff vitals across renders, animate the deltas

**Files:**
- Modify: `templates/tracker.html` — add the flourish module near `connectSSE` (~line 4300), and one call at the end of `_renderNow()` (tracker.html:2108)

**Interfaces:**
- Consumes: DOM structure from Task 4 (`.combatant[data-id]`, `.c-hp-ghost`, `.c-hp`, `.cp[data-cond-name]`, `.hero-flame`, `.cos-sphere-wrap`), CSS classes from Task 3.
- Produces: `applyCombatFlourishes()` — call it as the LAST line of `_renderNow()`. Also `window.__trkFlourishReady` (bool) for the manual-verification step.

- [ ] **Step 1: Add the flourish module (self-contained block, place before `connectSSE()`)**

```javascript
/* ── Combat flourishes ─────────────────────────────────────────────────
   renderInitList() rebuilds rows wholesale (innerHTML), so CSS transitions
   can't observe changes. Instead: keep a vitals snapshot per instance_id,
   diff it against STATE after every render, and animate the deltas onto
   the freshly-built nodes. First render only seeds the snapshot (no
   flourish storm on page load); combatants added mid-fight seed silently
   the same way. Reduced-motion users get none of it. */
const _REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)');
let _prevVitals = null;   // Map<instance_id, {hp, max, conds:Set, hero, inv}>

function _vitalsOf(c) {
    return {
        hp: c.current_hp, max: c.max_hp,
        conds: new Set(Object.entries(c.conditions || {})
            .filter(([, v]) => v && v !== 0 && v !== false).map(([k]) => k)),
        hero: (typeof c.hero_points === 'number') ? c.hero_points : null,
        inv: (c.cosmere && typeof c.cosmere.investiture_current === 'number')
            ? c.cosmere.investiture_current : null,
    };
}

/* Burst up to n particles (spark|mote) from el. Nodes remove themselves. */
function spawnParticles(el, className, n) {
    if (!el || (_REDUCED_MOTION && _REDUCED_MOTION.matches)) return;
    const count = Math.min(6, n || 5);
    for (let i = 0; i < count; i++) {
        const p = document.createElement('div');
        p.className = className;
        p.style.setProperty('--dx', (Math.random() * 28 - 14).toFixed(1) + 'px');
        el.appendChild(p);
        setTimeout(() => p.classList.add('rise'), i * 55);
        p.addEventListener('animationend', () => p.remove());
        setTimeout(() => p.remove(), 2400);   // backstop if animationend never fires
    }
}

function _popDamage(row, delta) {
    if (_REDUCED_MOTION && _REDUCED_MOTION.matches) return;
    const cell = row.querySelector('.c-hp');
    if (!cell) return;
    const pop = document.createElement('div');
    pop.className = 'trk-dmgpop' + (delta > 0 ? ' heal' : '');
    pop.textContent = (delta > 0 ? '+' : '') + delta;
    cell.appendChild(pop);
    void pop.offsetWidth;
    pop.classList.add('pop');
    pop.addEventListener('animationend', () => pop.remove());
    setTimeout(() => pop.remove(), 2400);
}

function _ghostDrain(row, oldPct, newPct) {
    if (_REDUCED_MOTION && _REDUCED_MOTION.matches) return;
    const ghost = row.querySelector('.c-hp-ghost');
    if (!ghost) return;
    ghost.style.transition = 'none';
    ghost.style.width = oldPct + '%';
    void ghost.offsetWidth;
    ghost.style.transition = '';
    setTimeout(() => { ghost.style.width = newPct + '%'; }, 120);
}

function applyCombatFlourishes() {
    const cs = (STATE && STATE.combatants) || [];
    const next = new Map();
    cs.forEach(c => next.set(c.instance_id, _vitalsOf(c)));
    const prev = _prevVitals;
    _prevVitals = next;
    if (!prev) return;                         // first render: seed only
    if (_REDUCED_MOTION && _REDUCED_MOTION.matches) return;

    cs.forEach(c => {
        const was = prev.get(c.instance_id);
        if (!was) return;                      // newly added: seed silently
        const now = next.get(c.instance_id);
        const row = document.querySelector('.combatant[data-id="' + c.instance_id + '"]');
        if (!row) return;

        // HP delta — skip when max changed too (elite/weak adjustment, not a hit).
        if (typeof was.hp === 'number' && typeof now.hp === 'number'
            && now.hp !== was.hp && was.max === now.max) {
            const delta = now.hp - was.hp;
            const oldPct = was.max > 0 ? Math.round(was.hp / was.max * 100) : 0;
            const newPct = now.max > 0 ? Math.round(now.hp / now.max * 100) : 0;
            _popDamage(row, delta);
            if (delta < 0) {
                _ghostDrain(row, oldPct, newPct);
                row.classList.remove('trk-row-hit');
                void row.offsetWidth;
                row.classList.add('trk-row-hit');
                row.addEventListener('animationend',
                    () => row.classList.remove('trk-row-hit'), { once: true });
            }
        }

        // New conditions pulse in (only chips that weren't there before).
        now.conds.forEach(k => {
            if (was.conds.has(k)) return;
            const chip = row.querySelector('.cp[data-cond-name="' + k.replace(/_/g, '-') + '"]');
            if (chip) chip.classList.add('trk-cond-in');
        });

        // Hero Point spent: sparks from the flame that just went out.
        if (was.hero !== null && now.hero !== null && now.hero < was.hero) {
            const flames = row.querySelectorAll('.hero-flame');
            const snuffed = flames[Math.max(0, Math.min(flames.length - 1, now.hero))];
            spawnParticles(snuffed, 'spark', 5);
        }

        // Investiture spent: light motes off the sphere.
        if (was.inv !== null && now.inv !== null && now.inv < was.inv) {
            spawnParticles(row.querySelector('.cos-sphere-wrap'), 'mote', 5);
        }
    });
}
window.__trkFlourishReady = true;
```

- [ ] **Step 2: Hook it into `_renderNow()` (tracker.html:2108)**

Add as the last line of `_renderNow()`, after `syncMultiDamageTypes();`:

```javascript
    applyCombatFlourishes();
```

- [ ] **Step 3: Verify templates parse**

Run: `python tools/check_templates.py`
Expected: exit 0

- [ ] **Step 4: Manual verification (dev server)**

With the dev server running and a PF2e encounter staged:
1. Deal damage to a PC from the tracker → HP fill snaps down, red ghost trails after ~120ms, `-N` pops and floats, row flashes + shakes once.
2. Heal → green `+N` pop, no shake, no ghost.
3. Apply a condition → its chip (and only it) pops in with overshoot.
4. Toggle Elite on a monster → HP changes but NO pop/shake (max changed).
5. Award then spend a Hero Point from party view → row flames update via SSE; the snuffed flame sheds sparks.
6. In DevTools, emulate reduced motion (Rendering panel) → damage lands instantly, no pops/particles/shake.
7. Console: `window.__trkFlourishReady` is `true`; no errors.

- [ ] **Step 5: Full test suite**

Run: `pytest -q`
Expected: PASS (or only pre-existing skips for absent `party_data/`)

- [ ] **Step 6: Commit**

```bash
git add templates/tracker.html
git commit -m "Tracker flourish layer: vitals diff across renders drives HP ghost/pops, hit shake, condition pulse, hero sparks, Investiture motes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Action-cost pips in the inspector

**Files:**
- Modify: `templates/tracker.html:2917-2930` (inspector spell list)

**Interfaces:**
- Consumes: `costPips(cost)` from Task 4; spell entries `{name, actions}` already in the payload.

- [ ] **Step 1: Render pips next to spell names**

At tracker.html:2924 the inspector builds spell names with:
```javascript
                const spells = (lvl.spells || []).map(s => s.name).filter(Boolean);
```
Downstream (line 2932) these interpolate raw into HTML via `${spells.join(', ')}` — no escaping is applied (verified), so markup appended to each name renders as markup. Change the map to:
```javascript
                const spells = (lvl.spells || [])
                    .filter(s => s.name)
                    .map(s => s.name + (costPips(s.actions) ? ' ' + costPips(s.actions) : ''));
```
No other changes to the join or surrounding structure. (`costPips` builds markup only from its own fixed strings — spell names pass through exactly as before, so this adds no new injection surface to the pre-existing unescaped interpolation.)

- [ ] **Step 2: Verify**

Run: `python tools/check_templates.py`
Expected: exit 0

Dev server: inspect a caster PC (Go'el — note the apostrophe; the row and inspector must render and stay clickable). Spells show gold diamond pips matching their cost; unknown costs show no pips rather than wrong ones.

- [ ] **Step 3: Commit**

```bash
git add templates/tracker.html
git commit -m "Inspector: diamond action-cost pips on spell rows

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Cross-system verification pass

**Files:** none created — verification only (fix-forward anything found, amending the relevant task's commit style).

- [ ] **Step 1: Full suite + template check**

Run: `pytest -q && python tools/check_templates.py`
Expected: PASS / exit 0

- [ ] **Step 2: PF2e visual pass (dev server)**

Stage a PF2e encounter (PC with apostrophe name + monster). Verify: ember ambient on the grid panel (subtle, irregular), rune watermark, flames, all Task 5 flourishes, cost pips in inspector. Verify text legibility is unchanged (no flourish overlaps row text).

- [ ] **Step 3: Cosmere visual pass**

Seed a Cosmere PC (with `radiant_order` and partial investiture in play_state), add to tracker. Verify: storm-breathe ambient (smooth, cool, right side), order-glyph watermark, sphere reflects current investiture and dims at 0, sheet-side investiture spend sends motes via SSE, damage flourishes identical in mechanics but storm-palette in color.

- [ ] **Step 4: Convergence + reduced-motion audit**

Side-by-side check: PF2e row and Cosmere row must read as different games (warm/irregular vs cool/smooth). Then emulate reduced motion and confirm every animation in the new section is inert on both.

- [ ] **Step 5: Commit any verification fixes; then done**

PR is cut from this branch after user review (user decides when; never push unprompted). Post-merge: verify on Railway per working agreement.
