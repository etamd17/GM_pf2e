"""The GM and the table, end to end, in real browsers (M6, handoff 053 item 5).

One running story, in order, because each step builds on the last the way a
session does: pair the TV, move and hide a token, set a background, undo and
redo it, move the table to another scene, and import a /map scene with its
background. Every step is checked on the TV through the render probe, not by
trusting the GM page.
"""

from __future__ import annotations

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

    # --- the table moves to another scene -------------------------------------------------------
    gm.locator(".vtt-scene-picker").select_option("scene_docks")
    gm.wait_for_url("**sceneId=scene_docks**")
    gm.get_by_text("live", exact=True).wait_for()
    gm.get_by_role("button", name="Show on table").click()
    wait_for(tv, lambda s: s["scene"] is not None and s["scene"]["width"] == 1600, "the TV follows to the Docks")

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

    assert gm.errors == [] and tv.errors == []
