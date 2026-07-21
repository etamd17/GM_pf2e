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
  (a long, high-entropy secret), and pass the same value to the CLI as `--token`. The tool sends it
  as the `X-Chronicle-Token` header; the app's `check_gm_access` allows it for `/api/chronicle*`
  ONLY (it unlocks nothing else). A wrong/absent token, or an unset server env, still 403s.

```bash
# On the app host (Railway variables): CHRONICLE_PUBLISH_TOKEN=<generated-secret>
# On the Mac, at publish time:
python tools/chronicle_build.py ... --publish-url https://<app>/api/chronicle/publish \
  --token "$CHRONICLE_PUBLISH_TOKEN"
```

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

## OPEN ITEM — reconcile the tool output with the existing player vault

`~/Documents/Campaign Chronicle/` already exists with a hand-authored structure
(`01 - Chronicle`, `02 - Cast`, `04 - Atlas`, `Home.md`, ...). The build tool writes
`manifest.json` + `content/<slug>.md` + `assets/` (the app reads the manifest; the folder layout
is irrelevant to the app). These coexist safely (the tool never touches the other folders), but
decide before the first real publish:
- **Option A:** point `--out` at the existing Campaign Chronicle vault; the tool's
  `manifest.json`/`content/`/`assets/` become the published source, and your `01-*` folders remain
  your own Obsidian organization (ignored by the app).
- **Option B:** point `--out` at a dedicated/fresh folder used only as the build+publish artifact,
  keeping it separate from the hand-authored vault.
This is a GM preference, not a code decision; the tool supports either.
