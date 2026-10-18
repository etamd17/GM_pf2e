from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import re

import app
import pf2e_generator


GENERATOR_IDS = [
    "encounter",
    "random_event",
    "weather",
    "trap",
    "travel_encounter",
    "npc",
    "villain",
    "faction",
    "rumor",
    "quest",
    "settlement",
    "tavern",
    "shop",
    "dungeon_room",
    "loot",
    "magic_item",
    "treasure_hoard",
    "puzzle",
    "deity",
]
GENERATOR_CATEGORIES = [
    "combat",
    "combat",
    "combat",
    "combat",
    "combat",
    "npc",
    "npc",
    "npc",
    "npc",
    "npc",
    "place",
    "place",
    "place",
    "place",
    "treasure",
    "treasure",
    "treasure",
    "lore",
    "lore",
]
GENERATOR_TITLES = [
    "Encounter",
    "Random Event",
    "Weather",
    "Trap / Hazard",
    "Travel Encounter",
    "Random NPC",
    "Villain Seed",
    "Faction",
    "Rumor",
    "Quest Hook",
    "Settlement",
    "Tavern",
    "Shop",
    "Dungeon Room",
    "Loot",
    "Magic Item",
    "Treasure Hoard",
    "Puzzle",
    "Deity / Cult",
]
BIOMES = [
    "City",
    "Forest",
    "Dungeon",
    "Desert",
    "Swamp",
    "Mountains",
    "Coastal",
    "Arctic",
    "Underground",
]


class _Node:
    def __init__(self, tag: str, attrs, parent: "_Node | None", serial: int):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.serial = serial
        self.children: list[_Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    @property
    def text(self) -> str:
        parts = [child.text if isinstance(child, _Node) else child for child in self.children]
        return " ".join("".join(parts).split())

    @property
    def direct_text(self) -> str:
        return " ".join("".join(child for child in self.children if isinstance(child, str)).split())

    def descendants(self, *, include_self: bool = False):
        if include_self:
            yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()


class _DOM(HTMLParser):
    _VOID = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", [], None, 0)
        self.stack = [self.root]
        self.nodes: list[_Node] = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.stack[-1], len(self.nodes) + 1)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def find(self, *, tag=None, cls=None, **attrs) -> list[_Node]:
        return [
            node
            for node in self.nodes
            if (tag is None or node.tag == tag)
            and (cls is None or cls in node.classes)
            and all(node.attrs.get(key) == value for key, value in attrs.items())
        ]


def _inside(root: _Node, *, tag=None, cls=None, **attrs) -> list[_Node]:
    return [
        node
        for node in root.descendants(include_self=True)
        if (tag is None or node.tag == tag)
        and (cls is None or cls in node.classes)
        and all(node.attrs.get(key) == value for key, value in attrs.items())
    ]


class _GeneratorStub:
    def __getattr__(self, name):
        if not name.startswith("get_"):
            raise AttributeError(name)
        generator_id = name.removeprefix("get_")
        return lambda **_kwargs: f"<p><strong>{generator_id}</strong> result</p>"


def _render(monkeypatch, path: str) -> tuple[_DOM, str]:
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    monkeypatch.setattr(app, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app, "pf2e_gen", _GeneratorStub())
    response = app.app.test_client().get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    return _DOM(html), html


