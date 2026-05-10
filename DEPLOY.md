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
