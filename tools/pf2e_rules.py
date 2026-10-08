"""Offline PF2e package compilation, verification and review; no app import."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.pf2e.rules.ingestion.compile_pack import write_package
from systems.pf2e.rules.ingestion.class_review_overlay import (
    verify_class_review_overlay,
    write_class_review_overlay,
)
from systems.pf2e.rules.ingestion.diff_pack import diff_packages
from systems.pf2e.rules.ingestion.evidence_inventory import (
    audit_evidence_inventory,
    diff_evidence_inventories,
    scan_corpus,
)
from systems.pf2e.rules.ingestion.evidence_snapshot import verify_evidence_snapshot
from systems.pf2e.rules.manifest import MAX_FILE_BYTES, read_file, read_json
from systems.pf2e.rules.registry import load_package
from systems.pf2e.rules.validation import RulesValidationError, bounded_json, require


def _render_result(result: dict, *, require_readback: bool = False) -> str:
    """Serialize bounded output, optionally enforcing strict-parser readback."""
    if require_readback:
        bounded_json(result)
    try:
        payload = json.dumps(result, sort_keys=True, ensure_ascii=True)
        size = len((payload + "\n").encode("ascii"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise RulesValidationError("invalid_json", "$") from None
    require(size <= MAX_FILE_BYTES, "limit_exceeded", "$")
    return payload


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
    scan_cmd = commands.add_parser(
        "evidence-scan", help="Inventory a local PF2e JSON corpus offline"
    )
    scan_cmd.add_argument("root", type=Path)
    scan_cmd.add_argument(
        "--summary", action="store_true", help="Print coverage totals without record rows"
    )
    audit_cmd = commands.add_parser(
        "evidence-audit", help="Compare an AoN census with a reviewed ledger"
    )
    audit_cmd.add_argument("census", type=Path)
    audit_cmd.add_argument("ledger", type=Path)
    evidence_diff_cmd = commands.add_parser(
        "evidence-diff", help="Compare two complete audited evidence reports"
    )
    evidence_diff_cmd.add_argument("before", type=Path)
    evidence_diff_cmd.add_argument("after", type=Path)
    snapshot_verify_cmd = commands.add_parser(
        "evidence-snapshot-verify",
        help="Verify a complete sharded evidence snapshot offline",
    )
    snapshot_verify_cmd.add_argument("manifest", type=Path)
    class_review_compile_cmd = commands.add_parser(
        "class-review-compile",
        help="Compile a frozen class-review overlay into a new immutable ID",
    )
    class_review_compile_cmd.add_argument("authoring", type=Path)
    class_review_compile_cmd.add_argument("--snapshot", type=Path, required=True)
    class_review_compile_cmd.add_argument("--corpus", type=Path, required=True)
    class_review_compile_cmd.add_argument("--store", type=Path, required=True)
    class_review_verify_cmd = commands.add_parser(
        "class-review-verify",
        help="Verify a frozen class-review overlay and optional trusted binding",
    )
    class_review_verify_cmd.add_argument("overlay", type=Path)
    class_review_verify_cmd.add_argument("--snapshot", type=Path, required=True)
    class_review_verify_cmd.add_argument("--corpus", type=Path, required=True)
    class_review_verify_cmd.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    try:
        exit_code = 0
        if args.command == "evidence-scan":
            result = scan_corpus(args.root)
            if args.summary:
                result = {
                    "schema_version": result["schema_version"],
                    "coverage": result["coverage"],
                }
        elif args.command == "evidence-audit":
            result = audit_evidence_inventory(
                read_json(read_file(args.census)),
                read_json(read_file(args.ledger)),
            )
            exit_code = 0 if result["complete"] else 1
        elif args.command == "evidence-diff":
            result = diff_evidence_inventories(
                read_json(read_file(args.before)),
                read_json(read_file(args.after)),
            )
            drift_fields = (
                "added", "removed", "evidence_changed", "newly_excluded",
                "restored", "exclusion_changed", "disposition_changed",
                "review_changed",
            )
            exit_code = 1 if any(result[field] for field in drift_fields) else 0
        elif args.command == "evidence-snapshot-verify":
            result = verify_evidence_snapshot(args.manifest)
        elif args.command == "class-review-compile":
            path = write_class_review_overlay(
                read_json(read_file(args.authoring)),
                args.snapshot,
                args.corpus,
                args.store,
            )
            result = {
                "path": str(path),
                **verify_class_review_overlay(
                    path,
                    args.snapshot,
                    args.corpus,
                ),
            }
        elif args.command == "class-review-verify":
            path = args.overlay.absolute()
            result = {
                "path": str(path),
                **verify_class_review_overlay(
                    path,
                    args.snapshot,
                    args.corpus,
                    expected_hash=args.expected_hash,
                ),
            }
        elif args.command == "diff":
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
        print(_render_result(
            result,
            require_readback=args.command in {
                "class-review-compile", "class-review-verify",
                "evidence-audit", "evidence-diff", "evidence-snapshot-verify",
            },
        ))
        return exit_code
    except RulesValidationError as error:
        print(json.dumps({"error": error.code, "path": error.path}), file=sys.stderr)
    except OSError:
        print(json.dumps({"error": "io_error", "path": "$files"}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
