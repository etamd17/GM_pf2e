"""Parity P3: live HP and conditions from the tracker (handoff 069).

The tracker's state reaches the VTT through ``service.sync_live``, which app.py
calls with its live indexes. Valeros (tok_hero) is the party character
``chr_valeros``; the skeleton is the combatant ``cmb_skel``.
"""

from __future__ import annotations

from sqlalchemy import func, select

from vtt import live
from vtt.commands import gm_snapshot_bytes
from vtt.protocol import ContractRegistry
from vtt.storage import writes as W
from vtt.storage.models import VttActor, VttEntity, VttOutbox

from .conftest import add_grant, payload
from .test_m1_read_path import seed_scene, subscribe_table
from .test_m2_commands import TOKEN, send
from .test_p1_fog import Table, expect, setup

REGISTRY = ContractRegistry()


def entry(name, hp, max_hp, *, pc=False, conditions=None, hidden=False, active=False, character_id=None, status=""):
    """One live-index entry, shaped as app._scene_live_indexes builds it."""
    out = {"name": name, "is_pc": pc, "current_hp": hp, "max_hp": max_hp, "conditions": conditions or {},
           "visible_to_players": not hidden, "is_active": active, "character_id": character_id}
    if not pc:
        out["hp_status"] = status
    return out


VALEROS = entry("Valeros", 18, 30, pc=True, conditions={"frightened": 2, "prone": True})
SKELETON = entry("Skeleton Guard", 20, 20)


def mirror(stack, combatants=None, characters=None):
    return stack.service.sync_live("camp_a", combatants or {}, characters or {})


class Gm(Table):
    """The GM's frames, accumulated (validated against the GM schema)."""

    def now(self):
        for message in self.client.get_received("/vtt"):
            if message["name"] not in ("vtt.snapshot", "vtt.patch"):
                continue                                  # command acks, not frames
            frame = payload(message)
            REGISTRY.validate("gm", frame)
            if message["name"] == "vtt.snapshot":
                self.held = {(e["kind"], e["id"]): e for e in frame["entities"]}
            elif message["name"] == "vtt.patch":
                for ref in frame["removed"]:
                    self.held.pop((ref["kind"], ref["id"]), None)
                for e in frame["upserted"]:
                    self.held[(e["kind"], e["id"])] = e
        return self.held


def link(stack, gm, token_id, actor):
    _, ack, _ = send(stack, gm, "token.update", {"id": token_id, "actorId": actor},
                     expected=expect(stack, "token", token_id))
    assert ack["outcome"] == "applied"


def outbox_count(db):
    with db.session() as session:
        return session.execute(select(func.count()).select_from(VttOutbox)).scalar_one()


# --- building and storing the mirror ------------------------------------------------------------

def test_records_are_built_from_the_live_indexes():
    actors = live.build(
        {"skel": entry("Skel‮eton\x07", 7, 20, status="Wounded",
                       conditions={"off_guard": True, "frightened": 0, "sickened": -1, "bad\nname": True, "doomed": 250}),
         "pc-combatant": entry("Valeros", 18, 30, pc=True, active=True, character_id="valeros"),
         "has space": entry("Nope", 1, 1)},
        {"valeros": VALEROS, "kyra": entry("Kyra", 25, 25, pc=True)})
    assert set(actors) == {"cmb_skel", "cmb_pc-combatant", "chr_valeros", "chr_kyra"}   # "cmb_has space" cannot be an id
    skel = actors["cmb_skel"]
    assert skel["name"] == "Skeleton" and skel["status"] == "Wounded" and skel["pc"] is False
    # A zero value is no condition; a bad name is left out; a value is capped.
    assert skel["conditions"] == [{"name": "doomed", "value": 99}, {"name": "off_guard", "value": None}]
    # A character is active while its combatant holds the turn.
    assert actors["chr_valeros"]["active"] is True and actors["chr_kyra"]["active"] is False
    assert actors["chr_valeros"]["conditions"] == [{"name": "frightened", "value": 2}, {"name": "prone", "value": None}]
    assert actors["chr_valeros"]["status"] == ""                              # never for a PC


