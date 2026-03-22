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
