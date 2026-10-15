from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import random
from types import SimpleNamespace

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
MISTBORN_CARD_LABELS = {
    **CARD_LABELS,
    "weather": "Mists / Weather",
    "spheres": "Coins",
    "loot": "Loot & Metals",
}
ROSHAR_ONLY = (
    "roshar", "alethi", "thaylen", "highstorm", "sphere", "spren",
    "shardblade", "fabrial", "chasmfiend", "urithiru", "kholinar",
    "herdazian", "veden", "azish", "broam", "rockbud", "stormfather",
)
CARD_CONTROL_CLASSES = {"cg-back", "cg-copy", "cg-pin", "cg-batch", "cg-reroll"}


class _GeneratorHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[str] = []
        self.labels: dict[str, str] = {}
        self.bodies: dict[str, str] = {}
        self.buttons: list[dict[str, object]] = []
        self._card_type: str | None = None
        self._card_div_depth = 0
        self._button: dict[str, object] | None = None
        self._label_parts: list[str] | None = None
        self._body_card: str | None = None

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
            if "cg-body" in classes and self._card_type is not None:
                self._body_card = self._card_type
                self.bodies[self._card_type] = ""

        if tag == "span" and "cg-label" in classes:
            self._label_parts = []

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

        if tag == "span" and self._label_parts is not None:
            if self._card_type is not None:
                self.labels[self._card_type] = " ".join(
                    "".join(self._label_parts).split()
                )
            self._label_parts = None

        if tag == "div" and self._card_type is not None:
            if self._body_card is not None and self._card_div_depth == 2:
                self._body_card = None
            self._card_div_depth -= 1
            if self._card_div_depth == 0:
                self._card_type = None

    def handle_data(self, data: str) -> None:
        if self._button is not None:
            self._button["text_parts"].append(data)
        if self._label_parts is not None:
            self._label_parts.append(data)
        if self._body_card is not None:
            self.bodies[self._body_card] += data


def _render_generator(monkeypatch, *, world="stormlight", era="era-1") -> tuple[str, _GeneratorHTMLParser]:
    monkeypatch.setattr(A, "_active_system", lambda: "cosmere")
    monkeypatch.setattr(A, "_is_gm", lambda: True)
    monkeypatch.setattr(A, "_cosmere_world", lambda: world)
    monkeypatch.setattr(A, "_cosmere_generator_era", lambda: era, raising=False)

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


def test_mistborn_generator_keeps_controls_and_uses_world_labels(monkeypatch):
    _html, parsed = _render_generator(monkeypatch, world="mistborn")

    assert parsed.cards == list(MISTBORN_CARD_LABELS)
    assert parsed.labels == MISTBORN_CARD_LABELS
    assert Counter(button["class"] for button in parsed.buttons) == {
        "cg-back": 9,
        "cg-copy": 9,
        "cg-pin": 9,
        "cg-batch": 2,
        "cg-reroll": 9,
        "cg-all": 2,
    }
    for card_type, label in MISTBORN_CARD_LABELS.items():
        card_buttons = [button for button in parsed.buttons if button["card"] == card_type]
        assert any(button["attrs"].get("aria-label") == f"Reroll {label}"
                   for button in card_buttons)


def test_mistborn_page_and_rerolls_use_server_world_without_roshar_leaks(monkeypatch):
    _html, parsed = _render_generator(monkeypatch, world="mistborn")
    for key, body in parsed.bodies.items():
        assert body.strip(), key
        assert not any(term in body.casefold() for term in ROSHAR_ONLY), (key, body)

    client = A.app.test_client()
    for key in MISTBORN_CARD_LABELS:
        for _ in range(40):
            response = client.post(
                f"/api/cosmere/generate/{key}", json={"world": "stormlight"})
            assert response.status_code == 200
            body = response.get_json()["html"]
            assert body.strip(), key
            assert not any(term in body.casefold() for term in ROSHAR_ONLY), (key, body)


def test_mistborn_era_two_routes_npcs_locations_and_coins(monkeypatch):
    random.seed(1902)
    monkeypatch.setattr(A, "_is_gm", lambda: True)
    monkeypatch.setattr(A, "_cosmere_world", lambda: "mistborn")
    monkeypatch.setattr(A, "_cosmere_generator_era", lambda: "era-2", raising=False)
    client = A.app.test_client()

    samples = {
        key: " ".join(client.post(f"/api/cosmere/generate/{key}").get_json()["html"]
                      for _ in range(80)).casefold()
        for key in ("npc", "location", "spheres")
    }
    assert any(word in samples["npc"] for word in ("constable", "gunsmith", "rail"))
    assert any(word in samples["location"] for word in ("elendel", "rail", "canal"))
    assert "banknote" in samples["spheres"]


def test_generator_era_uses_exact_era_two_binding_and_falls_back_safely(monkeypatch):
    from systems.cosmere.rules import profiles

    monkeypatch.setattr(A, "_load_campaign_config", lambda: {"cosmere_world": "mistborn"})
    monkeypatch.setattr(
        profiles, "normalize_binding",
        lambda _config: SimpleNamespace(allowed_eras=("era-2",)))
    assert A._cosmere_generator_era() == "era-2"

    monkeypatch.setattr(
        profiles, "normalize_binding",
        lambda _config: SimpleNamespace(allowed_eras=("era-1", "era-2")))
    assert A._cosmere_generator_era() == "era-1"

    def unavailable(_config):
        raise RuntimeError("rules bundle unavailable")

    monkeypatch.setattr(profiles, "normalize_binding", unavailable)
    assert A._cosmere_generator_era() == "era-1"
