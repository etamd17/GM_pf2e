# CHRONICLE — Player Campaign Hub: Design Document (v2)

Status: **DESIGN LOCKED (v2)** — corrected against the real codebase and re-scoped
after a multi-perspective review (2026-07-15). Supersedes the v1 draft.
Author: Evan + Claude.

Chronicle is the player-facing campaign hub for GM_pf2e: a "living chronicle" of the
active campaign that players browse from their own devices. It is fed from the GM's
Obsidian vault through a derived, spoiler-free **Player Vault**, published to the app
after each session, and comes alive during play via the existing SSE hub.

> **What changed from v1 (why this doc exists).** A grounded review against the real
> code found the v1 draft strong and well-integrated, but with several load-bearing
> errors that would have broken the build. This v2 corrects them and re-scopes around
> three decisions the GM made:
> 1. **Authoring model: Obsidian-only.** The vault + `/publish-chronicle` skill is the
>    sole write path. No in-app editor. (Mitigations for the single-point-of-failure
>    are in §10.)
> 2. **MVP-first, merged incrementally** behind an empty-state gate — not a long-lived
>    branch. Each phase merges to `main`; players see nothing until the first publish.
> 3. **System-agnostic content hub.** Chronicle works for PF2e *and* Cosmere from day
>    one; only the live-combat layer is PF2e-first.
>
> Per-player secrets are IN the MVP (GM decision), which pulls the secure identity gate
> into Phase 1 (§4.4). Notes folds into Chronicle (§5).

---

## 0. Corrections applied from the v1 review (read this first)

| # | v1 said | Reality (verified against code) | Resolution |
|---|---|---|---|
| C1 | Recipient filtering by "claimed PC name" is a hard security boundary | `session['player_name']` is self-asserted (any non-GM who GETs `/player/sheet/<name>` becomes that player, app.py:12838); the existing handout path is worse — `GET /api/handouts?player=<name>` trusts a client query param (app.py:9620) and the `handout` SSE broadcast pushes full content to all clients, gated only by client-side localStorage (app.py:9654). Present in account mode too. | Per-player secrets key on **`owner_user_id` via `_user_owns_pc` (app.py:426), account-mode only**. Legacy-open: recipient-scoped content is GM-only. The live-handout merge **fixes** the existing leak (server-side `player_filter`). §4.4 |
| C2 | "Atomically swaps `.staging/` to `current/`" | `os.replace()` onto a non-empty directory raises `ENOTEMPTY`; the repo's own `trash_campaign_dir` proves it (rmtree-then-move, core/storage.py:124). A directory swap is **not** atomic. | **Symlink/pointer swap**: `current` → `content/<hash>/`; rename the symlink (atomic). Retain prior dir for rollback. §4.1 |
| C3 | "mistune or markdown-it-py — pick at build time" | `markdown==3.5.1` is already declared and imported (app.py:11), currently a dead import; the codebase documents a no-new-deps posture. | **Reuse python-markdown** with an extensions config. §4.3 |
| C4 | (review suggested) light "parchment" reading pane like Fable | Parchment-cream surfaces were tried and **deliberately removed in 2026-05 as "too aggressive / vibe-coded"** (system.css:57-83); token names kept, values flipped to warm-dark. | Reading comfort stays **inside the warm-dark system** (measure + type discipline), not a light theme. §6 |
| C5 | Cosmere parked to v2 entirely | `_active_system()` (app.py:315) is a hard per-request gate; gating the hub on `pf2e` darkens a live Cosmere table. Content layer is system-agnostic markdown. | Ship the **content hub system-agnostic**; park only the PF2e-shaped live layer. §5, §8 |
| C6 | New storage path `campaigns/<cid>/chronicle/` | Every per-campaign path is **dual-bound** (campaign branch + flat `DATA_DIR` fallback) in `_bind_campaign_paths` (app.py:606-649); `_active_campaign_id()` returns None in legacy-open (the local dev mode). A single-branch path 500s in dev. | Add `chronicle_dir(cid)` to `core/storage.py`, **dual-bind** it, reference a `CHRONICLE_DIR` global. §4.1 |
| C7 | "Fifth tab"; markdown-blocked SSE "streamed in chunks" | Nav is 4 tabs legacy / 5 with an account; Chronicle makes 6. gevent monkeypatch does not yield around CPU-bound render or blocking file I/O; a long publish can trip the gunicorn `--timeout 120` SIGKILL that drops all SSE. | Fold **Notes into Chronicle** to reclaim a slot (§5); make ingest cooperative/background (§4.3). |
| C8 | `entity_key` matching extends "`_pc_state_payload`-adjacent player frame" | `_pc_state_payload` (app.py:1199) is per-PC HP/derived — no combatant list. The `encounter_update` player frame (app.py:1481) already ships censored combatant names. | Retarget live matching to the `encounter_update` frame; likely client-side name match, no new server field. §7 (Phase 3) |

