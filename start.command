#!/bin/bash
# Game-night launcher — double-click from Finder.
#
# Opens the Flask app in the Terminal that owns this script (which
# inherits Full Disk Access from your normal Terminal grant), then
# pops the GM hub in your default browser when the server is up.
#
# To put this in your Dock:  drag start.command onto the right side
# of the Dock, or alias it in your shell profile (see end of file).

set -e

# --- Resolve the repo root regardless of where the .command lives ---
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"

# --- Prefer a project venv if one exists, else fall back to system python3 ---
if [ -d "venv" ] && [ -f "venv/bin/python3" ]; then
    PY="$SCRIPT_DIR/venv/bin/python3"
    PIP="$SCRIPT_DIR/venv/bin/pip3"
elif [ -d ".venv" ] && [ -f ".venv/bin/python3" ]; then
    PY="$SCRIPT_DIR/.venv/bin/python3"
    PIP="$SCRIPT_DIR/.venv/bin/pip3"
else
    PY="$(command -v python3)"
    PIP="$(command -v pip3)"
fi

echo ""
echo "================================================================"
echo "  PF2e GM Dashboard"
echo "================================================================"
echo "  Repo:    $SCRIPT_DIR"
echo "  Python:  $PY"
echo ""

# --- Make sure required deps are present (cheap noop on subsequent runs) ---
if ! "$PY" -c "import flask, markdown, yaml" 2>/dev/null; then
    echo "Installing missing dependencies..."
    "$PIP" install --quiet -r requirements.txt
fi

# --- Open the GM hub once the server is listening ---
PORT=5001
URL="http://127.0.0.1:${PORT}/gm"
(
    # Background poll: hit the port until it answers, then open browser.
    for _ in $(seq 1 40); do
        if /usr/bin/curl -s -o /dev/null -w '%{http_code}' "${URL}" 2>/dev/null | grep -q '^[23]'; then
            /usr/bin/open "$URL"
            exit 0
        fi
        sleep 0.5
    done
) &

echo "Starting Flask on http://127.0.0.1:${PORT} ..."
echo "Press Ctrl+C in this window to stop the server."
echo ""

# --- Run Flask. We exec so Ctrl+C propagates cleanly. ---
exec "$PY" app.py

# ─── Optional: shell alias instead of double-click ────────────────────────
# Add this line to ~/.zshrc (or ~/.bashrc), then `pf2e` from any terminal:
#
#   alias pf2e='cd ~/GM_pf2e && ./start.command'
