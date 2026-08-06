# Deploying GM_pf2e to Railway

Production runs on **Railway**, which **auto-deploys the `main` branch**. The app
is a single **gunicorn gevent worker** (see `Procfile` / `railway.toml`) — this is
mandatory: SSE (live tracker, roll feed) relies on one worker with greenlets, so
never raise `--workers` above 1.

Persistent data lives on a **Railway volume** mounted at `/data` (set
`DATA_DIR=/data`). Everything the app writes — accounts, campaigns, party data,
uploads — is under there. Never write to the app's own filesystem; it's ephemeral
and wiped on every deploy.

> The app is **multi-system + account-based**: users log in, then pick a campaign
> (each campaign declares its system — PF2e or Cosmere), and the whole app
> presents that system. The first account is created at `/setup`.

---

## Environment variables

| Var | Needed | Why |
|-----|--------|-----|
| `DATA_DIR` | **yes** | Point at the mounted volume, e.g. `/data`. All persistent data lives here. |
| `SECRET_KEY` | **yes** | Flask session signing key. **If unset it is randomized per process**, so every restart/redeploy logs everyone out. Set a long, stable, random string. |
| `SETUP_TOKEN` | **yes** | Gates `/setup` (first-admin bootstrap). Without it, anyone who reaches the URL before you do can claim the admin account. Set a random string; you enter it on the setup form. |
| `FLASK_DEBUG` | recommended | Set `false` in production. |
| `GM_PASSWORD` | optional | Legacy GM gate (pre-accounts mode). Once accounts exist the GM is a campaign member, so this isn't the primary auth and can be left unset. |

---

## Routine updates (already on accounts)

```bash
git add -A && git commit -m "…" && git push      # to main
```

Railway auto-deploys. Volume data persists across deploys.

---

> **The two sections below are historical.** They document the one-time
> accounts + campaigns cutover, which has already happened. Keep them for the
> rollback notes; don't follow them as current instructions. In particular,
> where they say the map is removed — it isn't. The tactical map is live in the
> app again (see `CLAUDE.md` → Architecture → Tactical map), though it has not
> shipped to Railway yet.

## Staging deploy — preview the new login + campaign/character select risk-free

The branch `phase1-accounts-campaigns` is on GitHub. Stand up a **separate**
Railway service from it so you can click through the whole new flow without
touching production.

1. Railway → **New Service** → deploy from the `GM_pf2e` repo, **branch =
   `phase1-accounts-campaigns`** (a brand-new service, not the prod one).
2. Add a **Volume** to it, mount path `/data`.
3. Set variables: `DATA_DIR=/data`, `SECRET_KEY=<random>`, `SETUP_TOKEN=<random>`,
   `FLASK_DEBUG=false`. (Leave `GM_PASSWORD` unset.)
4. **Settings → Networking → Generate Domain.** Open the staging URL — it boots
   empty (no data yet).
5. Go to `/setup`, enter your `SETUP_TOKEN`, create your admin/GM account → you
   land on `/me` (the new "select your game" home).
6. Create a **Cosmere RPG** campaign, build a character, mint a player invite —
   exercise the entire flow. None of this affects production.

When staging looks right, do the production cutover.

---

## Production cutover — flip the live table to accounts + campaigns

Merging this branch to `main` deploys the whole pivot (accounts, campaigns,
Cosmere; the map was removed *at the time of this cutover* and has since been
reinstated). The cutover has a **deliberate trigger**: deploying alone
keeps the existing PF2e game working in *legacy mode*; running `/setup` is what
flips it to accounts and migrates the data.

**Before merging**

1. **Back up the volume** — Railway → the prod volume → snapshot, or archive
   `/data` and download it. This is the rollback safety net.
2. **Dry-run the migration** against current prod data:
   `DATA_DIR=/data python3 tools/migrate_to_campaigns.py --dry-run` — confirms the
   legacy game maps cleanly into Campaign #1. The migration **leaves the original
   flat files in place** as a backup.
3. **Set env vars** on the prod service: `SECRET_KEY` (stable!), `SETUP_TOKEN`,
   `DATA_DIR=/data`.
4. Open a PR `phase1-accounts-campaigns` → `main`; let **CI** (pytest +
   template-parse) go green; merge. Railway auto-deploys.

**After deploy — do this outside a session window**

5. The app is live in legacy mode: your existing PF2e game still works, the map
   is gone (as of that cutover), Cosmere is available but no campaigns exist yet.
6. Hit `/setup`, enter `SETUP_TOKEN`, create your **GM/admin** account. This
   auto-migrates the flat game into "Campaign #1" (your party + PCs) and turns on
   the login wall + campaign/character select.
7. Verify the migrated campaign (party + PCs present), then mint **player invite
   codes** at `/campaign/<id>/invites` and send them to your players.

**Rollback** — the migration leaves the original flat files untouched, so
reverting `main` returns to the previous app reading those files. Keep the
volume backup from step 1 until you've confirmed the new world is healthy.

---

## Tactical map — deploy constraints

The map has not shipped yet. When it does, three things matter operationally:

- **Scenes and map art live on the volume.** `core/storage.py` writes scene JSON
  and uploaded backgrounds to `DATA_DIR/campaigns/<cid>/scenes/` (and
  `DATA_DIR/scenes/` when there is no campaign). With `DATA_DIR=/data` that is
  correct and survives deploys. Both paths are gitignored — never commit them.
- **Volume growth is currently unbounded.** Backgrounds are capped at 25 MB
  *each*, there is no cap on scene count, no recompression, and **no
  delete-scene route exists**. Watch the volume, or fix that before heavy use.
- **The one-worker rule is now load-bearing for correctness, not just SSE.**
  Scene writes serialize through `app._path_lock`, which is an **in-process**
  lock. Raising `--workers` above 1 would let two workers clobber whole scene
  files — on top of breaking SSE. Do not raise it.

---

## Player vs GM access

- Players log in and land on their **player hub** for the active system
  (PF2e `/player`, Cosmere `/cosmere/player`).
- The GM lands on the **GM side** (PF2e `/gm`, Cosmere `/cosmere/pcs`).
- Every system is required to declare both hubs in its registry entry
  (`systems/<key>/__init__.py` → `SystemUI`), so this split holds for any future
  system automatically.
