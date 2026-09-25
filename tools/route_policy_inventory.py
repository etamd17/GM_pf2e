#!/usr/bin/env python3
"""Print and validate the declarative Flask route-policy inventory.

The application currently performs substantial work at import time.  This tool
uses a disposable DATA_DIR and the same background-thread exemption as tests so
an inventory run cannot touch campaign data or leave worker threads behind.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.route_policy import (  # noqa: E402  (repo path is installed above)
    POLICY_NOTES,
    audit_url_map,
    inventory_rows,
)


def _load_url_map():
    """Import the real app safely and return its registered Werkzeug URL map."""

    with tempfile.TemporaryDirectory(prefix="gm-pf2e-route-inventory-") as data_dir:
        # Route registration is configuration-independent.  Isolating these
        # values prevents the import-time storage probe from touching real data.
        os.environ["DATA_DIR"] = data_dir
        os.environ["SECRET_KEY"] = "route-policy-inventory-not-for-runtime"
        os.environ["PYTEST_CURRENT_TEST"] = "route-policy-inventory"
        # Keep machine-readable JSON clean while preserving startup diagnostics
        # for a human running the tool directly.
        with contextlib.redirect_stdout(sys.stderr):
            app_module = importlib.import_module("app")
        return app_module.app.url_map


def render_text(url_map, *, check_only: bool = False) -> str:
    """Return the stable human-readable report used locally and in CI."""

    audit = audit_url_map(url_map)
    rows = inventory_rows(url_map)
    lines: list[str] = []
    if not check_only:
        columns = (
            ("POLICY", [row.policy for row in rows]),
            ("METHOD", [row.method for row in rows]),
            ("ENDPOINT", [row.endpoint for row in rows]),
            ("RULE", [row.rule for row in rows]),
        )
        widths = [max(len(header), *(len(value) for value in values)) for header, values in columns]
        lines.append("  ".join(header.ljust(width) for (header, _values), width in zip(columns, widths)))
        lines.append("  ".join("-" * width for width in widths))
        for row in rows:
            values = (row.policy, row.method, row.endpoint, row.rule)
            lines.append("  ".join(value.ljust(width) for value, width in zip(values, widths)).rstrip())
        for endpoint, note in sorted(POLICY_NOTES.items()):
            lines.append(f"NOTE {endpoint}: {note}")

    unique_endpoints = {row.endpoint for row in rows}
    lines.append(
        f"SUMMARY routes={len({(row.endpoint, row.rule) for row in rows})} "
        f"method_rows={len(rows)} endpoints={len(unique_endpoints)} "
        f"status={'ok' if audit.ok else 'error'}"
    )
    lines.append(audit.describe())
    return "\n".join(lines)


def render_json(url_map) -> str:
    """Return stable JSON suitable for diffing in automation."""

    audit = audit_url_map(url_map)
    payload = {
        "status": "ok" if audit.ok else "error",
        "unclassified_endpoints": list(audit.unclassified_endpoints),
        "stale_endpoints": list(audit.stale_endpoints),
        "stale_method_overrides": [
            {"endpoint": endpoint, "method": method}
            for endpoint, method in audit.stale_method_overrides
        ],
        "routes": [row.as_dict() for row in inventory_rows(url_map)],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (default: text)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print only the coverage summary; still exits non-zero on drift",
    )
    args = parser.parse_args(argv)

    url_map = _load_url_map()
    audit = audit_url_map(url_map)
    if args.format == "json":
        print(render_json(url_map))
    else:
        print(render_text(url_map, check_only=args.check))
    return 0 if audit.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
