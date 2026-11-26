"""SE-03: the sheet edit fixes that only a real browser can show.

The Add Companion form sits directly in the sheet's ember host, whose CSS makes
every child position:relative; it has to stay a fixed overlay. "+ Add Pet" after
an Edit must start blank, and a restored feat must keep its level.
"""
import pytest

from test_ds_focus_browser import _focus_server

pytestmark = pytest.mark.browser

ROUTE = "/player/sheet/Kyle"


@pytest.fixture(scope="module")
def sheet_site(tmp_path_factory):
    with _focus_server(tmp_path_factory, "pf2e") as site:
        yield site["url"]


@pytest.fixture
def page(browser, sheet_site):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.set_default_timeout(12000)
    page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
    page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
    assert page.goto(sheet_site + ROUTE).status == 200
    page.wait_for_function("!!document.querySelector('[data-se-pet-edit]') && !!document.getElementById('se-modal')")
    yield page
    context.close()


def test_add_companion_form_is_an_overlay_and_starts_blank(page):
    page.evaluate("document.querySelector('[data-se-pet-edit]').click()")
    assert page.evaluate("document.getElementById('pet-name').value")       # Edit pre-fills
    page.evaluate("""() => [...document.querySelectorAll('button')]
        .find(b => b.textContent.trim() === '+ Add Pet').click()""")
    form = page.evaluate("""() => {
        const modal = document.getElementById('pet-add-modal');
        const box = modal.getBoundingClientRect();
        const hp = document.getElementById('pet-hp');
        return {position: getComputedStyle(modal).position, top: box.top, height: box.height,
                viewport: window.innerHeight, hidden: modal.classList.contains('hidden'),
                name: document.getElementById('pet-name').value, hpReset: hp.value === hp.defaultValue};
    }""")
    assert not form["hidden"]
    assert form["position"] == "fixed"
    assert form["top"] == 0 and form["height"] >= form["viewport"] - 20    # covers the window
    assert form["name"] == "" and form["hpReset"]


def test_restored_feat_keeps_its_level(page):
    result = page.evaluate("""() => {
        const dialog = document.getElementById('se-modal');
        const row = [...dialog.querySelectorAll('[data-se-feat-list] [data-se-source="build"]')]
            .find(r => r.getAttribute('data-se-level') !== '1');
        const before = {name: row.getAttribute('data-se-feat'), level: row.getAttribute('data-se-level'),
                        type: row.getAttribute('data-se-type')};
        row.querySelector('[data-se-drop]').click();
        const removed = [...dialog.querySelectorAll('[data-se-removed-list] [data-se-removed-feat]')]
            .find(r => r.getAttribute('data-se-removed-feat') === before.name);
        removed.querySelector('[data-se-restore]').click();
        const back = [...dialog.querySelectorAll('[data-se-feat]')]
            .find(r => r.getAttribute('data-se-feat') === before.name);
        return {before, level: back.getAttribute('data-se-level'), type: back.getAttribute('data-se-type')};
    }""")
    assert result["level"] == result["before"]["level"]
    assert result["type"] == result["before"]["type"]
