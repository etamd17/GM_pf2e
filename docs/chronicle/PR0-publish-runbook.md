# Chronicle — Publish Runbook (PR0)

How the GM turns the Obsidian vault into the spoiler-safe player Chronicle and publishes it.
This covers the vault-side pipeline: the build tool (`tools/chronicle_build.py`), the
`/publish-chronicle` Cowork skill, the auth token, and the end-to-end smoke test.

The app side (PR1) is already built: it ingests the published zip and serves `/chronicle*`.

## The pieces

- **`tools/chronicle_build.py`** — the deterministic build tool (runs on the Mac; stdlib-only,
  optional Pillow for image handling). It derives a small, spoiler-safe Player Vault from the GM
  vault, hard-fails on any surviving GM marker, zips it, and POSTs it to the app.
- **`/publish-chronicle` skill** — the Cowork orchestrator, installed in the GM vault at
  `<GM vault>/.claude/skills/publish-chronicle/SKILL.md`. It optionally AI-drafts player-safe
  epithets/recaps, runs the build tool in dry-run, shows the GM the review (the firewall moment),
  and publishes on approval.

## The firewall (what is stripped vs kept) — keyed on the vault's own `_Conventions.md`

| Callout | Player Chronicle |
|---|---|
| `[!danger]` `[!info]` `[!tip]` `[!warning]` | STRIPPED (GM-only). A surviving `[!danger]`/`[!secret]`/`[!gm]` ABORTS the build. |
| `[!quote]` | KEPT (read-aloud) -> rendered as a read-aloud block |
| `[!example]` | KEPT (player handout) -> rendered as a document frame |
| `[!check]` | HARVESTED -> Mysteries "What We Know" |
| `[!question]` | HARVESTED -> Mysteries "Open Questions" |
| `[!abstract]` | Used as the session recap body |
| `%%obsidian comments%%`, `<!-- html comments -->` | STRIPPED |

Everything else `>`-prefixed that is NOT `[!quote]`/`[!example]` is stripped (allowlist).
Two independent layers enforce this: `strip_gm_content` (primary) and `leak_check` (abort gate),
and the app re-scans at ingest (defense in depth).

## Selection (auto-propose + review)

A note becomes a player page ONLY if:
- it is an NPC/location the party has ENCOUNTERED — the tool unions `npcs_encountered` +
  `areas_covered` across your COMPLETED session notes; or
- it carries `chronicle: true` in frontmatter (force-include); or
- it lives under `Player Handouts/**` (copied wholesale; secret-free by that folder's own rule;
  `_`-prefixed and `type: reference` meta files are skipped).
`chronicle: false` force-excludes a note even if encountered. Everything else is excluded by default.
You then REVIEW the derived vault before it publishes.

## The CLI

```bash
# DRY RUN — build + strip + leak-check + print a review summary; does NOT publish.
python tools/chronicle_build.py \
  --vault "/Users/evananderson/Documents/Pathfinder Campaigns" \
  --out   "/Users/evananderson/Documents/Campaign Chronicle" \
  --campaign-id <cid> \
  --dry-run

# PUBLISH — same, minus --dry-run, plus the app URL (+ token for prod).
python tools/chronicle_build.py \
  --vault "/Users/evananderson/Documents/Pathfinder Campaigns" \
  --out   "/Users/evananderson/Documents/Campaign Chronicle" \
  --campaign-id <cid> \
  --publish-url http://localhost:5057/api/chronicle/publish
```

- `build_player_vault` STAGES to a temp dir and only syncs `manifest.json` + `content/` + `assets/`
  into `--out` on a CLEAN build. It NEVER deletes `--out` or anything else in it (your `.obsidian/`
  and other folders are untouched). On a leak it leaves `--out` untouched and exits nonzero.
- A leak (nonzero exit / `LEAK CHECK FAILED`) NEVER zips or publishes. Fix the offending note
  (wrap the secret in `[!danger]`) and re-run.
- **Option A safety (`--out` = your real player vault):** two behaviors keep the tool from
  disturbing the hand-authored notes you keep alongside its output:
  - The belt-and-suspenders re-scan before zipping is SCOPED to the tool's own managed outputs
    (`manifest.json` + `content/`). Your hand-authored `01 - Chronicle/`, `02 - Cast/`, `Home.md`,
    etc. are NOT re-scanned, so an in-world `[!danger]` callout you wrote for players (e.g. "the
    bridge is unstable") never false-positive-aborts a clean publish. (The staged build itself is
    still fully firewalled, and the app re-scans at ingest.)
  - The publish archive is written to a private temp file and removed after publishing, so `--out`
    never accrues a stray `chronicle.zip` build artifact.

## Auth (Task 16): publishing to prod

