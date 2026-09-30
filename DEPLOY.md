# Railway deployment runbook

Production auto-deploys the `main` branch to Railway. Treat a green pull request
as necessary but not sufficient: deploy through staging first and keep a current
backup of the persistent volume.

## Production invariants

- Run exactly one Railway replica and one Gunicorn gevent worker. SSE fan-out,
  rate limiting, session revocation, setup coordination, and several caches are
  process-local. More workers or replicas can split clients and bypass those
  guarantees until that state is moved to a shared service.
- Mount one Railway volume at `/data` and set `DATA_DIR=/data`. Accounts,
  campaigns, uploads, and health sentinels must live on that volume; the image
  filesystem is ephemeral.
- Keep `overlapSeconds = 0`. A mounted volume cannot safely serve two overlapping
  deployments. `drainingSeconds = 30` gives open requests and SSE clients a short
  shutdown window before they reconnect.
- `Procfile` and `railway.toml` must carry the same one-worker command. CI checks
  this invariant.
- Railway installs the hash-locked `requirements.txt`. Direct dependency intent
  lives in `requirements.in`; never hand-edit generated lock files.

`railway.toml` keeps the established service on Nixpacks 1.41.0. Two controlled
production releases using Railpack failed before application startup while the
last Nixpacks image remained healthy, so treat a future builder migration as a
separate staging change with build-log access. Railway's newer project-level
configuration format requires live project, service, and volume identities, so
migrate it only with `railway config pull` from the production project and review
the generated `.railway/railway.ts`; do not invent those identifiers in source
control.

The builder version is pinned in `railway.toml`. Nixpacks reads `runtime.txt` as
a Python major/minor selector, so `nixpacks.toml` also pins a cache-backed
Nixpkgs snapshot whose `python311` package is 3.11.16, keeping the production
patch release aligned with CI. Update and verify both pins when changing the
builder or `runtime.txt`.

## Required production configuration

| Setting | Required value |
| --- | --- |
| `DATA_DIR` | `/data`, backed by the mounted production volume |
| `SECRET_KEY` | Stable, random secret of at least 32 bytes; never a checked-in value. Recommended on new deployments; when unset, the app verifies and reuses the generated `.secret_key` on the mounted `DATA_DIR` volume |
| `SETUP_TOKEN` | Separate random secret of at least 24 bytes while first-admin setup is available; remove or rotate it after bootstrap |
| `FLASK_DEBUG` | `false` |
| `PUBLIC_BASE_URL` | Exact public HTTPS origin (no path, query, fragment, or credentials). On Railway this defaults to `https://$RAILWAY_PUBLIC_DOMAIN`; set it explicitly when a custom domain must be canonical |
| `TRUST_PROXY_HOPS` | Leave unset on Railway: its one Host/Proto hop and injected `X-Real-IP` are handled separately. For a custom reverse proxy, set the exact known hop depth from `0` through `3`; direct deployments default to `0` |
| `GM_PASSWORD` | Unset after account-based access is enabled |
| Railway replicas | `1` |

Use different secrets and a different volume for staging. Never attach the
production volume to a preview or staging service.

Legacy deployments that set only `GM_PASSWORD` retain Secure cookies, but a
non-Railway deployment must set `APP_ENV=production` to enable the complete
production request boundary and readiness validation.

After deploying PR3, legacy single-table GMs must sign in once again. Cookies
issued before the password-derived session epoch existed are intentionally
rejected so rotating `GM_PASSWORD` reliably revokes every older GM session.

## Health and monitoring contract

- `GET /live` is a minimal public process-liveness probe. It returns success
  whenever Flask can serve a request and exposes no paths, versions, users, or
  storage details.
- `GET /ready` is the public Railway deployment health check. It fails closed
  unless required production configuration and writable persistent storage are
  available. Its response is intentionally generic. Railway sends this probe
  with `Host: healthcheck.railway.app`; the application admits that exact Host
  only to `/live`, `/ready`, and `/health`, never to normal application routes.
- Railway allows 120 seconds for cold-start data loading before rejecting a new
  deployment; an unhealthy `/ready` response still prevents traffic promotion.
- Detailed storage diagnostics remain authenticated and GM-only; do not put
  absolute paths, exception strings, or configuration values in a public probe.

Railway's health check protects a deployment transition; it is not continuous
uptime monitoring. Configure an external monitor for `/live` and alert on
several consecutive failures. Alert separately when authenticated storage
diagnostics show a reset boot counter or an unwritable volume.

## Reproducible dependencies

