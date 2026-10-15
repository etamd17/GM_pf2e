from __future__ import annotations

from collections import Counter
import hashlib
from html.parser import HTMLParser
from types import SimpleNamespace

import app


HUB_IDS = {
    "campaign-tagline",
    "qa-d20-out",
    "qa-cal-date",
    "qa-cal-out",
    "adv-mode",
    "adv-xp-controls",
    "adv-xp-amt",
    "fa-toggle-btn",
    "qa-npc-modal",
    "qa-npc-title",
    "qa-npc-body",
}
HUB_HREFS = Counter(
    {
        "/tracker": 2,
        "/map": 1,
        "/party": 1,
        "/gmscreen": 1,
        "/encounter_builder": 1,
        "/generator": 2,
        "/player": 1,
        "/gm/stats": 1,
        "/gm/calendar": 1,
        "/api/perf": 1,
        "/health": 1,
        "/gm/logout": 1,
    }
)
HUB_ONCLICKS = Counter(
    [
        "qaRollD20()",
        "qaRollNpc()",
        "qaAdvanceDay()",
        "setAdvMode('milestone')",
        "setAdvMode('xp')",
        "markReady()",
        "awardXp(false)",
        "awardXp(true)",
        "toggleCampaignFA()",
        "if(event.target===this)qaCloseNpc()",
        "qaCloseNpc()",
        "qaRollNpc()",
    ]
)
HUB_TILE_TEXT_SHA256 = {
    "Party View": "633903309df25b141de5d74624fa0df7de2c8fdf3d7c0c6e751cb1f17a0e2233",
    "Encounter Tracker": "ca853ad3e101bec99273580a13868add1ac7521608dec0355ec8b7b81495e905",
    "Tactical Map": "e04caf2a6a531423c8f89969e216a472c996f90b7558cb6e4e36e0bb326a0a97",
    "GM Screen": "2be509a07f3d55cd13a515a66b670ec09a86f8f9a100f2925f8408e8985befd5",
    "Encounter Builder": "ccccca82bd24a1e851f6670ae2853af2a5cb40625c85b616af63aec7b586d83a",
    "Generators": "d6a5055104c3cf7bf94c5633dfbff3d05d66269df26496cb530d00e7cd50d241",
    "Player Hub": "45c13179d4a7d61ec8e80eed85c4fe53c4f57edaa111d0c5efc18b0f3b788dc1",
    "Campaign Stats": "7f5cfdd197fc320db1aea775f17636e52984a48dafc6bd79ed782f70024eb637",
    "Calendar": "9539a30d5550b4efa499c4d0aeb188a9f80590b8fa906f5f35bd1a7ddf8ead0f",
}

SCREEN_IDS = {
    "comp-search",
    "cat-all",
    "cat-spells",
    "cat-feats",
    "cat-equipment",
    "comp-results",
    "comp-results-list",
    "comp-detail",
    "comp-detail-name",
    "comp-detail-meta",
    "comp-detail-body",
    "checks-drawer",
    "reference-checks",
    "reference-conditions",
    "reference-adversaries",
    "reference-exploration",
}
SCREEN_ONCLICKS = Counter(
    [
        "setCompCat('all')",
        "setCompCat('spells')",
        "setCompCat('feats')",
        "setCompCat('equipment')",
        "document.getElementById('comp-detail').classList.add('hidden')",
        "toggleChecksDrawer()",
        "closeChecksDrawer()",
        "switchChecksTab('situation')",
        "switchChecksTab('skills')",
        "switchChecksTab('saves')",
        "switchChecksTab('dcs')",
        "switchChecksTab('knowledge')",
        "switchChecksTab('downtime')",
    ]
)
SCREEN_CARD_TEXT_SHA256 = {
    "DCs by Level": "866550ddee7a93fa6bee5d8408aef29322dcf583c58808ab7e4986035921df44",
    "Simple DCs": "63260fd89c4078c48011783bb66985c966df1aed007de2f298c9067f8e6f4df0",
    "Combat Maneuvers": "37564a4da96799658cb4aea4bc6e2f81d5c18ca7e2b5fd8a390d55482116c26d",
    "Common Conditions": "6c0b72076737976e8e436af4c45741d0c330c51240deff8e3e4d879251bab8dd",
    "Dying & Recovery": "8a9cdfd6e1475c78edd245613a96193cea533aa8d406619a6408e0613274d448",
    "Afflictions": "c803ded7a108795e2ec9a2397a27593645240bf09021ed7521e7dc66e9307aaf",
    "Elite & Weak Adjustments": "2cbf04e5e8a879f0f21b6f70f4d83df753676bac5306c7cfe68f0379213fbc0d",
    "Undead & Void Healing": "ac342074ebee7d17e4a1ad1e626a68d125e3a4d0f86a2615188fd67d64ec5cbd",
    "Environmental Damage": "71e7d35e8b9a4c89f87debb51a7a52e59851f3e061b63791cf4b29d2e9f6d2f3",
    "Material Hardness": "7ee5434bb6fb6a434fa81faf5663b82efe266b69540e7d09d156cb0935caad8e",
    "Cover": "feb8155b50723d654c42e6fee44c931bbebc5fd3a1c772ebe35ff27ae637d876",
    "Travel & Exploration": "2bde409d4488b32293039fff4af383ab2fba8ddc2db3d2e4faca0b6203914709",
    "Light, Vision & Senses": "c78951fa5d3e53487f72caadf488f062045f88c153aa56a9b38c21a027657579",
    "Creature Size & Reach": "a604c83d056ba6ac8a6723cc315de2f335957827488587ae279d81974b0ec401",
}
SCREEN_PANE_TEXT_SHA256 = {
    "situation": "ef26668c796868b5a34f3505f644e4df99fca31aeaa16d5ed8e733a263b3ac25",
    "skills": "6fec3f6e656011a8f464f0cac9fe02ed342ca71dcc642cc2b3e2a64a4adbf924",
    "saves": "f747c40ad42931b308ccc051caa8f8ff5c603aeed37b52a35be596ff151faa08",
    "dcs": "dfeefe9c978cc089640262770135c0b5358add33be2a9a3e312e6da8ffbecc1c",
    "knowledge": "76e49fd86a8582c2a693b4c090df2c3268668f4dc568ba29a66775522872918e",
    "downtime": "3e0b755f6c1ae48110c4560083869cab6e005895bfb3bbabaa81ba16250ed511",
}


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
        return " ".join("".join(c for c in self.children if isinstance(c, str)).split())

    def descendants(self, *, include_self: bool = False):
        if include_self:
            yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()


