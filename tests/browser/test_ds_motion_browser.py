"""DS-02 in a real browser: the motion classes, the reduced-motion block and the audit."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import functools
import http.server
from pathlib import Path
import threading

import pytest

from tools import ui_screens
from tools.ui_screens import MOTION_AUDIT_JS, MOTION_RECORDER_JS, motion_problems

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
CSS = "".join((ROOT / "static" / "css" / name).read_text(encoding="utf-8") for name in ("ds-tokens.css", "ds.css"))
BODY = """
<button id=press class=ds-press>Press</button>
<a id=link class=ds-press href=#z>Link</a>
<div id=enter class=ds-enter>Enter</div>
<div id=panel class=ds-panel>Panel</div>
<button id=closed aria-expanded=false>Closed</button>
<div id=collapse-closed class=ds-collapse><div><a id=hidden-link href=#x>Hidden</a></div></div>
<button id=opened aria-expanded=true><span id=chev class=ds-chevron style="width:20px;height:4px"></span>Open</button>
<div id=collapse-open class=ds-collapse><div style="position:relative">
  <a id=shown-link href=#y>Shown</a>
  <div id=menu style="position:absolute;top:100%;left:0;width:100px;height:40px;background:#fff">Menu</div>
</div></div>
<div style="height:60px"></div>
<details id=details class=ds-details open><summary>More <span id=dchev class=ds-chevron style="width:20px;height:4px"></span></summary><p>Inside</p></details>
<div id=skeleton class=ds-skeleton></div>
<div id=skeleton-hidden class=ds-skeleton hidden></div>
<button aria-expanded=true>x</button><div id=collapse-hidden class=ds-collapse hidden><div>y</div></div>
<span id=shimmer class=ds-shimmer>Rolling...</span>
<style>
@keyframes legacy-loop { to { margin-left: 4px; } }
#legacy { animation: legacy-loop 1s linear infinite; }
@keyframes gen-pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 0.30; } }
.gen-content.rerolling { animation: gen-pulse 0.7s ease-in-out infinite; }
</style>
<div id=legacy>Legacy</div>
<div id=gen class="gen-content rerolling">Rerolling</div>
"""


def _open(browser, body=BODY, **options):
    context = browser.new_context(viewport={"width": 800, "height": 900}, **options)
    tab = context.new_page()
    tab.add_init_script(MOTION_RECORDER_JS)
    tab.goto("about:blank")
    tab.set_content("<!doctype html><html><head><style>" + CSS + "</style></head><body>" + body + "</body></html>")
    return context, tab


@pytest.fixture(params=["no-preference", "reduce"])
def page(browser, request):
    context, tab = _open(browser, reduced_motion=request.param)
    tab.reduced = request.param == "reduce"
    yield tab
    context.close()


def _style(page, selector, prop, pseudo=None):
    return page.evaluate("([s, p, e]) => getComputedStyle(document.querySelector(s), e)[p]", [selector, prop, pseudo])


def _settle(page):
    """Wait for everything finite to finish; a fixed sleep flakes on a busy machine."""
    page.wait_for_function("""document.getAnimations().every(a =>
        a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)""")


def _box(page, selector):
    return page.locator(selector).bounding_box()


def test_entrance_classes_are_wired_and_end_in_place(page):
    assert _style(page, "#enter", "animationName") == "ds-fade-rise"
    assert _style(page, "#panel", "animationName") == "ds-panel-swap"
    assert _style(page, "#enter", "animationDuration") == ("1e-05s" if page.reduced else "0.22s")
    _settle(page)
    for selector in ("#enter", "#panel"):
        assert _style(page, selector, "opacity") == "1"
        # `none`, not an identity matrix: a held transform would trap fixed children.
        assert _style(page, selector, "transform") == "none"


def test_press_scales_unless_reduced(page):
    box = _box(page, "#press")
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    _settle(page)
    transform = _style(page, "#press", "transform")
    page.mouse.up()
    assert transform == ("matrix(1, 0, 0, 1, 0, 0)" if page.reduced else "matrix(0.97, 0, 0, 0.97, 0, 0)")


def test_press_works_on_a_link(browser):
    context, tab = _open(browser)
    at_rest = _box(tab, "#link")
    tab.mouse.move(at_rest["x"] + 5, at_rest["y"] + 5)
    tab.mouse.down()
    _settle(tab)
    pressed = _box(tab, "#link")
    tab.mouse.up()
    context.close()
    assert pressed["width"] < at_rest["width"]


def test_closed_disclosure_cannot_take_focus(page):
    _settle(page)
    assert _style(page, "#collapse-closed", "visibility") == "hidden"
    assert _style(page, "#collapse-open", "visibility") == "visible"
    assert _box(page, "#collapse-closed")["height"] == 0
    assert _box(page, "#collapse-open")["height"] > 0
    page.focus("#closed")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "opened"


def test_disclosure_opens_when_the_control_says_so(page):
    page.evaluate("document.querySelector('#closed').setAttribute('aria-expanded', 'true')")
    _settle(page)
    assert _style(page, "#collapse-closed", "visibility") == "visible"
    assert _box(page, "#collapse-closed")["height"] > 0
    assert _style(page, "#collapse-closed > *", "opacity") == "1"


def test_open_disclosure_does_not_clip_a_menu(page):
    _settle(page)
    box = _box(page, "#menu")
    top = page.evaluate("([x, y]) => document.elementFromPoint(x, y).id", [box["x"] + 5, box["y"] + box["height"] - 5])
    assert top == "menu"


def test_closing_still_clips(page):
    _settle(page)
    page.evaluate("document.querySelector('#opened').setAttribute('aria-expanded', 'false')")
    _settle(page)
    assert _style(page, "#collapse-open > *", "overflow") == "hidden"
    assert _style(page, "#collapse-open", "visibility") == "hidden"


def test_native_details_opens_and_closes(page):
    _settle(page)
    opened = _box(page, "#details")["height"]
    assert _style(page, "#details", "opacity", "::details-content") == "1"
    assert _style(page, "#details", "overflow", "::details-content") == "visible"
    page.evaluate("document.querySelector('#details').open = false")
    # Waits on the end state: getAnimations() does not list this pseudo-element.
    page.wait_for_function("""opened => {
        const details = document.querySelector('#details');
        return getComputedStyle(details, '::details-content').opacity === '0'
            && details.getBoundingClientRect().height < opened;
    }""", arg=opened, timeout=3000)


def test_reduced_motion_removes_height_and_angle_travel(page):
    expected = "0s" if page.reduced else "0.28s"
    assert _style(page, "#collapse-closed", "transitionDuration").split(",")[0] == expected
    assert _style(page, "#chev", "transitionDuration") == expected
    details = _style(page, "#details", "transitionProperty", "::details-content")
    assert ("height" in details) is (not page.reduced)
    # Fades keep their token duration either way (6.3).
    assert _style(page, "#collapse-open > *", "transitionDuration").split(",")[0] == "0.16s"


@pytest.mark.parametrize("chevron", ["#chev", "#dchev"])
def test_a_span_chevron_rotates(page, chevron):
    _settle(page)
    box = _box(page, chevron)
    # A 20x4 box turned on end. Measured exactly: an inline span has no such box.
    assert box["width"] == pytest.approx(4, abs=0.5)
    assert box["height"] == pytest.approx(20, abs=0.5)


def test_hidden_attribute_wins(page):
    for selector in ("#skeleton-hidden", "#collapse-hidden"):
        assert _style(page, selector, "display") == "none"


def test_reduced_motion_collapses_animations_and_keeps_text_readable(page):
    legacy = page.evaluate("document.querySelector('#legacy').getAnimations().length")
    if not page.reduced:
        assert legacy == 1
        assert motion_problems(page.evaluate(MOTION_AUDIT_JS)), "the audit must catch a real loop"
        assert _style(page, "#shimmer", "color") == "rgba(0, 0, 0, 0)"
        return
    page.wait_for_function("document.querySelector('#legacy').getAnimations().length === 0")
    assert _style(page, "#skeleton", "animationName") == "none"
    assert _style(page, "#shimmer", "animationName") == "none"
    assert _style(page, "#shimmer", "color") != "rgba(0, 0, 0, 0)"
    assert _style(page, "#shimmer", "backgroundImage") == "none"
    assert motion_problems(page.evaluate(MOTION_AUDIT_JS)) == []


def test_the_reroll_cue_keeps_a_static_form_under_reduce(page):
    """The page's own rule comes after ds.css, as on the real page."""
    if page.reduced:
        assert _style(page, "#gen", "animationName") == "none"
        assert _style(page, "#gen", "opacity") == "0.55"
    else:
        assert _style(page, "#gen", "animationName") == "gen-pulse"


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_locked_row_feedback_returns_to_baseline_on_the_real_builder(browser, pf2e_site, motion):
    """The picker leaves its classes attached, so nothing but the animation may change how they look."""
    context = browser.new_context(reduced_motion=motion)
    page = context.new_page()
    page.set_default_timeout(12000)
    try:
        pf2e_site.login(page)
        page.goto(pf2e_site.url + pf2e_site.builder)
        page.wait_for_function("typeof openModal === 'function' && typeof renderModalList === 'function'")
        # Known options for the page's own picker; the eligibility check, DOM and click handler are the real ones.
        page.evaluate("""() => {
            openModal('class');
            window.ignorePrereqs = false;
            state.level = 1;
            mCtx.activeTabs = false;
            mCtx.tab = 'all';
            mCtx.data = [{name: 'Locked A', level: 99}, {name: 'Locked B', level: 99}];
            renderModalList();
            document.addEventListener('animationstart', e => {
                (window.__started = window.__started || []).push(e.animationName);
            }, true);
        }""")
        rows = page.locator("#mod-list button.cp-opt-locked")
        assert rows.count() == 2
        # Every computed property of the row and its chip, bar the animation's own.
        look = """(row) => [row, row.querySelector('.cp-reason-chip')].map(el => {
            const style = getComputedStyle(el);
            return Object.fromEntries(Array.from(style)
                .filter(name => !name.startsWith('animation'))
                .map(name => [name, style.getPropertyValue(name)]));
        })"""

        def at_rest():
            page.mouse.move(0, 0)
            page.evaluate("document.activeElement.blur()")
            page.wait_for_function("document.getAnimations().every(a => a.playState !== 'running' || a.effect.getComputedTiming().iterations === Infinity)")
            return [rows.nth(i).evaluate(look) for i in range(2)]

        before = at_rest()
        # The second row twice: a repeat click restarts the feedback.
        for i in (0, 1, 1):
            rows.nth(i).click()
            rows.nth(i).locator(".cp-chip-flash").wait_for()
            page.wait_for_function("""row => row.getAnimations({subtree: true}).length === 0""",
                                   arg=rows.nth(i).element_handle())
        started = page.evaluate("window.__started")
        assert started.count("cp-shake") == 3 and started.count("cp-chip-flash") == 3, started
        assert at_rest() == before
        # The reason stays on the row, and a locked row never selects.
        assert rows.nth(0).locator(".cp-reason-chip").inner_text().strip()
        assert page.evaluate("mCtx.active") is None
    finally:
        context.close()


