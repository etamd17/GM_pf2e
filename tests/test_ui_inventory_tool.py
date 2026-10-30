"""DS-00: the inventory diff must fail on every change a design pass may not make."""
from tools.ui_inventory import diff_inventories, inventory_html, main

BASE = """<html><body class="wide account-home">
<nav aria-label="Main" class="topnav"><a href="/gm" class="nav-link">GM</a></nav>
<div id="hp" data-pc="Kyle" class="ability-chip">10</div>
<div class="ability-chip">2</div>
<button id="go" onclick="doThing('a')" role="tab" aria-selected="true" class="tab-btn">Go</button>
<form action="/api/save" method="post" id="f">
  <input name="hp" type="number" min="0">
  <select name="mode"><option value="a" selected>A</option><option value="b">B</option></select>
</form>
<p>Lock In</p>
<script>var ignored = "Lock In";</script>
</body></html>"""


def _capture(html, hooks=None):
    return {"mode": "html", "routes": {"/x": {"status": 200, **inventory_html(html, hooks)}}}


def _diff(after_html, hooks=None):
    return diff_inventories(_capture(BASE, hooks), _capture(after_html, hooks))


def test_identical_pages_pass():
    assert _diff(BASE) == ([], [])


def test_restyling_with_a_new_class_on_a_plain_element_is_only_an_addition():
    failures, additions = _diff(BASE.replace('class="ability-chip">2', 'class="ability-chip ds-card">2'))
    assert failures == []
    assert any("classes added x1: ds-card" in a for a in additions)


def test_removed_id_fails():
    failures, _ = _diff(BASE.replace('id="hp" ', ''))
    assert any("ids removed x1: hp" in f for f in failures)


def test_changed_data_value_fails():
    failures, _ = _diff(BASE.replace('data-pc="Kyle"', 'data-pc="Goel"'))
    assert any("data-pc=Kyle" in f for f in failures)


def test_class_added_to_an_aria_element_fails():
    failures, _ = _diff(BASE.replace('class="tab-btn"', 'class="tab-btn ds-btn"'))
    assert any(f.startswith("/x: aria removed") for f in failures)


def test_body_class_change_fails():
    failures, _ = _diff(BASE.replace('class="wide account-home"', 'class="wide account-home ds"'))
    assert any("<body> class" in f for f in failures)


def test_handler_and_form_changes_fail():
    failures, _ = _diff(BASE.replace("doThing('a')", "doThing('b')"))
    assert any("handlers removed" in f for f in failures)
    failures, _ = _diff(BASE.replace('<option value="b">B</option>', ''))
    assert any("forms removed" in f for f in failures)


def test_counted_class_losing_an_instance_fails():
    failures, _ = _diff(BASE.replace('<div class="ability-chip">2</div>', '<div>2</div>'))
    assert any("classes removed x1: ability-chip" in f for f in failures)


def test_copy_hook_counts_visible_text_only():
    inv = inventory_html(BASE, ["Lock In"])
    assert inv["text_hooks"] == {"Lock In": 1}
    failures, _ = _diff(BASE.replace("<p>Lock In</p>", "<p>Lock it in</p>"), ["Lock In"])
    assert any("text_hooks removed" in f for f in failures)


def test_status_change_and_missing_route_fail():
    before = _capture(BASE)
    after = {"mode": "html", "routes": {"/x": {"status": 500}}}
    assert diff_inventories(before, after)[0] == ["/x: status 200 -> 500"]
    assert diff_inventories(before, {"mode": "html", "routes": {}})[0] == [
        "/x: route missing from the after capture"]


def test_cli_diff_exit_codes(tmp_path):
    import json
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(_capture(BASE)), encoding="utf-8")
    a.write_text(json.dumps(_capture(BASE)), encoding="utf-8")
    assert main(["diff", str(b), str(a)]) == 0
    a.write_text(json.dumps(_capture(BASE.replace('id="go" ', ''))), encoding="utf-8")
    assert main(["diff", str(b), str(a)]) == 1
    a.write_text(json.dumps({"mode": "dom", "routes": {}}), encoding="utf-8")
    assert main(["diff", str(b), str(a)]) == 2


