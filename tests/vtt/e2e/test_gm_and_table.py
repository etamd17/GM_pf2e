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


def choose_tool(page, label):
    group = {"Token": "Place", "Light": "Place", "Ruler": "Measure", "Ping": "Measure", "Reveal": "Fog", "Hide": "Fog", "Door": "Walls"}.get(label)
    if group:
        page.get_by_role("button", name=group, exact=True).click()
    page.get_by_role("button", name="Templates" if label == "Template" else label, exact=True).click()


def test_a_session_on_the_gm_page_and_the_table_display(site, screens):
    from playwright.sync_api import expect
    gm = screens("gm")
    panel_requests = []
    def remember_panel(request):
        name = request.url.rsplit("/", 1)[-1]
        if name.endswith(".js") and name.startswith(("GmPanels-", "BestiaryPanel-", "AreaEffectPanel-", "LightingControls-")):
            panel_requests.append(name)
    gm.on("request", remember_panel)
    gm.goto(site.gm_url("scene_hall"))
    gm.get_by_text("live", exact=True).wait_for()
    # U-2: visible group labels, native keyboard activation, and distinct sub-tools.
    for group in ("Select", "Place", "Measure", "Templates", "Walls", "Fog", "Terrain"):
        assert gm.get_by_role("button", name=group, exact=True).is_visible()
    gm.get_by_role("button", name="Place", exact=True).hover()
    hint = gm.locator(".vtt-floating-tool-hint")
    hint.wait_for(state="visible")
    assert "Enter or Space" in hint.inner_text()
    button_box = gm.get_by_role("button", name="Place", exact=True).bounding_box()
    assert hint.bounding_box()["x"] >= button_box["x"] + button_box["width"]
    gm.get_by_role("button", name="Place", exact=True).focus()
    gm.keyboard.press("Enter")
    assert gm.get_by_role("button", name="Token", exact=True).get_attribute("aria-pressed") == "true"
    gm.get_by_role("button", name="Light", exact=True).click()
    assert gm.get_by_role("button", name="Light", exact=True).get_attribute("aria-pressed") == "true"
    gm.get_by_role("button", name="Measure", exact=True).focus()
    gm.keyboard.press("Space")
    assert gm.get_by_role("button", name="Ruler", exact=True).get_attribute("aria-pressed") == "true"
    choose_tool(gm, "Door")
    assert gm.get_by_role("button", name="Door", exact=True).get_attribute("aria-pressed") == "true"
    for width in (1440, 1024):
        gm.set_viewport_size({"width": width, "height": 900})
        assert gm.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shoot(gm, f"u2-shell-{width}")
    # U-8: exercise the session actions at a tablet viewport, not only a
    # no-overflow screenshot. Restore desktop after the recovery check.
    gm.set_viewport_size({"width": 1024, "height": 768})
    choose_tool(gm, "Terrain")
    gm.get_by_role("button", name="Erase terrain", exact=True).click()
    assert gm.get_by_label("Paint", exact=True).input_value() == "0"
    gm.get_by_role("button", name="Paint terrain", exact=True).click()
    assert gm.get_by_label("Paint", exact=True).input_value() == "1"
    choose_tool(gm, "Select")

    assert panel_requests == [], "hidden panels loaded before their first use"

    # --- pair the TV with a one-time code ---------------------------------------------------
    tv = screens("table")
    tv.goto(site.url + "/vtt/table")
    tv.get_by_label("Pairing code").wait_for()
    gm.get_by_role("tab", name="TV", exact=True).click()
    gm.get_by_role("button", name="Pair a display").click()
    code = gm.locator(".vtt-code-value").inner_text()
    gm.get_by_role("tab", name="Import", exact=True).click()
    expect(gm.get_by_role("heading", name="Import from /map", exact=True)).to_be_visible()
    gm.get_by_role("tab", name="TV", exact=True).click()
    expect(gm.locator(".vtt-code-value")).to_have_text(code)
    assert len([name for name in panel_requests if name.startswith("GmPanels-")]) == 1
    tv.get_by_label("Pairing code").fill(code)
    tv.get_by_role("button", name="Pair").click()
    shown = wait_for(tv, lambda s: s["scene"] is not None and token(s, "tok_hero") is not None,
                     "the TV shows the Hall with its token")
    assert shown["surface"] == "table" and shown["scene"]["width"] == 1280

    # U-4: keyboard Place and native drag both use the mirror's party actor.
    gm.get_by_role("tab", name="Creatures", exact=True).click()
    place_scout = gm.get_by_role("button", name="Place Table Scout (party)", exact=True)
    place_scout.wait_for()
    place_scout.focus()
    gm.keyboard.press("Enter")
    scout_state = wait_for(gm, lambda s: any(t["label"] == "Table Scout" and t.get("live") for t in s["tokens"]),
                           "party placement links live HP")
    scout_hp = next(t for t in scout_state["tokens"] if t["label"] == "Table Scout")["live"]["hp"]
    assert scout_hp > 0
    wait_for(tv, lambda s: any(t["label"] == "Table Scout" and (t.get("live") or {}).get("hp") == scout_hp for t in s["tokens"]),
             "TV gets exact HP for the party token")
    expect(gm.get_by_role("link", name="Open sheet for Table Scout", exact=True)).to_be_visible()
    # U-8: the second, distinct party actor is placed by drag, not a second
    # token for the first actor. Retain the duplicate-guard check below too.
    view = probe(gm)["viewport"]
    gm.get_by_role("button", name="Place Table Guide (party)", exact=True).drag_to(
        gm.locator(".vtt-map-host"), target_position={
            "x": view["x"] + 420 * view["scale"], "y": view["y"] + 350 * view["scale"]})
    wait_for(tv, lambda s: any(t["label"] == "Table Guide" and (t.get("live") or {}).get("hp", 0) > 0 for t in s["tokens"]),
             "a different party creature placed by drag reaches the TV with live HP")
    gm.once("dialog", lambda dialog: dialog.dismiss())
    place_scout.click()
    assert sum(t["label"] == "Table Scout" for t in probe(gm)["tokens"]) == 1
    view = probe(gm)["viewport"]
    gm.once("dialog", lambda dialog: dialog.accept())
    place_scout.drag_to(gm.locator(".vtt-map-host"), target_position={
        "x": view["x"] + 350 * view["scale"], "y": view["y"] + 350 * view["scale"]})
    wait_for(gm, lambda s: sum(t["label"] == "Table Scout" and bool(t.get("live")) for t in s["tokens"]) == 2,
             "native drag creates exactly one additional linked token")
    shoot(gm, "u4-creatures")

    # U-7: place, select, open the menu and hide, using only the keyboard.
    existing_ids = {t["id"] for t in probe(gm)["tokens"]}
    map_host = gm.locator(".vtt-map-host")
    map_host.focus()
    gm.keyboard.press("p")
    gm.keyboard.press("Enter")
    new_state = wait_for(gm, lambda s: any(t["id"] not in existing_ids for t in s["tokens"]),
                         "keyboard Place creates a token")
    placed = next(t for t in new_state["tokens"] if t["id"] not in existing_ids)
    wait_for(tv, lambda s: token(s, placed["id"]) is not None, "the placed token reaches the TV")
    gm.keyboard.press("v")
    token_button = gm.get_by_role("button", name=placed["label"], exact=True)
    for _ in range(40):
        gm.keyboard.press("Tab")
        if token_button.evaluate("el => el === document.activeElement"):
            break
    else:
        raise AssertionError("keyboard could not reach the token list")
    expect(token_button).to_be_visible()
    gm.keyboard.press("Enter")
    gm.keyboard.press("Shift+F10")
    menu = gm.get_by_role("dialog", name=f"Token actions: {placed['label']}", exact=True)
    expect(menu).to_be_visible()
    shoot(gm, "u7-token-menu")
    # Close has initial focus; the next field is Hide from TV.
    gm.keyboard.press("Tab")
    expect(menu.get_by_role("button", name="Hide from TV", exact=True)).to_be_focused()
    gm.keyboard.press("Enter")
    wait_for(tv, lambda s: token(s, placed["id"]) is None, "keyboard hide removes the token from the TV")
    assert token(wait_for(gm, lambda s: token(s, placed["id"])["hidden"], "GM keeps the hidden token"), placed["id"])
    gm.keyboard.press("Escape")
    expect(map_host).to_be_focused()

    # Multi-select uses a command and an undo entry per token.
    scouts = [t for t in probe(gm)["tokens"] if t["label"] == "Table Scout"]
    gm.get_by_role("button", name="Select tokens", exact=False).click()
    selection_panel = gm.get_by_role("region", name="Token selection", exact=True)
    selection_panel.get_by_role("button", name="Clear selection", exact=True).click()
    for t in scouts:
        selection_panel.locator("label").filter(has_text=t["id"]).get_by_role("checkbox").check()
    selection_panel.get_by_role("button", name="Move selection right", exact=True).click()
    wait_for(tv, lambda s: all(token(s, t["id"])["x"] == t["x"] + 64 for t in scouts), "both linked tokens move as a group")
    expect(selection_panel.get_by_text("2 applied;", exact=False)).to_be_visible()
    shoot(gm, "multi-select-panel")
    selection_panel.get_by_role("button", name="Copy tokens", exact=True).click()
    ids_before = {t["id"] for t in probe(gm)["tokens"]}
    selection_panel.get_by_role("button", name="Paste tokens (2)", exact=True).click()
    copied_state = wait_for(tv, lambda s: sum(t["label"] == "Table Scout" for t in s["tokens"]) == 4, "two copied linked party tokens reach the TV")
    copies = [t for t in copied_state["tokens"] if t["id"] not in ids_before]
    assert len(copies) == 2 and all(t.get("live", {}).get("hp", 0) > 0 for t in copies)
    gm.get_by_role("button", name="Select tokens", exact=False).click()
    map_host.focus()
    for remaining in (1, 0):
        gm.keyboard.press("Control+z")
        wait_for(tv, lambda s: sum(token(s, t["id"]) is not None for t in copies) == remaining, "undo removes one copied token at a time")
    shoot(gm, "multi-select-result")

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
    gm.get_by_role("tab", name="Scene", exact=True).click()
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

    # U-8 task 3: edit a different scene, including an actual draft token,
    # without moving the table pointer. Then return to editing the TV scene.
    displayed = probe(tv)
    gm.locator(".vtt-scene-picker").select_option("scene_docks")
    gm.wait_for_url("**sceneId=scene_docks**")
    gm.get_by_text("live", exact=True).wait_for()
    gm.get_by_text("Scene menu", exact=True).click()
    gm.get_by_label("Scene name", exact=True).fill("Docks draft")
    gm.get_by_role("button", name="Save name", exact=True).click()
    expect(gm.get_by_label("Editing", exact=True)).to_contain_text("Docks draft")
    choose_tool(gm, "Token")
    click_scene(gm, 96, 96)
    draft = wait_for(gm, lambda s: bool(s["tokens"]), "a token is created in the draft scene")["tokens"][0]
    assert probe(tv)["scene"] == displayed["scene"], "editing a draft changed the TV scene"
    assert token(probe(tv), draft["id"]) is None, "a draft token reached the TV"
    expect(gm.get_by_label("On TV", exact=True)).to_contain_text("Hall")
    gm.locator(".vtt-scene-picker").select_option("scene_hall")
    gm.wait_for_url("**sceneId=scene_hall**")
    gm.get_by_text("live", exact=True).wait_for()
    choose_tool(gm, "Select")

    # --- fog of war (parity P1): applied on the server, so the TV never gets the token ------------
    click_scene(gm, 1200, 900)                        # nothing selected: the Scene section
    gm.get_by_role("tab", name="Scene", exact=True).click()
    gm.get_by_label("Fog of war").click()
    wait_for(tv, lambda s: s["scene"]["fog"] is not None and s["scene"]["fog"]["enabled"]
             and token(s, "tok_hero") is None, "fog on hides the token from the TV")
    choose_tool(gm, "Reveal")
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
    choose_tool(gm, "Select")

    # --- terrain and templates (parity P2), still under fog ---------------------------------------
    choose_tool(gm, "Terrain")
    hero = token(probe(gm), "tok_hero")
    click_scene(gm, hero["x"], hero["y"])            # lava, the hero's room: 150 cells
    wait_for(gm, lambda s: s["scene"]["terrainPainted"] == 150, "the GM sees the painted room")
    tv.wait_for_timeout(500)
    assert probe(tv)["scene"]["terrainPainted"] == 0  # all of it is under fog: never sent to the TV
    choose_tool(gm, "Reveal")
    click_scene(gm, hero["x"], hero["y"])
    wait_for(tv, lambda s: s["scene"]["terrainPainted"] == 150, "revealing the room shows its terrain")
    choose_tool(gm, "Template")
    click_scene(gm, hero["x"], hero["y"])            # a 3-square burst on the hero
    wait_for(tv, lambda s: len(s["templates"]) == 1 and s["templates"][0]["shape"] == "burst",
             "the burst appears on the TV")
    shoot(gm, "p2-gm"), shoot(tv, "p2-tv")
    choose_tool(gm, "Select")
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
    # Wait for the roster mirror before using a button bound to its turn token.
    gm.get_by_role("tab", name="Creatures", exact=True).click()
    gm.get_by_role("listitem").filter(has_text="Zombie Shambler").first.wait_for()
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
    # The new turn strip consumes the same published action; only the mirror
    # updates the label and the TV's active outline.
    gm.get_by_role("button", name="Next turn", exact=True).click()
    wait_for(tv, lambda s: (token(s, "tok_hero")["live"] or {}).get("active") is False, "Next in the turn strip")
    gm.get_by_role("button", name="Previous turn", exact=True).click()
    wait_for(tv, lambda s: (token(s, "tok_hero")["live"] or {}).get("active") is True, "Previous in the turn strip")
    status, now = gm.evaluate(turn, {"direction": "next", "expectedActorId": hero, "expectedRound": 99999, "turnToken": "old-1"})
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
    click_token(gm, "tok_hero")
    gm.get_by_text("Conditions", exact=True).click()
    gm.get_by_role("button", name="Increase Frightened", exact=True).click()
    wait_for(tv, lambda s: "Frightened 1" in (token(s, "tok_hero")["live"] or {}).get("conditions", []), "condition picker increases Frightened")
    gm.get_by_role("button", name="Decrease Frightened", exact=True).click()
    wait_for(tv, lambda s: not (token(s, "tok_hero")["live"] or {}).get("conditions"), "condition picker decreases Frightened")
    shoot(gm, "consumers-conditions")

    # --- U-8 tasks 5 and 6: ruler, then ping, including fog privacy -------------------------------
    # The hero's room is revealed; the right half, past the wall at x=640, is still fogged.
    choose_tool(gm, "Ruler")
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
    # U-8 privacy: a ruler with a revealed start and a hidden endpoint must
    # not be relayed to the TV. Observe throughout the gesture, not only
    # after release (which would clear even an incorrectly relayed ruler).
    gm.mouse.move(sx(96), sy(416))
    gm.mouse.down()
    gm.mouse.move(sx(900), sy(416))
    wait_for(gm, lambda s: any(t["kind"] == "ruler" for t in s["transients"]), "GM sees its fog-crossing ruler")
    for _ in range(8):
        assert not any(t["kind"] == "ruler" for t in probe(tv)["transients"]), "a fog-crossing ruler reached the TV"
        tv.wait_for_timeout(100)
    gm.mouse.up()
    choose_tool(gm, "Ping")
    click_scene(gm, 900, 300)                                       # under fog: never shown on the TV
    tv.wait_for_timeout(400)
    assert probe(tv)["transients"] == []
    click_scene(gm, 200, 300)                                       # revealed: shown
    wait_for(tv, lambda s: s["transients"] == [{"kind": "ping", "x": 200, "y": 300}], "the TV shows the ping")
    choose_tool(gm, "Select")

    # U-8 task 7: a real socket interruption, with the paired TV left open.
    # Restarting the disposable harness server drops both connections. No
    # reload or re-pair is allowed to satisfy the TV recovery assertion.
    before_drop = probe(tv)
    site.stop()
    expect(tv.get_by_label("Reconnecting", exact=True)).to_be_visible(timeout=15000)
    site.start()
    wait_for(tv, lambda s: s["scene"] == before_drop["scene"]
             and sorted(s["tokens"], key=lambda t: t["id"]) == sorted(before_drop["tokens"], key=lambda t: t["id"])
             and s["templates"] == before_drop["templates"],
             "the TV reconnects by itself with its current scene, background, fog, tokens, live HP and templates", timeout_ms=30000)
    expect(tv.get_by_label("Reconnecting", exact=True)).to_have_count(0)
    gm.get_by_text("live", exact=True).wait_for()
    gm.set_viewport_size({"width": 1280, "height": 800})

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

    # Review the same real encounter through the template panel. The fresh
    # goblin has no token yet, so the GM explicitly includes it in the list.
    current_burst = probe(gm)["templates"][0]
    click_scene(gm, current_burst["x"] + 2 * 64, current_burst["y"])
    gm.get_by_role("region", name="Area effect review").wait_for()
    gm.get_by_label("Target Goblin Warrior", exact=True).wait_for()
    gm.get_by_role("button", name="Refresh targets", exact=True).click()
    gm.get_by_label("Target Goblin Warrior", exact=True).check()
    gm.get_by_label("Area amount", exact=True).fill("1")
    with gm.expect_response(lambda r: r.url.endswith("/vtt/api/area-effect") and r.request.method == "POST") as applied:
        gm.get_by_role("button", name="Apply reviewed effect", exact=True).click()
    assert applied.value.json()["results"] == [{"actorId": fresh["actorId"], "outcome": "applied", "net": 1, "defeated": False}]
    gm.get_by_text("Effect processed. Changes arrive from the tracker.", exact=True).wait_for()
    shoot(gm, "consumers-area")

    # The bestiary UI adds another goblin and queues exactly one linked token.
    gm.get_by_role("tab", name="Creatures", exact=True).click()
    gm.get_by_label("Search bestiary", exact=True).fill("goblin warrior")
    gm.get_by_role("button", name="Search bestiary", exact=True).click()
    gm.get_by_role("button", name="Add and place Goblin Warrior", exact=True).click()
    wait_for(gm, lambda s: any(t["label"] == "Goblin Warrior" and t.get("live") is not None for t in s["tokens"]), "bestiary UI places a linked creature")
    assert gm.evaluate(count_in_tracker, "Goblin Warrior") == 2
    shoot(gm, "consumers-bestiary")

    # --- the table moves to another scene -------------------------------------------------------
    gm.locator(".vtt-scene-picker").select_option("scene_docks")
    gm.wait_for_url("**sceneId=scene_docks**")
    gm.get_by_text("live", exact=True).wait_for()
    expect(gm.get_by_label("On TV", exact=True)).to_contain_text("Hall")
    # Editing the Docks alone must not publish or move the TV.
    assert probe(tv)["scene"]["width"] == 1280
    gm.get_by_text("Scene menu", exact=True).click()
    gm.get_by_label("Scene name", exact=True).fill("Docks berth")
    gm.get_by_role("button", name="Save name", exact=True).click()
    expect(gm.get_by_label("Editing", exact=True)).to_contain_text("Docks berth")
    assert probe(tv)["scene"]["width"] == 1280
    gm.once("dialog", lambda dialog: dialog.dismiss())
    gm.get_by_role("button", name="Show on TV").click()
    assert probe(tv)["scene"]["width"] == 1280
    gm.once("dialog", lambda dialog: dialog.accept())
    gm.get_by_role("button", name="Show on TV").click()
    wait_for(tv, lambda s: s["scene"] is not None and s["scene"]["width"] == 1600, "the TV follows to the Docks")
    expect(gm.get_by_label("On TV", exact=True)).to_contain_text("Docks berth")
    shoot(gm, "u3-editing-and-tv")
    assert probe(tv)["vision"]["lighting"] is False and probe(tv)["vision"]["drawn"] is False

    # --- dynamic lighting on the TV (E-6) ---------------------------------------------------------
    # The Dark Vault is lit: the TV draws darkness cast from its one torch,
    # blocked by the wall (the open door blocks nothing); the GM sees no
    # darkness (only a preview would show it).
    gm.locator(".vtt-scene-picker").select_option("scene_dark")
    gm.wait_for_url("**sceneId=scene_dark**")
    gm.get_by_text("live", exact=True).wait_for()
    assert probe(tv)["scene"]["width"] == 1600
    gm.once("dialog", lambda dialog: dialog.accept())
    gm.get_by_role("button", name="Show on TV").click()
    wait_for(tv, lambda s: s["scene"] is not None and s["vision"]["drawn"] is True, "the TV draws the darkness")
    assert probe(tv)["vision"] == {"lighting": True, "drawn": True, "sources": 1, "occluders": 1, "builds": 1}
    vision = probe(gm)["vision"]
    assert vision["lighting"] is True and vision["drawn"] is False
    shoot(tv, "e6-tv-dark")
    gm.get_by_role("tab", name="Scene", exact=True).click()
    gm.get_by_label("Preview party vision on this GM screen", exact=True).check()
    wait_for(gm, lambda s: s["vision"]["drawn"] is True, "local GM vision preview")
    gm.get_by_label("Preview party vision on this GM screen", exact=True).uncheck()
    wait_for(gm, lambda s: s["vision"]["drawn"] is False, "GM preview off")
    gm.get_by_label("Lighting on the TV", exact=True).click()
    wait_for(tv, lambda s: s["vision"]["lighting"] is False, "Scene UI turns TV lighting off")
    expect(gm.get_by_label("Lighting on the TV", exact=True)).not_to_be_checked()
    gm.get_by_label("Lighting on the TV", exact=True).click()
    wait_for(tv, lambda s: s["vision"]["drawn"] is True, "Scene UI restores TV lighting")
    expect(gm.get_by_label("Lighting on the TV", exact=True)).to_be_checked()
    shoot(gm, "consumers-lighting")
    # The TV scene cannot be deleted; the menu offers Clear TV instead.
    gm.get_by_text("Scene menu", exact=True).click()
    expect(gm.get_by_role("button", name="Delete scene", exact=True)).to_be_disabled()
    expect(gm.get_by_role("button", name="Clear TV before deleting", exact=True)).to_be_visible()
    gm.get_by_text("Scene menu", exact=True).click()
    for width in (1440, 1024):
        gm.set_viewport_size({"width": width, "height": 900})
        assert gm.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shoot(gm, f"consumers-layout-{width}")
    gm.set_viewport_size({"width": 1280, "height": 800})

    # --- import the /map scene, with its background ---------------------------------------------
    gm.get_by_role("tab", name="Import", exact=True).click()
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
    assert probe(tv)["scene"]["width"] == 1280                  # editing the import never shows it on TV
    assert not any(t["label"] == "Skeleton" for t in probe(tv)["tokens"])
    expect(gm.get_by_label("On TV", exact=True)).to_contain_text("Dark Vault")

    # Two GMs viewing the imported scene: one deletes, one stays connected
    # on the null-scene recovery snapshot, with a notice and the picker.
    observer = screens("gm")
    observer.goto(site.gm_url(imported))
    observer.get_by_text("live", exact=True).wait_for()
    wait_for(observer, lambda s: s["scene"]["width"] == 1400, "other GM views imported scene")
    gm.get_by_text("Scene menu", exact=True).click()
    gm.once("dialog", lambda dialog: dialog.accept())
    gm.get_by_role("button", name="Delete scene", exact=True).click()
    gm.wait_for_url(lambda url: f"sceneId={imported}" not in url)
    gm.get_by_text("live", exact=True).wait_for()
    observer.get_by_text("This scene was deleted.", exact=False).wait_for()
    observer.get_by_text("live", exact=True).wait_for()
    expect(observer.get_by_label("Editing", exact=True)).to_be_visible()
    expect(observer.locator(f'.vtt-scene-picker option[value="{imported}"]')).to_have_count(0)
    assert probe(tv)["scene"]["width"] == 1280  # deletion did not move or blank the TV
    shoot(observer, "u3-deleted-elsewhere")
    # The scene copy is prepared privately and opens only after applied.
    gm.locator(".vtt-scene-picker").select_option("scene_hall")
    gm.wait_for_url("**sceneId=scene_hall**")
    gm.get_by_text("live", exact=True).wait_for()
    tv_before_copy = probe(tv)["scene"]
    tv_label_before_copy = gm.get_by_label("On TV", exact=True).inner_text()
    gm.get_by_text("Scene menu", exact=True).click()
    gm.once("dialog", lambda dialog: dialog.accept("Hall review copy"))
    gm.get_by_role("button", name="Duplicate scene", exact=True).click()
    gm.wait_for_url(lambda url: "sceneId=" in url and "sceneId=scene_hall" not in url)
    gm.get_by_text("live", exact=True).wait_for()
    expect(gm.get_by_label("Editing", exact=True)).to_contain_text("Hall review copy")
    expect(gm.get_by_label("On TV", exact=True)).to_have_text(tv_label_before_copy)
    assert probe(tv)["scene"] == tv_before_copy, "duplicating changed the TV"
    # The actual form, not a fetch or a direct /tracker link, enters this campaign.
    gm.get_by_role("tab", name="Creatures", exact=True).click()
    with gm.context.expect_event("request", predicate=lambda request: request.method == "POST" and request.url.endswith(f"/campaign/{site.cid}/activate")) as activation:
        with gm.context.expect_page() as opened_tracker:
            gm.locator(".vtt-dock").get_by_role("button", name="Open tracker", exact=True).click()
    assert activation.value.header_value("origin") == site.url
    tracker = opened_tracker.value
    tracker.wait_for_url("**/tracker")
    assert tracker.evaluate("window.opener === null")
    tracker.close()
    assert gm.errors == [] and tv.errors == [] and observer.errors == []


