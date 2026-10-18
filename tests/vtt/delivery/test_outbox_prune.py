"""Outbox retention: old rows are pruned, and pruning never lets a live
subscription skip an event (the hub resyncs on any gap after its cursor)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from vtt.storage import stream
from vtt.storage import writes as W
from vtt.storage.models import VttOutbox

from .conftest import payload, received
from .test_m1_read_path import seed_scene, subscribe_gm


def positions(db, campaign="camp_a"):
    with db.session() as session:
        return [p for (p,) in session.execute(
            select(VttOutbox.position).where(VttOutbox.campaign_id == campaign).order_by(VttOutbox.position))]


def age_all(db, minutes):
    with db.transaction() as session:
        for row in session.execute(select(VttOutbox)).scalars():
            row.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)


def test_prune_keeps_the_newest_rows_and_anything_young(stack):
    seed_scene(stack.db)
    for x in range(12):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": x, "y": 64})
    everything = positions(stack.db)
    latest = everything[-1]
    # Old enough, but only rows more than `keep` behind the latest go.
    age_all(stack.db, 60)
    deleted = stream.prune_outbox(stack.db, keep=5)
    assert positions(stack.db) == [p for p in everything if p > latest - 5]
    assert deleted == len(everything) - len(positions(stack.db))
    # Young rows stay whatever their position.
    for x in range(3):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": 100 + x, "y": 64})
    before = positions(stack.db)
    assert stream.prune_outbox(stack.db, keep=1) == 5            # the five aged rows; none of the young three
    assert positions(stack.db)[-3:] == before[-3:]


def test_prune_is_per_campaign(stack):
    seed_scene(stack.db)
    for x in range(6):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": x, "y": 64})
    W.create_scene(stack.db, "camp_b", "scene_z", name="Other")
    age_all(stack.db, 60)
    stream.prune_outbox(stack.db, keep=2)
    assert len(positions(stack.db, "camp_a")) == 2
    assert len(positions(stack.db, "camp_b")) >= 1                 # its own latest is kept


def test_a_subscription_whose_next_rows_were_pruned_resyncs_instead_of_skipping(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    old_sub = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    sub = next(iter(stack.hub._by_sid.values()))
    # Rows arrive while the subscription is (artificially) not delivered to ...
    for x in range(4):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": 200 + x, "y": 64})
    # ... and the first ones after its cursor are pruned away.
    age_all(stack.db, 60)
    stream.prune_outbox(stack.db, keep=2)
    assert positions(stack.db)[0] > sub.cursor + 1
    stack.hub.notify("camp_a")
    messages = received(gm)
    assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
    assert payload(messages[0])["reason"] == "buffer-overflow" and payload(messages[0])["subscriptionId"] == old_sub
    snap = payload(messages[1])
    assert next(e for e in snap["entities"] if e["id"] == "tok_hero")["x"] == 203   # the latest state


def test_contiguous_rows_are_delivered_normally(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    received(gm)
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": 300, "y": 64})
    stack.hub.notify("camp_a")
    assert [m["name"] for m in received(gm)] == ["vtt.patch"]
    with stack.db.session() as session:
        assert session.execute(select(func.count()).select_from(VttOutbox)).scalar_one() > 0
