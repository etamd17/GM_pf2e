"""Neutral, package-bound Cosmere random tables and persisted roll state."""
from copy import deepcopy

import pytest

from systems.cosmere.rules.schema import RuleRecord, SourceRef


PACK_A = {
    "id": "cosmere.test.tables", "version": "1.0.0",
    "package_hash": "a" * 64, "dependencies": ["cosmere.core"],
}
PACK_B = {
    "id": "cosmere.test.addon", "version": "1.0.0",
    "package_hash": "b" * 64, "dependencies": ["cosmere.test.tables"],
}


def table(table_id, results, *, package=PACK_A, worlds=("roshar",), eras=(),
          spoiler="player"):
    return {
        "id": table_id, "version": "1.0.0", "kind": "table",
        "name": table_id.rsplit(".", 1)[-1].title(),
        "summary": "Independent test table summary.",
        "citations": [{"artifact_id": "stormlight.handbook",
                       "page_start": 10, "page_end": 10}],
        "worlds": list(worlds), "eras": list(eras),
        "spoiler_scope": spoiler, "automation": "calculated",
        "audit_status": "provisional", "parameters": {"results": results},
        "package": deepcopy(package),
    }


def result(result_id, value=None, *, weight=1, table_id=None):
    row = {"id": result_id, "weight": weight}
    if table_id is not None:
        row["table_id"] = table_id
    else:
        row["value"] = value
    return row


def catalog(*records, packages=(PACK_A, PACK_B)):
    return {
        "packages": [deepcopy(row) for row in packages],
        "catalogs": {"table": [deepcopy(row) for row in records]},
    }


def test_rule_schema_accepts_the_neutral_table_kind():
    record = RuleRecord(
        id="test.weather", version="1.0.0", kind="table", name="Weather",
        summary="A normalized table.",
        citations=(SourceRef("stormlight.handbook", 10, 10),),
        worlds=("roshar",), eras=(), spoiler_scope="player",
        automation="calculated", audit_status="provisional",
        parameters={"results": [{"id": "clear", "weight": 1,
                                  "value": {"text": "Clear"}}]},
    )
    assert record.kind == "table"


def test_seeded_weighted_and_nested_rolls_are_stable_and_structured():
    from systems.cosmere.tables import TableRegistry

    leaf = table("test.leaf", [
        result("common", {"text": "Common"}, weight=8),
        result("rare", {"text": "Rare", "tags": ["notable"]}, weight=2),
    ])
    root = table("test.root", [result("delegate", table_id="test.leaf")])
    registry = TableRegistry(catalog(root, leaf), world="roshar",
                             spoiler_scope="gm")

    first = registry.roll("test.root", seed="session-17")
    second = registry.roll("test.root", seed="session-17")

    assert first == second
    assert first["seed"] == "session-17"
    assert first["resolution_path"][0] == {
        "table_id": "test.root", "result_id": "delegate",
        "package_id": PACK_A["id"], "package_hash": PACK_A["package_hash"],
    }
    assert first["resolution_path"][-1]["table_id"] == "test.leaf"
    assert first["result"]["id"] in {"common", "rare"}
    assert first["result"]["value"]["text"] in {"Common", "Rare"}
    assert first["citations"] == [{"artifact_id": "stormlight.handbook",
                                    "page_start": 10, "page_end": 10}]
    assert first["package_hashes"] == [PACK_A["package_hash"]]
    assert "html" not in first["result"]

    # Weighted selection is deterministic but not degenerate.
    observed = {registry.roll("test.leaf", seed=f"seed-{index}")["result"]["id"]
                for index in range(100)}
    assert observed == {"common", "rare"}


@pytest.mark.parametrize("records,message", [
    ([table("test.invalid", [result("bad", {"text": "x"}, weight=0)])],
     "positive integer"),
    ([table("test.invalid", [result("same", {"text": "x"}),
                              result("same", {"text": "y"})])],
     "duplicate result"),
    ([table("test.invalid", [result("missing", table_id="test.absent")])],
     "missing nested table"),
    ([table("test.one", [result("two", table_id="test.two")]),
      table("test.two", [result("one", table_id="test.one")])],
     "cycle"),
])
def test_table_registry_rejects_invalid_records(records, message):
    from systems.cosmere.tables import TableRegistry, TableValidationError

    with pytest.raises(TableValidationError, match=message):
        TableRegistry(catalog(*records), world="roshar", spoiler_scope="gm")


