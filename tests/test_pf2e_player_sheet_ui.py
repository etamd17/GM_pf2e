"""UI-10 contracts for the additive PF2e player-sheet refinement."""

from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import quote

import pytest

import app


ROOT = Path(app.__file__).resolve().parent
SHEET = ROOT / "templates" / "player_sheet.html"
FIXTURE = ROOT / "tests" / "fixtures" / "kyle_l10.json"


def _digest(value) -> str:
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


class SheetInventory(HTMLParser):
    """Capture the behavior-bearing markup that UI-10 must not change."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.ids = []
        self.handlers = []
        self.links = []
        self.aria = []
        self.forms = []
        self._form = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        key = attributes.get("id") or attributes.get("name") or ""
        if attributes.get("id"):
            self.ids.append((tag, attributes["id"]))
        for name, value in attrs:
            if name.startswith("on"):
                self.handlers.append((tag, key, name, value or ""))
        if tag == "a" and attributes.get("href"):
            self.links.append((attributes["href"], attributes.get("target", "")))
        semantics = tuple(sorted(
            (name, value or "")
            for name, value in attrs
            if name == "role" or name == "tabindex" or name.startswith("aria-")
        ))
        if semantics:
            self.aria.append((tag, key, semantics))

        if tag == "form":
            self._form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "GET").upper(),
                "fields": [],
            }
        elif self._form is not None and tag in {"button", "input", "select", "textarea"}:
            self._form["fields"].append((
                tag,
                attributes.get("name", ""),
                attributes.get("type", ""),
            ))

    def handle_endtag(self, tag):
        if tag == "form" and self._form is not None:
            self.forms.append((
                self._form["action"],
                self._form["method"],
                tuple(self._form["fields"]),
            ))
            self._form = None


@pytest.fixture
def rendered_sheet(monkeypatch):
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pc = app.Character(raw, file_path=str(FIXTURE))
    monkeypatch.setattr(app, "PARTY_LIBRARY", {pc.name: pc})
    monkeypatch.setattr(app, "_sync_party_from_disk", lambda: None)
    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    response = app.app.test_client().get("/player/sheet/" + quote(pc.name))
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_player_sheet_behavior_inventory_matches_main(rendered_sheet):
    inventory = SheetInventory(rendered_sheet)
    actual = {
        "ids": (len(inventory.ids), _digest(inventory.ids)),
        "handlers": (len(inventory.handlers), _digest(inventory.handlers)),
        "links": (len(inventory.links), _digest(inventory.links)),
        "aria": (len(inventory.aria), _digest(inventory.aria)),
        "forms": (len(inventory.forms), _digest(inventory.forms)),
    }
    expected = {
        "ids": (210, "aae454ee691210c4de6715fa72565ca19c3df327d63449abbe8471369f976e87"),
        "handlers": (388, "f642fe7e8a2e35903794d540d85713b424582035901181472bd89ca8a56dc28f"),
        "links": (3, "7a49458611d7c503a0dc067fedce93f8ee59bfc70d5af410f81c740420a9c162"),
        "aria": (161, "7c25eb13cce2951bc06c88655b92b144c84252ad06ae4daee7c12efbf37d6d77"),
        "forms": (1, "30eef1e694a33ea7e0cf77fe4468757883db2ad69ef3fd5318af863792417797"),
    }
    assert actual == expected, actual


def test_revision_a_css_is_scoped_and_excludes_other_proposals():
    source = SHEET.read_text(encoding="utf-8")
    marker = "UI-10 REVISION A"
    assert source.count(marker) == 1
    start = source.index(marker)
    end = source.index("</style>", start)
    block = source[start:end]

    for selector, declaration in (
        ("body .char-header .ability-row-wrap", "gap: 0 !important"),
        ("body .char-header .ability-block", "border-right: 1px solid var(--border-strong) !important"),
        ("body .char-header .ability-block .mod", "font-size: 22px !important"),
        ("body .char-header .stats-strip .ss-val", "font-size: 24px !important"),
        ("body .sheet-ember-host .tab-btn", "min-height: 40px !important"),
        ("body .sheet-ember-host .skill-row", "min-height: 40px !important"),
        ("body .sheet-ember-host [data-strike-btn]", "min-height: 70px !important"),
        ("@media (max-width: 700px)", "gap: 6px !important"),
    ):
        assert selector in block
        assert declaration in block

    for forbidden in (
        "sheet-preview-session", "preview-sheet-feedback", "#tab-core",
        "position: sticky", "inventory",
    ):
        assert forbidden not in block