Additional adopted improvements from the review (cheaper than v1 assumed):
**Lore is a first-class screen** (the GM's actual ask); **backlinks** ("Appears in") for
near-zero cost by inverting the resolved-wikilink map; **client-side search** over the
manifest (server stays dumb); **reuse the existing `/gm/threads` graph** for a future
player-filtered relationship view; **server-side rollback** to the previous publish;
move the strip/generate logic into a **versioned `tools/` CLI** the skill shells out to.

---

## 1. Product summary
*The wiki that knows what session it is, who is asking, and whether the party is in
combat right now.*

Positioning vs LegendKeeper / Kanka / World Anvil / Saga20 / TavernScribe / Fable:
those are wikis that know nothing about the game engine, or recap generators that know
nothing about the prep. Chronicle is the only place where the prep vault, the player
wiki, and the live combat engine are one system. Spoiler safety is **by construction**
(mechanical stripping at generation, git-diff review before publish, server-side
re-check at ingest), and the players' content stays a portable plain-markdown git vault
even if the hub dies.

Differentiators, by phase (see §9):
- **MVP (Phase 1):** session-stamped reading hub (Home, Story, Lore, Cast), in-world
  handout documents, per-player secrets, a private player journal. Works PF2e + Cosmere.
- **Phase 2 (wiki depth):** Atlas, Mysteries board, backlinks, client-side search,
  "new since your last visit" markers.
- **Phase 3 (live layer — the true differentiation):** live entity badges during
  encounters, "You are here", Field Guide from encounter history, calendar.

## 2. Architecture
Content flows one direction; live state flows over SSE.

```
GM VAULT (Obsidian, source of truth)
    | /publish-chronicle (Cowork skill -> shells out to tools/chronicle_build.py)
    v
PLAYER VAULT (derived, git repo, zero secrets by rule)
    | zip -> POST /api/chronicle/publish (GM-authed)
    v
GM_pf2e / Railway volume   <DATA_DIR>/campaigns/<cid>/chronicle/  (or flat fallback)
    | render markdown -> HTML fragments at publish; atomic symlink swap
    v
PLAYERS  /chronicle*  (player-scope gate, recipient-filtered server-side, SSE-live)
```

Locked decisions:
- **The app never reads the GM vault.** Ingestion is exclusively the publish endpoint
  (preserves the deliberate removal of live-vault coupling).
- **Render at publish time, not request time.** Page views are file reads + Jinja.
- **Wikilinks resolved at generation time.** `[[X]]` → `/chronicle/page/<slug>` if
  published, else plain text (unlinked text = "not discovered yet"). Backlinks are the
  inverse map, also computed at generation.
- **Recipient filtering is server-side**, keyed on account ownership (§4.4).
- **System-agnostic content.** The manifest + fragments carry no system assumption;
  templates branch on `body.system-pf2e` / `body.system-cosmere` for skin only.

## 3. Player Vault + publish pipeline (Obsidian-only)

### 3.1 Player Vault
A sibling vault (own git repo) mirroring Chronicle sections:
```
Home.md            Recaps/S01..md      Cast/<npc>.md    Atlas/<place>.md
Lore/<topic>.md    Handouts/...        Field Guide/<creature>.md
Mysteries.md       Calendar.md         assets/ (referenced-only, resized <=1600px)
```

### 3.2 Generation rules (GM vault -> Player Vault) — run by `tools/chronicle_build.py`
| GM vault source | Rule |
|---|---|
| `Player Handouts/**` | Copy wholesale (already secret-free by its README rule) |
| Session notes / `Player Handouts/Recaps/*` | Player recap per session; GM notes never copied verbatim |
| NPC / location / lore notes | Player edition only if frontmatter opts in (§3.3) |
| `[!danger]`, `[!secret]`, `[!gm]` callouts | **Stripped, always** (hard-fail if any survive) |
| `[!quote]` | Kept — read-aloud block (`.callout-quote`) |
| `[!example]` | Kept — handout/prompt panel (`.doc-frame`) |
| `[!check]` / `[!question]` | Harvested into `Mysteries.md` (fact / question lanes) — **into the manifest even in the MVP** so Phase 2 lights up without a data migration |
| `[!info]` `[!tip]` `[!warning]` | Stripped (GM table cues) |
| Dataview blocks | Baked to static markdown |
| HTML comments, non-whitelisted frontmatter | Stripped (leak vectors — see §10) |
| Wikilinks | Rewritten to player-vault targets when published, else plain text |
| Stat blocks / encounter tables | Never copied |
| Attachments | Referenced-only; resized; **EXIF stripped** |

> **The strip/generate logic lives in a versioned `tools/chronicle_build.py`**, not
> inside the Obsidian skill. The `/publish-chronicle` skill is a thin wrapper that
> shells out to it, so the spoiler firewall is diffable and unit-tested in CI. This is
> the main mitigation for choosing Obsidian-only authoring.

### 3.2.1 Two layers: deterministic firewall + AI-assisted derivation
Generation is intentionally split so the GM does NOT hand-author a "player edition" of
every note. There is a script you run on the vault that pulls spoiler-free info from past
sessions and drafts the player content for you.

**Layer 1 — deterministic (`tools/chronicle_build.py`, always runs, no AI, unit-tested).**
The mechanical spoiler firewall: strip `[!danger]`/`[!secret]`/`[!gm]` + HTML comments +
non-whitelisted frontmatter + EXIF, copy opted-in notes, resolve wikilinks + backlinks,
bake dataview, harvest `[!check]`/`[!question]` into Mysteries, subset/resize assets, and
the hard leak-check. This alone produces a spoiler-free **text** player vault even with no
AI in the loop (your "at least a text document" floor).

**Layer 2 — AI-assisted derivation (the `/publish-chronicle` skill, opt-in, HITL).**
Reuses the app's proven pattern — `_anthropic_complete` (app.py:5822, the "Previously on"
recap) and `_extract_beats_via_claude` (app.py:7083, which already reads raw session notes
and extracts structured beats into `story_threads.json`; app.py:6853 documents "the GM's
Cowork regenerates this file from session notes"). Pointed at player output, the skill:
- reads the past **session notes** and **drafts a spoiler-free player recap** per session
  (story-prose, GM-only content excluded by prompt), written to `Recaps/S0N.md`;
- **extracts entity stubs** the players have encountered — Cast (name, epithet, "last seen
  SN"), Atlas (place, "discovered SN"), Lore topics, open Mysteries — as draft notes;
- marks every AI-drafted note `status: draft` in frontmatter.

**Spoiler safety is preserved because the human is the gate, exactly as today:**
1. The AI runs **on the GM's machine** (Cowork), so raw secret notes never reach the
   server — only the reviewed, stripped output is ever published.
2. AI **drafts**; the GM reviews the **git diff** (the existing firewall moment) and
   edits/approves before anything publishes.
3. Layer 1's mechanical `[!danger]` strip + the server-side re-check at ingest are hard
   backstops under the AI, not replacements for it. A `draft`-marked note the GM never
   approves is never published.

### 3.3 Frontmatter contract
```yaml
---
title: Romi
section: cast          # home|recap|cast|atlas|lore|handout|fieldguide
slug: romi
epithet: The broker who wasn't
tags: [suspect]
recipients: all        # all | [<pc_slug>, ...]  -- matched to ACCOUNT OWNERSHIP (§4.4)
session_introduced: 2
session_updated: 4
portrait: assets/romi.png
entity_key: romi       # optional -- Phase 3 live-tracker matching
---
```

### 3.4 Publish flow
1. GM runs `/publish-chronicle`. (Optional, opt-in) it first **AI-drafts** spoiler-free
   player recaps + entity stubs from the past session notes (§3.2.1 Layer 2), on the GM's
   machine. It then invokes `tools/chronicle_build.py` (§3.2.1 Layer 1), which regenerates
   the player vault, runs the leak check, bakes dataview, resolves wikilinks + backlinks,
   subsets/resizes/strips-EXIF on assets, and skips any `status: draft` note the GM hasn't
   approved.
2. Skill shows a **git diff summary** (the spoiler firewall moment) — GM approves.
3. Skill commits, zips (`manifest.json` + `content/**` + `assets/**`, cap **48 MB** to
   stay clear of the 64 MB `MAX_CONTENT_LENGTH`), POSTs to `/api/chronicle/publish`.
4. Server validates the manifest (incl. `schema_version`), **re-runs the leak check**
   (defense in depth — reject with 400 if any `[!danger]`/secret marker survives),
   renders fragments into a fresh stamped content dir, then **atomically repoints the
   `current` symlink**. Broadcasts `chronicle_update`.

`manifest.json`: `schema_version`, campaign id, `session_number`, `generated_at`,
`pages[]` (frontmatter + rendered filename + resolved backlinks), `mysteries[]`,
`calendar`, `fieldguide[]`, `spine[]`.

## 4. Server spec (GM_pf2e changes)

### 4.1 Storage (Railway volume; dual-bound; symlink swap)
```
<chronicle_dir>/                     # campaigns/<cid>/chronicle/  OR  DATA_DIR/chronicle (flat fallback)
    current -> content/<hash>/       # SYMLINK, atomically repointed on publish
    content/<hash>/manifest.json
    content/<hash>/html/<slug>.html
    content/<hash>/assets/...
    previous -> content/<oldhash>/   # retained for one-click rollback (keep last N)
    seen_creatures.json              # Phase 3 encounter log (bounded/rotated)
    reader_state.json                # {user_id: {last_seen_session, last_visit}} (fsync=False)
    .staging/<hash>/                 # publish workspace
```
- `core/storage.py::chronicle_dir(cid)` + `CHRONICLE_DIR` global **bound in both
  branches** of `_bind_campaign_paths` (campaign path + flat `DATA_DIR/chronicle`).
- Swap = write `content/<hash>/` fully → `os.symlink` a temp link → `os.replace()` it
  onto `current` (atomic on POSIX). Resolve `current` at request time.

### 4.2 Routes
Player-facing (behind the new player-scope gate; recipient-filtered):
`GET /chronicle` (Home) · `/chronicle/story` · `/chronicle/lore` · `/chronicle/cast`
· `/chronicle/handouts` · `/chronicle/journal` (folded-in Notes) · `/chronicle/page/<slug>`
· `/chronicle/assets/<path>` (path-normalization traversal guard).
Phase 2 adds `/chronicle/atlas`, `/chronicle/mysteries`, `/chronicle/search` (client-side).

GM-only — **add `/api/chronicle` to `GM_API_PREFIXES`** (no `@gm_required` double-flag):
`POST /api/chronicle/publish` (stream to temp file, background render) ·
`GET /api/chronicle/status` (last publish, page count, leak report, unmatched entity_keys) ·
`POST /api/chronicle/rollback` (repoint `current` -> `previous`) ·
`POST /api/chronicle/scene` (Phase 3).

### 4.3 Rendering
- **python-markdown** (already imported, app.py:11) invoked only in the publish handler,
  with a fixed extensions set (tables, attr_list, footnotes, sane_lists). **Sanitize**
  the rendered HTML (fragments are injected into player pages).
- Callouts map at render: `[!quote]` → `.callout-quote`, `[!example]` → `.doc-frame`;
  anything unexpected → plain blockquote.
- **Non-blocking ingest**: stream upload to a temp file (not `BytesIO`); render in
  bounded batches with `gevent.sleep(0)` between pages; return `202` and finish in a
  spawned greenlet so a mid-session publish can never trip the `--timeout 120` SIGKILL.
- **Cache**: fragments/assets served with a per-publish version token (the SW pins
  unversioned static assets forever — see the cache-freshness scheme).
- **Escaping (repo bug class):** Chronicle templates use `data-*` + `addEventListener`
  only; no inline `onclick`. Extend `tests/test_inline_handler_escaping.py` to
  `chronicle*.html`.

### 4.4 Auth, identity, and the recipient boundary
- **New player-scope gate** for `/chronicle*` (none exists today; only GM + CSRF
  `before_request` hooks). In account mode it requires campaign membership; in
  legacy-open it requires `session['player_name']`.
- **Per-player secrets key on account ownership** — `owner_user_id` via `_user_owns_pc`
  (app.py:426), NOT the self-asserted `session['player_name']`. A page/handout whose
  `recipients` doesn't include a PC the requesting user owns is excluded from listings
  AND returns 404 on direct URL. GM sees all (with an "Only <name>" pill).
- **Legacy-open fallback:** any non-`all` recipient is treated as **GM-only** (never
  served to a player), because identity is unauthenticated in that mode. → *Per-player
  secrets at your table require account mode.* This is called out as a conscious choice.
- **Fix the pre-existing handout leak** in the same pass: the merged live handouts must
  be filtered server-side (`player_filter` on the `handout` broadcast; drop the
  `?player=` query-param trust).

### 4.5 Session-awareness (Phase 2)
`reader_state.json` stores `last_seen_session` per user (written `fsync=False`, off the
SSE hot path). New-dots render server-side where `session_updated > last_seen_session`;
on first visit (`last_seen_session` null) nothing is "new."

### 4.6 Live behavior (SSE, Phase 3)
All via `window.appSSE(...)`; broadcasts via `sse_broadcast(..., player_filter=...)`.
`chronicle_update` (publish) → toast + refresh new-dots. Existing `encounter_update`
→ live-session banner + "in this scene" badges (matched client-side against the already
-censored combatant names in the player frame). `chronicle_scene` → "You are here".
`handout`/`handout_deleted` (existing, now server-filtered) → gallery updates.

## 5. Screens
**MVP (Phase 1):**
1. **Home** — kicker, "As of Session N" + in-game date, latest recap with read-aloud
   pull-quote, shortcuts, party chips.
2. **Story So Far** — vertical timeline (hairline rail), one recap card per session,
   chapter breaks as centered labels. (Note the reuse seam with the existing "Previously
   on" recap and `/gm/threads` — share the recap source, don't fork it.)
3. **Lore** — first-class index (same card grammar as Cast), the GM's explicit ask.
4. **Cast** — entity card grid (portrait/monogram, name, epithet, tag pills, "Last seen SN").
5. **Handouts** — in-world document frames; **merges published docs + the hardened live
   `handouts.json`**; per-recipient pills.
6. **My Journal** — the folded-in Notes surface, reusing existing `/api/notes` +
   `/api/journal`; private per player. Gives the hub two-way feel and reclaims a nav slot.

**Phase 2:** Atlas, Mysteries. **Phase 3:** Field Guide, calendar widget, live badges.

Entry point: a **Chronicle** tab in `_player_nav.html` **and** `_cosmere_player_nav.html`,
gated on `chronicle/current` existing. Notes tab is **removed** (folded in). Net nav count
stays at 5 (account) / 4 (legacy). Sheet remains the landing page. Single-column on phones.

## 6. Design system + visual restraint
Warm-dark identity, existing `system.css` tokens only. No new hues, no gradients, no
glow, motion = opacity/transform, `prefers-reduced-motion` respected and **calm-by-default**.

**Reading surfaces** (recaps, lore, doc-frames): stay warm-dark, but make reading
comfortable — body on a lifted `--bg-card` surface (not the raw `--bg-page` floor),
Alegreya ~18px, line-height ~1.6, **measure clamped to ~62-68ch** (the splash already
does `max-width:660px`), `--text-1` for contrast. **No light/parchment pane** (removed
in 2026-05 as vibe-coded).

**Dial-down pass** (mirrors the anti-ornament pass system.css already applied):
- Delete glow tokens/shadows; `new-dot` = flat 6px gilt disc.
- `live-dot` static (or one 2s opacity fade, off by default under reduced-motion).
- Portrait fallback = quiet body-size monogram on a flat `--ink-700` well (no 44px drop-cap).
- Placeholders flat (no gradient hatch): flat well + small document glyph, or a quiet
  "No image" caption.
- Live banner = slim ruled bar (ruby left-rule + ruby text on dark chrome), not full-bleed.
- Tracked-uppercase in ONE role (section kicker); titles Cinzel sentence-case, epithets
  Alegreya italic, "Last seen SN" low-emphasis sentence-case.
- Timeline = hairline rail + solid node dots; chapter breaks = centered Cinzel label
  with thin side rules, not a rotated gilt diamond.

Accessibility: interactive cards are real anchors; tabs are buttons with `aria-current`;
new-dots paired with visually-hidden "updated" text; live badges in an `aria-live`
container; AA contrast on all text/token pairs.

## 7. Integration seams
- **VTT/battle-map (separate effort):** Atlas is the map's future mount point;
  `chronicle_scene`/"You are here" is the SSE seam it subscribes to. Reserved, not designed.
- **Cosmere:** content hub is system-agnostic from day one; only Phase-3 Field Guide +
  `entity_key` matching are PF2e-shaped (rename the v2 item to "Cosmere live-layer parity").
- **Notes:** folded into Chronicle's Journal (not shipped as an orphan tab).
- **Recap/threads:** Story So Far shares the existing recap source and can later surface
  a player-filtered `/gm/threads` graph rather than inventing one.

## 8. Build plan (incremental, gated merges)
Each PR: branch off `main`, `pytest -q` + `python tools/check_templates.py` green,
Railway-verified, **merged behind the empty-state gate** (tab hidden until first publish).

| PR | Scope | Key tests |
|---|---|---|
| 0 | `tools/chronicle_build.py` (Layer 1) + `/publish-chronicle` skill with AI derivation (Layer 2, reusing the `_anthropic_complete`/`_extract_beats_via_claude` prompt pattern) + player-vault scaffold (no app PR) | build-CLI unit tests: strip, wikilink+backlink resolve, leak-check, asset subset/EXIF, `status: draft` skip |
| 1 | Publish endpoint (temp-file ingest, symlink swap, manifest+leak validation, rollback, python-markdown), `chronicle_dir` dual-bind, player-scope gate + **ownership-keyed recipients**, Home + Story + Lore + Cast + Handouts merge (+ handout-leak fix) + Journal, nav tab (both systems), empty-state gate | leak-check refusal (400), auth-prefix + player-gate, recipient 404 (ownership), asset traversal, symlink-swap atomicity, template parse, inline-handler ban |
| 2 | Atlas, Mysteries, detail pages, backlinks, client-side search, `reader_state` new-dots | new-dot session math, backlink inversion, search index shape |
| 3 | Live layer: banner, `entity_key` match on the encounter frame, `chronicle_scene`, `chronicle_update` toast; Field Guide + `seen_creatures.json` (bounded); calendar | SSE frame censoring (no GM data in player frame), encounter-end logging, rotation |

## 9. Risks and mitigations
1. **Publish friction / single-point-of-failure (Obsidian-only).** One-command skill;
   `tools/chronicle_build.py` versioned + tested; `status` endpoint nags "last published
   S3" on the GM hub when stale; **server-side rollback** for a bad publish.
2. **Spoiler leak.** Strip `[!danger]`/`[!secret]`/`[!gm]` + HTML comments + non-whitelist
   frontmatter + EXIF at generation AND re-check at ingest; git-diff approval; recipients
   ownership-keyed server-side; unpublished = 404; rendered HTML sanitized.
   **AI derivation (§3.2.1 Layer 2) can hallucinate or over-share** — mitigated by: AI runs
   on the GM's machine (raw notes never hit the server); everything AI-drafted is
   `status: draft` and excluded until the GM approves it in the diff; the deterministic
   Layer-1 strip + ingest re-check run *under* the AI as hard backstops.
3. **Attachment bloat.** Referenced-only, resize 1600px, 48 MB cap, publish replaces `current/`.
4. **SSE worker stall.** Temp-file upload, bounded render + `gevent.sleep(0)`, 202 +
   background greenlet; publish off-session by convention.
5. **entity_key drift.** Normalized-key match + `status` report of unmatched keys.
6. **Legacy-mode secret exposure.** Per-player secrets require account mode; legacy-open
   treats non-`all` recipients as GM-only.

## 10. Parked for later
- In-app authoring (GM chose Obsidian-only; revisit if the vault path proves brittle).
- Cosmere live-layer parity (Field Guide + entity_key for Cosmere).
- Service-worker offline reading; PDF export of sections.
- `audience=table` Chronicle Home as the table screen's idle view.
- Player theories/contributions feeding back into GM prep.