class _DOM(HTMLParser):
    _VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

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


def _text_digest(node: _Node) -> str:
    return hashlib.sha256(node.text.encode("utf-8")).hexdigest()


def _render(monkeypatch, path: str) -> _DOM:
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    if path == "/gm":
        monkeypatch.setattr(
            app,
            "PARTY_LIBRARY",
            {"pc": SimpleNamespace(name="Alyx", hp=20, current_hp=14, level=3)},
        )
        monkeypatch.setattr(app, "MONSTER_LIBRARY", {"wolf": object()})
        monkeypatch.setattr(app, "ACTIVE_ENCOUNTER", [object()])
        monkeypatch.setattr(
            app,
            "_load_campaign_config",
            lambda: {
                "name": "Inventory Test",
                "tagline": "Contract fixture",
                "session_number": 7,
                "next_session_at": "Soon",
            },
        )
        monkeypatch.setattr(app, "_free_archetype_enabled", lambda: False)
        monkeypatch.setattr(app, "_load_calendar", lambda: {"day": 1, "month": 0, "year": 4724})
        monkeypatch.setattr(
            app,
            "_load_campaign_stats",
            lambda: {"total_rolls": 4, "total_crits": 1, "total_combat_rounds": 2},
        )

    response = app.app.test_client().get(path)
    assert response.status_code == 200
    return _DOM(response.get_data(as_text=True))