- **Local dev** (`GM_PASSWORD=''`, legacy-open): no token needed; the app treats everyone as GM.
- **Prod** (Railway, `GM_PASSWORD` set): set `CHRONICLE_PUBLISH_TOKEN` in the app's environment
  (a long, high-entropy secret). The tool sends it as the `X-Chronicle-Token` header; the app's
  `check_gm_access` allows it for `/api/chronicle*` ONLY (it unlocks nothing else). A wrong/absent
  token, or an unset server env, still 403s.

**Pass it via the environment, not `--token`.** The CLI reads the same
`CHRONICLE_PUBLISH_TOKEN` variable the server does, so you export one value and both ends agree.
A token in argv is readable by any process on the box via `ps` while the publish runs, and it stays
in your shell history afterwards — and this token replaces the entire player-facing Chronicle, so
it is worth keeping off the command line. `--token` still wins if given, for existing scripts.

Both sources are stripped of surrounding whitespace: `export TOKEN=$(cat secret)` picks up a
trailing newline, which would otherwise fail the server's `hmac.compare_digest` and surface as an
inexplicable 403.

```bash
# On the app host (Railway variables): CHRONICLE_PUBLISH_TOKEN=<generated-secret>
# At publish time, same variable name:
export CHRONICLE_PUBLISH_TOKEN='<the-secret>'
python tools/chronicle_build.py ... --publish-url https://<app>/api/chronicle/publish
```

You also need the **target campaign's id** for `--campaign-id`. In account mode the publish route
resolves its target from the manifest and a missing or unknown id is a hard 400 — it never falls
back to "whatever campaign is live", so a typo cannot land your players' Chronicle in the wrong
campaign. Get the id from `/campaign/<id>/invites`. (The `campaign_id: null` you see against a
local legacy-open dev app is the no-accounts path, not that fallback.)

## End-to-end smoke test (Task 21)

1. **Build the sample** against the committed fixture (no real vault needed) and inspect it:
   ```bash
   python tools/chronicle_build.py --vault tests/fixtures/gm_vault_sample \
     --out /tmp/chron-smoke --campaign-id sample --dry-run
   # -> prints the review summary; /tmp/chron-smoke has manifest.json + content/ + assets/
   grep -ri "camazotz\|azlanti\|sacrifice" /tmp/chron-smoke   # -> NOTHING (secrets stripped)
   ```
2. **Run against the real vault** in dry-run; read the review summary and the diff of the derived
   player vault. This is the firewall/approval moment — confirm no secret is present.
3. **Publish to a LOCAL app** (legacy-open) and load it:
   ```bash
   DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 python app.py &   # or the launch config
   python tools/chronicle_build.py --vault ... --out ... --campaign-id <cid> \
     --publish-url http://localhost:5057/api/chronicle/publish
   # open http://localhost:5057/chronicle  -> the derived Chronicle renders
   ```
