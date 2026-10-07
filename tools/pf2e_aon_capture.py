"""Human-triggered, metadata-only Archives of Nethys evidence capture."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systems.pf2e.rules.ingestion.aon_capture import (
    CATEGORY_SPECS,
    CATEGORY_SPECS_V1,
    CENSUS_SOURCE_FIELDS,
    LEDGER_SOURCE_FIELDS,
    QUERY_CONTRACT_VERSION,
    REVIEWED_CATEGORIES,
    SEARCH_ENDPOINT,
    _bounded_response,
    build_category_query,
    build_category_query_v1,
    build_scope_policy,
    build_scope_query,
    build_scope_query_v1,
    compile_census_shard,
    compile_ledger_shard,
    compile_scope_observation,
)
from systems.pf2e.rules.ingestion.evidence_snapshot import (
    normalize_capture_receipt,
    normalize_scope_policy,
    normalize_scope_receipt,
)
from systems.pf2e.rules.manifest import (
    MAX_FILE_BYTES,
    canonical_json,
    digest,
    read_file,
    read_json,
    reject_links,
)
from systems.pf2e.rules.validation import (
    RulesValidationError,
    iso_date,
    require,
    text,
    timestamp,
)


MAX_RESPONSE_BYTES = MAX_FILE_BYTES
REQUEST_TIMEOUT_SECONDS = 30
MIN_REQUEST_INTERVAL_SECONDS = 0.5
MAX_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = frozenset({429, 502, 503, 504})
USER_AGENT = (
    "GM-pf2e-evidence-capture/1.0 "
    "(+https://github.com/etamd17/GM_pf2e; human-triggered metadata audit)"
)
REQUEST_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
    "Content-Type": "application/json",
    "User-Agent": USER_AGENT,
}


def canonical_query_bytes(query: dict) -> bytes:
    """Return the exact canonical request bytes hashed by snapshot receipts."""
    require(type(query) is dict, "invalid_type", "$.query")
    return canonical_json(query)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(url: str, payload: bytes, headers: dict, timeout: int,
                    max_bytes: int) -> bytes:
    request = Request(url, data=payload, headers=headers, method="POST")
    opener = build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        require(response.geturl() == SEARCH_ENDPOINT,
                "redirect_refused", "$network")
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                declared_size = int(declared)
            except ValueError:
                raise RulesValidationError(
                    "invalid_response", "$network.content_length"
                ) from None
            require(0 <= declared_size <= max_bytes,
                    "limit_exceeded", "$network.response")
        data = response.read(max_bytes + 1)
    require(len(data) <= max_bytes, "limit_exceeded", "$network.response")
    return data


def _decode_response(data: bytes) -> dict:
    require(type(data) is bytes, "invalid_type", "$network.response")
    require(len(data) <= MAX_RESPONSE_BYTES,
            "limit_exceeded", "$network.response")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise RulesValidationError("invalid_json", "$network.response")
            value[key] = item
        return value

    def forbidden(_value):
        raise RulesValidationError("invalid_json", "$network.response")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise RulesValidationError("invalid_json", "$network.response")
        return number

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=forbidden,
            parse_float=finite_float,
        )
    except RulesValidationError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise RulesValidationError("invalid_json", "$network.response") from None
    try:
        _bounded_response(value)
    except RulesValidationError as error:
        raise RulesValidationError(error.code, "$network.response") from None
    require(type(value) is dict, "invalid_type", "$network.response")
    return value


class SearchClient:
    """Fixed-endpoint, paced client with bounded and narrowly retried reads."""

    def __init__(self, *, transport=None, sleep=time.sleep,
                 monotonic=time.monotonic):
        self._transport = transport or _http_transport
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at = None

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = MIN_REQUEST_INTERVAL_SECONDS - (now - self._last_request_at)
            if remaining > 0:
                self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at = now

    def search(self, query: dict) -> tuple[dict, bytes]:
        payload = canonical_query_bytes(query)
        for attempt in range(MAX_ATTEMPTS):
            self._pace()
            try:
                data = self._transport(
                    SEARCH_ENDPOINT,
                    payload,
                    dict(REQUEST_HEADERS),
                    REQUEST_TIMEOUT_SECONDS,
                    MAX_RESPONSE_BYTES,
                )
            except HTTPError as error:
                if error.code in RETRYABLE_HTTP_STATUSES and attempt + 1 < MAX_ATTEMPTS:
                    continue
                raise RulesValidationError("http_error", "$network") from None
            except (URLError, OSError):
                raise RulesValidationError("network_error", "$network") from None
            require(type(data) is bytes, "invalid_type", "$network.response")
            require(len(data) <= MAX_RESPONSE_BYTES,
                    "limit_exceeded", "$network.response")
            return _decode_response(data), data
        raise RulesValidationError("http_error", "$network")


def _artifact_receipt(mode: str, *, policy: dict, run_id: str,
                      captured_at: str, artifacts: list[dict]) -> dict:
    query_projection = [
        {"category": item["category"], "sha256": item["query_sha256"]}
        for item in artifacts
    ]
    result_projection = [
        {"category": item["category"], "sha256": item["response_sha256"]}
        for item in artifacts
    ]
    return normalize_capture_receipt({
        "schema_version": 1,
        "mode": mode,
        "run_id": run_id,
        "captured_at": captured_at,
        "scope_id": policy["scope_id"],
        "authority": policy["authority"],
        "site_update_date": policy["site_update_date"],
        "search_endpoint": SEARCH_ENDPOINT,
        "resolved_index": policy["resolved_index"],
        "query_set_sha256": digest(canonical_json(query_projection)),
        "result_set_sha256": digest(canonical_json(result_projection)),
        "artifacts": artifacts,
    })


def _target(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    require(
        bool(parts) and not PurePosixPath(relative).is_absolute()
        and all(part not in {"", ".", ".."} for part in parts),
        "invalid_package_layout",
        "$output",
    )
    return root.joinpath(*parts)


def _preflight_outputs(root: Path, relatives: list[str]) -> Path:
    root = Path(root).absolute()
    reject_links(root)
    _require_directory_ancestors(root)
    seen = set()
    for relative in relatives:
        normalized = relative.casefold()
        require(normalized not in seen, "duplicate_id", "$output")
        seen.add(normalized)
        destination = _target(root, relative)
        _require_directory_ancestors(destination.parent)
        reject_links(destination)
        require(not destination.exists(), "output_exists", "$output/" + relative)
    return root


def _require_directory_ancestors(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        require(stat.S_ISDIR(info.st_mode), "invalid_package_layout", "$output")


def _write_outputs(root: Path, documents: dict[str, bytes], receipt_path: str) -> None:
    """Write the authoritative receipt last; existing outputs are never replaced."""
    root = _preflight_outputs(root, list(documents))
    root.mkdir(parents=True, exist_ok=True)
    ordered = sorted(path for path in documents if path != receipt_path)
    ordered.append(receipt_path)
    for relative in ordered:
        destination = _target(root, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        reject_links(destination.parent)
        try:
            with destination.open("xb") as handle:
                handle.write(documents[relative])
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise RulesValidationError(
                "output_exists", "$output/" + relative
            ) from None


def _load_policy(path: Path) -> dict:
    return normalize_scope_policy(read_json(read_file(Path(path))))


def capture_scope(*, client: SearchClient, scope_id: str,
                  site_update_date: str, expected_index: str, run_id: str,
                  captured_at: str, output_root: Path) -> dict:
    text(scope_id, "$.scope_id", pattern=r"pf2e-[a-z0-9]+(?:-[a-z0-9]+)*")
    iso_date(site_update_date, "$.site_update_date")
    text(expected_index, "$.expected_index", pattern=r"aon-[0-9]{8}-[0-9]{6}")
    text(run_id, "$.run_id", pattern=r"[a-z0-9]+(?:-[a-z0-9]+)*")
    timestamp(captured_at, "$.captured_at")
    root = _preflight_outputs(
        output_root, ["scope-policy.json", "scope-receipt.json"]
    )
    query = build_scope_query_v1()
    response, response_bytes = client.search(query)
    observation = compile_scope_observation(response)
    require(
        observation["resolved_index"] == expected_index,
        "capture_mismatch",
        "$.resolved_index",
    )
    policy = build_scope_policy(
        observation, scope_id=scope_id, site_update_date=site_update_date
    )
    receipt = normalize_scope_receipt({
        "schema_version": 1,
        "run_id": run_id,
        "captured_at": captured_at,
        "scope_id": scope_id,
        "authority": policy["authority"],
        "site_update_date": site_update_date,
        "search_endpoint": SEARCH_ENDPOINT,
        "resolved_index": policy["resolved_index"],
        "query_sha256": digest(canonical_query_bytes(query)),
        "response_sha256": digest(response_bytes),
        "reported_records": observation["reported_records"],
        "returned_records": observation["returned_records"],
        "categories": policy["categories"],
    })
    documents = {
        "scope-policy.json": canonical_json(policy),
        "scope-receipt.json": canonical_json(receipt),
    }
    _write_outputs(root, documents, "scope-receipt.json")
    return {
        "mode": "scope",
        "resolved_index": policy["resolved_index"],
        "records": policy["total_records"],
        "categories": len(policy["categories"]),
    }


def capture_categories(*, client: SearchClient, mode: str, policy_path: Path,
                       run_id: str, captured_at: str, output_root: Path,
                       census_captured_at: str | None = None,
                       snapshot_id: str | None = None) -> dict:
    require(mode in {"census", "ledger"}, "invalid_value", "$.mode")
    policy = _load_policy(policy_path)
    text(run_id, "$.run_id", pattern=r"[a-z0-9]+(?:-[a-z0-9]+)*")
    timestamp(captured_at, "$.captured_at")
    included = [
        item for item in policy["categories"] if item["included_records"] > 0
    ]
    require(
        {item["name"] for item in included} <= CATEGORY_SPECS_V1.keys(),
        "scope_mismatch",
        "$.categories",
    )
    if mode == "ledger":
        require(census_captured_at is not None,
                "missing_field", "$.census_captured_at")
        timestamp(census_captured_at, "$.census_captured_at")
        require(snapshot_id is not None, "missing_field", "$.snapshot_id")
        text(snapshot_id, "$.snapshot_id",
             pattern=r"pf2e-aon-[a-z0-9]+(?:-[a-z0-9]+)*")
    subdirectory = "census" if mode == "census" else "ledger"
    receipt_path = mode + "-receipt.json"
    relative_paths = [
        f"{subdirectory}/{item['name']}.json" for item in included
    ] + [receipt_path]
    root = _preflight_outputs(output_root, relative_paths)
    documents = {}
    artifacts = []
    for item in included:
        category = item["name"]
        query = build_category_query_v1(category, mode)
        response, response_bytes = client.search(query)
        if mode == "census":
            document = compile_census_shard(
                response,
                category=category,
                kind=CATEGORY_SPECS[category][0],
                expected_index=policy["resolved_index"],
                captured_at=captured_at,
                site_update_date=policy["site_update_date"],
            )
            returned = len(document["records"])
        else:
            document = compile_ledger_shard(
                response,
                category=category,
                expected_index=policy["resolved_index"],
                inventory_id=f"{snapshot_id}.{category}",
                census_captured_at=census_captured_at,
                created_at=captured_at,
            )
            returned = len(document["entries"])
        reported = response["hits"]["total"]["value"]
        require(reported == item["included_records"],
                "scope_mismatch", "$.categories." + category)
        require(returned == item["included_records"],
                "count_mismatch", "$.categories." + category)
        data = canonical_json(document)
        relative = f"{subdirectory}/{category}.json"
        documents[relative] = data
        artifacts.append({
            "category": category,
            "path": relative,
            "sha256": digest(data),
            "reported_records": reported,
            "returned_records": returned,
            "query_sha256": digest(canonical_query_bytes(query)),
            "response_sha256": digest(response_bytes),
        })
    artifacts.sort(key=lambda item: item["category"])
    receipt = _artifact_receipt(
        mode,
        policy=policy,
        run_id=run_id,
        captured_at=captured_at,
        artifacts=artifacts,
    )
    documents[receipt_path] = canonical_json(receipt)
    _write_outputs(root, documents, receipt_path)
    return {
        "mode": mode,
        "resolved_index": policy["resolved_index"],
        "records": sum(item["returned_records"] for item in artifacts),
        "shards": len(artifacts),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="mode", required=True)
    scope = commands.add_parser(
        "scope", help="Capture exhaustive category facets", allow_abbrev=False
    )
    scope.add_argument("--scope-id", required=True)
    scope.add_argument("--site-update-date", required=True)
    scope.add_argument("--expected-index", required=True)
    scope.add_argument("--run-id", required=True)
    scope.add_argument("--captured-at", required=True)
    scope.add_argument("--output-root", type=Path, required=True)
    for mode in ("census", "ledger"):
        command = commands.add_parser(
            mode, help=f"Capture {mode} shards", allow_abbrev=False
        )
        command.add_argument("--scope-policy", type=Path, required=True)
        command.add_argument("--run-id", required=True)
        command.add_argument("--captured-at", required=True)
        command.add_argument("--output-root", type=Path, required=True)
        if mode == "ledger":
            command.add_argument("--census-captured-at", required=True)
            command.add_argument("--snapshot-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = SearchClient()
        if args.mode == "scope":
            result = capture_scope(
                client=client,
                scope_id=args.scope_id,
                site_update_date=args.site_update_date,
                expected_index=args.expected_index,
                run_id=args.run_id,
                captured_at=args.captured_at,
                output_root=args.output_root,
            )
        else:
            result = capture_categories(
                client=client,
                mode=args.mode,
                policy_path=args.scope_policy,
                run_id=args.run_id,
                captured_at=args.captured_at,
                output_root=args.output_root,
                census_captured_at=getattr(args, "census_captured_at", None),
                snapshot_id=getattr(args, "snapshot_id", None),
            )
        print(json.dumps(result, sort_keys=True, ensure_ascii=True))
        return 0
    except RulesValidationError as error:
        print(json.dumps({"error": error.code, "path": error.path}), file=sys.stderr)
    except OSError:
        print(json.dumps({"error": "io_error", "path": "$files"}), file=sys.stderr)
    return 2


__all__ = [
    "CATEGORY_SPECS",
    "CATEGORY_SPECS_V1",
    "CENSUS_SOURCE_FIELDS",
    "LEDGER_SOURCE_FIELDS",
    "REVIEWED_CATEGORIES",
    "SEARCH_ENDPOINT",
    "MAX_RESPONSE_BYTES",
    "MIN_REQUEST_INTERVAL_SECONDS",
    "REQUEST_TIMEOUT_SECONDS",
    "QUERY_CONTRACT_VERSION",
    "SearchClient",
    "USER_AGENT",
    "build_category_query",
    "build_category_query_v1",
    "build_scope_policy",
    "canonical_query_bytes",
    "capture_categories",
    "capture_scope",
    "build_parser",
    "main",
    "build_scope_query",
    "build_scope_query_v1",
    "compile_census_shard",
    "compile_ledger_shard",
    "compile_scope_observation",
]


if __name__ == "__main__":
    raise SystemExit(main())