# Review findings (second pass): each change below broke behaviour while the
# first version of the diff passed.
PAGE = """<html><body class="p">
<div id="a"><button onclick="one()">1</button></div>
<div id="b"><button onclick="two()">2</button></div>
<div id="adv" style="display:none">x</div>
<details id="d" open><summary>s</summary></details>
<p id="msg" hidden>m</p>
<input name="loot-mode" type="radio" value="split">
<div role="tablist"><button id="t1" role="tab">A</button><button id="t2" role="tab">B</button></div>
<form action="/up" method="post" enctype="multipart/form-data">
  <input name="_csrf" type="hidden" value="TOKEN-ONE">
  <select name="kind"><option>equipment</option><option>weapon</option></select>
  <textarea name="note">keep</textarea>
</form>
<script src="/static/js/a.js?v=1"></script><script src="/static/js/b.js?v=1"></script>
<script>window.boot = 1;</script>
</body></html>"""


def _pdiff(after_html):
    return diff_inventories(_capture(PAGE), _capture(after_html))


def test_handlers_swapped_between_containers_fail():
    swapped = PAGE.replace("one()", "TMP").replace("two()", "one()").replace("TMP", "two()")
    assert _pdiff(swapped)[0]


def test_script_order_and_inline_script_edits_fail():
    reordered = PAGE.replace('<script src="/static/js/a.js?v=1"></script><script src="/static/js/b.js?v=1"></script>',
                             '<script src="/static/js/b.js?v=1"></script><script src="/static/js/a.js?v=1"></script>')
    assert any("script_order" in f for f in _pdiff(reordered)[0])
    assert any("script_order" in f for f in _pdiff(PAGE.replace("window.boot = 1", "window.boot = 2"))[0])
    assert _pdiff(PAGE.replace("?v=1", "?v=2")) == ([], [])


def test_duplicated_id_fails():
    assert any("ids duplicated: a" in f for f in _pdiff(PAGE.replace('<p id="msg"', '<span id="a"></span><p id="msg"'))[0])


def test_inline_display_hidden_and_open_are_frozen():
    assert _pdiff(PAGE.replace('style="display:none"', 'style="display:flex"'))[0]
    assert _pdiff(PAGE.replace('<p id="msg" hidden>', '<p id="msg">'))[0]
    assert _pdiff(PAGE.replace('<details id="d" open>', '<details id="d">'))[0]


def test_loose_controls_options_text_textarea_and_enctype_are_frozen():
    assert _pdiff(PAGE.replace('name="loot-mode"', 'name="loot_mode"'))[0]
    assert _pdiff(PAGE.replace("<option>equipment</option>", "<option>gear</option>"))[0]
    assert _pdiff(PAGE.replace(">keep</textarea>", ">changed</textarea>"))[0]
    assert _pdiff(PAGE.replace(' enctype="multipart/form-data"', ''))[0]


def test_tab_order_is_frozen():
    swapped = PAGE.replace('<button id="t1" role="tab">A</button><button id="t2" role="tab">B</button>',
                           '<button id="t2" role="tab">B</button><button id="t1" role="tab">A</button>')
    assert any("tab_order" in f for f in _pdiff(swapped)[0])


def test_csrf_token_and_split_copy_do_not_cause_false_failures():
    assert _pdiff(PAGE.replace("TOKEN-ONE", "TOKEN-TWO")) == ([], [])
    html = BASE.replace("<p>Lock In</p>", "<p>Lo<b>ck</b> In</p>")
    assert inventory_html(html, ["Lock In"])["text_hooks"] == {"Lock In": 1}


def test_unclosed_form_is_still_recorded():
    inv = inventory_html('<form action="/x" method="post"><input name="q">')
    assert any("/x" in k for k in inv["forms"])


