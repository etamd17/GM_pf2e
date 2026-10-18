"""Verify private PDF metadata or compile normalized Cosmere authoring JSON.

Usage:
  python tools/cosmere_rules.py compile --authoring AUTHORING.json --output REGISTRY.json
  python tools/cosmere_rules.py inventory --mapping PRIVATE_MAPPING.json --output PRIVATE_INVENTORY.json

The mapping is an explicit product-ID-to-local-path JSON object. PDF contents and
runtime paths never enter either output. Private inventory must be outside this
repository. This command does not extract PDF prose or infer edition from text.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from systems.cosmere.rules.compiler import MAX_JSON_BYTES, canonical_json, compile_registry, read_json
from systems.cosmere.rules.registry import load_registry
from systems.cosmere.rules.schema import RulesValidationError, identifier, require, to_dict
from systems.cosmere.rules.seeds import SEED_DIRECTORY


def write_atomic(path: Path, raw: bytes):
    path = Path(path)
    require(path.suffix.lower() == ".json", "output must be a JSON file")
    require(path.parent.is_dir() and not path.is_symlink(), "invalid output location")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".cosmere-rules-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def inventory_sources(mapping, artifacts, output, *, repo_root=REPO_ROOT):
    """Verify mapped PDF binaries and write metadata only, with no source paths."""
    output = Path(output)
    require(not output.resolve().is_relative_to(Path(repo_root).resolve()), "private inventory must be outside repository")
    require(isinstance(mapping, Mapping) and bool(mapping), "explicit artifact path mapping is required")
    entries = []
    for product_id, local_path in sorted(mapping.items()):
        identifier(product_id)
        require(product_id in artifacts, "unknown mapped artifact ID")
        require(isinstance(local_path, (str, Path)), "invalid runtime artifact path")
        artifact = artifacts[product_id]
        path = Path(local_path)
        require(path.is_file(), "missing source artifact")
        require(path.name == artifact.basename, "source basename mismatch")
        require(path.resolve() != output.resolve(), "inventory cannot overwrite source")
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            require(before.st_size == artifact.byte_count, "source byte count mismatch")
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            require(digest == artifact.sha256, "source SHA-256 mismatch")
            handle.seek(0)
            try:
                from pypdf import PdfReader
                reader = PdfReader(handle, strict=True)
                require(not reader.is_encrypted, "encrypted source artifact")
                require(len(reader.pages) == artifact.page_count, "source page count mismatch")
                edition_marker = (reader.metadata or {}).get("/Edition")
                if edition_marker and artifact.edition != "unverified":
                    require(edition_marker == artifact.edition, "source edition mismatch")
            except RulesValidationError:
                raise
            except Exception:
                raise RulesValidationError("invalid source PDF") from None
            after = os.fstat(handle.fileno())
            require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "source changed during verification")
        entry = to_dict(artifact)
        entry.update(verification="verified", extraction_status="not_extracted",
                     edition_verification="metadata" if edition_marker else "manifest_observation",
                     source_policy="comparison_only" if artifact.rights_state == "quarantined" else "private_authoring")
        entries.append(entry)
    result = {"schema_version": 1, "private": True, "artifacts": entries}
    write_atomic(output, canonical_json(result))
    return result


def read_runtime_mapping(path: Path):
    """Paths are allowed only in this ephemeral input; never in compiled JSON."""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate mapping artifact ID")
            result[key] = value
        return result

    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    require(len(raw) <= MAX_JSON_BYTES, "mapping size limit exceeded")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise RulesValidationError("invalid runtime mapping JSON") from None
    require(isinstance(result, dict), "mapping must be an artifact-ID-to-path object")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    compile_command = commands.add_parser("compile", help="compile normalized authoring JSON only")
    compile_command.add_argument("--authoring", type=Path, required=True)
    compile_command.add_argument("--output", type=Path, required=True)
    inventory_command = commands.add_parser("inventory", help="verify private PDF metadata; never extract text")
    inventory_command.add_argument("--mapping", type=Path, required=True)
    inventory_command.add_argument("--registry", type=Path, default=SEED_DIRECTORY / "registry.json")
    inventory_command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            require(args.authoring.resolve() != args.output.resolve(), "compiled output cannot overwrite authoring")
            with args.authoring.open("rb") as handle:
                data = read_json(handle.read(MAX_JSON_BYTES + 1))
            raw = compile_registry(data)
            write_atomic(args.output, raw)
            print("Compiled Cosmere registry.")
        else:
            require(args.output.resolve() not in {args.mapping.resolve(), args.registry.resolve()},
                    "inventory cannot overwrite its input documents")
            registry = load_registry(args.registry)
            inventory_sources(read_runtime_mapping(args.mapping), registry.artifacts, args.output)
            print("Verified private source inventory; no text extracted.")
        return 0
    except RulesValidationError as error:
        print(f"Cosmere rules validation failed: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("Cosmere rules I/O failed; verify input and output locations.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