def test_nested_references_respect_package_dependencies_and_depth_limit():
    from systems.cosmere.tables import TableRegistry, TableValidationError

    base = table("test.base", [result("done", {"text": "Done"})], package=PACK_A)
    allowed = table("test.addon", [result("base", table_id="test.base")],
                    package=PACK_B)
    TableRegistry(catalog(base, allowed), world="roshar", spoiler_scope="gm")

    forbidden = table("test.base-ref", [result("addon", table_id="test.addon-leaf")],
                      package=PACK_A)
    addon_leaf = table("test.addon-leaf", [result("done", {"text": "Done"})],
                       package=PACK_B)
    with pytest.raises(TableValidationError, match="dependency"):
        TableRegistry(catalog(forbidden, addon_leaf), world="roshar",
                      spoiler_scope="gm")

    chain = []
    for index in range(18):
        target = f"test.depth-{index + 1}" if index < 17 else None
        chain.append(table(
            f"test.depth-{index}",
            [result("next", table_id=target)] if target else
            [result("done", {"text": "Done"})],
        ))
    with pytest.raises(TableValidationError, match="depth"):
        TableRegistry(catalog(*chain), world="roshar", spoiler_scope="gm")

    # Validation must not depend on lexical table order.  A leaf-first walk
    # used to mark this reverse-named chain visited and skip the root depth.
    reverse_chain = []
    names = [f"test.{letter}" for letter in "zyxwvutsrqponmlkji"]
    for index, table_id in enumerate(names):
        target = names[index + 1] if index + 1 < len(names) else None
        reverse_chain.append(table(
            table_id,
            [result("next", table_id=target)] if target else
            [result("done", {"text": "Done"})],
        ))
    with pytest.raises(TableValidationError, match="depth"):
        TableRegistry(catalog(*reverse_chain), world="roshar",
                      spoiler_scope="gm")


def test_table_scope_filters_world_era_pack_and_spoilers_fail_closed():
    from systems.cosmere.tables import TableNotFoundError, TableRegistry

    records = [
        table("test.roshar", [result("ok", {"text": "Roshar"})]),
        table("test.era-one", [result("ok", {"text": "Era 1"})],
              worlds=("scadrial",), eras=("era-1",)),
        table("test.gm", [result("ok", {"text": "Secret"})], spoiler="gm"),
        table("test.disabled-pack", [result("ok", {"text": "Disabled"})],
              package=PACK_B),
    ]
    registry = TableRegistry(
        catalog(*records), world="roshar", spoiler_scope="player",
        allowed_pack_ids={PACK_A["id"]})
    assert registry.roll("test.roshar", seed="ok")["result"]["id"] == "ok"
    for hidden in ("test.era-one", "test.gm", "test.disabled-pack"):
        with pytest.raises(TableNotFoundError):
            registry.roll(hidden, seed="hidden")

    scadrial = TableRegistry(catalog(*records), world="scadrial", era="era-1",
                             spoiler_scope="gm")
    assert scadrial.roll("test.era-one", seed="ok")["result"]["id"] == "ok"


def test_table_state_tracks_bounded_history_pins_stale_writes_and_replays():
    from systems.cosmere.tables import (
        TableStateError,
        TableRegistry,
        pin_roll,
        roll_and_record,
        unpin_roll,
    )

    registry = TableRegistry(catalog(table(
        "test.history", [result("one", {"text": "One"})])),
        world="roshar", spoiler_scope="gm")
    state = {}
    state, rolled = roll_and_record(
        state, registry, "test.history", seed="fixed", expected_revision=0,
        request_id="request.roll.1", history_limit=3)
    assert state["revision"] == 1 and len(state["history"]) == 1
    assert rolled["replayed"] is False

    replay_state, replay = roll_and_record(
        state, registry, "test.history", seed="fixed", expected_revision=0,
        request_id="request.roll.1", history_limit=3)
    assert replay_state == state and replay["replayed"] is True
    with pytest.raises(TableStateError, match="different roll"):
        roll_and_record(state, registry, "test.history", seed="changed",
                        expected_revision=1, request_id="request.roll.1")
    with pytest.raises(TableStateError, match="stale"):
        roll_and_record(state, registry, "test.history", seed="next",
                        expected_revision=0, request_id="request.roll.2")

    roll_id = state["history"][0]["id"]
    state, pinned = pin_roll(state, roll_id, expected_revision=1,
                             request_id="request.pin.1")
    assert state["revision"] == 2 and state["pins"][0]["roll_id"] == roll_id
    state, replay_pin = pin_roll(state, roll_id, expected_revision=1,
                                 request_id="request.pin.1")
    assert replay_pin["replayed"] is True
    state, removed = unpin_roll(state, roll_id, expected_revision=2,
                                request_id="request.unpin.1")
    assert removed["replayed"] is False and state["pins"] == []

    for index in range(4):
        state, _ = roll_and_record(
            state, registry, "test.history", seed=f"bounded-{index}",
            expected_revision=state["revision"],
            request_id=f"request.roll.bounded.{index}", history_limit=3)
    assert len(state["history"]) == 3
    assert len(state["receipts"]) <= 100