class _Resp:
    def __init__(self, html, status=200):
        self.status_code, self.content_type, self._html = status, "text/html; charset=utf-8", html
        self.headers = {}

    def get_data(self, as_text=True):
        return self._html


def test_generated_ids_are_normalised_only_in_account_profiles():
    from tools.ui_inventory import _record
    a = '<a href="/campaign/' + "a" * 32 + '/invites">x</a>'
    b = '<a href="/campaign/' + "b" * 32 + '/invites">x</a>'
    assert _record(_Resp(a), {}, "/me", {}) == _record(_Resp(b), {}, "/me", {})
    assert _record(_Resp(a), {}, "/me") != _record(_Resp(b), {}, "/me")
    assert _record(_Resp(a, 302), {}, "/me") == {"status": 302}
    # The seeded campaign and user ids keep separate placeholders, so a template
    # that puts the user id where the campaign id belongs still fails.
    cid, uid = "c" * 32, "d" * 32
    good = '<a href="/campaign/' + cid + '/invites">x</a>'
    wrong = '<a href="/campaign/' + uid + '/invites">x</a>'
    ids = {cid: "{cid}", uid: "{uid}"}
    assert _record(_Resp(good), {}, "/me", ids) != _record(_Resp(wrong), {}, "/me", ids)


def test_account_profile_renders_every_route(tmp_path):
    """The account flow (setup, new campaign, activate) still reaches every page."""
    import json
    import subprocess
    import sys
    from pathlib import Path
    out = tmp_path / "account.json"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "tools/ui_inventory.py", "capture", "--profile", "account",
                             "--out", str(out)], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", timeout=600)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    routes = json.loads(out.read_text(encoding="utf-8"))["routes"]
    assert len(routes) == 16 and {e["status"] for e in routes.values()} == {200}


def test_import_inventories_prerendered_states(tmp_path):
    import json
    states = tmp_path / "states.json"
    states.write_text(json.dumps({"workspace": '<nav aria-label="Map tools" class="vtt-rail"></nav>'}),
                      encoding="utf-8")
    out = tmp_path / "cap.json"
    assert main(["import", str(states), "--out", str(out)]) == 0
    cap = json.loads(out.read_text(encoding="utf-8"))
    assert cap["mode"] == "import" and cap["routes"]["workspace"]["status"] == 200
    assert any("Map tools" in k for k in cap["routes"]["workspace"]["aria"])


def test_react_ids_are_numbered_by_appearance():
    from tools.ui_inventory import _number_react_ids
    a = '<div id="_r_0_-tab" aria-controls="_r_0_-panel"></div><p id="_r_1_"></p>'
    b = '<div id="_r_4_-tab" aria-controls="_r_4_-panel"></div><p id="_r_9_"></p>'
    assert _number_react_ids(a) == _number_react_ids(b)
    swapped = '<div id="_r_1_-tab" aria-controls="_r_0_-panel"></div><p id="_r_1_"></p>'
    assert _number_react_ids(a) != _number_react_ids(swapped)


def test_deploy_token_inside_an_inline_script_is_not_a_script_edit():
    """The three.js import map embeds /static/... ?v=<deploy>, which differs
    between two checkouts (found in DS-01). Only the token is ignored."""
    def page(token, extra=""):
        return ('<html><body><script type="importmap">{"imports": {"three": '
                f'"/static/js/vendor/three.module.js?v={token}"}}}}{extra}</script></body></html>')
    same = diff_inventories(_capture(page("1793330195")), _capture(page("1793215527")))
    assert same[0] == []
    edited = diff_inventories(_capture(page("1793330195")), _capture(page("1793215527", " ")))
    assert edited[0] == []                       # whitespace at the end is stripped
    changed = diff_inventories(_capture(page("1")), _capture(page("1", ', "x": "/y"')))
    assert changed[0] and "script_order" in changed[0][0]


# Codex review of DS-00: each of these changed behaviour with no inventory difference.
LOOSE = """<html><body class="p">
<select id="month"><option value="10" selected>October</option><option value="12">December</option></select>
<textarea id="note">keep</textarea>
<input id="kind" list="kinds"><datalist id="kinds"><option value="weapon"></datalist>
<a id="save" href="/save">Save</a><a id="delete" href="/delete">Delete</a>
</body></html>"""


