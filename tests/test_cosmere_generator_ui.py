from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser

import app as A


CARD_LABELS = {
    "name": "Name",
    "npc": "NPC",
    "weather": "Highstorm / Weather",
    "spheres": "Spheres",
    "loot": "Loot & Fabrials",
    "location": "Location",
    "hook": "Plot Hook",
    "rumor": "Rumor",
    "dressing": "Scene Dressing",
}
CARD_CONTROL_CLASSES = {"cg-back", "cg-copy", "cg-pin", "cg-batch", "cg-reroll"}


class _GeneratorHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[str] = []
        self.buttons: list[dict[str, object]] = []
        self._card_type: str | None = None
        self._card_div_depth = 0
        self._button: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())

        if tag == "div":
            if self._card_type is not None:
                self._card_div_depth += 1
            elif "cg-card" in classes:
                self._card_type = attributes["data-type"]
                self._card_div_depth = 1
                self.cards.append(self._card_type)

        if tag == "button":
            matching = classes & (CARD_CONTROL_CLASSES | {"cg-all"})
            if matching:
                self._button = {
                    "card": self._card_type,
                    "class": next(iter(matching)),
                    "attrs": attributes,
                    "text_parts": [],
                }

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._button is not None:
            self._button["text"] = " ".join(
                "".join(self._button.pop("text_parts")).split()
            )
            self.buttons.append(self._button)
            self._button = None

        if tag == "div" and self._card_type is not None:
            self._card_div_depth -= 1
            if self._card_div_depth == 0:
                self._card_type = None

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button["text_parts"].append(data)


def _render_generator(monkeypatch) -> tuple[str, _GeneratorHTMLParser]:
    monkeypatch.setattr(A, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(A, "_is_gm", lambda: True)

    response = A.app.test_client().get("/cosmere/generator")
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    parser = _GeneratorHTMLParser()
    parser.feed(html)
    return html, parser


def test_generator_renders_all_controls_with_visible_labels_in_approved_order(monkeypatch):
    html, parsed = _render_generator(monkeypatch)

    assert parsed.cards == list(CARD_LABELS)
    counts = Counter(button["class"] for button in parsed.buttons)
    assert counts == {
        "cg-back": 9,
        "cg-copy": 9,
        "cg-pin": 9,
        "cg-batch": 2,
        "cg-reroll": 9,
        "cg-all": 2,
    }

    expected_text = {
        "cg-back": "Back",
        "cg-copy": "Copy",
        "cg-pin": "Pin",
        "cg-batch": "Roll 5",
        "cg-reroll": "Reroll",
    }
    for card_type in CARD_LABELS:
        card_buttons = [button for button in parsed.buttons if button["card"] == card_type]
        expected_order = ["cg-back", "cg-copy", "cg-pin"]
        if card_type in {"name", "npc"}:
            expected_order.append("cg-batch")
        expected_order.append("cg-reroll")
        assert [button["class"] for button in card_buttons] == expected_order
        for button in card_buttons:
            assert expected_text[button["class"]] in button["text"]

    header_text = [button["text"] for button in parsed.buttons if button["class"] == "cg-all"]
    assert header_text == ["↻ Reroll all", "Copy all"]
    header_handlers = [
        button["attrs"]["onclick"]
        for button in parsed.buttons
        if button["class"] == "cg-all"
    ]
    assert header_handlers == ["cgRerollAll()", "cgCopyAll(this)"]
    assert "Rosharan prep, one tap at a time." not in html
    assert "refreshes every card except the ones you pin" in html
    assert "returns a card to its previous result" in html


def test_generator_keeps_the_existing_handler_and_accessibility_contract(monkeypatch):
    _html, parsed = _render_generator(monkeypatch)

    by_card_and_class = {
        (button["card"], button["class"]): button["attrs"]
        for button in parsed.buttons
        if button["card"] is not None
    }
    for card_type, label in CARD_LABELS.items():
        expected = {
            "cg-back": {
                "onclick": f"cgBack('{card_type}')",
                "title": "Previous result",
                "aria-label": f"Previous {label}",
            },
            "cg-copy": {
                "onclick": f"cgCopy('{card_type}', this)",
                "title": f"Copy {label}",
                "aria-label": f"Copy {label}",
            },
            "cg-pin": {
                "onclick": f"cgTogglePin('{card_type}', this)",
                "title": "Pin — keep this when you Reroll all",
                "aria-label": f"Pin {label}",
                "aria-pressed": "false",
            },
            "cg-reroll": {
                "onclick": f"cgReroll('{card_type}')",
                "title": f"Reroll {label}",
                "aria-label": f"Reroll {label}",
            },
        }
        if card_type in {"name", "npc"}:
            expected["cg-batch"] = {
                "onclick": f"cgBatch('{card_type}', 5)",
                "title": "Roll 5 at once (a crowd)",
                "aria-label": f"Roll 5 {label}",
            }

        for control_class, required_attrs in expected.items():
            attrs = by_card_and_class[(card_type, control_class)]
            for name, value in required_attrs.items():
                assert attrs[name] == value
        assert "disabled" in by_card_and_class[(card_type, "cg-back")]