def test_the_mirror_is_capped():
    many = {f"c{i:03d}": entry(f"Goblin {i}", 5, 5) for i in range(live.MAX_ACTORS + 10)}
    assert len(live.build(many, {})) == live.MAX_ACTORS


def test_sync_writes_only_what_changed_and_deletes_what_is_gone(stack):
    seed_scene(stack.db)
    before = outbox_count(stack.db)
    assert mirror(stack, {"skel": SKELETON}, {"valeros": VALEROS}) == ["chr_valeros", "cmb_skel"]
    assert outbox_count(stack.db) == before + 1                               # one event for the batch
    assert mirror(stack, {"skel": SKELETON}, {"valeros": VALEROS}) == []
    assert outbox_count(stack.db) == before + 1                               # nothing changed: no event
    assert mirror(stack, {}, {"valeros": {**VALEROS, "current_hp": 10}}) == ["chr_valeros", "cmb_skel"]
    with stack.db.session() as session:
        rows = {r.id: r.data for r in session.execute(select(VttActor)).scalars()}
    assert set(rows) == {"chr_valeros"} and rows["chr_valeros"]["hp"] == 10


def test_a_campaign_that_never_used_the_vtt_gets_no_writes(stack):
    W.create_scene(stack.db, "camp_a", "scene_a", name="x")                   # camp_a exists ...
    with stack.db.transaction() as session:
        session.query(VttEntity).delete()
        from vtt.storage.models import VttScene
        session.query(VttScene).delete()                                      # ... with no VTT scene
    streams_before = _streams(stack.db)
    assert mirror(stack, {"skel": SKELETON}) == []
    with stack.db.session() as session:
        assert session.execute(select(func.count()).select_from(VttActor)).scalar_one() == 0
    assert _streams(stack.db) == streams_before                               # not even a stream row


def _streams(db):
    from vtt.storage.models import VttCampaignStream
    with db.session() as session:
        return session.execute(select(func.count()).select_from(VttCampaignStream)).scalar_one()


# --- the GM ------------------------------------------------------------------------------------

def test_the_gm_sees_every_actor_and_follows_changes(stack):
    gm, _ = setup(stack)
    held = Gm(gm)
    assert mirror(stack, {"skel": SKELETON}, {"valeros": VALEROS})
    actor = held.now()[("actor", "chr_valeros")]
    assert (actor["name"], actor["pc"], actor["hp"], actor["maxHp"]) == ("Valeros", True, 18, 30)
    assert held.held[("actor", "cmb_skel")]["hidden"] is False
    mirror(stack, {"skel": {**SKELETON, "current_hp": 3, "hp_status": "Wounded"}}, {"valeros": VALEROS})
    skel = held.now()[("actor", "cmb_skel")]
    assert (skel["hp"], skel["status"]) == (3, "Wounded")
    mirror(stack, {}, {"valeros": VALEROS})                                   # the encounter ended
    assert ("actor", "cmb_skel") not in held.now()


# --- the table ---------------------------------------------------------------------------------

