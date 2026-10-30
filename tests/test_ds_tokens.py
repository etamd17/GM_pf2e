"""DS-01: design-system tokens, the warm-gilt switch and the stylesheet links."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_contrast  # noqa: E402
import design_tokens  # noqa: E402

SYSTEM = (ROOT / "static" / "css" / "system.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "templates"
# Found, not listed: a new standalone page that links system.css must link ds.css too.
SYSTEM_PAGES = sorted(p.stem for p in TEMPLATES.glob("*.html")
                      if "filename='css/system.css'" in p.read_text(encoding="utf-8"))
TOKEN_PAGES = ["_account_base", "splash"]


def _template(name):
    return (TEMPLATES / f"{name}.html").read_text(encoding="utf-8")


def test_ds_tokens_css_is_generated_from_system_css():
    have = (ROOT / "static" / "css" / "ds-tokens.css").read_text(encoding="utf-8")
    assert have.replace("\r\n", "\n") == design_tokens.render_ds_tokens(SYSTEM), \
        "static/css/ds-tokens.css drifted: run python tools/design_tokens.py generate"


def test_ds_tokens_css_holds_only_resolved_custom_properties():
    text = (ROOT / "static" / "css" / "ds-tokens.css").read_text(encoding="utf-8")
    body = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    assert "var(" not in body
    selectors = {s.strip() for s in re.findall(r"([^{}]+)\{", body)}
    assert selectors <= {":root", "@media (prefers-reduced-motion: reduce)",
                         "@media (max-width: 640px)"}, selectors
    for decl in re.findall(r"([^{};]+):[^;{}]+;", body):
        assert decl.strip().startswith("--"), decl


@pytest.mark.parametrize("name", SYSTEM_PAGES)
def test_ds_css_is_linked_right_after_system_css(name):
    assert len(SYSTEM_PAGES) >= 7
    lines = [l.strip() for l in _template(name).splitlines()]
    sys_at = next(i for i, l in enumerate(lines) if "filename='css/system.css'" in l)
    assert "url_for('static', filename='css/ds.css')" in lines[sys_at + 1]


@pytest.mark.parametrize("name", TOKEN_PAGES)
def test_pages_without_system_css_get_the_token_partial(name):
    src = _template(name)
    assert "css/system.css" not in src            # never import system.css's !important rules
    tokens = src.index("filename='css/ds-tokens.css'")
    ds = src.index("filename='css/ds.css'")
    assert tokens < ds < src.index("<style>")     # the page's own :root still wins


def test_the_27a_override_is_gone_and_section_1_renders():
    assert "#0d0c10 !important" not in SYSTEM and "#d28a3d" not in SYSTEM.split("27a.")[1][:2000]
    html, body = design_tokens.computed(SYSTEM, "pf2e")
    assert (body["--bg-page"], body["--bg-card"], body["--gilt-300"]) == ("#0c0a07", "#1c1813", "#c9a34e")
    # Section 2 re-points --border-strong at --border-rule on html and body.
    assert body["--border-strong"] == "rgba(201, 163, 78, 0.28)"


# Every token the removed 27a override set, as <body> resolved it before DS-01
# (tools/design_tokens.py on cb5a29f5). A theme line lost later would fall through
# to warm gilt inside a Cosmere page.
COSMERE_BODY = {
    "stormlight": {
        "--bg-page": "#0b0f15", "--bg-card": "#131b26", "--bg-card-2": "#1a2431",
        "--bg-input": "#0e141c", "--bg-sunken": "#0e141c", "--gilt-100": "#d3ecfb",
        "--gilt-200": "#9fd2f2", "--gilt-300": "#5fa8e0", "--gilt-400": "#3d83bf",
        "--border-card": "rgba(95, 168, 224, 0.16)", "--border-rule": "rgba(95, 168, 224, 0.30)",
        "--border-strong": "rgba(95, 168, 224, 0.30)", "--text-on-cream": "#e7eef6",
        "--text-on-cream-2": "#b9c6d6", "--text-on-cream-3": "#9fb1c4",
        "--text-on-dark": "#e7eef6", "--text-on-dark-2": "#9fb2c6", "--text-on-dark-3": "#96a9bf",
    },
    "mistborn": {
        "--bg-page": "#0d0d0e", "--bg-card": "#151518", "--bg-card-2": "#1d1d21",
        "--bg-input": "#100f12", "--bg-sunken": "#100f12", "--gilt-100": "#d7dbe0",
        "--gilt-200": "#c8cdd5", "--gilt-300": "#aab0ba", "--gilt-400": "#828892",
        "--border-card": "rgba(202, 204, 212, 0.13)", "--border-rule": "rgba(202, 204, 212, 0.26)",
        "--border-strong": "rgba(202, 204, 212, 0.26)", "--text-on-cream": "#e9e7e2",
        "--text-on-cream-2": "#b3b2aa", "--text-on-cream-3": "#a9a89e",
        "--text-on-dark": "#e9e7e2", "--text-on-dark-2": "#b3b2aa", "--text-on-dark-3": "#a9a89e",
    },
}

# What <html> paints with on a Cosmere page before DS-01 (from the removed override).
COSMERE_HTML = {"--bg-page": "#0d0c10", "--border-rule": "rgba(210, 138, 61, 0.30)",
                "--text-on-dark": "#ebe9df", "--text-on-dark-3": "#a79d88"}


@pytest.mark.parametrize("theme", ["stormlight", "mistborn"])
def test_cosmere_keeps_every_value_it_had_before_ds_01(theme):
    """Inside <body> the Cosmere themes re-declare every removed token, so nothing
    there moves. <html> paints the page background, text colour and the page
    scrollbar (base.html) from :root, so those values are pinned on Cosmere pages."""
    html, body = design_tokens.computed(SYSTEM, theme)
    assert {k: html[k] for k in COSMERE_HTML} == COSMERE_HTML
    assert {k: body[k] for k in COSMERE_BODY[theme]} == COSMERE_BODY[theme]


def test_section_27_tints_are_warm_on_pf2e_and_5e_and_unchanged_on_cosmere():
    """The 27a orange also lived on as literals in later rules. Only the <html> pin
    may still spell it."""
    assert len(re.findall(r"rgba\(210, 138, 61", SYSTEM)) == 1
    assert "--border-rule:     rgba(210, 138, 61, 0.30);" in SYSTEM
    expected = {"pf2e": "201, 163, 78", "dnd5e": "201, 163, 78",
                "stormlight": "210, 138, 61", "mistborn": "210, 138, 61"}
    for theme, triple in expected.items():
        assert design_tokens.computed(SYSTEM, theme)[1]["--tint-27-rgb"] == triple


def test_5e_hp_roles_are_its_own_and_the_rest_stay_pf2e():
    _, dnd = design_tokens.computed(SYSTEM, "dnd5e")
    _, pf2e = design_tokens.computed(SYSTEM, "pf2e")
    assert {k: dnd[k] for k in ("--c-hp-ok", "--c-hp-bloodied", "--c-hp-crit", "--c-hp-down")} == {
        "--c-hp-ok": "#5fbf7f", "--c-hp-bloodied": "#e0b04a",
        "--c-hp-crit": "#e0603c", "--c-hp-down": "#555555"}
    assert dnd["--c-hp-hurt"] == pf2e["--c-hp-hurt"]
    assert dnd["--c-accent"] == pf2e["--c-accent"]     # until the DS-24a mockup (Q4)


def _tok(css, theme="pf2e"):
    return design_tokens.computed(css, theme)


def test_nested_var_fallbacks_resolve_as_a_browser_does():
    css = ":root{--a:#111;--b:#222;--c:#333;" \
          "--x:var(--a,color-mix(in srgb,var(--b) 50%,transparent));" \
          "--y:var(--nope,var(--c));--z:var(--nope,var(--nada,#444))}"
    html, _ = _tok(css)
    assert (html["--x"], html["--y"], html["--z"]) == ("#111", "#333", "#444")


def test_important_is_recognised_in_every_valid_spelling():
    for spelling in ("!important", "! important", "!IMPORTANT"):
        html, _ = _tok(":root{--z:#fff %s} :root{--z:#000}" % spelling)
        assert html["--z"] == "#fff", spelling


def test_html_has_specificity_counts_its_argument():
    css = "html:has(> body.system-cosmere.cosmere-mistborn){--z:#111} html:has(> body.system-cosmere){--z:#222}"
    assert _tok(css, "mistborn")[0]["--z"] == "#111"
    assert _tok(css, "stormlight")[0]["--z"] == "#222"


def test_strings_and_statement_at_rules_do_not_confuse_the_parser():
    assert _tok(':root{--q:"}";--z:#fff}')[0] == {"--q": '"}"', "--z": "#fff"}
    assert _tok(':root{--q:";";--z:#fff}')[0] == {"--q": '";"', "--z": "#fff"}
    assert _tok(':root{--q:"/*";--z:#fff} .a{} :root{--y:"*/"}')[0]["--z"] == "#fff"
    assert _tok("@import url(x.css);\n:root{--z:#fff}")[0] == {"--z": "#fff"}


@pytest.mark.parametrize("css", [
    ".system-cosmere{--c-x:#000}",
    "body.system-cosmere:not(.cosmere-mistborn){--c-x:#000}",
    "@supports (color:red){:root{--c-x:#000}}",
    "@media a{@media b{:root{--fs-x:1}}}",
])
def test_a_token_the_resolver_cannot_model_is_an_error_not_a_silent_skip(css):
    with pytest.raises(ValueError, match="does not|not model"):
        _tok(":root{--c-x:#fff}" + css, "stormlight")


def test_generated_file_covers_body_only_tokens_and_html_media_rules():
    out = design_tokens.render_ds_tokens(
        ":root{--c-a:#111} body{--c-x:#222} @media (max-width:640px){html{--fs-2xl:1rem !important}}")
    assert "--c-x: #222;" in out and "--fs-2xl: 1rem !important;" in out


def test_contrast_tool_checks_a_new_text_role_it_was_not_told_about():
    weak = SYSTEM + "\n:root, body { --c-arcane: #5a3a8a; --c-arcane-text: #221133; }\n"
    rows = check_contrast.check(weak)
    assert any(r["fg"] == "--c-arcane-text" and r["kind"] == "text" and not r["ok"] for r in rows)
    assert any(r["fg"] == "--c-arcane-text" and r["kind"] == "status-text" for r in rows)


def test_contrast_json_output_parses(capsys):
    import json
    assert check_contrast.main(["x", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["theme"] == "pf2e"
    assert "enforced failure" in captured.err


@pytest.mark.parametrize(("theme", "expected"), [
    ("pf2e", {"--c-page": "#0c0a07", "--c-surface": "#1c1813", "--c-accent": "#c9a34e",
              "--c-focus": "#e0b65a", "--c-on-accent": "#0c0a07", "--c-info": "#c9bfa3"}),
    ("stormlight", {"--c-page": "#0b0f15", "--c-surface": "#131b26", "--c-accent": "#5fa8e0",
                    "--c-focus": "#9fd2f2", "--c-on-accent": "#06242b", "--c-info": "#9fd2f2",
                    "--c-hp-ok": "#5fe6b0", "--c-chart-1": "#d3ecfb"}),
    ("mistborn", {"--c-page": "#0d0d0e", "--c-surface": "#151518", "--c-accent": "#c8cdd5",
                  "--c-focus": "#e9e7e2", "--c-on-accent": "#0c0a07", "--c-info": "#b3b2aa",
                  "--c-chart-1": "#d7dbe0", "--c-hp-down": "#a9a89e"}),
])
def test_colour_roles_follow_each_theme(theme, expected):
    """Roles are declared on <body> too, so they re-resolve inside the Cosmere themes."""
    _, body = design_tokens.computed(SYSTEM, theme)
    assert {k: body[k] for k in expected} == expected


def test_contrast_floors_hold_in_every_theme():
    rows = check_contrast.check(SYSTEM)
    failures = [r for r in rows if r["enforced"] and not r["ok"]]
    assert not failures, failures
    pf2e = {(r["kind"], r["fg"]): r["ratio"] for r in rows if r["theme"] == "pf2e"}
    assert pf2e[("text", "--c-text-1")] == pytest.approx(14.7, abs=0.05)
    assert pf2e[("text", "--c-accent")] == pytest.approx(7.4, abs=0.05)


def test_contrast_tool_fails_when_a_text_role_drops_below_the_floor():
    weak = SYSTEM.replace("--c-success-text: #9bc06f;", "--c-success-text: #3a4a2a;")
    rows = check_contrast.check(weak)
    assert any(r["fg"] == "--c-success-text" and r["enforced"] and not r["ok"] for r in rows)