def _ldiff(after_html):
    return diff_inventories(_capture(LOOSE), _capture(after_html))


def test_loose_select_options_and_selection_are_frozen():
    assert _ldiff(LOOSE) == ([], [])
    assert any("controls removed" in f and "[10*,12]" in f for f in _ldiff(LOOSE.replace('value="10"', 'value="11"'))[0])
    assert _ldiff(LOOSE.replace(' selected>', '>'))[0]
    assert _ldiff(LOOSE.replace('value="12">', 'value="12" disabled>'))[0]
    assert _ldiff(LOOSE.replace('<option value="12">December</option>', ''))[0]


def test_loose_textarea_text_is_frozen():
    failures, _ = _ldiff(LOOSE.replace(">keep<", ">erased<"))
    assert any("controls removed" in f and "text=keep" in f for f in failures)


def test_datalist_options_are_frozen():
    assert _ldiff(LOOSE.replace('value="weapon"', 'value="armor"'))[0]


def test_payload_in_a_form_is_still_on_the_form_and_counts_once_as_a_control():
    html = '<form action="/x"><select id="s" name="n"><option value="a">A</select><textarea name="t">x</textarea></form>'
    inv = inventory_html(html)
    assert list(inv["controls"].values()) == [1, 1]
    assert any("[a]" in k for k in inv["controls"]) and any("text=x" in k for k in inv["controls"])
    form = next(iter(inv["forms"]))
    assert "[a]" in form and "text=x" in form


def test_unclosed_options_and_selects_are_recorded():
    inv = inventory_html('<select id="a"><option value="1"><option value="2" selected><select id="b"><option value="3">')
    assert sorted(inv["controls"]) == ["#a select  [1,2*]", "#b select  [3]"]


def test_links_swapped_between_identified_elements_fail():
    swapped = LOOSE.replace("/save", "/TMP").replace("/delete", "/save").replace("/TMP", "/delete")
    failures, _ = _ldiff(swapped)
    assert any("links removed" in f and "#save a href=/save" in f for f in failures)
    assert any("links removed" in f and "#delete a href=/delete" in f for f in failures)


def test_links_swapped_between_containers_fail():
    page = '<div id="a"><a href="/one">1</a></div><div id="b"><a href="/two">2</a></div>'
    swapped = page.replace("/one", "/TMP").replace("/two", "/one").replace("/TMP", "/two")
    assert diff_inventories(_capture(page), _capture(swapped))[0]


def test_changed_redirect_target_fails():
    def route(target):
        return {"mode": "html", "routes": {"/x": {"status": 302, "redirected_to": target}}}
    assert diff_inventories(route("/a"), route("/a")) == ([], [])
    assert diff_inventories(route("/a"), route("/login"))[0] == ["/x: redirect /a -> /login"]


def test_html_mode_records_the_redirect_target_with_ids_normalised():
    from tools.ui_inventory import _record
    resp = _Resp("", 302)
    resp.headers = {"Location": "/campaign/" + "c" * 32 + "/x"}
    assert _record(resp, {}, "/me", {"c" * 32: "{cid}"}) == {"status": 302, "redirected_to": "/campaign/{cid}/x"}


class _Tab:
    def __init__(self, page, i):
        self.page, self.i = page, i

    def is_visible(self):
        return True

    def get_attribute(self, _name):
        return f"t{self.i}"

    def click(self):
        self.page.url = self.page.after_click.get(self.i, self.page.url)