def test_empty_campaign_guidance_and_sidebar(site, screens):
    gm = screens("gm")
    gm.goto(site.url + "/me")
    gm.locator(f'form[action="/campaign/{site.empty_cid}/activate"] button').first.click()
    gm.wait_for_url(lambda url: not url.endswith("/me"))
    gm.goto(f"{site.url}/vtt/gm?campaignId={site.empty_cid}")
    gm.get_by_role("heading", name="Set up your first scene").wait_for()
    gm.get_by_role("button", name="Pair a TV").click()
    assert gm.get_by_role("tab", name="TV", exact=True).get_attribute("aria-selected") == "true"
    gm.reload()
    gm.get_by_role("heading", name="Set up your first scene").wait_for()
    assert gm.get_by_role("tab", name="TV", exact=True).get_attribute("aria-selected") == "true"
    for width in (1440, 1024):
        gm.set_viewport_size({"width": width, "height": 900})
        assert gm.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shoot(gm, f"u1-empty-{width}")
    gm.once("dialog", lambda dialog: dialog.accept("First room"))
    gm.get_by_role("button", name="Create first scene").click()
    gm.wait_for_url("**sceneId=**")
    gm.get_by_text("live", exact=True).wait_for()
    assert gm.get_by_role("heading", name="Set up your first scene").count() == 0
    gm.get_by_text("Scene menu", exact=True).click()
    gm.once("dialog", lambda dialog: dialog.accept())
    gm.get_by_role("button", name="Delete scene", exact=True).click()
    gm.wait_for_url(lambda url: "sceneId=" not in url)
    gm.get_by_role("heading", name="Set up your first scene").wait_for()
    assert gm.errors == []
