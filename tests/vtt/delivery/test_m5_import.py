"""M5: importing legacy /map scenes into the VTT (handoff 046).

Legacy scenes are built with core/scenes.py itself, so the importer is tested
against the real format, not a guess at it."""

from __future__ import annotations

import hashlib

from sqlalchemy import func, select

from core import scenes as legacy
from vtt.importer import legacy_scenes as importer
from vtt.storage.models import VttEntity, VttOutbox, VttScene, VttTablePointer

from .conftest import CSRF, payload, received
from .test_m1_read_path import subscribe_gm


def legacy_scene(name="Crypt", **kwargs):
    scene = legacy.new_scene("camp_a", name, **kwargs)
    scene["id"] = "abc123"
    return legacy.normalize_scene(scene)


def full_scene():
    scene = legacy_scene(width=1400, height=1000, grid_size=70)
    hero = legacy.add_token(scene, name="Valeros", x=105, y=105, size=1, color="#4f8a62", locked=True)
    hero["id"] = "tok1"
    spy = legacy.add_token(scene, name="Spy", x=455, y=315, size=2, color="#a84b45", visible_to_players=False,
                           combatant_id="cmb-1", image="spy.png")
    spy["id"] = "tok2"
    scene["walls"] = [
        {"id": "w1", "x1": 0, "y1": 0, "x2": 700, "y2": 0, "kind": "wall", "open": False, "secret": False},
        {"id": "w2", "x1": 700, "y1": 0, "x2": 700, "y2": 140, "kind": "door", "open": False, "secret": False},
        {"id": "w3", "x1": 0, "y1": 140, "x2": 140, "y2": 140, "kind": "door", "open": True, "secret": False},
        {"id": "w4", "x1": 140, "y1": 140, "x2": 280, "y2": 140, "kind": "door", "open": False, "secret": True},
    ]
    scene["lights"] = [
        {"id": "l1", "x": 210, "y": 210, "radius": 350, "color": "#ffaa33", "intensity": 0.75,
         "visible_to_players": True},
        {"id": "l2", "x": 600, "y": 600, "radius": 200, "color": "#3366ff", "intensity": 0.4,
         "visible_to_players": False},
    ]
    return scene


def rows(db):
    with db.session() as session:
        return {(r.kind, r.id): (r.data, r.hidden) for r in session.execute(select(VttEntity)).scalars()}


def outbox(db):
    with db.session() as session:
        return session.execute(select(func.count()).select_from(VttOutbox)).scalar_one()


# --- the mapping ---------------------------------------------------------------------------

def test_tokens_walls_doors_and_lights_map_across():
    plan = importer.plan_scene(full_scene())
    assert plan.scene_id == "legacy_abc123"
    assert plan.scene == {"name": "Crypt", "width": 1400, "height": 1000, "grid_size": 70, "show_grid": True,
                          "background_asset_id": None}
    by_id = {eid: (kind, data, hidden) for kind, eid, data, hidden in plan.entities}
    assert by_id["legacy_tok1"] == ("token", {
        "x": 105, "y": 105, "size": 1, "label": "Valeros", "color": 0x4F8A62, "artAssetId": None,
        "actorId": None, "locked": True, "controllerIds": []}, False)
    assert by_id["legacy_tok2"][2] is True and by_id["legacy_tok2"][1]["size"] == 2
    assert [by_id[f"legacy_w{i}"][1]["doorState"] for i in (1, 2, 3, 4)] == ["none", "closed", "open", "secret"]
    assert by_id["legacy_l1"] == ("light", {"x": 210, "y": 210, "radius": 350, "color": 0xFFAA33}, False)
    assert by_id["legacy_l2"][2] is True


def test_what_cannot_come_across_is_reported():
    scene = full_scene()
    scene["background"] = {"filename": "map.png"}
    scene["fog"]["enabled"] = True
    scene["templates"] = [{"id": "t"}]
    scene["terrain"] = [{"kind": "lava"}]
    scene["grid"]["offset_x"] = 10
    scene["settings"]["dynamic_lighting"] = True
    plan = importer.plan_scene(scene)
    importer.resolve_assets(plan, None)          # no /map file reader: the image cannot come across
    dropped = " | ".join(plan.dropped)
    for words in ("background image", "fog", "1 template", "1 terrain", "grid offset", "dynamic lighting",
                  "token art on 1", "tracker links (live HP and conditions) on 1", "light intensity on 1",
                  "vision radius on 2"):
        assert words in dropped, words


def test_out_of_range_and_broken_values_are_clamped_and_reported():
    scene = legacy_scene()
    token = legacy.add_token(scene, name="Far", x=10, y=10)
    token["x"], token["size"], token["color"] = 99999, "big", "not-a-colour"
    plan = importer.plan_scene(scene)
    [(_, _, data, _)] = plan.entities
    assert data["x"] == 16384 and data["size"] == 1 and data["color"] == 0xB88A44
    assert any("x: 99999 is outside" in c for c in plan.clamped)
    assert any("size: not a number" in c for c in plan.clamped)