def test_gm_hub_preserves_inventory_and_selected_b_layout(monkeypatch):
    dom = _render(monkeypatch, "/gm")
    content, = [
        node
        for node in dom.find(tag="div")
        if node.classes == {"max-w-7xl", "mx-auto"}
    ]
    stage, = dom.find(cls="gm-hub-stage")
    nodes = list(content.descendants(include_self=True))

    rendered_ids = [node.attrs["id"] for node in nodes if "id" in node.attrs]
    assert len(rendered_ids) == len(set(rendered_ids))
    assert set(rendered_ids) == HUB_IDS
    assert Counter(node.attrs["href"] for node in nodes if "href" in node.attrs) == HUB_HREFS
    assert Counter(node.attrs["onclick"] for node in nodes if "onclick" in node.attrs) == HUB_ONCLICKS

    amount, = _inside(stage, tag="input", id="adv-xp-amt")
    assert {key: amount.attrs.get(key) for key in ("type", "min", "value", "placeholder")} == {
        "type": "number",
        "min": "1",
        "value": "80",
        "placeholder": "XP",
    }

    assert [node.attrs.get("aria-live") for node in _inside(stage, cls="qa-out") if node.attrs.get("id")] == [
        "polite",
        "polite",
    ]
    assert Counter(
        (node.attrs.get("href"), node.attrs.get("aria-label"))
        for node in _inside(stage, tag="a", cls="tile__link")
    ) == Counter(
        {
            ("/gmscreen", "Open GM Screen"): 1,
            ("/generator", "Open Generators"): 1,
            ("/gm/calendar", "Open Calendar"): 1,
        }
    )
    modal, = _inside(content, id="qa-npc-modal")
    assert {key: modal.attrs.get(key) for key in ("role", "aria-modal", "aria-labelledby")} == {
        "role": "dialog",
        "aria-modal": "true",
        "aria-labelledby": "qa-npc-title",
    }
    crest, = _inside(stage, cls="gm-crest")
    combat_dot, = _inside(stage, cls="rc-dot")
    modal_close, = _inside(modal, tag="button", cls="qa-modal__x")
    assert crest.attrs.get("aria-hidden") == "true"
    assert combat_dot.attrs.get("aria-hidden") == "true"
    assert modal_close.attrs.get("aria-label") == "Close"

    tiles = _inside(stage, cls="tile")
    titled_tools = []
    for tile in tiles:
        links = _inside(tile, tag="a")
        href = tile.attrs.get("href") or (links[0].attrs.get("href") if links else None)
        headings = _inside(tile, tag="h2", cls="tile__title")
        if href and headings:
            titled_tools.append((href, headings[0].text))
    assert titled_tools == [
        ("/party", "Party View"),
        ("/tracker", "Encounter Tracker"),
        ("/map", "Tactical Map"),
        ("/gmscreen", "GM Screen"),
        ("/encounter_builder", "Encounter Builder"),
        ("/generator", "Generators"),
        ("/player", "Player Hub"),
        ("/gm/stats", "Campaign Stats"),
        ("/gm/calendar", "Calendar"),
    ]
    assert {
        _inside(tile, tag="h2", cls="tile__title")[0].text: _text_digest(tile)
        for tile in tiles
        if _inside(tile, tag="h2", cls="tile__title")
    } == HUB_TILE_TEXT_SHA256

    party_watch, = _inside(stage, cls="gm-party-watch")
    party_link, = [node for node in _inside(party_watch, tag="a", href="/party")]
    run_label, = [
        node for node in _inside(stage, cls="gm-group-label") if node.text.startswith("Run")
    ]
    assert party_link.serial < run_label.serial
    assert _inside(party_watch, cls="tile-health")

    run_grid, = _inside(stage, cls="gm-main-grid")
    assert [node.attrs["href"] for node in _inside(run_grid, tag="a") if "href" in node.attrs] == [
        "/tracker",
        "/map",
        "/gmscreen",
    ]

    prep_grid, manage_grid = _inside(stage, cls="gm-secondary-grid")
    assert [node.attrs["href"] for node in _inside(prep_grid, tag="a") if "href" in node.attrs] == [
        "/encounter_builder",
        "/generator",
    ]
    assert [node.attrs["href"] for node in _inside(manage_grid, tag="a") if "href" in node.attrs] == [
        "/player",
        "/gm/stats",
        "/gm/calendar",
    ]