def test_generator_keeps_every_tool_and_exposes_labelled_card_actions(monkeypatch):
    """Catches dropped/reordered generators, tools, context choices, or handlers."""
    dom, html = _render(monkeypatch, "/generator?level=7&biome=Coastal")
    grid, = dom.find(cls="masonry")
    cards = _inside(grid, cls="gen-card")

    assert len(cards) == 19
    assert [card.attrs.get("data-cat") for card in cards] == GENERATOR_CATEGORIES
    assert [
        _inside(card, cls="gen-content")[0].attrs.get("id") for card in cards
    ] == [f"content-{generator_id}" for generator_id in GENERATOR_IDS]
    assert [_inside(card, cls="gen-card-title")[0].direct_text for card in cards] == GENERATOR_TITLES
    assert Counter(GENERATOR_CATEGORIES) == Counter(
        {"combat": 5, "npc": 5, "place": 4, "treasure": 3, "lore": 2}
    )

    for card, generator_id in zip(cards, GENERATOR_IDS):
        title, = _inside(card, cls="gen-card-title")
        assert title.attrs.get("onclick") == "toggleCollapse(this)"
        assert title.attrs.get("role") == "button"
        assert title.attrs.get("tabindex") == "0"
        assert title.attrs.get("onkeydown") == "toggleCollapseKey(event, this)"

        actions = [
            node
            for node in _inside(card, tag="button")
            if node.classes & {"copy-btn", "pin-btn", "reroll-btn"}
        ]
        assert [button.text for button in actions] == ["Copy", "Pin", "Reroll"]
        assert [button.attrs.get("onclick") for button in actions] == [
            f"copyCard('{generator_id}', this)",
            f"pinCard('{generator_id}', this)",
            f"rerollSingle('{generator_id}', this)",
        ]
        assert [button.attrs.get("title") for button in actions] == [
            "Copy",
            "Pin this result",
            "Reroll",
        ]
        assert [button.attrs.get("aria-label", "").split(" ", 1)[0] for button in actions] == [
            "Copy",
            "Pin",
            "Reroll",
        ]
        assert all(" " in button.attrs.get("aria-label", "") for button in actions)
        assert not any(_inside(button, tag="svg") for button in actions)

    level, = dom.find(tag="input", id="gen-level")
    assert {
        key: level.attrs.get(key) for key in ("type", "value", "min", "max", "onchange")
    } == {
        "type": "number",
        "value": "7",
        "min": "1",
        "max": "20",
        "onchange": "_genUpdateUrl()",
    }
    biome, = dom.find(tag="select", id="gen-biome")
    assert biome.attrs.get("onchange") == "_genUpdateUrl()"
    options = _inside(biome, tag="option")
    assert [option.attrs.get("value") for option in options] == BIOMES
    assert [option.text for option in options] == BIOMES
    assert [option.text for option in options if "selected" in option.attrs] == ["Coastal"]

    rapid = dom.find(tag="button", cls="rapid-btn")
    assert [button.text for button in rapid] == [
        "Name",
        "Tavern",
        "Shop",
        "Place",
        "Twist",
        "Omen",
        "Bounty",
        "Loot",
        "Trinket",
        "Room",
    ]
    assert [button.attrs.get("onclick") for button in rapid] == [
        f"rapidFire('{kind}', this)"
        for kind in (
            "rapid_name",
            "rapid_tavern",
            "rapid_shop",
            "rapid_place",
            "rapid_twist",
            "rapid_omen",
            "rapid_bounty",
            "rapid_loot",
            "rapid_trinket",
            "rapid_room",
        )
    ]
    filters = dom.find(tag="button", cls="gen-filter-chip")
    assert [button.text for button in filters] == [
        "All",
        "Combat",
        "NPCs",
        "Places",
        "Treasure",
        "Lore",
    ]
    assert [button.attrs.get("onclick") for button in filters] == [
        f"filterCards('{category}', this)"
        for category in ("all", "combat", "npc", "place", "treasure", "lore")
    ]

    reroll_all, = dom.find(tag="button", onclick="rerollAll()")
    copy_all, = dom.find(tag="button", onclick="copyAllCards(this)")
    clear, = dom.find(tag="button", onclick="clearRapid()")
    assert reroll_all.text.endswith("Reroll All")
    assert copy_all.text == "Copy Table"
    assert clear.text == "Clear"
    assert len(dom.find(id="rapid-results")) == 1
    assert {
        node.attrs.get("id") for node in dom.nodes if node.attrs.get("id", "").startswith("pinned-")
    } == {"pinned-section", "pinned-count", "pinned-body"}
    pinned_header, = dom.find(cls="pinned-header")
    assert pinned_header.attrs.get("onclick") == "togglePinned()"
    assert pinned_header.attrs.get("role") == "button"
    assert pinned_header.attrs.get("tabindex") == "0"
    assert pinned_header.attrs.get("onkeydown") == "togglePinnedKey(event)"
    assert "function toggleCollapseKey(event, titleEl)" in html
    assert "function togglePinnedKey(event)" in html
    assert "event.key === 'Enter' || event.key === ' '" in html
    assert "Changing level or biome rerolls all 19 results. Pin anything you want to keep first." in dom.root.text
    assert "column-count" not in html
    assert re.search(r"\.rapid-clear\s*\{[^}]*min-height:\s*32px", html)
    assert re.search(r"\.pinned-remove-btn\s*\{[^}]*min-height:\s*32px", html)
    assert re.search(
        r"\.rapid-copy,\s*\.rapid-dismiss\s*\{[^}]*min-width:\s*32px[^}]*min-height:\s*32px",
        html,
    )
    assert '<button class="rapid-copy" title="Copy" aria-label="Copy">' in html
    assert '<button class="rapid-dismiss" title="Dismiss" aria-label="Dismiss">' in html
    assert "row.querySelector('.rapid-copy').onclick = function()" in html
    assert "copyText(row.querySelector('.rapid-result-text').innerText, this);" in html
    assert "row.querySelector('.rapid-dismiss').onclick = function() { row.remove(); };" in html
    assert 'class="pinned-remove-btn" onclick="unpinCard(\'${p.id}\')" title="Remove pin" aria-label="Remove pin"' in html
    assert "fetch('/api/generator/pins')" in html
    assert "fetch('/api/generator/pin'" in html
    assert "fetch(`/api/generator/pin/${pinId}`" in html