def test_forced_colours_keep_the_shimmer_readable(browser):
    context, tab = _open(browser, forced_colors="active")
    assert _style(tab, "#shimmer", "animationName") == "none"
    assert _style(tab, "#shimmer", "backgroundImage") == "none"
    assert _style(tab, "#shimmer", "color") != "rgba(0, 0, 0, 0)"
    context.close()


def test_reduced_animation_still_fires_animationend(browser):
    """Waiters like the tracker's flourish cleanup depend on this."""
    context, tab = _open(browser, "<div id=x>x</div><style>"
                         "@keyframes once { to { opacity: 0.5; } } .run { animation: once 5s; }</style>",
                         reduced_motion="reduce")
    ended = tab.evaluate("""() => new Promise(resolve => {
        const x = document.querySelector('#x');
        x.addEventListener('animationend', () => resolve(true));
        x.classList.add('run');
        setTimeout(() => resolve(false), 1000);
    })""")
    context.close()
    assert ended


# ── The `motion` command itself, end to end against pages served over HTTP ─────

LOOP = "<style>@keyframes s { to { opacity: 0.5; } } #x { animation: s 1s infinite !important; animation-iteration-count: infinite !important; }</style><div id=x>x</div>"
# Over before the 1500ms wait ends, so only the recorder can see it.
SHORT = "<style>@keyframes s { to { opacity: 0.5; } } #x { animation: s 400ms; animation-duration: 400ms !important; }</style><div id=x>x</div>"
PLAIN = "<style>@keyframes s { to { opacity: 0.5; } } #x { animation: s 1s infinite; }</style><div id=x>x</div>"


