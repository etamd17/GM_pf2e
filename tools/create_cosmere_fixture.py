"""Regenerate the small invented Cosmere rules fixture used in CI."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from systems.cosmere.rules.compiler import compile_registry  # noqa: E402


SOURCE = "fixture.original-rules"


def record(id, kind, name, *, worlds, eras=(), parameters=None):
    return {"id": id, "version": "1.1.0", "kind": kind, "name": name,
            "summary": f"Invented {name.lower()} for automated tests only.",
            "citations": [{"artifact_id": SOURCE, "page_start": 1, "page_end": 1}],
            "worlds": list(worlds), "eras": list(eras), "spoiler_scope": "player",
            "automation": "tracked", "audit_status": "provisional",
            "parameters": parameters or {}}


def pack(id, version, kind, dependencies, worlds, eras=(), records=()):
    return {"id": id, "version": version, "kind": kind,
            "dependencies": list(dependencies), "sources": [SOURCE],
            "worlds": list(worlds), "eras": list(eras),
            "spoiler_scope": "player", "audit_status": "provisional",
            "records": list(records)}


def main():
    authoring = {"schema_version": 1, "artifacts": [{
        "product_id": SOURCE, "basename": "invented-test-source.pdf",
        "edition": "fictional", "sha256": "1" * 64, "byte_count": 1,
        "page_count": 1, "authority_tier": "handbook",
        "extraction_class": "rules", "spoiler_scope": "player",
        "rights_state": "private"}], "packs": [
        pack("cosmere.core", "1.0.0", "core", (), ("cosmere",)),
        pack("cosmere.stormlight.legacy", "1.0.0", "compatibility",
             ("cosmere.core",), ("roshar",)),
        pack("cosmere.mistborn.core", "1.1.0", "world", ("cosmere.core",),
             ("scadrial",), records=(
                 record("fixture.mistborn.training-resource", "resource", "Training resource",
                        worlds=("scadrial",), parameters={"max": 3}),
                 record("fixture.mistborn.practice-action", "action", "Practice action",
                        worlds=("scadrial",), parameters={"cost": 1}),
             )),
        pack("cosmere.mistborn.era-1", "1.1.0", "world",
             ("cosmere.core", "cosmere.mistborn.core"), ("scadrial",), ("era-1",),
             records=(record("fixture.mistborn.era-1.path", "path", "Invented first era path",
                             worlds=("scadrial",), eras=("era-1",)),)),
        pack("cosmere.mistborn.era-2", "1.1.0", "world",
             ("cosmere.core", "cosmere.mistborn.core"), ("scadrial",), ("era-2",),
             records=(record("fixture.mistborn.era-2.path", "path", "Invented second era path",
                             worlds=("scadrial",), eras=("era-2",)),)),
    ]}
    target = ROOT / "tests" / "fixtures" / "cosmere_rules" / "rules"
    target.mkdir(parents=True, exist_ok=True)
    (target / "authoring.json").write_text(
        json.dumps(authoring, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (target / "registry.json").write_bytes(compile_registry(authoring))


if __name__ == "__main__":
    main()
