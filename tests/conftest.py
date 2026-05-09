"""Test configuration: put the repo root on sys.path so `import app`
works regardless of where pytest is invoked from.

Importing app.py executes the top-level Flask wiring: routes register,
the SQLite compendium loads from `pf2e_database.db`, the party_data
JSONs are walked, etc. None of it starts the server. If a test environment
is missing the compendium DB or party_data dir, the import will surface
the failure clearly here rather than mid-test."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
