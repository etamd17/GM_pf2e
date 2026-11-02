"""DS-03: focus and hover checks against the rendered app and its real handlers."""
from pathlib import Path
from io import BytesIO
from contextlib import contextmanager
import re
import json
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.browser
ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _focus_server(tmp_path_factory, profile, long_chronicle=False):
    """Keep the fixtured app out of pytest's already-imported application process."""
    directory = tmp_path_factory.mktemp("ds-focus-server")
    ready = directory / "ready.json"
    log_path = directory / "server.log"
    source = """
from pathlib import Path
import json, sys
from tools.ui_screens import _served
with _served(Path(sys.argv[1]), sys.argv[3]) as (port, cookie, _):
    if sys.argv[4] == 'long-chronicle':
        import app as module
        docs_root = module._chronicle_docs_root()
        entries = []
        for section in ('recap', 'lore', 'cast', 'handout'):
            entry = module._chronicle_lib.new_entry(doc_id='focus-' + section,
                slug='d-focus-' + section, title=section.title() + ' note', section=section,
                original_filename='focus.txt', source_ext='.txt', byte_count=12)
            entry['published'] = True
            entry['previewed_at'] = entry['uploaded_at']
            entries.append(entry)
            module._chronicle_doc_write(docs_root, entry['slug'], '<p>Fixture note.</p>')
        module._chronicle_lib.save_index(docs_root, {'docs': entries})
    ready = Path(sys.argv[2])
    temporary = ready.with_suffix('.tmp')
    temporary.write_text(json.dumps({'url': f'http://127.0.0.1:{port}', 'cookie': cookie}), encoding='utf-8')
    temporary.replace(ready)
    sys.stdin.read()  # The parent closes the pipe to exit the real server context.
"""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                       PYTHONUNBUFFERED="1")
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-u", "-c", source, str(ROOT), str(ready), profile,
                                    "long-chronicle" if long_chronicle else "default"],
                                   cwd=ROOT, env=environment, stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + (300 if os.name == "nt" else 90)
            while not ready.exists():
                assert process.poll() is None, log_path.read_text(encoding="utf-8")[-4000:]
                if time.monotonic() >= deadline:
                    pytest.fail("Focus server did not become ready:\n" + log_path.read_text(encoding="utf-8")[-4000:])
                time.sleep(.1)
            yield json.loads(ready.read_text(encoding="utf-8"))
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    # Only this fixture's known child tree; the venv launcher may
                    # have a Python child which also owns the listening socket.
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                else:
                    process.kill()
                process.wait(timeout=10)


@pytest.fixture(scope="module")
def focus_site(tmp_path_factory):
    with _focus_server(tmp_path_factory, "pf2e") as site:
        yield site["url"]


@pytest.fixture(scope="module")
def chronicle_site(tmp_path_factory):
    # Published normal doc entries expose all six of the real section links.
    with _focus_server(tmp_path_factory, "pf2e", long_chronicle=True) as site:
        yield site["url"]


@pytest.fixture(scope="module")
def cosmere_site_data(tmp_path_factory):
    with _focus_server(tmp_path_factory, "cosmere") as site:
        yield site


@pytest.fixture(scope="module")
def mistborn_site_data(tmp_path_factory):
    if not os.environ.get("COSMERE_DATA_DIR"):
        pytest.skip("Private Mistborn release not installed")
    with _focus_server(tmp_path_factory, "mistborn") as site:
        yield site


@pytest.fixture(scope="module", params=["cosmere", "mistborn"])
def world_control_site(request, tmp_path_factory, cosmere_site_data):
    if request.param == "cosmere":
        yield cosmere_site_data
    else:
        yield request.getfixturevalue("mistborn_site_data")


@pytest.fixture
def focus_page(browser, focus_site):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(12000)
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    yield page, focus_site
    context.close()


def _keyboard_focus(page, target):
    page.keyboard.press("Tab")
    target.focus()
    assert target.evaluate("el => el.matches(':focus-visible')")


def _ring(target):
    return target.evaluate("""el => {
        const s = getComputedStyle(el);
        return {style: s.outlineStyle, width: parseFloat(s.outlineWidth),
                color: s.outlineColor, opacity: Number(s.opacity)};
    }""")


def _assert_ring(target):
    ring = _ring(target)
    assert ring["style"] not in {"none", "hidden"} and ring["width"] >= 2, ring
    assert ring["color"] not in {"transparent", "rgba(0, 0, 0, 0)"}, ring


def _assert_four_painted_edges(normal, behind, metadata):
    """Independently refuse a ring that survives only as left/right brackets."""
    from PIL import Image
    from tools.ui_focus import _clip

    visible = Image.open(BytesIO(normal)).convert("RGB")
    hidden = Image.open(BytesIO(behind)).convert("RGB")
    clip, rect = _clip(metadata), metadata["rect"]
    distance = metadata["outlineOffset"] + metadata["outlineWidth"] / 2
    colour = tuple(metadata["outlineRgb"])
    for side in ("top", "bottom", "left", "right"):
        for fraction in (.25, .5, .75):
            if side in ("top", "bottom"):
                x = rect["x"] + rect["width"] * fraction
                y = rect["y"] - distance if side == "top" else rect["y"] + rect["height"] + distance
            else:
                x = rect["x"] - distance if side == "left" else rect["x"] + rect["width"] + distance
                y = rect["y"] + rect["height"] * fraction
            # A one-pixel perpendicular search tolerates fractional CSS positions,
            # but must find changed, fully painted ink on every edge, not text.
            found = False
            for offset in (-1, 0, 1):
                px = round(x - clip["x"] + (offset if side in ("left", "right") else 0))
                py = round(y - clip["y"] + (offset if side in ("top", "bottom") else 0))
                if 0 <= px < visible.width and 0 <= py < visible.height:
                    ink, surface = visible.getpixel((px, py)), hidden.getpixel((px, py))
                    if max(abs(a - b) for a, b in zip(ink, colour)) <= 8 and ink != surface:
                        found = True
            assert found, (side, fraction, metadata)


