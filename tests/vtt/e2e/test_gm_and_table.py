"""The GM and the table, end to end, in real browsers (M6, handoff 053 item 5).

One running story, in order, because each step builds on the last the way a
session does: pair the TV, move and hide a token, set a background, undo and
redo it, move the table to another scene, and import a /map scene with its
background. Every step is checked on the TV through the render probe, not by
trusting the GM page.
"""

from __future__ import annotations

import json

import io

import pytest

pytestmark = pytest.mark.browser


def probe(page):
    return page.evaluate("() => window.__vttProbe ? window.__vttProbe() : null")


def wait_for(page, predicate, message, timeout_ms=15000):
    """Poll the probe until predicate(probe) holds."""
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    last = None
    while time.monotonic() < deadline:
        last = probe(page)
        if last is not None and predicate(last):
            return last
        page.wait_for_timeout(100)
    raise AssertionError(f"{message}; last probe: {last}")


def token(state, token_id):
    return next((t for t in state["tokens"] if t["id"] == token_id), None)


def click_scene(page, x, y):
    """Click a scene point on the GM canvas (scene -> screen through the probe)."""
    view = probe(page)["viewport"]
    box = page.locator(".vtt-map-host canvas").bounding_box()
    page.mouse.click(box["x"] + view["x"] + x * view["scale"], box["y"] + view["y"] + y * view["scale"])


def click_token(page, token_id):
    """Click a token where the GM canvas draws it (scene -> screen through the
    probe's viewport)."""
    state = wait_for(page, lambda s: token(s, token_id) is not None, f"{token_id} on the GM map")
    t, view = token(state, token_id), state["viewport"]
    box = page.locator(".vtt-map-host canvas").bounding_box()
    page.mouse.click(box["x"] + view["x"] + t["x"] * view["scale"], box["y"] + view["y"] + t["y"] * view["scale"])
    page.get_by_label("Label").wait_for()


def shoot(page, name):
    """Keep a screenshot for a human look when BROWSER_SHOTS_DIR is set (the
    probe proves what is drawn; this shows how it looks)."""
    import os
    folder = os.environ.get("BROWSER_SHOTS_DIR")
    if folder:
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(folder, f"{name}.png"))


def png(color=(30, 110, 200), size=(160, 120)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, "PNG")
    return out.getvalue()


