#!/usr/bin/env python3
"""Import a campaign's legacy /map scenes into the VTT (M5).

    python tools/vtt_import_scenes.py --campaign CID            # dry run: report only
    python tools/vtt_import_scenes.py --campaign CID --apply    # write

Reads DATA_DIR (the legacy store, read only) and DATABASE_URL (the VTT store).
Run while no one is on the VTT, or use the GM page's import (which also wakes
live screens): this process cannot notify a running app's subscribers, so open
pages see the import on their next reload or change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import scenes as legacy  # noqa: E402
from core.persistence import Database  # noqa: E402
from vtt.importer.legacy_scenes import import_campaign  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", required=True)
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    scenes = [legacy.normalize_scene(s) for s in legacy.list_scenes(args.campaign)]
    db = Database.from_env("DATABASE_URL")
    try:
        report = import_campaign(db, args.campaign, scenes, legacy.table_scene_id(args.campaign), apply=args.apply)
    finally:
        db.dispose()
    print(json.dumps(report, indent=2))
    return 1 if any(s["status"] == "refused" for s in report["scenes"]) else 0


if __name__ == "__main__":
    sys.exit(main())