class _Page:
    """The part of Playwright capture_dom touches: `final_urls` maps the nth load to where it ended."""

    def __init__(self, final_urls, tabs=0, after_click=None, status=200):
        self.final_urls, self.tabs, self.after_click, self.status = final_urls, tabs, after_click or {}, status
        self.url, self.loads = "", 0

    def goto(self, url, wait_until=None):
        self.url = self.final_urls.get(self.loads, self.final_urls.get("*", url))
        self.loads += 1
        return type("R", (), {"status": self.status})()

    def wait_for_timeout(self, _ms):
        pass

    def content(self):
        return '<html><body class="login"><form action="/login" method="post"></form></body></html>'

    def locator(self, _selector):
        page = self
        return type("L", (), {"count": lambda s: page.tabs, "nth": lambda s, i: _Tab(page, i)})()


def _stub_playwright(monkeypatch, page):
    import sys
    import types

    class Engine:
        chromium = types.SimpleNamespace(launch=lambda **_k: types.SimpleNamespace(
            new_page=lambda **_k: page, close=lambda: None))

    class Ctx:
        def __enter__(self):
            return Engine()

        def __exit__(self, *_a):
            return False

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = Ctx
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)


def _dom(monkeypatch, tmp_path, page, *args):
    import json
    _stub_playwright(monkeypatch, page)
    out = tmp_path / "dom.json"
    code = main(["capture", "--mode", "dom", "--base-url", "http://127.0.0.1:5057", "--out", str(out), *args])
    return code, json.loads(out.read_text(encoding="utf-8"))["routes"]


def test_dom_capture_refuses_a_login_bounce(monkeypatch, tmp_path, capsys):
    page = _Page({"*": "http://127.0.0.1:5057/login?next=/gm"})
    code, routes = _dom(monkeypatch, tmp_path, page, "--routes", "/gm")
    assert code == 1
    assert routes == {"/gm": {"status": 302, "redirected_to": "/login?next=/gm"}}
    assert "NOT CHECKED" in capsys.readouterr().out


def test_dom_capture_refuses_the_legacy_login_bounce_too(monkeypatch, tmp_path):
    code, routes = _dom(monkeypatch, tmp_path, _Page({"*": "http://127.0.0.1:5057/gm/login"}), "--routes", "/gm")
    assert code == 1 and routes["/gm"]["redirected_to"] == "/gm/login"


def test_dom_capture_still_allows_the_login_page_itself(monkeypatch, tmp_path):
    code, routes = _dom(monkeypatch, tmp_path, _Page({}), "--routes", "/login")
    assert code == 0 and routes["/login"]["status"] == 200 and routes["/login"]["body_class"] == "login"


def test_dom_capture_ignores_query_fragment_and_trailing_slash(monkeypatch, tmp_path):
    code, routes = _dom(monkeypatch, tmp_path, _Page({"*": "http://127.0.0.1:5057/gm/?x=1#top"}), "--routes", "/gm")
    assert code == 0 and routes["/gm"]["status"] == 200


def test_dom_capture_refuses_a_bounce_on_a_tab_reload(monkeypatch, tmp_path):
    good = "http://127.0.0.1:5057/gm"
    page = _Page({0: good, 1: good, 2: "http://127.0.0.1:5057/login"}, tabs=2)
    code, routes = _dom(monkeypatch, tmp_path, page, "--routes", "/gm")
    assert code == 1
    assert routes["/gm"]["status"] == 200 and routes["/gm#tab0:t0"]["status"] == 200
    assert routes["/gm#tab1"] == {"status": 302, "redirected_to": "/login"}


def test_dom_capture_refuses_a_tab_click_that_leaves_the_page(monkeypatch, tmp_path):
    page = _Page({}, tabs=1, after_click={0: "http://127.0.0.1:5057/login"})
    code, routes = _dom(monkeypatch, tmp_path, page, "--routes", "/gm")
    assert code == 1 and routes["/gm#tab0:t0"] == {"status": 302, "redirected_to": "/login"}


def test_allow_status_can_still_expect_a_dom_redirect(monkeypatch, tmp_path):
    page = _Page({"*": "http://127.0.0.1:5057/gm"})
    code, routes = _dom(monkeypatch, tmp_path, page, "--routes", "/gm/rest", "--allow-status", "/gm/rest=302")
    assert code == 0 and routes["/gm/rest"] == {"status": 302, "redirected_to": "/gm"}