def test_loot_ledger_keeps_award_contract_and_uses_selected_b_layout(monkeypatch):
    """Catches lost award fields/actions or a return to misleading wealth wording."""
    dom, html = _render(monkeypatch, "/gm/loot")
    required_ids = [
        "ledger-subtitle",
        "party-total-bar",
        "party-level",
        "party-total-wealth",
        "expected-wealth",
        "entry-count",
        "award-form",
        "award-recipient",
        "award-note",
        "award-pp",
        "award-gp",
        "award-sp",
        "award-cp",
        "item-rows",
        "btn-add-item",
        "btn-award",
        "award-status",
        "history-list",
        "wealth-grid",
    ]
    page, = {node.parent for node in dom.find(cls="ledger-header")}
    rendered_ids = [
        node.attrs["id"] for node in page.descendants(include_self=True) if "id" in node.attrs
    ]
    assert rendered_ids == required_ids

    summary, = dom.find(id="party-total-bar")
    assert [node.text for node in _inside(summary, cls="stat-label")] == [
        "Party Level",
        "Party coins",
        "Existing level reference / PC",
        "Ledger Entries",
    ]
    assert "Total Party Wealth" not in dom.root.text
    assert "coins only" in dom.root.text.lower()
    assert "items are not valued" in dom.root.text.lower()

    workspace, = dom.find(cls="loot-award-workspace")
    award_form, = _inside(workspace, id="award-form")
    history, = _inside(workspace, id="history-list")
    assert award_form.serial < history.serial
    assert "giving loot to a character adds it to their sheet and records an entry" in workspace.text.lower()
    assert "award to sheet deposits an older entry" in workspace.text.lower()
    assert "delete log removes the log only" in workspace.text.lower()
    assert "not items already given" in workspace.text.lower()

    balances, = dom.find(tag="details", cls="loot-balances")
    assert "open" not in balances.attrs
    balances_summary, = _inside(balances, tag="summary")
    wealth_grid, = _inside(balances, id="wealth-grid")
    assert balances_summary.text == "Character coin balances"
    assert workspace.serial < balances.serial < wealth_grid.serial

    award, = dom.find(id="award-form")
    recipient, = _inside(award, tag="select", id="award-recipient")
    note, = _inside(award, tag="input", id="award-note")
    assert recipient.attrs.get("id") == "award-recipient"
    assert note.attrs.get("type") == "text"
    assert len(_inside(award, cls="item-row")) == 1
    assert len(_inside(award, tag="input", cls="item-name")) == 1
    item_qty, = _inside(award, tag="input", cls="item-qty")
    assert {key: item_qty.attrs.get(key) for key in ("type", "min", "value")} == {
        "type": "number",
        "min": "1",
        "value": "1",
    }
    labels = {
        label.attrs["for"]: label.text
        for label in _inside(award, tag="label")
        if "for" in label.attrs
    }
    assert labels == {
        "award-recipient": "Recipient",
        "award-note": "Note",
        "award-pp": "PP",
        "award-gp": "GP",
        "award-sp": "SP",
        "award-cp": "CP",
    }
    for denomination in ("pp", "gp", "sp", "cp"):
        coin, = _inside(award, tag="input", id=f"award-{denomination}")
        assert {key: coin.attrs.get(key) for key in ("type", "min", "value")} == {
            "type": "number",
            "min": "0",
            "value": "0",
        }
    add_item, = _inside(award, tag="button", id="btn-add-item")
    submit, = _inside(award, tag="button", id="btn-award")
    assert add_item.text == "+ Add item"
    assert submit.text == "Record Award"
    assert "disabled" in submit.attrs

    # The rendered client is the boundary that chooses sheet deposit versus log-only.
    assert "const url = deposit ? '/api/send_loot' : '/api/loot_ledger/add';" in html
    assert "{target: recipient, items: items, coins: coins, note: note}" in html
    assert "{recipient: recipient, items: items, coins: coins, note: note}" in html
    assert "'/api/loot_ledger/' + encodeURIComponent(entryId) + '/award'" in html
    assert "fetch('/api/loot_ledger/delete'" in html
    assert "body: JSON.stringify({id: id})" in html
    assert "confirm('Delete this ledger entry?')" in html
    assert '<button class="history-delete" data-id="' in html
    assert ">Delete log</button>" in html
    assert 'class="history-award"' in html
    assert "list.querySelectorAll('.history-delete').forEach(function(btn)" in html
    assert "deleteEntry(btn.dataset.id);" in html
    assert "list.querySelectorAll('.history-award').forEach(function(btn)" in html
    assert "awardToSheet(btn.dataset.id, btn);" in html
    assert 'class="award-badge"' in html
    assert "No loot awards recorded yet." in html
    assert "Failed to load ledger data." in html
    assert "populateRecipients(d.party_wealth);" in html
    assert "classList.add('over')" not in html
    assert "classList.add('under')" not in html
    assert "sel.innerHTML = '<option value=\"\">-- select --</option>';" in html
    assert "partyWealth.forEach(function(pc)" in html
    assert "opt.value = pc.name;" in html
    assert "opt.textContent = pc.name;" in html


def test_every_magical_puzzle_template_formats(monkeypatch):
    """Every magical puzzle template must receive each placeholder it uses."""
    generator = pf2e_generator.RobustPF2eGenerator()
    puzzle_types = [
        "riddle",
        "logic",
        "physical",
        "magical",
        "trap",
        "haunt",
        "environmental",
    ]

    selected = {"template": None}

    def choose(sequence):
        if sequence == puzzle_types:
            return "magical"
        if sequence is generator.magical_puzzles:
            return selected["template"]
        return sequence[0]

    monkeypatch.setattr(pf2e_generator.random, "choice", choose)

    for template in generator.magical_puzzles:
        selected["template"] = template
        result = generator.get_puzzle(level=5, biome="City")
        assert "<b>Magical Puzzle</b>" in result
        if "illusion" in template:
            assert f"DC {generator._dc_easy(5)} Arcana" in result
        if "mirror dimension" in template:
            assert "HP 25" in result