def test_gm_screen_preserves_reference_inventory_and_adds_topic_controls(monkeypatch):
    dom = _render(monkeypatch, "/gmscreen")
    content, = [
        node
        for node in dom.find(tag="div")
        if node.classes == {"max-w-7xl", "mx-auto"}
    ]
    nodes = list(content.descendants(include_self=True))

    rendered_ids = [node.attrs["id"] for node in nodes if "id" in node.attrs]
    assert len(rendered_ids) == len(set(rendered_ids))
    assert set(rendered_ids) == SCREEN_IDS
    assert Counter(node.attrs["onclick"] for node in nodes if "onclick" in node.attrs) == SCREEN_ONCLICKS

    search, = _inside(content, tag="input", id="comp-search")
    assert {
        key: search.attrs.get(key)
        for key in ("type", "autocomplete", "placeholder")
    } == {
        "type": "text",
        "autocomplete": "off",
        "placeholder": "Search by name... (e.g. Fireball, Shield Block, Flaming Rune)",
    }

    checks_button, = _inside(content, tag="button", cls="gm-checks-button")
    assert checks_button.attrs.get("type") == "button"
    assert checks_button.attrs.get("aria-controls") == "checks-drawer"
    assert checks_button.attrs.get("aria-expanded") == "false"
    assert "onclick" not in checks_button.attrs
    assert checks_button.text == "Checks reference · C"

    nav, = _inside(content, tag="nav", cls="gm-reference-nav")
    assert nav.attrs.get("aria-label") == "Reference topics"
    topic_buttons = _inside(nav, tag="button", cls="gm-reference-tab")
    assert [button.attrs.get("data-reference-topic") for button in topic_buttons] == [
        "checks",
        "conditions",
        "adversaries",
        "exploration",
        "all",
    ]
    assert [button.text for button in topic_buttons] == [
        "Checks & actions",
        "Conditions & dying",
        "Adversaries & damage",
        "Exploration",
        "All references",
    ]
    assert [button.attrs.get("aria-pressed") for button in topic_buttons] == [
        "true",
        "false",
        "false",
        "false",
        "false",
    ]
    assert [button.attrs.get("tabindex") for button in topic_buttons] == ["0", "-1", "-1", "-1", "-1"]
    assert [button.attrs.get("aria-controls") for button in topic_buttons] == [
        "reference-checks",
        "reference-conditions",
        "reference-adversaries",
        "reference-exploration",
        "reference-checks reference-conditions reference-adversaries reference-exploration",
    ]
    assert all(button.attrs.get("type") == "button" for button in topic_buttons)

    groups = _inside(content, tag="section", cls="gm-group")
    expected = [
        (
            "checks",
            "Checks, DCs & Actions",
            3,
            4,
            ["DCs by Level", "Simple DCs", "Treat Wounds", "Combat Maneuvers"],
        ),
        (
            "conditions",
            "Conditions & Dying",
            3,
            2,
            ["Common Conditions", "Dying & Recovery", "Afflictions"],
        ),
        (
            "adversaries",
            "Adversaries & Damage",
            4,
            2,
            [
                "Elite & Weak Adjustments",
                "Undead & Void Healing",
                "Environmental Damage",
                "Material Hardness",
            ],
        ),
        (
            "exploration",
            "Exploration & Environment",
            4,
            4,
            ["Cover", "Travel & Exploration", "Light, Vision & Senses", "Creature Size & Reach"],
        ),
    ]
    assert len(groups) == len(expected)
    for group, (topic, label, card_count, table_count, headings) in zip(groups, expected):
        assert group.attrs.get("data-reference-topic") == topic
        group_label, = _inside(group, cls="gm-group-label")
        assert group_label.text == label
        assert len(_inside(group, cls="gm-ref-card")) == card_count
        assert len(_inside(group, tag="table", cls="gm-table")) == table_count
        assert [node.direct_text for node in _inside(group, tag="h2", cls="gm-ref-header")] == headings

    assert sum(len(_inside(group, cls="gm-ref-card")) for group in groups) == 14
    assert sum(len(_inside(group, tag="table", cls="gm-table")) for group in groups) == 12
    cards = [card for group in groups for card in _inside(group, cls="gm-ref-card")]
    assert {
        _inside(card, tag="h2", cls="gm-ref-header")[0].direct_text: _text_digest(card)
        for card in cards
    } == SCREEN_CARD_TEXT_SHA256

    category_buttons = _inside(content, tag="button", cls="gm-cat-btn")
    assert [(button.attrs.get("id"), button.text) for button in category_buttons] == [
        ("cat-all", "All"),
        ("cat-spells", "Spells"),
        ("cat-feats", "Feats"),
        ("cat-equipment", "Items"),
    ]
    assert ["active" in button.classes for button in category_buttons] == [True, False, False, False]
    comp_results, = _inside(content, id="comp-results")
    comp_detail, = _inside(content, id="comp-detail")
    assert "hidden" in comp_results.classes
    assert "hidden" in comp_detail.classes
    drawer, = _inside(content, id="checks-drawer")
    assert "checks-drawer" in drawer.classes
    handle, = _inside(drawer, tag="div", cls="checks-drawer-handle")
    assert "glow-pulse" in handle.classes
    assert handle.attrs.get("onclick") == "toggleChecksDrawer()"
    assert handle.attrs.get("title") == "Open/close checks reference (C)"
    drawer_tabs = _inside(drawer, tag="button", cls="checks-tab")
    assert [tab.attrs.get("data-tab") for tab in drawer_tabs] == [
        "situation",
        "skills",
        "saves",
        "dcs",
        "knowledge",
        "downtime",
    ]
    assert [tab.text for tab in drawer_tabs] == [
        "By Situation",
        "Skills",
        "Saves",
        "DCs & Flat Checks",
        "Recall Knowledge",
        "Downtime",
    ]
    assert ["active" in tab.classes for tab in drawer_tabs] == [True, False, False, False, False, False]
    assert len(_inside(drawer, cls="checks-body")) == 1
    assert len(_inside(content, cls="gm-cond")) == 1
    panes = _inside(content, cls="checks-pane")
    assert [pane.attrs.get("data-pane") for pane in panes] == [
        "situation",
        "skills",
        "saves",
        "dcs",
        "knowledge",
        "downtime",
    ]
    assert ["active" in pane.classes for pane in panes] == [True, False, False, False, False, False]
    assert {pane.attrs["data-pane"]: _text_digest(pane) for pane in panes} == SCREEN_PANE_TEXT_SHA256
