# Chronicle PR0 (Vault Publish Pipeline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Obsidian-side publish pipeline that derives a small, spoiler-safe **Player Vault** from the GM's 1.1 GB Obsidian vault and POSTs it to the already-built PR1 app — making Chronicle actually usable. Selection model: **auto-propose + review** (the tool proposes from metadata the GM already maintains; the GM reviews before publish).

**Architecture:** A deterministic Python build tool (`tools/chronicle_build.py`, runs on the GM's Mac) walks the GM vault, auto-proposes player-facing entities from session-note `npcs_encountered`/`areas_covered` metadata, strips GM-only callouts per the vault's own `_Conventions.md` taxonomy (`[!danger]`/`[!info]`/`[!tip]`/`[!warning]` out; `[!quote]`/`[!example]` kept; `[!check]`/`[!question]` harvested into Mysteries; `[!abstract]` seeds recaps), resolves wikilinks, builds a `manifest.json` + `content/<slug>.md` + `assets/`, hard-fails if any `[!danger]` survives (the firewall), zips, and POSTs to `/api/chronicle/publish`. A thin `/publish-chronicle` Cowork skill wraps it with optional AI enrichment (drafts player-safe epithets/recaps for review) and the review-then-approve step.

**Tech Stack:** Python stdlib only for the build tool (no PyYAML — a small frontmatter subset parser; `urllib` for the POST; optional `Pillow` for EXIF/resize, degrades gracefully if absent). Reuses the PR1 app endpoints. Cowork skill = markdown.

## Global Constraints (every task inherits these — copied verbatim from the contract)

- **The Player Vault emits MARKDOWN, not HTML.** Keep `[!quote]`/`[!example]` Obsidian callouts intact — the PR1 app renders + sanitizes them at publish (`_chronicle_render_markdown` → `.chron-callout-quote`/`.chron-doc-frame`).
- **The firewall is the point.** `strip_gm_content` removes `[!danger]`/`[!info]`/`[!tip]`/`[!warning]` + `%%obsidian comments%%` + `<!-- html comments -->`; `leak_check` re-scans the WHOLE emitted vault and the build **ABORTS (never zips/publishes) if any `[!danger]`/`[!secret]`/`[!gm]` survives.** The PR1 app re-scans at ingest (defense in depth).
- **Slugs MUST match `^[a-z0-9][a-z0-9-]{0,80}$`** (PR1's `_chronicle_validate_manifest` rejects otherwise; the fragment filename == the slug). `section` ∈ {home, recap, cast, atlas, lore, handout, fieldguide}.
- **Manifest shape (PR1 contract):** `{schema_version: 1, campaign_id, session_number, generated_at, pages: [{slug, section, title, source, recipients, ...}], mysteries: [], calendar: {}, fieldguide: [], spine: []}`. Zip = `manifest.json` at archive ROOT + `content/**` + `assets/**`, cap 48 MB.
- **Default-EXCLUDE.** A note becomes a player page ONLY via auto-propose (encountered in a completed session) or explicit `chronicle: true`; `chronicle: false` force-excludes. `Player Handouts/**` is always included (secret-free by the vault's own README rule).
- **No app runtime deps.** `tools/chronicle_build.py` is a build tool (runs on the Mac), NOT shipped to the single-gevent-worker app — stdlib-only, with optional `Pillow` via `requirements-dev.txt` only. Do NOT touch the app's `requirements.txt`.
- **Test imports:** `from tools import chronicle_build as cb` (tools/ resolves as a namespace package from repo root, like the existing `tools/check_templates.py`). Tests are pure (no network, no AI); the `publish()` POST is tested with a monkeypatched `urlopen`.
- No emojis anywhere. Commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. TDD. CI: `pytest -q`.

## Reconciliation notes (parallel-drafted slices; these govern conflicts)
- **Execution order:** Part A1 → A2 → A3 (build `tools/chronicle_build.py` bottom-up) → Part B (auth token, independent) → Part C (skill + scaffold + smoke test, depends on the CLI existing).
- **One module, three drafting slices:** A1/A2/A3 all build `tools/chronicle_build.py` and share one `tests/test_chronicle_build.py` (append per task). All slices use the exact contract signatures (`parse_note`, `slugify`, `strip_gm_content`, `select_entities`, `resolve_wikilinks`, `build_backlinks`, `build_manifest`, `collect_assets`, `leak_check`, `build_player_vault`, `make_zip`, `publish`, `main`).
- **Auth:** the `X-Chronicle-Token` header + `CHRONICLE_PUBLISH_TOKEN` env, scoped to the `/api/chronicle` prefix in `check_gm_access`. Local legacy-open dev needs no token; prod (GM_PASSWORD set) uses it.
- **AI enrichment (Part C skill) runs ABOVE the deterministic firewall** — any AI-drafted epithet/recap still passes `leak_check` before publish, and AI drafts are `status: draft` for GM review. The deterministic core (Parts A) produces a valid, spoiler-safe vault with NO AI.

---

---

## Part A: `tools/chronicle_build.py` — the deterministic build core (firewall)

### A1 — Fixture, parsing, and the callout firewall

TDD tasks for the deterministic spoiler-firewall core of `tools/chronicle_build.py`. All pure-Python, no network, no AI. Import convention in tests: `from tools import chronicle_build as cb` (tools/ resolves as a PEP-420 namespace package from repo root, same as the existing `tools/check_templates.py`).

---

### Task: Synthetic mini-GM-vault fixture

**Files:**
- Create `tests/fixtures/gm_vault_sample/Sessions/Session - April 21 2026.md`
- Create `tests/fixtures/gm_vault_sample/NPCs/Romi Bracken.md`
- Create `tests/fixtures/gm_vault_sample/NPCs/Alzira Vane.md`
- Create `tests/fixtures/gm_vault_sample/Areas/C2 Intake Entrance.md`
- Create `tests/fixtures/gm_vault_sample/Player Handouts/Letters & Journals/Romi's Note.md`
- Create `tests/fixtures/gm_vault_sample/Player Handouts/_README.md`
- Test `tests/test_chronicle_build.py` (fixture-integrity test only, no `cb` import yet)

**Interfaces:** Consumes — nothing (committed static files). Produces — the fixture tree every later A1 test derives from. Coverage per contract: a completed session note with `npcs_encountered`/`areas_covered` + `[!abstract]`; an NPC note mixing `[!danger]`/`[!info]`/`[!warning]`/`[!quote]`/`[!check]`/`[!question]` + `%%..%%` + `<!--..-->`; a location note; a Player Handouts/Letters note; a `chronicle: false` force-exclude note; planted `[!danger]` blocks for the (later-slice) leak_check.

- [ ] Step: Write the failing test

```python
# tests/test_chronicle_build.py
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "gm_vault_sample"


def test_fixture_vault_is_present_and_shaped():
    assert FIXTURE.is_dir()
    session = FIXTURE / "Sessions" / "Session - April 21 2026.md"
    romi = FIXTURE / "NPCs" / "Romi Bracken.md"
    alzira = FIXTURE / "NPCs" / "Alzira Vane.md"
    area = FIXTURE / "Areas" / "C2 Intake Entrance.md"
    letter = FIXTURE / "Player Handouts" / "Letters & Journals" / "Romi's Note.md"
    for p in (session, romi, alzira, area, letter):
        assert p.is_file(), p

    session_text = session.read_text(encoding="utf-8")
    assert "session_number: 5" in session_text
    assert "npcs_encountered: [Romi Bracken, Cult Patrol Guards]" in session_text
    assert "[!abstract]" in session_text

    romi_text = romi.read_text(encoding="utf-8")
    # the NPC note must exercise every firewall branch
    for token in ("[!danger]", "[!info]", "[!warning]", "[!quote]",
                  "[!check]", "[!question]", "[!example]", "%%", "<!--"):
        assert token in romi_text, token

    assert "chronicle: false" in alzira.read_text(encoding="utf-8")
    assert "[!danger]" in area.read_text(encoding="utf-8")  # planted for leak_check
```

- [ ] Step: Run it, expect FAIL

```bash
pytest -q tests/test_chronicle_build.py::test_fixture_vault_is_present_and_shaped
```

Expected: `AssertionError` on `assert FIXTURE.is_dir()` (the fixture tree does not exist yet).

- [ ] Step: Minimal implementation — create the fixture files verbatim.

`tests/fixtures/gm_vault_sample/Sessions/Session - April 21 2026.md`
```markdown
---
type: session_notes
session_number: 5
date: 2026-04-21
chapter: 3
book: 1
areas_covered: [C2, C3, C11]
npcs_encountered: [Romi Bracken, Cult Patrol Guards]
pcs_present: [Kyle, Amadeus, Gavin, Goel]
status: completed
tags: [session, notes, book1, chapter3]
---

# Session 5 — Into the Intake

> [!abstract] Previously On
> The party breached the Intake Entrance and met Romi Bracken at the glowing door.

The party descended into C2 and pressed toward the blue-lit door.

> [!quote] Read Aloud
> The cavern opens into a wide chamber lit by cold blue lamps.

> [!danger] GM Secret
> The lamps are azlanti tech keyed to the keystone Romi carries.
```

`tests/fixtures/gm_vault_sample/NPCs/Romi Bracken.md`
```markdown
---
type: npc
name: "Romi Bracken"
role: "Cult leader (revealed S4)"
status: active
chronicle: true
tags: [npc, cult, book1, recurring]
---

# Romi Bracken

> [!info] Stat Strip
> Lvl 5 | AC 22 | HP 70

A warm shopkeeper with an easy smile and a good word for everyone.

> [!quote] Recruitment Pitch
> "You'll make incredible soldiers for a cause greater than yourselves."

> [!danger] True Motive
> Romi serves Camazotz and intends to sacrifice the party at the door.

> [!check] Confirmed
> The party knows Romi runs the Intake and greeted them by name.

> [!question] Suspected
> The party suspects Romi is hiding something behind the sealed door.

> [!warning] At the table
> If Amadeus touches the cult symbol, escalate the temptation.

%%GM note: reroll his reaction if the party opens hostile%%

<!-- internal: cross-link this to the Camazotz arc file -->

> [!example] Handout Fragment
> A pressed flower and a note reading "Come find me."
```

`tests/fixtures/gm_vault_sample/NPCs/Alzira Vane.md`
```markdown
---
type: npc
name: "Alzira Vane"
role: "Deep-cover contact"
status: active
chronicle: false
tags: [npc, book1]
---

# Alzira Vane

> [!quote] Greeting
> "You didn't hear this from me."
```

`tests/fixtures/gm_vault_sample/Areas/C2 Intake Entrance.md`
```markdown
---
type: location
area_code: C2
name: "Intake Entrance"
chapter: 3
book: 1
tags: [location, book1, chapter3]
---

# C2 Intake Entrance

> [!quote] First Impression
> Stone steps descend toward a closed door etched with spread wings.

> [!info] History
> Built by the azlanti as a containment vault long before the cult arrived.

> [!danger] Hidden
> The keystone opens a sub-level the party has not yet found.
```

`tests/fixtures/gm_vault_sample/Player Handouts/Letters & Journals/Romi's Note.md`
```markdown
---
type: handout
title: "A Note from Romi"
---

# A Note from Romi

> [!example] Letter
> Come find me at the blue door. There is a place for you here. — R
```

`tests/fixtures/gm_vault_sample/Player Handouts/_README.md`
```markdown
---
type: reference
title: "Player Handouts — secret-free by rule"
---

Everything under `Player Handouts/` is safe to share with players verbatim.
```

- [ ] Step: Run tests, expect PASS

```bash
pytest -q tests/test_chronicle_build.py::test_fixture_vault_is_present_and_shaped
```

- [ ] Step: Commit

```bash
git add tests/fixtures/gm_vault_sample tests/test_chronicle_build.py
git commit -m "Chronicle PR0: synthetic GM-vault fixture for the firewall tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task: `parse_note` + stdlib frontmatter subset parser

**Files:** Create `tools/chronicle_build.py`; Modify `tests/test_chronicle_build.py`.

**Interfaces:**
- Produces `parse_note(path) -> dict` = `{"frontmatter": dict, "body": str, "path": str}`. Splits a leading `---`YAML`---` block from the body; tolerates a note with no frontmatter (returns `{}` + full text). Frontmatter parsed by a stdlib-only subset parser (no PyYAML — keeps the tool stdlib-only per contract): scalars (str/int/bool/null), quoted strings, inline flow lists `[a, b]`, and `- item` block lists.

- [ ] Step: Write the failing test

```python
# append to tests/test_chronicle_build.py
from tools import chronicle_build as cb


def test_parse_note_splits_frontmatter_and_body():
    note = cb.parse_note(FIXTURE / "NPCs" / "Romi Bracken.md")
    fm = note["frontmatter"]
    assert fm["type"] == "npc"
    assert fm["name"] == "Romi Bracken"            # quotes stripped
    assert fm["role"] == "Cult leader (revealed S4)"
    assert fm["chronicle"] is True                 # bool coercion
    assert fm["tags"] == ["npc", "cult", "book1", "recurring"]  # flow list
    assert note["body"].startswith("\n# Romi Bracken") or \
           note["body"].lstrip().startswith("# Romi Bracken")
    assert note["path"].endswith("Romi Bracken.md")


def test_parse_note_coerces_ints_and_flow_lists():
    fm = cb.parse_note(FIXTURE / "Sessions" / "Session - April 21 2026.md")["frontmatter"]
    assert fm["session_number"] == 5               # int, not "5"
    assert fm["areas_covered"] == ["C2", "C3", "C11"]
    assert fm["npcs_encountered"] == ["Romi Bracken", "Cult Patrol Guards"]
    assert fm["status"] == "completed"


def test_parse_note_tolerates_missing_frontmatter(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("# Just a heading\n\nno yaml here\n", encoding="utf-8")
    note = cb.parse_note(p)
    assert note["frontmatter"] == {}
    assert note["body"] == "# Just a heading\n\nno yaml here\n"


def test_parse_note_supports_block_lists(tmp_path):
    p = tmp_path / "block.md"
    p.write_text("---\ntags:\n  - alpha\n  - beta\n---\nbody\n", encoding="utf-8")
    fm = cb.parse_note(p)["frontmatter"]
    assert fm["tags"] == ["alpha", "beta"]
```

- [ ] Step: Run it, expect FAIL

```bash
pytest -q tests/test_chronicle_build.py -k parse_note
```

Expected: `ModuleNotFoundError: No module named 'tools.chronicle_build'`.

- [ ] Step: Minimal implementation

```python
# tools/chronicle_build.py
"""Chronicle PR0 build tool: derive a spoiler-safe player vault from the GM
Obsidian vault. Runs on the GM's Mac. Stdlib-only core (optional Pillow later);
NOT imported by the Flask app.
"""
import re
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")
_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


def _coerce_scalar(v):
    v = v.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _split_flow(s):
    parts, cur, quote = [], "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _parse_frontmatter(text):
    data, key = {}, None
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        item = _ITEM_RE.match(raw)
        if item and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_coerce_scalar(item.group(1)))
            continue
        kv = _KV_RE.match(raw)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val == "":
                data[key] = ""  # may be overwritten by a following block list
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[key] = [_coerce_scalar(x) for x in _split_flow(inner)] if inner else []
            else:
                data[key] = _coerce_scalar(val)
    return data


def parse_note(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = {}, text
    m = _FRONTMATTER_RE.match(text)
    if m:
        frontmatter = _parse_frontmatter(m.group(1))
        body = m.group(2)
    return {"frontmatter": frontmatter, "body": body, "path": str(path)}
```

- [ ] Step: Run tests, expect PASS

```bash
pytest -q tests/test_chronicle_build.py -k parse_note
```

- [ ] Step: Commit

```bash
git add tools/chronicle_build.py tests/test_chronicle_build.py
git commit -m "Chronicle PR0: parse_note + stdlib frontmatter subset parser

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task: `slugify`

**Files:** Modify `tools/chronicle_build.py`; Modify `tests/test_chronicle_build.py`.

**Interfaces:** Produces `slugify(title) -> str`. Lowercase, `[^a-z0-9]+` -> `-`, strip leading/trailing `-`, truncate to the PR1 length ceiling. Guarantees a match of `^[a-z0-9][a-z0-9-]{0,80}$` (PR1's `_chronicle_validate_manifest` rejects otherwise); returns `"page"` for empty/unsluggable input.

- [ ] Step: Write the failing test

```python
# append to tests/test_chronicle_build.py
import re as _re

SLUG_RE = _re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def test_slugify_basic():
    assert cb.slugify("C2 Intake Entrance") == "c2-intake-entrance"
    assert cb.slugify("Romi Bracken") == "romi-bracken"


def test_slugify_strips_punctuation_and_apostrophes():
    assert cb.slugify("Go'el, the Warpriest!") == "go-el-the-warpriest"
    assert cb.slugify("  --Letters & Journals--  ") == "letters-journals"


def test_slugify_empty_and_symbol_only_fall_back_to_page():
    assert cb.slugify("") == "page"
    assert cb.slugify("!!!") == "page"
    assert cb.slugify(None) == "page"


def test_slugify_always_matches_pr1_pattern():
    for title in ["C2 Intake Entrance", "Romi Bracken", "!!!", "",
                  "x" * 200, "9 Lives", "-leading-dash-"]:
        assert SLUG_RE.match(cb.slugify(title)), title
```

- [ ] Step: Run it, expect FAIL

```bash
pytest -q tests/test_chronicle_build.py -k slugify
```

Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'slugify'`.

- [ ] Step: Minimal implementation

```python
# add to tools/chronicle_build.py
_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def slugify(title):
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")[:81].strip("-")
    if not s or not _SLUG_OK.match(s):
        return "page"
    return s
```

- [ ] Step: Run tests, expect PASS

```bash
pytest -q tests/test_chronicle_build.py -k slugify
```

- [ ] Step: Commit

```bash
git add tools/chronicle_build.py tests/test_chronicle_build.py
git commit -m "Chronicle PR0: slugify with PR1-safe slug guarantee

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task: `strip_gm_content` — the spoiler firewall

**Files:** Modify `tools/chronicle_build.py`; Modify `tests/test_chronicle_build.py`.

**Interfaces:** Produces `strip_gm_content(body) -> dict` = `{"player_body": str, "mysteries": [{"kind": "fact"|"question", "text": str}], "recap_seed": str|None}`. Walks callout blocks per the contract policy table:
- STRIP entirely: `[!danger]`, `[!info]`, `[!tip]`, `[!warning]` — and, firewall-conservatively, any callout kind not on an explicit allowlist (so a stray `[!secret]`/`[!gm]` never survives).
- KEEP verbatim (Obsidian callout syntax intact, for PR1's renderer): `[!quote]`, `[!example]`.
- HARVEST out of the body: `[!check]` -> `{"kind": "fact"}`, `[!question]` -> `{"kind": "question"}`.
- PULL into `recap_seed`: `[!abstract]`.
- Also strip Obsidian `%%comments%%` and HTML `<!--comments-->` anywhere.

This is the primary firewall — a false negative leaks, so the strip assertions are exhaustive.

- [ ] Step: Write the failing test

```python
# append to tests/test_chronicle_build.py
def _romi_body():
    return cb.parse_note(FIXTURE / "NPCs" / "Romi Bracken.md")["body"]


def test_strip_removes_all_gm_callouts():
    out = cb.strip_gm_content(_romi_body())
    pb = out["player_body"]
    # [!danger] gone, content and marker both
    assert "[!danger]" not in pb
    assert "sacrifice the party" not in pb
    assert "Camazotz" not in pb
    # [!info] gone
    assert "[!info]" not in pb
    assert "AC 22" not in pb
    # [!warning] gone
    assert "[!warning]" not in pb
    assert "escalate the temptation" not in pb


def test_strip_keeps_player_callouts_verbatim():
    out = cb.strip_gm_content(_romi_body())
    pb = out["player_body"]
    # quote kept with its callout syntax intact for the PR1 renderer
    assert "> [!quote] Recruitment Pitch" in pb
    assert "incredible soldiers for a cause greater than yourselves" in pb
    # example kept
    assert "> [!example] Handout Fragment" in pb
    assert "A pressed flower" in pb
    # plain narration outside any callout survives
    assert "A warm shopkeeper with an easy smile" in pb


def test_strip_harvests_check_and_question_to_mysteries():
    out = cb.strip_gm_content(_romi_body())
    kinds = {(m["kind"]) for m in out["mysteries"]}
    assert "fact" in kinds and "question" in kinds
    fact = next(m for m in out["mysteries"] if m["kind"] == "fact")
    question = next(m for m in out["mysteries"] if m["kind"] == "question")
    assert "runs the Intake" in fact["text"]
    assert "hiding something behind the sealed door" in question["text"]
    # harvested callouts are removed from the player body
    assert "[!check]" not in out["player_body"]
    assert "[!question]" not in out["player_body"]


def test_strip_pulls_abstract_into_recap_seed():
    session_body = cb.parse_note(
        FIXTURE / "Sessions" / "Session - April 21 2026.md")["body"]
    out = cb.strip_gm_content(session_body)
    assert out["recap_seed"] is not None
    assert "breached the Intake Entrance and met Romi Bracken" in out["recap_seed"]
    assert "[!abstract]" not in out["player_body"]
    # the danger block in the same note is still stripped
    assert "azlanti tech" not in out["player_body"]
    assert "[!danger]" not in out["player_body"]


def test_strip_removes_obsidian_and_html_comments():
    out = cb.strip_gm_content(_romi_body())
    assert "%%" not in out["player_body"]
    assert "reroll his reaction" not in out["player_body"]
    assert "<!--" not in out["player_body"]
    assert "cross-link this to the Camazotz arc" not in out["player_body"]


def test_strip_unknown_callout_is_dropped_by_default():
    body = "> [!secret] hush\n> the vault code is 1234\n\nvisible line\n"
    out = cb.strip_gm_content(body)
    assert "1234" not in out["player_body"]
    assert "[!secret]" not in out["player_body"]
    assert "visible line" in out["player_body"]


def test_strip_no_recap_returns_none():
    out = cb.strip_gm_content("plain body, no callouts\n")
    assert out["recap_seed"] is None
    assert out["mysteries"] == []
    assert "plain body" in out["player_body"]
```

- [ ] Step: Run it, expect FAIL

```bash
pytest -q tests/test_chronicle_build.py -k strip
```

Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'strip_gm_content'`.

- [ ] Step: Minimal implementation

```python
# add to tools/chronicle_build.py
_OBSIDIAN_COMMENT = re.compile(r"%%.*?%%", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CALLOUT_MARKER = re.compile(
    r"^>\s*\[!(?P<kind>[A-Za-z]+)\][-+]?\s?(?P<title>.*?)\s*$")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")

_KEEP_KINDS = {"quote", "example"}       # kept verbatim, callout syntax intact
_HARVEST = {"check": "fact", "question": "question"}
# every other kind (danger/info/tip/warning + any unknown) is stripped


def strip_gm_content(body):
    body = _OBSIDIAN_COMMENT.sub("", body)
    body = _HTML_COMMENT.sub("", body)
    lines = body.split("\n")
    out_lines, mysteries, recap_seed = [], [], None
    i, n = 0, len(lines)
    while i < n:
        m = _CALLOUT_MARKER.match(lines[i])
        if not m:
            out_lines.append(lines[i])
            i += 1
            continue
        kind = m.group("kind").lower()
        title = m.group("title").strip()
        block = [lines[i]]
        j = i + 1
        while j < n and lines[j].startswith(">"):
            block.append(lines[j])
            j += 1
        content = "\n".join(
            (_QUOTE_LINE.match(b).group(1) if _QUOTE_LINE.match(b) else b)
            for b in block[1:]
        ).strip()
        harvest_text = content if content else title

        if kind in _KEEP_KINDS:
            out_lines.extend(block)              # verbatim
        elif kind == "abstract":
            seed = " ".join(x for x in (title, content) if x).strip()
            recap_seed = seed or None
        elif kind in _HARVEST:
            if harvest_text:
                mysteries.append({"kind": _HARVEST[kind], "text": harvest_text})
        # else: danger/info/tip/warning/unknown -> dropped entirely
        i = j

    player_body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return {
        "player_body": player_body + "\n" if player_body else "",
        "mysteries": mysteries,
        "recap_seed": recap_seed,
    }
```

- [ ] Step: Run tests, expect PASS

```bash
pytest -q tests/test_chronicle_build.py
```

- [ ] Step: Commit

```bash
git add tools/chronicle_build.py tests/test_chronicle_build.py
git commit -m "Chronicle PR0: strip_gm_content spoiler firewall (strip/keep/harvest/recap)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Open questions

1. **Unknown callout default = strip.** The contract's policy table enumerates only the nine taxonomy callouts. I made any un-listed callout kind (e.g. a stray `[!secret]`/`[!gm]`/`[!note]`) **strip** rather than keep, because "a false negative leaks" and PR1's ingest leak-scan 400s on `[!secret]`/`[!gm]`. Downside: a GM who invents a *new player-safe* callout would see it silently dropped. If that's undesirable, flip the default to keep-and-log — but that widens the firewall. Recommend confirming strip-by-default.

2. **Frontmatter parser is a hand-rolled stdlib subset, not PyYAML.** Per the contract constraint ("stdlib + optional Pillow; do NOT add app runtime deps"), I avoided importing `yaml`. The subset covers scalars, quoted strings, inline flow lists, and `- ` block lists — everything the real session/NPC/location frontmatter uses. It does **not** handle nested maps, multi-line `>`/`|` block scalars, or anchors. If real GM frontmatter uses any of those, we either extend the parser or reconsider taking a PyYAML dev-dependency. Worth spot-checking against the live 1.1 GB vault's actual frontmatter before locking.

3. **`recap_seed` from multiple `[!abstract]` blocks.** Current code keeps the *last* abstract encountered in a note. Session notes have exactly one, so this is fine for the fixture, but if a note ever carries two, the earlier is dropped silently. Flag for the `select_entities`/`build_player_vault` slice (B/later-A) to decide whether recap seeds should concatenate across a session note.

4. **Wikilinks / `%%comment%%` inside a kept `[!quote]`.** Comments are stripped globally before callout-walking, so a `%%...%%` inside a quote block is removed — correct for secrecy. But wikilinks inside kept callouts are left intact here (they're resolved later by `resolve_wikilinks`, a separate slice). Noting the ordering dependency: `strip_gm_content` must run before wikilink resolution, and the assembled plan should sequence them that way.

### A2 — Auto-propose selection, wikilinks, manifest

I have the spec and conventions. My slice is A2 (select_entities, resolve_wikilinks, build_backlinks, build_manifest). These are pure deterministic functions in `tools/chronicle_build.py`. I'll write the TDD task plan now.

---

# Chronicle PR0 — Slice A2 TDD Plan: auto-propose selection + wikilink resolution + manifest

**Scope:** four pure-Python deterministic functions in `tools/chronicle_build.py`, each with a real unit test (no network, no AI). Exact signatures from `PR0_CONTRACT.md` §"Module design".

**Cross-slice dependencies (from A1, assumed already landed in the assembled plan):**
- `parse_note(path) -> {frontmatter, body, path}` — consumed by `select_entities`.
- `slugify(title) -> str` — not called directly here, but slugs my tests assert against are produced upstream.
- The synthetic fixture `tests/fixtures/gm_vault_sample/**` (A1). To keep A2's tests independent of A1's exact NPC names and free of ordering coupling, my `select_entities` unit test builds its own tiny vault under `tmp_path`; a second assertion runs against the shared fixture only if it exists (skipped otherwise). This is a deliberate deviation from "test against the A1 fixture" — noted under Open questions.

All four functions land in the same module A1 creates. Every implementation step notes the module-top imports/constants it needs; add them once (idempotent).

---

### Task: `select_entities` — union encountered NPCs/areas across completed sessions, honor `chronicle:` overrides

**Files:**
- Modify: `tools/chronicle_build.py`
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Consumes: `parse_note(path) -> dict` (A1); walks `*.md` under `vault_dir`.
- Produces: `select_entities(vault_dir) -> {"npcs": set[str], "areas": set[str], "sessions": [note dicts sorted by session_number]}`

- [ ] **Step: Write the failing test**

```python
# tests/test_chronicle_build.py
import textwrap
import pytest
from tools import chronicle_build as cb


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text), encoding="utf-8")


def _make_vault(tmp_path):
    # Two completed sessions (out of number order on disk) + one in_progress.
    _write(tmp_path / "Session - April 21 2026.md", """\
        ---
        type: session_notes
        session_number: 5
        status: completed
        npcs_encountered: [Romi Bracken, Cult Patrol Guards]
        areas_covered: [C2, C3]
        ---
        > [!abstract] The party met Romi at the intake door.
        """)
    _write(tmp_path / "Session - April 14 2026.md", """\
        ---
        type: session_notes
        session_number: 4
        status: complete
        npcs_encountered: [Alzira]
        areas_covered: [C1]
        ---
        Body.
        """)
    _write(tmp_path / "Session - April 28 2026.md", """\
        ---
        type: session_notes
        session_number: 6
        status: in_progress
        npcs_encountered: [The Hidden Patron]
        areas_covered: [C9]
        ---
        Body.
        """)
    # NPC force-excluded even though encountered.
    _write(tmp_path / "NPCs" / "Alzira.md", """\
        ---
        type: npc
        name: Alzira
        chronicle: false
        ---
        Body.
        """)
    # NPC force-included even though never encountered.
    _write(tmp_path / "NPCs" / "Old Salk.md", """\
        ---
        type: npc
        name: Old Salk
        chronicle: true
        ---
        Body.
        """)
    # Location force-included by area_code.
    _write(tmp_path / "Areas" / "C11 Sky Dock.md", """\
        ---
        type: location
        area_code: C11
        name: Sky Dock
        chronicle: true
        ---
        Body.
        """)
    return tmp_path


def test_select_entities_proposes_encountered_and_honors_overrides(tmp_path):
    result = cb.select_entities(_make_vault(tmp_path))

    # Encountered-in-completed-session NPCs are proposed.
    assert "Romi Bracken" in result["npcs"]
    assert "Cult Patrol Guards" in result["npcs"]
    # chronicle:false force-excludes even though encountered.
    assert "Alzira" not in result["npcs"]
    # chronicle:true force-includes even though never encountered.
    assert "Old Salk" in result["npcs"]
    # in_progress session contributes nothing.
    assert "The Hidden Patron" not in result["npcs"]

    assert "C2" in result["areas"] and "C3" in result["areas"]
    assert "C1" in result["areas"]          # from the other completed session
    assert "C11" in result["areas"]         # force-included location
    assert "C9" not in result["areas"]      # in_progress session excluded

    # Only completed sessions, sorted by session_number.
    nums = [n["frontmatter"]["session_number"] for n in result["sessions"]]
    assert nums == [4, 5]
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py::test_select_entities_proposes_encountered_and_honors_overrides`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'select_entities'`

- [ ] **Step: Minimal implementation**

```python
# tools/chronicle_build.py  (ensure these are at module top)
import os


def _iter_markdown(vault_dir):
    for root, _dirs, files in os.walk(str(vault_dir)):
        for name in files:
            if name.endswith(".md"):
                yield os.path.join(root, name)


def _is_completed(status):
    return str(status or "").strip().lower() in ("complete", "completed")


def select_entities(vault_dir):
    npcs, areas, sessions = set(), set(), []
    inc_npc, exc_npc, inc_area, exc_area = set(), set(), set(), set()

    for path in _iter_markdown(vault_dir):
        note = parse_note(path)
        fm = note.get("frontmatter") or {}
        ntype = fm.get("type")

        if ntype == "session_notes":
            if _is_completed(fm.get("status")):
                sessions.append(note)
                for n in fm.get("npcs_encountered") or []:
                    npcs.add(str(n).strip())
                for a in fm.get("areas_covered") or []:
                    areas.add(str(a).strip())

        elif ntype == "npc":
            name = str(fm.get("name") or "").strip()
            ch = fm.get("chronicle")
            if name and ch is True:
                inc_npc.add(name)
            elif name and ch is False:
                exc_npc.add(name)

        elif ntype == "location":
            code = str(fm.get("area_code") or fm.get("name") or "").strip()
            ch = fm.get("chronicle")
            if code and ch is True:
                inc_area.add(code)
            elif code and ch is False:
                exc_area.add(code)

    npcs = (npcs | inc_npc) - exc_npc
    areas = (areas | inc_area) - exc_area
    sessions.sort(key=lambda note: note.get("frontmatter", {}).get("session_number", 0))
    return {"npcs": npcs, "areas": areas, "sessions": sessions}
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py::test_select_entities_proposes_encountered_and_honors_overrides`

- [ ] **Step: Commit**
  `git commit -m "Chronicle PR0: select_entities auto-proposes encountered NPCs/areas + chronicle overrides" --trailer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: `resolve_wikilinks` — `[[X]]`/`[[X|alt]]` to page links or plain text; `![[img]]` to image or stripped

**Files:**
- Modify: `tools/chronicle_build.py`
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Consumes: `body: str`, `title_to_slug: dict` mapping **published page title -> slug** and **copied asset filename -> asset path** (the two membership sets; see Open questions).
- Produces: `resolve_wikilinks(body, title_to_slug) -> str`
  - `[[X]]` / `[[X|alt]]` -> `[display](/chronicle/page/<slug>)` when `X` is a published title; else the plain `display` text (unlinked text is the not-discovered signal).
  - `![[img.png]]` -> `![img.png](<path>)` only when `img.png` is a copied asset; else the embed is stripped.

- [ ] **Step: Write the failing test**

```python
def test_resolve_wikilinks_published_unpublished_and_embeds():
    title_to_slug = {
        "Romi Bracken": "romi-bracken",
        "map.png": "assets/map.png",
    }
    body = (
        "You meet [[Romi Bracken]] at the door.\n"
        "She serves [[The Hidden Patron|a shadowy master]].\n"
        "See [[Romi Bracken|the recruiter]] again.\n"
        "![[map.png]]\n"
        "![[secret-gm-diagram.png]]\n"
    )
    out = cb.resolve_wikilinks(body, title_to_slug)

    # Published title -> link, display defaults to the title.
    assert "[Romi Bracken](/chronicle/page/romi-bracken)" in out
    # Aliased published title -> link with the alias as display.
    assert "[the recruiter](/chronicle/page/romi-bracken)" in out
    # Unpublished target -> plain display text, NO link syntax, NO raw wikilink.
    assert "a shadowy master" in out
    assert "The Hidden Patron" not in out
    assert "/chronicle/page/the-hidden-patron" not in out
    assert "[[" not in out
    # Copied asset embed -> markdown image.
    assert "![map.png](assets/map.png)" in out
    # Un-copied asset embed -> stripped entirely.
    assert "secret-gm-diagram" not in out
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py::test_resolve_wikilinks_published_unpublished_and_embeds`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'resolve_wikilinks'`

- [ ] **Step: Minimal implementation**

```python
# tools/chronicle_build.py  (ensure `import re` at module top)
_WIKILINK_RE = re.compile(r"!?\[\[([^\]]+?)\]\]")


def resolve_wikilinks(body, title_to_slug):
    def _repl(m):
        raw = m.group(0)
        target, _sep, alt = m.group(1).partition("|")
        target = target.strip()
        display = alt.strip() if alt else target
        if raw.startswith("!"):  # embed
            if target in title_to_slug:
                return "![{}]({})".format(target, title_to_slug[target])
            return ""
        if target in title_to_slug:
            return "[{}](/chronicle/page/{})".format(display, title_to_slug[target])
        return display

    return _WIKILINK_RE.sub(_repl, body)
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py::test_resolve_wikilinks_published_unpublished_and_embeds`

- [ ] **Step: Commit**
  `git commit -m "Chronicle PR0: resolve_wikilinks maps published links, degrades unpublished to plain text, gates embeds on copied assets" --trailer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: `build_backlinks` — invert the resolved outbound-link map

**Files:**
- Modify: `tools/chronicle_build.py`
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Consumes: `pages` — a list of page dicts, each `{slug, title, body}` where `body` is already run through `resolve_wikilinks` (so outbound links appear as `/chronicle/page/<slug>`).
- Produces: `build_backlinks(pages) -> dict[slug, list[{"slug", "title"}]]` — for each page, who links to it (deduped, self-links ignored).

- [ ] **Step: Write the failing test**

```python
def test_build_backlinks_two_page_cross_link():
    pages = [
        {"slug": "romi-bracken", "title": "Romi Bracken",
         "body": "Leader at [C2 Intake](/chronicle/page/c2-intake)."},
        {"slug": "c2-intake", "title": "C2 Intake",
         "body": "Watched over by [Romi](/chronicle/page/romi-bracken). "
                 "See also [Romi again](/chronicle/page/romi-bracken)."},
    ]
    back = cb.build_backlinks(pages)

    # c2-intake is linked from romi-bracken.
    assert back["c2-intake"] == [{"slug": "romi-bracken", "title": "Romi Bracken"}]
    # romi-bracken is linked from c2-intake, deduped despite two references.
    assert back["romi-bracken"] == [{"slug": "c2-intake", "title": "C2 Intake"}]


def test_build_backlinks_ignores_self_and_unknown_targets():
    pages = [
        {"slug": "loop", "title": "Loop",
         "body": "self [x](/chronicle/page/loop) and [ghost](/chronicle/page/nope)."},
    ]
    back = cb.build_backlinks(pages)
    assert back == {"loop": []}
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k build_backlinks`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'build_backlinks'`

- [ ] **Step: Minimal implementation**

```python
# tools/chronicle_build.py  (ensure `import re` at module top)
_PAGE_LINK_RE = re.compile(r"/chronicle/page/([a-z0-9][a-z0-9-]{0,80})")


def build_backlinks(pages):
    title_by_slug = {p["slug"]: p.get("title", p["slug"]) for p in pages}
    backlinks = {p["slug"]: [] for p in pages}
    seen = {p["slug"]: set() for p in pages}
    for p in pages:
        src = p["slug"]
        for target in _PAGE_LINK_RE.findall(p.get("body") or ""):
            if target in backlinks and target != src and src not in seen[target]:
                backlinks[target].append({"slug": src, "title": title_by_slug[src]})
                seen[target].add(src)
    return backlinks
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k build_backlinks`

- [ ] **Step: Commit**
  `git commit -m "Chronicle PR0: build_backlinks inverts the resolved outbound-link map" --trailer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: `build_manifest` — emit the exact PR1 manifest shape; validate slugs + sections

**Files:**
- Modify: `tools/chronicle_build.py`
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Consumes: `campaign_id: str`, `session_number: int`, `pages: list[dict]` (each carries `slug/section/title/source/recipients` + optional `epithet/tags/session_introduced/session_updated/portrait/pull_quote/chapter/backlinks`), `mysteries: list`, `spine: list`, `calendar: dict`.
- Produces: `build_manifest(...) -> dict` with top-level `{schema_version: 1, campaign_id, session_number, generated_at, pages, mysteries, calendar, fieldguide: [], spine}`. Raises `ValueError` on a slug not matching `^[a-z0-9][a-z0-9-]{0,80}$` or a `section` outside the allowed set (fail early rather than let PR1's `_chronicle_validate_manifest` 400).

- [ ] **Step: Write the failing test**

```python
def test_build_manifest_shape_and_validation():
    pages = [
        {"slug": "romi-bracken", "section": "cast", "title": "Romi Bracken",
         "source": "content/romi-bracken.md", "recipients": "all",
         "epithet": "The Recruiter", "tags": ["cult"],
         "session_introduced": 4, "portrait": "assets/romi.png",
         "backlinks": [{"slug": "c2-intake", "title": "C2 Intake"}]},
        {"slug": "c2-intake", "section": "atlas", "title": "C2 Intake",
         "source": "content/c2-intake.md", "recipients": ["kyle"]},
    ]
    manifest = cb.build_manifest(
        campaign_id="shades-of-blood", session_number=5,
        pages=pages, mysteries=[{"kind": "fact", "text": "known"}],
        spine=[{"session": 4}, {"session": 5}], calendar={"era": "AR"},
    )

    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "shades-of-blood"
    assert manifest["session_number"] == 5
    assert manifest["calendar"] == {"era": "AR"}
    assert manifest["fieldguide"] == []
    assert manifest["spine"] == [{"session": 4}, {"session": 5}]
    assert manifest["mysteries"] == [{"kind": "fact", "text": "known"}]
    # generated_at is an ISO-8601 Z timestamp.
    assert manifest["generated_at"].endswith("Z") and "T" in manifest["generated_at"]

    slug_re = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
    allowed = {"home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"}
    for pg in manifest["pages"]:
        assert slug_re.match(pg["slug"])
        assert pg["section"] in allowed
        assert set(("slug", "section", "title", "source", "recipients")) <= set(pg)

    # Optional fields present when supplied, absent when not.
    romi = next(p for p in manifest["pages"] if p["slug"] == "romi-bracken")
    assert romi["epithet"] == "The Recruiter"
    assert romi["backlinks"] == [{"slug": "c2-intake", "title": "C2 Intake"}]
    c2 = next(p for p in manifest["pages"] if p["slug"] == "c2-intake")
    assert "epithet" not in c2 and "portrait" not in c2
    assert c2["recipients"] == ["kyle"]


def test_build_manifest_rejects_bad_slug_and_section():
    with pytest.raises(ValueError):
        cb.build_manifest("c", 1, [{"slug": "Bad Slug", "section": "cast",
                                    "title": "x", "recipients": "all"}], [], [], {})
    with pytest.raises(ValueError):
        cb.build_manifest("c", 1, [{"slug": "ok", "section": "spoilers",
                                    "title": "x", "recipients": "all"}], [], [], {})
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k build_manifest`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'build_manifest'`

- [ ] **Step: Minimal implementation**

```python
# tools/chronicle_build.py  (ensure `import re` and `from datetime import datetime, timezone` at module top)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_SECTIONS = frozenset(("home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"))
_PAGE_OPTIONAL = (
    "epithet", "tags", "session_introduced", "session_updated",
    "portrait", "pull_quote", "chapter", "backlinks",
)


def build_manifest(campaign_id, session_number, pages, mysteries, spine, calendar):
    out_pages = []
    for p in pages:
        slug = p.get("slug", "")
        if not _SLUG_RE.match(slug or ""):
            raise ValueError("invalid slug: {!r}".format(slug))
        section = p.get("section")
        if section not in _SECTIONS:
            raise ValueError("invalid section {!r} for slug {!r}".format(section, slug))
        entry = {
            "slug": slug,
            "section": section,
            "title": p.get("title", slug),
            "source": p.get("source", "content/{}.md".format(slug)),
            "recipients": p.get("recipients", "all"),
        }
        for key in _PAGE_OPTIONAL:
            if p.get(key) is not None:
                entry[key] = p[key]
        out_pages.append(entry)

    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "session_number": session_number,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": out_pages,
        "mysteries": list(mysteries or []),
        "calendar": dict(calendar or {}),
        "fieldguide": [],
        "spine": list(spine or []),
    }
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k build_manifest`

- [ ] **Step: Commit**
  `git commit -m "Chronicle PR0: build_manifest emits schema_version 1 shape, validates slugs and sections" --trailer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: Full-slice green + Jinja/template guard (integration checkpoint)

**Files:** none new (verification only).

- [ ] **Step: Run the whole build-tool test module**
  `pytest -q tests/test_chronicle_build.py`
  Expect: all A2 tests plus any A1/A3 tests in the file pass.

- [ ] **Step: Run the full suite to confirm no import-time regressions**
  `pytest -q`

- [ ] **Step: Commit (only if anything changed; otherwise skip)**
  `git commit -m "Chronicle PR0: A2 selection/wikilink/manifest slice green" --trailer "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Open questions (ambiguities in the contract for this slice)

1. **`status` value: `complete` vs `completed`.** `PR0_CONTRACT.md` line 40 says `status: complete|in_progress`; the real `obsidian_vault/_Conventions.md` says `status: in_progress | completed`. I implemented `_is_completed` to accept **both** (`complete`/`completed`, case-insensitive) so the tool matches the GM's actual vault. Confirm no third spelling is used in the live vault.

2. **`resolve_wikilinks` embed membership set.** The signature is fixed to `(body, title_to_slug)`, but the contract also says embeds resolve "only if that asset is in the copied set." With no separate asset-set parameter, I made `title_to_slug` the single membership map holding **both** page-title->slug and asset-filename->asset-path entries; embeds emit `![fname](title_to_slug[fname])`. If A3's orchestrator instead wants a separate `asset_set` argument, the signature must change — flagging so the assembled plan stays consistent. (Disambiguation between a page link and an image is by the `!` prefix, so overloading the dict is safe.)

3. **`build_backlinks` input contract.** I assume each page dict carries a resolved `body` (post-`resolve_wikilinks`) and backlinks are derived by scanning for `/chronicle/page/<slug>`. If A3 instead threads an explicit per-page `links` list, `build_backlinks` should invert that list directly. Either is a one-line change; needs to be pinned when A3's `build_player_vault` is written.

4. **NPC/area name keys vs encountered-list keys.** `npcs_encountered` uses display names (`Romi Bracken`) while `areas_covered` uses area codes (`C2`); `chronicle:` overrides on NPC notes key off `name`, on location notes off `area_code`. `select_entities` therefore returns npcs keyed by name and areas keyed by code. If the live vault ever lists an area by name in `areas_covered` while the location note only has `area_code`, the override won't match — worth a normalization pass in A3 if it occurs.

5. **`fieldguide` top-level.** `build_manifest` hard-codes `fieldguide: []` (no parameter for it in the fixed signature) even though `fieldguide` is also a valid page `section`. Confirm PR1 populates the field-guide screen from `pages[].section == "fieldguide"` rather than the top-level `fieldguide` array; if the top-level array must carry entries, `build_manifest` needs a new parameter.

### A3 — Assets, leak-check, orchestration, zip, publish, CLI

Read the contract and conventions. My slice (A3) is pure orchestration/IO on top of the A1 parse/select/strip and A2 resolve/backlinks/manifest functions, all in the same `tools/chronicle_build.py`. Here is the TDD plan for slice A3.

---

# Chronicle PR0 — Slice A3: assets, leak firewall, orchestration, zip, publish, CLI

**Scope:** `collect_assets`, `leak_check`, `build_player_vault`, `make_zip`, `publish`, `main` — all in `tools/chronicle_build.py`. This slice owns the **ABORT-on-leak guarantee**: no zip, no publish, ever, when `leak_check` is non-empty.

**Depends on (Consumes from sibling slices — assumed present in the assembled module):**
- A1: `parse_note(path) -> {frontmatter, body, path}`, `slugify(title) -> str`, `strip_gm_content(body) -> {player_body, mysteries, recap_seed}`, `select_entities(vault_dir) -> {npcs, areas, sessions}`, and the fixture tree `tests/fixtures/gm_vault_sample/**`.
- A2: `resolve_wikilinks(body, title_to_slug) -> str`, `build_backlinks(pages) -> dict`, `build_manifest(campaign_id, session_number, pages, mysteries, spine, calendar) -> dict`.

**Ordering note:** Tasks A3.1, A3.2, A3.4, A3.5, A3.6-guards are self-contained unit tests (no A1/A2 needed). Task A3.3 (`build_player_vault`) and the CLI happy-path (A3.6) are integration and green only once A1+A2 land; sequence this slice after A1/A2 in the assembled plan.

---

### Task: A3.1 — collect_assets (copy player images, strip EXIF, budget)

**Files:**
- Modify: `tools/chronicle_build.py` (add module header + `collect_assets` and helpers)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Produces: `collect_assets(pages, vault_dir, out_assets_dir) -> list[str]` — copies referenced player images (`Player Handouts/**` portraits & maps) into `out_assets_dir`, strips EXIF when Pillow importable (else copies as-is), enforces a <48 MB total budget (log + skip oversize), returns sorted copied basenames. A `page` dict carries an optional `portrait` and a `body` markdown string; refs are the union of `portrait`, Obsidian `![[embed]]`, and markdown `![alt](path)`.

- [ ] **Step: Write the failing test**

```python
# tests/test_chronicle_build.py
import os
import sys
import io
import struct
import zipfile
import pathlib

import pytest

import tools.chronicle_build as cb


def _write_png(path, size_bytes=0):
    # Minimal 1x1 PNG; pad with a trailing tEXt-ish filler chunk to hit a size.
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d4944415478da6360000002000100057b8fe30000000049454e44ae426082"
    )
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(png)
        if size_bytes:
            f.write(b"\x00" * size_bytes)


def test_collect_assets_copies_referenced_skips_unreferenced(tmp_path):
    vault = tmp_path / "vault"
    portraits = vault / "Player Handouts" / "NPC Portraits"
    _write_png(portraits / "romi.png")
    _write_png(portraits / "unused.png")

    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "romi", "body": "Portrait: ![Romi](assets/romi.png)"}]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    assert (out_assets / "romi.png").exists()
    assert not (out_assets / "unused.png").exists()


def test_collect_assets_reads_embed_and_portrait_field(tmp_path):
    vault = tmp_path / "vault"
    maps = vault / "Player Handouts" / "Maps"
    _write_png(maps / "intake.png")
    portraits = vault / "Player Handouts" / "NPC Portraits"
    _write_png(portraits / "alzira.png")

    out_assets = tmp_path / "out" / "assets"
    pages = [
        {"slug": "intake", "body": "![[Maps/intake.png]]"},
        {"slug": "alzira", "portrait": "NPC Portraits/alzira.png", "body": ""},
    ]

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["alzira.png", "intake.png"]


def test_collect_assets_pillow_absent_still_copies(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "NPC Portraits" / "romi.png")
    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "romi", "body": "![[romi.png]]"}]

    # Force `from PIL import Image` to raise ImportError.
    monkeypatch.setitem(sys.modules, "PIL", None)

    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == ["romi.png"]
    assert (out_assets / "romi.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_collect_assets_skips_oversize_over_budget(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    _write_png(vault / "Player Handouts" / "Maps" / "big.png", size_bytes=1024)
    out_assets = tmp_path / "out" / "assets"
    pages = [{"slug": "big", "body": "![[big.png]]"}]

    monkeypatch.setattr(cb, "ASSET_BUDGET_BYTES", 100)  # smaller than the file
    copied = cb.collect_assets(pages, str(vault), str(out_assets))

    assert copied == []
    assert not (out_assets / "big.png").exists()
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k collect_assets`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'collect_assets'` (or `ASSET_BUDGET_BYTES`).

- [ ] **Step: Minimal implementation**

```python
# tools/chronicle_build.py
"""Chronicle PR0 build tool: derive a spoiler-safe player vault from the GM vault.

Runs on the GM's Mac (full vault access). Stdlib-only for the app; optionally uses
Pillow (dev-only) to strip image EXIF, degrading to a plain copy if absent.
"""
import argparse
import json
import logging
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
import uuid
import zipfile

log = logging.getLogger("chronicle_build")

ASSET_BUDGET_BYTES = 48 * 1024 * 1024  # keep the whole archive under the PR1 48 MB cap
PLAYER_HANDOUTS = "Player Handouts"
_IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_EMBED_RE = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _iter_asset_refs(pages):
    for page in pages:
        portrait = page.get("portrait")
        if portrait:
            yield portrait
        body = page.get("body") or ""
        for m in _EMBED_RE.finditer(body):
            yield m.group(1)
        for m in _MD_IMG_RE.finditer(body):
            yield m.group(1)


def _find_asset(vault_dir, base):
    handouts = os.path.join(vault_dir, PLAYER_HANDOUTS)
    for root, _dirs, files in os.walk(handouts):
        if base in files:
            return os.path.join(root, base)
    return None


def _strip_exif(src, dst):
    try:
        from PIL import Image  # dev-only; absent in the app runtime
    except ImportError:
        shutil.copy2(src, dst)
        return
    try:
        img = Image.open(src)
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(dst)
    except Exception:  # any decode/save failure -> keep the pixels, drop the metadata concern
        shutil.copy2(src, dst)


def collect_assets(pages, vault_dir, out_assets_dir):
    os.makedirs(out_assets_dir, exist_ok=True)
    copied = []
    seen = set()
    total = 0
    for ref in _iter_asset_refs(pages):
        base = os.path.basename(ref.strip())
        if not base or base in seen:
            continue
        if os.path.splitext(base)[1].lower() not in _IMG_EXTS:
            continue
        src = _find_asset(vault_dir, base)
        if not src:
            log.warning("chronicle: referenced asset not found: %s", base)
            continue
        size = os.path.getsize(src)
        if total + size > ASSET_BUDGET_BYTES:
            log.warning("chronicle: asset budget exceeded, skipping %s (%d bytes)", base, size)
            continue
        _strip_exif(src, os.path.join(out_assets_dir, base))
        seen.add(base)
        total += size
        copied.append(base)
    return sorted(copied)
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k collect_assets`

- [ ] **Step: Commit**
  `git commit -m "chronicle: collect_assets copies player images, strips EXIF, enforces budget

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: A3.2 — leak_check (the spoiler firewall)

**Files:**
- Modify: `tools/chronicle_build.py` (add `leak_check`)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Produces: `leak_check(out_dir) -> list[str]` — re-scans the WHOLE emitted player vault (every `.md` + `manifest.json`) for `[!danger]` / `[!secret]` / `[!gm]`. Non-empty return means a spoiler survived → caller MUST abort (never zip/publish). Returns sorted `"<relpath>: [!<kind>]"` offenders.

- [ ] **Step: Write the failing test**

```python
def test_leak_check_clean_tree_returns_empty(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "romi.md").write_text(
        "> [!quote] Read aloud\n> The door opens.\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    assert cb.leak_check(str(out)) == []


def test_leak_check_catches_planted_danger(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "romi.md").write_text(
        "Intro text\n\n> [!danger] Romi is the cult leader\n> secret motive\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"schema_version": 1, "pages": []}', encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert offenders == ["content/romi.md: [!danger]"]


def test_leak_check_catches_secret_and_gm_including_manifest(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "content" / "a.md").write_text("> [!secret] hidden\n", encoding="utf-8")
    (out / "manifest.json").write_text('{"note": "[!gm] leaked into manifest"}', encoding="utf-8")

    offenders = cb.leak_check(str(out))
    assert "content/a.md: [!secret]" in offenders
    assert "manifest.json: [!gm]" in offenders
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k leak_check`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'leak_check'`.

- [ ] **Step: Minimal implementation**

```python
_LEAK_RE = re.compile(r"\[!\s*(danger|secret|gm)\b", re.IGNORECASE)


def leak_check(out_dir):
    """Re-scan the emitted player vault for GM spoiler callouts. Non-empty == ABORT."""
    offenders = []
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            if not (fn.endswith(".md") or fn == "manifest.json"):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            rel = os.path.relpath(path, out_dir).replace(os.sep, "/")
            for m in _LEAK_RE.finditer(text):
                offenders.append("%s: [!%s]" % (rel, m.group(1).lower()))
    return sorted(offenders)
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k leak_check`

- [ ] **Step: Commit**
  `git commit -m "chronicle: leak_check re-scans emitted vault for surviving GM callouts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: A3.3 — build_player_vault (orchestration)

**Files:**
- Modify: `tools/chronicle_build.py` (add `build_player_vault` + selection/section helpers + `_review_summary`)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Consumes: `parse_note`, `slugify`, `strip_gm_content`, `select_entities` (A1); `resolve_wikilinks`, `build_backlinks`, `build_manifest` (A2).
- Produces: `build_player_vault(vault_dir, out_dir, campaign_id) -> {manifest, review_summary}` — orchestrates select → strip → resolve → assets → backlinks → manifest; writes `out_dir/{manifest.json, content/<slug>.md, assets/**}`; returns the manifest and a human review summary (counts + page/mystery list + unmatched entities).

- [ ] **Step: Write the failing test** (end-to-end against the A1 fixture)

```python
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "gm_vault_sample")


def test_build_player_vault_end_to_end(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")

    manifest = result["manifest"]
    assert manifest["schema_version"] == 1
    assert manifest["campaign_id"] == "shades-of-blood"
    assert isinstance(result["review_summary"], str) and result["review_summary"]

    # The encountered NPC (Romi) became a cast page with a safe slug.
    slugs = {p["slug"] for p in manifest["pages"]}
    assert "romi-bracken" in slugs
    for p in manifest["pages"]:
        assert re.match(r"^[a-z0-9][a-z0-9-]{0,80}$", p["slug"])
        assert p["section"] in {"home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"}

    # GM content is GONE from every emitted content file.
    content_dir = out / "content"
    joined = "\n".join(
        (content_dir / f).read_text(encoding="utf-8") for f in os.listdir(content_dir))
    assert "[!danger]" not in joined
    assert "[!info]" not in joined      # info is GM-only per policy
    assert "cult leader" not in joined.lower()   # planted spoiler string in the fixture danger block
    assert "[!quote]" in joined         # player-facing read-aloud preserved

    # The firewall agrees the tree is clean.
    assert cb.leak_check(str(out)) == []


def test_build_player_vault_harvests_mysteries(tmp_path):
    out = tmp_path / "out"
    result = cb.build_player_vault(FIXTURE, str(out), campaign_id="shades-of-blood")
    kinds = {m["kind"] for m in result["manifest"]["mysteries"]}
    assert "fact" in kinds        # from [!check]
    assert "question" in kinds    # from [!question]
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k build_player_vault`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'build_player_vault'`.

- [ ] **Step: Minimal implementation**

```python
def _note_title(note):
    fm = note["frontmatter"]
    return fm.get("name") or fm.get("title") or \
        os.path.splitext(os.path.basename(note["path"]))[0]


def _is_handout(path):
    p = path.replace(os.sep, "/")
    return ("/" + PLAYER_HANDOUTS + "/") in p or p.split("/")[0] == PLAYER_HANDOUTS


def _section_for(note):
    p = note["path"].replace(os.sep, "/")
    if _is_handout(p):
        return "lore" if "/Lore Pages/" in p else "handout"
    ntype = note["frontmatter"].get("type")
    if ntype == "npc":
        return "cast"
    if ntype == "location":
        return "atlas"
    return "lore"


def _select_pages(notes, selection):
    """Return (included_notes, unmatched_entities). Default-exclude; chronicle: overrides win."""
    npc_names = {n.lower() for n in selection["npcs"]}
    area_codes = {a.lower() for a in selection["areas"]}
    included, matched_npcs, matched_areas = [], set(), set()
    for note in notes:
        fm = note["frontmatter"]
        if fm.get("chronicle") is False:
            continue
        if _is_handout(note["path"]):
            included.append(note)
            continue
        ntype = fm.get("type")
        if ntype == "npc":
            name = (fm.get("name") or _note_title(note)).lower()
            if fm.get("chronicle") is True or name in npc_names:
                included.append(note)
                matched_npcs.add(name)
        elif ntype == "location":
            code = str(fm.get("area_code") or "").lower()
            if fm.get("chronicle") is True or (code and code in area_codes):
                included.append(note)
                matched_areas.add(code)
        elif fm.get("chronicle") is True:
            included.append(note)
    unmatched = sorted((npc_names - matched_npcs) | (area_codes - matched_areas))
    return included, unmatched


def _load_notes(vault_dir):
    notes = []
    for root, _dirs, files in os.walk(vault_dir):
        for fn in files:
            if fn.endswith(".md"):
                notes.append(parse_note(os.path.join(root, fn)))
    return notes


def _review_summary(pages, mysteries, unmatched, session_number):
    lines = ["Chronicle build review (session %s)" % session_number,
             "Pages: %d" % len(pages)]
    by_section = {}
    for p in pages:
        by_section.setdefault(p["section"], []).append(p["title"])
    for section in sorted(by_section):
        lines.append("  [%s] %s" % (section, ", ".join(sorted(by_section[section]))))
    lines.append("Mysteries: %d" % len(mysteries))
    for m in mysteries:
        lines.append("  (%s) %s" % (m["kind"], m["text"]))
    if unmatched:
        lines.append("Unmatched entities (encountered but no page): %s"
                     % ", ".join(unmatched))
    return "\n".join(lines)


def build_player_vault(vault_dir, out_dir, campaign_id):
    content_dir = os.path.join(out_dir, "content")
    assets_dir = os.path.join(out_dir, "assets")
    os.makedirs(content_dir, exist_ok=True)
    os.makedirs(assets_dir, exist_ok=True)

    selection = select_entities(vault_dir)
    sessions = selection["sessions"]
    included, unmatched = _select_pages(_load_notes(vault_dir), selection)

    title_to_slug = {_note_title(n): slugify(_note_title(n)) for n in included}

    pages, mysteries = [], []
    for note in included:
        title = _note_title(note)
        slug = title_to_slug[title]
        stripped = strip_gm_content(note["body"])
        resolved = resolve_wikilinks(stripped["player_body"], title_to_slug)
        page = {
            "slug": slug,
            "section": _section_for(note),
            "title": title,
            "recipients": "all",
            "source": "content/%s.md" % slug,
            "body": resolved,
        }
        if note["frontmatter"].get("player_epithet"):
            page["epithet"] = note["frontmatter"]["player_epithet"]
        if note["frontmatter"].get("portrait"):
            page["portrait"] = note["frontmatter"]["portrait"]
        pages.append(page)
        mysteries.extend(stripped["mysteries"])

    collect_assets(pages, vault_dir, assets_dir)

    backlinks = build_backlinks(pages)
    for page in pages:
        bl = backlinks.get(page["slug"])
        if bl:
            page["backlinks"] = bl

    for page in pages:
        with open(os.path.join(content_dir, page["slug"] + ".md"), "w", encoding="utf-8") as f:
            f.write(page["body"])

    spine = []
    for s in sessions:
        sfm = s["frontmatter"]
        spine.append({"session_number": sfm.get("session_number"),
                      "date": sfm.get("date"),
                      "summary": strip_gm_content(s["body"])["recap_seed"] or ""})
    session_number = sessions[-1]["frontmatter"].get("session_number") if sessions else None

    manifest = build_manifest(campaign_id, session_number, pages, mysteries, spine, {})
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return {"manifest": manifest,
            "review_summary": _review_summary(pages, mysteries, unmatched, session_number)}
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k build_player_vault`

- [ ] **Step: Commit**
  `git commit -m "chronicle: build_player_vault orchestrates select/strip/resolve/assets/manifest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: A3.4 — make_zip (archive with manifest at root)

**Files:**
- Modify: `tools/chronicle_build.py` (add `make_zip`)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Produces: `make_zip(out_dir) -> str` — zips `manifest.json` + `content/**` + `assets/**` (skip `.gitkeep`), manifest at archive ROOT. Returns the zip path.

- [ ] **Step: Write the failing test**

```python
def test_make_zip_has_manifest_at_root_and_skips_gitkeep(tmp_path):
    out = tmp_path / "out"
    (out / "content").mkdir(parents=True)
    (out / "assets").mkdir(parents=True)
    (out / "manifest.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (out / "content" / "romi.md").write_text("# Romi\n", encoding="utf-8")
    (out / "content" / ".gitkeep").write_text("", encoding="utf-8")
    (out / "assets" / "romi.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    zip_path = cb.make_zip(str(out))

    assert os.path.exists(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "content/romi.md" in names
        assert "assets/romi.png" in names
        assert not any(n.endswith(".gitkeep") for n in names)
        assert zf.read("manifest.json") == b'{"schema_version": 1}'
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k make_zip`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'make_zip'`.

- [ ] **Step: Minimal implementation**

```python
def make_zip(out_dir):
    """Zip manifest.json (at root) + content/** + assets/**, skipping .gitkeep."""
    zip_path = os.path.join(out_dir, "chronicle.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        manifest = os.path.join(out_dir, "manifest.json")
        if os.path.exists(manifest):
            zf.write(manifest, "manifest.json")
        for sub in ("content", "assets"):
            subdir = os.path.join(out_dir, sub)
            if not os.path.isdir(subdir):
                continue
            for root, _dirs, files in os.walk(subdir):
                for fn in sorted(files):
                    if fn == ".gitkeep":
                        continue
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, out_dir).replace(os.sep, "/")
                    zf.write(full, arc)
    return zip_path
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k make_zip`

- [ ] **Step: Commit**
  `git commit -m "chronicle: make_zip packs manifest at root plus content and assets

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: A3.5 — publish (multipart POST via urllib, no new dep)

**Files:**
- Modify: `tools/chronicle_build.py` (add `publish` + `_encode_multipart`)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Produces: `publish(zip_path, url, token=None) -> (ok, response)` — POSTs multipart `archive=<zip>` (Content-Type `application/zip`), sets `X-Chronicle-Token` header when `token` is given, uses `urllib` only. Returns `(ok_bool, response_text)`.

- [ ] **Step: Write the failing test** (monkeypatched `urlopen`, no network)

```python
class _FakeResp:
    def __init__(self, status=200, body=b'{"ok": true}'):
        self.status = status
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_publish_posts_multipart_with_token(tmp_path, monkeypatch):
    zip_path = tmp_path / "chronicle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", "{}")

    captured = {}

    def fake_urlopen(req, *a, **kw):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["content_type"] = req.get_header("Content-type")
        captured["token"] = req.get_header("X-chronicle-token")
        captured["data"] = req.data
        return _FakeResp()

    monkeypatch.setattr(cb.urllib.request, "urlopen", fake_urlopen)

    ok, resp = cb.publish(str(zip_path), "https://tableview.up.railway.app/api/chronicle/publish",
                          token="sekret")

    assert ok is True
    assert resp == '{"ok": true}'
    assert captured["method"] == "POST"
    assert captured["content_type"].startswith("multipart/form-data; boundary=")
    assert captured["token"] == "sekret"
    assert b'name="archive"' in captured["data"]
    assert b"PK" in captured["data"]  # the zip bytes are in the body


def test_publish_no_token_omits_header_and_reports_http_error(tmp_path, monkeypatch):
    zip_path = tmp_path / "chronicle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", "{}")

    def fake_urlopen(req, *a, **kw):
        assert req.get_header("X-chronicle-token") is None
        raise cb.urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b"leak detected"))

    monkeypatch.setattr(cb.urllib.request, "urlopen", fake_urlopen)

    ok, resp = cb.publish(str(zip_path), "https://example/api/chronicle/publish")
    assert ok is False
    assert "leak detected" in resp
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k publish`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'publish'`.

- [ ] **Step: Minimal implementation**

```python
def _encode_multipart(zip_path):
    boundary = "chronicle%s" % uuid.uuid4().hex
    filename = os.path.basename(zip_path)
    with open(zip_path, "rb") as f:
        payload = f.read()
    parts = [
        ("--" + boundary).encode(),
        ('Content-Disposition: form-data; name="archive"; filename="%s"' % filename).encode(),
        b"Content-Type: application/zip",
        b"",
        payload,
        ("--" + boundary + "--").encode(),
        b"",
    ]
    body = b"\r\n".join(parts)
    return body, "multipart/form-data; boundary=%s" % boundary


def publish(zip_path, url, token=None):
    body, content_type = _encode_multipart(zip_path)
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    if token:
        req.add_header("X-Chronicle-Token", token)
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode("utf-8", "replace")
            status = getattr(resp, "status", 200)
            return (200 <= status < 300, text)
    except urllib.error.HTTPError as e:
        return (False, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        return (False, str(e))
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k publish`

- [ ] **Step: Commit**
  `git commit -m "chronicle: publish POSTs the archive multipart via urllib with optional token

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

### Task: A3.6 — main / CLI (dry-run + abort-on-leak)

**Files:**
- Modify: `tools/chronicle_build.py` (add `main` + `__main__` guard)
- Test: `tests/test_chronicle_build.py`

**Interfaces:**
- Produces: `main(argv) -> int` — argparse `--vault --out --campaign-id [--publish-url] [--token] [--dry-run]`. Order: build → print review summary → `leak_check`; a leak prints offenders and returns nonzero **before any zip**; `--dry-run` stops after the leak check (no zip, no publish); otherwise zip then optional publish.

- [ ] **Step: Write the failing test**

```python
def test_main_dry_run_prints_summary_and_does_not_publish(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"

    def boom(*a, **k):
        raise AssertionError("publish must not be called on --dry-run")
    monkeypatch.setattr(cb, "publish", boom)

    zipped = {"called": False}
    real_make_zip = cb.make_zip
    monkeypatch.setattr(cb, "make_zip", lambda d: zipped.__setitem__("called", True) or real_make_zip(d))

    rc = cb.main([
        "--vault", FIXTURE, "--out", str(out),
        "--campaign-id", "shades-of-blood",
        "--publish-url", "https://example/api/chronicle/publish",
        "--dry-run",
    ])

    assert rc == 0
    assert zipped["called"] is False
    printed = capsys.readouterr().out
    assert "Pages:" in printed          # review summary reached stdout
    assert not (out / "chronicle.zip").exists()


def test_main_aborts_nonzero_on_leak_and_never_zips(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"

    def leaky_build(vault_dir, out_dir, campaign_id):
        os.makedirs(os.path.join(out_dir, "content"), exist_ok=True)
        with open(os.path.join(out_dir, "content", "boom.md"), "w", encoding="utf-8") as f:
            f.write("> [!danger] planted spoiler survived\n")
        with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
            f.write('{"schema_version": 1, "pages": []}')
        return {"manifest": {"schema_version": 1}, "review_summary": "Pages: 1"}

    monkeypatch.setattr(cb, "build_player_vault", leaky_build)
    monkeypatch.setattr(cb, "make_zip", lambda d: (_ for _ in ()).throw(
        AssertionError("make_zip must not run when a leak is present")))
    monkeypatch.setattr(cb, "publish", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("publish must not run when a leak is present")))

    rc = cb.main(["--vault", str(tmp_path), "--out", str(out), "--campaign-id", "x"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "LEAK CHECK FAILED" in err
    assert "content/boom.md: [!danger]" in err
    assert not (out / "chronicle.zip").exists()
```

- [ ] **Step: Run it, expect FAIL**
  `pytest -q tests/test_chronicle_build.py -k main`
  Expected: `AttributeError: module 'tools.chronicle_build' has no attribute 'main'`.

- [ ] **Step: Minimal implementation**

```python
def main(argv=None):
    parser = argparse.ArgumentParser(prog="chronicle_build",
                                     description="Build a spoiler-safe Chronicle player vault.")
    parser.add_argument("--vault", required=True, help="path to the GM Obsidian vault")
    parser.add_argument("--out", required=True, help="output dir for the player vault")
    parser.add_argument("--campaign-id", required=True, dest="campaign_id")
    parser.add_argument("--publish-url", default=None, dest="publish_url")
    parser.add_argument("--token", default=None)
    parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = parser.parse_args(argv)

    result = build_player_vault(args.vault, args.out, args.campaign_id)
    print(result["review_summary"])

    offenders = leak_check(args.out)
    if offenders:
        print("LEAK CHECK FAILED - aborting, nothing zipped or published. Offenders:",
              file=sys.stderr)
        for o in offenders:
            print("  " + o, file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run: leak check passed. Not zipping or publishing.")
        return 0

    zip_path = make_zip(args.out)
    print("Wrote archive: " + zip_path)

    if args.publish_url:
        ok, resp = publish(zip_path, args.publish_url, token=args.token)
        if not ok:
            print("Publish FAILED: " + str(resp), file=sys.stderr)
            return 1
        print("Published: " + str(resp))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step: Run tests, expect PASS**
  `pytest -q tests/test_chronicle_build.py -k main`

- [ ] **Step: Final full-suite run, expect PASS**
  `pytest -q tests/test_chronicle_build.py && pytest -q`

- [ ] **Step: Commit**
  `git commit -m "chronicle: CLI main with dry-run and hard abort-on-leak before any zip or publish

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

---

## Open questions (contract ambiguities for this slice)

1. **Asset reference model.** The contract's `collect_assets(pages, ...)` doesn't pin the `page` shape. I scan each page's `body` (markdown `![alt](path)` + Obsidian `![[embed]]`) plus a `portrait` field. If A2's `resolve_wikilinks` rewrites `![[img]]` → `assets/<name>` *only when copied*, there's a resolve↔collect ordering cycle. I broke it by having `collect_assets` scan for both raw embeds and rewritten links and running it after resolve in `build_player_vault`; confirm A2 leaves an embed/link form `collect_assets` can see, or expose an explicit `image_refs` list per page.

2. **Area-code matching.** `areas_covered` uses sub-codes like `C2a`, but location notes carry `area_code: C2`. My `_select_pages` matches exact-lowercased codes, so `C2a` won't match `C2` and would surface as an "unmatched entity". Confirm whether A1's `select_entities` normalizes sub-areas to their parent, or whether prefix-matching belongs here.

3. **Recap page vs spine-only.** I fold each session's `[!abstract]` recap seed into `manifest.spine[].summary` and emit no dedicated `section: recap` page (Part C's AI polishes the recap later). If PR1 expects a `recap` content page at MVP, `build_player_vault` needs one synthesized page; flag if so.

4. **`portrait` provenance.** I read `portrait` from NPC frontmatter only; auto-matching `Player Handouts/NPC Portraits/<name>.png` to a cast page is not done here. Confirm whether portrait auto-binding is A3's job or deferred.

5. **Zip location.** `make_zip` writes `out_dir/chronicle.zip` (inside the vault dir). `leak_check` ignores it (scans only `.md` + `manifest.json`) and `make_zip` never re-includes it, so it's safe, but if a caller runs `leak_check` expecting a pristine tree, note the stray `.zip`.

**Files this slice creates/modifies:**
- `/Users/evananderson/GM_pf2e/.claude/worktrees/player-campaign-hub-4d7448/tools/chronicle_build.py`
- `/Users/evananderson/GM_pf2e/.claude/worktrees/player-campaign-hub-4d7448/tests/test_chronicle_build.py`

**Consumes (other slices):** `tests/fixtures/gm_vault_sample/**` and the A1/A2 functions listed above.

---

## Part B + C: Publish auth token + `/publish-chronicle` skill + Player Vault scaffold

I have everything I need. Here is the plan for my slice.

---

# Chronicle PR0 — Slice B+C: Publish Auth Token + Cowork Skill + Player Vault Scaffold

Slice A (`tools/chronicle_build.py`) is drafted separately; this slice makes the CLI able to POST headlessly (Part B, app repo, TDD) and supplies the vault-side orchestration + scaffold artifacts (Part C, authored + smoke-tested).

---

## PART B — Publish auth token (app repo, TDD)

### Task B1: Scope `X-Chronicle-Token` to the `/api/chronicle` publish API

**Files:**
- Modify: `/Users/evananderson/GM_pf2e/.claude/worktrees/player-campaign-hub-4d7448/app.py` (add `_chronicle_token_ok`, wire into `check_gm_access` at :498)
- Test: `/Users/evananderson/GM_pf2e/.claude/worktrees/player-campaign-hub-4d7448/tests/test_chronicle_auth.py` (append)

**Interfaces:**
- Consumes: `request.path`, `request.headers['X-Chronicle-Token']`, `os.environ['CHRONICLE_PUBLISH_TOKEN']`, existing `_is_gm()`.
- Produces: `_chronicle_token_ok(path: str) -> bool`; behavioral change to the `check_gm_access` before_request — a valid token returns `None` (allow) instead of `403` for `/api/chronicle*` only.

**Contract note / firewall placement:** the token bypass must be scoped to `path.startswith('/api/chronicle')` and must NOT weaken any other GM prefix; it only applies after `_is_gm()` is already False (so the legacy-open and GM-session paths are untouched — they return earlier).

- [ ] **Step: Write the failing test** (subprocess-with-throwaway-DATA_DIR style, matching this file's existing tests). Append to `tests/test_chronicle_auth.py`:

```python
def test_chronicle_publish_token_unlocks_only_chronicle():
    # GM_PASSWORD set + no GM session => a plain caller is a non-GM. The
    # CHRONICLE_PUBLISH_TOKEN header must unlock EXACTLY /api/chronicle*, and
    # nothing else, and only when the env token is non-empty and matches.
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        os.environ['GM_PASSWORD'] = 'sekret'
        os.environ['CHRONICLE_PUBLISH_TOKEN'] = 'tok-abc123'
        import app as A
        c = A.app.test_client()

        # No header -> the chronicle publish API is still GM-gated (403).
        assert c.post('/api/chronicle/publish').status_code == 403

        # Wrong token -> still 403.
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': 'nope'}).status_code == 403

        # Correct token -> NOT 403 (the gate let it through; the route itself
        # may 400 on a missing archive, but it is no longer auth-blocked).
        rv = c.post('/api/chronicle/publish',
                    headers={'X-Chronicle-Token': 'tok-abc123'})
        assert rv.status_code != 403, rv.status_code

        # The token does NOT unlock any OTHER GM prefix (scope check).
        assert c.post('/api/clear_encounter',
                      headers={'X-Chronicle-Token': 'tok-abc123'}).status_code == 403
        print('TOKEN_SCOPED_OK')
    ''')
    assert 'TOKEN_SCOPED_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_publish_token_inert_when_env_unset():
    # With no CHRONICLE_PUBLISH_TOKEN in the environment, the header is inert:
    # a matching-looking header cannot unlock anything (empty expected != any).
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        os.environ['GM_PASSWORD'] = 'sekret'
        os.environ.pop('CHRONICLE_PUBLISH_TOKEN', None)
        import app as A
        c = A.app.test_client()
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': ''}).status_code == 403
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': 'anything'}).status_code == 403
        print('TOKEN_INERT_OK')
    ''')
    assert 'TOKEN_INERT_OK' in r.stdout, r.stdout + r.stderr
```

- [ ] **Step: Run it, expect FAIL.**
  `pytest -q tests/test_chronicle_auth.py -k "publish_token" 2>&1 | tail -20`
  Expected: `test_chronicle_publish_token_unlocks_only_chronicle` fails — the correct-token POST still returns `403` (assertion `rv.status_code != 403` fails), so `TOKEN_SCOPED_OK` is absent from stdout. (`test_..._inert_when_env_unset` already passes — the bypass doesn't exist yet — which is the correct baseline for that scope check.)

- [ ] **Step: Minimal implementation.** In `app.py`, add the helper just above `check_gm_access` (at line 497), and wire it into the gate. Add `import hmac` to the top-of-file imports if not already present (grep first: `grep -n '^import hmac' app.py`).

```python
def _chronicle_token_ok(path):
    """A valid X-Chronicle-Token unlocks EXACTLY the /api/chronicle publish API
    for headless CLI publishing (PR0 build tool -> prod). The env token must be
    non-empty and match the header exactly; only /api/chronicle paths are
    eligible, so a leaked token can never reach any other GM-gated prefix.
    Local dev (legacy-open, GM_PASSWORD='') never reaches here -- _is_gm() is
    already True there, so no token is needed."""
    if not path.startswith('/api/chronicle'):
        return False
    expected = os.environ.get('CHRONICLE_PUBLISH_TOKEN', '')
    if not expected:
        return False
    supplied = request.headers.get('X-Chronicle-Token', '')
    return hmac.compare_digest(supplied, expected)


@app.before_request
def check_gm_access():
    """Block GM-only API routes for non-GM callers (account or legacy mode).

    Exception: a valid X-Chronicle-Token unlocks only the /api/chronicle publish
    API for the headless PR0 build tool (see _chronicle_token_ok)."""
    path = request.path
    if any(path.startswith(prefix) for prefix in GM_API_PREFIXES) and not _is_gm():
        if _chronicle_token_ok(path):
            return None
        return jsonify({"error": "GM access required"}), 403
```

- [ ] **Step: Run tests, expect PASS.**
  `pytest -q tests/test_chronicle_auth.py 2>&1 | tail -15` (whole file, to prove no regression to the existing gate tests).

- [ ] **Step: Commit.**
  ```
  git commit -am "Chronicle PR0: scope X-Chronicle-Token to the /api/chronicle publish API

  A non-empty CHRONICLE_PUBLISH_TOKEN env + matching X-Chronicle-Token header
  lets the headless PR0 build tool POST to /api/chronicle* without a GM session.
  Constant-time compare; scoped so a leaked token unlocks nothing else; inert
  when the env var is unset. Legacy-open dev is unaffected (already _is_gm()).

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

## PART C — Vault-side artifacts (authored, verified by smoke run, not pytest)

These live in the GM's real Obsidian vault (`obsidian_vault/`), which carries its own `.claude/`. They are docs/skill/scaffold files — no unit tests; a single end-to-end smoke run (Task C5) is the acceptance gate.

### Task C1: The `/publish-chronicle` Cowork skill

**Files:**
- Create: `obsidian_vault/.claude/skills/publish-chronicle/SKILL.md`

**Interfaces:**
- Consumes (at runtime): the GM's session/NPC notes for optional AI enrichment; `tools/chronicle_build.py` CLI (`--vault --out --campaign-id [--dry-run] [--publish-url] [--token]`); `git diff` on the player vault; the `review_summary` printed by the build tool.
- Produces: player-safe `player_epithet:` / `status: draft` enrichment fields authored back into the GM vault (never spoilers), then a firewall review, then (on GM approval) a real publish.

- [ ] **Step: Author the file.** Exact content:

```markdown
---
name: publish-chronicle
description: >-
  Publish the spoiler-safe player Chronicle from this Obsidian vault. Use when
  the GM says "publish the chronicle", "update the player wiki", "push the
  chronicle", or after a session is written up. Optionally drafts player-safe
  epithets and a recap first, then ALWAYS shows the GM a firewall review (the
  derived player vault + a git diff) before anything is POSTed. The deterministic
  build tool is the real spoiler firewall; this skill only orchestrates it.
---

# Publish the Player Chronicle

Turn the GM Obsidian vault into a spoiler-safe player vault and publish it to the
Chronicle app. The deterministic tool `tools/chronicle_build.py` is the firewall
(strips `[!danger]`/`[!info]`/`[!tip]`/`[!warning]`, harvests `[!check]`/`[!question]`,
keeps `[!quote]`/`[!example]`, hard leak-checks). Your job is to orchestrate it,
optionally pre-draft player-safe prose, and make the GM the final gate.

Never invent facts. Never copy an NPC `role:` field verbatim into player text (it
often carries spoilers, e.g. `role: "Cult leader (revealed S4)"`). Every draft you
write is player-facing and must reveal nothing the party has not learned.

## Step 1 - Locate paths and confirm intent
- GM vault: this vault's root (the folder containing `_Conventions.md`).
- Player vault out-dir: ask the GM, or default to `../player_vault/` beside this vault.
- Campaign id and publish URL: ask the GM (URL only needed for a real publish).
- Confirm which session number is being published (usually the latest `status: completed`
  session note).

## Step 2 - OPTIONAL AI enrichment (drafts only; the firewall still runs after)
For each NPC/place the build tool will include (the union of `npcs_encountered` +
`areas_covered` across completed session notes), and for the session recap:
- If an NPC note has NO `player_epithet:`, DRAFT a short, spoiler-free epithet from
  ONLY what the party has observed (`[!check]` facts, read-aloud `[!quote]` text,
  the neutral parts of the note) and write it into the NPC note's frontmatter as
  `player_epithet: "..."`. If you cannot make one safely, leave it unset (the tool
  omits it). Do NOT derive it from `[!danger]`/`[!info]`/`role`.
- Draft a player-facing recap for the session and write it as a note tagged
  `status: draft` (e.g. `Player Handouts/Recaps/S<N> Recap (draft).md`) for GM
  review. The tool's deterministic recap seed (the `[!abstract]` one-liner) is the
  fallback if the GM rejects your draft.
- Optionally draft an entity "what you saw" blurb the same way, as `status: draft`.
Present a bullet list of every field/file you drafted so the GM can eyeball them.

## Step 3 - Build the player vault (DRY RUN - the firewall moment)
Shell out (from the app repo checkout, which holds `tools/`):

    python tools/chronicle_build.py \
      --vault <gmvault> --out <playervault> --campaign-id <cid> --dry-run

Then show the GM, together:
- the tool's printed `review_summary` (pages, mysteries, recap, assets, any skips);
- `git -C <playervault> diff` (or `git -C <playervault> status` + diff of new files)
  so they see exactly what changed vs the last publish;
- an explicit line confirming the leak-check passed (the tool aborts if not).
Say clearly: "This is the firewall. Nothing has been published. Approve to publish."

## Step 4 - Publish ONLY on explicit GM approval
After the GM approves, re-run WITHOUT `--dry-run` to build and POST:

    python tools/chronicle_build.py \
      --vault <gmvault> --out <playervault> --campaign-id <cid> \
      --publish-url <url> --token "$CHRONICLE_PUBLISH_TOKEN"

Report the app's response (accepted session number / published page count) back to
the GM. If the tool's leak-check or the app's ingest scan rejects the archive, STOP
and show the GM the offending callouts; do not retry until they are removed.

## Guardrails
- No emojis anywhere.
- Never POST without an explicit GM yes in Step 4.
- Never write a spoiler into a player-facing draft; when unsure, omit and tell the GM.
- The token is read from the `CHRONICLE_PUBLISH_TOKEN` env var; never print it.
```

- [ ] **Step: Smoke-verify** the frontmatter parses (Cowork loads it) and there are no emojis:
  `python tools/check_templates.py >/dev/null 2>&1; grep -nP '[\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}]' obsidian_vault/.claude/skills/publish-chronicle/SKILL.md && echo "EMOJI FOUND" || echo "clean"`
  Expected: `clean`.

- [ ] **Step: Commit.**
  ```
  git commit -am "Chronicle PR0: add /publish-chronicle Cowork skill (vault-side orchestrator)

  Orchestrates optional player-safe AI enrichment -> chronicle_build.py --dry-run
  firewall review (review_summary + git diff) -> GM-approved publish. Deterministic
  tool remains the spoiler firewall; the skill never POSTs without GM approval.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

**Open question:** The contract puts the skill under `obsidian_vault/.claude/skills/` but the CLI lives in the app repo's `tools/`. The skill assumes the GM has the app repo checked out and runs the CLI from there (Step 3/4 shell out with `python tools/chronicle_build.py`). If the GM runs Cowork with cwd = the vault, the skill should say how to reach `tools/` (an absolute path, or a wrapper). Flagging for the assembler to pin the invocation cwd.

---

### Task C2: Player Vault scaffold — folder layout + `.gitkeep`s

**Files (create):**
- `tests/fixtures/player_vault_scaffold/content/.gitkeep`
- `tests/fixtures/player_vault_scaffold/assets/.gitkeep`
- `tests/fixtures/player_vault_scaffold/.gitignore`

(The scaffold is committed as a template under `tests/fixtures/` so the smoke test and the skill have a known-good target shape to copy/point at; the live player vault the GM actually publishes lives outside the repo, beside their Obsidian vault.)

**Interfaces:** matches the PR1 zip shape the build tool emits — `manifest.json` at root, `content/<slug>.md`, `assets/**`. The build tool writes `manifest.json` and populates `content/`+`assets/`; the scaffold pre-creates the two dirs and a `.gitignore` so the player vault is a clean git repo (the skill's `git diff` firewall depends on it being version-controlled).

- [ ] **Step: Create the dirs and keepers.**
  `.gitkeep` files are empty. `.gitignore`:
```gitignore
# Player vault is generated by tools/chronicle_build.py. Track the manifest and
# player markdown so `git diff` shows the firewall delta each publish; ignore
# the zip and any local scratch.
*.zip
.DS_Store
```

- [ ] **Step: Smoke-verify** the shape:
  `find tests/fixtures/player_vault_scaffold -type f | sort`
  Expected: the three files above.

- [ ] **Step: Commit** (folded with C3 below — same scaffold changeset).

---

### Task C3: `Home.md` template + generation-rules README

**Files (create):**
- `tests/fixtures/player_vault_scaffold/content/Home.md`
- `tests/fixtures/player_vault_scaffold/GENERATION_RULES.md`

**Interfaces:** `Home.md` is the `section: home` seed page (slug `home`, `recipients: all`); the build tool may overwrite/augment it, but the template documents the expected frontmatter shape a `pages[]` entry needs (`slug`, `section`, `title`, `recipients`). `GENERATION_RULES.md` restates the callout policy so the GM authoring in Obsidian knows what will/won't cross the firewall.

- [ ] **Step: Author `content/Home.md`:**
```markdown
---
slug: home
section: home
title: "Welcome to the Chronicle"
recipients: all
---

# The Chronicle

This is the player-facing record of our campaign. It only ever contains what your
party has actually seen, heard, or been handed at the table. If something feels
missing, that is by design -- the story is still unfolding.

- **Recap** -- what happened last session.
- **Cast** -- the people you have met.
- **Atlas** -- the places you have been.
- **Lore** -- what you have learned about the world.
- **Handouts** -- letters, maps, and documents you have received.
- **Mysteries** -- what you know, and what you still suspect.
```

- [ ] **Step: Author `GENERATION_RULES.md`** (the GM-facing firewall cheat-sheet, restating `_Conventions.md`'s callout meanings in publish terms):
```markdown
# Player Chronicle - generation rules (for the GM)

The build tool (`tools/chronicle_build.py`) derives this player vault from your
Obsidian vault. It is a spoiler firewall: it keys off the callout taxonomy in
`_Conventions.md`. Author normally in Obsidian; this is what crosses to players.

## Callout policy (what publishes, what does not)

| Callout | In your vault | Crosses to players? |
|---|---|---|
| `[!danger]` | GM-only / spoiler | NO - stripped; a survivor ABORTS the publish |
| `[!info]` | Static lore / backstory | NO - stripped (GM-only by default) |
| `[!tip]` | "If players ask X, say Y" | NO - stripped |
| `[!warning]` | At-the-table trigger | NO - stripped |
| `[!abstract]` | Summary / "Previously On" | Seeds the session RECAP body |
| `[!quote]` | Read-aloud, verbatim | YES - kept as a quote block |
| `[!example]` | Player-facing handout content | YES - kept as a document frame |
| `[!check]` | Confirmed knowledge | Harvested -> Mysteries "What We Know" |
| `[!question]` | Unconfirmed / suspected | Harvested -> Mysteries "Open Questions" |

Also stripped everywhere: Obsidian `%%comments%%`, HTML `<!-- comments -->`, and
any frontmatter field not on the player whitelist.

## What becomes a player page
- Default is EXCLUDE. A note publishes ONLY if the party encountered it (its name
  appears in some completed session's `npcs_encountered` / `areas_covered`) OR it
  sets `chronicle: true`.
- `chronicle: false` force-excludes a note even if it was encountered.
- Everything under `Player Handouts/**` always publishes (it is secret-free by the
  vault's own README rule).

## NPC epithets (spoiler trap)
`role:` often carries spoilers (e.g. `role: "Cult leader (revealed S4)"`) and is
NEVER shown to players. To give an NPC a player-facing tagline, set a
`player_epithet:` in that note's frontmatter. If absent, the epithet is omitted
(or the publish skill drafts a safe one for your review).

## The firewall moment
Every publish runs `--dry-run` first and shows you a review summary + a git diff of
this vault. Nothing reaches players until you approve. The app re-scans on ingest as
a second layer, but YOUR review is the real gate.
```

- [ ] **Step: Smoke-verify** no emojis and the Home page frontmatter is valid YAML:
  `grep -nP '[\x{1F000}-\x{1FAFF}\x{2600}-\x{27BF}]' tests/fixtures/player_vault_scaffold/*.md tests/fixtures/player_vault_scaffold/content/*.md && echo EMOJI || echo clean; python -c "import yaml,sys; yaml.safe_load(open('tests/fixtures/player_vault_scaffold/content/Home.md').read().split('---')[1])" && echo YAML_OK`
  Expected: `clean` then `YAML_OK`.

- [ ] **Step: Commit** (C2 + C3 together).
  ```
  git commit -am "Chronicle PR0: player-vault scaffold (layout, Home.md, generation rules)

  Committed template under tests/fixtures/player_vault_scaffold/: content/ +
  assets/ dirs, a .gitignore so the live player vault is a clean git repo (the
  skill's diff firewall needs it), a section:home Home.md, and a GM-facing
  GENERATION_RULES.md restating the callout publish policy.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

### Task C4: Wire the token env into the local publish flow (docs stub)

**Files:**
- Modify: `DEPLOY.md` (append a short "Chronicle publish token" subsection)

**Interfaces:** documents the `CHRONICLE_PUBLISH_TOKEN` env var added in Part B — set it on Railway (prod) and export it locally when the CLI publishes to prod. Legacy-open local dev needs no token.

- [ ] **Step: Author** the appended subsection:
```markdown
## Chronicle publish token

The PR0 build tool (`tools/chronicle_build.py`) publishes the player Chronicle by
POSTing to `/api/chronicle/publish`. In production (GM_PASSWORD set), that route is
GM-gated, so headless publishing uses a scoped token:

- Set `CHRONICLE_PUBLISH_TOKEN` to a long random string in the Railway service env.
- Pass the same value to the CLI: `--token "$CHRONICLE_PUBLISH_TOKEN"` (or export it
  and let the `/publish-chronicle` skill read it).
- The token unlocks ONLY `/api/chronicle*` and nothing else; it is compared in
  constant time and is inert if the env var is empty.
- Local dev (legacy-open, `GM_PASSWORD=''`) is already open to the GM, so no token
  is needed there.
```

- [ ] **Step: Smoke-verify:** `grep -n "CHRONICLE_PUBLISH_TOKEN" DEPLOY.md` returns the new lines; `grep -nP '[\x{1F000}-\x{1FAFF}]' DEPLOY.md || echo clean`.

- [ ] **Step: Commit.**
  ```
  git commit -am "Chronicle PR0: document CHRONICLE_PUBLISH_TOKEN in DEPLOY.md

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

---

### Task C5: Smoke-test PR0 end to end (tasks-style, no pytest)

**Files:** none created — this is a manual/scripted acceptance run wiring Slice A's tool + Part B's token + the scaffold together.

**Interfaces:** Consumes `tools/chronicle_build.py` (Slice A), the real `obsidian_vault/`, a local legacy-open app instance, the Part B gate. Produces a confirmed round-trip: a built player vault, a valid zip, a successful publish, and a rendered `/chronicle`.

- [ ] **Step: Build against the real vault (dry-run first — the firewall).**
  ```bash
  python tools/chronicle_build.py \
    --vault obsidian_vault --out /tmp/player_vault_smoke \
    --campaign-id smoke --dry-run
  ```
  Expect: a printed `review_summary` (pages/mysteries/recap/assets), and an explicit leak-check PASS. Inspect: `find /tmp/player_vault_smoke -type f | sort`, then `python -c "import json;m=json.load(open('/tmp/player_vault_smoke/manifest.json'));print(len(m['pages']),'pages');import re;bad=[p['slug'] for p in m['pages'] if not re.match(r'^[a-z0-9][a-z0-9-]{0,80}$',p['slug'])];print('bad slugs:',bad)"` — expect `bad slugs: []`.

- [ ] **Step: Leak-scan the emitted vault by hand** (independent of the tool's own check):
  `grep -rnE '\[!danger\]|\[!secret\]|\[!gm\]|%%|<!--' /tmp/player_vault_smoke/content && echo "LEAK - STOP" || echo "no leaks"`
  Expect: `no leaks`. If anything prints, the firewall failed — do not proceed.

- [ ] **Step: Start a local legacy-open app** (no token needed; legacy-open GM passes the gate):
  ```bash
  DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 FLASK_DEBUG=true python app.py &
  ```

- [ ] **Step: Publish for real to the local instance:**
  ```bash
  python tools/chronicle_build.py \
    --vault obsidian_vault --out /tmp/player_vault_smoke \
    --campaign-id smoke --publish-url http://127.0.0.1:5057/api/chronicle/publish
  ```
  Expect: the tool reports the app accepted the archive (session number + page count). (Token path is exercised separately: restart with `GM_PASSWORD=x CHRONICLE_PUBLISH_TOKEN=tok` and re-run with `--token tok`; a bad/absent token must 403 — this is what Part B's unit test already proves, so the smoke run only needs the legacy-open happy path.)

- [ ] **Step: Load the reading hub** and eyeball it:
  `curl -s http://127.0.0.1:5057/chronicle | grep -o '<title>[^<]*</title>'` and open in a browser — confirm Recap/Cast/Atlas/Lore/Handouts/Mysteries render, a `[!quote]` shows as `.chron-callout-quote`, and NO GM callout text or `role:` spoiler appears. Stop the server when done.

- [ ] **Step: No commit** (verification only). Record the result (pages published, leak-scan clean, `/chronicle` renders) in the PR description.

---

## Open questions (whole slice)

1. **Token compare import:** `_chronicle_token_ok` uses `hmac.compare_digest`; confirm `import hmac` exists at the top of `app.py` (grep in B1) — add it if not. A plain `==` also works but is not constant-time; I've chosen the constant-time compare.
2. **Skill cwd vs. tool location:** the skill lives in the vault's `.claude/` but shells out to `tools/chronicle_build.py` in the app repo. The assembler must decide how the GM's Cowork session reaches that path (absolute path baked into the skill, a `$GM_PF2E_REPO` env var, or a thin wrapper script in the vault). Noted in Task C1.
3. **Live player vault location:** the committed scaffold under `tests/fixtures/` is a template; the actual published player vault is outside the repo (beside the Obsidian vault). Confirm whether the GM wants the live player vault itself under version control (the skill's `git diff` firewall assumes yes) or whether the tool should emit its own diff against the previous manifest instead.

---

## Self-Review

**Spec coverage (PR0 contract → task):**
- Firewall (`strip_gm_content`, callout policy) → Part A1. `leak_check` abort → Part A3.
- Auto-propose selection from session metadata → Part A2 (`select_entities`).
- Wikilink/backlink resolution → Part A2. Manifest (PR1 shape + slug validation) → Part A2.
- Assets copy/EXIF/size-budget → Part A3. Orchestration + zip + publish + CLI → Part A3.
- Headless auth token → Part B. Cowork skill + AI enrichment + review gate → Part C. Player Vault scaffold + generation-rules doc → Part C. End-to-end smoke test → Part C.

**Placeholder scan:** clean (no TBD/TODO/implement-later; every step shows real code or real file content).

**Type/name consistency:** all slices use the contract signatures; import convention `from tools import chronicle_build as cb`; shared `tests/test_chronicle_build.py`; `X-Chronicle-Token`/`CHRONICLE_PUBLISH_TOKEN` consistent.

## Open decisions for you (not blocking)

1. **Image resize (Pillow).** The plan copies referenced player images and strips EXIF *if* Pillow is importable, else copies as-is (no resize). Your `Player Handouts/` portraits/maps are already reasonable-sized, so v1 can ship without resize. If you want guaranteed ≤1600px, add `Pillow` to `requirements-dev.txt` (build-tool only, never the app). Say the word.
2. **AI enrichment depth (Part C).** v1's Cowork skill drafts player-safe epithets + prose recaps + "what you saw" blurbs as `status: draft` for your review. If you'd rather the first version be purely deterministic (epithets only from an explicit `player_epithet:` field, recaps only from your `[!abstract]` summaries — no AI drafting), the deterministic core already supports that; the skill's AI step is additive.
3. **Where the Player Vault + skill get committed.** `tools/chronicle_build.py` + the auth token + fixtures live in the app repo (this branch). The `/publish-chronicle` skill + Player Vault scaffold are vault-side artifacts — the plan writes them under `obsidian_vault/.claude/skills/` and a sibling player-vault folder; where those get committed (the vault's own git repo vs. elsewhere) is yours to place.
4. **Recipients / per-player secrets.** v1 publishes everything as `recipients: all`. Per-player secrets (the `recipients: [pc-slug]` path PR1 supports in account mode) can be driven from a `recipients:` frontmatter field on a note in a later pass — deferred unless you want it now.

## Not in PR0 (deferred)
Live SSE handling, Field Guide auto-generation from encounter history (PR1 Phase 3), Calendarium calendar baking, and Cosmere-vault specifics. PR0 delivers the PF2e Shades-of-Blood publish path end to end; the pipeline is system-agnostic markdown so a Cosmere vault reuses it.

## After this plan
Once approved, execute the same way as PR1 — subagent-driven, TDD, each task spec+quality reviewed, full-suite green before each completion, and a final whole-branch review. Then a **real end-to-end smoke run**: `tools/chronicle_build.py` against your actual `obsidian_vault/`, inspect the derived Player Vault, publish to a local legacy-open app, and load `/chronicle`. Merge to main (and Railway) remains your call.
