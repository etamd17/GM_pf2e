# Deploying GM_pf2e to Railway

## Prerequisites
- A [Railway](https://railway.app) account ($5/mo Hobby plan)
- A [GitHub](https://github.com) account
- Git installed on your Mac

## Step 1: Organize Your Project

Make sure your project has this structure:
```
~/GM_pf2e/
├── app.py
├── class_matrix.py
├── pf2e_database.db
├── pf2e_generator.py
├── build_db.py
├── requirements.txt
├── Procfile
├── railway.toml
├── runtime.txt
├── .gitignore
└── templates/
    ├── base.html
    ├── player_sheet.html
    ├── player_view.html
    ├── player_builder.html
    ├── player_levelup.html
    ├── tracker.html
    ├── party_view.html
    ├── vault.html
    ├── gmscreen.html
    ├── encounter_builder.html
    └── generator.html
```

## Step 2: Push to GitHub

```bash
cd ~/GM_pf2e
git init
git add -A
git commit -m "Initial deploy"
```

Create a PRIVATE repo at https://github.com/new called GM_pf2e, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/GM_pf2e.git
git branch -M main
git push -u origin main
```

## Step 3: Deploy on Railway

1. Go to https://railway.app and sign in
2. Click "New Project" then "Deploy from GitHub Repo"
3. Select your GM_pf2e repository
4. Railway auto-detects Python and starts building

## Step 4: Add Persistent Storage

1. Click your service then Settings then Volumes
2. Click "+ New Volume"
3. Mount path: `/data`
4. Click "Create Volume"

## Step 5: Set Environment Variables

In your service Variables tab, add:

- `DATA_DIR` = `/data`
- `FLASK_DEBUG` = `false`
- `GM_PASSWORD` = (pick a password only you know)

The GM_PASSWORD protects all GM pages (tracker, vault, party view,
encounter builder, GM screen). Players only see /player/* routes.
When you visit /tracker or /party etc, you will be prompted for the password.
Locally with no GM_PASSWORD set, everything is open as before.

## Step 6: Generate a Domain

1. Settings then Networking
2. Click "Generate Domain"
3. Share the URL with your players

Players go to: yourapp.up.railway.app (lands on Player Hub)
You go to: yourapp.up.railway.app/gm/login (enter password, then full GM access)

## Updating

```bash
cd ~/GM_pf2e
git add -A
git commit -m "description of changes"
git push
```

Railway auto-deploys. Character data on the volume persists.

## Step 7 (optional): Obsidian vault sync

The Notes feature reads campaign content from `vault_data/` on the server.
That directory needs a Railway persistent volume so it survives redeploys,
and a separate `tools/push_vault.py` run from your Mac to populate it.

### Attach a Railway volume

1. Project → service → **Variables** tab → **+ New Variable**:
   `PF2E_VAULT_DATA = /app/vault_data`
2. **Settings** tab → scroll to **Volumes** → **+ Add Volume**:
   - Mount path: `/app/vault_data`
   - Size: 1 GB is plenty for typical campaign content (no SRD needed)
3. Redeploy. The volume is now empty; the next push will fill it.

### Push your vault from your Mac

```bash
cd ~/GM_pf2e
python3 tools/push_vault.py --url https://yourapp.up.railway.app
```

You'll be prompted for the GM password (set on Railway as `GM_PASSWORD`).
The first run uploads everything (minus `zzrules/` SRD content, large
attachments > 5 MB, and per-machine `.obsidian/` config). Subsequent runs
ship only files you've modified since the last push.

Common one-liner aliases for `~/.zshrc`:

```bash
alias pf2e-push='cd ~/GM_pf2e && python3 tools/push_vault.py --url https://yourapp.up.railway.app'
alias pf2e-pull='cd ~/GM_pf2e && python3 tools/pull_vault.py --url https://yourapp.up.railway.app'
```

### Pull session-export markdown back to your local vault

When you click **End Session — Export** on the GM hub, the app writes a
session-recap markdown into `vault_data/Sessions/` on the server. To bring
that file back to your local Obsidian vault for editing:

```bash
python3 tools/pull_vault.py --url https://yourapp.up.railway.app
```

Pulls only files modified after your last successful push. Safe to run
repeatedly.

### When to push

- Before each session, after prep in Obsidian
- After Cowork-written summaries land in your local vault
- Whenever you've added new NPCs / locations you want available in the
  drawer during the session

## Step 8 (recommended): Git-backed vault sync — no more manual push

This replaces the `tools/push_vault.py` workflow with a continuous,
bidirectional sync. Once configured:

- Edit in Obsidian → Obsidian Git plugin auto-commits + pushes every N
  minutes → Railway's background poller picks it up.
- Edit on the website (or click "End Session — Export") → server
  immediately `git commit` + `git push`s the change to the same repo
  → Obsidian Git pulls it on its next interval and it shows up in your
  local vault.

You keep using `push_vault.py` for the **first** upload (or anytime you
want a clean bulk reset). After that, all sync is automatic.

### One-time setup

**1. Create a private GitHub repo for the vault**

Make a new repo at https://github.com/new — name it something like
`pf2e-vault`, mark it **Private**. Don't initialize with anything
(no README, no .gitignore).

**2. Bootstrap the repo from your local vault**

```bash
cd ~/Documents/Pathfinder\ Campaigns   # or wherever your vault lives
git init -b main
echo "zzrules/" > .gitignore   # optional: skip the 16k SRD subtree
echo ".obsidian/workspace*" >> .gitignore
git add .
git commit -m "initial vault import"
git remote add origin https://github.com/YOUR_USERNAME/pf2e-vault.git
git push -u origin main
```

**3. Install Obsidian Git inside Obsidian**

Settings → Community plugins → Browse → search "Obsidian Git" → install
→ enable. Then in its settings:
- **Auto pull interval (minutes):** 5
- **Auto push interval (minutes):** 5
- **Commit message:** anything — `vault: {{date}} {{hostname}}` works
- (Optional) Enable "Pull updates on startup"

The plugin will now keep your local vault and the GitHub repo in sync
without any manual `git push`.

**4. Generate a GitHub Personal Access Token for Railway**

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained
tokens → **Generate new token**. Configure:
- **Token name:** `pf2e-railway-vault-sync`
- **Expiration:** 1 year (or however long you're comfortable)
- **Resource owner:** your account
- **Repository access:** Only select repositories → pick `pf2e-vault`
- **Permissions:** Repository permissions → **Contents: Read and write**

Copy the token (`github_pat_...`) — you'll only see it once.

**5. Add env vars on Railway**

Project → service → Variables → **+ New Variable** for each:

```
PF2E_VAULT_GIT_URL          https://github.com/YOUR_USERNAME/pf2e-vault.git
PF2E_VAULT_GIT_TOKEN        github_pat_...   (from step 4)
PF2E_VAULT_GIT_BRANCH       main
PF2E_VAULT_PULL_INTERVAL_SEC 120
PF2E_VAULT_GIT_USER_NAME    PF2E Bot
PF2E_VAULT_GIT_USER_EMAIL   pf2e-bot@noreply
```

Redeploy. On the first boot, the server clones your vault repo into
`/app/vault_data` and starts the background poller.

### Verify it's working

`https://yourapp.up.railway.app/api/notes/health` returns a JSON blob
that now includes a `git_sync` section:

```json
{
  "available": true,
  "source": "vault_data",
  "git_sync": {
    "enabled": true,
    "branch": "main",
    "initialized": true,
    "last_pull_ok": true,
    "last_pull_at": 1715300000.0,
    "head_sha": "a1b2c3d...",
    "pull_interval_sec": 120
  }
}
```

If `enabled: false` → an env var is missing. If `initialized: false` →
the clone failed; check `last_pull_error` for the underlying git error
(invalid token, wrong URL, etc.).

### What this changes

| Edit happens here | Reaches the other side via | Latency |
|---|---|---|
| Obsidian on your Mac | Obsidian Git auto-push → Railway background pull | ≤ 5 min + 2 min |
| Website / drawer editor | Server immediate commit+push → Obsidian Git auto-pull | < 1 sec + ≤ 5 min |
| "End Session — Export" | Same as above — written to `Sessions/<title>.md` | < 1 sec + ≤ 5 min |

You never need to run `push_vault.py` again unless you want to do a
clean bulk re-upload. `pull_vault.py` is also obsolete — your local
vault is already in sync.
