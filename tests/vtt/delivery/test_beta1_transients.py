"""Beta-1 transients (handoff 087, U-5): ping and ruler, relayed live and never
stored. The seeded scene is 2048 x 2048 with a 64 px grid, published and on
the TV; fog is off unless a test turns it on.
"""

from __future__ import annotations

from sqlalchemy import func, select

from vtt.delivery import hub as hub_module
from vtt.protocol import ContractRegistry
from vtt.storage import writes as W
from vtt.storage.models import VttOutbox

from .conftest import NAMESPACE, add_grant, payload
from .test_m1_read_path import subscribe_table
from .test_m2_commands import send
from .test_p1_fog import expect, setup

REGISTRY = ContractRegistry()


def gm_subscription(gm_client_frames):
    return next(payload(m)["subscriptionId"] for m in gm_client_frames if m["name"] == "vtt.snapshot")


def transient(gm, sub_id, body):
    gm.emit("vtt.transient", {"schemaVersion": 1, "subscriptionId": sub_id, "transient": body}, namespace=NAMESPACE)


def relays(client):
    out = []
    for message in client.get_received(NAMESPACE):
        if message["name"] == "vtt.transient_relay":
            frame = payload(message)
            out.append(frame)
    return out


def ready(stack):
    """A GM and a table, both live, their snapshots consumed; the GM's subscription id."""
    seed_frames = []
    gm, table = setup(stack)
    table.get_received(NAMESPACE)
    sub_id = next(data["subscriptionId"] for sid, event, data in stack.sent
                  if event == "vtt.snapshot" and data["audience"] == "gm")
    return gm, table, sub_id, seed_frames


def outbox_count(db):
    with db.session() as session:
        return session.execute(select(func.count()).select_from(VttOutbox)).scalar_one()


def test_a_ping_on_the_scene_the_tv_shows_reaches_the_tv_and_is_never_stored(stack):
    gm, table, sub_id, _ = ready(stack)
    before = outbox_count(stack.db)
    transient(gm, sub_id, {"type": "ping", "x": 320, "y": 192})
    [frame] = relays(table)
    REGISTRY.validate("transient", frame)
    assert frame["transient"] == {"type": "ping", "x": 320, "y": 192} and frame["ttlMs"] == 3000
    # The key is opaque: it names neither the GM nor the GM's subscription.
    assert sub_id not in frame["key"] and "user_gm" not in frame["key"]
    assert outbox_count(stack.db) == before                        # nothing stored


def test_a_ruler_and_its_clear_share_one_latest_wins_key_distinct_from_pings(stack):
    gm, table, sub_id, _ = ready(stack)
    transient(gm, sub_id, {"type": "ruler", "rule": "alternating", "points": [{"x": 64, "y": 64}, {"x": 320, "y": 64}]})
    transient(gm, sub_id, {"type": "ruler", "rule": "alternating", "points": [{"x": 64, "y": 64}, {"x": 384, "y": 64}]})
    transient(gm, sub_id, {"type": "ruler-clear"})
    transient(gm, sub_id, {"type": "ping", "x": 10, "y": 10})
    frames = relays(table)
    assert [f["transient"]["type"] for f in frames] == ["ruler", "ruler", "ruler-clear", "ping"]
    assert frames[0]["key"] == frames[1]["key"] == frames[2]["key"] != frames[3]["key"]
    assert frames[0]["ttlMs"] == 10000


def test_a_draft_scene_never_reaches_the_tv(stack):
    gm, table, sub_id, _ = ready(stack)
    W.set_table_pointer(stack.db, "camp_a", None)                  # the TV shows nothing now
    transient(gm, sub_id, {"type": "ping", "x": 320, "y": 192})
    assert relays(table) == []


def test_fog_keeps_pings_and_rulers_that_would_reveal_it_off_the_tv(stack):
    gm, table, sub_id, _ = ready(stack)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    # Reveal cells (1, 1) and (5, 1) only; (2..4, 1) stay hidden between them.
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1], [5, 1]]})
    table.get_received(NAMESPACE)
    transient(gm, sub_id, {"type": "ping", "x": 96, "y": 96})      # revealed: shown
    transient(gm, sub_id, {"type": "ping", "x": 900, "y": 900})    # fogged: not shown
    # Both ends revealed, but the segment crosses hidden cells: not shown.
    transient(gm, sub_id, {"type": "ruler", "rule": "alternating", "points": [{"x": 96, "y": 96}, {"x": 352, "y": 96}]})
    frames = relays(table)
    assert [f["transient"] for f in frames] == [{"type": "ping", "x": 96, "y": 96}]


def test_a_second_gm_on_the_same_scene_sees_what_the_tv_may_not(stack):
    gm, table, sub_id, _ = ready(stack)
    from .test_m1_read_path import subscribe_gm
    other, _ = subscribe_gm(stack)
    other.get_received(NAMESPACE)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    table.get_received(NAMESPACE)
    other.get_received(NAMESPACE)
    transient(gm, sub_id, {"type": "ping", "x": 900, "y": 900})    # under fog
    assert relays(table) == []
    assert [f["transient"]["type"] for f in relays(other)] == ["ping"]
    assert relays(gm) == []                                         # never echoed to the sender


def test_invalid_or_foreign_transients_are_dropped(stack):
    gm, table, sub_id, _ = ready(stack)
    transient(gm, "sub_ZZZZZZZZZZZZZZZZ", {"type": "ping", "x": 1, "y": 1})          # not its subscription
    transient(gm, sub_id, {"type": "ping", "x": 4000, "y": 1})                       # off the 2048 px scene
    transient(gm, sub_id, {"type": "cursor", "x": 1, "y": 1})                        # not a variant
    gm.emit("vtt.transient", {"nonsense": True}, namespace=NAMESPACE)
    assert relays(table) == []
    assert gm.is_connected(NAMESPACE)                               # a GM is not disconnected for it
    assert stack.hub.stats["transients_dropped"] >= 4


def test_a_table_that_sends_a_transient_is_closed(stack):
    _gm, table, _sub_id, _ = ready(stack)
    table.emit("vtt.transient", {"schemaVersion": 1, "subscriptionId": "sub_AAAAAAAAAAAAAAAA",
                                 "transient": {"type": "ping", "x": 1, "y": 1}}, namespace=NAMESPACE)
    assert not table.is_connected(NAMESPACE)


def test_a_flood_is_rate_limited(stack, monkeypatch):
    monkeypatch.setattr(hub_module, "TRANSIENT_RATE", 0.0)          # no refill during the test
    gm, table, sub_id, _ = ready(stack)
    for x in range(int(hub_module.TRANSIENT_BURST) + 5):
        transient(gm, sub_id, {"type": "ping", "x": x, "y": 1})
    assert len(relays(table)) == int(hub_module.TRANSIENT_BURST)
    assert stack.hub.stats["transients_rate_limited"] == 5


def test_nothing_is_replayed_to_a_table_that_connects_later(stack):
    gm, table, sub_id, _ = ready(stack)
    transient(gm, sub_id, {"type": "ping", "x": 320, "y": 192})
    later, _ = subscribe_table(stack, add_grant(stack.db))
    assert relays(later) == []
