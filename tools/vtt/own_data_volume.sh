#!/bin/sh
# Volume ownership, production-plan.md G1 option (b): give the data volume to
# the image's unprivileged app user (uid 10001) ONCE, then run without root.
#
# An operator runs this deliberately, as root, in a one-off container that has
# the volume mounted (Railway: a temporary root run of this image with
# RAILWAY_RUN_UID=0, then remove that variable). It changes ownership only;
# it never deletes, moves or rewrites a file. It prints a before/after count
# and verifies the app user can write, so the step leaves evidence.
#
#   sh tools/vtt/own_data_volume.sh            # dry run: report only
#   sh tools/vtt/own_data_volume.sh --apply    # change ownership
set -eu

DATA="${DATA_DIR:-/data}"
APP_UID=10001
# The image's useradd makes an "app" group with a system gid, not 10001.
APP_GID=$(getent group app 2>/dev/null | cut -d: -f3)
APP_GID=${APP_GID:-$APP_UID}

[ -d "$DATA" ] || { echo "no data directory at $DATA" >&2; exit 1; }
total=$(find "$DATA" | wc -l)
foreign=$(find "$DATA" ! -uid "$APP_UID" | wc -l)
echo "$DATA: $total entries, $foreign not owned by uid $APP_UID"

if [ "${1:-}" != "--apply" ]; then
  echo "dry run: nothing changed (pass --apply to change ownership)"
  exit 0
fi
[ "$(id -u)" = "0" ] || { echo "--apply must run as root" >&2; exit 1; }

chown -R "$APP_UID:$APP_GID" "$DATA"
left=$(find "$DATA" ! -uid "$APP_UID" | wc -l)
echo "after: $left entries not owned by uid $APP_UID"
[ "$left" = "0" ] || { echo "ownership change incomplete" >&2; exit 1; }

# Verify as the app user, the way the entrypoint will.
if command -v setpriv >/dev/null 2>&1; then
  setpriv --reuid "$APP_UID" --regid "$APP_GID" --clear-groups sh -c ": > '$DATA/.owner-check' && rm '$DATA/.owner-check'"
  echo "verified: uid $APP_UID can write $DATA"
else
  echo "setpriv not available: verify by starting the image without RAILWAY_RUN_UID (the entrypoint checks)"
fi