@pytest.fixture
def served(tmp_path, monkeypatch):
    ds = (ROOT / "static" / "css" / "ds.css").read_text(encoding="utf-8")
    for name, body in {"loop": LOOP, "short": SHORT, "plain": PLAIN}.items():
        (tmp_path / f"{name}.html").write_text(f"<!doctype html><style>{ds}</style>{body}", encoding="utf-8")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(tmp_path)))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    @contextlib.contextmanager
    def fake_served(repo, profile, routes=None):
        yield server.server_address[1], None, [("gm", r, r) for r in routes]

    monkeypatch.setattr(ui_screens, "_served", fake_served)
    yield
    server.shutdown()


def _command(*routes):
    """In a thread: Playwright's sync API refuses to start inside another one's loop."""
    with ThreadPoolExecutor(1) as pool:
        return pool.submit(ui_screens.main, ["motion", "--routes", *routes]).result()


def test_motion_command_passes_a_collapsed_page(served, capsys):
    assert _command("/plain.html") == 0
    assert "2 pages checked, 0 problems" in capsys.readouterr().out


def test_motion_command_fails_a_loop(served, capsys):
    assert _command("/loop.html") == 1
    assert "MOTION desktop /loop.html" in capsys.readouterr().out


def test_motion_command_fails_a_short_animation_at_full_length(served, capsys):
    assert _command("/short.html") == 1
    assert "1 x 400ms" in capsys.readouterr().out


def test_motion_command_fails_a_page_that_will_not_load(served, capsys):
    assert _command("/nope.html") == 1
    out = capsys.readouterr().out
    assert "did not load" in out and "0 pages checked" in out
