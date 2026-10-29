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

    def get_data(self, as_text=True):
        return self._html


def test_generated_ids_are_normalised_only_in_account_profiles():
    from tools.ui_inventory import _record
    a = '<a href="/campaign/' + "a" * 32 + '/invites">x</a>'
    b = '<a href="/campaign/' + "b" * 32 + '/invites">x</a>'
    assert _record(_Resp(a), {}, "/me", True) == _record(_Resp(b), {}, "/me", True)
    assert _record(_Resp(a), {}, "/me") != _record(_Resp(b), {}, "/me")
    assert _record(_Resp(a, 302), {}, "/me") == {"status": 302}


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
