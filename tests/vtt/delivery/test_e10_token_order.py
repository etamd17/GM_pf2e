"""E-10: token stacking order. Stored in the token's data, sent to the GM and
the table only when nonzero (so tokens never reordered keep their versions),
drawn by (order, id)."""

from __future__ import annotations

from .conftest import add_grant, payload, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table
from .test_m2_commands import expect, send


def test_an_order_reaches_the_gm_and_the_table_and_zero_is_never_sent(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    hero = next(e for e in payload(received(gm, "vtt.snapshot")[0])["entities"] if e["id"] == "tok_hero")
    assert "order" not in hero
    table, _ = subscribe_table(stack, add_grant(stack.db))
    assert all("order" not in e for e in payload(received(table, "vtt.snapshot")[0])["entities"])
    _, ack, after = send(stack, gm, "token.update", {"id": "tok_hero", "order": 5},
                         expected=expect(stack, "token", "tok_hero"))
    assert ack["outcome"] == "applied"
    gm_patch = next(d for _, e, d in after if e == "vtt.patch" and d.get("originCommandId"))
    hero = next(e for e in gm_patch["upserted"] if e["id"] == "tok_hero")
    assert hero["order"] == 5
    [table_patch] = [payload(m) for m in received(table, "vtt.patch")]
    assert table_patch["upserted"][0]["order"] == 5
    # The GM's own version (the shared helper would also read the table's patches).
    _, ack, after = send(stack, gm, "token.update", {"id": "tok_hero", "order": 0},
                         expected=[{"kind": "token", "id": "tok_hero", "version": hero["version"]}])
    assert ack["outcome"] == "applied"
    gm_patch = next(d for _, e, d in after if e == "vtt.patch" and d.get("originCommandId"))
    assert "order" not in next(e for e in gm_patch["upserted"] if e["id"] == "tok_hero")


def test_an_order_out_of_range_or_fractional_is_invalid(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    for bad in (1_000_001, -1_000_001, 1.5):
        _, ack, _ = send(stack, gm, "token.update", {"id": "tok_hero", "order": bad},
                         expected=expect(stack, "token", "tok_hero"))
        assert ack["code"] == "invalid-schema", bad