4. **Verify on Railway** before relying on it at the table (per the project's working agreement):
   publish with the prod URL + token, load the live `/chronicle`, and check recipient scoping with
   a second account.

## Publishing an already-player-facing vault (`--mode player-vault`)

Everything above assumes `--vault` is the GM's private authoring vault, and the tool DERIVES a
player vault from it (auto-select encountered entities, strip GM callouts). Some GMs instead
hand-author a vault that is ALREADY player-facing end to end (e.g.
`~/Documents/Campaign Chronicle`, organized as `01 - Chronicle/`, `02 - Cast/`, `04 - Atlas/`,
`05 - Handouts/`, `Home.md`, ...). For that vault, `--mode player-vault` skips the derivation step
and ingests it close to as-authored.

### When to use which mode

- **`gm-vault`** (the default): the input is the GM's private notes. The tool auto-selects
  encountered NPCs/areas, unions them with `chronicle: true` overrides, and runs every page body
  through `strip_gm_content` (the callout firewall) to strip GM-only content before it can leak.
- **`player-vault`**: the input is ALREADY curated for players. The tool includes (almost) every
  note — mapping folder/type to an app section instead of auto-selecting by encounter — and
  bodies are NEVER passed through `strip_gm_content`. That firewall's allowlist would delete
  legitimate `[!info]` blocks and mangle `[!abstract]` blocks the GM deliberately wrote FOR
  players in this vault. Verified on the real vault: 26 of the 53 pages retain player callouts
  that `gm-vault` mode would have stripped.
- **Choosing the wrong mode is the main risk.** Running `gm-vault` mode over an already-player-facing
  vault silently deletes player content (the firewall has nothing GM-only to find, so it just
  guts the callouts). Running `player-vault` mode over a real GM vault would publish GM secrets —
  it has no per-note firewall to catch them first. This is exactly why the leak gate below is not
  optional: it is the only thing standing between a mode mistake and a real leak.

### The command

```bash
# DRY RUN — build + leak-check + print a review summary; does NOT publish.
python3 tools/chronicle_build.py \
  --vault "/Users/evananderson/Documents/Campaign Chronicle" \
  --out   <out-dir> \
  --campaign-id <cid> \
  --mode player-vault \
  --dry-run

# PUBLISH — same, minus --dry-run, plus the app URL (+ --token for prod).
python3 tools/chronicle_build.py \
  --vault "/Users/evananderson/Documents/Campaign Chronicle" \
  --out   <out-dir> \
  --campaign-id <cid> \
  --mode player-vault \
  --publish-url http://localhost:5057/api/chronicle/publish
```

Verified against the real vault: the dry-run above exits 0 and reports 53 pages across sections
`atlas 8, cast 16, handout 3, home 1, lore 18, recap 7`.

### The safety guarantees (why a GM can trust this)

- **The leak gate always runs, in both modes.** Any surviving `[!danger]`/`[!secret]`/`[!gm]`
  marker aborts the build: nonzero exit, no zip, no publish. Skipping the callout *firewall* in
  player-vault mode does not skip this independent check. Verified: the leak fixture exits 1 and
  writes no zip.
- **`--out` is never destroyed.** The build stages to a temp dir and syncs into `--out` only on a
  clean build (only `manifest.json` + `content/` + `assets/` are written/replaced); it never
  `rmtree`'s `--out`. This matters because `--out` can legitimately BE the GM's real vault, which
  holds hand-authored content, `.obsidian/` config, and possibly `.git`.
- **Skipped automatically:** dot-directories (`.obsidian/`, `.remember/`), the `_Site/` folder,
  underscore-prefixed FILENAMES (e.g. `_About Handouts.md`), and `type: reference` notes.
- **NOT skipped, deliberately:** the `_maps/` folder — despite the underscore, its notes are
  `type: handout` player content and are included.

### What to check in the dry-run before publishing

1. **Page count and section breakdown** in the printed review summary look right for the vault's
   current state (a big unexplained jump or drop is worth investigating before publishing).
2. **Leak check passed** — the dry-run output ends with `Dry run: leak check passed. Not zipping
   or publishing.` A leak instead prints the offending notes and exits nonzero; fix the note
   (wrap the secret in `[!danger]` so `gm-vault` mode would strip it, or remove it if this vault
   should never carry it) and re-run.
3. **Asset warnings.** The tool logs `referenced asset not found` for any `![[embed]]` that
   doesn't resolve, then degrades that embed to nothing rather than failing the build. On the real
   vault today this currently fires for two references from the Map Gallery note:
   `"Talmandor's Bounty - Town Map.png"` and `"The Tower at Sea.png"` — neither file is in
   `zz_Attachments/` yet, so both degrade to nothing in the published page. This is expected until
   fixed; to fix it, add the two files to `zz_Attachments/` under exactly the referenced names.

## SETTLED — `--out` points at the player vault itself (Option A)

`~/Documents/Campaign Chronicle/` already exists with a hand-authored structure
(`01 - Chronicle`, `02 - Cast`, `04 - Atlas`, `Home.md`, ...). The build tool writes
`manifest.json` + `content/<slug>.md` + `assets/` (the app reads the manifest; the folder layout
is irrelevant to the app). This was an open question until 2026-08-10; **the GM chose Option A**:

> `--out` IS the Campaign Chronicle vault. The tool's `manifest.json` / `content/` / `assets/`
> live inside it and are the published source; the `01-*` / `02-*` folders remain the GM's own
> Obsidian organization and the app ignores them.

Two behaviors already documented above exist specifically to make Option A safe, and they are the
reason it is a reasonable default rather than a risk: the pre-zip re-scan is scoped to the tool's
own managed outputs (so an in-world `[!danger]` in a hand-authored note cannot false-positive-abort
a clean publish), and the archive is written to a temp path so the vault never accrues a stray
`chronicle.zip`. The tool also never deletes `--out` or anything in it.

Option B (a dedicated build folder) remains supported; nothing in the tool assumes A.

## Platform note — the tool runs on Windows too

Every path in this runbook is a Mac path, which is where the vault has historically lived. The
tool itself is platform-agnostic and was exercised end to end on Windows 11 on 2026-08-10
(`.venv/Scripts/python.exe tools/chronicle_build.py ...`, forward or backslashes both fine).

What was verified there, against a real running app rather than in dry-run:

- A **real, non-dry-run publish** in `--mode player-vault` against a live `--publish-url`
  (`tests/fixtures/player_vault_sample`, 6 pages) — the first time that path had been run against
  a real publish URL at all. `/chronicle` then served the published content.
- The **leak gate** aborts in player-vault mode: `tests/fixtures/player_vault_leak_sample` exits 1,
  writes no zip, and leaves `--out` untouched **even with `--publish-url` supplied**.
- **Rollback** works, including the Windows directory-symlink case that `os.replace` cannot do
  (`_chronicle_repoint` handles it). After a second publish, `can_rollback` flips true and
  `POST /api/chronicle/rollback` returns `current` to the prior publish.
- `can_rollback` is **false after a first-ever publish** — there is no previous tree to return to.
  Worth knowing before the very first prod publish: that one has no undo, only a re-publish.
