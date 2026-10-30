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
SYSTEM_PAGES = ["base", "campaign_intro", "mobile_combat", "player_builder",
                "player_levelup", "player_sheet", "player_view"]
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
    assert body["--bg-page"] != "#0d0c10"            # the pin stays on <html> only
    assert body["--border-strong"] == body["--border-rule"]


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
