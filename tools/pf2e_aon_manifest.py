"""Build a verified offline manifest from independent AoN capture receipts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.pf2e.rules.ingestion.evidence_snapshot import (
    build_snapshot_manifest,
)
from systems.pf2e.rules.manifest import canonical_json, reject_links
from systems.pf2e.rules.validation import RulesValidationError, require


MANIFEST_NAME = "snapshot-manifest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--snapshot-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    return parser


def _publish_exclusive(destination: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        reject_links(destination)
        try:
            if os.name == "nt":
                os.rename(temporary_path, destination)
            else:
                os.link(temporary_path, destination)
        except FileExistsError:
            raise RulesValidationError("output_exists", "$output") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def write_snapshot_manifest(snapshot_root: Path, *, snapshot_id: str) -> dict:
    root = Path(snapshot_root).absolute()
    reject_links(root)
    require(root.is_dir(), "invalid_package_layout", "$output")
    destination = root / MANIFEST_NAME
    reject_links(destination)
    require(not destination.exists(), "output_exists", "$output")
    manifest = build_snapshot_manifest(root, snapshot_id=snapshot_id)
    data = canonical_json(manifest)
    reject_links(destination)
    _publish_exclusive(destination, data)
    return {
        "categories": len(manifest["shards"]),
        "manifest": MANIFEST_NAME,
        "records": manifest["included_records"],
        "snapshot_id": manifest["snapshot_id"],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = write_snapshot_manifest(
            args.snapshot_root, snapshot_id=args.snapshot_id
        )
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except RulesValidationError as error:
        print(json.dumps({"error": error.code, "path": error.path}), file=sys.stderr)
    except OSError:
        print(json.dumps({"error": "io_error", "path": "$files"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
