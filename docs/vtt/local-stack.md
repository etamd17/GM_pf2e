# The VTT local stack

This is the local Docker stack from VTT-ADR-0003, built in M6 (handoff 053). It runs the whole
site with the VTT switched on, backed by:
- PostgreSQL;
- an S3-compatible asset store.

Production (`main` on Railway) is not affected. It still builds Python-only and does not have
the VTT switched on.

## Run it

```bash
cp docker/env.example .env      # set every value; .env is gitignored
docker compose up --build       # then open http://localhost:8000
```

The first start:
- builds the client inside the image (`npm ci`, build, bundle check);
- applies the migrations;
- creates the asset bucket.

Create the first account at `/setup`, make a campaign, then open `/vtt/gm?campaignId=<id>`.

| Service | What it is |
| --- | --- |
| `web` | The Flask app: one gevent worker, exactly as `Procfile` runs it. `DATA_DIR` is the `app-data` volume. |
| `db` | PostgreSQL 17, the same major version as CI and the local test container. |
| `assets` | A SeaweedFS S3 gateway holding uploaded backgrounds and token art (bucket `vtt-assets`). |
| `assets-bucket` | A one-shot job that creates the bucket and exits. |
| `lan-proxy` | Profile `lan` only: Caddy with a local CA, for a real TV (see below). |

**Why not MinIO.** ADR-0003 named MinIO, but MinIO no longer publishes community images:
- Docker Hub answers "access denied";
- quay.io has no tags.

The app only speaks S3 (Signature V4), so any compatible store works. Railway's object storage
would be the staging choice.

**Why no Redis or worker.** In ADR-0002 Redis only wakes up delivery and fans it out;
PostgreSQL and the outbox stay the source of truth. With the single gevent worker that
production requires, the app already fans out within its own process. Redis and a worker first
matter, and can first be tested, when there is more than one web process.

## A real TV on the LAN

The table display's pairing cookie is `Secure`. A TV cannot store it over plain HTTP to a LAN
address (`localhost` is the browser's only exception). So:

1. Pick a name the TV can resolve to this machine, for example `vtt.lan` in the router's DNS or
   the TV's hosts file.
2. In `.env`, set `VTT_LAN_HOST=vtt.lan` and `VTT_ALLOWED_ORIGINS=https://vtt.lan`.
3. Run `docker compose --profile lan up --build`.
4. Install Caddy's root certificate on the TV once. It is at
   `/data/caddy/pki/authorities/local/root.crt` in the `caddy-data` volume:
   `docker compose cp lan-proxy:/data/caddy/pki/authorities/local/root.crt .`
5. On the TV, open `https://vtt.lan/vtt/table` and pair it with a code from the GM page.

## Asset storage outside compose

`VTT_ASSETS_BACKEND` chooses where images go:

- **`local`** (the default): files under `VTT_ASSETS_DIR`, or `DATA_DIR/vtt-assets`. Fine for a
  single machine or one volume.
- **`s3`**: needs these four:
  - `VTT_ASSETS_S3_ENDPOINT`
  - `VTT_ASSETS_S3_BUCKET`
  - `VTT_ASSETS_S3_ACCESS_KEY`
  - `VTT_ASSETS_S3_SECRET_KEY`

  `VTT_ASSETS_S3_REGION` is optional and defaults to `us-east-1`.

## Tests

| What | Command |
| --- | --- |
| Python VTT suite | `DATABASE_URL=postgresql+psycopg://... pytest tests/vtt -m "not browser"` |
| Client | `npm run typecheck && npm test && npm run build && npm run check:bundle` (in `frontend/vtt`) |
| Browser harness | `pytest tests/vtt/e2e -m browser` |

The browser harness:
- needs `requirements-browser.txt` installed and Chromium;
- uses SQLite unless `VTT_E2E_DATABASE_URL` is set.

`.github/workflows/vtt.yml` runs all of these and builds the image. It first runs when the VTT
branch is pushed, which needs the user's approval.
