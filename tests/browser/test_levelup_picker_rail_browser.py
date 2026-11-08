"""UI-18: the level-up gains rail must not cover the open choice picker.

The rail sits above the picker overlay so its GM override switch stays usable while a
picker is open. It used to paint its whole box there, over the search field and over
the first rows of the list, hiding the reason a locked row cannot be picked.
"""
import pytest

from test_ds_focus_browser import _focus_server

pytestmark = pytest.mark.browser

ROUTE = "/player/levelup/Kyle"


@pytest.fixture(scope="module")
def levelup_site(tmp_path_factory):
    with _focus_server(tmp_path_factory, "pf2e") as site:
        yield site["url"]


def _levelup_page(browser, url, width, height, motion="reduce"):
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion=motion)
    page = context.new_page()
    page.set_default_timeout(12000)
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    assert page.goto(url + ROUTE).status == 200
    page.wait_for_function("typeof openModal === 'function' && typeof renderModalList === 'function'")
    return context, page


SHOWN = "!document.getElementById('uni-modal').classList.contains('opacity-0')"
SETTLED = "document.getAnimations().every(a => a.playState !== 'running')"


# The short phone leaves the picker window no spare height, so only the reserved band holds the switch.
@pytest.fixture(params=[(390, 844), (390, 600), (1440, 900)], ids=["phone", "short-phone", "desktop"])
def picker(browser, levelup_site, request):
    context, page = _levelup_page(browser, levelup_site, *request.param)
    page.evaluate("openModal('general_feat')")
    page.locator("#uni-modal").wait_for(state="visible")
    page.wait_for_function("document.body.classList.contains('picker-open')")
    page.wait_for_function(SHOWN)
    # One locked option, so it renders as the list's first row, the spot the rail used to cover.
    # The eligibility check, renderer and click handler are the page's own.
    page.evaluate("""() => {
        window.ignorePrereqs = false;
        mCtx.activeTabs = false;
        mCtx.tab = 'all';
        mCtx.data = [{name: 'Locked fixture', level: 99, desc: 'Fixture description'}];
        renderModalList();
    }""")
    page.wait_for_function(SETTLED)
    yield page
    context.close()


# Hit testing follows paint order, but skips pointer-events:none, which is how the rail
# used to hide from it. Lift that only while sampling, so the topmost painted box answers.
TOPMOST_SHARE = """([sel, rounded]) => {
    const lift = document.createElement('style');
    lift.textContent = '* { pointer-events: auto !important; }';
    document.head.appendChild(lift);
    try {
        const el = document.querySelector(sel);
        const r = el.getBoundingClientRect();
        // A pill's rounded ends are outside its painted box, so skip them.
        const inset = rounded ? r.height / 2 : 1;
        let own = 0, all = 0;
        const covers = new Set();
        for (let x = r.left + inset; x < r.right - inset; x += 2) {
            for (let y = r.top + 1; y < r.bottom - 1; y += 2) {
                all++;
                const top = document.elementFromPoint(x, y);
                if (top && (top === el || el.contains(top))) own++;
                else if (top) covers.add((top.closest('[id]') || top).id || top.tagName);
            }
        }
        return {share: all ? own / all : 0, covers: Array.from(covers)};
    } finally {
        lift.remove();
    }
}"""


def _rect(page, selector):
    return page.locator(selector).evaluate(
        "el => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }")


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


ROW = "#mod-list button.cp-opt-locked"
CHIP = ROW + " .cp-reason-chip"


def test_the_locked_reason_and_search_are_not_covered_while_the_picker_is_open(picker):
    assert picker.locator(ROW).count() == 1
    assert picker.locator(CHIP).inner_text().strip()
    for selector in (CHIP, ROW, "#mod-search"):
        seen = picker.evaluate(TOPMOST_SHARE, [selector, False])
        assert seen["share"] == 1, (selector, seen)