def test_the_table_sees_exact_pc_hp_and_only_a_status_for_anything_else(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    mirror(stack, {"skel": SKELETON}, {"valeros": VALEROS})
    link(stack, gm, "tok_hero", "chr_valeros")
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_skel", "actorId": "cmb_skel"})
    assert ack["outcome"] == "applied"
    held = table.now()
    assert held[("token", "tok_hero")]["live"] == {
        "hp": 18, "maxHp": 30, "status": None, "active": False,
        "conditions": [{"name": "frightened", "value": 2}, {"name": "prone", "value": None}]}
    assert held[("token", "tok_skel")]["live"] == {"hp": None, "maxHp": None, "status": None,
                                                   "conditions": [], "active": False}
    mirror(stack, {"skel": {**SKELETON, "current_hp": 0, "hp_status": "Dead"}}, {"valeros": VALEROS})
    assert table.now()[("token", "tok_skel")]["live"]["status"] == "Dead"


def test_a_change_the_table_cannot_see_sends_it_nothing(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    mirror(stack, {"skel": SKELETON}, {})
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_skel", "actorId": "cmb_skel"})
    table.now()
    held_gm = Gm(gm)
    # 20 -> 15 of 20: still above half, so the table's view is unchanged.
    mirror(stack, {"skel": {**SKELETON, "current_hp": 15}}, {})
    assert table_client.get_received("/vtt") == []
    assert held_gm.now()[("actor", "cmb_skel")]["hp"] == 15                   # the GM still hears of it


def test_a_token_whose_actor_is_hidden_in_the_tracker_never_reaches_the_table(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    mirror(stack, {"skel": SKELETON}, {})
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_skel", "actorId": "cmb_skel"})
    assert ("token", "tok_skel") in table.now()
    mirror(stack, {"skel": {**SKELETON, "visible_to_players": False}}, {})
    assert ("token", "tok_skel") not in table.now()
    held_gm = Gm(gm)
    _, ack, _ = send(stack, gm, "token.move", {"id": "tok_skel", "x": 320, "y": 320},
                     expected=expect(stack, "token", "tok_skel"))
    assert ack["outcome"] == "applied"
    assert ("token", "tok_skel") not in table.now()                          # moving it does not reveal it
    # A fresh table snapshot leaves it out too.
    later, _ = subscribe_table(stack, add_grant(stack.db))
    assert ("token", "tok_skel") not in Table(later).now()
    mirror(stack, {"skel": SKELETON}, {})
    assert ("token", "tok_skel") in table.now()
    assert ("token", "tok_skel") in held_gm.now()            # the GM kept it throughout (the move patch)


def test_unlinking_or_losing_the_actor_leaves_live_null(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    mirror(stack, {}, {"valeros": VALEROS})
    link(stack, gm, "tok_hero", "chr_valeros")
    assert table.now()[("token", "tok_hero")]["live"]["hp"] == 18
    mirror(stack, {}, {})
    assert table.now()[("token", "tok_hero")]["live"] is None
    mirror(stack, {}, {"valeros": VALEROS})
    assert table.now()[("token", "tok_hero")]["live"]["hp"] == 18
    link(stack, gm, "tok_hero", None)
    assert table.now()[("token", "tok_hero")]["live"] is None


# --- import and sizing -------------------------------------------------------------------------

def test_legacy_tracker_links_become_actor_ids(stack):
    from vtt.importer import legacy_scenes as importer
    from .test_m5_import import full_scene
    scene = full_scene()
    scene["tokens"][0]["character_id"] = "valeros"
    scene["tokens"].append({**scene["tokens"][0], "id": "tok3", "character_id": "has space"})
    report = importer.import_campaign(stack.db, "camp_a", [scene], None, apply=True)["scenes"][0]
    assert report["status"] == "imported"
    assert "tracker links (live HP and conditions) on 1 token(s)" in " | ".join(report["dropped"])
    with stack.db.session() as session:
        hero = session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1"))
        spy = session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok2"))
        broken = session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok3"))
    assert hero.data["actorId"] == "chr_valeros" and spy.data["actorId"] == "cmb_cmb-1"
    assert broken.data["actorId"] is None


def test_sizing_reserves_a_full_live_block_for_every_linked_token(stack):
    seed_scene(stack.db)
    for i in range(200):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", f"tok_{i:03d}", {"x": i, "y": i, "label": ""})
    with stack.db.session() as session:
        unlinked, _, _ = gm_snapshot_bytes(session, "camp_a", "scene_a")
    for i in range(200):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", f"tok_{i:03d}",
                        {"x": i, "y": i, "label": "", "actorId": f"cmb_{i}"})
    with stack.db.session() as session:
        linked, _, _ = gm_snapshot_bytes(session, "camp_a", "scene_a")
    # 200 linked tokens on the table outgrow the GM frame and its actor reserve.
    assert linked > unlinked and linked > 200 * 1000


# --- table-media follows the tracker's hidden flag (handoff 072, finding 1) ---------------------

def _art(stack, token_id, asset):
    with stack.db.transaction() as session:
        row = session.get(VttEntity, ("camp_a", "scene_a", "token", token_id))
        row.data = {**row.data, "artAssetId": asset}


def test_art_of_a_token_whose_actor_is_hidden_is_not_readable_by_the_table(stack):
    from .test_m6_assets import fetch, stored
    gm, table_client = setup(stack)
    asset = stored(stack)
    link(stack, gm, "tok_hero", "cmb_skel")
    _art(stack, "tok_hero", asset)
    credential = add_grant(stack.db)
    mirror(stack, {"skel": SKELETON})
    assert fetch(stack, "camp_a", asset, credential, member=False).status_code == 200
    mirror(stack, {"skel": {**SKELETON, "visible_to_players": False}})
    assert ("token", "tok_hero") not in Table(table_client).now()
    assert fetch(stack, "camp_a", asset, credential, member=False).status_code == 404
    mirror(stack, {"skel": SKELETON})                                          # shown again
    assert fetch(stack, "camp_a", asset, credential, member=False).status_code == 200


def test_art_shared_with_a_visible_token_stays_readable(stack):
    from .test_m6_assets import fetch, stored
    gm, _ = setup(stack)
    asset = stored(stack)
    link(stack, gm, "tok_hero", "cmb_skel")
    _art(stack, "tok_hero", asset)
    _, ack, _ = send(stack, gm, "token.create", {**TOKEN, "id": "tok_twin", "artAssetId": asset})
    assert ack["outcome"] == "applied"
    mirror(stack, {"skel": {**SKELETON, "visible_to_players": False}})
    assert fetch(stack, "camp_a", asset, add_grant(stack.db), member=False).status_code == 200


# --- U-4: placement hints for the Creatures list ------------------------------------------------

def test_records_carry_a_checked_size_and_sheet_link():
    actors = live.build(
        {"skel": {**entry("Skeleton", 7, 20), "vtt_hints": {"size": 2, "sheet_url": "/tracker"}},
         "evil": {**entry("Imp", 1, 1), "vtt_hints": {"size": 99, "sheet_url": "//evil.example/x"}},
         "bare": entry("Rat", 1, 1)},
        {"valeros": {**VALEROS, "vtt_hints": {"size": 1, "sheet_url": "/player/sheet/Valeros"}}})
    assert (actors["cmb_skel"]["size"], actors["cmb_skel"]["sheetUrl"]) == (2.0, "/tracker")
    assert (actors["cmb_evil"]["size"], actors["cmb_evil"]["sheetUrl"]) == (10.0, None)   # clamped; refused
    assert (actors["cmb_bare"]["size"], actors["cmb_bare"]["sheetUrl"]) == (1.0, None)    # defaults
    assert actors["chr_valeros"]["sheetUrl"] == "/player/sheet/Valeros"


def test_the_gm_actor_entity_carries_size_and_sheet_link(stack):
    gm, _ = setup(stack)
    held = Gm(gm)
    mirror(stack, {"skel": {**SKELETON, "vtt_hints": {"size": 2, "sheet_url": "/tracker"}}}, {})
    actor = held.now()[("actor", "cmb_skel")]
    assert (actor["size"], actor["sheetUrl"]) == (2.0, "/tracker")


# --- E-1: the round, for the turn controls' expected-round check --------------------------------

def test_records_carry_the_round_on_combatants_only():
    actors = live.build(
        {"skel": {**entry("Skeleton", 7, 20, active=True), "vtt_round": 3, "vtt_turn": "9f3c2a1b0e4d-12"},
         "odd": {**entry("Imp", 1, 1), "vtt_round": "3", "vtt_turn": "not a token"},
         "big": {**entry("Rat", 1, 1), "vtt_round": 10**9}},
        {"valeros": VALEROS})
    assert (actors["cmb_skel"]["round"], actors["cmb_skel"]["turnToken"]) == (3, "9f3c2a1b0e4d-12")
    assert (actors["cmb_odd"]["round"], actors["cmb_odd"]["turnToken"]) == (None, None)   # malformed: left out
    assert actors["cmb_big"]["round"] == 100000                               # capped
    assert actors["chr_valeros"]["round"] is None and actors["chr_valeros"]["turnToken"] is None


def test_the_gm_actor_entity_carries_the_round(stack):
    gm, _ = setup(stack)
    held = Gm(gm)
    mirror(stack, {"skel": {**SKELETON, "is_active": True, "vtt_round": 2, "vtt_turn": "abc-3"}}, {})
    actor = held.now()[("actor", "cmb_skel")]
    assert (actor["active"], actor["round"], actor["turnToken"]) == (True, 2, "abc-3")