def _full_straight_core_contrast(normal, behind, metadata):
    """Check every geometric straight-core pixel, without filtering by ink RGB."""
    from PIL import Image
    from tools.ui_focus import _clip, contrast_ratio

    a, b = (Image.open(BytesIO(data)).convert("RGB") for data in (normal, behind))
    clip, r = _clip(metadata), metadata["rect"]
    width = metadata["outlineWidth"]
    distance = metadata["outlineOffset"] + width / 2
    # The rounded portions and their antialiased fringe are not straight cores.
    guard = max(max(radius) for radius in metadata["radii"]) + width
    values = []
    for y in range(a.height):
        for x in range(a.width):
            px, py = x + clip["x"] + .5, y + clip["y"] + .5
            horizontal = (r["x"] + guard <= px <= r["x"] + r["width"] - guard
                and min(abs(py - (r["y"] - distance)), abs(py - (r["y"] + r["height"] + distance))) < width / 2)
            vertical = (r["y"] + guard <= py <= r["y"] + r["height"] - guard
                and min(abs(px - (r["x"] - distance)), abs(px - (r["x"] + r["width"] + distance))) < width / 2)
            if horizontal or vertical:
                values.append(contrast_ratio(a.getpixel((x, y)), b.getpixel((x, y))))
    assert values, metadata
    return {"fullCoreMinimum": min(values), "fullCorePixels": len(values)}


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_base_navigation_focus_paints_all_four_edges_inside_the_scroller(focus_page, tmp_path, width):
    from tools.ui_focus import META_JS, measure

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/gm").status == 200
    nav = page.locator(".nav-links")
    assert nav.evaluate("el => getComputedStyle(el).overflowY") == "auto"
    controls = (nav.locator(":scope > a").first,
                nav.locator(":scope > button[onclick*='_qsOpen']"))
    shape = """el => ({html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
        left:el.offsetLeft,top:el.offsetTop,font:getComputedStyle(el).font})"""
    for index, control in enumerate(controls):
        control.scroll_into_view_if_needed()
        page.mouse.move(0, 0)
        before = control.evaluate(shape)
        _keyboard_focus(page, control)
        page.wait_for_timeout(200)
        metadata = page.evaluate(META_JS)
        normal, behind = measure(page, metadata)
        (tmp_path / f"nav-{index}-focus.png").write_bytes(normal)
        (tmp_path / f"nav-{index}-behind.png").write_bytes(behind)
        (tmp_path / f"nav-{index}-metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        assert not metadata.get("measurementError"), metadata
        _assert_four_painted_edges(normal, behind, metadata)
        assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
        assert control.evaluate(shape) == before


def _assert_full_paint_without_moving_layout(page, control, tmp_path, name):
    from tools.ui_focus import META_JS, measure

    control.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    shape = """el => {
        const s=getComputedStyle(el);
        return {html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
            left:el.offsetLeft,top:el.offsetTop,text:el.textContent,font:s.font,
            border:[s.borderLeftWidth,s.borderLeftColor,s.borderBottomWidth,s.borderBottomColor]};
    }"""
    before = control.evaluate(shape)
    _keyboard_focus(page, control)
    page.wait_for_timeout(350)  # Preserve, then finish, the page's real transitions.
    metadata = page.evaluate(META_JS)
    normal, behind = measure(page, metadata)
    (tmp_path / (name + "-focus.png")).write_bytes(normal)
    (tmp_path / (name + "-behind.png")).write_bytes(behind)
    (tmp_path / (name + "-metrics.json")).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    assert not metadata.get("measurementError"), metadata
    _assert_four_painted_edges(normal, behind, metadata)
    assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
    assert control.evaluate(shape) == before


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_builder_navigation_ring_stays_inside_the_real_scrollport(focus_page, tmp_path, width):
    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/player/builder").status == 200
    for name in ("nav-start", "nav-ancestry"):
        _assert_full_paint_without_moving_layout(page, page.locator("#" + name), tmp_path, name)


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_rapid_bar_ring_stays_inside_the_real_clipping_bar(focus_page, tmp_path, width):
    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/generator").status == 200
    bar = page.locator(".rapid-bar")
    for name in ("Clear", "Twist", "Trinket"):
        _assert_full_paint_without_moving_layout(page, bar.get_by_role("button", name=name, exact=True), tmp_path, name)


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_pf2e_encounter_summary_ring_clears_the_following_tool_strip(focus_page, tmp_path, width):
    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/tracker").status == 200
    _assert_full_paint_without_moving_layout(page, page.locator("#pf2e-encounter-setup > summary"), tmp_path, "setup")


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_calendar_navigation_ring_clears_the_neighbouring_month_picker(focus_page, tmp_path, width):
    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/gm/calendar").status == 200
    for name in ("Prev", "Next"):
        _assert_full_paint_without_moving_layout(page, page.locator(".cal-nav-btn").filter(has_text=name), tmp_path, name)


@contextmanager
def _cosmere_page(browser, site, width):
    context = browser.new_context(viewport={"width": width, "height": 844 if width == 390 else 900},
                                  reduced_motion="reduce")
    context.add_cookies([dict(name="session", value=site["cookie"], domain="127.0.0.1", path="/")])
    page = context.new_page()
    page.set_default_timeout(12000)
    # CI must exercise real markup without depending on the runtime font CDN.
    # The separate actual-font reproduction is recorded in the task evidence.
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    try:
        yield page
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_cosmere_encounter_summary_ring_clears_the_following_tool_strip(browser, cosmere_site_data, tmp_path, width):
    with _cosmere_page(browser, cosmere_site_data, width) as page:
        assert page.goto(cosmere_site_data["url"] + "/tracker").status == 200
        _assert_full_paint_without_moving_layout(page, page.locator("#cos-encounter-setup > summary"), tmp_path, "setup")


@pytest.mark.parametrize("width", [320, 375, 390, 1440], ids=["reflow-floor", "phone375", "phone390", "desktop"])
@pytest.mark.parametrize("route", ["/chronicle", "/chronicle/journal"], ids=["home-current", "journal-current"])
def test_chronicle_tabs_paint_all_four_edges_and_keep_the_current_underline(browser, chronicle_site, tmp_path, width, route):
    from tools.ui_focus import META_JS, measure

    context = browser.new_context(viewport={"width": width, "height": 844 if width <= 390 else 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(12000)
    page.route("https://fonts.googleapis.com/**", lambda request: request.abort())
    page.route("https://fonts.gstatic.com/**", lambda request: request.abort())
    geometry = """() => {
        const nav=document.querySelector('.chron-subnav'), rect=el=>{
            const r=el.getBoundingClientRect();return {x:r.x+scrollX,y:r.y+scrollY,width:r.width,height:r.height};};
        const tabs=[...nav.querySelectorAll('.chron-tab')].map(el=>{
            const r=rect(el), u=getComputedStyle(el,'::after'), range=document.createRange();
            range.selectNodeContents(el);
            return {label:el.textContent,html:el.outerHTML,rect:{...r,x:r.x+nav.scrollLeft},
                glyphs:[...range.getClientRects()].map(g=>({x:g.x+scrollX+nav.scrollLeft,y:g.y+scrollY,width:g.width,height:g.height})),
                underline:u.content==='none'?null:{x:r.x+nav.scrollLeft+parseFloat(u.left),
                    y:r.y+r.height-parseFloat(u.bottom)-parseFloat(u.height),
                    width:r.width-parseFloat(u.left)-parseFloat(u.right),height:parseFloat(u.height),colour:u.backgroundColor}};
        });
        const s=getComputedStyle(nav);
        return {tabs,nav:{rect:rect(nav),padding:s.padding,margin:s.margin,shadow:s.boxShadow,
                scrollWidth:nav.scrollWidth,clientWidth:nav.clientWidth},
            flow:[document.querySelector('.chron-head'),document.querySelector('.chron-body'),
                ...document.querySelector('.chron-body').children].map(rect),
            hooks:[...document.querySelectorAll('a,button,input,select,textarea,form')].map(el=>[...el.attributes].filter(a=>a.name!=='style').map(a=>[a.name,a.value])),
            pageWidth:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth};
    }"""
    try:
        assert page.goto(chronicle_site + route).status == 200
        page.evaluate("document.fonts.ready.then(() => true)")
        page.mouse.move(0, 0)
        tabs = page.locator(".chron-subnav .chron-tab")
        assert tabs.count() == 6  # Real nav, seeded through published doc entries.
        resting = page.evaluate(geometry)
        assert resting["pageWidth"] == resting["viewport"]
        # All normal section labels fit at the 320px reflow floor. The bounded
        # 280px overflow diagnostic fails on original and current CSS; it is not
        # represented as a positive gate or patched with scripted scrolling.
        # Reach both ends by native Tab; do not centre a tab or rewrite the DOM.
        for _ in range(200):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement === document.querySelector('.chron-tab')"):
                break
        else:
            pytest.fail("Native Tab did not reach the first Chronicle section")
        window_scroll = page.evaluate("[scrollX,scrollY]")
        for index in range(tabs.count()):
            if index:
                page.keyboard.press("Tab")
            page.wait_for_timeout(200)
            assert tabs.nth(index).evaluate("el => el === document.activeElement && el.matches(':focus-visible')")
            assert page.evaluate("[scrollX,scrollY]") == window_scroll
            focused = page.evaluate(geometry)
            assert focused["flow"] == resting["flow"]
            assert focused["hooks"] == resting["hooks"]
            assert focused["pageWidth"] == resting["pageWidth"]
            if index in (0, tabs.count() - 1):
                metadata = page.evaluate(META_JS)
                normal, behind = measure(page, metadata)
                (tmp_path / f"tab-{index}-focus.png").write_bytes(normal)
                (tmp_path / f"tab-{index}-behind.png").write_bytes(behind)
                metadata.update(_full_straight_core_contrast(normal, behind, metadata))
                (tmp_path / f"tab-{index}-metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
                assert not metadata.get("measurementError"), metadata
                _assert_four_painted_edges(normal, behind, metadata)
                assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
                assert metadata["fullCoreMinimum"] >= 3, metadata
            for original, actual in zip(resting["tabs"], focused["tabs"]):
                assert actual["glyphs"] == original["glyphs"]
                assert actual["underline"] == original["underline"]
                assert actual["html"] == original["html"]
                assert actual["rect"]["y"] == original["rect"]["y"]
                assert actual["rect"]["height"] == original["rect"]["height"]
                assert actual["rect"]["x"] == original["rect"]["x"] - (4 if width <= 480 else 0)
                assert actual["rect"]["width"] == original["rect"]["width"] + (8 if width <= 480 else 0)
            for left, right in zip(focused["tabs"], focused["tabs"][1:]):
                assert left["rect"]["x"] + left["rect"]["width"] <= right["rect"]["x"]
            if width > 480:
                assert focused["nav"] == resting["nav"]
        # Native reverse navigation restores the original unfocused box styles.
        for _ in range(tabs.count()):
            page.keyboard.press("Shift+Tab")
        page.wait_for_timeout(200)
        assert not page.evaluate("document.querySelector('.chron-subnav').matches(':has(> .chron-tab:focus-visible)')")
        restored = page.evaluate(geometry)
        assert restored == resting
    finally:
        context.close()


def _assert_actual_painted_ring(page, control, tmp_path, name):
    from tools.ui_focus import META_JS, measure

    page.evaluate("document.fonts.ready.then(() => true)")
    control.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    before = control.evaluate("""el => ({html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
        left:el.offsetLeft,top:el.offsetTop,text:el.textContent})""")
    _keyboard_focus(page, control)
    control.evaluate("el => el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'})")
    row = page.evaluate(META_JS)
    normal, behind = measure(page, row)
    (tmp_path / (name + "-focus.png")).write_bytes(normal)
    (tmp_path / (name + "-behind.png")).write_bytes(behind)
    assert not row.get("measurementError"), row
    assert row["contrast"] >= 3 and row["missingSamples"] == 0, row
    assert control.evaluate("""el => ({html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
        left:el.offsetLeft,top:el.offsetTop,text:el.textContent})""") == before


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_cosmere_wizard_rail_ring_clears_its_phone_scroll_edges(browser, cosmere_site_data, tmp_path, width):
    from tools.ui_focus import META_JS, measure

    with _cosmere_page(browser, cosmere_site_data, width) as page:
        assert page.goto(cosmere_site_data["url"] + "/cosmere/builder").status == 200
        page.wait_for_function("document.querySelectorAll('.bld .wiz-rail .wiz-tab').length === 9")
        shape = """el => {
            const s=getComputedStyle(el),r=el.getBoundingClientRect();
            // Keep layout coordinates independent of the rail's native sticky scroll.
            return {html:el.outerHTML,x:el.offsetLeft,y:el.offsetTop,
                width:r.width,height:r.height,font:s.font,colour:s.color,
                background:s.backgroundColor,border:s.border,opacity:s.opacity};
        }"""
        for name, index, activate, selected in (
            ("identity-active", 0, False, True),
            ("review-inactive", 8, False, False),
            ("review-active", 8, True, True),
        ):
            control = page.locator(f'.wiz-tab[data-i="{index}"]')
            if activate:
                control.click()
            page.wait_for_timeout(350)
            assert control.evaluate("el => el.classList.contains('active')") is selected
            control.scroll_into_view_if_needed()
            page.mouse.move(0, 0)
            before = control.evaluate(shape)
            _keyboard_focus(page, control)
            page.wait_for_timeout(350)
            metadata = page.evaluate(META_JS)
            normal, behind = measure(page, metadata)
            metadata.update(_full_straight_core_contrast(normal, behind, metadata))
            (tmp_path / (name + "-focus.png")).write_bytes(normal)
            (tmp_path / (name + "-behind.png")).write_bytes(behind)
            (tmp_path / (name + "-metrics.json")).write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            assert not metadata.get("measurementError"), metadata
            assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
            assert metadata["fullCoreMinimum"] >= 3, metadata
            assert control.evaluate(shape) == before
            assert metadata["outlineOffset"] == (-3 if width <= 760 else 2)


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_mistborn_stepper_ring_is_painted_without_changing_values_or_sending_actions(browser, mistborn_site_data, tmp_path, width):
    from tools.ui_focus import META_JS, measure

    with _cosmere_page(browser, mistborn_site_data, width) as page:
        assert page.goto(mistborn_site_data["url"] + "/cosmere/builder").status == 200
        page.wait_for_function("typeof LAST_PREVIEW !== 'undefined' && LAST_PREVIEW !== null")
        controls = page.locator(".mbb .stepper button")
        assert controls.count() >= 4 and controls.count() % 2 == 0
        values = "() => [...document.querySelectorAll('.mbb input,.mbb select,.mbb textarea')].map(el => [el.id,el.name,el.value,el.checked])"
        original_values = page.evaluate(values)
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        shape = """el => ({html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
            left:el.offsetLeft,top:el.offsetTop,text:el.textContent,font:getComputedStyle(el).font})"""
        for index in (0, 1, controls.count() - 2, controls.count() - 1):
            control = controls.nth(index)
            assert control.is_enabled()
            _keyboard_focus(page, control)
            # Isolate its nested parent clip from unrelated fixed-widget overlap;
            # the full profile audit retains those separate layout diagnostics.
            control.evaluate("el => el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'})")
            page.mouse.move(0, 0)
            page.wait_for_timeout(350)
            before = control.evaluate(shape)
            metadata = page.evaluate(META_JS)
            normal, behind = measure(page, metadata)
            (tmp_path / f"stepper-{index}-focus.png").write_bytes(normal)
            (tmp_path / f"stepper-{index}-behind.png").write_bytes(behind)
            (tmp_path / f"stepper-{index}-metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            assert not metadata.get("measurementError"), metadata
            assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
            assert metadata["outlineOffset"] == -2
            assert control.evaluate(shape) == before
            assert page.evaluate(values) == original_values
        assert posts == []


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_cosmere_controls_keep_selected_and_inactive_world_choices_visible(browser, world_control_site, width, tmp_path):
    with _cosmere_page(browser, world_control_site, width) as page:
        assert page.goto(world_control_site["url"] + "/cosmere/gm").status == 200
        controls = page.locator(".cw-btn:visible:not(:disabled)")
        assert controls.count() >= 2
        assert controls.locator("xpath=self::*[contains(concat(' ',normalize-space(@class),' '),' on ')]").count() >= 1
        for i in range(controls.count()):
            _assert_actual_painted_ring(page, controls.nth(i), tmp_path, f"world-{i}")


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_cosmere_glossary_ring_is_inside_the_clipping_details(browser, cosmere_site_data, width, tmp_path):
    with _cosmere_page(browser, cosmere_site_data, width) as page:
        assert page.goto(cosmere_site_data["url"] + "/cosmere/builder").status == 200
        _assert_actual_painted_ring(page, page.locator(".cb-glossary > summary"), tmp_path, "glossary")


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_cosmere_xref_ring_stays_away_from_neighbouring_punctuation(browser, cosmere_site_data, width, tmp_path):
    with _cosmere_page(browser, cosmere_site_data, width) as page:
        assert page.goto(cosmere_site_data["url"] + "/cosmere/gmscreen").status == 200
        link = page.locator("a.gms-xref").filter(has_text="Opportunity").first
        _assert_actual_painted_ring(page, link, tmp_path, "opportunity")
        # A fallback font need not hit the same punctuation pixels as Inter.
        # Keep the no-extra-gap policy alongside the actual paint/layout check.
        assert link.evaluate("el => getComputedStyle(el).outlineOffset") == "0px"


@pytest.mark.parametrize("route,opener", [("/player/builder", "class"), ("/player/levelup/Kyle", "class_feat")],
                         ids=["builder", "levelup"])
@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_picker_rows_keep_their_ring_at_the_scroll_edge_without_changing_eligibility(focus_page, tmp_path, route, opener, width):
    from tools.ui_focus import META_JS, measure, _settled

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + route).status == 200
    page.wait_for_function("typeof openModal === 'function' && typeof renderModalList === 'function'")
    page.evaluate("kind => openModal(kind)", opener)
    page.locator("#uni-modal").wait_for(state="visible")
    page.evaluate("document.fonts.ready.then(() => true)")
    # An opening scale transition changes screen deltas relative to scrollTop.
    # Align only after the real finite transition has finished.
    _settled(page)
    shape = """el => {
        const s=getComputedStyle(el);
        return {html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
                left:el.offsetLeft,top:el.offsetTop,text:el.textContent,font:s.font,
                color:s.color,opacity:s.opacity,classes:el.className};
    }"""
    for state, override, selector, opacity in (
        ("eligible", False, "button:not(.cp-opt-locked):not(.cp-opt-override)", 1),
        ("locked", False, "button.cp-opt-locked", .55),
        ("override", True, "button.cp-opt-override", 1),
    ):
        page.evaluate("""override => {
            window.ignorePrereqs = override;
            mCtx.activeTabs = false;
            mCtx.tab = 'all';
            mCtx.data = Array.from({length:48}, (_, i) => ({
                name:`${String(i).padStart(2, '0')} focus fixture`,
                level:i % 2 ? 99 : 0, desc:'Fixture description'
            }));
            renderModalList();
        }""", override)
        assert page.locator("#mod-list button").count() == 48
        candidates = page.locator("#mod-list " + selector)
        assert candidates.count() == 24  # The renderer sorts by eligibility.
        # Leave one row after the target so end padding cannot prevent alignment.
        # Enough eligible rows precede it to overflow even the desktop scroller.
        row = candidates.nth(candidates.count() - 2)
        page.mouse.move(0, 0)
        _keyboard_focus(page, row)
        _settled(page)
        # Exercise the real nested scroller, with the row flush at its bottom.
        # Do not hide the separate pre-existing level-up gains-rail overlap.
        row.evaluate("""el => {
            const list=document.getElementById('mod-list');
            list.scrollTop += el.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom;
        }""")
        assert row.evaluate("""el => Math.abs(el.getBoundingClientRect().bottom -
            document.getElementById('mod-list').getBoundingClientRect().bottom)""") <= 1
        before = row.evaluate(shape)
        assert _ring(row)["opacity"] == opacity
        _assert_ring(row)
        metadata = page.evaluate(META_JS)
        normal, behind = measure(page, metadata)
        (tmp_path / (state + "-focus.png")).write_bytes(normal)
        (tmp_path / (state + "-behind.png")).write_bytes(behind)
        assert not metadata.get("measurementError"), metadata
        assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
        assert row.evaluate(shape) == before


def test_hover_revealed_sheet_control_is_visible_on_keyboard_focus(focus_page):
    page, url = focus_page
    page.goto(url + "/player/sheet/Kyle")
    page.locator("#btn-tab-inventory").click()
    control = page.locator("button[onclick^='removeInvItem(']").first
    control.wait_for(state="attached")
    page.mouse.move(0, 0)
    _keyboard_focus(page, control)
    _assert_ring(control)
    assert _ring(control)["opacity"] == 1


@pytest.mark.parametrize("theme", ["dark", "light", "parchment", "hi-contrast"])
@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_sheet_tab_ring_clears_clipping_and_preserves_the_active_underline(focus_page, tmp_path, theme, width):
    from tools.ui_focus import META_JS, measure

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    page.goto(url + "/player/sheet/Kyle")
    page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
    combat = page.locator("#btn-tab-combat")
    inventory = page.locator("#btn-tab-inventory")
    page.wait_for_function("document.getElementById('btn-tab-combat').classList.contains('active')")
    shape = """el => {
        const s=getComputedStyle(el);
        return {width:el.offsetWidth,height:el.offsetHeight,left:el.offsetLeft,top:el.offsetTop,
                font:s.font,text:el.textContent,html:el.outerHTML,
                underline:[s.borderBottomWidth,s.borderBottomStyle,s.borderBottomColor]};
    }"""
    for state, tab, activate, selected in (
        ("default-combat", combat, False, True),
        ("inactive-inventory", inventory, False, False),
        ("active-inventory", inventory, True, True),
        ("active-combat", combat, True, True),
    ):
        if activate:
            tab.click()
        assert tab.evaluate("el => el.classList.contains('active')") is selected
        tab.scroll_into_view_if_needed()
        page.mouse.move(0, 0)
        before = tab.evaluate(shape)
        _keyboard_focus(page, tab)
        row = page.evaluate(META_JS)
        normal, behind = measure(page, row)
        (tmp_path / (state + "-focus.png")).write_bytes(normal)
        (tmp_path / (state + "-behind.png")).write_bytes(behind)
        assert not row.get("measurementError"), row
        # All sides must paint inside the strip and above the unchanged selected
        # underline, not merely a surviving left edge or matching accent paint.
        assert row["missingSamples"] == 0 and row["contrast"] >= 3, row
        assert tab.evaluate(shape) == before


def test_stretched_hub_link_paints_its_ring_on_the_tile(focus_page, tmp_path):
    from PIL import Image, ImageChops
    from tools.ui_focus import META_JS, measure

    page, url = focus_page
    page.goto(url + "/gm")
    link = page.get_by_role("link", name="Open GM Screen", exact=True)
    tile = page.locator(".tile").filter(has=link)
    tile.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    # The empty inline link has no useful box; its stretched ::after is the indicator.
    assert link.evaluate("el => el.getBoundingClientRect().width") == 0
    before = Image.open(BytesIO(tile.screenshot(animations="disabled"))).convert("RGB")
    before.save(tmp_path / "hub-link-unfocused.png")
    _keyboard_focus(page, link)
    ring = link.evaluate("""el => {
        const s = getComputedStyle(el, '::after');
        return {style:s.outlineStyle, width:s.outlineWidth, offset:s.outlineOffset,
                color:s.outlineColor};
    }""")
    assert ring["style"] == "solid" and ring["width"] == "2px", ring
    assert ring["offset"] == "-2px", ring
    # Check the actual raster, not only the pseudo-element's computed declarations.
    after = Image.open(BytesIO(tile.screenshot(path=str(tmp_path / "hub-link-focus.png"),
                                              animations="disabled"))).convert("RGB")
    metadata = page.evaluate(META_JS)
    normal, behind = measure(page, metadata)
    assert not metadata.get("measurementError"), metadata
    _assert_four_painted_edges(normal, behind, metadata)
    (tmp_path / "hub-link-ring-focus.png").write_bytes(normal)
    (tmp_path / "hub-link-ring-behind.png").write_bytes(behind)
    # An empty static anchor must not draw its own 6x24px box over the glyph.
    # Only the intended perimeter changes; keep the whole interior untouched.
    interior = (8, 8, after.width - 8, after.height - 8)
    assert ImageChops.difference(before, after).crop(interior).getbbox() is None


@pytest.mark.parametrize("width", [375, 1440], ids=["phone", "desktop"])
def test_roster_card_link_paints_inside_its_clipping_card(focus_page, tmp_path, width):
    from PIL import Image
    from tools.ui_focus import META_JS, measure

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 812 if width == 375 else 900})
    page.goto(url + "/player?roster=1")
    card = page.locator('.card[data-char-name="Kyle"]')
    link = card.get_by_role("link", name="Open Kyle's character sheet", exact=True)
    card.scroll_into_view_if_needed()
    page.mouse.move(0, 0)
    # Other card affordances keep their own appearance while the overlay link focuses.
    controls = card.locator("button")
    opacities = controls.evaluate_all("els => els.map(el => getComputedStyle(el).opacity)")
    before = Image.open(BytesIO(card.screenshot(animations="disabled"))).convert("RGB")
    _keyboard_focus(page, link)
    _assert_ring(link)
    card.screenshot(path=str(tmp_path / "roster-card-focus.png"), animations="disabled")
    metadata = page.evaluate(META_JS)
    normal, behind = measure(page, metadata)
    assert not metadata.get("measurementError"), metadata
    _assert_four_painted_edges(normal, behind, metadata)
    (tmp_path / "roster-card-ring-focus.png").write_bytes(normal)
    (tmp_path / "roster-card-ring-behind.png").write_bytes(behind)
    before.save(tmp_path / "roster-card-unfocused.png")
    assert link.evaluate("el => getComputedStyle(el).outlineOffset") == "-2px"
    assert controls.evaluate_all("els => els.map(el => getComputedStyle(el).opacity)") == opacities


@pytest.mark.parametrize("width", [375, 1440], ids=["phone", "desktop"])
def test_sheet_pin_ring_contrasts_without_touching_neighbouring_text(focus_page, tmp_path, width):
    from tools.ui_focus import META_JS, measure, problems

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 812 if width == 375 else 900})
    page.goto(url + "/player/sheet/Kyle")
    page.wait_for_function("document.querySelectorAll('.stats-strip .pin-fav-btn').length > 0")
    # Cover every header pin. Core is intentionally hidden on wide sheets, so
    # the real Core-tab skill-pin check belongs to the phone pass.
    targets = page.locator(".stats-strip .pin-fav-btn")
    count = targets.count()
    assert count >= 3
    for i in range(count + (width < 1280)):
        if i == count:
            page.locator("#btn-tab-core").click()
        pin = targets.nth(i) if i < count else page.locator(".skill-row .pin-fav-btn").first
        pin.scroll_into_view_if_needed()
        page.mouse.move(0, 0)
        before = pin.evaluate("""el => {
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return {x:r.x+scrollX, y:r.y+scrollY, width:r.width,height:r.height,
                    text:el.textContent,color:s.color,font:s.font,html:el.outerHTML};
        }""")
        _keyboard_focus(page, pin)
        # Measure this compact control away from the unrelated fixed footer/FAB
        # overlap. The full Tab audit retains and reports that layout finding.
        pin.evaluate("el => el.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'})")
        row = page.evaluate(META_JS)
        normal, behind = measure(page, row)
        (tmp_path / f"pin-{i}-focused.png").write_bytes(normal)
        (tmp_path / f"pin-{i}-behind.png").write_bytes(behind)
        (tmp_path / f"pin-{i}-metrics.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        assert not row.get("measurementError"), row
        assert not problems(row), row
        assert row["contrast"] >= 3, row
        assert row["missingSamples"] == 0, row
        assert row["outlineOffset"] == -2, row
        assert pin.evaluate("""el => {
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return {x:r.x+scrollX, y:r.y+scrollY, width:r.width,height:r.height,
                    text:el.textContent,color:s.color,font:s.font,html:el.outerHTML};
        }""") == before


@pytest.mark.parametrize("width", [390, 1440], ids=["phone", "desktop"])
def test_whisper_ring_stays_inside_its_circle_on_the_native_tab_path(focus_page, tmp_path, width):
    from tools.ui_focus import META_JS, measure

    page, url = focus_page
    page.set_viewport_size({"width": width, "height": 844 if width == 390 else 900})
    assert page.goto(url + "/player/sheet/Kyle").status == 200
    page.evaluate("document.fonts.ready.then(() => true)")
    page.mouse.move(0, 0)
    # Keep the underlying Staff card and natural scroll position: centring this
    # fixed button would replace the normal text which exposed the collision.
    for _ in range(200):
        page.keyboard.press("Tab")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        if page.evaluate("document.activeElement.id") == "whisper-fab":
            break
    else:
        pytest.fail("Native Tab navigation did not reach Whisper to GM")
    control = page.locator("#whisper-fab")
    assert control.evaluate("el => el.matches(':focus-visible')")
    staff = page.locator("#tab-combat .ds-surface-sunken").filter(
        has=page.get_by_text("Staff", exact=True)).first
    assert staff.get_by_text(re.compile(r"^1d4\s*\+\s*1B$")).count() >= 1
    shape = """el => {
        const s=getComputedStyle(el), g=getComputedStyle(el,'::before'), r=el.getBoundingClientRect();
        return {html:el.outerHTML,rect:{x:r.x,y:r.y,width:r.width,height:r.height},
                opacity:s.opacity,radius:s.borderRadius,color:s.color,background:s.backgroundColor,
                glyph:{content:g.content,font:g.font,color:g.color},scrollX,scrollY};
    }"""
    card_shape = """el => {
        const r=el.getBoundingClientRect();
        return {html:el.outerHTML,rect:{x:r.x,y:r.y,width:r.width,height:r.height}};
    }"""
    before, underlying = control.evaluate(shape), staff.evaluate(card_shape)
    metadata = page.evaluate(META_JS)
    normal, behind = measure(page, metadata)
    (tmp_path / "whisper-focus.png").write_bytes(normal)
    (tmp_path / "whisper-behind.png").write_bytes(behind)
    (tmp_path / "whisper-metrics.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    assert not metadata.get("measurementError"), metadata
    assert metadata["contrast"] >= 3 and metadata["missingSamples"] == 0, metadata
    # Fallback fonts need not place the exact Staff glyph pixels under the ring.
    # The painted frame must still fit inside the existing circle in every font.
    assert metadata["outlineOffset"] + metadata["outlineWidth"] <= 0, metadata
    assert control.evaluate(shape) == before
    assert staff.evaluate(card_shape) == underlying


def test_persistent_damage_error_keeps_its_inline_red_outline_on_focus(focus_page):
    page, url = focus_page
    page.goto(url + "/tracker")
    page.wait_for_function("typeof renderActivePanel === 'function'")
    # Deterministic combatant data, rendered through the page's real inspector.
    page.evaluate("""() => {
        STATE.combatants = [{instance_id:'focus-monster', name:'Focus test monster',
            is_pc:false, hp:20, max_hp:20, current_hp:20, ac:15, initiative:10,
            conditions:[], persistent_damage:'', defenses:{}, saves:{}, speeds:{}}];
        STATE.turn_index = 0;
        renderActivePanel();
    }""")
    control = page.locator("#pd-ap-focus-monster")
    control.wait_for(state="visible")
    _keyboard_focus(page, control)
    _assert_ring(control)
    page.evaluate("_markPDInputError(document.getElementById('pd-ap-focus-monster'), 'Invalid damage')")
    assert _ring(control)["color"] == "rgb(239, 68, 68)"
    page.evaluate("_markPDInputError(document.getElementById('pd-ap-focus-monster'), '')")
    _assert_ring(control)
    assert _ring(control)["color"] != "rgb(239, 68, 68)"


_EXPANDED_SHAPE = """el => {
    const s=getComputedStyle(el), r=el.getBoundingClientRect(), q=document.createRange();
    q.selectNodeContents(el);
    return {html:el.outerHTML,width:el.offsetWidth,height:el.offsetHeight,
        left:el.offsetLeft,top:el.offsetTop,font:s.font,background:s.backgroundColor,
        border:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth,
            s.borderTopColor,s.borderRightColor,s.borderBottomColor,s.borderLeftColor],
        glyphs:[...q.getClientRects()].map(g=>({x:g.x-r.x,y:g.y-r.y,width:g.width,height:g.height}))};
}"""


def _expanded_paint(page, control, tmp_path, name, *, scrollport=None, fully_visible=True):
    """Keep the real layout and inspect every geometric straight stroke pixel."""
    from tools.ui_focus import META_JS, measure

    page.mouse.move(0, 0)
    page.wait_for_timeout(350)  # Settle existing hover transitions before the baseline.
    before = control.evaluate(_EXPANDED_SHAPE)
    if not control.evaluate("el => el === document.activeElement && el.matches(':focus-visible')"):
        _keyboard_focus(page, control)
    page.wait_for_timeout(350)
    if scrollport:
        # Borders/scrollbars are not part of a scroller's inner painting area.
        control.evaluate("""(el, selector) => {
            const list=document.querySelector(selector), p=list.getBoundingClientRect();
            list.scrollTop += el.getBoundingClientRect().bottom -
                (p.top+list.clientTop+list.clientHeight);
        }""", scrollport)
    row = page.evaluate(META_JS)
    row['scrollport'] = control.evaluate("""el => {
        let p=el.parentElement;
        while(p&&!/(auto|scroll)/.test(getComputedStyle(p).overflowY)) p=p.parentElement;
        if(!p) return null;
        const r=el.getBoundingClientRect(), b=p.getBoundingClientRect();
        return {rect:{x:b.x,y:b.y,width:b.width,height:b.height},
            clientWidth:p.clientWidth,clientHeight:p.clientHeight,
            scrollLeft:p.scrollLeft,scrollTop:p.scrollTop,
            bottomClearance:b.top+p.clientTop+p.clientHeight-r.bottom};
    }""")
    normal, behind = measure(page, row)
    row.update(_full_straight_core_contrast(normal, behind, row))
    (tmp_path / (name + '-focus.png')).write_bytes(normal)
    (tmp_path / (name + '-behind.png')).write_bytes(behind)
    (tmp_path / (name + '-metrics.json')).write_text(json.dumps(row, indent=2), encoding='utf-8')
    assert control.evaluate(_EXPANDED_SHAPE) == before
    if fully_visible:
        assert not row.get('measurementError'), row
        assert row['missingSamples'] == 0 and row['contrast'] >= 3, row
        assert row['fullCoreMinimum'] >= 3, row
    return row


def _sheet_expanded_page(focus_page, width):
    page, url = focus_page
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 900})
    assert page.goto(url + '/player/sheet/Kyle').status == 200
    page.wait_for_timeout(500)  # Initial spell-slot synchronisation precedes focus assertions.
    return page


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_equipment_rows_keep_eligibility_opacity_and_paint_inside_the_scrollport(focus_page, tmp_path, width):
    page = _sheet_expanded_page(focus_page, width)
    page.evaluate('openWeaponBrowser()')
    page.locator('#equip-modal').wait_for(state='visible')
    # The captured catalogue contains no untrained rows. Feed bounded Advanced
    # entries to the real renderer, then use its actual category handler.
    page.evaluate("""() => {
        for(let i=0;i<20;i++) dbWeapons.push({...dbWeapons[0],
            name:`DS03 advanced fixture ${String(i).padStart(2,'0')}`,category:'advanced'});
        renderEquipList();
    }""")
    requests = []
    page.on('request', lambda r: requests.append(r.url)
            if r.method == 'POST' and '/api/spell_slots/' not in r.url else None)
    for state, selector, opacity in (
        ('proficient', '#equip-list .equip-item:not(.opacity-40)', 1),
        ('untrained', '#equip-list .equip-item.opacity-40', .4),
    ):
        if state == 'untrained':
            page.locator('#equip-cat-btns').get_by_role('button', name='Advanced', exact=True).click()
        page.locator('#equip-search').click()
        # The actual keyboard path enters rows without selecting/equipping one.
        for _ in range(12):
            page.keyboard.press('Tab')
            if page.evaluate('selector => document.activeElement.matches(selector)', selector):
                break
        else:
            pytest.fail('Native Tab did not reach the real equipment rows')
        for _ in range(5):
            page.keyboard.press('Tab')
        control = page.locator(selector).nth(5)
        assert control.evaluate('el => el === document.activeElement')
        page.wait_for_timeout(350)
        # Native focus can horizontally scroll the modal's clipped contents
        # outside its frame on phone. Preserve that separate layout defect
        # instead of moving/resizing it to manufacture a four-edge pass.
        rect = control.bounding_box()
        frame = page.locator('#equip-modal > .parchment-frame')
        frame_rect = frame.bounding_box()
        left, right = max(0, frame_rect['x']), min(width, frame_rect['x'] + frame_rect['width'])
        visible = rect['x'] >= left and rect['x'] + rect['width'] <= right
        row = _expanded_paint(page, control, tmp_path, state,
                              scrollport='#equip-list', fully_visible=visible)
        (tmp_path / (state + '-frame.json')).write_text(json.dumps(
            {'control':rect,'frame':frame_rect,'fully_inside_frame':visible}, indent=2), encoding='utf-8')
        assert row['effectiveOpacity'] == opacity, row
        if not visible:
            assert width == 390
            assert rect['x'] < left or rect['x'] + rect['width'] > right, row
            assert control.bounding_box()['x'] == rect['x']
            assert frame.bounding_box() == frame_rect
    assert requests == []


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_equipment_categories_keep_selected_borders_and_their_full_focus_stroke(focus_page, tmp_path, width):
    page = _sheet_expanded_page(focus_page, width)
    page.evaluate('openWeaponBrowser()')
    for name in ('Simple', 'Advanced'):
        control = page.locator('#equip-cat-btns').get_by_role('button', name=name, exact=True)
        _expanded_paint(page, control, tmp_path, name + '-inactive')
        control.click()  # Only changes the browser's view/filter.
        _expanded_paint(page, control, tmp_path, name + '-selected')


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_choice_category_tabs_keep_selected_and_unselected_focus_strokes(focus_page, tmp_path, width):
    page, url = focus_page
    page.set_viewport_size({'width': width, 'height': 844 if width == 390 else 900})
    page.goto(url + '/player/builder')
    page.evaluate("openModal('class')")
    page.locator('#uni-modal').wait_for(state='visible')
    for name, logical_tab, activate in (('Core', 'core', False), ('All', 'all', False), ('All', 'all', True)):
        control = page.locator('#mod-tabs .mod-tab-btn[data-tab="' + logical_tab + '"]')
        if activate:
            control.click()
            assert page.evaluate('mCtx.tab') == logical_tab
        _expanded_paint(page, control, tmp_path, name + ('-selected' if activate else '-initial'))


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_caster_view_buttons_keep_both_selected_states_and_full_focus_strokes(focus_page, tmp_path, width):
    page = _sheet_expanded_page(focus_page, width)
    page.locator('#btn-tab-magic').click()
    toggle = page.locator('.caster-view-toggle').first
    toggle.scroll_into_view_if_needed()
    index = int(toggle.get_attribute('id').rsplit('-', 1)[1])
    requests = []
    page.on('request', lambda r: requests.append(r.url)
            if r.method == 'POST' and '/api/spell_slots/' not in r.url else None)
    for selected in ('play', 'prep'):
        # The real view-only handler avoids opening/committing Daily Preparation.
        page.evaluate('([i,mode]) => setCasterView(i,mode)', [index, selected])
        for name in ('Play', 'Prep'):
            control = toggle.get_by_role('button', name=name, exact=True)
            assert control.evaluate("el => el.classList.contains('active')") == (name.lower() == selected)
            _expanded_paint(page, control, tmp_path, selected + '-' + name)
    assert requests == []


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_inventory_focus_strokes_clear_the_list_without_changing_item_actions(focus_page, tmp_path, width):
    page = _sheet_expanded_page(focus_page, width)
    page.locator('#btn-tab-inventory').click()
    item = page.locator('#inv-list .inv-item').filter(
        has=page.locator('.invested-toggle[data-item-name="Clear Voiced Chime"]'))
    requests = []
    page.on('request', lambda r: requests.append(r.url)
            if r.method == 'POST' and '/api/spell_slots/' not in r.url else None)
    for name, selector, rest in (
        ('invested', '.invested-toggle', '1'),
        ('drop', 'button[onclick^="removeInvItem("]', '0'),
    ):
        control = item.locator(selector)
        item.scroll_into_view_if_needed()
        # Position at the edge before keyboard focus, allowing the browser's
        # normal focus scrolling to retain its own clearance. Scrolling AFTER
        # focus would manufacture a zero-clearance state for tiny Drop's glyph.
        control.evaluate("""el => {
            const list=document.getElementById('inv-list'), p=list.getBoundingClientRect();
            list.scrollTop += el.getBoundingClientRect().bottom-
                (p.top+list.clientTop+list.clientHeight);
        }""")
        page.mouse.move(0, 0)
        page.locator('#btn-tab-inventory').focus()
        page.wait_for_timeout(350)
        assert control.evaluate('el => getComputedStyle(el).opacity') == rest
        if name == 'drop':
            # Follow the real same-row keyboard chain. The taller invested
            # control leaves Drop its natural 2px bottom clearance; do not
            # invent a new zero-clearance state by scrolling after focus.
            _keyboard_focus(page, item.locator('.invested-toggle'))
            page.keyboard.press('Tab')
            assert control.evaluate("el => el === document.activeElement && el.matches(':focus-visible')")
        row = _expanded_paint(page, control, tmp_path, name)
        assert row['effectiveOpacity'] == 1
        if name == 'drop':
            assert abs(row['scrollport']['bottomClearance'] - 2) <= .5, row
        page.keyboard.press('Shift+Tab')
        page.wait_for_timeout(350)
        assert control.evaluate('el => getComputedStyle(el).opacity') == rest
        item.hover()
        page.wait_for_timeout(350)
        assert control.evaluate('el => getComputedStyle(el).opacity') == '1'
        page.mouse.move(0, 0)
    assert requests == []


