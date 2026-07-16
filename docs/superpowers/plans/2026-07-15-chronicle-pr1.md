# Chronicle PR1 (MVP Trunk) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Chronicle player-hub MVP trunk — a GM-published, spoiler-safe, session-aware reading hub (Home, Story, Lore, Cast, Handouts, Journal) served server-side from a Railway volume, gated behind a first-publish empty state, working for PF2e and Cosmere.

**Architecture:** A GM POSTs a player-vault zip to `/api/chronicle/publish`; the server validates + re-scans for spoiler markers, renders each page's markdown to a sanitized HTML fragment with the already-present python-markdown, and atomically repoints a `current` symlink at the new content dir (a directory `os.replace` is not atomic). Player `/chronicle*` GET routes are file reads + Jinja behind a new player-scope gate, with per-page recipient filtering keyed on account ownership. All live behavior stays on the existing `appSSE` hub. Nothing renders per-request markdown; the single gevent worker is never blocked.

**Tech Stack:** Flask (monolith `app.py`), server-rendered Jinja + vanilla JS (no build step), `python-markdown==3.5.1` (already a dependency), gevent (1 worker), Railway volume storage, pytest.

## Global Constraints (every task inherits these — values copied verbatim from CHRONICLE_DESIGN.md)

- **Reuse python-markdown** (`markdown==3.5.1`, already imported at `app.py:11`, currently a dead import). Do NOT add mistune/markdown-it-py or any markdown/sanitizer/image library (`bleach`, `Pillow` are NOT in requirements).
- **Directory `os.replace` is NOT atomic** (`ENOTEMPTY`). Publishing swaps a `current` symlink; see `core/storage.py:124` (`trash_campaign_dir`) for the repo's own dir-move precedent.
- **Never block the gevent worker** (`Procfile`: `--workers 1 --worker-class gevent --timeout 120`): stream uploads to a temp file (never `io.BytesIO(f.read())`), render in bounded batches yielding with `gevent.sleep(0)`.
- **Volume-only persistence**, dual-bound paths (campaign branch + flat `DATA_DIR` fallback); `_atomic_write_json` (`app.py:166`) for JSON state, `fsync=False` for low-stakes reader state.
- **GM API is prefix-gated**: add `'/api/chronicle'` to `GM_API_PREFIXES` (`app.py:443`); do NOT add `@gm_required` (no double-flag). Player `/chronicle*` routes use a new `before_request` player-scope gate — a different path prefix, no overlap.
- **Per-player secrets key on account ownership** (`owner_user_id` via `_user_owns_pc`, `app.py:426`), never the self-asserted `session['player_name']`. Legacy-open mode: any non-`all` recipient is GM-only.
- **No inline `onclick`** in `chronicle*.html` — `data-*` + `addEventListener` only; extend `tests/test_inline_handler_escaping.py`.
- **No emojis** anywhere (code, UI, comments, commits). **Warm-dark only** — no light/parchment reading pane (removed 2026-05 as vibe-coded). Tokens from `static/css/system.css`.
- **Commit trailer** on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Branch is `claude/player-campaign-hub-4d7448`; do not commit to `main`. CI gate per task cluster: `pytest -q` + `python tools/check_templates.py`.
- **Legacy-open dev run:** `DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 FLASK_DEBUG=true python app.py`.

---

## Cross-Slice Reconciliation Contract (AUTHORITATIVE — resolves conflicts between parallel-drafted parts)

The five parts were drafted in parallel. Where they disagreed, THIS section is the tie-breaker. Read it before implementing any part.

**1. Execution order:** Part 1 (Storage) -> Part 2 (Publish) -> Part 3 (Auth) -> Part 4 (Templates/CSS) -> Part 5 (Routes/Nav/Context). Part 4 templates are static (testable alone); Part 5 routes render them and are tested by seeding content on disk (no publish needed).

**2. CSS class prefix is `.chron-*`** (matches the spec §6 and the mockup). The earlier `.chr-*` naming is dropped. Part 4 owns all `.chron-*` classes; Part 5 templates reference only `.chron-*`.

**3. Template + CSS + nav ownership (de-dup):**
   - **Part 4 owns:** `templates/chronicle_base.html` + the six screen templates (`chronicle_home/story/lore/cast/handouts/journal/page.html`), the `.chron-*` block in `system.css`, the `{% block head_extra %}` seam in `base.html` (to load Alegreya — `base.html` ships only Inter+Cinzel), and the `chronicle*.html` inline-handler ban test.
   - **Part 5 owns:** all routes, reader helpers, the `chronicle_published` context processor, and the **nav-tab swap** (Notes -> Chronicle) in `_player_nav.html` + `_cosmere_player_nav.html`.
   - Part 5 does NOT create templates or CSS; Part 4 does NOT edit the nav or add routes.