def test_commit_stamp_in_the_sse_hub_is_not_a_script_edit():
    """_sse_hub.html writes the running commit into an inline script, so any two
    commits differed (found rerunning DS-01). Only that one value is ignored."""
    def page(stamp, rest="var a = 1;"):
        return f'<html><body><script>var LOADED_VERSION = "{stamp}"; {rest}</script></body></html>'
    assert diff_inventories(_capture(page("cb5a29f5")), _capture(page("ec32f01a"))) == ([], [])
    assert diff_inventories(_capture(page("cb5a29f5")), _capture(page("ec32f01a", "var a = 2;")))[0]
    other = '<html><body><script>var OTHER_VERSION = "{}";</script></body></html>'
    assert diff_inventories(_capture(other.format("a")), _capture(other.format("b")))[0]


# Independent review of the fixes above.
def test_disabled_fieldset_is_frozen_both_ways():
    plain = '<form action="/a"><fieldset><input name="a"></fieldset></form>'
    off = plain.replace("<fieldset>", "<fieldset disabled>")
    failures, _ = diff_inventories(_capture(plain), _capture(off))
    assert any("state added" in f and "fieldset disabled" in f for f in failures)
    failures, _ = diff_inventories(_capture(off), _capture(plain))
    assert any("state removed" in f and "fieldset disabled" in f for f in failures)
    assert diff_inventories(_capture(off), _capture(off)) == ([], [])
    # Other new state stays an addition, as it always was.
    failures, additions = diff_inventories(_capture(plain), _capture(plain.replace("<input", "<p hidden></p><input")))
    assert failures == [] and any("state added" in a for a in additions)


def test_diff_refuses_captures_that_recorded_unchecked_routes(monkeypatch, tmp_path, capsys):
    bounced = _Page({"*": "http://127.0.0.1:5057/login"})
    assert _dom(monkeypatch, tmp_path, bounced, "--routes", "/gm")[0] == 1
    first = tmp_path / "first.json"
    first.write_text((tmp_path / "dom.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert _dom(monkeypatch, tmp_path, bounced, "--routes", "/gm")[0] == 1
    assert main(["diff", str(first), str(tmp_path / "dom.json")]) == 2
    assert "NOT CHECKED, recapture first: /gm=302 -> /login" in capsys.readouterr().out


def test_diff_accepts_a_capture_whose_exception_was_declared(monkeypatch, tmp_path):
    page = _Page({"*": "http://127.0.0.1:5057/gm"})
    args = ("--routes", "/gm/rest", "--allow-status", "/gm/rest=302")
    assert _dom(monkeypatch, tmp_path, page, *args)[0] == 0
    first = tmp_path / "first.json"
    first.write_text((tmp_path / "dom.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert _dom(monkeypatch, tmp_path, page, *args)[0] == 0
    assert main(["diff", str(first), str(tmp_path / "dom.json")]) == 0


def test_dom_capture_does_not_mistake_url_normalisation_for_a_redirect(monkeypatch, tmp_path):
    from tools.ui_inventory import _elsewhere
    assert _elsewhere("http://h:1/player/sheet/Go'el%20Thrall", "http://h:1/player/sheet/Go'el Thrall") is None
    assert _elsewhere("http://h/s/S%C3%A9ra", "http://h/s/Séra") is None
    assert _elsewhere("http://localhost:5/x", "HTTP://LOCALHOST:5/x") is None
    assert _elsewhere("http://localhost/x", "http://localhost:80/x") is None
    assert _elsewhere("http://h:1/login", "http://h:1/x") == "/login"
    assert _elsewhere("http://other:1/x", "http://h:1/x") == "/x"
    page = _Page({"*": "http://127.0.0.1:5057/player/sheet/Go%20el"})
    code, routes = _dom(monkeypatch, tmp_path, page, "--routes", "/player/sheet/Go el")
    assert code == 0 and routes["/player/sheet/Go el"]["status"] == 200
