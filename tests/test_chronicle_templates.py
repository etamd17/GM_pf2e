"""Static guards for the Chronicle template slice (PR1, Part 4a).

No app render — these assert file presence, the extend-chain, that the
reading serif is actually loaded (base.html only ships Inter+Cinzel), and
that system.css defines the .chron-* component grammar. Full render is
covered by tools/check_templates.py (parse) + the route tests.

Scope note: only chronicle_base.html exists at this point in the build
(the six screen templates land in the next commit), so the all-screens
existence/extend-chain test and the inline-handler-ban test are deferred
to that commit's test file, per the Part 4a task brief.
"""
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(_REPO, "templates")


def _tpl(name):
    with open(os.path.join(_TPL, name), encoding="utf-8") as f:
        return f.read()


def test_chronicle_base_extends_base_and_loads_reading_font():
    text = _tpl("chronicle_base.html")
    assert '{% extends "base.html" %}' in text
    # base.html does NOT load Alegreya; the reading surface must pull it in.
    assert "Alegreya" in text, "reading serif not loaded in chronicle_base.html"


def test_base_html_has_head_extra_seam():
    # chronicle_base injects the <link> via this additive block.
    assert "{% block head_extra %}" in _tpl("base.html")


_CHRON_CLASSES = [
    ".chron-kicker", ".chron-title", ".chron-subnav", ".chron-tab",
    ".chron-live-bar", ".chron-grid", ".chron-card", ".chron-monogram",
    ".chron-portrait", ".chron-pill", ".chron-prose", ".chron-callout-quote",
    ".chron-doc-frame", ".chron-timeline", ".chron-chapter", ".chron-empty",
]


def test_system_css_defines_chron_component_classes():
    with open(os.path.join(_REPO, "static", "css", "system.css"), encoding="utf-8") as f:
        css = f.read()
    missing = [c for c in _CHRON_CLASSES if c not in css]
    assert not missing, f"system.css missing Chronicle classes: {missing}"


def test_chron_live_bar_respects_hidden_attribute():
    with open(os.path.join(_REPO, "static", "css", "system.css"), encoding="utf-8") as f:
        css = f.read()
    assert ".chron-live-bar[hidden]" in css, "live-banner must honor the hidden attribute (else it renders always-on)"
