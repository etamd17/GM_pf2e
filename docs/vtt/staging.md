# VTT staging on Railway (M7)

Staging is a separate environment, `vtt-staging`, in the production Railway project
(`gamemaster-tool`). It was created on 2026-10-18 with the user's approval, following VTT-ADR-0003.
Production (`GM_pf2e`, deployed from `main`) shares no service, variable, volume or database with
it.

## What runs there

| Resource | Notes |
| --- | --- |
| `vtt-web` | Built from this repository's `Dockerfile`, branch `agent/claude-vtt-platform`. A push to that branch redeploys staging. Migrations run in the pre-deploy step (below), never at start; the entrypoint checks its environment and the data volume, then runs gunicorn with ONE gevent worker. |
| `Postgres` | Railway's template, PostgreSQL 18; the local tests run on 17. Railway generates the password. |
| `vtt-data` | A 1 GB volume at `/data`: the app's JSON stores. |
| `vtt-assets` | A Railway bucket (region sjc, virtual-hosted URLs) for VTT images. |
| Domain | `https://vtt-web-vtt-staging.up.railway.app`, routed to port 8000. |

**Variables on `vtt-web`.** Read their values in the Railway dashboard; they are not written down
here.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `SECRET_KEY` | Generated |
| `SETUP_TOKEN` | Generated; needed once, at `/setup` |
| `OWNERSHIP_BACKEND` | `sql` |
| `VTT_ASSETS_BACKEND` | `s3` |
| `VTT_ASSETS_S3_*` | References to the bucket (`${{vtt-assets.ENDPOINT}}`, `BUCKET`, `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`, `REGION`), and `VTT_ASSETS_S3_URL_STYLE=virtual` |
| `VTT_ALLOWED_ORIGINS` | The staging domain |
| `PORT` | `8000` |
| `RAILWAY_RUN_UID` | `0` (see below) |
| `RAILWAY_DOCKERFILE_PATH` | `Dockerfile` |

## Deviations from ADR-0003, and why

- **Deployed from `agent/claude-vtt-platform`, not `side/vtt-rebuild`.** The VTT code lives on the
  platform branch; `side/vtt-rebuild` holds only the docs. Merging the two, or merging `main`
  in, is integration work the user has not approved.
- **The rest of the site follows `main` only when it is merged in.** `main` was merged into the
  branch on 2026-10-18 at `a9be6450`, with the user's approval. Merge again to bring staging's
  copy of the site up to date.
- **No Redis.** One web process; ADR-0002 brings Redis in only with a second process.
- **`RAILWAY_RUN_UID=0`.** Railway mounts volumes owned by root, and the image's `app` user
  (uid 10001) could not write `/data`. The container runs as root on staging only. Locally,
  compose still runs as `app`.

## Pre-deploy step

Since P-1 (2026-10-20) every deploy first runs `sh tools/vtt/predeploy.sh`, the same step
production will run:

1. `alembic upgrade head`, with `lock_timeout=10s` and `statement_timeout=300s` (override with
   `MIGRATION_LOCK_TIMEOUT` / `MIGRATION_STATEMENT_TIMEOUT`), so a migration that cannot take its
   lock fails the deploy instead of stalling the live site;
2. `python tools/vtt/check_asset_store.py`, which round-trips one probe object through the
   configured asset store (key `vtt-probe/<sha256>`).

It runs in a separate container without the volume. A failure blocks the new deployment, but a
migration that already committed stays, so every migration must be expand-only and safe for the
image still running. `RUN_MIGRATIONS` is unset on staging: the entrypoint makes no database
change, and `/ready` fails if the VTT's schema is not the one the release needs.

## First use

1. Open the domain and go to `/setup`. Enter the `SETUP_TOKEN` from the Railway dashboard and
   create the admin account.
2. Create a campaign, then open `/vtt/gm?campaignId=<id>`.
3. Pair the TV: on the GM page choose "Pair a display", and open `/vtt/table` on the TV. The
   pairing cookie is `Secure`, and Railway's HTTPS domain allows it.

## Costs

- Usage-based: the web service and PostgreSQL bill for CPU and memory while running, and the
  volumes bill by size.
- The idle footprint is one small Python process and one small PostgreSQL.
- Check the project's usage page after a session.
- Sleeping the services when unused, or deleting the environment, stops the charges.

## Removing staging

Delete the `vtt-staging` environment in the dashboard. Its services, volumes and database go
with it, and production is unaffected.