**4. Nav-gate flag is `chronicle_published`** (bool), produced by a Part-5 context processor = `_chronicle_manifest() is not None`. (Drops the routes draft's alternate `chronicle_available` name.)

**5. Empty-state / Notes access:** the nav shows the **Chronicle** tab when `chronicle_published` is true, and the **Notes** tab when it is false — so players never lose their private notes before the first publish. (Corrects the routes draft, which removed Notes unconditionally.) `/chronicle/journal` is the folded-in Notes surface once published.

**6. Slug normalization (publish<->routes seam):** every `manifest.pages[].slug` MUST already match `^[a-z0-9][a-z0-9-]{0,80}$`. Part 2's `_chronicle_validate_manifest` REJECTS (400) any page whose slug does not. Part 2 writes the fragment to `html/<slug>.html` (slug used verbatim, since it is already safe); Part 5 reads `html/<slug>.html` by the same key. (`_chronicle_safe_slug` is a defensive normalizer only; with a valid manifest it is the identity.)

**7. Template context contract (Part 5 routes -> Part 4 templates).** Every page dict exposes the §3.3 frontmatter fields plus two route-computed fields: `portrait_url` (from `chronicle_asset_url(page.portrait)` or None) and `recipient_label` (comma-joined recipients when not `all`, else None). Per screen, Part 5 passes:
   - Home: `manifest`, `latest_recap` (a page dict + `.html` = its rendered fragment + optional `.pull_quote`), `party` (list of `{name, tagline, portrait_url}`), `nav`.
   - Story: `recaps` (list of `{title, session_number, html, chapter}`), `nav`.
   - Lore/Cast/Handouts: `pages` (or `handouts` = list of `{title, html, image_url, recipient_label}`), `nav`.
   - Page detail: `page`, `page_html` (the fragment), `backlinks` (list of `{slug, title}`), `nav`.
   - `nav` is a dict of visible-page counts per section: `{story, lore, cast, handouts}` — Part 4's sub-tab strip shows a tab only when its count > 0.
   - `is_gm` and `chronicle_published` are injected globally (context processors).

**8. `_user_owns_pc` is PF2e-party-dir-only** (`app.py:426`, does not scan `cosmere_pc_dir`). Part 3's `_chronicle_owned_pc_slugs` scans BOTH dirs, so Chronicle *pages* work for Cosmere owners; live *handout* ownership for a Cosmere PC is a known gap tracked as an open decision (see the close of this plan), not blocking for PR1.

**9. Required amendments to the Part 4 draft (apply while building Part 4):**
   - **Empty-state body guard.** `chronicle_base.html`'s body renders the screen block only when `manifest` is truthy, else a `.chron-empty` "The chronicle opens after your first session." — so every route degrades gracefully and screen templates never dereference a None manifest:
     ```jinja
     <main class="chron-body">
       {% if manifest %}{% block chronicle %}{% endblock %}
       {% else %}<div class="chron-empty"><p>The chronicle opens after your first session.</p></div>{% endif %}
     </main>
     ```
   - **Journal template.** `chronicle_journal.html` **server-renders** the passed `notes` into the textarea (`<textarea id="chron-journal">{{ notes }}</textarea>`) and autosaves via `POST /api/notes` with a JSON `{text}` body (the existing per-owner store Part 5's route reads via `_load_notes_text`) — NOT `/api/journal`. Use `addEventListener` only (no inline handlers).
   - **Drop the Part 4 nav-swap task** (Task 6 in the draft) and its `test_nav_swaps_notes_for_gated_chronicle_tab` — the nav swap is owned by Part 5 (contract §3). Everything else in the Part 4 draft stands.

---


---

## Part 1: Storage Layer (symlink swap, dual-bind, rollback)

## 1. Grounding (exact real code this slice builds against)

**`core/storage.py` — per-feature path helpers (verified, lines 71–91).** Every per-campaign path is a one-liner `os.path.join(campaign_dir(cid), '<name>')`, where `campaign_dir(cid)` validates the id through `_check_id` (regex `^[0-9a-f]{32}$`, raises `ValueError` on traversal). Example real lines:
```python
def campaign_dir(cid):
    return os.path.join(CAMPAIGNS_DIR, _check_id(cid, 'campaign_id'))
def journal_dir(cid):  return os.path.join(campaign_dir(cid), 'journals')
def handouts_dir(cid): return os.path.join(campaign_dir(cid), 'uploads', 'handouts')
```
`chronicle_dir(cid)` mirrors these exactly. `shutil` and `os` are already imported in `core/storage.py` (lines 26–31).

**The dir-move / non-atomic-replace precedent (`trash_campaign_dir`, lines 116–126):** the repo already proves a directory `os.replace` is unsafe — it uses `shutil.rmtree(dst)` then `shutil.move(src, dst)` to replace an existing dir. Our swap mirrors this but uses a **symlink repoint** for atomicity (design C2).

**`app.py` DATA_DIR (line 584):** `DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)`.

**`app.py` `_bind_campaign_paths` (lines 606–649) — the ACTUAL structure I insert into.** Globals are declared `None` at module scope (lines 600–603), then `global`-declared and assigned inside `_bind_campaign_paths` in **two branches** keyed on `if cid:` (campaign, uses `_storage.<helper>(cid)` + `_storage.ensure_campaign_dirs(cid)`) `else:` (flat, uses `os.path.join(DATA_DIR, ...)`). Module tail line 662 calls `_bind_campaign_paths(_storage.get_live_campaign_id())` at import. `load_campaign` (652) re-binds on switch.

**`_atomic_write_json` (166–199)** and **`_active_campaign_id` (226–254)** confirmed; not modified by this slice (consumed by other slices). `_user_owns_pc` (426) and `GM_API_PREFIXES` (443–490) confirmed; consumed by the auth slice, not this one.

**`import shutil` is NOT present in `app.py`** (`grep -nc shutil app.py` → 0). This slice adds it (the swap/rollback and the publish slice both need it).

**Test conventions.** `tests/conftest.py` puts repo root on `sys.path` so `import app` works in-process; importing `app` executes top-level wiring and binds paths to `DATA_DIR` at import. Binding-oriented tests (`tests/test_cosmere_campaign_binding.py`) set `DATA_DIR`/`GM_PASSWORD` to a throwaway before importing app. This slice's app-global helpers are pure filesystem ops parameterized by the `CHRONICLE_DIR` global, so they are tested in-process by `monkeypatch.setattr(app, 'CHRONICLE_DIR', <tmp>)` — no server, no subprocess needed.

## 2. Files

**Create:**
- `tests/test_chronicle_storage.py` — unit tests for `chronicle_dir` + all app-global chronicle helpers.

**Modify:**
- `core/storage.py` — add `chronicle_dir(cid)` beside the per-feature helpers (after line 90, before `homebrew_file`'s block ends / line 91).
- `app.py`:
  - line 16 area — add `import shutil`.
  - lines 600–603 — add `CHRONICLE_DIR` to the `None`-init tuple.
  - lines 610–613 — add `global CHRONICLE_DIR`.
  - line ~616 (campaign branch) — `CHRONICLE_DIR = _storage.chronicle_dir(cid)`.
  - line ~634 (flat branch) — `CHRONICLE_DIR = os.path.join(DATA_DIR, 'chronicle')`.
  - after line 649 (end of `_bind_campaign_paths` else-branch), before `def load_campaign` (652) — the four helpers `_chronicle_content_dir`, `_chronicle_manifest`, `_chronicle_swap`, `_chronicle_rollback`.

---

## 3. Ordered TDD tasks

### Task 1: `core/storage.py::chronicle_dir(cid)`

**Files:** Modify `core/storage.py`; Test `tests/test_chronicle_storage.py`
**Interfaces:** Produces `chronicle_dir(campaign_id) -> str`

- [ ] **Write the failing test.** Create `tests/test_chronicle_storage.py` with the pure-storage tests first:
```python
import os
import json
import tempfile

# Bind app's DATA_DIR to a throwaway BEFORE any `import app` (mirrors
# tests/test_cosmere_campaign_binding.py); harmless for the pure-storage tests.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='chron-data-'))
os.environ.setdefault('GM_PASSWORD', '')

import pytest
from core import storage


def test_chronicle_dir_is_under_campaign():
    cid = storage.new_id()
    assert storage.chronicle_dir(cid) == os.path.join(storage.campaign_dir(cid), 'chronicle')


def test_chronicle_dir_rejects_traversal_id():
    with pytest.raises(ValueError):
        storage.chronicle_dir('../escape')
```

- [ ] **Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_storage.py::test_chronicle_dir_is_under_campaign`
  Expected: `AttributeError: module 'core.storage' has no attribute 'chronicle_dir'`.

- [ ] **Minimal implementation.** In `core/storage.py`, add the helper in the per-feature block. Insert immediately after the `homebrew_file` line (line 90):
```python
def homebrew_file(cid):            return os.path.join(campaign_dir(cid), 'homebrew.json')
def chronicle_dir(cid):            return os.path.join(campaign_dir(cid), 'chronicle')
```
(`campaign_dir` already validates `cid` via `_check_id`, so traversal ids raise `ValueError` for free — that is what the second test asserts.)

- [ ] **Run, expect PASS.** `pytest -q tests/test_chronicle_storage.py`

- [ ] **Commit.**
  `git commit -am "Chronicle: add chronicle_dir(cid) storage helper"` with trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

---

### Task 2: `CHRONICLE_DIR` global, dual-bound in `_bind_campaign_paths`

**Files:** Modify `app.py`; Test `tests/test_chronicle_storage.py`
**Interfaces:** Produces module global `app.CHRONICLE_DIR`; Consumes `storage.chronicle_dir(cid)`

- [ ] **Write the failing test.** Append to `tests/test_chronicle_storage.py`:
```python
import app as A  # top-level import; DATA_DIR already pinned above


def test_chronicle_dir_binds_campaign_branch():
    cid = storage.new_id()
    A._bind_campaign_paths(cid)
    assert A.CHRONICLE_DIR == storage.chronicle_dir(cid)


def test_chronicle_dir_binds_flat_fallback():
    A._bind_campaign_paths(None)          # legacy-open dev mode
    assert A.CHRONICLE_DIR == os.path.join(A.DATA_DIR, 'chronicle')
```

- [ ] **Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_storage.py::test_chronicle_dir_binds_flat_fallback`
  Expected: `AttributeError: module 'app' has no attribute 'CHRONICLE_DIR'`.

- [ ] **Minimal implementation.** Four edits in `app.py`.

  (a) Add the import near the other stdlib imports (after line 16 `import tempfile`):
```python
import tempfile
import shutil
```

  (b) Module-scope `None`-init — extend line 603:
```python
HANDOUTS_FILE = COSMERE_ADVERSARIES_FILE = None
CHRONICLE_DIR = None
```

  (c) `global` declaration inside `_bind_campaign_paths` — extend the block at lines 610–613:
```python
    global COSMERE_PC_DIR, COSMERE_HOMEBREW_FILE, HANDOUTS_FILE, COSMERE_ADVERSARIES_FILE
    global CHRONICLE_DIR
```

  (d) Both branches. In the `if cid:` branch, after line 631 (`COSMERE_ADVERSARIES_FILE = _storage.cosmere_adversaries_file(cid)`), before `_storage.ensure_campaign_dirs(cid)`:
```python
        COSMERE_ADVERSARIES_FILE = _storage.cosmere_adversaries_file(cid)
        CHRONICLE_DIR = _storage.chronicle_dir(cid)
        _storage.ensure_campaign_dirs(cid)
```
  In the `else:` branch, after line 649 (`COSMERE_ADVERSARIES_FILE = os.path.join(DATA_DIR, 'cosmere_adversaries.json')`):
```python
        COSMERE_ADVERSARIES_FILE = os.path.join(DATA_DIR, 'cosmere_adversaries.json')
        CHRONICLE_DIR = os.path.join(DATA_DIR, 'chronicle')
```

- [ ] **Run, expect PASS.** `pytest -q tests/test_chronicle_storage.py`

- [ ] **Commit.**
  `git commit -am "Chronicle: dual-bind CHRONICLE_DIR global in _bind_campaign_paths"` (+ trailer)

---

### Task 3: `_chronicle_content_dir()` + `_chronicle_manifest()` (empty-state resolve)

**Files:** Modify `app.py`; Test `tests/test_chronicle_storage.py`
**Interfaces:** Produces `_chronicle_content_dir() -> str | None`, `_chronicle_manifest() -> dict | None`

- [ ] **Write the failing test.** Append the shared fixture + these tests:
```python
def _stage_content(chron_dir, h, session=1):
    """Build a fake, fully-rendered content tree under .staging/<h> and return it."""
    d = os.path.join(chron_dir, '.staging', h)
    os.makedirs(os.path.join(d, 'html'), exist_ok=True)
    with open(os.path.join(d, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'schema_version': 1, 'session_number': session, 'pages': []}, f)
    return d


@pytest.fixture
def chron(tmp_path, monkeypatch):
    cd = str(tmp_path / 'chronicle')
    os.makedirs(cd, exist_ok=True)
    monkeypatch.setattr(A, 'CHRONICLE_DIR', cd)
    return cd


def test_content_dir_is_none_before_first_publish(chron):
    assert A._chronicle_content_dir() is None
    assert A._chronicle_manifest() is None
```

- [ ] **Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_storage.py::test_content_dir_is_none_before_first_publish`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_content_dir'`.

- [ ] **Minimal implementation.** Insert in `app.py` immediately after `_bind_campaign_paths` ends (after line 649's else-block), before `def load_campaign` (line 652):
```python
# --- Chronicle content resolution (empty-state gate keys on None) -----------
def _chronicle_content_dir():
    """Absolute path to the currently-published chronicle content dir, or None
    if nothing has been published yet. Resolves the `current` symlink under
    CHRONICLE_DIR; the empty-state nav gate keys on the None return."""
    if not CHRONICLE_DIR:
        return None
    target = os.path.realpath(os.path.join(CHRONICLE_DIR, 'current'))
    if os.path.isdir(target) and os.path.isfile(os.path.join(target, 'manifest.json')):
        return target
    return None


def _chronicle_manifest():
    """Load the live publish's manifest.json, or None if nothing is published."""
    content = _chronicle_content_dir()
    if not content:
        return None
    return _storage.load_json(os.path.join(content, 'manifest.json'))
```
(`os.path.realpath` of a missing `current` link returns the link path itself; the `isdir` check then fails → `None`. `_storage.load_json` returns `None` on any read/parse error.)

- [ ] **Run, expect PASS.** `pytest -q tests/test_chronicle_storage.py`

- [ ] **Commit.**
  `git commit -am "Chronicle: _chronicle_content_dir + _chronicle_manifest resolvers"` (+ trailer)

---

### Task 4: `_chronicle_swap(staging_dir, new_hash)` — atomic symlink repoint + previous rotation + orphan prune

**Files:** Modify `app.py`; Test `tests/test_chronicle_storage.py`
**Interfaces:** Produces `_chronicle_swap(staging_dir, new_hash) -> None`

- [ ] **Write the failing test.**
```python
def test_swap_publishes_and_current_resolves(chron):
    A._chronicle_swap(_stage_content(chron, 'hashA', session=3), 'hashA')
    assert A._chronicle_content_dir() == os.path.join(chron, 'content', 'hashA')
    assert os.path.realpath(os.path.join(chron, 'current')).endswith('hashA')
    assert A._chronicle_manifest()['session_number'] == 3
    # staging consumed by the move
    assert not os.path.exists(os.path.join(chron, '.staging', 'hashA'))


def test_second_swap_rotates_previous_and_prunes(chron):
    A._chronicle_swap(_stage_content(chron, 'h1', session=1), 'h1')
    A._chronicle_swap(_stage_content(chron, 'h2', session=2), 'h2')
    assert A._chronicle_manifest()['session_number'] == 2
    assert os.path.realpath(os.path.join(chron, 'previous')).endswith('h1')
    # both current + previous targets survive; nothing else lingers
    kept = set(os.listdir(os.path.join(chron, 'content')))
    assert kept == {'h1', 'h2'}
```

- [ ] **Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_storage.py::test_swap_publishes_and_current_resolves`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_swap'`.

- [ ] **Minimal implementation.** Add below `_chronicle_manifest` (still before `def load_campaign`):
```python
def _chronicle_swap(staging_dir, new_hash):
    """Publish a fully-rendered staging dir as the new live content.

    Moves `staging_dir` -> CHRONICLE_DIR/content/<new_hash>, then ATOMICALLY
    repoints the `current` symlink at it (write a temp symlink + os.replace,
    which is atomic on POSIX -- a reader never sees a half-swapped pointer).
    A directory os.replace is NOT atomic (ENOTEMPTY), hence the symlink repoint;
    this mirrors trash_campaign_dir's rmtree-then-move precedent (storage.py:124).
    The outgoing target is retained as `previous` for one-click rollback; older
    orphaned content dirs are pruned so disk stays bounded to current+previous.
    """
    content_root = os.path.join(CHRONICLE_DIR, 'content')
    os.makedirs(content_root, exist_ok=True)
    dest = os.path.join(content_root, new_hash)
    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.move(staging_dir, dest)

    current = os.path.join(CHRONICLE_DIR, 'current')
    previous = os.path.join(CHRONICLE_DIR, 'previous')
    old_target = os.path.realpath(current) if os.path.islink(current) else None

    _chronicle_repoint(current, dest)
    if old_target and os.path.isdir(old_target) and old_target != dest:
        _chronicle_repoint(previous, old_target)

    keep = {os.path.realpath(p) for p in (current, previous) if os.path.islink(p)}
    for name in os.listdir(content_root):
        p = os.path.join(content_root, name)
        if os.path.isdir(p) and p not in keep:
            shutil.rmtree(p, ignore_errors=True)


def _chronicle_repoint(link_path, target):
    """Atomically point `link_path` (a symlink) at `target`: create a temp
    symlink in the same dir, then os.replace it onto link_path (atomic on POSIX;
    works whether or not link_path already exists)."""
    tmp = link_path + '.tmp'
    if os.path.lexists(tmp):
        os.remove(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link_path)
```

- [ ] **Run, expect PASS.** `pytest -q tests/test_chronicle_storage.py`

- [ ] **Commit.**
  `git commit -am "Chronicle: _chronicle_swap atomic symlink repoint + previous rotation"` (+ trailer)

---

### Task 5: `_chronicle_rollback()` — repoint current → previous (reversible)

**Files:** Modify `app.py`; Test `tests/test_chronicle_storage.py`
**Interfaces:** Produces `_chronicle_rollback() -> bool`

- [ ] **Write the failing test.**
```python
def test_rollback_restores_previous_publish(chron):
    A._chronicle_swap(_stage_content(chron, 'h1', session=1), 'h1')
    A._chronicle_swap(_stage_content(chron, 'h2', session=2), 'h2')
    assert A._chronicle_rollback() is True
    assert A._chronicle_manifest()['session_number'] == 1
    assert os.path.realpath(os.path.join(chron, 'current')).endswith('h1')
    # reversible: the just-superseded publish is now `previous`
    assert os.path.realpath(os.path.join(chron, 'previous')).endswith('h2')


def test_rollback_is_false_without_a_previous(chron):
    A._chronicle_swap(_stage_content(chron, 'h1', session=1), 'h1')
    assert A._chronicle_rollback() is False


def test_rollback_is_false_before_any_publish(chron):
    assert A._chronicle_rollback() is False
```

- [ ] **Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_storage.py::test_rollback_restores_previous_publish`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_rollback'`.

- [ ] **Minimal implementation.** Add below `_chronicle_repoint`:
```python
def _chronicle_rollback():
    """One-click undo of the last publish: repoint `current` at `previous`.
    Reversible -- the superseded target rotates back into `previous`. Returns
    True if a rollback happened, False if there is no previous publish."""
    if not CHRONICLE_DIR:
        return False
    current = os.path.join(CHRONICLE_DIR, 'current')
    previous = os.path.join(CHRONICLE_DIR, 'previous')
    if not os.path.islink(previous):
        return False
    prev_target = os.path.realpath(previous)
    if not os.path.isdir(prev_target):
        return False
    cur_target = os.path.realpath(current) if os.path.islink(current) else None

    _chronicle_repoint(current, prev_target)
    if cur_target and os.path.isdir(cur_target) and cur_target != prev_target:
        _chronicle_repoint(previous, cur_target)
    return True
```

- [ ] **Run, expect PASS.** `pytest -q tests/test_chronicle_storage.py`

- [ ] **Full-suite guard + commit.**
  `pytest -q tests/test_chronicle_storage.py && python tools/check_templates.py`
  `git commit -am "Chronicle: _chronicle_rollback (reversible current<->previous swap)"` (+ trailer)

---

## Open questions

1. **`chronicle/` in `CAMPAIGN_SUBDIRS`?** I deliberately did **not** add `'chronicle'` to `storage.CAMPAIGN_SUBDIRS` (storage.py:164). The empty-state gate keys on `_chronicle_content_dir()` returning `None`, which works whether or not the base dir exists; `_chronicle_swap` creates `content/` lazily via `os.makedirs`. Pre-creating an empty `chronicle/` would be harmless but adds nothing. Flag if you'd rather it be pre-created for parity with `journals/` et al.
2. **Rollback depth = 1.** The prune in `_chronicle_swap` bounds disk to exactly `{current, previous}`, so rollback is single-level (matches design §4.1 "one-click rollback"; the "keep last N" phrasing implies more). If multi-step undo is wanted in PR1, the prune should keep the last N hashes and `previous` should become a small stack — I kept it single-level for MVP simplicity. Confirm.
3. **`reader_state.json` / `seen_creatures.json`** (design §4.1) are Phase-2/3 and not part of this storage slice; they'll be added by their owning slices using `_atomic_write_json(..., fsync=False)`. Noting so they aren't expected here.
4. **Cross-filesystem `shutil.move`.** `staging_dir` is assumed to live under `CHRONICLE_DIR/.staging/` (same volume as `content/`), so the move is a fast rename. The publish-endpoint slice must place its temp/staging tree under `CHRONICLE_DIR`, not the OS temp dir, or `shutil.move` degrades to a copy. Contract note for that slice.

---

## Part 2: Publish / Status / Rollback / Leak-scan / Render

## 1. Grounding (exact code I build against)

All line refs verified by reading the files in this worktree.

**GM auth is prefix-gated, not per-route (app.py:442-497).** `GM_API_PREFIXES` is a flat tuple of path prefixes; a single `@app.before_request def check_gm_access()` does `if any(path.startswith(prefix) for prefix in GM_API_PREFIXES) and not _is_gm(): return jsonify({"error": "GM access required"}), 403`. So adding `'/api/chronicle'` to that tuple GM-gates *every* `/api/chronicle/*` route with no decorator. The memory note "GM API auth is centralized… don't re-flag prefix-gated routes" and the design's "no `@gm_required` double-flag" both point here. `_is_gm()` (app.py:391-402) returns True in legacy-open mode (`not GM_PASSWORD`), which is how the tests reach the endpoint.

**Zip ingest + zip-slip guard to mirror — and the exact anti-pattern to fix (app.py:6497-6545, `campaign_import`).** It does `zf = zipfile.ZipFile(io.BytesIO(f.read()))` (the memory/design flag: reads the whole upload into RAM — we must `f.save(tmp)` instead) and guards traversal with:
```python
target = os.path.normpath(os.path.join(dest, n))
if target != dest and not target.startswith(dest + os.sep):
    return jsonify({'ok': False, 'error': 'unsafe path in archive'}), 400
```
I reuse that guard verbatim against the staging dir. `BadZipFile` → 400 is also modeled here (app.py:6509).

**Streaming a FileStorage to disk without a RAM read — the real precedent (app.py:9669-9714, `upload_handout_image`).** It uses `f.seek(0, os.SEEK_END); size = f.tell(); f.seek(0)` to size without reading, then `f.save(filepath)`. Werkzeug's `FileStorage.save()` streams in chunks — that is our "temp file, not `BytesIO(f.read())`".

**`markdown` is imported and currently dead (app.py:11).** `grep -n "markdown\." app.py` returns nothing — the import at line 11 has no call site. This subsystem is its first consumer. `markdown==3.5.1` and `gevent==26.5.0` are both pinned in requirements.txt. **No `bleach`, no `Pillow`** in requirements — HTML sanitize must be dependency-free (regex).

**`_atomic_write_json(path, obj, indent=2, fsync=True)` (app.py:166-199)** — temp-in-same-dir → fsync → `os.replace`. Not needed for fragments (plain file writes) but is the JSON-write contract if I persist any state.

**Prod worker (Procfile):** `gunicorn app:app --workers 1 --worker-class gevent --worker-connections 1000 --timeout 120`. One gevent worker; a CPU/IO-bound publish that never yields can trip the 120s SIGKILL and drop every SSE socket. Cooperative yielding uses `gevent.sleep(0)` (gevent is importable even under the Flask dev server / test client — `gevent.sleep(0)` is a harmless yield there).

**Test style (tests/test_campaign_backup.py).** Isolation via a subprocess that sets `os.environ['DATA_DIR']=tempdir; os.environ['GM_PASSWORD']=''` *before* `import app`, then drives `app.test_client()`. Multipart upload shape: `data={'backup': (io.BytesIO(bytes), 'backup.zip')}, content_type='multipart/form-data'`. I mirror this exactly (field name `archive`). A `_run(body)` helper shells `python -c`.

**`MAX_CONTENT_LENGTH = 64 MB` (app.py:105)** rejects oversized POSTs at the WSGI layer; the design's 48 MB zip cap stays clear of it.

**Consumed from the storage subsystem (shared contract — must land first):**
- global `CHRONICLE_DIR` (bound in both branches of `_bind_campaign_paths`, app.py:606-649; flat branch = `os.path.join(DATA_DIR, 'chronicle')`),
- `_chronicle_content_dir() -> str | None` (resolves `CHRONICLE_DIR/current`),
- `_chronicle_manifest() -> dict | None` (loads `<content>/manifest.json`),
- `_chronicle_swap(staging_dir, new_hash) -> None` (moves `staging_dir` under `content/<hash>`, atomically repoints the `current` symlink, rotates `previous`),
- `_chronicle_rollback() -> bool`.

I **produce**: `GM_API_PREFIXES` entry, `_chronicle_leak_scan`, `_chronicle_render_markdown` (+ callout preprocess + sanitize), `_chronicle_validate_manifest`, `_chronicle_safe_slug`, `_chronicle_coop_yield`, the three routes, and the sample fixture.

---

## 2. Files

**Modify**
- `app.py`
  - line ~490: add `'/api/chronicle',` inside the `GM_API_PREFIXES` tuple (before its closing `)` at app.py:490).
  - new section appended after the handout routes (`upload_handout_image` ends ~app.py:9714): the leak-scan/render/validate helpers + the three routes.

**Create**
- `tests/fixtures/chronicle_sample/manifest.json`
- `tests/fixtures/chronicle_sample/content/home.md`
- `tests/fixtures/chronicle_sample/content/romi.md`
- `tests/fixtures/chronicle_sample/assets/.gitkeep`
- `tests/test_chronicle_publish.py`

---

## 3. TDD tasks (ordered)

> Dependency: the **storage subsystem** (`CHRONICLE_DIR`, `_chronicle_swap`, `_chronicle_content_dir`, `_chronicle_manifest`, `_chronicle_rollback`) must be merged before Task 5 onward. Tasks 1–4 are standalone.

---

### Task 1: GM-gate the `/api/chronicle` prefix

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces the auth gate for all `/api/chronicle/*` routes (via `GM_API_PREFIXES`). Consumes `check_gm_access` (app.py:492).

- [ ] **Write the failing test** (create the test file with this first):
```python
"""Chronicle PR1 — publish endpoint, leak/manifest validation, markdown render,
status + rollback. Subprocess isolation (fresh DATA_DIR, legacy-open GM mode),
mirroring tests/test_campaign_backup.py."""
from __future__ import annotations
import io, os, sys, json, zipfile, subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    return subprocess.run(
        [sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
        capture_output=True, text=True, cwd=_REPO)


def test_chronicle_prefix_is_gm_gated():
    # The prefix must be present in the centralized GM gate so every
    # /api/chronicle/* route is GM-only with no per-route decorator.
    import app as A  # imported in-process here is fine: pure constant check
    assert '/api/chronicle' in A.GM_API_PREFIXES
```

- [ ] **Run it, expect FAIL:**
  `pytest -q tests/test_chronicle_publish.py::test_chronicle_prefix_is_gm_gated`
  Expected: `AssertionError` (the string is not yet in the tuple). (If the module-level `import app` in the test picks up a shared DATA_DIR, keep this test's assertion to the constant only — no client calls — so it needs no temp dir.)

- [ ] **Minimal implementation** — in `app.py`, inside the `GM_API_PREFIXES` tuple, immediately before the closing `)` at app.py:490 (right after the `'/api/round_events',` block):
```python
    '/api/round_events',
    # Chronicle (player campaign hub) publish pipeline: ingest a player-vault
    # zip, render markdown -> html fragments, atomic symlink swap, rollback.
    # GET reading routes live at /chronicle* (player-scoped, NOT here); only the
    # /api/chronicle/* mutations are GM-only, gated centrally by this prefix.
    '/api/chronicle',
)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_chronicle_prefix_is_gm_gated`

- [ ] **Commit:** `git commit -am "Chronicle: GM-gate the /api/chronicle route prefix"`
  (trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 2: `_chronicle_leak_scan(root_dir)`

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces `_chronicle_leak_scan(root_dir) -> list[str]` (offending `"relpath: marker"` entries; `[]` == clean).

- [ ] **Write the failing test:**
```python
def test_leak_scan_flags_forbidden_markers(tmp_path):
    import app as A
    (tmp_path / 'content').mkdir()
    (tmp_path / 'content' / 'clean.md').write_text('# Recap\nThe party arrived.\n')
    (tmp_path / 'content' / 'leaky.md').write_text('> [!danger] the lich is the mayor\n')
    (tmp_path / 'manifest.json').write_text('{"note": "has a [!secret] in json too"}')
    offenders = A._chronicle_leak_scan(str(tmp_path))
    assert any('leaky.md' in o and '[!danger]' in o for o in offenders), offenders
    assert any('manifest.json' in o and '[!secret]' in o for o in offenders), offenders
    assert not any('clean.md' in o for o in offenders), offenders
    # clean tree -> empty list
    (tmp_path / 'content' / 'leaky.md').unlink()
    (tmp_path / 'manifest.json').write_text('{"note": "ok"}')
    assert A._chronicle_leak_scan(str(tmp_path)) == []
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py::test_leak_scan_flags_forbidden_markers`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_leak_scan'`.

- [ ] **Minimal implementation** — append a new section after the handout routes (~app.py:9714):
```python
# ═════════════════════════════════════════════════════════════════════
#  CHRONICLE — player campaign hub: publish pipeline (PR1)
#  Ingest a player-vault zip, validate its manifest, RE-RUN the spoiler
#  leak check (defense in depth under the vault-side firewall), render
#  each page's markdown to an html fragment, and atomically repoint the
#  `current` symlink (swap owned by the storage subsystem). GM-only via
#  the '/api/chronicle' GM_API_PREFIXES gate.
# ═════════════════════════════════════════════════════════════════════
CHRONICLE_SCHEMA_VERSION = 1

# Obsidian GM-only callouts that must NEVER reach a player vault. The vault-side
# tools/chronicle_build.py strips them at generation; this is the server-side
# backstop (a bad build, a hand-edited zip) — publish is refused if any survive.
_CHRONICLE_LEAK_MARKERS = ('[!danger]', '[!secret]', '[!gm]')
_CHRONICLE_SCAN_EXTS = ('.md', '.markdown', '.json', '.html', '.htm', '.txt')


def _chronicle_leak_scan(root_dir):
    """Walk root_dir; return a sorted list of 'relpath: marker' strings for every
    text file containing a forbidden spoiler-callout marker. [] means clean."""
    offenders = []
    for base, _dirs, files in os.walk(root_dir):
        for fn in files:
            if not fn.lower().endswith(_CHRONICLE_SCAN_EXTS):
                continue
            full = os.path.join(base, fn)
            try:
                with open(full, encoding='utf-8', errors='ignore') as fp:
                    low = fp.read().lower()
            except OSError:
                continue
            rel = os.path.relpath(full, root_dir)
            for marker in _CHRONICLE_LEAK_MARKERS:
                if marker in low:
                    offenders.append('%s: %s' % (rel, marker))
    return sorted(offenders)
```
(Markers are already lowercase, so comparing against the lowercased text is correct and case-insensitive.)

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_leak_scan_flags_forbidden_markers`

- [ ] **Commit:** `git commit -am "Chronicle: server-side spoiler leak scan (_chronicle_leak_scan)"`

---

### Task 3: markdown render (callout mapping + sanitize)

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces `_chronicle_render_markdown(md_text) -> str`, `_chronicle_safe_slug(slug) -> str`, `_chronicle_coop_yield() -> None`.

- [ ] **Write the failing test:**
```python
def test_render_markdown_callouts_and_sanitize():
    import app as A
    md = (
        "# Session 3\n\n"
        "The party met **Romi**.\n\n"
        "> [!quote] Romi\n> We never had this conversation.\n\n"
        "> [!example] Handout\n> A torn ledger page.\n\n"
        "> [!note] table cue\n> keep this plain\n\n"
        "<script>alert(1)</script>\n\n"
        "[click](javascript:alert(2))\n"
    )
    html = A._chronicle_render_markdown(md)
    assert '<h1' in html and '<strong>Romi</strong>' in html
    assert 'class="callout-quote"' in html
    assert 'class="doc-frame"' in html
    assert '<blockquote>' in html          # unknown callout -> plain blockquote
    assert '[!quote]' not in html and '[!note]' not in html   # markers consumed
    assert '<script' not in html.lower()   # sanitized
    assert 'javascript:' not in html.lower()


def test_safe_slug():
    import app as A
    assert A._chronicle_safe_slug("Romi's Ledger") == 'romi-s-ledger'
    assert A._chronicle_safe_slug('../etc/passwd') == 'etc-passwd'
    assert A._chronicle_safe_slug('') == 'page'
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py -k "render_markdown or safe_slug"`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_render_markdown'`.

- [ ] **Minimal implementation** — append below `_chronicle_leak_scan`:
```python
_CHRONICLE_MD_EXTENSIONS = ['tables', 'attr_list', 'footnotes', 'sane_lists']
# A callout header line:  > [!quote] Optional title   /   > [!example]+ Title
_CHRONICLE_CALLOUT_RE = re.compile(r'^>\s*\[!(\w+)\][+-]?\s*(.*)$')


def _chronicle_coop_yield():
    """Yield the gevent worker mid-publish so a long render can't monopolize the
    single worker and trip the gunicorn --timeout 120 SIGKILL (which would drop
    every player's SSE). Harmless no-op under the dev server / test client."""
    try:
        import gevent
        gevent.sleep(0)
    except Exception:
        pass


def _chronicle_safe_slug(slug):
    """Filesystem-safe slug for an html fragment filename (no traversal)."""
    return re.sub(r'[^a-z0-9]+', '-', str(slug or '').lower()).strip('-') or 'page'


def _chronicle_callout_preprocess(md_text):
    """Rewrite Obsidian callouts to block-level HTML BEFORE the markdown pass.
    [!quote] -> div.callout-quote (read-aloud), [!example] -> div.doc-frame
    (handout panel); anything else -> a plain <blockquote>. python-markdown
    passes block-level HTML through untouched, so the inner body is rendered
    with a nested markdown pass and the whole div survives the outer render."""
    lines = (md_text or '').split('\n')
    out, i = [], 0
    while i < len(lines):
        m = _CHRONICLE_CALLOUT_RE.match(lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        ctype = m.group(1).lower()
        body = []
        if m.group(2).strip():
            body.append(m.group(2).strip())
        i += 1
        while i < len(lines) and lines[i].startswith('>'):
            body.append(re.sub(r'^>\s?', '', lines[i])); i += 1
        inner = markdown.markdown('\n'.join(body), extensions=_CHRONICLE_MD_EXTENSIONS)
        if ctype == 'quote':
            out.append('<div class="callout-quote">%s</div>' % inner)
        elif ctype == 'example':
            out.append('<div class="doc-frame">%s</div>' % inner)
        else:
            out.append('<blockquote>%s</blockquote>' % inner)
        out.append('')  # blank line keeps the raw-HTML block isolated
    return '\n'.join(out)


def _chronicle_sanitize_html(html):
    """Dependency-free defense-in-depth scrub (no bleach in requirements). The
    content already passed the leak scan and is GM-authored, but fragments are
    injected into player pages, so strip active-content vectors."""
    html = re.sub(r'(?is)<(script|style|iframe|object|embed)\b.*?</\1\s*>', '', html)
    html = re.sub(r'(?is)<(script|style|iframe|object|embed)\b[^>]*/?>', '', html)
    html = re.sub(r'(?i)\s+on\w+\s*=\s*"[^"]*"', '', html)
    html = re.sub(r"(?i)\s+on\w+\s*=\s*'[^']*'", '', html)
    html = re.sub(r'(?i)(href|src)\s*=\s*(["\'])\s*javascript:[^"\']*\2', r'\1=\2#\2', html)
    return html


def _chronicle_render_markdown(md_text):
    pre = _chronicle_callout_preprocess(md_text)
    html = markdown.markdown(pre, extensions=_CHRONICLE_MD_EXTENSIONS)
    return _chronicle_sanitize_html(html)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py -k "render_markdown or safe_slug"`

- [ ] **Commit:** `git commit -am "Chronicle: markdown render with callout mapping + HTML sanitize"`

---

### Task 4: Sample fixture zip (no real vault needed)

**Files:** Create `tests/fixtures/chronicle_sample/**`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces a committed player-vault sample + a `_zip_fixture()` test helper that packs it into `bytes` for multipart upload.

- [ ] **Write the failing test** (helper + a shape assertion that forces the fixture to exist and be valid):
```python
_FIX = os.path.join(_REPO, 'tests', 'fixtures', 'chronicle_sample')


def _zip_dir_bytes(src_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _d, files in os.walk(src_dir):
            for fn in files:
                if fn == '.gitkeep':
                    continue
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, src_dir))
    buf.seek(0)
    return buf.read()


def test_sample_fixture_is_valid_vault():
    man = json.load(open(os.path.join(_FIX, 'manifest.json')))
    assert man['schema_version'] == 1
    assert isinstance(man['pages'], list) and man['pages']
    for p in man['pages']:
        assert p['slug'] and p['source']
        assert os.path.isfile(os.path.join(_FIX, p['source'])), p['source']
    # zips without error and manifest sits at the archive root
    z = zipfile.ZipFile(io.BytesIO(_zip_dir_bytes(_FIX)))
    assert 'manifest.json' in z.namelist()
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py::test_sample_fixture_is_valid_vault`
  Expected: `FileNotFoundError: .../tests/fixtures/chronicle_sample/manifest.json`.

- [ ] **Minimal implementation** — create these committed files:

`tests/fixtures/chronicle_sample/manifest.json`:
```json
{
  "schema_version": 1,
  "campaign_id": "sample",
  "session_number": 3,
  "generated_at": "2026-07-15T00:00:00Z",
  "pages": [
    {"slug": "home", "section": "home", "title": "The Chronicle", "recipients": "all", "source": "content/home.md"},
    {"slug": "romi", "section": "cast", "title": "Romi", "epithet": "The broker who wasn't", "recipients": "all", "session_introduced": 2, "session_updated": 3, "source": "content/romi.md"}
  ],
  "mysteries": [],
  "calendar": {},
  "fieldguide": [],
  "spine": ["home", "romi"]
}
```

`tests/fixtures/chronicle_sample/content/home.md`:
```markdown
# The Chronicle

As of **Session 3**, the party has reached the harbor city.

> [!quote] The dockmaster
> Nobody sails past the reef after dark. Nobody.
```

`tests/fixtures/chronicle_sample/content/romi.md`:
```markdown
# Romi

*The broker who wasn't.* Last seen Session 3.

Romi deals in favors and forged manifests. The party met her at the ledger house.

> [!example] Handout: a torn ledger page
> ...three crates, unmarked, paid in full...
```

`tests/fixtures/chronicle_sample/assets/.gitkeep`: (empty — keeps the dir under git)

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_sample_fixture_is_valid_vault`

- [ ] **Commit:** `git commit -am "Chronicle: sample player-vault fixture for publish tests + dev"`

---

### Task 5: `POST /api/chronicle/publish` (temp-file ingest → validate → leak → render → swap)

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces `POST /api/chronicle/publish`, `_chronicle_validate_manifest(manifest) -> (bool, str|None)`. Consumes `CHRONICLE_DIR`, `_chronicle_swap(staging_dir, new_hash)`, `_chronicle_manifest()`, `sse_broadcast`.

- [ ] **Write the failing test** (happy path + zip-slip + leak refusal, subprocess-isolated):
```python
def test_publish_happy_path_and_leak_and_zipslip():
    zb = _zip_dir_bytes(_FIX)
    # leaky variant: same manifest, one page carrying a forbidden marker
    lbuf = io.BytesIO()
    with zipfile.ZipFile(lbuf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            "schema_version": 1, "session_number": 3,
            "pages": [{"slug": "leak", "source": "content/leak.md", "recipients": "all"}]}))
        z.writestr('content/leak.md', '> [!danger] the mayor is the lich\n')
    lbuf.seek(0)
    lb = lbuf.read()
    # zip-slip variant
    sbuf = io.BytesIO()
    with zipfile.ZipFile(sbuf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            "schema_version": 1, "pages": [{"slug": "x", "source": "content/x.md", "recipients": "all"}]}))
        z.writestr('../evil.md', 'pwned')
    sbuf.seek(0)
    sb = sbuf.read()

    import base64
    body = '''
import tempfile, base64, io, os, json
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

good = base64.b64decode({good!r})
leak = base64.b64decode({leak!r})
slip = base64.b64decode({slip!r})

# happy path -> 200, fragments exist, current resolves
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(good), 'chronicle.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 200, (r.status_code, r.data)
j = r.get_json(); assert j['ok'] and j['pages'] == 2, j
content = A._chronicle_content_dir()
assert content and os.path.isfile(os.path.join(content, 'html', 'home.html'))
assert os.path.isfile(os.path.join(content, 'html', 'romi.html'))
assert '<div class="callout-quote">' in open(os.path.join(content, 'html', 'home.html')).read()
assert A._chronicle_manifest()['session_number'] == 3

# leak -> 400, and `current` is UNCHANGED (still the good publish)
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(leak), 'leak.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 400 and r.get_json().get('leaks'), r.data
assert A._chronicle_manifest()['session_number'] == 3   # not clobbered

# zip-slip -> 400 and no escape file written
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(slip), 'slip.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 400, r.data
assert not os.path.exists(os.path.join(TMP, 'evil.md'))
assert not os.path.exists(os.path.join(TMP, 'chronicle', 'evil.md'))
print('PUBLISH_OK')
'''.format(good=base64.b64encode(zb).decode(),
           leak=base64.b64encode(lb).decode(),
           slip=base64.b64encode(sb).decode())
    r = _run(body)
    assert 'PUBLISH_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py::test_publish_happy_path_and_leak_and_zipslip`
  Expected: the subprocess raises inside the client call — `404` (route absent) so `assert r.status_code == 200` fails; `PUBLISH_OK` never prints.

- [ ] **Minimal implementation** — append below the render helpers:
```python
def _chronicle_validate_manifest(manifest):
    """Return (ok, error). Enforce the schema_version handshake and that pages[]
    is a non-empty list of {slug, source} entries (the render contract)."""
    if not isinstance(manifest, dict):
        return False, 'manifest.json missing or not a JSON object'
    if manifest.get('schema_version') != CHRONICLE_SCHEMA_VERSION:
        return False, 'unsupported schema_version: %r' % manifest.get('schema_version')
    pages = manifest.get('pages')
    if not isinstance(pages, list) or not pages:
        return False, 'manifest pages[] missing or empty'
    for p in pages:
        if not isinstance(p, dict) or not p.get('slug') or not p.get('source'):
            return False, 'each page requires slug + source'
    return True, None


@app.route('/api/chronicle/publish', methods=['POST'])
def chronicle_publish():
    """Ingest a player-vault zip (manifest.json + content/**.md + assets/**),
    validate + leak-scan + render markdown -> html/<slug>.html, then atomically
    repoint `current`. GM-only via the '/api/chronicle' GM_API_PREFIXES gate."""
    import zipfile, hashlib, shutil
    if not CHRONICLE_DIR:
        return jsonify({'ok': False, 'error': 'no chronicle storage bound'}), 400
    f = request.files.get('archive')
    if not f or not f.filename:
        return jsonify({'ok': False, 'error': 'no archive uploaded (field "archive")'}), 400

    staging_root = os.path.join(CHRONICLE_DIR, '.staging')
    os.makedirs(staging_root, exist_ok=True)
    # Stream the upload to disk (NOT BytesIO(f.read()) -- the campaign_import
    # anti-pattern): a 48 MB read would balloon the single worker's RAM and the
    # read+unzip must not pin the worker. FileStorage.save() streams in chunks.
    fd, tmp_zip = tempfile.mkstemp(dir=staging_root, suffix='.zip')
    os.close(fd)
    staging_dir = None
    try:
        f.save(tmp_zip)
        # Content-hash the bytes for a stable, dedup-friendly publish dir name.
        h = hashlib.sha256()
        with open(tmp_zip, 'rb') as zp:
            for chunk in iter(lambda: zp.read(65536), b''):
                h.update(chunk)
        new_hash = h.hexdigest()[:16]
        staging_dir = os.path.join(staging_root, new_hash)
        if os.path.isdir(staging_dir):
            shutil.rmtree(staging_dir)
        os.makedirs(staging_dir)

        try:
            zf = zipfile.ZipFile(tmp_zip)
        except zipfile.BadZipFile:
            return jsonify({'ok': False, 'error': 'not a valid .zip'}), 400

        # Extract with the zip-slip guard (mirrors campaign_import, app.py:6519).
        for n in zf.namelist():
            if n.endswith('/'):
                continue
            target = os.path.normpath(os.path.join(staging_dir, n))
            if target != staging_dir and not target.startswith(staging_dir + os.sep):
                return jsonify({'ok': False, 'error': 'unsafe path in archive'}), 400
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as out:
                out.write(zf.read(n))
            _chronicle_coop_yield()

        manifest = _storage.load_json(os.path.join(staging_dir, 'manifest.json'))
        ok, err = _chronicle_validate_manifest(manifest)
        if not ok:
            return jsonify({'ok': False, 'error': err}), 400

        # Defense-in-depth spoiler re-check across the WHOLE staged tree.
        leaks = _chronicle_leak_scan(staging_dir)
        if leaks:
            return jsonify({'ok': False, 'error': 'spoiler markers present in vault',
                            'leaks': leaks[:50]}), 400

        # Render each page markdown -> html/<slug>.html in bounded, yielding batches.
        html_dir = os.path.join(staging_dir, 'html')
        os.makedirs(html_dir, exist_ok=True)
        for page in manifest['pages']:
            src = os.path.normpath(os.path.join(staging_dir, str(page['source'])))
            if not src.startswith(staging_dir + os.sep) or not os.path.isfile(src):
                return jsonify({'ok': False,
                                'error': 'missing page source: %s' % page.get('source')}), 400
            with open(src, encoding='utf-8') as sp:
                html = _chronicle_render_markdown(sp.read())
            slug = _chronicle_safe_slug(page['slug'])
            with open(os.path.join(html_dir, slug + '.html'), 'w', encoding='utf-8') as hp:
                hp.write(html)
            _chronicle_coop_yield()

        # Atomic symlink repoint + rotate previous (storage subsystem).
        _chronicle_swap(staging_dir, new_hash)
        staging_dir = None  # ownership handed to _chronicle_swap; don't rmtree it
        sse_broadcast('chronicle_update', {'session_number': manifest.get('session_number')})
        return jsonify({'ok': True, 'hash': new_hash,
                        'pages': len(manifest['pages']),
                        'session_number': manifest.get('session_number')})
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
        if staging_dir and os.path.isdir(staging_dir):
            try:
                shutil.rmtree(staging_dir)  # failed publish -> clean up staged dir
            except OSError:
                pass
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_publish_happy_path_and_leak_and_zipslip`

- [ ] **Commit:** `git commit -am "Chronicle: POST /api/chronicle/publish (temp-file ingest, leak+manifest validation, markdown render, symlink swap)"`

---

### Task 6: `GET /api/chronicle/status`

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces `GET /api/chronicle/status`. Consumes `_chronicle_manifest()`, `_chronicle_content_dir()`, `CHRONICLE_DIR`.

- [ ] **Write the failing test:**
```python
def test_status_reports_last_publish():
    zb = _zip_dir_bytes(_FIX)
    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

# before any publish
j = c.get('/api/chronicle/status').get_json()
assert j['published'] is False, j

c.post('/api/chronicle/publish',
       data={{'archive': (io.BytesIO(base64.b64decode({good!r})), 'c.zip')}},
       content_type='multipart/form-data')
j = c.get('/api/chronicle/status').get_json()
assert j['published'] is True and j['session_number'] == 3 and j['pages'] == 2, j
assert j['can_rollback'] is False, j   # first publish -> no previous yet
print('STATUS_OK')
'''.format(good=base64.b64encode(zb).decode())
    r = _run(body)
    assert 'STATUS_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py::test_status_reports_last_publish`
  Expected: `404` on `/api/chronicle/status` → `KeyError/assert` in subprocess; `STATUS_OK` not printed.

- [ ] **Minimal implementation** — append below `chronicle_publish`:
```python
@app.route('/api/chronicle/status', methods=['GET'])
def chronicle_status():
    """Last-publish summary for the GM hub: session, page count, current hash,
    and whether a rollback target exists. GM-only via the prefix gate."""
    man = _chronicle_manifest()
    if man is None:
        return jsonify({'published': False})
    content = _chronicle_content_dir()
    prev = os.path.join(CHRONICLE_DIR, 'previous') if CHRONICLE_DIR else None
    return jsonify({
        'published': True,
        'session_number': man.get('session_number'),
        'generated_at': man.get('generated_at'),
        'pages': len(man.get('pages') or []),
        'hash': os.path.basename(os.path.realpath(content)) if content else None,
        'can_rollback': bool(prev and os.path.lexists(prev)),
    })
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_status_reports_last_publish`

- [ ] **Commit:** `git commit -am "Chronicle: GET /api/chronicle/status (last publish + rollback availability)"`

---

### Task 7: `POST /api/chronicle/rollback`

**Files:** Modify `app.py`; Test `tests/test_chronicle_publish.py`

**Interfaces:** Produces `POST /api/chronicle/rollback`. Consumes `_chronicle_rollback() -> bool`, `_chronicle_manifest()`, `sse_broadcast`.

- [ ] **Write the failing test** (publish twice → rollback restores session 2):
```python
def test_rollback_restores_previous_publish():
    # build a second vault variant that reports session 2
    v2 = io.BytesIO()
    with zipfile.ZipFile(v2, 'w') as z:
        man = json.load(open(os.path.join(_FIX, 'manifest.json')))
        man['session_number'] = 2
        z.writestr('manifest.json', json.dumps(man))
        for p in man['pages']:
            z.writestr(p['source'], open(os.path.join(_FIX, p['source'])).read())
    v2.seek(0)
    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

def pub(b):
    return c.post('/api/chronicle/publish',
                  data={{'archive': (io.BytesIO(b), 'c.zip')}},
                  content_type='multipart/form-data')

# rollback with nothing to roll back to -> 400
assert c.post('/api/chronicle/rollback').status_code == 400

pub(base64.b64decode({s2!r}))   # session 2 (becomes previous)
pub(base64.b64decode({s3!r}))   # session 3 (current)
assert A._chronicle_manifest()['session_number'] == 3

r = c.post('/api/chronicle/rollback')
assert r.status_code == 200 and r.get_json()['ok'], r.data
assert A._chronicle_manifest()['session_number'] == 2   # current now points at prev
print('ROLLBACK_OK')
'''.format(s2=base64.b64encode(v2.read()).decode(),
           s3=base64.b64encode(_zip_dir_bytes(_FIX)).decode())
    r = _run(body)
    assert 'ROLLBACK_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
```

- [ ] **Run, expect FAIL:** `pytest -q tests/test_chronicle_publish.py::test_rollback_restores_previous_publish`
  Expected: `405/404` on `/api/chronicle/rollback` → first assert (`== 400`) fails.

- [ ] **Minimal implementation** — append below `chronicle_status`:
```python
@app.route('/api/chronicle/rollback', methods=['POST'])
def chronicle_rollback():
    """Repoint `current` back to the previous publish (one-click undo of a bad
    publish). Swap logic + previous-dir bookkeeping live in the storage
    subsystem's _chronicle_rollback(). GM-only via the prefix gate."""
    if not _chronicle_rollback():
        return jsonify({'ok': False, 'error': 'no previous publish to roll back to'}), 400
    man = _chronicle_manifest() or {}
    sse_broadcast('chronicle_update', {'session_number': man.get('session_number'),
                                       'rolled_back': True})
    return jsonify({'ok': True, 'session_number': man.get('session_number')})
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_publish.py::test_rollback_restores_previous_publish`

- [ ] **Full-suite gate:** `pytest -q tests/test_chronicle_publish.py && python tools/check_templates.py`

- [ ] **Commit:** `git commit -am "Chronicle: POST /api/chronicle/rollback (repoint current -> previous)"`

---

## Open questions

1. **200 vs 202.** The design mentions both "bounded batches + `gevent.sleep(0)`" and "accept→202→spawn greenlet." I chose the **synchronous, cooperatively-yielding 200** path (simpler, deterministically testable, and it never blocks the worker because `gevent.sleep(0)` yields between every extracted file and every rendered page). The 202+greenlet variant is only needed if a single publish's render is long enough to matter *even with yields*, which at a 4-player table's page count it is not. If the assembled plan wants 202, the render loop moves into a `gevent.spawn`-ed function and `/status` grows a `rendering` state — flagging so the whole plan stays consistent.
2. **`new_hash` provenance.** I hash the **zip bytes** (sha256[:16]). If the storage subsystem's `_chronicle_swap` expects the hash to key on *content-dir* contents (post-render) instead, align on one definition. Zip-bytes hashing is stable and lets a re-publish of an identical vault dedup to the same `content/<hash>`.
3. **Sanitize depth.** No `bleach`/`Pillow` in requirements and the no-new-deps posture is explicit, so I ship a regex scrub (scripts/styles/iframes/`on*=`/`javascript:`). This is a backstop under the vault firewall + leak scan, not a general-purpose sanitizer. If richer allow-listing is wanted, adding `bleach` is a requirements decision for the GM to make.
4. **Asset validation/EXIF/resize** is a vault-side (`tools/chronicle_build.py`, PR0) responsibility per §3.2.1; the server copies `assets/**` through the staged tree as-is (still covered by the zip-slip guard and the 48 MB cap). Confirm the server is not expected to re-strip EXIF at ingest.
5. **`_chronicle_swap` staging ownership.** My handler sets `staging_dir = None` after a successful `_chronicle_swap` so the `finally` cleanup won't `rmtree` the just-swapped dir — this assumes `_chronicle_swap` **moves/renames** `staging_dir` into `content/<hash>` (not copies). If it copies and leaves the staging dir, adjust the cleanup contract with the storage subsystem.

> **Part 2 amendment (contract §6 — slug safety).** `_chronicle_validate_manifest` must ALSO reject any page whose `slug` does not match `^[a-z0-9][a-z0-9-]{0,80}$` (return `(False, 'invalid slug: %r' % p.get('slug'))`), so the fragment filename `html/<slug>.html` written here is exactly the key Part 5 looks up. Add the check inside the `for p in pages:` loop next to the slug/source presence check.


---

## Part 3: Player-scope Auth + Ownership Recipients + Handout-leak Fix

## 1. Grounding (real code this builds against)

**Auth primitives (app.py:391–440).** `_is_gm()` (app.py:391) returns True for a site admin, the active campaign's GM, **or legacy-open mode with no `GM_PASSWORD`** (`return (not GM_PASSWORD) or session.get('gm_authenticated', False)`). `_account_mode()` (app.py:222) distinguishes account vs legacy. `_user_owns_pc(user_id, pc_name)` (app.py:426) is the real ownership predicate: it resolves `_active_campaign_id()`, walks `_storage.party_dir(cid)`, and for each wrapped doc whose `_campaigns._character_name(doc) == pc_name` returns `doc.get('owner_user_id') == user_id`. It is **PF2e-party-dir-only today** (doesn't scan `cosmere_pc_dir`).

**The self-asserted identity hole (the reason recipients key on ownership).** `session['player_name']` is set by any non-GM via `POST` handlers (app.py:6050 `set_player`, app.py:12839) with only a `PARTY_LIBRARY` membership check — it is **not** an ownership proof. So per-player secrets must key on `owner_user_id`, per design C1.

**The only two `before_request` hooks (app.py:492–524).** `check_gm_access` (app.py:492) prefix-gates `GM_API_PREFIXES` and returns `jsonify({"error": "GM access required"}), 403`. `_csrf_guard` (app.py:509) short-circuits non-POST. A new gate slots in right after `_csrf_guard` as a third `@app.before_request`. There is **no player-scope gate today** — player HTML pages (`/party`, `/notes`) are open functions with no auth decorator (app.py:6059, 8474).

**`GM_API_PREFIXES` (app.py:443–490)** is a flat tuple matched by `path.startswith(prefix)`. Adding `'/api/chronicle'` here gates the GM publish/status/rollback routes (handled by the storage/publish subsystem); note `/api/chronicle*` does **not** collide with the player `/chronicle*` gate below (different prefix). `abort` is **not** imported — return `(msg, 403)` tuples like `check_gm_access`.

**Handout routes (app.py:9617–9667) — the pre-existing leak.**
- `get_handouts` (app.py:9617) reads `request.args.get('player')` (app.py:9620) and filters `HANDOUTS` by that **client-supplied** name — any client can pass `?player=<victim>` and read their handouts.
- `create_handout` (app.py:9628, `@gm_required`) ends with `sse_broadcast('handout', handout)` (app.py:9655) — **no `player_filter`**, so the full content (incl. targeted handouts) is pushed to every SSE client; the client JS gate in `player_view.html` (~374) and `player_sheet.html` (~4133) (`if (!h.recipients.includes('all') && !h.recipients.includes(myName)) return;`) is the *only* filter and is client-side/trivially bypassed.
- Handout `recipients` historically hold pc **names** (app.py:9593 comment: `recipients: ['all' or pc_names]`), unlike Chronicle page frontmatter which holds pc **slugs** (design §3.3).

**`sse_broadcast(event_type, data, *, player_filter=None)` (app.py:1060).** The player-facing frame is computed **once** and **shared by every player subscriber** — subscribers are `(q, is_gm)` tuples (app.py:1106) with **no per-player identity**. `player_filter` returning `None`/`False` **drops the frame for all players** (GMs still get `data`). This is why a *targeted* handout cannot be safely live-fanned-out: there is no per-recipient frame. Precedent filters: app.py:1448 (`encounter_update`), app.py:13607 (`whisper` → `lambda d: None`).

**Recipient resolution helpers available.** `_active_campaign_id()` (app.py:226; returns None in legacy-open and for non-members in account mode), `_active_campaign_doc()` (app.py:257), `_campaigns.user_role(campaign, user_id)` (core/campaigns.py:106), `_campaigns._character_name(doc)` (core/campaigns.py:177), `_storage.slugify(name)` (core/storage.py:62), `_storage.party_dir` / `cosmere_pc_dir` / `load_json` (core/storage.py:76/89/222). Aliases: `_auth`/`_campaigns` imported app.py:219, `_storage` app.py:595 (module globals — safe to reference from function bodies defined anywhere, resolved at call time).

**Test conventions.** In-process: `import app` + `monkeypatch.setattr(app, ...)` (see `tests/test_campaign_config_safety.py`). End-to-end account-mode: a subprocess with a throwaway `DATA_DIR` driving `app.app.test_client()` through the real `/setup` → `/campaigns/new` → `/campaign/<cid>/activate` flow (see `tests/test_cosmere_campaign_binding.py`; `_run(body)` shells `python -c`). `auth.create_user(username, password, display_name=None, is_admin=False)` (core/auth.py:73), `auth.get_user_by_username` (core/auth.py:55), `campaigns.add_member(cid, user_id, role, character_id=None)` (core/campaigns.py:119), `storage.wrap_character(chid, cid, system, data, *, owner_user_id=)` (core/storage.py:266). `tests/conftest.py` only puts the repo root on `sys.path`. `tools/check_templates.py` exists. Inline-handler guard lives at `tests/test_inline_handler_escaping.py`.

## 2. Files

**Modify**
- `app.py`
  - **Add** `_chronicle_player_gate` `@app.before_request` — insert immediately after `_csrf_guard` (after app.py:524).
  - **Add** helpers `_chronicle_owned_pc_slugs`, `_chronicle_page_visible`, `_handout_visible_to_request`, `_handout_player_filter` — insert just above the `# PLAYER HANDOUTS` banner (before app.py:9590, where `_storage`/`_campaigns` are in scope).
  - **Replace** `get_handouts` body (app.py:9617–9626).
  - **Replace** the `sse_broadcast('handout', handout)` call in `create_handout` (app.py:9654–9655).

**Create**
- `tests/test_chronicle_auth.py`

*(Nav-tab `_player_nav.html`/`_cosmere_player_nav.html` edits, the `'/api/chronicle'` `GM_API_PREFIXES` entry, and the reading routes that consume `_chronicle_page_visible` belong to the reading-routes/nav subsystem; this slice **produces** `_chronicle_page_visible` for them.)*

---

## 3. TDD tasks (ordered)

### Task 1: `_chronicle_page_visible` + `_chronicle_owned_pc_slugs` (recipient predicate)

**Files:** Modify `app.py` · Test `tests/test_chronicle_auth.py`
**Interfaces:**
- Produces `_chronicle_page_visible(page_meta, *, user, is_gm) -> bool`
- Produces `_chronicle_owned_pc_slugs(user_id) -> set[str]`
- Consumes `_active_campaign_id()`, `_account_mode()`, `_storage.party_dir/cosmere_pc_dir/load_json/slugify`, `_campaigns._character_name`

- [ ] **Write the failing test** (`tests/test_chronicle_auth.py`, in-process unit tests):
```python
"""Chronicle player-scope auth: recipient visibility + handout-leak fix.

Recipients key on ACCOUNT OWNERSHIP (owner_user_id), never the self-asserted
session['player_name'] (app.py:6050). Chronicle pages address a pc SLUG; live
handouts address a pc NAME. Legacy-open (unauthenticated identity) => non-'all'
content is GM-only.
"""
import app


def test_page_all_is_public():
    # A public page shows to anyone, even an anonymous non-GM.
    assert app._chronicle_page_visible({'recipients': 'all'}, user=None, is_gm=False)
    assert app._chronicle_page_visible({'recipients': ['all']}, user=None, is_gm=False)
    # Missing recipients defaults to public (author didn't scope it).
    assert app._chronicle_page_visible({}, user=None, is_gm=False)


def test_page_targeted_gm_sees_all():
    assert app._chronicle_page_visible({'recipients': ['aria']}, user=None, is_gm=True)


def test_page_targeted_owner_sees_nonowner_hidden(monkeypatch):
    monkeypatch.setattr(app, '_account_mode', lambda: True)
    monkeypatch.setattr(app, '_chronicle_owned_pc_slugs',
                        lambda uid: {'aria'} if uid == 'u_alice' else set())
    assert app._chronicle_page_visible({'recipients': ['aria']},
                                       user={'id': 'u_alice'}, is_gm=False)
    assert not app._chronicle_page_visible({'recipients': ['aria']},
                                           user={'id': 'u_bob'}, is_gm=False)
    # 'all' in a mixed list is still public.
    assert app._chronicle_page_visible({'recipients': ['aria', 'all']},
                                       user={'id': 'u_bob'}, is_gm=False)


def test_page_targeted_legacy_open_is_gm_only(monkeypatch):
    # No accounts -> no trustworthy identity -> a scoped page never reaches a player.
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    assert not app._chronicle_page_visible({'recipients': ['aria']},
                                           user=None, is_gm=False)
```

- [ ] **Run it, expect FAIL:** `pytest -q tests/test_chronicle_auth.py`
  Expected: `AttributeError: module 'app' has no attribute '_chronicle_page_visible'`.

- [ ] **Minimal implementation** — insert this block in `app.py` immediately **above** the `# ──────────────────────────────────────────────────────────\n# PLAYER HANDOUTS` banner (currently app.py:9590):
```python
# ── Chronicle: recipient (per-player secret) visibility ──────────────────
# Recipients key on ACCOUNT OWNERSHIP, never the self-asserted
# session['player_name'] (any non-GM can set that to any name, app.py:6050).
# Chronicle PAGES address a pc SLUG (frontmatter `recipients:`); live HANDOUTS
# predate slugs and address a pc NAME. Both resolve to the same owner_user_id
# predicate as _user_owns_pc (app.py:426).
def _chronicle_owned_pc_slugs(user_id):
    """Slugs of the PCs `user_id` owns in the active campaign, for matching a
    Chronicle page's `recipients`. Empty set if none / no active campaign."""
    cid = _active_campaign_id()
    slugs = set()
    if not cid:
        return slugs
    for pdir in (_storage.party_dir(cid), _storage.cosmere_pc_dir(cid)):
        if not os.path.isdir(pdir):
            continue
        for fn in os.listdir(pdir):
            if not fn.endswith('.json'):
                continue
            doc = _storage.load_json(os.path.join(pdir, fn))
            if isinstance(doc, dict) and doc.get('owner_user_id') == user_id:
                slugs.add(_storage.slugify(_campaigns._character_name(doc)))
    return slugs


def _chronicle_page_visible(page_meta, *, user, is_gm):
    """True if a Chronicle page should be shown to this caller.
    `recipients` 'all' (or a list containing 'all', or absent) -> everyone.
    Otherwise it is a per-player secret: the GM always sees it; in account mode a
    player sees it iff they own a PC whose slug is in `recipients`; in legacy-open
    (identity is unauthenticated) a non-'all' page is GM-only."""
    recips = page_meta.get('recipients', 'all')
    if recips == 'all' or (isinstance(recips, (list, tuple)) and 'all' in recips):
        return True
    if is_gm:
        return True
    if not _account_mode() or not user:
        return False   # legacy-open: no trustworthy identity -> GM-only
    recip_list = recips if isinstance(recips, (list, tuple)) else [recips]
    owned = _chronicle_owned_pc_slugs(user['id'])
    return any(r in owned for r in recip_list)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_auth.py`

- [ ] **Commit:**
```
Chronicle: ownership-keyed page recipient visibility (_chronicle_page_visible)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 2: Handout-leak fix — server-side GET filter + SSE frame filter

**Files:** Modify `app.py` · Test `tests/test_chronicle_auth.py`
**Interfaces:**
- Produces `_handout_visible_to_request(h) -> bool`
- Produces `_handout_player_filter(h) -> dict | None`
- Consumes `_is_gm()`, `_account_mode()`, `_auth.current_user()`, `_user_owns_pc`, `sse_broadcast(..., player_filter=)`

- [ ] **Write the failing test** — append to `tests/test_chronicle_auth.py` (the SSE-frame filter is deterministic and unit-testable directly; the end-to-end GET filtering is verified in Task 4):
```python
def test_handout_player_filter_drops_targeted():
    # The player SSE frame is SHARED by every player (sse_broadcast has no
    # per-player identity), so a targeted handout must NOT fan out live.
    assert app._handout_player_filter({'recipients': ['all']}) is not None
    assert app._handout_player_filter({'recipients': ['all', 'Aria']}) is not None
    assert app._handout_player_filter({'recipients': ['Aria']}) is None
    assert app._handout_player_filter({'recipients': []}) is None
    assert app._handout_player_filter({}) is None
```

- [ ] **Run it, expect FAIL:** `pytest -q tests/test_chronicle_auth.py::test_handout_player_filter_drops_targeted`
  Expected: `AttributeError: module 'app' has no attribute '_handout_player_filter'`.

- [ ] **Minimal implementation** — (a) add the two helpers to the same Chronicle block from Task 1 (below `_chronicle_page_visible`):
```python
def _handout_visible_to_request(h):
    """Whether live handout `h` is visible to the CURRENT request's caller.
    GM sees all; 'all' is public; otherwise account-mode OWNERSHIP (recipient is
    a pc NAME here, historically) or the legacy self-picked player_name."""
    recips = h.get('recipients') or []
    if 'all' in recips:
        return True
    if _is_gm():
        return True
    if _account_mode():
        u = _auth.current_user()
        return bool(u) and any(_user_owns_pc(u['id'], r) for r in recips)
    pn = session.get('player_name')   # legacy-open self-picked identity
    return bool(pn) and pn in recips


def _handout_player_filter(h):
    """SSE player-frame filter for the 'handout' broadcast. The player frame is
    SHARED across all player subscribers (app.py:1106, no per-player identity),
    so a targeted handout can't be safely fanned out live -- pushing it would
    leak it to every player (the pre-existing bug, design C1). Only 'all'
    handouts go out live; a targeted handout is dropped for players and its
    recipients pick it up on the next per-caller GET /api/handouts."""
    return h if 'all' in (h.get('recipients') or []) else None
```
(b) **Replace** `get_handouts` (app.py:9617–9626) with:
```python
@app.route('/api/handouts', methods=['GET'])
def get_handouts():
    """Handouts visible to the CALLER, decided server-side. GM sees all; a player
    sees only 'all' handouts or ones addressed to a character they OWN. The
    ?player= query param is NO LONGER trusted -- it let any client read another
    player's handouts by naming them (the pre-existing leak, design C1)."""
    if _is_gm():
        return jsonify({"handouts": HANDOUTS})
    visible = [h for h in HANDOUTS if _handout_visible_to_request(h)]
    return jsonify({"handouts": visible})
```
(c) **Replace** the broadcast in `create_handout` (app.py:9654–9655) with:
```python
    # Broadcast: only 'all' handouts fan out live (the player SSE frame is shared
    # across players, app.py:1106); a targeted handout is dropped for players and
    # its recipients pick it up on the next per-caller GET /api/handouts.
    sse_broadcast('handout', handout, player_filter=_handout_player_filter)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_auth.py`

- [ ] **Commit:**
```
Chronicle: fix pre-existing handout leak (server-side GET filter + SSE frame filter)

GET /api/handouts no longer trusts the ?player= query param; the 'handout'
SSE broadcast drops targeted handouts from the shared player frame. Recipients
key on owner_user_id via _user_owns_pc, not the self-asserted player_name.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 3: `/chronicle*` player-scope gate (`before_request`)

**Files:** Modify `app.py` · Test `tests/test_chronicle_auth.py`
**Interfaces:**
- Produces `_chronicle_player_gate()` (`@app.before_request`)
- Consumes `_is_gm()`, `_account_mode()`, `_auth.current_user()`, `_active_campaign_doc()`, `_campaigns.user_role`, `session['player_name']`, `redirect`/`url_for('login')`

- [ ] **Write the failing test** — add a subprocess helper + gate test to `tests/test_chronicle_auth.py` (the gate needs a real request context, session, and env control, so use the repo's `_run` subprocess pattern):
```python
import os, sys, textwrap, subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script],
                          capture_output=True, text=True, cwd=_REPO)


def test_chronicle_gate_account_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
        auth.create_user('alice','pw_alice12','Alice'); alice = auth.get_user_by_username('alice')
        auth.create_user('bob','pw_bob1234','Bob');     bob   = auth.get_user_by_username('bob')

        assert c.post('/campaigns/new', data={'name':'Golarion','system':'pf2e'}).status_code == 302
        cid = [x for x in storage.list_campaign_ids()
               if campaigns.get_campaign(x)['name']=='Golarion'][0]
        campaigns.add_member(cid, alice['id'], 'player')      # bob is NOT a member
        assert c.post('/campaign/'+cid+'/activate').status_code == 302

        # bob (not a member of this campaign) is refused at the gate.
        with c.session_transaction() as s: s['user_id']=bob['id']; s['active_campaign_id']=cid
        assert c.get('/chronicle').status_code == 403, 'non-member must be blocked'

        # alice (a member) passes the gate; no reading route exists yet in this
        # slice, so Flask 404s AFTER the gate -> proves the gate let her through.
        with c.session_transaction() as s: s['user_id']=alice['id']; s['active_campaign_id']=cid
        assert c.get('/chronicle').status_code == 404, 'member must pass the gate (404 = route not built)'

        # a logged-OUT caller in account mode is redirected to login, not 403.
        with c.session_transaction() as s: s.clear()
        rv = c.get('/chronicle')
        assert rv.status_code == 302 and '/login' in rv.headers['Location'], rv.headers.get('Location')
        print('GATE_ACCOUNT_OK')
    ''')
    assert 'GATE_ACCOUNT_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_gate_legacy_password_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'sekret'
        import app as A
        c = A.app.test_client()
        # legacy mode WITH a password: a player who has not picked a character is
        # refused; picking one (session player_name) lets them through (404=no route).
        assert c.get('/chronicle').status_code == 403
        with c.session_transaction() as s: s['player_name'] = 'Aria'
        assert c.get('/chronicle').status_code == 404
        # the GM (authenticated) always passes.
        with c.session_transaction() as s:
            s.clear(); s['gm_authenticated'] = True
        assert c.get('/chronicle').status_code == 404
        print('GATE_LEGACY_OK')
    ''')
    assert 'GATE_LEGACY_OK' in r.stdout, r.stdout + r.stderr
```

- [ ] **Run it, expect FAIL:** `pytest -q tests/test_chronicle_auth.py::test_chronicle_gate_account_mode`
  Expected: assertion `'non-member must be blocked'` fails — without the gate, `GET /chronicle` returns **404** (no route) for bob instead of 403.

- [ ] **Minimal implementation** — insert this `@app.before_request` immediately **after** `_csrf_guard` (after app.py:524):
```python
# Player-scope gate for the Chronicle reading hub (/chronicle*). No such gate
# existed before Chronicle: the GM API is prefix-gated (check_gm_access) and
# player pages were open. Chronicle pages can carry per-player secrets, so the
# hub requires an IDENTIFIED caller -- account mode: a member of the active
# campaign; legacy-open with a GM password: a self-picked character. The GM
# always passes (in legacy dev with no GM_PASSWORD, _is_gm() is True, so the
# local dev flow stays open). Per-page recipient filtering is a second layer
# (_chronicle_page_visible). The GM /api/chronicle/* routes are gated separately
# via GM_API_PREFIXES, not here (different path prefix -- no overlap).
@app.before_request
def _chronicle_player_gate():
    path = request.path
    if not (path == '/chronicle' or path.startswith('/chronicle/')):
        return
    if _is_gm():
        return
    if _account_mode():
        u = _auth.current_user()
        if not u:
            return redirect(url_for('login', next=path))
        if _campaigns.user_role(_active_campaign_doc(), u['id']):
            return
        return ("Chronicle is for this campaign's players.", 403)
    # Legacy-open with a GM password set: the player must have picked a character.
    if session.get('player_name'):
        return
    return ('Pick your character to read the Chronicle.', 403)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_auth.py`

- [ ] **Commit:**
```
Chronicle: player-scope before_request gate for /chronicle*

Account mode requires active-campaign membership; legacy-with-password requires
a picked character; GM always passes. No player gate existed before Chronicle.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 4: End-to-end recipient enforcement (handout ownership + `?player=` distrust)

**Files:** Test `tests/test_chronicle_auth.py` (no new source — proves Tasks 1–2 end-to-end through the real routes)
**Interfaces:** Consumes `POST/GET /api/handouts`, `_user_owns_pc`, `storage.wrap_character`

- [ ] **Write the failing test** — append to `tests/test_chronicle_auth.py`:
```python
def test_handout_recipients_ownership_account_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
        auth.create_user('alice','pw_alice12','Alice'); alice = auth.get_user_by_username('alice')
        auth.create_user('bob','pw_bob1234','Bob');     bob   = auth.get_user_by_username('bob')

        assert c.post('/campaigns/new', data={'name':'Golarion','system':'pf2e'}).status_code == 302
        cid = [x for x in storage.list_campaign_ids()
               if campaigns.get_campaign(x)['name']=='Golarion'][0]
        campaigns.add_member(cid, alice['id'], 'player')
        campaigns.add_member(cid, bob['id'], 'player')
        assert c.post('/campaign/'+cid+'/activate').status_code == 302

        # A claimed PC 'Aria' owned by alice, written straight to the party store.
        doc = storage.wrap_character(storage.new_id(), cid, 'pf2e',
                                     {'name':'Aria','build':{'name':'Aria'}},
                                     owner_user_id=alice['id'])
        storage.atomic_write_json(os.path.join(storage.party_dir(cid), 'Aria.json'), doc)

        # GM (the setup admin session) creates a handout targeted ONLY at Aria.
        assert c.post('/api/handouts',
                      json={'title':'For Aria','content':'secret','recipients':['Aria']}).status_code == 200
        # ...and a public one.
        assert c.post('/api/handouts',
                      json={'title':'Town Notice','content':'hi','recipients':['all']}).status_code == 200

        def titles(resp): return {h['title'] for h in resp.get_json()['handouts']}

        # alice (the OWNER) sees the targeted handout -- with NO ?player= param.
        with c.session_transaction() as s: s['user_id']=alice['id']; s['active_campaign_id']=cid
        assert 'For Aria' in titles(c.get('/api/handouts'))
        assert 'Town Notice' in titles(c.get('/api/handouts'))

        # bob (a member but NOT the owner) never sees it, and cannot conjure it
        # by passing ?player=Aria (the old trusted-param leak is closed).
        with c.session_transaction() as s: s['user_id']=bob['id']; s['active_campaign_id']=cid
        assert 'For Aria' not in titles(c.get('/api/handouts'))
        assert 'For Aria' not in titles(c.get('/api/handouts?player=Aria'))
        assert 'Town Notice' in titles(c.get('/api/handouts'))     # public still reaches everyone

        # the GM sees everything.
        with c.session_transaction() as s:
            s.clear(); s['user_id'] = auth.get_user_by_username('gm')['id']; s['active_campaign_id']=cid
        assert {'For Aria','Town Notice'} <= titles(c.get('/api/handouts'))
        print('HANDOUT_RECIPIENTS_OK')
    ''')
    assert 'HANDOUT_RECIPIENTS_OK' in r.stdout, r.stdout + r.stderr
```

- [ ] **Run it, expect FAIL if run before Task 2** (`pytest -q tests/test_chronicle_auth.py::test_handout_recipients_ownership_account_mode`): the pre-fix `get_handouts` honors `?player=Aria`, so `'For Aria' not in titles(...?player=Aria)` fails. (After Tasks 1–3 it passes on first run — keep it as the regression guard.)

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_auth.py`

- [ ] **Commit:**
```
Chronicle: end-to-end guard for handout recipient ownership + ?player= distrust

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

---

### Task 5: Full-suite + template parse green

- [ ] **Run:** `pytest -q` and `python tools/check_templates.py`
  Expected: suite green (no template changes in this slice, so `check_templates.py` is unaffected but run per CI contract).
- [ ] **Commit** (only if a prior task needs a fixup): none expected.

---

## 4. Open questions

1. **Handout recipients are pc NAMES; Chronicle pages are pc SLUGS.** This slice keeps that split (handouts stay name-keyed for backward compat with existing `handouts.json` records and the GM handout-creation UI; pages are slug-keyed per frontmatter §3.3). The merged Handouts *screen* (reading-routes subsystem, §5) will surface both — it must apply `_handout_visible_to_request` to live handouts and `_chronicle_page_visible` to published handout pages separately. Flagging in case the design intends a single unified key; if so, `create_handout` would need to also slugify recipients and `_handout_visible_to_request` switch to slug matching.

2. **`_user_owns_pc` is PF2e-party-dir-only** (app.py:426 doesn't scan `cosmere_pc_dir`), so account-mode handout ownership for a **Cosmere** PC currently resolves False. `_chronicle_owned_pc_slugs` (Task 1) *does* scan both dirs, so Chronicle *pages* work for Cosmere owners, but live *handouts* to a Cosmere PC would not match. Options: (a) extend `_user_owns_pc` to also scan `cosmere_pc_dir` (touches a shared, snapshot-guarded function — needs its own justification), or (b) have `_handout_visible_to_request` fall back to `recipient_slug in _chronicle_owned_pc_slugs(u['id'])`. Recommend (b) inside this slice to keep `_user_owns_pc` untouched — but it changes handout matching from name to slug, which reopens Q1. Deferring the decision to the design owner.

3. **Client-side handout gates left in place.** `player_view.html` (~374) and `player_sheet.html` (~4133) still filter SSE handouts client-side. After the SSE fix they only ever receive `'all'` frames, so the gate is now redundant defense-in-depth (harmless). Left untouched to keep this slice server-only; note it if the reading-routes subsystem rewrites those handlers.

4. **Legacy-open (no `GM_PASSWORD`) treats everyone as GM** via `_is_gm()`, so the `/chronicle*` gate is fully open in local dev — intended (matches the repo's legacy-open dev posture) and consistent with design §4.4 ("per-player secrets require account mode"). The gate's legacy-*with-password* branch is the only legacy path that actually restricts.

---

## Part 4: Templates + `.chron-*` Component Classes

## 1. Grounding (exact code this slice builds against)

**How content templates attach to the shell.** Every non-standalone screen extends `base.html` and fills three blocks — verified in `templates/party_view.html:1-5` and `templates/calendar.html:1-25`:
```jinja
{% extends "base.html" %}
{% block title %}...{% endblock %}
{% block extra_head %} ...raw CSS... {% endblock %}
{% block content %} ...markup... {% endblock %}
```
`base.html:49-62` wraps `extra_head` **inside** the head `<style>…</style>`, so `extra_head` is CSS-only — you cannot emit a `<link>` or `<script>` there. `base.html`'s `<head>` exposes only two override seams: `{% block title %}` (line 20) and `{% block extra_head %}` (line 61). **There is no head seam for a `<link>`** — this matters for the reading font (below).

**Fonts actually loaded by `base.html`.** `base.html:50` imports only `Inter` + `Cinzel`; `:65` conditionally adds Cormorant/Playfair for Cosmere. **Alegreya (`--font-flavor`, `system.css:123`) is loaded ONLY in the standalone `player_sheet.html:40`, never in `base.html`.** So a Chronicle screen extending `base.html` renders `--font-flavor` as its Georgia fallback unless we load Alegreya ourselves. A late `@import` appended into `base.html`'s `<style>` via `extra_head` is ignored by the cascade (`@import` must precede all style rules). → We add one additive head seam to `base.html` and override it in `chronicle_base.html` with a real `<link>`.

**Bottom player nav.** `base.html:483-505` includes `templates/_player_nav.html` for PF2e players and `:509-512` includes `_cosmere_player_nav.html` for Cosmere, computing `active_player_tab` from `request.path`. Both nav partials are plain `<a class="player-nav-tab">` lists (`_player_nav.html:6-42`, `_cosmere_player_nav.html:6-42`) with a **Notes** tab pointing at `/notes` (`_player_nav.html:27-32`, `_cosmere_player_nav.html:27-32`). `request.path` is available directly in these partials (they already read it via `active_player_tab`). Since `chronicle_base.html` extends `base.html`, both the top nav (`base.html:73-109`) and the correct bottom player nav come for free — chronicle_base does **not** re-include a nav.

**Tokens (all real, `system.css`).** Ramps/aliases `:28-99`: `--gilt-100..600`, `--ruby-100..600`, `--ink-50..950`; `--bg-page:#0c0a07`, `--bg-card:#1c1813`, `--bg-card-2:#28221a`, `--bg-sunken:#14110c`; `--text-on-cream / -2 / -3` (primary/secondary/muted **on a warm-dark card**, `:78-83`), `--text-on-dark / -2 / -3` (on the page floor, `:84-86`); `--border-card:rgba(201,163,78,0.14)`, `--border-rule:rgba(201,163,78,0.28)` (`:88-89`). Spacing `--sp-1..9` (`:98-107`), radii `--r-sm:2px` / `--r-md:4px` (`:110-112`), motion `--t-fast/base/slow` (`:120-123`), fonts `--font-display` (Cinzel) / `--font-flavor` (Alegreya) / `--font-ui` (Inter) (`:125-128`).

**The restraint bar this slice must clear.** The anti-ornament pass `system.css:210-289` is the reference: it force-flattens cards to `background:var(--bg-card)` + single `1px solid var(--border-card)` + `box-shadow:none` (`:270-280`), neutralizes drop-caps (`:255-262`), hides sub-ornament glyphs (`:265`), and turns nested cards into hairline-`border-top`-separated sections (`:283-297`). The hairline idiom recurs as `border-top:1px solid var(--border-card)` on `X + X` sibling rows (`:2935`, `:3111`, `:3207`). Reading measure precedent: `splash.html:59` uses `max-width:660px`. Bottom-nav body offset: `body.has-player-nav{padding-bottom:56px}` (`system.css:3938-3940`) — reading pages inherit this automatically.

**Template CI + escaping guards.** `tools/check_templates.py` auto-discovers `templates/**/*.html` and `jinja2.Environment.parse()`s each (parse-only; `{% extends %}` is not resolved, so extend-chains parse fine). `tests/test_inline_handler_escaping.py` is a static-regex guard: `_HANDLER_ON_LINE = re.compile(r'\bon[a-z]+\s*=\s*"')` (line ~97), `_read(rel)` opens a repo-relative path, and a `_GUARDED` list names files to check. Chronicle's rule is stricter than escaping — **zero inline handlers** — so we add a dedicated ban test keyed on `templates/chronicle*.html`.

**Context this slice consumes (produced by the routing/storage slices).** Per `CHRONICLE_DESIGN.md §3.4 / §4.4`: a request-scoped context processor exposes `chronicle_published` (bool, `_chronicle_manifest() is not None`), and each reading route passes `manifest`, a `nav` dict of section counts (`nav.story/lore/cast/handouts`), `is_gm`, and per-screen data (`pages`, `recaps`, `latest_recap`, `handouts`, `page`+`page_html`+`backlinks`, `party`). Each page dict carries the frontmatter contract (`§3.3`): `title, section, slug, epithet, tags, recipients, session_introduced, session_updated`, plus routing-computed `portrait_url` and `recipient_label`. **These names are the coordination contract with the other slices** (see Open Questions).

---

## 2. Files

**Create**
- `templates/chronicle_base.html` — shell: `head_extra` (Alegreya), kicker, sub-tab strip, live-banner slot, `{% block chronicle %}`.
- `templates/chronicle_home.html`
- `templates/chronicle_story.html`
- `templates/chronicle_lore.html`
- `templates/chronicle_cast.html`
- `templates/chronicle_handouts.html`
- `templates/chronicle_journal.html`
- `templates/chronicle_page.html`
- `tests/test_chronicle_templates.py`

**Modify**
- `templates/base.html` — add `{% block head_extra %}{% endblock %}` before `</head>` (after the `system.css` link, ~line 69).
- `templates/_player_nav.html` — replace the Notes tab (lines 27-32) with a `chronicle_published`-gated Chronicle tab.
- `templates/_cosmere_player_nav.html` — same swap (lines 27-32).
- `static/css/system.css` — append the `.chron-*` component block (end of file, ~line 4849).
- `tests/test_inline_handler_escaping.py` — add `test_chronicle_templates_have_no_inline_handlers`.

---

## 3. TDD Tasks

### Task 1: Test harness — existence, extend-chain, reading-font, CSS classes, inline-handler ban

**Files:** Create `tests/test_chronicle_templates.py`; Modify `tests/test_inline_handler_escaping.py`

**Interfaces:** Consumes nothing (static file assertions). Produces the guards that gate Tasks 2-6.

- [ ] **Write the failing tests.** Create `tests/test_chronicle_templates.py`:
```python
"""Static guards for the Chronicle template slice (PR1).

No app render — these assert file presence, the extend-chain, that the
reading serif is actually loaded (base.html only ships Inter+Cinzel), and
that system.css defines the .chron-* component grammar. Full render is
covered by tools/check_templates.py (parse) + the route tests.
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(_REPO, "templates")

_EXPECTED = [
    "chronicle_base.html", "chronicle_home.html", "chronicle_story.html",
    "chronicle_lore.html", "chronicle_cast.html", "chronicle_handouts.html",
    "chronicle_journal.html", "chronicle_page.html",
]


def _tpl(name):
    with open(os.path.join(_TPL, name), encoding="utf-8") as f:
        return f.read()


def test_expected_chronicle_templates_exist():
    for name in _EXPECTED:
        assert os.path.isfile(os.path.join(_TPL, name)), f"missing template: {name}"


def test_screen_templates_extend_chronicle_base():
    for name in _EXPECTED:
        if name == "chronicle_base.html":
            continue
        assert '{% extends "chronicle_base.html" %}' in _tpl(name), \
            f"{name} must extend chronicle_base.html"


def test_chronicle_base_extends_base_and_loads_reading_font():
    text = _tpl("chronicle_base.html")
    assert '{% extends "base.html" %}' in text
    # base.html does NOT load Alegreya; the reading surface must pull it in.
    assert "Alegreya" in text, "reading serif not loaded in chronicle_base.html"


def test_base_html_has_head_extra_seam():
    # chronicle_base injects the <link> via this additive block.
    assert "{% block head_extra %}" in _tpl("base.html")


_CHRON_CLASSES = [
    ".chron-kicker", ".chron-title", ".chron-subnav", ".chron-tab",
    ".chron-live-bar", ".chron-grid", ".chron-card", ".chron-monogram",
    ".chron-portrait", ".chron-pill", ".chron-prose", ".chron-callout-quote",
    ".chron-doc-frame", ".chron-timeline", ".chron-chapter", ".chron-empty",
]


def test_system_css_defines_chron_component_classes():
    with open(os.path.join(_REPO, "static", "css", "system.css"), encoding="utf-8") as f:
        css = f.read()
    missing = [c for c in _CHRON_CLASSES if c not in css]
    assert not missing, f"system.css missing Chronicle classes: {missing}"
```
Then append to `tests/test_inline_handler_escaping.py` (uses the module's existing `_REPO`, `glob`, `re`, `_read`):
```python
# Chronicle rule (stricter than escaping): data-* + addEventListener ONLY —
# no inline onclick/onload/on* at all. A leading \s ensures we match real
# attributes and skip data-on-* and mid-word "on".
_INLINE_HANDLER_ANY = re.compile(r'\son[a-z]+\s*=\s*"')


def test_chronicle_templates_have_no_inline_handlers():
    files = glob.glob(os.path.join(_REPO, "templates", "chronicle*.html"))
    assert files, "no chronicle*.html templates found (guard would silently pass)"
    offenders = []
    for path in files:
        for i, line in enumerate(_read(os.path.relpath(path, _REPO)).splitlines(), 1):
            if _INLINE_HANDLER_ANY.search(line):
                offenders.append(f"{os.path.basename(path)}:{i}: {line.strip()}")
    assert not offenders, (
        "inline event handlers in Chronicle templates (use addEventListener):\n"
        + "\n".join(offenders)
    )
```

- [ ] **Run it, expect FAIL.** `pytest -q tests/test_chronicle_templates.py tests/test_inline_handler_escaping.py`
  Expected: `test_expected_chronicle_templates_exist` → `AssertionError: missing template: chronicle_base.html`; `test_chronicle_templates_have_no_inline_handlers` → `AssertionError: no chronicle*.html templates found`.

- [ ] **Minimal implementation.** None — this task only lands the failing guards. (No commit until they can pass; proceed to Task 2.)

- [ ] **Run tests, expect PASS.** Deferred — these go green as Tasks 2-6 land.

- [ ] **Commit** (after Task 6 makes them green, or commit the test file now on a red suite only if your workflow allows; otherwise fold into Task 6's commit).

---

### Task 2: `.chron-*` component block in `system.css`

**Files:** Modify `static/css/system.css` (append at EOF, ~line 4849)
**Test:** `tests/test_chronicle_templates.py::test_system_css_defines_chron_component_classes`

**Interfaces:** Consumes tokens (`--gilt-*`, `--ruby-*`, `--ink-700`, `--bg-card`, `--text-on-cream*`, `--border-card/rule`, `--sp-*`, `--r-*`, `--t-fast`, `--font-*`). Produces the `.chron-*` classes every screen template consumes.

- [ ] **Write the failing test.** Already in Task 1 (`test_system_css_defines_chron_component_classes`).

- [ ] **Run it, expect FAIL.** `pytest -q tests/test_chronicle_templates.py::test_system_css_defines_chron_component_classes`
  Expected: `AssertionError: system.css missing Chronicle classes: ['.chron-kicker', ...]`.

- [ ] **Minimal implementation.** Append to the very end of `static/css/system.css`:
```css
/* ═══════════════════════════════════════════════════════════════════════
   CHRONICLE — player campaign hub. Warm-dark reading surfaces + a restrained
   card grammar. Mirrors the anti-ornament pass (~210-289): no glow, flat
   dots, static badges, ONE uppercase role, hairline rails. Tokens only.
   ═══════════════════════════════════════════════════════════════════════ */
.chron { max-width: 72rem; margin: 0 auto; }

/* The one uppercase, tracked role: the section kicker. */
.chron-kicker {
    font-family: var(--font-ui);
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--gilt-300); margin: 0 0 var(--sp-3);
}
/* Titles: Cinzel, sentence-case, not shouted. */
.chron-title {
    font-family: var(--font-display);
    font-size: 24px; font-weight: 600; letter-spacing: 0.02em;
    color: var(--text-on-dark); margin: 0 0 var(--sp-5);
}

/* Sub-tab strip — hairline underline, gilt on current. */
.chron-head { border-bottom: 1px solid var(--border-card); margin-bottom: var(--sp-6); }
.chron-subnav { display: flex; gap: var(--sp-4); flex-wrap: wrap; }
.chron-tab {
    font-family: var(--font-ui); font-size: 13px; font-weight: 500;
    color: var(--text-on-dark-3); text-decoration: none;
    padding: var(--sp-2) 0 var(--sp-3); position: relative;
    transition: color var(--t-fast);
}
.chron-tab:hover { color: var(--text-on-dark); }
.chron-tab[aria-current="page"] { color: var(--gilt-300); }
.chron-tab[aria-current="page"]::after {
    content: ""; position: absolute; left: 0; right: 0; bottom: -1px;
    height: 2px; background: var(--gilt-300);
}

/* Slim live bar — ruby left-rule + ruby text, NOT full-bleed. */
.chron-live-bar {
    display: flex; align-items: center; gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-4); margin-bottom: var(--sp-5);
    background: var(--bg-card);
    border: 1px solid var(--border-card);
    border-left: 3px solid var(--ruby-300);
    border-radius: var(--r-sm);
    font-family: var(--font-ui); font-size: 13px; color: var(--ruby-200);
}
.chron-live-bar__dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ruby-300); flex-shrink: 0; }
.chron-live-bar__text { flex: 1; }
.chron-live-bar__link { color: var(--gilt-300); text-decoration: none; font-weight: 600; }
.chron-live-bar__link:hover { color: var(--gilt-200); }

/* Card grid — one grammar for Cast + Lore. */
.chron-grid {
    list-style: none; margin: 0; padding: 0;
    display: grid; gap: var(--sp-4);
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
}
.chron-grid__item { margin: 0; }
.chron-card {
    display: flex; gap: var(--sp-4); align-items: flex-start;
    padding: var(--sp-4);
    background: var(--bg-card);
    border: 1px solid var(--border-card);
    border-radius: var(--r-md);
    text-decoration: none;
    transition: border-color var(--t-fast);
}
.chron-card:hover { border-color: var(--border-rule); }
.chron-card__figure { flex-shrink: 0; }
.chron-portrait {
    width: 56px; height: 56px; border-radius: var(--r-sm);
    object-fit: cover; display: block; background: var(--ink-700);
}
/* Monogram fallback — quiet body-size letter on a flat well (no 44px drop-cap). */
.chron-monogram {
    display: flex; align-items: center; justify-content: center;
    width: 56px; height: 56px; border-radius: var(--r-sm);
    background: var(--ink-700);
    font-family: var(--font-display); font-size: 20px; font-weight: 600;
    color: var(--text-on-cream-2);
}
.chron-card__body { min-width: 0; }
.chron-card__title {
    font-family: var(--font-display); font-size: 15px; font-weight: 600;
    color: var(--text-on-cream); margin: 0 0 2px; letter-spacing: 0.01em;
}
.chron-card__epithet {
    font-family: var(--font-flavor); font-style: italic; font-size: 13px;
    color: var(--text-on-cream-2); margin: 0 0 var(--sp-2);
}
.chron-card__meta {
    font-family: var(--font-ui); font-size: 11px; color: var(--text-on-cream-3);
    margin: var(--sp-2) 0 0; display: flex; gap: var(--sp-2);
    align-items: center; flex-wrap: wrap;
}

/* Flat tag pills — static, no glow. */
.chron-tags { display: flex; gap: var(--sp-2); flex-wrap: wrap; margin: 0; padding: 0; }
.chron-pill {
    font-family: var(--font-ui); font-size: 10px; font-weight: 600;
    letter-spacing: 0.04em; padding: 2px 7px; border-radius: var(--r-sm);
    background: rgba(201,163,78,0.10); border: 1px solid var(--border-card);
    color: var(--text-on-cream-2); text-decoration: none;
}
.chron-pill--restricted {
    background: rgba(168,58,58,0.14); border-color: rgba(168,58,58,0.34);
    color: var(--ruby-200);
}

/* Reading surface — warm-dark, Alegreya, clamped measure (~65ch). */
.chron-prose {
    max-width: 65ch;
    background: var(--bg-card); border: 1px solid var(--border-card);
    border-radius: var(--r-md); padding: var(--sp-6) var(--sp-7);
    font-family: var(--font-flavor); font-size: 18px; line-height: 1.62;
    color: var(--text-on-cream);
}
.chron-prose h1, .chron-prose h2, .chron-prose h3 {
    font-family: var(--font-display); color: var(--text-on-cream);
    line-height: 1.25; margin: 1.4em 0 0.5em;
}
.chron-prose h1 { font-size: 22px; }
.chron-prose h2 { font-size: 19px; }
.chron-prose h3 { font-size: 16px; }
.chron-prose p { margin: 0 0 1em; }
.chron-prose a { color: var(--gilt-300); text-decoration: underline; text-underline-offset: 2px; }
.chron-prose a:hover { color: var(--gilt-200); }
.chron-prose img { max-width: 100%; height: auto; border-radius: var(--r-sm); }

/* Read-aloud quote ([!quote]) + handout panel ([!example]). */
.chron-callout-quote {
    margin: 1.2em 0; padding: var(--sp-3) var(--sp-5);
    border-left: 2px solid var(--gilt-400);
    font-style: italic; color: var(--text-on-cream-2);
}
.chron-doc-frame {
    margin: 1.2em 0; padding: var(--sp-5);
    background: var(--bg-card-2); border: 1px solid var(--border-card);
    border-radius: var(--r-sm);
}

/* Story timeline — hairline rail + solid node dots. */
.chron-timeline { position: relative; margin: 0; padding: 0 0 0 var(--sp-6); list-style: none; }
.chron-timeline::before {
    content: ""; position: absolute; left: 5px; top: 4px; bottom: 4px;
    width: 1px; background: var(--border-card);
}
.chron-timeline__item { position: relative; margin: 0 0 var(--sp-6); }
.chron-timeline__node {
    position: absolute; left: -22px; top: 6px;
    width: 9px; height: 9px; border-radius: 50%; background: var(--gilt-300);
}
/* Chapter break — centered Cinzel label + thin side rules (no gilt diamond). */
.chron-chapter {
    display: flex; align-items: center; gap: var(--sp-4); margin: var(--sp-7) 0;
    font-family: var(--font-display); font-size: 13px; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--text-on-dark-3);
}
.chron-chapter::before, .chron-chapter::after {
    content: ""; flex: 1; height: 1px; background: var(--border-card);
}

/* New-since marker — flat 6px gilt disc (Phase 2). */
.chron-new-dot {
    display: inline-block; width: 6px; height: 6px; border-radius: 50%;
    background: var(--gilt-200); vertical-align: middle; margin-left: var(--sp-2);
}

/* Flat empty-state placeholder — flat well + small glyph, no gradient hatch. */
.chron-empty {
    padding: var(--sp-8) var(--sp-4); text-align: center;
    color: var(--text-on-dark-3); font-family: var(--font-ui); font-size: 14px;
    background: var(--bg-card); border: 1px solid var(--border-card);
    border-radius: var(--r-md);
}
.chron-empty__glyph {
    width: 32px; height: 32px; margin: 0 auto var(--sp-3); display: block;
    fill: none; stroke: var(--text-on-dark-3); stroke-width: 1.5;
    stroke-linecap: round; stroke-linejoin: round;
}

@media (prefers-reduced-motion: reduce) {
    .chron-tab, .chron-card, .chron-live-bar__link { transition: none; }
}
```

- [ ] **Run tests, expect PASS.** `pytest -q tests/test_chronicle_templates.py::test_system_css_defines_chron_component_classes`

- [ ] **Commit.** `git commit -am "Chronicle: warm-dark .chron-* component classes in system.css"` (trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`)

---

### Task 3: `chronicle_base.html` shell + `base.html` head seam

**Files:** Create `templates/chronicle_base.html`; Modify `templates/base.html`
**Test:** `tests/test_chronicle_templates.py` (`_extends_base_and_loads_reading_font`, `_base_html_has_head_extra_seam`), `tools/check_templates.py`

**Interfaces:** Consumes `manifest`, `nav` (`.story/.lore/.cast/.handouts`), `request.path`, `window.appSSE` (from `_sse_hub.html`), `encounter_update` SSE frame. Produces `{% block chronicle %}` for the six screens; the live-banner slot; the Alegreya reading font.

- [ ] **Write the failing test.** Covered by Task 1's `test_chronicle_base_extends_base_and_loads_reading_font` and `test_base_html_has_head_extra_seam`.

- [ ] **Run it, expect FAIL.** `pytest -q tests/test_chronicle_templates.py::test_base_html_has_head_extra_seam tests/test_chronicle_templates.py::test_chronicle_base_extends_base_and_loads_reading_font`
  Expected: `AssertionError: {% block head_extra %} ... in base.html` then a missing-file `FileNotFoundError`/`AssertionError` for chronicle_base.

- [ ] **Minimal implementation.** In `templates/base.html`, add the head seam immediately after the `system.css` `<link>` (line 69), before `</head>` (line 70):
```jinja
    <link rel="stylesheet" href="{{ url_for('static', filename='css/system.css') }}">
    {# Additive head seam: child templates inject a stylesheet <link> that a
       late @import inside the block above could not (cascade drops it). #}
    {% block head_extra %}{% endblock %}
</head>
```
Create `templates/chronicle_base.html`:
```jinja
{% extends "base.html" %}
{% block title %}Chronicle{% if manifest %} — Session {{ manifest.session_number }}{% endif %}{% endblock %}

{% block head_extra %}
  {# Alegreya is the long-form reading serif (--font-flavor). base.html loads
     only Inter + Cinzel, so the Chronicle reading surfaces pull it in here.
     A real <link> (not a late @import in base's <style>, which is ignored). #}
  <link rel="stylesheet"
        href="https://fonts.googleapis.com/css2?family=Alegreya:ital,wght@0,400;0,500;0,700;1,400;1,500&display=swap">
{% endblock %}

{% block content %}
<div class="chron">

  {# Live-session banner slot. PR1 ships it hidden; Phase 3 keeps it live. #}
  <div id="chron-live-bar" class="chron-live-bar" role="status" aria-live="polite" hidden>
    <span class="chron-live-bar__dot" aria-hidden="true"></span>
    <span class="chron-live-bar__text">A session is live at the table now.</span>
    <a href="/mobile" class="chron-live-bar__link">Open combat</a>
  </div>

  <header class="chron-head">
    <p class="chron-kicker">The Chronicle{% if manifest %} · As of Session {{ manifest.session_number }}{% endif %}</p>
    <nav class="chron-subnav" aria-label="Chronicle sections">
      {% set _p = request.path %}
      <a href="/chronicle" class="chron-tab"{% if _p == '/chronicle' %} aria-current="page"{% endif %}>Home</a>
      {% if nav.story %}<a href="/chronicle/story" class="chron-tab"{% if _p.startswith('/chronicle/story') %} aria-current="page"{% endif %}>Story</a>{% endif %}
      {% if nav.lore %}<a href="/chronicle/lore" class="chron-tab"{% if _p.startswith('/chronicle/lore') %} aria-current="page"{% endif %}>Lore</a>{% endif %}
      {% if nav.cast %}<a href="/chronicle/cast" class="chron-tab"{% if _p.startswith('/chronicle/cast') %} aria-current="page"{% endif %}>Cast</a>{% endif %}
      {% if nav.handouts %}<a href="/chronicle/handouts" class="chron-tab"{% if _p.startswith('/chronicle/handouts') %} aria-current="page"{% endif %}>Handouts</a>{% endif %}
      <a href="/chronicle/journal" class="chron-tab"{% if _p.startswith('/chronicle/journal') %} aria-current="page"{% endif %}>Journal</a>
    </nav>
  </header>

  <main class="chron-body">
    {% block chronicle %}{% endblock %}
  </main>
</div>

<script>
(function () {
  // Live-session banner: reveal the slim bar while an encounter is active.
  // Subscribe on the shared hub (base.html <head>) — never new EventSource.
  var bar = document.getElementById('chron-live-bar');
  if (!bar || !window.appSSE) return;
  window.appSSE('encounter_update', function (e) {
    var d = null; try { d = JSON.parse(e.data); } catch (_) { return; }
    var live = !!(d && d.active_name) && !(d && d.ended);
    bar.hidden = !live;
  });
})();
</script>
{% endblock %}
```

- [ ] **Run tests, expect PASS.** `pytest -q tests/test_chronicle_templates.py -k "head_extra or reading_font" && python tools/check_templates.py`

- [ ] **Commit.** `git commit -am "Chronicle: base shell (chronicle_base) + head_extra seam for reading font"`

---

### Task 4: `chronicle_cast.html` (representative full screen)

**Files:** Create `templates/chronicle_cast.html`
**Test:** `tests/test_chronicle_templates.py`, `tests/test_inline_handler_escaping.py`, `tools/check_templates.py`

**Interfaces:** Consumes `pages` (list of frontmatter dicts + `portrait_url`, `recipient_label`), `is_gm`. Server-rendered anchors to `/chronicle/page/<slug>`; monogram fallback; restricted pill for GM.

- [ ] **Write the failing test.** Covered by Task 1 (existence, extend-chain, inline-handler ban).

- [ ] **Run it, expect FAIL.** `pytest -q tests/test_chronicle_templates.py::test_expected_chronicle_templates_exist`
  Expected: `AssertionError: missing template: chronicle_cast.html`.

- [ ] **Minimal implementation.** Create `templates/chronicle_cast.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">Cast</h1>

{% if pages %}
<ul class="chron-grid" role="list">
  {% for p in pages %}
  <li class="chron-grid__item">
    <a class="chron-card" href="/chronicle/page/{{ p.slug }}">
      <div class="chron-card__figure">
        {% if p.portrait_url %}
          <img class="chron-portrait" src="{{ p.portrait_url }}" alt="" loading="lazy" width="56" height="56">
        {% else %}
          <span class="chron-monogram" aria-hidden="true">{{ (p.title or '?')[0]|upper }}</span>
        {% endif %}
      </div>
      <div class="chron-card__body">
        <h2 class="chron-card__title">{{ p.title }}</h2>
        {% if p.epithet %}<p class="chron-card__epithet">{{ p.epithet }}</p>{% endif %}
        {% if p.tags %}
        <div class="chron-tags">
          {% for t in p.tags %}<span class="chron-pill">{{ t }}</span>{% endfor %}
        </div>
        {% endif %}
        <p class="chron-card__meta">
          {% if p.session_updated %}<span>Last seen Session {{ p.session_updated }}</span>{% endif %}
          {% if is_gm and p.recipient_label %}<span class="chron-pill chron-pill--restricted">Only {{ p.recipient_label }}</span>{% endif %}
        </p>
      </div>
    </a>
  </li>
  {% endfor %}
</ul>
{% else %}
<div class="chron-empty">
  <svg class="chron-empty__glyph" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
    <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>
  <p>No one has been introduced to the chronicle yet.</p>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Run tests, expect PASS.** `pytest -q tests/test_chronicle_templates.py tests/test_inline_handler_escaping.py -k "cast or exist or extend or inline" && python tools/check_templates.py`

- [ ] **Commit.** `git commit -am "Chronicle: Cast screen (entity card grid, monogram fallback)"`

---

### Task 5: Remaining five screen templates (Home, Story, Lore, Handouts, Journal, Page)

**Files:** Create `templates/chronicle_home.html`, `chronicle_story.html`, `chronicle_lore.html`, `chronicle_handouts.html`, `chronicle_journal.html`, `chronicle_page.html`
**Test:** `tests/test_chronicle_templates.py`, `tests/test_inline_handler_escaping.py`, `tools/check_templates.py`

**Interfaces:** Consumes `manifest`, `latest_recap` (`.html`, `.pull_quote`), `party`, `recaps` (`.title/.session_number/.html/.chapter`), `pages`, `handouts` (`.title/.html/.image_url/.recipient_label`), `page`+`page_html`+`backlinks`, `is_gm`. All fragments are server-sanitized at publish (`§4.3`) → `|safe`.

- [ ] **Write the failing test.** Covered by Task 1.

- [ ] **Run it, expect FAIL.** `pytest -q tests/test_chronicle_templates.py::test_screen_templates_extend_chronicle_base`
  Expected: `AssertionError: missing template: chronicle_home.html` (or the first absent one).

- [ ] **Minimal implementation.** Create the six files.

`chronicle_home.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">{{ manifest.title or 'The story so far' }}</h1>
<p class="chron-card__meta" style="margin-bottom:var(--sp-6);">
  As of Session {{ manifest.session_number }}{% if manifest.calendar and manifest.calendar.in_game_date %} · {{ manifest.calendar.in_game_date }}{% endif %}
</p>

{% if latest_recap %}
<article class="chron-prose">
  {% if latest_recap.pull_quote %}<div class="chron-callout-quote">{{ latest_recap.pull_quote }}</div>{% endif %}
  {{ latest_recap.html|safe }}
  <p><a href="/chronicle/story">Read the full story &rarr;</a></p>
</article>
{% else %}
<div class="chron-empty"><p>The chronicle opens after your first session.</p></div>
{% endif %}

{% if party %}
<h2 class="chron-title" style="font-size:16px;margin-top:var(--sp-7);">The party</h2>
<ul class="chron-grid" role="list">
  {% for c in party %}
  <li class="chron-grid__item"><div class="chron-card">
    {% if c.portrait_url %}<img class="chron-portrait" src="{{ c.portrait_url }}" alt="" loading="lazy">{% else %}<span class="chron-monogram" aria-hidden="true">{{ (c.name or '?')[0]|upper }}</span>{% endif %}
    <div class="chron-card__body">
      <h3 class="chron-card__title">{{ c.name }}</h3>
      {% if c.tagline %}<p class="chron-card__epithet">{{ c.tagline }}</p>{% endif %}
    </div>
  </div></li>
  {% endfor %}
</ul>
{% endif %}
{% endblock %}
```

`chronicle_story.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">Story so far</h1>
{% if recaps %}
<ol class="chron-timeline">
  {% for r in recaps %}
  {% if r.chapter %}<li class="chron-chapter">{{ r.chapter }}</li>{% endif %}
  <li class="chron-timeline__item">
    <span class="chron-timeline__node" aria-hidden="true"></span>
    <article class="chron-prose">
      <h2>{{ r.title }}</h2>
      <p class="chron-card__meta">Session {{ r.session_number }}</p>
      {{ r.html|safe }}
    </article>
  </li>
  {% endfor %}
</ol>
{% else %}
<div class="chron-empty"><p>No sessions have been chronicled yet.</p></div>
{% endif %}
{% endblock %}
```

`chronicle_lore.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">Lore</h1>
{% if pages %}
<ul class="chron-grid" role="list">
  {% for p in pages %}
  <li class="chron-grid__item">
    <a class="chron-card" href="/chronicle/page/{{ p.slug }}">
      <span class="chron-monogram" aria-hidden="true">{{ (p.title or '?')[0]|upper }}</span>
      <div class="chron-card__body">
        <h2 class="chron-card__title">{{ p.title }}</h2>
        {% if p.epithet %}<p class="chron-card__epithet">{{ p.epithet }}</p>{% endif %}
        {% if p.tags %}<div class="chron-tags">{% for t in p.tags %}<span class="chron-pill">{{ t }}</span>{% endfor %}</div>{% endif %}
        {% if is_gm and p.recipient_label %}<p class="chron-card__meta"><span class="chron-pill chron-pill--restricted">Only {{ p.recipient_label }}</span></p>{% endif %}
      </div>
    </a>
  </li>
  {% endfor %}
</ul>
{% else %}
<div class="chron-empty"><p>No lore has been uncovered yet.</p></div>
{% endif %}
{% endblock %}
```

`chronicle_handouts.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">Handouts</h1>
{% if handouts %}
<div style="display:grid;gap:var(--sp-5);">
  {% for h in handouts %}
  <article class="chron-doc-frame">
    <h2 class="chron-card__title">{{ h.title }}</h2>
    {% if is_gm and h.recipient_label %}<span class="chron-pill chron-pill--restricted">Only {{ h.recipient_label }}</span>{% endif %}
    {% if h.image_url %}<img src="{{ h.image_url }}" alt="{{ h.title }}" loading="lazy" style="max-width:100%;height:auto;border-radius:var(--r-sm);margin-top:var(--sp-3);">{% endif %}
    {% if h.html %}<div class="chron-prose" style="border:none;background:none;padding:var(--sp-3) 0 0;max-width:65ch;">{{ h.html|safe }}</div>{% endif %}
  </article>
  {% endfor %}
</div>
{% else %}
<div class="chron-empty"><p>No handouts have been shared yet.</p></div>
{% endif %}
{% endblock %}
```

`chronicle_page.html`:
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<article class="chron-prose">
  <p class="chron-kicker">{{ page.section|capitalize }}</p>
  <h1>{{ page.title }}</h1>
  {% if page.epithet %}<p class="chron-card__epithet" style="font-size:15px;">{{ page.epithet }}</p>{% endif %}
  {{ page_html|safe }}
</article>

{% if backlinks %}
<aside class="chron-doc-frame" style="max-width:65ch;margin-top:var(--sp-5);">
  <p class="chron-kicker">Appears in</p>
  <ul class="chron-tags" role="list">
    {% for b in backlinks %}<li><a class="chron-pill" href="/chronicle/page/{{ b.slug }}">{{ b.title }}</a></li>{% endfor %}
  </ul>
</aside>
{% endif %}
{% endblock %}
```

`chronicle_journal.html` — folds in Notes; consumes the existing journal endpoint. No inline handlers (addEventListener only):
```jinja
{% extends "chronicle_base.html" %}
{% block chronicle %}
<h1 class="chron-title">My journal</h1>
<p class="chron-card__meta" style="margin-bottom:var(--sp-4);">Private to you. Only you can read these notes.</p>

<div class="chron-doc-frame" style="max-width:65ch;">
  <textarea id="chron-journal-text"
            style="width:100%;min-height:40vh;background:var(--bg-input);border:1px solid var(--border-card);border-radius:var(--r-sm);color:var(--text-on-cream);font-family:var(--font-flavor);font-size:16px;line-height:1.6;padding:var(--sp-4);resize:vertical;"
            placeholder="Write your thoughts on the campaign…"></textarea>
  <div style="display:flex;align-items:center;gap:var(--sp-3);margin-top:var(--sp-3);">
    <button type="button" id="chron-journal-save" class="chron-pill" style="cursor:pointer;padding:6px 14px;">Save</button>
    <span id="chron-journal-status" class="chron-card__meta" role="status" aria-live="polite"></span>
  </div>
</div>

<script>
(function () {
  var ta = document.getElementById('chron-journal-text');
  var btn = document.getElementById('chron-journal-save');
  var st = document.getElementById('chron-journal-status');
  if (!ta || !btn) return;
  // Load existing journal text. Endpoint shape confirmed with the routing slice.
  fetch('/api/journal').then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) { if (d && typeof d.text === 'string') ta.value = d.text; })
    .catch(function () {});
  btn.addEventListener('click', function () {
    st.textContent = 'Saving…';
    var fd = new FormData(); fd.append('text', ta.value);
    fetch('/api/journal', { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' }, body: fd })
      .then(function (r) { st.textContent = r.ok ? 'Saved.' : 'Save failed.'; })
      .catch(function () { st.textContent = 'Save failed.'; });
  });
})();
</script>
{% endblock %}
```

- [ ] **Run tests, expect PASS.** `pytest -q tests/test_chronicle_templates.py tests/test_inline_handler_escaping.py && python tools/check_templates.py`

- [ ] **Commit.** `git commit -am "Chronicle: Home, Story, Lore, Handouts, Journal, Page screens"`

---


> **Task 6 (nav swap) removed — owned by Part 5 per contract §3.** Also apply the two Part-4 amendments in contract §9 (empty-state body guard in `chronicle_base.html`; journal template server-renders `{{ notes }}` and saves via `POST /api/notes`).

## Open Questions (spec ambiguities for my slice)

1. **Head-seam ownership (`base.html`).** Task 3 adds `{% block head_extra %}` to `base.html` — the only clean way to load Alegreya, since `extra_head` is CSS-only inside a `<style>` and a late `@import` is dropped by the cascade. This edit is not listed in the shared contract's modify targets. If the routing/storage slice already adds a head seam, reuse its block name instead of `head_extra`. Recommend `head_extra` as the canonical name.
2. **Context field names.** My templates consume `nav.{story,lore,cast,handouts}`, per-page `portrait_url` + `recipient_label`, `latest_recap.{html,pull_quote}`, `recaps[].{title,session_number,html,chapter}`, `handouts[].{title,html,image_url,recipient_label}`, `page` + `page_html` + `backlinks[].{slug,title}`, `party[].{name,tagline,portrait_url}`, and `chronicle_published`. These must match exactly what the routing slice injects — this is the hard coordination surface. If the manifest carries different keys, adapt the routing-side view builders rather than the templates.
3. **`encounter_update` live-flag shape.** The banner script toggles on `d.active_name` present and `!d.ended`. The exact player-frame fields (`§4.6` / correction C8) are finalized in Phase 3; if the frame uses a different "combat active" signal, only the small script in `chronicle_base.html` changes.
4. **Journal endpoint contract.** `chronicle_journal.html` assumes `GET/POST /api/journal` with a `{text}` shape (design §5 says "reusing existing `/api/notes` + `/api/journal`"). Confirm the real shape with the routing slice; if it's `/api/notes` with a different field, update the two `fetch` calls only.
5. **Restricted-pill in legacy-open mode.** Per `§4.4`, non-`all` recipients are GM-only in legacy-open. Templates render the `chron-pill--restricted` "Only <name>" pill purely on `is_gm and recipient_label`; the routing slice must ensure players never receive restricted pages in the `pages`/`handouts` lists at all (the template is not the security boundary).

---

## Part 5: Reading Routes, Nav Gate & Context (reconciled)

> This part owns the `/chronicle*` GET routes, reader helpers, the `chronicle_published`
> context processor, and the nav-tab swap. It renders Part 4's templates through the
> **Template Context Contract** (reconciliation §7). It creates NO templates or CSS.
>
> **Depends on:** Part 1 (`_chronicle_manifest`, `_chronicle_content_dir`, `CHRONICLE_DIR`),
> Part 3 (`_chronicle_page_visible`, `_handout_visible_to_request`), Part 4 (all
> `chronicle_*.html` + `.chron-*` CSS). Reuses existing `_notes_owner()` /
> `_load_notes_text()` (app.py:8474-8486), `send_from_directory`, `abort`.
>
> **Part 4 amendment (required):** `chronicle_base.html`'s body must render a
> `.chron-empty` "The chronicle opens after your first session." message in place of
> `{% block chronicle %}` when `manifest` is falsy, so every reading route degrades to a
> friendly empty state and screen templates never dereference a None manifest:
> ```jinja
> <main class="chron-body">
>   {% if manifest %}{% block chronicle %}{% endblock %}
>   {% else %}<div class="chron-empty"><p>The chronicle opens after your first session.</p></div>{% endif %}
> </main>
> ```

**Files:**
- Modify: `app.py` — context processor after `_inject_campaign_chrome` (~app.py:5806); the reader helpers + routes as one labeled block after `serve_handout_image` (~app.py:15216).
- Modify: `templates/_player_nav.html` (lines 27-32), `templates/_cosmere_player_nav.html` (lines 27-32) — swap the Notes tab for a `chronicle_published`-gated Chronicle/Notes pair.
- Test: `tests/test_chronicle_reading.py`.

**Shared test harness** (top of `tests/test_chronicle_reading.py` — a subprocess runner + an on-disk content seeder that writes the exact shape Part 2 produces, so routes are testable without going through publish):
```python
"""Chronicle player reading routes + nav gate (PR1, Part 5). Subprocess isolation
with a throwaway DATA_DIR; GM_PASSWORD='' == legacy-open == caller is the GM."""
import os, sys, textwrap, subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


_SEED = '''
import os, json
def seed_chronicle(chronicle_dir, pages, *, session_number=3, html=None, assets=None):
    """Write chronicle_dir/current -> content/<h>/{manifest.json, html/<slug>.html, assets/*}."""
    h = 'deadbeef' * 8
    content = os.path.join(chronicle_dir, 'content', h)
    os.makedirs(os.path.join(content, 'html'), exist_ok=True)
    os.makedirs(os.path.join(content, 'assets'), exist_ok=True)
    manifest = {'schema_version': 1, 'session_number': session_number,
                'generated_at': '2026-07-15T00:00:00Z', 'pages': pages,
                'mysteries': [], 'calendar': {}, 'fieldguide': [], 'spine': []}
    with open(os.path.join(content, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)
    for slug, frag in (html or {}).items():
        with open(os.path.join(content, 'html', slug + '.html'), 'w') as f:
            f.write(frag)
    for rel, data in (assets or {}).items():
        with open(os.path.join(content, 'assets', rel), 'wb') as f:
            f.write(data)
    link = os.path.join(chronicle_dir, 'current'); tmp = link + '.tmp'
    if os.path.islink(tmp): os.unlink(tmp)
    os.symlink(content, tmp); os.replace(tmp, link)
    return content
'''
```

---

### Task 1: Context processor + nav-tab swap (empty-state gate)

**Files:** Modify `app.py` (~5806), `templates/_player_nav.html`, `templates/_cosmere_player_nav.html`; Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes `_chronicle_manifest()`; Produces context var `chronicle_published: bool`.

- [ ] **Write the failing test** (append after the harness):
```python
def test_nav_shows_notes_before_publish_and_chronicle_after():
    r = _run(_SEED + '''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        c = A.app.test_client()
        # pre-publish: Notes tab present, Chronicle tab absent
        pre = c.get('/notes').data
        assert b'>Notes<' in pre and b'>Chronicle<' not in pre, 'pre-publish nav wrong'
        # after a publish: Chronicle replaces Notes
        seed_chronicle(A.CHRONICLE_DIR, [{'slug':'home','section':'home','title':'Home','recipients':'all'}],
                       html={'home':'<p>hi</p>'})
        post = c.get('/chronicle').data
        assert b'>Chronicle<' in post and b'href="/notes"' not in post, 'post-publish nav wrong'
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```

- [ ] **Run it, expect FAIL:** `pytest -q tests/test_chronicle_reading.py::test_nav_shows_notes_before_publish_and_chronicle_after`
  Expected: fails — `chronicle_published` undefined so the `{% if %}` never shows Chronicle (and the `/chronicle` route 404s until Task 2). Depends on `A.CHRONICLE_DIR` (Part 1).

- [ ] **Minimal implementation.** In `app.py`, immediately after `_inject_campaign_chrome` (app.py:5806):
```python
@app.context_processor
def _inject_chronicle_ctx():
    """`chronicle_published` gates the player nav's Chronicle tab (empty-state):
    true once the first publish exists. One manifest probe per render."""
    return {'chronicle_published': _chronicle_manifest() is not None}
```
In `templates/_player_nav.html`, replace the Notes `<a>` (lines 27-32) with the gated pair:
```jinja
    {% if chronicle_published %}
    <a href="/chronicle"
       class="player-nav-tab{% if request.path.startswith('/chronicle') %} active{% endif %}"
       aria-label="Chronicle">
        <svg viewBox="0 0 24 24"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
        <span>Chronicle</span>
    </a>
    {% else %}
    <a href="/notes"
       class="player-nav-tab{% if active_player_tab == 'notes' %} active{% endif %}"
       aria-label="Session notes">
        <svg viewBox="0 0 24 24"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>
        <span>Notes</span>
    </a>
    {% endif %}
```
Apply the identical replacement to `templates/_cosmere_player_nav.html` (lines 27-32).

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py::test_nav_shows_notes_before_publish_and_chronicle_after && python tools/check_templates.py`
- [ ] **Commit:** `git commit -am "Chronicle: chronicle_published context processor + empty-state nav swap (Notes<->Chronicle)"`

---

### Task 2: Reader helpers + `/chronicle` Home

**Files:** Modify `app.py` (~15216); Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes `_chronicle_manifest`, `_chronicle_content_dir`, `_chronicle_page_visible`, `_handout_visible_to_request`, `_account_mode`, `_is_gm`, `_auth.current_user`; Produces `chronicle_asset_url` (template global), `_chronicle_visible_pages(section=None)`, `_chronicle_page_view(p)`, `_chronicle_fragment(slug)`, `_chronicle_nav_counts()`, `_chronicle_render(template, **ctx)`, `_chronicle_version()`, route `GET /chronicle`.

- [ ] **Write the failing test:**
```python
def test_home_renders_after_publish():
    r = _run(_SEED + '''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        seed_chronicle(A.CHRONICLE_DIR,
            [{'slug':'home','section':'home','title':'The Story So Far','recipients':'all'},
             {'slug':'s03','section':'recap','title':'Session 3','recipients':'all','session_updated':3}],
            html={'home':'<p>Home body.</p>', 's03':'<p>They fled north.</p>'})
        c = A.app.test_client()
        rv = c.get('/chronicle')
        assert rv.status_code == 200, rv.status_code
        assert b'They fled north.' in rv.data          # latest recap fragment injected
        assert b'As of Session 3' in rv.data           # session stamp from chronicle_base
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


def test_home_empty_state_when_unpublished():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        rv = A.app.test_client().get('/chronicle')
        assert rv.status_code == 200 and b'opens after your first session' in rv.data, rv.status_code
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```

- [ ] **Run it, expect FAIL:** `pytest -q tests/test_chronicle_reading.py::test_home_renders_after_publish` -> 404 (`/chronicle` route absent).

- [ ] **Minimal implementation.** In `app.py`, after `serve_handout_image` (~app.py:15216), add the labeled block:
```python
# ══════════════════════════════════════════════════════════════════════════
#  CHRONICLE — player reading routes (PR1). Pages are rendered to sanitized
#  HTML fragments at publish (Part 2); these routes are file reads + Jinja.
#  Access is gated by the /chronicle* player-scope before_request (Part 3);
#  per-page recipient scoping is server-side via _chronicle_page_visible.
# ══════════════════════════════════════════════════════════════════════════
_CHRONICLE_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,80}$')
_CHRONICLE_SECTION_TO_NAV = {'recap': 'story', 'lore': 'lore', 'cast': 'cast', 'handout': 'handouts'}


def _chronicle_version():
    """Per-publish cache token = the content-dir hash (new every publish), so
    assets are immutable+versioned (the SW pins unversioned assets forever)."""
    cdir = _chronicle_content_dir()
    return os.path.basename(cdir) if cdir else ''


@app.template_global()
def chronicle_asset_url(rel):
    """Versioned URL for a manifest-referenced asset; frontmatter stores
    'assets/<f>', the route serves from <content>/assets/<f>. None if empty."""
    from urllib.parse import quote
    rel = (rel or '').lstrip('/')
    if not rel:
        return None
    if rel.startswith('assets/'):
        rel = rel[len('assets/'):]
    return '/chronicle/assets/' + quote(rel) + '?v=' + (_chronicle_version() or '0')


def _chronicle_current_user():
    return _auth.current_user() if _account_mode() else None


def _chronicle_visible_pages(section=None):
    """Manifest pages this caller may see (recipient/ownership filter, Part 3),
    optionally limited to one manifest `section`."""
    man = _chronicle_manifest()
    if not man:
        return []
    user, gm = _chronicle_current_user(), _is_gm()
    return [p for p in man.get('pages', [])
            if (section is None or p.get('section') == section)
            and _chronicle_page_visible(p, user=user, is_gm=gm)]


def _chronicle_page_view(p):
    """Add the two route-computed presentation fields (contract §7) to a page."""
    recips = p.get('recipients', 'all')
    is_public = recips in ('all', None) or (isinstance(recips, (list, tuple)) and 'all' in recips)
    label = None
    if not is_public:
        label = ', '.join(recips) if isinstance(recips, (list, tuple)) else str(recips)
    v = dict(p)
    v['portrait_url'] = chronicle_asset_url(p['portrait']) if p.get('portrait') else None
    v['recipient_label'] = label
    return v


def _chronicle_fragment(slug):
    """Read a pre-rendered, already-sanitized html/<slug>.html fragment. Slug is
    regex-validated (no traversal); None if unpublished or missing."""
    cdir = _chronicle_content_dir()
    if not cdir or not _CHRONICLE_SLUG_RE.match(slug or ''):
        return None
    fpath = os.path.join(cdir, 'html', slug + '.html')
    if not os.path.isfile(fpath):
        return None
    try:
        with open(fpath, encoding='utf-8') as f:
            return f.read()
    except OSError:
        return None


def _chronicle_nav_counts():
    """Visible-page counts per sub-tab so chronicle_base shows a tab only when > 0."""
    counts = {'story': 0, 'lore': 0, 'cast': 0, 'handouts': 0}
    for p in _chronicle_visible_pages():
        key = _CHRONICLE_SECTION_TO_NAV.get(p.get('section'))
        if key:
            counts[key] += 1
    for h in HANDOUTS:
        if _handout_visible_to_request(h):
            counts['handouts'] += 1
    return counts


def _chronicle_render(template, **ctx):
    """Shared entry: every screen gets `manifest` + `nav`. When unpublished,
    chronicle_base shows the empty state and the screen block is skipped."""
    return render_template(template, manifest=_chronicle_manifest(),
                           nav=_chronicle_nav_counts(), **ctx)


@app.route('/chronicle')
def chronicle_home():
    pages = _chronicle_visible_pages()
    recaps = sorted((p for p in pages if p.get('section') == 'recap'),
                    key=lambda p: p.get('session_updated') or 0, reverse=True)
    latest = None
    if recaps:
        latest = _chronicle_page_view(recaps[0])
        latest['html'] = _chronicle_fragment(recaps[0]['slug'])
        latest['pull_quote'] = recaps[0].get('pull_quote')
    party = [{'name': v['title'], 'tagline': v.get('epithet'), 'portrait_url': v['portrait_url']}
             for v in (_chronicle_page_view(p) for p in pages if p.get('section') == 'cast')][:8]
    return _chronicle_render('chronicle_home.html', latest_recap=latest, party=party)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py -k home && python tools/check_templates.py`
- [ ] **Commit:** `git commit -am "Chronicle: reader helpers + /chronicle Home (contract-aligned context)"`

---

### Task 3: Section indexes `/chronicle/<story|lore|cast|handouts>` (+ recipient scoping)

**Files:** Modify `app.py` (chronicle block); Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes `_chronicle_visible_pages(section)`, `_chronicle_page_view`, `_chronicle_fragment`, `_handout_visible_to_request`; Produces routes `GET /chronicle/story|lore|cast|handouts`.

- [ ] **Write the failing test** (public vs recipient-scoped, account mode):
```python
def test_cast_index_scopes_recipients():
    r = _run(_SEED + '''
        import tempfile, os, json
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name':'Roshar','system':'pf2e'}).status_code == 302
        cid = [x for x in storage.list_campaign_ids() if campaigns.get_campaign(x)['name']=='Roshar'][0]
        assert c.post('/campaign/'+cid+'/activate').status_code == 302
        auth.create_user('shai','pw123456','Shai'); shai = auth.get_user_by_username('shai')
        campaigns.add_member(cid, shai['id'], 'player')
        pdir = storage.party_dir(cid); os.makedirs(pdir, exist_ok=True)
        doc = storage.wrap_character('c'*32, cid, 'pf2e', {'build':{'name':'Shallan'}}, owner_user_id=shai['id'])
        with open(os.path.join(pdir,'shallan.json'),'w') as f: json.dump(doc, f)
        seed_chronicle(storage.chronicle_dir(cid), [
            {'slug':'romi','section':'cast','title':'Romi','recipients':'all'},
            {'slug':'secret','section':'cast','title':'Kaladin-only','recipients':['kaladin']},
        ], html={'romi':'<p>x</p>','secret':'<p>y</p>'})
        # the GM (setup session) sees both
        both = c.get('/chronicle/cast').data
        assert b'Romi' in both and b'Kaladin-only' in both
        # Shallan's owner sees only the public card
        p = A.app.test_client()
        assert p.post('/login', data={'username':'shai','password':'pw123456'}).status_code == 302
        seen = p.get('/chronicle/cast').data
        assert b'Romi' in seen and b'Kaladin-only' not in seen
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```
*(Confirm `auth.create_user` / `campaigns.add_member` / `storage.wrap_character` signatures against core/ when implementing — grounded in Part 3.)*

- [ ] **Run it, expect FAIL:** 404 on `/chronicle/cast`.

- [ ] **Minimal implementation.** Add to the chronicle block:
```python
@app.route('/chronicle/<any(story,lore,cast,handouts):view>')
def chronicle_section(view):
    if view == 'story':
        recaps = sorted(_chronicle_visible_pages('recap'), key=lambda p: p.get('session_updated') or 0)
        rows = []
        for p in recaps:
            rows.append({'title': p.get('title'), 'session_number': p.get('session_updated'),
                         'html': _chronicle_fragment(p['slug']), 'chapter': p.get('chapter')})
        return _chronicle_render('chronicle_story.html', recaps=rows)
    if view == 'handouts':
        rows = [{'title': v['title'], 'html': _chronicle_fragment(v['slug']),
                 'image_url': v['portrait_url'], 'recipient_label': v['recipient_label']}
                for v in (_chronicle_page_view(p) for p in _chronicle_visible_pages('handout'))]
        # merge visible live handouts (image/title only in PR1; text handouts live in the vault)
        for h in HANDOUTS:
            if _handout_visible_to_request(h):
                rl = None if 'all' in (h.get('recipients') or []) else ', '.join(h.get('recipients') or [])
                rows.append({'title': h.get('title'), 'html': None,
                             'image_url': h.get('image_url'), 'recipient_label': rl})
        return _chronicle_render('chronicle_handouts.html', handouts=rows)
    section = 'cast' if view == 'cast' else 'lore'
    pages = sorted((_chronicle_page_view(p) for p in _chronicle_visible_pages(section)),
                   key=lambda v: (v.get('title') or '').lower())
    return _chronicle_render('chronicle_%s.html' % view, pages=pages)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py -k cast && python tools/check_templates.py`
- [ ] **Commit:** `git commit -am "Chronicle: Story/Lore/Cast/Handouts section indexes with server-side recipient scoping"`

---

### Task 4: Page detail `/chronicle/page/<slug>` (404 on hidden/unknown)

**Files:** Modify `app.py`; Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes `_chronicle_visible_pages()`, `_chronicle_page_view`, `_chronicle_fragment`; Produces route `GET /chronicle/page/<slug>`.

- [ ] **Write the failing test:**
```python
def test_page_detail_and_hidden_404():
    r = _run(_SEED + '''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        seed_chronicle(A.CHRONICLE_DIR, [{'slug':'romi','section':'cast','title':'Romi','recipients':'all'}],
                       html={'romi':'<h1>Romi</h1><p>The broker.</p>'})
        c = A.app.test_client()
        ok = c.get('/chronicle/page/romi')
        assert ok.status_code == 200 and b'The broker.' in ok.data
        assert c.get('/chronicle/page/nope').status_code == 404       # unknown == not discovered
        assert c.get('/chronicle/page/..%2f..%2fmanifest').status_code == 404
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```

- [ ] **Run it, expect FAIL:** route missing -> 404 for the ok case too.

- [ ] **Minimal implementation.** Add to the chronicle block:
```python
@app.route('/chronicle/page/<slug>')
def chronicle_page(slug):
    # Must be a VISIBLE manifest page AND have a fragment. Hidden/unknown -> 404
    # (recipient-scoped or unpublished pages must not leak their existence).
    match = next((p for p in _chronicle_visible_pages() if p.get('slug') == slug), None)
    frag = _chronicle_fragment(slug) if match else None
    if match is None or frag is None:
        abort(404)
    return _chronicle_render('chronicle_page.html', page=_chronicle_page_view(match),
                             page_html=frag, backlinks=match.get('backlinks') or [])
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py -k page_detail`
- [ ] **Commit:** `git commit -am "Chronicle: /chronicle/page/<slug> fragment detail, 404 on hidden/unknown"`

---

### Task 5: Journal `/chronicle/journal` (folded-in Notes)

**Files:** Modify `app.py`; Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes existing `_notes_owner()` + `_load_notes_text()` (app.py:8474-8486) and the existing `POST /api/notes`; Produces route `GET /chronicle/journal`.

- [ ] **Write the failing test:**
```python
def test_journal_reuses_notes_store():
    r = _run(_SEED + '''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        seed_chronicle(A.CHRONICLE_DIR, [{'slug':'home','section':'home','title':'H','recipients':'all'}], html={'home':'<p>x</p>'})
        c = A.app.test_client()
        assert c.post('/api/notes', json={'text':'my private theory'}).status_code == 200
        body = c.get('/chronicle/journal').data
        assert b'my private theory' in body      # same per-owner store as /notes
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```
*(Part 4's `chronicle_journal.html` loads/saves via the existing notes/journal endpoint using `addEventListener` — no inline handlers. If the endpoint shape differs from `{text}`, adjust only the template's two `fetch` calls; the route passes the current text as `notes`.)*

- [ ] **Run it, expect FAIL:** 404 on `/chronicle/journal`.

- [ ] **Minimal implementation.** Add to the chronicle block:
```python
@app.route('/chronicle/journal')
def chronicle_journal():
    return _chronicle_render('chronicle_journal.html', notes=_load_notes_text(_notes_owner()))
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py -k journal`
- [ ] **Commit:** `git commit -am "Chronicle: /chronicle/journal folds in the private Notes surface"`

---

### Task 6: Asset serving `/chronicle/assets/<path:asset>` (traversal guard + immutable cache)

**Files:** Modify `app.py`; Test `tests/test_chronicle_reading.py`
**Interfaces:** Consumes `_chronicle_content_dir()`; Produces route `GET /chronicle/assets/<path:asset>`.

- [ ] **Write the failing test:**
```python
def test_asset_serving_and_traversal_guard():
    r = _run(_SEED + '''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        seed_chronicle(A.CHRONICLE_DIR, [{'slug':'home','section':'home','title':'H','recipients':'all'}],
                       html={'home':'<p>x</p>'}, assets={'romi.png': b'PNGDATA'})
        c = A.app.test_client()
        ok = c.get('/chronicle/assets/romi.png')
        assert ok.status_code == 200 and ok.data == b'PNGDATA'
        assert 'max-age' in ok.headers.get('Cache-Control','')
        assert c.get('/chronicle/assets/..%2f..%2fmanifest.json').status_code == 404
        print('OK')
    ''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
```

- [ ] **Run it, expect FAIL:** 404 on the valid `romi.png` (route missing).

- [ ] **Minimal implementation.** Add to the chronicle block (confirm `send_from_directory` + `abort` are already imported at the top of `app.py` — they are, used by `serve_handout_image`; add `from werkzeug.exceptions import NotFound` if not present):
```python
@app.route('/chronicle/assets/<path:asset>')
def chronicle_asset(asset):
    """Serve a published asset from <content>/assets. send_from_directory
    safe-joins and raises NotFound on any traversal escape. Immutable long-cache;
    the URL carries the per-publish ?v=<hash>, so a new publish yields new URLs."""
    cdir = _chronicle_content_dir()
    if not cdir:
        abort(404)
    try:
        return send_from_directory(os.path.join(cdir, 'assets'), asset, max_age=31536000)
    except NotFound:
        abort(404)
```

- [ ] **Run, expect PASS:** `pytest -q tests/test_chronicle_reading.py`
- [ ] **Full Part-5 gate + commit:** `pytest -q tests/test_chronicle_reading.py && python tools/check_templates.py`
  `git commit -am "Chronicle: versioned asset serving with path-traversal guard"`

---

---

## Task Map

| Part | Deliverable | Tasks | Test file |
|---|---|---|---|
| 1 | Storage: `chronicle_dir` dual-bind, symlink swap, rollback | 5 | `tests/test_chronicle_storage.py` |
| 2 | Publish: ingest, leak scan, markdown render, status, rollback, fixture | 7 | `tests/test_chronicle_publish.py` |
| 3 | Auth: player-scope gate, ownership recipients, handout-leak fix | 5 | `tests/test_chronicle_auth.py` |
| 4 | Templates + `.chron-*` CSS + Alegreya head-seam + inline-handler ban | 5 | `tests/test_chronicle_templates.py` |
| 5 | Reading routes, nav swap, context processor | 6 | `tests/test_chronicle_reading.py` |

**Natural review checkpoints** (each is green + Railway-verifiable on its own): after Part 2 (GM can publish + `status` shows it, no player UI yet), after Part 3 (recipient boundary + handout-leak fix landed), and after Part 5 (players can read the hub). All merge to `main` behind the empty-state gate — the tab is invisible until the GM's first publish.

## Self-Review

**Spec coverage (CHRONICLE_DESIGN.md PR1 row + MVP scope) -> task:**
- Ingest / staging swap / manifest validation / renderer -> Part 2 T5 (+ T2 leak, T3 render, T1 gate).
- `chronicle_dir` dual-bind + symlink swap + rollback -> Part 1 T1-T5.
- python-markdown reuse -> Part 2 T3 (Global Constraints).
- Non-blocking ingest -> Part 2 T3 (`_chronicle_coop_yield`) + T5 (temp-file, bounded batches).
- Leak-check refusal (`[!danger]` -> 400) -> Part 2 T2 + T5.
- Auth prefix (`/api/chronicle` in `GM_API_PREFIXES`) -> Part 2 T1.
- Player-scope gate -> Part 3 T3.
- Ownership-keyed recipients + 404 -> Part 3 T1/T4, Part 5 T3/T4.
- Handout-leak fix -> Part 3 T2.
- Home / Story / Lore / Cast / Handouts / Journal -> Part 5 T2/T3/T5 + Part 4 T4/T5.
- Handouts merge (published + live) -> Part 5 T3.
- Notes folds into Journal -> Part 5 T1 (nav) + T5 (route) + Part 4 (template).
- Nav tab both systems + empty-state gate -> Part 5 T1.
- Asset serving + traversal guard + cache token -> Part 5 T6 + T2 (`chronicle_asset_url`).
- Warm-dark restraint / dial-down -> Part 4 T2 CSS.
- Inline-handler ban extended to `chronicle*.html` -> Part 4 T1.
- Rollback endpoint -> Part 2 T7 (route) + Part 1 T5 (logic).
- System-agnostic (PF2e + Cosmere) -> content routes never gate on `_active_system()`; nav swap edits both nav partials.

**Placeholder scan:** no "TBD/TODO/implement later" in task bodies; every code step shows real code. Two intentional confirm-at-implementation notes (`auth.create_user`/`add_member`/`wrap_character` signatures in Part 3/5 tests; the journal endpoint field shape) are grounded and flagged, not placeholders.

**Type/name consistency (post-reconciliation):** `.chron-*` prefix everywhere (§2); `chronicle_published` is the single nav-gate flag (§4); slugs match `^[a-z0-9][a-z0-9-]{0,80}$` and the fragment filename == the manifest slug on both the write (Part 2) and read (Part 5) side (§6); `_chronicle_page_visible(page_meta, *, user, is_gm)` has one signature used by Parts 3+5; `_chronicle_swap(staging_dir, new_hash)` MOVES staging (Part 1) and Part 2 hands off ownership accordingly.

## Open decisions for you (not blocking — pick during or after PR1)

1. **Cosmere handout ownership.** `_user_owns_pc` scans only the PF2e party dir, so account-mode ownership of a live handout addressed to a *Cosmere* PC resolves False (Chronicle *pages* already work for Cosmere via `_chronicle_owned_pc_slugs`). Options: extend `_user_owns_pc` to also scan `cosmere_pc_dir` (touches a snapshot-guarded function), or have the handout check fall back to slug matching. Recommend deferring to a small follow-up unless your Cosmere table uses targeted handouts now.
2. **Live handout text in the Handouts screen.** PR1 shows published handout *pages* (vault-authored letters, sanitized) as full text, and live `handouts.json` entries as image+title only (safe, no raw HTML injection). If you want live text handouts rendered inline too, that's a small Phase-2 add (escape + wrap their `content`).
3. **Publish response: 200 vs 202.** Plan ships the synchronous, cooperatively-yielding 200 (deterministic, testable; `gevent.sleep(0)` between every file/page keeps the worker responsive). The 202 + background-greenlet variant is only worth it if a single publish's render is long even with yields — not the case at a 4-player page count. Say the word if you want 202.
4. **Rollback depth = 1.** `current` <-> `previous` single-level undo (matches "one-click rollback"). If you want multi-step history, the prune keeps last N and `previous` becomes a small stack.
5. **Markdown sanitize depth.** Dependency-free regex scrub (scripts/styles/`on*=`/`javascript:`) as a backstop under the vault firewall + leak scan. If you want allow-list sanitizing, adding `bleach` is a requirements decision.

## Not in PR1 (deferred, per the design)
Atlas, Mysteries, backlinks generation, client-side search, new-dots/`reader_state`, and the live layer (in-scene badges, `chronicle_scene`, Field Guide, calendar) are Phases 2-3. The manifest already reserves `mysteries[]`/`fieldguide[]`/`spine[]`/`backlinks` so those light up without a data migration.