def test_the_override_switch_stays_usable_beside_the_picker_window(picker):
    switch = picker.locator("#gm-override-switch")
    assert switch.is_visible()
    box = _rect(picker, "#gm-override-switch")
    assert not _overlaps(box, _rect(picker, "#uni-window")), box
    width, height = picker.viewport_size["width"], picker.viewport_size["height"]
    assert box[0] >= 0 and box[1] >= 0 and box[2] <= width and box[3] <= height, box
    seen = picker.evaluate(TOPMOST_SHARE, ["#gm-override-switch", True])
    assert seen["share"] == 1, seen
    # The real switch and its handler: the locked row becomes selectable in place.
    # Only the fill: the inline style on the label already beats .armed's colour and border.
    look = "el => getComputedStyle(el).backgroundColor"
    unarmed = switch.evaluate(look)
    # Off the rail, over the dark overlay, the unarmed pill needs a solid fill to read.
    assert unarmed.startswith("rgb("), unarmed
    switch.click()
    assert picker.evaluate("window.ignorePrereqs") is True
    assert picker.locator("#mod-list button.cp-opt-override").count() == 1
    # Armed must still look armed while it sits in the overlay.
    switch.locator("xpath=self::*[contains(@class, 'armed')]").wait_for()
    picker.wait_for_function("document.getAnimations().every(a => a.playState !== 'running')")
    assert switch.evaluate(look) == "rgba(245, 158, 11, 0.14)", unarmed
    switch.click()
    assert picker.evaluate("window.ignorePrereqs") is False
    assert picker.locator(ROW).count() == 1


def test_keyboard_focus_on_a_locked_row_is_visible_and_enter_does_not_select(picker):
    picker.locator("#mod-search").focus()
    for _ in range(12):
        picker.keyboard.press("Tab")
        if picker.evaluate("document.activeElement.matches('#mod-list button')"):
            break
    row = picker.locator(ROW)
    assert row.evaluate("el => el === document.activeElement && el.matches(':focus-visible')")
    ring = row.evaluate("""el => { const s = getComputedStyle(el);
        return {style: s.outlineStyle, width: parseFloat(s.outlineWidth), color: s.outlineColor}; }""")
    assert ring["style"] not in {"none", "hidden"} and ring["width"] >= 2, ring
    assert ring["color"] not in {"transparent", "rgba(0, 0, 0, 0)"}, ring
    assert picker.evaluate(TOPMOST_SHARE, [ROW, False])["share"] == 1
    picker.keyboard.press("Enter")
    assert picker.evaluate("mCtx.active") is None
    assert picker.evaluate(TOPMOST_SHARE, [CHIP, False])["share"] == 1


def test_the_rail_comes_back_when_the_picker_closes(picker):
    picker.evaluate("closeModal()")
    picker.wait_for_function("!document.body.classList.contains('picker-open')")
    picker.locator("#uni-modal").wait_for(state="hidden")
    assert picker.locator("#gains-rail").is_visible()
    assert picker.locator("#gm-override-switch").evaluate("el => getComputedStyle(el).position") == "static"
    rail, switch = _rect(picker, "#gains-rail"), _rect(picker, "#gm-override-switch")
    assert rail[0] <= switch[0] and switch[2] <= rail[2] and rail[1] <= switch[1] and switch[3] <= rail[3]


# What the page looks like at one instant, read in the same task as the call before it, so
# neither the picker's 10ms open timer nor its 200ms close timer can have run yet.
RAIL_NOW = """() => {
    const rail = document.getElementById('gains-rail'), sw = document.getElementById('gm-override-switch');
    const r = rail.getBoundingClientRect(), s = sw.getBoundingClientRect();
    return {overlayHidden: document.getElementById('uni-modal').classList.contains('hidden'),
            railVisibility: getComputedStyle(rail).visibility,
            switchPosition: getComputedStyle(sw).position,
            switchInRail: r.left <= s.left && s.right <= r.right && r.top <= s.top && s.bottom <= r.bottom};
}"""


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_the_rail_stays_in_place_while_the_overlay_is_still_transparent(browser, levelup_site, motion):
    """The overlay fades in and out around a hidden-class change; the rail must not leave a hole meanwhile."""
    context, page = _levelup_page(browser, levelup_site, 1440, 900, motion)
    try:
        # Opening: picker-open is set at once, but the overlay is still transparent.
        opening = page.evaluate("() => { openModal('general_feat'); return (" + RAIL_NOW + ")(); }")
        assert opening == {"overlayHidden": False, "railVisibility": "visible",
                           "switchPosition": "static", "switchInRail": True}, opening
        page.wait_for_function(SHOWN)
        # Not SETTLED: with motion allowed the page has unrelated looping animations.
        page.wait_for_function("getComputedStyle(document.getElementById('gm-override-switch')).position === 'fixed'")
        page.wait_for_function("getComputedStyle(document.getElementById('gains-rail')).visibility === 'hidden'")
        # Closing: the overlay starts fading out at once and is only hidden 200ms later.
        closing = page.evaluate("() => { closeModal(); return (" + RAIL_NOW + ")(); }")
        assert closing == {"overlayHidden": False, "railVisibility": "visible",
                           "switchPosition": "static", "switchInRail": True}, closing
    finally:
        context.close()
