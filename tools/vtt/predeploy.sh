#!/bin/sh
# The pre-deploy step (P-1, production-plan.md G2): reviewed, database-only
# migrations, then the asset-store probe. Railway runs it in a separate
# container: volumes are NOT mounted and file changes are discarded, so nothing
# here may touch /data. A failure blocks the new deployment; it does not undo a
# migration that already committed, which is why every migration released this
# way must be expand-only and safe for the image still running.
#
#   sh tools/vtt/predeploy.sh
#
# With no DATABASE_URL (the site in JSON mode with the VTT off) there is
# nothing to migrate, and with the VTT off there is no asset store to probe.
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  # Bounded waits: a migration that cannot take its lock, or runs away, fails
  # the deploy instead of stalling the live site behind it.
  PGOPTIONS="-c lock_timeout=${MIGRATION_LOCK_TIMEOUT:-10s} -c statement_timeout=${MIGRATION_STATEMENT_TIMEOUT:-300s}" \
    python -m alembic upgrade head
else
  echo "predeploy: no DATABASE_URL, no migrations to run"
fi

if [ "${VTT_ENABLED:-}" = "1" ]; then
  python tools/vtt/check_asset_store.py
else
  echo "predeploy: VTT off, no asset store to probe"
fi