@pytest.mark.parametrize('width', [390, 1440], ids=['phone', 'desktop'])
def test_condition_toggle_keeps_on_off_borders_and_paints_at_the_real_scroll_edge(focus_page, tmp_path, width):
    page = _sheet_expanded_page(focus_page, width)
    requests = []
    page.on('request', lambda r: requests.append(r.url)
            if r.method == 'POST' and '/api/spell_slots/' not in r.url else None)
    for active in (False, True):
        # Client fixture state only; the real renderer produces its On/Off control.
        # Never click the toggle or send a condition mutation to the server.
        page.evaluate('(active) => {currentConditions.undetected=active;openConditionPicker();}', active)
        control = page.locator('[data-cond-row="undetected"] > button[data-cond-act="toggle"]')
        control.wait_for(state='visible')
        assert control.inner_text() == ('On' if active else 'Off')
        page.mouse.move(0, 0)
        for _ in range(220):
            page.keyboard.press('Tab')
            if control.evaluate("el => el === document.activeElement && el.matches(':focus-visible')"):
                break
        else:
            pytest.fail('Native Tab did not reach the final real condition toggle')
        error = control.evaluate("""el => {
            let p=el.parentElement;
            while(p&&!/(auto|scroll)/.test(getComputedStyle(p).overflowY)) p=p.parentElement;
            if(!p) throw Error('Condition scrollport missing');
            let r=el.getBoundingClientRect(), b=p.getBoundingClientRect();
            p.scrollTop += r.bottom-(b.top+p.clientTop+p.clientHeight);
            return el.getBoundingClientRect().bottom-
                (p.getBoundingClientRect().top+p.clientTop+p.clientHeight);
        }""")
        assert abs(error) <= .5
        _expanded_paint(page, control, tmp_path, 'on' if active else 'off')
        page.locator('[data-close]').click()
    assert requests == []


@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "touch"])
def test_tile_hover_lift_is_only_on_a_hover_pointer(browser, focus_site, touch):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, has_touch=touch)
    try:
        page = context.new_page()
        page.goto(focus_site + "/gm")
        tile = page.locator(".tile").first
        tile.wait_for(state="visible")
        page.mouse.move(0, 0)
        before = tile.evaluate("el => getComputedStyle(el).transform")
        tile.hover()
        page.wait_for_timeout(250)
        after = tile.evaluate("el => getComputedStyle(el).transform")
        assert page.evaluate("matchMedia('(hover: hover) and (pointer: fine)').matches") is not touch
        if touch:
            assert after == before == "none"
        else:
            assert after != before and after == "matrix(1, 0, 0, 1, 0, -2)"
    finally:
        context.close()
