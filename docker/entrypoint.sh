#!/bin/sh
# Web container start (M6, P-1): optionally apply migrations, then run the app
# exactly as production does (Procfile): ONE gevent worker, which the SSE hub
# and the in-process VTT fan-out both require. Never raise --workers.
#
# RUN_MIGRATIONS (P-1, production-plan.md G1/G2):
#   unset or 0  no database change at start (production: migrations run in the
#               pre-deploy step, tools/vtt/predeploy.sh, never here);
#   1           run Alembic first (local development and compose);
#   anything    else fails at start, so a typo never silently skips or runs them.
# With them off and a required schema missing, /ready fails (core.persistence.runtime).
set -eu

fail() {
  echo "$1" >&2
  exit 1
}

case "${RUN_MIGRATIONS:-0}" in
  0|1) ;;
  *) fail "RUN_MIGRATIONS must be 0 or 1 (got '${RUN_MIGRATIONS}')" ;;
esac

# A database is needed only by the VTT and by SQL-backed accounts. The site in
# JSON mode with the VTT off (production today) starts without one.
needs_database=0
if [ "${VTT_ENABLED:-}" = "1" ]; then
  needs_database=1
  [ -n "${SECRET_KEY:-}" ] || fail "SECRET_KEY is required with VTT_ENABLED=1 (table grants and version tokens derive from it)"
fi
case "${OWNERSHIP_BACKEND:-json}" in
  sql|shadow) needs_database=1 ;;
esac
if [ "$needs_database" = "1" ] || [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  [ -n "${DATABASE_URL:-}" ] || fail "DATABASE_URL is required (VTT_ENABLED=1, OWNERSHIP_BACKEND=${OWNERSHIP_BACKEND:-json} or RUN_MIGRATIONS=1)"
fi

# Volume ownership, production-plan.md G1 option (b), decided 2026-10-20. A
# Railway volume is root-owned and has no one-off shell, so the one-time
# ownership change runs here, in ONE deploy made with RAILWAY_RUN_UID=0 and
# OWN_DATA_VOLUME=1: the volume is given to the app user (verified), and the
# app is then started as that user, not as root. Remove both variables for
# the next deploy, which runs unprivileged as the image's USER.
drop=""
if [ "${OWN_DATA_VOLUME:-0}" = "1" ]; then
  [ "$(id -u)" = "0" ] || fail "OWN_DATA_VOLUME=1 needs root for this one deploy (RAILWAY_RUN_UID=0); see production-plan.md G1"
  sh /app/tools/vtt/own_data_volume.sh --apply
  app_gid=$(getent group app | cut -d: -f3)
  drop="setpriv --reuid 10001 --regid ${app_gid:-10001} --clear-groups"
elif [ "${OWN_DATA_VOLUME:-0}" != "0" ]; then
  fail "OWN_DATA_VOLUME must be 0 or 1 (got '${OWN_DATA_VOLUME}')"
fi

# The data volume must be writable by the user the app runs as. Railway volumes
# are root-owned: either run as root (RAILWAY_RUN_UID=0, production-plan.md G1
# option a) or give the volume to uid 10001 once (option b, tools/vtt/own_data_volume.sh).
# Failing here is the point: an unwritable volume otherwise loses every save
# quietly, one request at a time.
if [ -n "${DATA_DIR:-}" ] && [ -d "$DATA_DIR" ]; then
  probe="$DATA_DIR/.write-probe.$$"
  if ! ( : > "$probe" ) 2>/dev/null; then
    fail "DATA_DIR ($DATA_DIR) is not writable by uid $(id -u); see production-plan.md G1"
  fi
  rm -f "$probe"
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python -m alembic upgrade head
fi

# --no-control-socket: gunicorn's control socket would need a writable /app.
# shellcheck disable=SC2086 # $drop is deliberately split into its words
exec $drop gunicorn app:app \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 1 \
  --worker-class gevent \
  --worker-connections 1000 \
  --timeout 120 \
  --no-control-socket