def test_ids_are_stable_and_always_valid():
    assert importer.vtt_id("tok1") == "legacy_tok1"
    odd = importer.vtt_id("has space/and:colon")
    assert odd.startswith("legacyh_") and len(odd) == len("legacyh_") + 40
    assert importer.vtt_id("has space/and:colon") == odd
    assert importer.vtt_id("x" * 200) != importer.vtt_id("x" * 199)
    assert len(importer.vtt_id("x" * 200)) <= 128


def test_control_characters_never_reach_a_label():
    scene = legacy_scene(name="Bad‮Name")
    legacy.add_token(scene, name="Evil\u0007Token")
    plan = importer.plan_scene(scene)
    assert plan.name == "BadName" and plan.entities[0][2]["label"] == "EvilToken"


# --- writing -------------------------------------------------------------------------------

def test_a_dry_run_writes_nothing(stack):
    report = importer.import_campaign(stack.db, "camp_a", [full_scene()], "abc123", apply=False)
    assert report["scenes"][0]["status"] == "planned"
    assert report["scenes"][0]["entities"] == {"token": 2, "wall": 4, "light": 2}
    assert rows(stack.db) == {} and outbox(stack.db) == 0


def test_applying_imports_the_scene_puts_it_on_the_table_and_is_idempotent(stack):
    report = importer.import_campaign(stack.db, "camp_a", [full_scene()], "abc123", apply=True)
    entry = report["scenes"][0]
    assert entry["status"] == "imported" and entry["created"] == 8 and entry["onTable"] is True
    with stack.db.session() as session:
        scene = session.get(VttScene, ("camp_a", "legacy_abc123"))
        assert scene.published is True and scene.grid_size == 70
        assert session.get(VttTablePointer, "camp_a").scene_id == "legacy_abc123"
    assert len(rows(stack.db)) == 8
    again = importer.import_campaign(stack.db, "camp_a", [full_scene()], "abc123", apply=True)
    assert again["scenes"][0]["created"] == 0 and again["scenes"][0]["updated"] == 8
    assert len(rows(stack.db)) == 8


def test_a_re_import_never_deletes_what_the_gm_added(stack):
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    from vtt.storage import writes as W
    W.upsert_entity(stack.db, "camp_a", "legacy_abc123", "token", "gm_added", {"x": 1, "y": 1})
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    assert ("token", "gm_added") in rows(stack.db)


def test_a_scene_over_the_frame_limit_is_refused_whole_and_the_others_still_import(stack, monkeypatch):
    monkeypatch.setattr(importer, "GM_ENTITY_CAP", 5)
    small = legacy_scene(name="Small")
    small["id"] = "small1"
    report = importer.import_campaign(stack.db, "camp_a", [full_scene(), small], None, apply=True)
    big, ok = report["scenes"]
    assert big["status"] == "refused" and "over the frame limit" in big["reason"]
    assert ok["status"] == "imported"
    with stack.db.session() as session:
        assert session.get(VttScene, ("camp_a", "legacy_abc123")) is None      # nothing of it written
        assert session.get(VttScene, ("camp_a", "legacy_small1")) is not None


def test_a_scene_from_another_campaign_is_skipped(stack):
    other = legacy_scene()
    other["campaign_id"] = "camp_b"
    assert importer.import_campaign(stack.db, "camp_a", [other], None, apply=True)["scenes"] == []


# --- the GM route ----------------------------------------------------------------------------

def post(stack, body, csrf=CSRF):
    return stack.app.test_client().post("/vtt/api/import-legacy", json=body, headers={"X-CSRF-Token": csrf},
                                        base_url="https://localhost")


def test_the_route_needs_gm_and_csrf_and_defaults_to_a_dry_run(stack):
    stack.legacy["camp_a"] = ([full_scene()], "abc123")
    assert post(stack, {"campaignId": "camp_b"}).status_code == 403
    assert post(stack, {"campaignId": "camp_a"}, csrf="wrong").status_code == 403
    dry = post(stack, {"campaignId": "camp_a"}).get_json()
    assert dry["applied"] is False and rows(stack.db) == {}


def test_an_applied_import_reaches_a_live_gm(stack):
    scene = full_scene()
    stack.legacy["camp_a"] = ([scene], "abc123")
    assert post(stack, {"campaignId": "camp_a", "apply": True}).get_json()["scenes"][0]["status"] == "imported"
    gm, _ = subscribe_gm(stack, scene_id="legacy_abc123")
    received(gm)
    scene["tokens"][0]["x"] = 245                                   # the legacy scene changes...
    post(stack, {"campaignId": "camp_a", "apply": True})              # ...and is re-imported
    patches = [payload(m) for m in received(gm, "vtt.patch")]
    moved = [e for p in patches for e in p["upserted"] if e["id"] == "legacy_tok1"]
    assert moved and moved[-1]["x"] == 245


