# Chronicle Surface Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:test-driven-development to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Chronicle from normal website discovery and rendering while
preserving all stored data and bookmarked behavior.

**Architecture:** Delete only shared-surface integrations. Player navigation
returns to its existing Notes branch unconditionally, and normal template
rendering no longer probes Chronicle storage. The isolated Chronicle subsystem
remains intact and unlinked for compatibility and recovery.

**Tech Stack:** Flask, Jinja2, pytest.

**Spec:**
`docs/superpowers/specs/2026-10-07-chronicle-surface-retirement-design.md`

## Global Constraints

- Do not delete or migrate any Chronicle data.
- Do not change Chronicle routes, APIs, authorization, export, or handouts.
- Do not add a replacement tile, link, layout, or visual design.
- Use strict RED-GREEN TDD for each behavior change.

## Review Focus

- A published Chronicle must not replace Notes in either player system.
- GM pages must not perform hidden Chronicle filesystem reads after links go.
- Removing the GM Hub tile must not disturb the remaining grid or statistics.
- Seeded Chronicle files must remain byte-identical.
- Saved Chronicle URLs must retain their current response behavior.

---

### Task 1: Retire shared discovery and restore Notes

**Files:**
- Modify: `tests/test_chronicle_reading.py`
- Modify: `tests/test_nav_cleanup.py`
- Modify: `templates/base.html`
- Modify: `templates/gm_hub.html`
- Modify: `templates/_player_nav.html`
- Modify: `templates/_cosmere_player_nav.html`
- Modify: `templates/_pc_sheet/_tab_inventory.html`

**Interfaces:**
- Consumes: existing navigation partial contexts and GM Hub route.
- Produces: identical unconditional Notes markup in both player navs and no
  Chronicle discovery markup on shared GM surfaces.

- [ ] **Step 1: Write failing navigation behavior tests**

Change the existing published/unpublished assertions to require Notes in both
states and add GM surface assertions scoped to navigation and hub tiles.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q tests/test_chronicle_reading.py tests/test_nav_cleanup.py`

Expected: failures identify the still-present Chronicle links and conditional
player swap.

- [ ] **Step 3: Remove discovery markup and make Notes unconditional**

Delete the Chronicle GM link/tile, collapse both player conditionals to their
existing Notes branch, and change inventory empty-state copy to `No session
entries yet.`

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest -q tests/test_chronicle_reading.py tests/test_nav_cleanup.py`

Expected: all tests pass.

### Task 2: Remove normal-page Chronicle I/O

**Files:**
- Modify: `tests/test_nav_cleanup.py`
- Modify: `app.py`

**Interfaces:**
- Consumes: `/gm`, shared template rendering, and isolated Chronicle helpers.
- Produces: normal pages that never call `_chronicle_content_dir`,
  `_chronicle_doc_pages`, or `_chronicle_docs_index`.

- [ ] **Step 1: Write failing behavior tests**

Patch the three Chronicle readers to raise and prove representative GM and
player pages still render. Assert the GM Hub template receives no Chronicle
count values.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `pytest -q tests/test_nav_cleanup.py`

Expected: normal rendering raises from the existing context processor or GM
Hub count lookup.

- [ ] **Step 3: Remove the context processor and GM Hub count reads**

Delete `_inject_chronicle_ctx`, the Hub index load, and the two Chronicle
template arguments. Leave all isolated Chronicle routes/helpers unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest -q tests/test_nav_cleanup.py`

Expected: all tests pass.

### Task 3: Data-preservation and regression gate

**Files:**
- Modify: `tests/test_nav_cleanup.py`
- Create: `docs/remediation/chronicle-surface-retirement.md`

**Interfaces:**
- Consumes: seeded Chronicle directories and existing bookmarked routes.
- Produces: executable proof that ordinary navigation is non-mutating and the
  compatibility boundary remains live.

- [ ] **Step 1: Add preservation and bookmark tests**

Hash sentinel Chronicle files before/after normal GM/player requests and
assert representative Chronicle bookmarks retain their prior non-404 behavior.

- [ ] **Step 2: Run focused retirement and Chronicle suites**

Run: `pytest -q tests/test_nav_cleanup.py tests/test_chronicle_reading.py`

Expected: all tests pass.

- [ ] **Step 3: Run repository gates**

Run the template parser, non-browser regression suite, browser suite, and
`git diff --check` using the repository's documented commands.

- [ ] **Step 4: Review and ship**

Inspect the complete diff, run an independent whole-branch review, then commit,
push, open a PR, wait for CI, and merge only if every required check passes.
