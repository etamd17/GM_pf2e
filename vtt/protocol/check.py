"""Run the shared corpus: python -m vtt.protocol.check [--root PATH] [--json]."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import ContractRegistry, ProtocolValidationError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    registry = ContractRegistry(args.root)
    # The trusted corpus intentionally contains invalid numeric examples. Apply the
    # strict wire boundary to each example, not to its containing test manifest.
    cases = json.loads((registry.root / "fixtures" / "cases.json").read_text(encoding="utf-8"))
    results = []
    for case in cases:
        try:
            if "raw" in case:
                registry.parse_and_validate(case["schema"], case["raw"])
            else:
                registry.validate(case["schema"], case["message"])
            valid = True
        except ProtocolValidationError:
            valid = False
        results.append({"id": case["id"], "valid": valid, "matches": valid == case["valid"]})
    report = {"sourceDigest": registry.source_digest(), "results": results}
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"{sum(r['matches'] for r in results)}/{len(results)} canonical fixture cases passed")
        for result in results:
            if not result["matches"]:
                print(f"FAIL {result['id']}")
        print(f"source digest: {report['sourceDigest']}")
    return 0 if all(result["matches"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