def test_a_hidden_nameplate_is_reported():
    scene = full_scene()
    scene["tokens"][0]["show_nameplate"] = False
    assert any("hidden nameplate on 1" in d for d in importer.plan_scene(scene).dropped)


# --- collisions (handoff 048) --------------------------------------------------------------

def _state(db):
    with db.session() as session:
        scenes = {(s.id, s.name, s.published) for s in session.execute(select(VttScene)).scalars()}
        pointer = session.get(VttTablePointer, "camp_a")
        pointed = pointer.scene_id if pointer else None
    return scenes, rows(db), pointed, outbox(db)


def test_a_native_scene_with_the_same_id_is_refused_never_overwritten(stack):
    from vtt.storage import writes as W
    W.create_scene(stack.db, "camp_a", "legacy_abc123", name="Native GM scene")
    W.upsert_entity(stack.db, "camp_a", "legacy_abc123", "token", "legacy_tok1", {"x": 7, "y": 7})
    before = _state(stack.db)
    dry = importer.import_campaign(stack.db, "camp_a", [full_scene()], "abc123", apply=False)
    assert dry["scenes"][0]["status"] == "refused" and "did not create" in dry["scenes"][0]["reason"]
    report = importer.import_campaign(stack.db, "camp_a", [full_scene()], "abc123", apply=True)
    assert report["scenes"][0]["status"] == "refused"
    assert _state(stack.db) == before


def test_a_native_entity_in_an_imported_scene_is_refused_never_overwritten(stack):
    from vtt.storage import writes as W
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    W.upsert_entity(stack.db, "camp_a", "legacy_abc123", "token", "legacy_tok9", {"x": 7, "y": 7})
    before = _state(stack.db)
    scene = full_scene()
    extra = legacy.add_token(scene, name="Late")
    extra["id"] = "tok9"
    report = importer.import_campaign(stack.db, "camp_a", [scene], None, apply=True)
    assert report["scenes"][0]["status"] == "refused"
    assert "token id legacy_tok9" in report["scenes"][0]["reason"]
    assert _state(stack.db) == before


def test_an_imported_entity_deleted_then_recreated_natively_is_no_longer_the_imports(stack):
    from vtt.storage import writes as W
    from vtt.storage.models import VttImportedEntity
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    with stack.db.transaction() as session:
        session.delete(session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1")))
    with stack.db.session() as session:      # its provenance went with it
        assert session.get(VttImportedEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1")) is None
    W.upsert_entity(stack.db, "camp_a", "legacy_abc123", "token", "legacy_tok1", {"x": 7, "y": 7})
    report = importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    assert report["scenes"][0]["status"] == "refused"
    assert rows(stack.db)[("token", "legacy_tok1")][0] == {"x": 7, "y": 7}


def test_an_imported_entity_the_gm_deleted_comes_back_on_a_re_import(stack):
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    with stack.db.transaction() as session:
        session.delete(session.get(VttEntity, ("camp_a", "legacy_abc123", "token", "legacy_tok1")))
    again = importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    assert again["scenes"][0]["status"] == "imported" and again["scenes"][0]["created"] == 1


def test_raw_and_hashed_ids_never_alias():
    raw = "has space"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    assert importer.vtt_id(raw) != importer.vtt_id(digest)
    assert importer.vtt_id(digest) == "legacy_" + digest
    assert importer.vtt_id(raw) == "legacyh_" + digest


def test_another_source_mapping_to_an_imported_id_is_refused(stack):
    # Provenance names the original source, so a different source landing on
    # the same id cannot prove itself.
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    before = _state(stack.db)
    plan = importer.plan_scene(full_scene())
    plan.legacy_id = "someone-else"
    try:
        importer.apply_plan(stack.db, "camp_a", plan)
    except importer.Refused as refusal:
        assert "did not create" in str(refusal)
    else:
        raise AssertionError("not refused")
    assert _state(stack.db) == before


def test_two_legacy_items_mapping_to_one_id_refuse_the_scene(stack):
    scene = full_scene()
    twin = legacy.add_token(scene, name="Twin")
    twin["id"] = "tok1"
    plan = importer.plan_scene(scene)
    assert plan.duplicates == ["token legacy_tok1"]
    report = importer.import_campaign(stack.db, "camp_a", [scene], None, apply=True)
    assert report["scenes"][0]["status"] == "refused" and "more than one" in report["scenes"][0]["reason"]
    assert rows(stack.db) == {} and outbox(stack.db) == 0


def test_a_refused_re_import_leaves_the_earlier_import_and_outbox_unchanged(stack, monkeypatch):
    importer.import_campaign(stack.db, "camp_a", [full_scene()], None, apply=True)
    before = _state(stack.db)
    changed = full_scene()
    changed["name"] = "Replacement"
    changed["tokens"][0]["x"] = 999
    monkeypatch.setattr(importer, "GM_ENTITY_CAP", 1)
    result = importer.import_campaign(stack.db, "camp_a", [changed], "abc123", apply=True)
    assert result["scenes"][0]["status"] == "refused"
    assert _state(stack.db) == before