The input files contain exact direct pins. Generated files contain the full
cross-platform Python 3.11 resolution and SHA-256 hashes. The generator itself is
pinned in `requirements-dev.in`.

```bash
python -m pip install "uv==0.11.23"
uv pip compile requirements.in --universal --python-version 3.11 --generate-hashes --custom-compile-command "uv pip compile requirements.in --universal --python-version 3.11 --generate-hashes -o requirements.txt" -o requirements.txt
uv pip compile requirements-dev.in --universal --python-version 3.11 --generate-hashes --custom-compile-command "uv pip compile requirements-dev.in --universal --python-version 3.11 --generate-hashes -o requirements-dev.txt" -o requirements-dev.txt
python -m pip install --require-hashes -r requirements-dev.txt
python -m pip check
python -m pip_audit --strict --require-hashes -r requirements-dev.txt
```

When updating a dependency, edit the applicable `.in` file, regenerate both
locks, run the checks above and the test suite, and commit inputs and generated
outputs together. Dependabot opens weekly dependency and GitHub Actions updates;
review and regenerate both lock files rather than merging a partial update.
CI regenerates over temporary copies of the committed lock files, so its
freshness check preserves existing transitive pins instead of silently resolving
the newest packages on every run.

## Production runtime smoke

After installing `requirements.txt` into a clean virtual environment, run this
on Linux or in WSL with that environment first on `PATH`:

```bash
python tools/smoke_production_runtime.py
```

CI creates that production-only environment from the hash-locked
`requirements.txt`, runs `pip check`, imports Gunicorn's gevent worker, and then
runs the same command. Keeping development tooling out of this environment
prevents its transitive dependencies from masking a missing production runtime
dependency. The smoke launches the exact `railway.toml` start command in
production mode with a temporary `DATA_DIR`, bootstraps an admin, creates and
activates a campaign, opens an authenticated SSE stream, proves `/ready` can run
concurrently through the Railway probe Host, triggers a GM mutation, observes
its SSE event, and requires Gunicorn to exit cleanly on `SIGTERM`. Gunicorn does
not run natively on Windows, so Windows development relies on WSL or the Linux CI
gate. The temporary data is discarded and no external service is contacted.

This smoke validates the application server boundary, not the image builder,
Railway proxy, or volume attachment. Keep the staging checks below.

## Pre-deploy checklist

1. Confirm CI passed: lock-freshness and hash-locked install checks, `pip check`,
   `pip-audit`, deployment invariant checks, the production runtime smoke,
   template parse, and the full test suite.
2. Confirm the target is staging, with its own `/data` volume and credentials.
3. Confirm staging uses one replica and that its variables match the table above.
4. Back up the production volume and record how to restore that backup.
5. Record the last known-good commit and Railway deployment ID.
6. Exercise login, campaign selection, a GM mutation, a player mutation, and an
   SSE update on staging. Verify a second browser cannot see another campaign.
7. Restart staging once; verify data remains and `/ready` becomes successful.

## Deploy

1. Merge the reviewed pull request to `main`; do not bypass required CI checks.
2. Watch Railway build and deploy logs. Stop if dependency installation is not
   hash-verified, `/ready` fails, or Railway attempts overlapping replicas.
3. Confirm the new deployment is healthy before routing normal traffic.

## Post-deploy verification

For at least 15 minutes after release:

1. Request `/live` and `/ready` from outside Railway and confirm successful,
   generic responses.
2. Log in as a GM and a player in separate browsers. Verify campaign isolation,
   one write from each role, and SSE reconnect after a page refresh.
3. Verify an uploaded file and a changed character survive one controlled
   restart. Confirm the persistent boot counter advances rather than resetting.
4. Watch 5xx rate, restarts, memory, open connections, readiness failures, and
   repeated SSE reconnects. Record the deployment result.

## Rollback

Rollback immediately for authorization leakage, missing/corrupt data, repeated
restart loops, sustained 5xx responses, failed readiness, or unusable SSE.

1. In Railway, redeploy the recorded last-known-good image or revert the merge.
2. Keep one replica and the same volume attached; do not create an overlapping
   deployment to the mounted volume.
3. Re-run `/live`, `/ready`, login, campaign-isolation, mutation, and persistence
   checks.
4. If data was mutated incompatibly, stop writes and restore the pre-deploy
   volume backup using the documented Railway procedure.

An application-image rollback does not roll back files on the volume. Never
restore the volume merely because code was rolled back; restore only for a
confirmed data-format or corruption incident, and preserve the failed volume for
forensics.