def test_a_session_on_the_gm_page_and_the_table_display(site, screens):
    gm = screens("gm")
    gm.goto(site.gm_url("scene_hall"))
    gm.get_by_text("live", exact=True).wait_for()

    # --- pair the TV with a one-time code ---------------------------------------------------
    tv = screens("table")
    tv.goto(site.url + "/vtt/table")
    tv.get_by_label("Pairing code").wait_for()
    gm.get_by_role("button", name="Pair a display").click()
    code = gm.locator(".vtt-code-value").inner_text()
    tv.get_by_label("Pairing code").fill(code)
    tv.get_by_role("button", name="Pair").click()
    shown = wait_for(tv, lambda s: s["scene"] is not None and token(s, "tok_hero") is not None,
                     "the TV shows the Hall with its token")
    assert shown["surface"] == "table" and shown["scene"]["width"] == 1280

    # --- move a token: the TV follows ---------------------------------------------------------
    start_x = token(shown, "tok_hero")["x"]
    click_token(gm, "tok_hero")
    gm.locator(".vtt-map-host").focus()
    gm.keyboard.press("ArrowRight")
    wait_for(tv, lambda s: token(s, "tok_hero")["x"] == start_x + 64, "the TV sees the move")

    # --- hide it: gone from the TV, dimmed on the GM page ------------------------------------
    # The toggle shows the server's state, so it flips when the frame lands.
    gm.get_by_label("Hidden from the table").click()
    wait_for(tv, lambda s: token(s, "tok_hero") is None, "the hidden token leaves the TV")
    assert token(wait_for(gm, lambda s: token(s, "tok_hero")["hidden"], "dimmed for the GM"), "tok_hero")
    assert gm.get_by_label("Hidden from the table").is_checked()
    gm.get_by_label("Hidden from the table").click()
    wait_for(tv, lambda s: token(s, "tok_hero") is not None, "back on the TV")

    # --- set a background: uploaded, then drawn on the TV -------------------------------------
    click_scene(gm, 1200, 900)                        # empty floor: nothing selected, the Scene section
    gm.get_by_label("Background file").set_input_files(
        files=[{"name": "hall.png", "mimeType": "image/png", "buffer": png()}])
    with_bg = wait_for(tv, lambda s: s["scene"]["background"] == "loaded", "the TV draws the background")
    assert with_bg["scene"]["backgroundUrl"].startswith("/vtt/table-media/asset_")

    # --- undo and redo the background ----------------------------------------------------------
    gm.locator(".vtt-map-host").focus()
    gm.keyboard.press("Control+z")
    wait_for(tv, lambda s: s["scene"]["backgroundUrl"] is None, "undo removes it from the TV")
    gm.keyboard.press("Control+Shift+z")
    wait_for(tv, lambda s: s["scene"]["background"] == "loaded", "redo brings it back")

    # --- fog of war (parity P1): applied on the server, so the TV never gets the token ------------
    click_scene(gm, 1200, 900)                        # nothing selected: the Scene section
    gm.get_by_label("Fog of war").click()
    wait_for(tv, lambda s: s["scene"]["fog"] is not None and s["scene"]["fog"]["enabled"]
             and token(s, "tok_hero") is None, "fog on hides the token from the TV")
    gm.get_by_role("button", name="Reveal", exact=True).click()
    hero = token(probe(gm), "tok_hero")
    click_scene(gm, hero["x"], hero["y"])
    # the room left of the wall: 10 of 20 columns, all 15 rows
    wait_for(tv, lambda s: token(s, "tok_hero") is not None and s["scene"]["fog"]["revealed"] == 150,
             "revealing its room (and only its room) brings it back")
    gm.locator(".vtt-map-host").focus()
    # undo waits until the reveal has landed in the GM's own projection
    wait_for(gm, lambda s: s["scene"]["fog"]["revealed"] > 0, "the GM page shows the reveal")
    shoot(gm, "fog-gm"), shoot(tv, "fog-tv")
    gm.keyboard.press("Control+z")
    wait_for(tv, lambda s: token(s, "tok_hero") is None and s["scene"]["fog"]["revealed"] == 0,
             "undo covers the room again")
    gm.get_by_role("button", name="Select", exact=True).click()

    # --- terrain and templates (parity P2), still under fog ---------------------------------------
    gm.get_by_role("button", name="Terrain", exact=True).click()
    hero = token(probe(gm), "tok_hero")
    click_scene(gm, hero["x"], hero["y"])            # lava, the hero's room: 150 cells
    wait_for(gm, lambda s: s["scene"]["terrainPainted"] == 150, "the GM sees the painted room")
    tv.wait_for_timeout(500)
    assert probe(tv)["scene"]["terrainPainted"] == 0  # all of it is under fog: never sent to the TV
    gm.get_by_role("button", name="Reveal", exact=True).click()
    click_scene(gm, hero["x"], hero["y"])
    wait_for(tv, lambda s: s["scene"]["terrainPainted"] == 150, "revealing the room shows its terrain")
    gm.get_by_role("button", name="Template", exact=True).click()
    click_scene(gm, hero["x"], hero["y"])            # a 3-square burst on the hero
    wait_for(tv, lambda s: len(s["templates"]) == 1 and s["templates"][0]["shape"] == "burst",
             "the burst appears on the TV")
    shoot(gm, "p2-gm"), shoot(tv, "p2-tv")
    gm.get_by_role("button", name="Select", exact=True).click()
    # Drag the burst one square down by a point inside it, clear of the hero.
    state = probe(gm)
    burst, view, g = state["templates"][0], state["viewport"], state["scene"]["gridSize"]
    box = gm.locator(".vtt-map-host canvas").bounding_box()
    sx = lambda x: box["x"] + view["x"] + x * view["scale"]
    sy = lambda y: box["y"] + view["y"] + y * view["scale"]
    gm.mouse.move(sx(burst["x"] + 2 * g), sy(burst["y"]))
    gm.mouse.down()
    gm.mouse.move(sx(burst["x"] + 2 * g), sy(burst["y"] + g / 2), steps=4)
    gm.mouse.move(sx(burst["x"] + 2 * g), sy(burst["y"] + g), steps=4)
    gm.mouse.up()
    wait_for(tv, lambda s: len(s["templates"]) == 1 and s["templates"][0]["y"] == burst["y"] + g,
             "a dragged template moves on the TV")

    # --- live HP from the tracker (parity P3) ---------------------------------------------------
    # A monster joins the real tracker; the token is linked to it in the
    # inspector; damage on the tracker reaches the TV as a status only.
    tracker_post = """async ([path, body, form]) => {
        const csrf = JSON.parse(document.getElementById('vtt-boot').textContent).csrf;
        const init = form
            ? {method: 'POST', body: new URLSearchParams(body), headers: {'X-CSRF-Token': csrf, 'X-Requested-With': 'XMLHttpRequest'}}
            : {method: 'POST', body: JSON.stringify(body), headers: {'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}};
        const response = await fetch(path, init);
        return [response.status, await response.text()];
    }"""
    status, text = gm.evaluate(tracker_post, ["/api/add_custom_monster", {"name": "Skeleton Guard", "hp": 20}, False])
    assert status == 200, text
    instance = json.loads(text)["instance_id"]
    click_token(gm, "tok_hero")
    picker = gm.get_by_label("Tracker link")
    gm.wait_for_function("() => [...document.querySelectorAll('option')].some(o => o.text === 'Skeleton Guard')")
    picker.select_option(label="Skeleton Guard")
    # The only combatant, so it holds the turn: the TV outlines it.
    wait_for(tv, lambda s: (token(s, "tok_hero") or {}).get("live") == {
        "hp": None, "maxHp": None, "status": None, "conditions": [], "active": True}, "the TV sees the link, at full HP")
    status, text = gm.evaluate(tracker_post, [f"/api/adjust_hp/{instance}", {"amount": "15", "action": "damage"}, True])
    assert status == 200, text
    wait_for(tv, lambda s: (token(s, "tok_hero")["live"] or {}).get("status") == "Wounded",
             "tracker damage shows as Wounded on the TV")
    assert token(probe(tv), "tok_hero")["live"]["hp"] is None             # never the monster's numbers
    wait_for(gm, lambda s: (token(s, "tok_hero")["live"] or {}).get("hp") == 5, "the GM sees the exact HP")
    shoot(gm, "p3-gm"), shoot(tv, "p3-tv")

    # --- Speed while dragging (E-4) -------------------------------------------------------------
    # The hero token is linked to the Skeleton Guard (a custom monster: Speed
    # 25). Dragged five squares, the GM page reads 25 / 25 ft; seven squares is
    # a second move. Brought back and released where it started.
    state = probe(gm)
    view = state["viewport"]
    box = gm.locator(".vtt-map-host canvas").bounding_box()
    sx = lambda x: box["x"] + view["x"] + x * view["scale"]
    sy = lambda y: box["y"] + view["y"] + y * view["scale"]
    hero_at = token(state, "tok_hero")
    gm.mouse.move(sx(hero_at["x"]), sy(hero_at["y"]))
    gm.mouse.down()
    gm.mouse.move(sx(hero_at["x"] + 5 * 64), sy(hero_at["y"]), steps=6)
    wait_for(gm, lambda s: (s["drag"] or {}).get("label") == "25 / 25 ft", "the drag reads 25 / 25 ft")
    gm.mouse.move(sx(hero_at["x"] + 7 * 64), sy(hero_at["y"]), steps=3)
    wait_for(gm, lambda s: (s["drag"] or {}).get("moves") == 2, "seven squares is a second move")
    shoot(gm, "e4-drag")
    gm.mouse.move(sx(hero_at["x"]), sy(hero_at["y"]), steps=6)
    gm.mouse.up()
    wait_for(gm, lambda s: s["drag"] is None, "the reading goes on release")

    # --- turn controls (E-1) ----------------------------------------------------------------------
    # A second monster joins; the GM page's turn route runs the tracker's own
    # next turn, and the TV's outline follows through the live mirror.
    status, text = gm.evaluate(tracker_post, ["/api/add_custom_monster", {"name": "Zombie Shambler", "hp": 20}, False])
    assert status == 200, text
    zombie = "cmb_" + json.loads(text)["instance_id"]
    turn = """async (body) => {
        const boot = JSON.parse(document.getElementById('vtt-boot').textContent);
        const response = await fetch('/vtt/api/turn', {method: 'POST', body: JSON.stringify({campaignId: boot.campaignId, ...body}),
            headers: {'X-CSRF-Token': boot.csrf, 'Content-Type': 'application/json'}});
        return [response.status, await response.json()];
    }"""
    # A page behind the tracker is told where it is (with the turn token), and nothing moves.
    hero = f"cmb_{instance}"
    status, now = gm.evaluate(turn, {"direction": "next", "expectedActorId": hero, "expectedRound": 99999,
                                     "turnToken": "old-1"})
    assert status == 409 and now["outcome"] == "stale" and now["actorId"] == hero, now
    shown = {"expectedActorId": hero, "expectedRound": now["round"], "turnToken": now["turnToken"]}
    status, moved = gm.evaluate(turn, {"direction": "next", **shown})
    assert status == 200 and moved["outcome"] == "ok" and moved["actorId"] == zombie, moved
    wait_for(tv, lambda s: (token(s, "tok_hero")["live"] or {}).get("active") is False, "the turn moved off the hero's monster")
    # The same click again is stale: no turn is skipped.
    status, again = gm.evaluate(turn, {"direction": "next", **shown})
    assert status == 409 and again["outcome"] == "stale" and again["actorId"] == zombie, again
    status, back = gm.evaluate(turn, {"direction": "prev", "expectedActorId": zombie, "expectedRound": moved["round"],
                                      "turnToken": moved["turnToken"]})
    assert status == 200 and back["actorId"] == hero and back["round"] == now["round"], back
    wait_for(tv, lambda s: (token(s, "tok_hero")["live"] or {}).get("active") is True, "previous turn comes back to it")
    # Review 095: back at the same actor and round, the original Next is still stale.
    status, replay = gm.evaluate(turn, {"direction": "next", **shown})
    assert status == 409 and replay["outcome"] == "stale" and replay["actorId"] == hero, replay
    # ... and so is a request from before the tracker reloads a saved encounter
    # (same instance ids, same round).
    status, text = gm.evaluate(tracker_post, ["/api/save_encounter", {"encounter_name": "e1 check"}, True])
    assert status == 200, text
    status, text = gm.evaluate(tracker_post, ["/api/load_encounter", {"encounter_name": "e1 check"}, True])
    assert status == 200, text
    status, reloaded = gm.evaluate(turn, {"direction": "next", "expectedActorId": hero, "expectedRound": back["round"],
                                          "turnToken": back["turnToken"]})
    assert status == 409 and reloaded["outcome"] == "stale" and reloaded["actorId"] == hero, reloaded
    assert reloaded["turnToken"] != back["turnToken"]

    # --- conditions from the map (E-2) ------------------------------------------------------------
    condition = """async (body) => {
        const boot = JSON.parse(document.getElementById('vtt-boot').textContent);
        const response = await fetch('/vtt/api/condition', {method: 'POST', body: JSON.stringify({campaignId: boot.campaignId, ...body}),
            headers: {'X-CSRF-Token': boot.csrf, 'Content-Type': 'application/json'}});
        return [response.status, await response.json()];
    }"""
    skeleton = {"actorId": f"cmb_{instance}", "condition": "frightened"}
    status, done = gm.evaluate(condition, {**skeleton, "operation": "add", "expectedValue": 0, "rounds": 2})
    assert status == 200 and done == {"outcome": "ok", "value": 1}, done
    wait_for(gm, lambda s: (token(s, "tok_hero")["live"] or {}).get("conditions") == ["Frightened 1"],
             "the GM page shows the tracker's Frightened 1")
    status, again = gm.evaluate(condition, {**skeleton, "operation": "add", "expectedValue": 0})
    assert status == 409 and again == {"outcome": "stale", "value": 1}, again          # a double click stacks nothing
    status, wrong = gm.evaluate(condition, {**skeleton, "condition": "determined", "operation": "add", "expectedValue": 0})
    assert status == 422 and wrong["outcome"] == "invalid", wrong                      # Cosmere's, not PF2e's
    status, done = gm.evaluate(condition, {**skeleton, "operation": "remove", "expectedValue": 1})
    assert status == 200 and done["value"] == 0, done
    wait_for(gm, lambda s: not ((token(s, "tok_hero")["live"] or {}).get("conditions") or []), "and its removal")

    # --- ping and ruler on the TV (beta-1) -------------------------------------------------------
    # The hero's room is revealed; the right half, past the wall at x=640, is still fogged.
    gm.get_by_role("button", name="Ping", exact=True).click()
    click_scene(gm, 900, 300)                                       # under fog: never shown on the TV
    tv.wait_for_timeout(400)
    assert probe(tv)["transients"] == []
    click_scene(gm, 200, 300)                                       # revealed: shown
    wait_for(tv, lambda s: s["transients"] == [{"kind": "ping", "x": 200, "y": 300}], "the TV shows the ping")
    gm.get_by_role("button", name="Ruler", exact=True).click()
    state = probe(gm)
    view = state["viewport"]
    box = gm.locator(".vtt-map-host canvas").bounding_box()
    sx = lambda x: box["x"] + view["x"] + x * view["scale"]
    sy = lambda y: box["y"] + view["y"] + y * view["scale"]
    # Six squares straight across the revealed room (64 px grid): 30 ft by either rule.
    gm.mouse.move(sx(96), sy(416))
    gm.mouse.down()
    gm.mouse.move(sx(300), sy(416), steps=5)
    gm.mouse.move(sx(480), sy(416), steps=5)
    wait_for(tv, lambda s: {"kind": "ruler", "feet": 30} in s["transients"], "the TV shows the ruler and its distance")
    gm.mouse.up()
    wait_for(tv, lambda s: not any(t["kind"] == "ruler" for t in s["transients"]), "the ruler clears after release",
             timeout_ms=6000)
    gm.get_by_role("button", name="Select", exact=True).click()

    # --- bestiary to tracker (E-3) --------------------------------------------------------------
    # Search the PF2e bestiary, add a creature through the tracker's own add,
    # and a retry with the same request id adds nothing.
    bestiary = """async ([path, body]) => {
        const boot = JSON.parse(document.getElementById('vtt-boot').textContent);
        const init = body
            ? {method: 'POST', body: JSON.stringify({campaignId: boot.campaignId, ...body}),
               headers: {'X-CSRF-Token': boot.csrf, 'Content-Type': 'application/json'}}
            : {method: 'GET'};
        const url = body ? path : path + (path.includes('?') ? '&' : '?') + 'campaignId=' + boot.campaignId;
        const response = await fetch(url, init);
        return [response.status, await response.json()];
    }"""
    count_in_tracker = """async (name) => {
        const state = await (await fetch('/api/tracker_state')).json();
        const ids = new Set();
        (function walk(v) {
            if (Array.isArray(v)) v.forEach(walk);
            else if (v && typeof v === 'object') {
                if (v.name === name && v.instance_id) ids.add(v.instance_id);
                Object.values(v).forEach(walk);
            }
        })(state);
        return ids.size;
    }"""
    status, found = gm.evaluate(bestiary, ["/vtt/api/bestiary?q=goblin%20warrior", None])
    assert status == 200 and found["outcome"] == "ok", found
    goblin = next(r for r in found["results"] if r["name"] == "Goblin Warrior")
    assert goblin["size"] == 1
    status, giant = gm.evaluate(bestiary, ["/vtt/api/bestiary?q=ogre", None])
    assert any(r["size"] >= 2 for r in giant["results"]), giant          # monster sizes come from the statblock
    shown = found["encounterToken"]
    request = {"ref": goblin["ref"], "requestId": "req-e3e2e000001", "encounterToken": shown}
    status, added = gm.evaluate(bestiary, ["/vtt/api/bestiary/add", request])
    assert status == 200 and added["outcome"] == "ok" and added["actorId"].startswith("cmb_"), added
    status, again = gm.evaluate(bestiary, ["/vtt/api/bestiary/add", request])
    assert status == 200 and again["actorId"] == added["actorId"] and again["repeat"] is True, again
    assert gm.evaluate(count_in_tracker, "Goblin Warrior") == 1
    # Review 098: once the tracker clears the encounter, a request bound to the
    # old one adds nothing; a fresh one (the empty encounter's token) works.
    status, text = gm.evaluate(tracker_post, ["/api/clear_encounter", {}, True])
    assert status == 200, text
    status, stale = gm.evaluate(bestiary, ["/vtt/api/bestiary/add", {**request, "requestId": "req-e3e2e000002"}])
    assert status == 409 and stale["outcome"] == "stale" and stale["encounterToken"] != shown, stale
    assert gm.evaluate(count_in_tracker, "Goblin Warrior") == 0
    status, fresh = gm.evaluate(bestiary, ["/vtt/api/bestiary/add", {**request, "requestId": "req-e3e2e000003",
                                                                     "encounterToken": stale["encounterToken"]}])
    assert status == 200 and fresh["outcome"] == "ok", fresh
    assert gm.evaluate(count_in_tracker, "Goblin Warrior") == 1

    # --- area effects (E-5) ----------------------------------------------------------------------
    # Fire damage to the reviewed list (the goblin) through the tracker's HP
    # engine; the same request id again applies nothing.
    area = {"requestId": "req-e5e2e000001", "encounterToken": stale["encounterToken"], "action": "damage",
            "actorIds": [fresh["actorId"]], "amount": 3, "damageType": "fire"}
    status, hit = gm.evaluate(bestiary, ["/vtt/api/area-effect", area])
    assert status == 200 and hit["outcome"] == "ok", hit
    assert hit["results"] == [{"actorId": fresh["actorId"], "outcome": "applied", "net": 3, "defeated": False}], hit
    status, again = gm.evaluate(bestiary, ["/vtt/api/area-effect", area])
    assert status == 200 and again["repeat"] is True and again["results"] == hit["results"], again
    status, wrong = gm.evaluate(bestiary, ["/vtt/api/area-effect", {**area, "requestId": "req-e5e2e000002",
                                                                     "damageType": "keen"}])
    assert status == 422 and wrong["outcome"] == "invalid", wrong          # Cosmere's damage type

    # --- the table moves to another scene -------------------------------------------------------
    gm.locator(".vtt-scene-picker").select_option("scene_docks")
    gm.wait_for_url("**sceneId=scene_docks**")
    gm.get_by_text("live", exact=True).wait_for()
    gm.get_by_role("button", name="Show on table").click()
    wait_for(tv, lambda s: s["scene"] is not None and s["scene"]["width"] == 1600, "the TV follows to the Docks")
    assert probe(tv)["vision"]["lighting"] is False and probe(tv)["vision"]["drawn"] is False

    # --- dynamic lighting on the TV (E-6) ---------------------------------------------------------
    # The Dark Vault is lit: the TV draws darkness cast from its one torch,
    # blocked by the wall (the open door blocks nothing); the GM sees no
    # darkness (only a preview would show it).
    gm.locator(".vtt-scene-picker").select_option("scene_dark")
    gm.wait_for_url("**sceneId=scene_dark**")
    gm.get_by_text("live", exact=True).wait_for()
    gm.get_by_role("button", name="Show on table").click()
    wait_for(tv, lambda s: s["scene"] is not None and s["vision"]["drawn"] is True, "the TV draws the darkness")
    assert probe(tv)["vision"] == {"lighting": True, "drawn": True, "sources": 1, "occluders": 1, "builds": 1}
    vision = probe(gm)["vision"]
    assert vision["lighting"] is True and vision["drawn"] is False
    shoot(tv, "e6-tv-dark")

    # --- import the /map scene, with its background ---------------------------------------------
    gm.get_by_role("button", name="Check what would import").click()
    gm.get_by_text("Old Crypt").wait_for()
    gm.get_by_role("button", name="Import 1 scene").click()
    gm.get_by_text("Done. Imported scenes are in the scene picker", exact=False).wait_for()
    imported = "legacy_" + site.legacy_scene
    gm.locator(".vtt-scene-picker").select_option(imported)
    gm.wait_for_url(f"**sceneId={imported}**")
    wait_for(gm, lambda s: s["scene"]["width"] == 1400 and s["scene"]["background"] == "loaded",
             "the imported background is drawn on the GM page")
    assert any(t["label"] == "Skeleton" for t in probe(gm)["tokens"])
    assert probe(gm)["vision"]["lighting"] is True              # /map's dynamic lighting came across (E-6)

    assert gm.errors == [] and tv.errors == []
