# VTT staging on Railway (M7)

Staging is a separate environment, `vtt-staging`, in the production Railway project
(`gamemaster-tool`). It was created on 2026-10-18 with the user's approval, following VTT-ADR-0003.
Production (`GM_pf2e`, deployed from `main`) shares no service, variable, volume or database with
it.

## What runs there

| Resource | Notes |
| --- | --- |
| `vtt-web` | Built from this repository's `Dockerfile`, branch `agent/claude-vtt-platform`. A push to that branch redeploys staging. The entrypoint applies migrations, then runs gunicorn with ONE gevent worker. |
| `Postgres` | Railway's template, PostgreSQL 18; the local tests run on 17. Railway generates the password. |
| `vtt-data` | A 1 GB volume at `/data`: the app's JSON stores, and VTT images (`VTT_ASSETS_BACKEND=local`). |
| Domain | `https://vtt-web-vtt-staging.up.railway.app`, routed to port 8000. |

**Variables on `vtt-web`.** Read their values in the Railway dashboard; they are not written down
here.

| Variable | Value |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
| `SECRET_KEY` | Generated |
| `SETUP_TOKEN` | Generated; needed once, at `/setup` |
| `OWNERSHIP_BACKEND` | `sql` |
| `VTT_ASSETS_BACKEND` | `local` |
| `VTT_ALLOWED_ORIGINS` | The staging domain |
| `PORT` | `8000` |
| `RAILWAY_RUN_UID` | `0` (see below) |
| `RAILWAY_DOCKERFILE_PATH` | `Dockerfile` |

## Deviations from ADR-0003, and why

- **Deployed from `agent/claude-vtt-platform`, not `side/vtt-rebuild`.** The VTT code lives on the
  platform branch; `side/vtt-rebuild` holds only the docs. Merging the two, or merging `main`
  in, is integration work the user has not approved.
- **The branch is 144 commits behind `main`.** Staging therefore runs an older copy of the rest
  of the site.
- **Images are stored on the volume, not in a bucket.** New Railway buckets use virtual-hosted
  URLs, but `S3Backend` builds path-style URLs only. Teaching it the other style is a small
  follow-up. Staging is a single instance, so the volume is sound.
- **No Redis.** One web process; ADR-0002 brings Redis in only with a second process.
- **`RAILWAY_RUN_UID=0`.** Railway mounts volumes owned by root, and the image's `app` user
  (uid 10001) could not write `/data`. The container runs as root on staging only. Locally,
  compose still runs as `app`.

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
