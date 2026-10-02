"""Offline PF2e package compilation, verification and review; no app import."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.pf2e.rules.ingestion.compile_pack import write_package
from systems.pf2e.rules.ingestion.diff_pack import diff_packages
from systems.pf2e.rules.manifest import read_file, read_json
from systems.pf2e.rules.registry import load_package
from systems.pf2e.rules.validation import RulesValidationError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_cmd = commands.add_parser("compile", help="Compile authoring JSON into a new immutable ID")
    compile_cmd.add_argument("source", type=Path)
    compile_cmd.add_argument("--store", type=Path, required=True)
    validate_cmd = commands.add_parser("validate", help="Verify a package and optional trusted binding")
    validate_cmd.add_argument("package", type=Path)
    validate_cmd.add_argument("--expected-hash")
    diff_cmd = commands.add_parser("diff", help="Compare two verified packages")
    diff_cmd.add_argument("before", type=Path)
    diff_cmd.add_argument("after", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "diff":
            result = diff_packages(load_package(args.before), load_package(args.after))
        else:
            if args.command == "compile":
                path = write_package(read_json(read_file(args.source)), args.store)
                package = load_package(path)
            else:
                path = args.package
                package = load_package(path, expected_hash=args.expected_hash)
            result = {"path": str(path), "ruleset_id": package.manifest["ruleset_id"],
                      "package_hash": package.manifest["package_hash"],
                      "status": package.manifest["publication_status"],
                      "enabled_records": sum(r.state == "enabled" for r in package.records.values()),
                      "quarantined_records": sum(r.state == "quarantined" for r in package.records.values())}
        # ASCII-safe JSON keeps Windows console encodings from corrupting diagnostics.
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except RulesValidationError as error:
        print(json.dumps({"error": error.code, "path": error.path}), file=sys.stderr)
    except OSError:
        print(json.dumps({"error": "io_error", "path": "$files"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
