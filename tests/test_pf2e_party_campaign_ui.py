from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import flask
import pytest

import app


ROOT = Path(app.__file__).resolve().parent


# Frozen from the tracked templates before UI-09. Keep these literal: deriving a
# baseline from the template under test would let a regression update its own
# expected value. UI-09 additions are listed separately below.
PARTY_MAIN_IDS = (
    "skill-compare-toggle", "party-damage-toggle", "loot-distribute-toggle",
    "loot-distribute-flyout", "loot-pp", "loot-gp", "loot-sp", "loot-cp",
    "loot-items", "loot-target-row", "loot-target", "loot-preview",
    "party-damage-flyout", "pwd-amount", "pwd-type", "skill-matrix-overlay",
    "sm-title", "party-grid", "detail-modal", "detail-title", "detail-meta",
    "detail-close", "detail-body", "gm-toast-container",
    "pf2e-action-toast-container", "dice-widget", "dice-panel", "dice-total",
    "dice-breakdown", "custom-roll-form", "custom-roll-input", "screen-flash",
    "init-flourish", "boss-reveal", "br-epithet", "br-name", "br-level",
    "round-event-banner", "reb-round", "reb-title", "reb-text", "scene-mood",
    "qs-overlay", "qs-dialog", "qs-input", "qs-cats", "qs-results",
    "chat-widget", "chat-panel", "chat-messages", "chat-form", "chat-input",
    "chat-fab", "chat-unread", "kb-help-card",
)
PARTY_UI09_IDS = ("party-watch", "party-watch-1", "party-card-1", "party-card-2")
PARTY_MAIN_LINKS = (
    ("/gm", ""), ("/gm", ""), ("/party", ""), ("/tracker", ""),
    ("/gmscreen", ""), ("/generator", ""), ("/gm/loot", ""),
    ("/tracker", ""), ("/player/sheet/Kyle", "_blank"),
    ("/player/sheet/Go%27el%20Thrall", "_blank"),
)
PARTY_MAIN_HANDLERS = (
    ("onclick", "window._qsOpen && window._qsOpen()"),
    ("onclick", "openSkillMatrix()"),
    ("onclick", "toggleAllPartyDamage()"),
    ("onclick", "toggleLootDistribute()"),
    ("onclick", "document.getElementById('loot-distribute-flyout').classList.add('hidden')"),
    ("onclick", "applyLootDistribution()"),
    ("onclick", "applyAllPartyDamage('damage')"),
    ("onclick", "applyAllPartyDamage('heal')"),
    ("onclick", "if(event.target===this)closeSkillMatrix()"),
    ("onclick", "closeSkillMatrix()"),
    ("onclick", "event.stopPropagation()"),
    ("onclick", "toggleDicePanel()"),
    *(("onclick", f"quickRoll({sides})") for sides in (4, 6, 8, 10, 12, 20, 100)),
    ("onclick", "rollPlotDie()"),
    ("onsubmit", "event.preventDefault(); customRoll();"),
    ("onclick", "toggleDicePanel()"),
    ("onclick", "toggleChatPanel()"),
    ("onsubmit", "event.preventDefault(); sendChat();"),
    ("onclick", "toggleChatPanel()"),
    ("onclick", "event.stopPropagation()"),
    (
        "onclick",
        "document.getElementById('kb-help-card').classList.add('hidden'); "
        "document.getElementById('kb-help-card').style.display='none';",
    ),
)
PARTY_MAIN_ARIA = Counter({
    ("div", "skill-matrix-overlay", "sm-overlay",
     (("aria-labelledby", "sm-title"), ("aria-modal", "true"), ("role", "dialog"))): 1,
    ("button", "", "sm-x", (("aria-label", "Close"),)): 1,
    ("input", "", "hp-input hp-amount", (("aria-label", "Adjust amount"),)): 2,
    ("button", "", "hp-adjust-btn hp-damage-btn action-pill dmg",
     (("aria-label", "Apply damage"),)): 2,
    ("button", "", "hp-adjust-btn hp-heal-btn action-pill heal",
     (("aria-label", "Apply healing"),)): 2,
    ("button", "", "hero-adjust-btn hero-decrease",
     (("aria-label", "Decrease hero points"),)): 2,
    ("button", "", "hero-adjust-btn hero-increase",
     (("aria-label", "Award hero point"),)): 2,
    ("div", "", "pc-tabs", (("role", "tablist"),)): 2,
    ("button", "", "text-base-500 hover:text-base-200 text-lg leading-none",
     (("aria-label", "Close"),)): 1,
    ("div", "screen-flash", "no-print", (("aria-hidden", "true"),)): 1,
    ("div", "init-flourish", "init-flourish no-print", (("aria-hidden", "true"),)): 1,
    ("div", "boss-reveal", "boss-reveal no-print", (("aria-hidden", "true"),)): 1,
    ("div", "", "br-content", (("aria-label", "A foe is revealed"), ("role", "dialog"))): 1,
    ("div", "round-event-banner", "reb no-print",
     (("aria-hidden", "true"), ("role", "status"))): 1,
    ("div", "scene-mood", "no-print", (("aria-hidden", "true"),)): 1,
    ("button", "", "", (("aria-label", "Close"),)): 2,
})
PARTY_UI09_ARIA = Counter({
    ("section", "party-watch", "party-watch",
     (("aria-label", "Party attention"), ("aria-live", "polite"))): 1,
    ("span", "", "", (("aria-hidden", "true"),)): 1,
    ("div", "party-card-1", "pc-card is-dying", (("tabindex", "-1"),)): 1,
    ("div", "party-card-2", "pc-card", (("tabindex", "-1"),)): 1,
})
PARTY_MAIN_FORMS = (
    ("", "GET", (
        ("input", (("type", "text"), ("placeholder", "2d6+4")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("", "GET", (
        ("input", (("type", "text"), ("placeholder", "Type a message..."),
                   ("autocomplete", "off")), ()),
        ("button", (("type", "submit"),), ()),
    )),
)
PARTY_MAIN_JS_CLASS_COUNTS = Counter({
    "conditions-row": 2, "condition-pill": 3, "detail-link": 79,
    "exploration-pill": 2, "exploration-row": 2, "focus-pips": 2,
    "hero-adjust-btn": 4, "hero-decrease": 2, "hero-increase": 2,
    "hero-pips": 2, "hero-pip": 6, "hero-row": 2, "hp-amount": 2,
    "hp-bar-fill": 2, "hp-damage-btn": 2, "hp-display": 2,
    "hp-heal-btn": 2, "hp-row": 2, "pc-card": 2, "pc-tab": 10,
    "pc-tab-panel": 10, "slot-pip": 48,
})
PARTY_EXPECTED_DATA_KEYS = Counter({
    "data-detail-type": 79, "data-detail-name": 79, "data-detail-desc": 79,
    "data-detail-subtitle": 46, "data-slot-key": 42, "data-detail-rank": 33,
    "data-detail-tradition": 33, "data-spell-level": 17, "data-tab": 10,
    "data-panel": 10, "data-caster-idx": 7, "data-pc": 4,
    "data-max-hp": 4, "data-party-index": 3, "data-pc-name": 2,
    "data-cond-name": 3, "data-watch-id": 2, "data-current-hp": 2,
    "data-active-system": 1, "data-party-card-id": 1,
})

CAMPAIGN_MAIN_LINKS = (("/", ""), ("/me", ""))
CAMPAIGN_MAIN_HANDLERS = (
    ("onsubmit", "return confirm('Remove GM Two from The Long Eclipse?');"),
    ("onsubmit", "return confirm('Remove Player One from The Long Eclipse?');"),
    ("onsubmit", "return confirm('Remove Player Two from The Long Eclipse?');"),
    (
        "onsubmit",
        "return confirm('Switch The Long Eclipse to the selected system? This changes the rules, "
        "sheets, and tools for the whole table.');",
    ),
    (
        "onsubmit",
        'return confirm("Move The Long Eclipse and its data to trash? You can restore it for 30 days.");',
    ),
)
CAMPAIGN_SAFE_HANDLER_REWRITES = {
    value: value.replace("confirm('", 'confirm("').replace("?');", '?");')
    for event, value in CAMPAIGN_MAIN_HANDLERS[:3]
}
CAMPAIGN_SAFE_HANDLER_REWRITES[CAMPAIGN_MAIN_HANDLERS[3][1]] = (
    'return confirm("Switch The Long Eclipse to the selected system? This changes the rules, '
    'sheets, and tools for the whole table.");'
)
CAMPAIGN_EXPECTED_HANDLERS = tuple(
    (event, CAMPAIGN_SAFE_HANDLER_REWRITES.get(value, value))
    for event, value in CAMPAIGN_MAIN_HANDLERS
)
CAMPAIGN_MAIN_FORMS = (
    ("/campaign/eclipse/members/gm-two/role", "POST", (
        ("input", (("type", "hidden"), ("name", "role"), ("value", "player")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/members/gm-two/remove", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/members/player-one/role", "POST", (
        ("input", (("type", "hidden"), ("name", "role"), ("value", "gm")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/members/player-one/remove", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/members/player-two/role", "POST", (
        ("input", (("type", "hidden"), ("name", "role"), ("value", "gm")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/members/player-two/remove", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/invite", "POST", (
        ("input", (("type", "hidden"), ("name", "role"), ("value", "player")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/invite", "POST", (
        ("input", (("type", "hidden"), ("name", "role"), ("value", "gm")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/invite/DEMO-PLAYER/revoke", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/invite/DEMO-COGM/revoke", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/system", "POST", (
        ("select", (("name", "system"),),
         (("pf2e", True), ("cosmere", False), ("dnd5e", False))),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/eclipse/delete", "POST", (
        ("input", (("name", "confirm_name"), ("autocomplete", "off"),
                   ("placeholder", "The Long Eclipse")), ()),
        ("button", (("type", "submit"),), ()),
    )),
)
CAMPAIGN_UI09_IDS = (
    "campaign-management-title", "campaign-tab-members", "campaign-tab-characters",
    "campaign-tab-settings", "members", "campaign-members-title", "invites",
    "campaign-invites-title", "characters", "settings", "system",
    "campaign-system-title", "campaign-system-select", "danger",
    "campaign-danger-title", "campaign-delete-confirm",
)
CAMPAIGN_UI09_ARIA = Counter({
    ("main", "", "campaign-management", (("aria-labelledby", "campaign-management-title"),)): 1,
    ("div", "", "campaign-tabs",
     (("aria-label", "Campaign management sections"), ("role", "tablist"))): 1,
    ("button", "campaign-tab-members", "",
     (("aria-controls", "members"), ("aria-selected", "true"),
      ("role", "tab"), ("tabindex", "0"))): 1,
    ("button", "campaign-tab-characters", "",
     (("aria-controls", "characters"), ("aria-selected", "false"),
      ("role", "tab"), ("tabindex", "-1"))): 1,
    ("button", "campaign-tab-settings", "",
     (("aria-controls", "settings"), ("aria-selected", "false"),
      ("role", "tab"), ("tabindex", "-1"))): 1,
    ("div", "members", "campaign-panel campaign-tab-panel",
     (("aria-labelledby", "campaign-tab-members"), ("role", "tabpanel"),
      ("tabindex", "0"))): 1,
    ("section", "", "campaign-section campaign-members",
     (("aria-labelledby", "campaign-members-title"),)): 1,
    ("section", "invites", "campaign-section campaign-invites",
     (("aria-labelledby", "campaign-invites-title"),)): 1,
    ("section", "characters", "campaign-panel campaign-tab-panel campaign-section campaign-characters",
     (("aria-labelledby", "campaign-tab-characters"), ("role", "tabpanel"),
      ("tabindex", "0"))): 1,
    ("div", "settings", "campaign-panel campaign-tab-panel",
     (("aria-labelledby", "campaign-tab-settings"), ("role", "tabpanel"),
      ("tabindex", "0"))): 1,
    ("section", "system", "campaign-section settings-system",
     (("aria-labelledby", "campaign-system-title"),)): 1,
    ("section", "danger", "campaign-section danger-section",
     (("aria-labelledby", "campaign-danger-title"),)): 1,
})
CAMPAIGN_EXPECTED_DATA_KEYS = Counter({
    "data-link": 6, "data-campaign-tab": 3, "data-campaign-panel": 3,
    "data-campaign-tablist": 1, "data-campaign-tabs": 1,
})

ACCOUNT_GM_LINKS = (
    ("/", ""), ("/campaigns/new", ""),
    ("/campaign/campaign-one/invites", ""),
    ("/campaign/campaign-one/export", ""),
    ("/campaign/campaign-two/invites", ""),
    ("/campaign/campaign-two/export", ""), ("/logout", ""),
)
ACCOUNT_PLAYER_LINKS = (("/", ""), ("/campaigns/new", ""), ("/logout", ""))
ACCOUNT_GM_HANDLERS = (
    ("onchange", "restoreBackup(this)"),
    ("onclick", "backupNow(this)"),
    ("onsubmit", 'return purgeConfirm(this, "Old Table");'),
)
ACCOUNT_PLAYER_HANDLERS = (("onchange", "restoreBackup(this)"),)
ACCOUNT_GM_FORMS = (
    ("/campaign/campaign-one/activate", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/campaign-one/activate", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/campaign-two/activate", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/campaign-one/activate", "POST", (
        ("input", (("type", "hidden"), ("name", "then"),
                   ("value", "/player/sheet/Hero%201")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/campaign-two/activate", "POST", (
        ("input", (("type", "hidden"), ("name", "then"),
                   ("value", "/player/sheet/Hero%202")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/trashed-table/restore", "POST", (
        ("button", (("type", "submit"),), ()),
    )),
    ("/campaign/trashed-table/purge", "POST", (
        ("input", (("type", "hidden"), ("name", "confirm_name"), ("value", "")), ()),
        ("button", (("type", "submit"),), ()),
    )),
    ("/me/password", "POST", (
        ("input", (("name", "current_password"), ("type", "password"),
                   ("required", "")), ()),
        ("input", (("name", "new_password"), ("type", "password"),
                   ("required", ""), ("placeholder", "at least 6 characters")), ()),
        ("button", (("type", "submit"),), ()),
    )),
)
ACCOUNT_PLAYER_FORMS = (
    ACCOUNT_GM_FORMS[0], ACCOUNT_GM_FORMS[1], ACCOUNT_GM_FORMS[2],
    ACCOUNT_GM_FORMS[3], ACCOUNT_GM_FORMS[7],
)

PARTY_MAIN_JS_SELECTORS = {
    ".conditions-row", ".detail-link", ".exploration-pill", ".exploration-row",
    ".focus-pips .slot-pip", ".hero-adjust-btn", ".hero-decrease",
    ".hero-increase", ".hero-pips .hero-pip", ".hero-row", ".hp-amount",
    ".hp-bar-fill", ".hp-damage-btn", ".hp-display", ".hp-heal-btn", ".hp-row",
    ".pc-card", ".pc-card[data-pc-name]", ".pc-tab", ".pc-tab-panel", ".slot-pip",
    ".slot-pip[data-slot-key]", '[data-from=compendium]',
    'input[name="loot-mode"]:checked', '.pc-card[data-pc-name="',
    '[data-caster-idx="', '[data-spell-level="',
}
PARTY_MAIN_JS_IDS = {
    "detail-body", "detail-close", "detail-meta", "detail-modal", "detail-title",
    "loot-cp", "loot-distribute-flyout", "loot-distribute-toggle", "loot-gp",
    "loot-items", "loot-pp", "loot-preview", "loot-sp", "loot-target",
    "loot-target-row", "party-damage-flyout", "party-damage-toggle", "pwd-amount",
    "skill-matrix-overlay",
}
PARTY_MAIN_DATASET_KEYS = {
    "detailDesc", "detailName", "detailRank", "detailSubtitle", "detailTradition",
    "detailType", "from", "maxHp", "panel", "pc", "pcName", "slotKey", "tab",
}


class Inventory(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.onclicks: list[str] = []
        self.handlers: list[tuple[str, str, str, str]] = []
        self.aria: list[tuple[str, str, str, tuple[tuple[str, str], ...]]] = []
        self.class_counts: Counter[str] = Counter()
        self.data_attrs: list[tuple[str, str, str]] = []
        self.forms: list[tuple[str, str, tuple[str, ...]]] = []
        self.form_contracts: list[tuple] = []
        self._form: dict[str, object] | None = None
        self._select: dict[str, object] | None = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = tuple((attributes.get("class") or "").split())
        self.class_counts.update(classes)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "a" and attributes.get("href"):
            self.hrefs.append(attributes["href"])
            self.links.append((attributes["href"], attributes.get("target", "")))
        if attributes.get("onclick"):
            self.onclicks.append(attributes["onclick"])
        key = (
            attributes.get("id")
            or attributes.get("name")
            or attributes.get("action")
            or attributes.get("href")
            or " ".join(classes)
        )
        for name, value in attrs:
            if name.startswith("on"):
                self.handlers.append((tag, key, name, value or ""))
            if name.startswith("data-"):
                self.data_attrs.append((tag, name, value or ""))
        semantic = tuple(sorted(
            (name, value or "")
            for name, value in attrs
            if name == "role" or name == "tabindex" or name.startswith("aria-")
        ))
        if semantic:
            self.aria.append((
                tag,
                attributes.get("id", ""),
                attributes.get("class", ""),
                semantic,
            ))
        if tag == "form":
            self._form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "GET").upper(),
                "names": [],
                "fields": [],
            }
        elif self._form is not None and tag in {"input", "select", "textarea", "button"}:
            if attributes.get("name"):
                self._form["names"].append(attributes["name"])
            functional = tuple(
                (name, "" if value is None else value)
                for name, value in attrs
                if name in {
                    "name", "type", "value", "min", "max", "step", "accept",
                    "autocomplete", "placeholder", "required", "readonly", "disabled",
                    "multiple", "checked",
                }
            )
            field = {"tag": tag, "attrs": functional, "options": []}
            self._form["fields"].append(field)
            if tag == "select":
                self._select = field
        elif tag == "option" and self._select is not None:
            self._select["options"].append((
                attributes.get("value", ""),
                "selected" in attributes,
            ))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        if tag == "form" and self._form is not None:
            self.forms.append((
                self._form["action"],
                self._form["method"],
                tuple(self._form["names"]),
            ))
            self.form_contracts.append((
                self._form["action"],
                self._form["method"],
                tuple(
                    (field["tag"], field["attrs"], tuple(field["options"]))
                    for field in self._form["fields"]
                ),
            ))
            self._form = None


def _handler_values(inventory: Inventory) -> Counter:
    return Counter((event, value) for _tag, _key, event, value in inventory.handlers)


def _data_key_counts(inventory: Inventory) -> Counter:
    return Counter(name for _tag, name, _value in inventory.data_attrs)


def _js_selector_literals(source: str) -> set[str]:
    return {
        match.group(2)
        for match in re.finditer(
            r"(?:querySelector|querySelectorAll|closest)\(\s*(['\"])(.*?)\1",
            source,
        )
    }


def _js_id_literals(source: str) -> set[str]:
    return {
        match.group(2)
        for match in re.finditer(r"getElementById\(\s*(['\"])(.*?)\1", source)
    }


def _js_dataset_keys(source: str) -> set[str]:
    return set(re.findall(r"\.dataset\.([A-Za-z_$][\w$]*)", source))


def _render_manage(
    *, system="pf2e", campaign_name="The Long Eclipse",
    members=None, rows=None, invites=None, gm_total=2,
):
    members = members if members is not None else [
        {"name": "Owner", "is_self": True, "role": "gm", "user_id": "owner"},
        {"name": "GM Two", "is_self": False, "role": "gm", "user_id": "gm-two"},
        {"name": "Player One", "is_self": False, "role": "player", "user_id": "player-one"},
        {"name": "Player Two", "is_self": False, "role": "player", "user_id": "player-two"},
    ]
    rows = rows if rows is not None else [
        {"id": "hero-one", "name": "Hero One", "claimed": False,
         "code": "HERO-ONE", "kind": system},
        {"id": "hero-two", "name": "Hero Two", "claimed": True,
         "owner": "Player One", "kind": system},
    ]
    invites = invites if invites is not None else [
        {"role": "player", "code": "DEMO-PLAYER"},
        {"role": "gm", "code": "DEMO-COGM"},
    ]
    with app.app.test_request_context("/campaign/eclipse/invites"):
        return flask.render_template(
            "campaign_invites.html",
            campaign={
                "id": "eclipse",
                "name": campaign_name,
                "system": system,
                "members": [
                    {"user_id": "owner", "role": "gm"},
                    {"user_id": "gm-two", "role": "gm"},
                    {"user_id": "player-one", "role": "player"},
                    {"user_id": "player-two", "role": "player"},
                ],
            },
            members=members,
            rows=rows,
            open_invites=invites,
            gm_total=gm_total,
            trash_ttl_days=30,
        )


def _render_account(*, gm: bool):
    campaigns = [
        {"id": "campaign-one", "name": "First Table", "system": "pf2e",
         "members": [{"user_id": "user", "role": "gm" if gm else "player"}]},
        {"id": "campaign-two", "name": "Second Table", "system": "pf2e",
         "members": [{"user_id": "user", "role": "gm" if gm else "player"}]},
    ]
    character_count = 2 if gm else 1
    characters = [
        {"name": f"Hero {index + 1}", "accent": "#e0b65a", "subtitle": "Fighter",
         "stats": [("HP", 20), ("AC", 18)], "campaign_name": campaigns[index]["name"],
         "system": "pf2e", "is_live": False, "campaign_id": campaigns[index]["id"],
         "sheet_url": f"/player/sheet/Hero%20{index + 1}"}
        for index in range(character_count)
    ]
    trashed = ([{"id": "trashed-table", "name": "Old Table", "system": "pf2e",
                 "members": [{"user_id": "user", "role": "gm"}]}] if gm else [])
    with app.app.test_request_context("/me"):
        return flask.render_template(
            "account_home.html",
            user={"id": "user", "username": "user", "display_name": "Table User",
                  "is_admin": False},
            campaigns=campaigns,
            gm_campaign_ids=[c["id"] for c in campaigns] if gm else [],
            characters=characters,
            active_campaign_id="campaign-one",
            live_campaign_id=None,
            last_campaign={"id": "campaign-one", "name": "First Table",
                           "system": "pf2e", "is_gm": gm},
            trashed_campaigns=trashed,
            trash_ttl_days=30,
            last_backup_at=1700000000 if gm else None,
            workflow_enabled=False,
            workflow_drafts=[],
            workflow_targets=[],
            workflow_error=None,
            active_system="pf2e",
        )


def _fixture_character(filename: str):
    path = ROOT / "tests" / "fixtures" / filename
    return app.Character(json.loads(path.read_text(encoding="utf-8")), file_path=str(path))


def _render_party(monkeypatch):
    low = _fixture_character("kyle_l10.json")
    low.current_hp = max(1, low.hp // 3)
    low.conditions = {"dying": 1, "frightened": 2, "prone": True}
    healthy = _fixture_character("goel_l10.json")
    healthy.current_hp = healthy.hp
    healthy.conditions = {}
    monkeypatch.setattr(app, "_sync_party_from_disk", lambda: None)
    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(app, "_is_gm", lambda: True)
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    monkeypatch.setattr(app, "PARTY_LIBRARY", {low.name: low, healthy.name: healthy})
    response = app.app.test_client().get("/party")
    assert response.status_code == 200
    return response.get_data(as_text=True), low, healthy


def test_party_attention_links_to_stable_cards_and_preserves_controls(monkeypatch):
    html, low, healthy = _render_party(monkeypatch)
    inventory = Inventory(html)
    watch = re.search(
        r'<section[^>]*class="party-watch"[^>]*aria-label="Party attention"[^>]*>(.*?)</section>',
        html,
        re.S,
    )
    assert watch
    assert low.name in watch.group(1)
    assert healthy.name not in watch.group(1)
    assert 'href="#party-card-1"' in watch.group(1)
    assert "Dying 1" in watch.group(1)
    assert "Frightened 2" in watch.group(1)
    assert "Prone" in watch.group(1)
    assert "Prone True" not in watch.group(1)
    assert "party-card-1" in inventory.ids and "party-card-2" in inventory.ids
    assert len(inventory.ids) == len(set(inventory.ids))
    assert Counter(inventory.ids) == Counter(PARTY_MAIN_IDS + PARTY_UI09_IDS)
    assert Counter(inventory.links) == Counter(PARTY_MAIN_LINKS + (("#party-card-1", ""),))
    assert _handler_values(inventory) == Counter(PARTY_MAIN_HANDLERS)
    assert Counter(inventory.form_contracts) == Counter(PARTY_MAIN_FORMS)
    assert Counter(inventory.aria) == PARTY_MAIN_ARIA + PARTY_UI09_ARIA
    assert Counter({
        name: inventory.class_counts[name]
        for name in PARTY_MAIN_JS_CLASS_COUNTS
    }) == PARTY_MAIN_JS_CLASS_COUNTS
    assert inventory.class_counts["party-watch-item"] == 1
    assert _data_key_counts(inventory) == PARTY_EXPECTED_DATA_KEYS

    preserved_ids = {
        "skill-compare-toggle", "party-damage-toggle", "loot-distribute-toggle",
        "loot-distribute-flyout", "loot-pp", "loot-gp", "loot-sp", "loot-cp",
        "loot-items", "loot-target-row", "loot-target", "loot-preview",
        "party-damage-flyout", "pwd-amount", "pwd-type", "skill-matrix-overlay",
        "sm-title", "party-grid", "detail-modal", "detail-title", "detail-meta",
        "detail-close", "detail-body",
    }
    assert preserved_ids <= set(inventory.ids)
    assert Counter(inventory.onclicks) >= Counter({
        "openSkillMatrix()": 1,
        "toggleAllPartyDamage()": 1,
        "toggleLootDistribute()": 1,
        "applyLootDistribution()": 1,
        "applyAllPartyDamage('damage')": 1,
        "applyAllPartyDamage('heal')": 1,
        "closeSkillMatrix()": 1,
    })
    assert "/tracker" in inventory.hrefs
    assert html.count('class="ability-chip"') == 12
    assert "Cantrip" in html and "Rank " in html


def test_party_attention_is_refreshed_by_live_hp_and_condition_updates():
    source = (ROOT / "templates" / "party_view.html").read_text(encoding="utf-8")
    assert PARTY_MAIN_JS_SELECTORS <= _js_selector_literals(source)
    assert PARTY_MAIN_JS_IDS <= _js_id_literals(source)
    assert PARTY_MAIN_DATASET_KEYS <= _js_dataset_keys(source)
    for new_hook in (
        ".party-watch-item", ".conditions-row .condition-pill",
        "partyCardId", "partyIndex", "watchId", "currentHp",
    ):
        assert new_hook in source
    assert "function refreshPartyAttention(card)" in source
    hp_body = re.search(r"function updateCardHP\(.*?(?=\n    const PV_SEV_RED)", source, re.S)
    condition_body = re.search(r"function updateConditions\(.*?(?=\n    function updateFocus)", source, re.S)
    assert hp_body and "refreshPartyAttention(card)" in hp_body.group(0)
    assert condition_body and "refreshPartyAttention(card)" in condition_body.group(0)
    hidden_rule = re.search(
        r"\.party-watch\[hidden\]\s*,\s*\.party-watch-item\[hidden\]\s*\{(?P<body>[^}]*)\}",
        source,
        re.S,
    )
    assert hidden_rule and re.search(r"display:\s*none", hidden_rule.group("body"))
    mobile_rule = re.search(
        r"#loot-distribute-flyout\s*,\s*#party-damage-flyout\s*\{(?P<body>[^}]*)\}",
        source,
        re.S,
    )
    assert mobile_rule
    assert re.search(r"width:\s*calc\(100vw\s*-\s*2rem\)", mobile_rule.group("body"))


def test_campaign_management_keeps_exact_form_contract_and_selected_tabs():
    html = _render_manage()
    inventory = Inventory(html)
    assert Counter(inventory.ids) == Counter(CAMPAIGN_UI09_IDS)
    assert Counter(inventory.links) == Counter(CAMPAIGN_MAIN_LINKS)
    assert _handler_values(inventory) == Counter(CAMPAIGN_EXPECTED_HANDLERS)
    assert Counter(inventory.form_contracts) == Counter(CAMPAIGN_MAIN_FORMS)
    assert Counter(inventory.aria) == CAMPAIGN_UI09_ARIA
    assert inventory.class_counts["copy-btn"] == 3
    assert _data_key_counts(inventory) == CAMPAIGN_EXPECTED_DATA_KEYS
    assert inventory.forms == [
        ("/campaign/eclipse/members/gm-two/role", "POST", ("role",)),
        ("/campaign/eclipse/members/gm-two/remove", "POST", ()),
        ("/campaign/eclipse/members/player-one/role", "POST", ("role",)),
        ("/campaign/eclipse/members/player-one/remove", "POST", ()),
        ("/campaign/eclipse/members/player-two/role", "POST", ("role",)),
        ("/campaign/eclipse/members/player-two/remove", "POST", ()),
        ("/campaign/eclipse/invite", "POST", ("role",)),
        ("/campaign/eclipse/invite", "POST", ("role",)),
        ("/campaign/eclipse/invite/DEMO-PLAYER/revoke", "POST", ()),
        ("/campaign/eclipse/invite/DEMO-COGM/revoke", "POST", ()),
        ("/campaign/eclipse/system", "POST", ("system",)),
        ("/campaign/eclipse/delete", "POST", ("confirm_name",)),
    ]
    assert len(re.findall(r'<button\b[^>]*\brole="tab"', html)) == 3
    assert len(re.findall(r'<(?:div|section)\b[^>]*\brole="tabpanel"', html)) == 3
    for anchor in ("members", "invites", "characters", "system", "danger"):
        assert f'id="{anchor}"' in html
    assert "4 members" in html and "2 GMs" in html
    assert "2 characters" in html and "2 open invitations" in html
    assert "handed off" not in html.lower() and "Hand off" not in html
    assert "/release" not in html


def test_campaign_management_empty_and_last_gm_states_keep_complete_tabs():
    empty = _render_manage(members=[], rows=[], invites=[], gm_total=1)
    assert len(re.findall(r'<button\b[^>]*\brole="tab"', empty)) == 3
    assert len(re.findall(r'<(?:div|section)\b[^>]*\brole="tabpanel"', empty)) == 3
    assert "No members yet" in empty
    assert "No characters are available for this campaign yet." in empty

    last_gm = _render_manage(
        members=[{"name": "Only GM", "is_self": False, "role": "gm", "user_id": "only-gm"}],
        rows=[],
        invites=[],
        gm_total=1,
    )
    forms = Inventory(last_gm).forms
    assert not any(action.endswith("/only-gm/role") for action, _method, _fields in forms)
    assert "Make player" not in last_gm


def test_campaign_management_retains_cosmere_only_release_control():
    html = _render_manage(
        system="cosmere",
        members=[],
        invites=[],
        rows=[{"id": "cosmere-hero", "name": "Radiant", "claimed": True,
               "owner": "Player", "kind": "cosmere"}],
        gm_total=1,
    )
    assert '/cosmere/pc/cosmere-hero/release' in html
    assert "Hand off" in html


def test_campaign_confirmation_handlers_escape_apostrophes():
    html = _render_manage(
        system="cosmere",
        campaign_name="King's Road",
        members=[
            {"name": "GM O'Neil", "is_self": False, "role": "gm", "user_id": "gm-one"},
        ],
        rows=[{
            "id": "radiant-echo", "name": "Radiant's Echo", "claimed": True,
            "owner": "Player", "kind": "cosmere",
        }],
        invites=[],
        gm_total=2,
    )
    handlers = _handler_values(Inventory(html))
    expected = Counter({
        (
            "onsubmit",
            'return confirm("Remove GM O\\u0027Neil from King\\u0027s Road?");',
        ): 1,
        (
            "onsubmit",
            'return confirm("Hand off Radiant\\u0027s Echo? This frees their join link so a player can claim it.");',
        ): 1,
        (
            "onsubmit",
            'return confirm("Switch King\\u0027s Road to the selected system? This changes the rules, sheets, and tools for the whole table.");',
        ): 1,
        (
            "onsubmit",
            'return confirm("Move King\\u0027s Road and its data to trash? You can restore it for 30 days.");',
        ): 1,
    })
    assert handlers >= expected


def test_campaign_tab_source_has_keyboard_hash_and_hidden_contracts():
    source = (ROOT / "templates" / "campaign_invites.html").read_text(encoding="utf-8")
    assert ".copy-btn" in _js_selector_literals(source)
    for token in (
        "data-campaign-tabs", "ArrowLeft", "ArrowRight", "Home", "End",
        "aria-selected", "tabIndex", "hashchange", "members: 'members'",
        "invites: 'members'", "characters: 'characters'", "settings: 'settings'",
        "system: 'settings'", "danger: 'settings'",
    ):
        assert token in source
    hidden_rule = re.search(
        r"\.campaign-management-page\s+\.campaign-tab-panel\[hidden\]\s*,\s*"
        r"\.campaign-management-page\s+\[hidden\]\s*\{(?P<body>[^}]*)\}",
        source,
        re.S,
    )
    assert hidden_rule and re.search(
        r"display:\s*none\s*!important", hidden_rule.group("body")
    )


def test_campaign_tab_controller_runtime():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the campaign tab behavior test")
    source = (ROOT / "templates" / "campaign_invites.html").read_text(encoding="utf-8")
    match = re.search(r"<script data-campaign-tabs>(.*?)</script>", source, re.S)
    assert match
    harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => {
  const {code} = JSON.parse(input);
  let focused = null;
  const windowListeners = {};
  function element(id, attributes) {
    return {id, attrs:{...attributes}, hidden:false, tabIndex:0, listeners:{}, scrolled:false,
      getAttribute(name){return this.attrs[name] ?? null;},
      setAttribute(name,value){this.attrs[name]=String(value);},
      addEventListener(name,fn){this.listeners[name]=fn;},
      focus(){focused=this;},
      scrollIntoView(){this.scrolled=true;}};
  }
  const tabs = ['members','characters','settings'].map(key =>
    element('campaign-tab-'+key, {'role':'tab','data-campaign-tab':key}));
  const panels = ['members','characters','settings'].map(key =>
    element(key, {'data-campaign-panel':key}));
  const sections = {
    members:panels[0], characters:panels[1], settings:panels[2],
    invites:element('invites',{}), system:element('system',{}), danger:element('danger',{})
  };
  const tablist = {querySelectorAll(selector){assert.equal(selector,'[role="tab"]');return tabs;}};
  const location = {hash:'#danger'};
  const context = {
    document:{
      querySelector(selector){return selector==='[data-campaign-tablist]'?tablist:null;},
      querySelectorAll(selector){return selector==='[data-campaign-panel]'?panels:[];},
      getElementById(id){return sections[id] || null;}
    },
    window:{
      location,
      history:{replaceState(_state,_title,hash){location.hash=hash;}},
      requestAnimationFrame(fn){fn();},
      addEventListener(name,fn){windowListeners[name]=fn;}
    }
  };
  vm.runInNewContext(code, context);
  function selected(expected) {
    tabs.forEach((tab,index) => {
      const yes = tab.attrs['data-campaign-tab'] === expected;
      assert.equal(tab.attrs['aria-selected'], String(yes));
      assert.equal(tab.tabIndex, yes ? 0 : -1);
      assert.equal(panels[index].hidden, !yes);
    });
  }
  selected('settings');
  assert.equal(sections.danger.scrolled,true);
  tabs[0].listeners.click();
  selected('members'); assert.equal(location.hash,'#members');
  function key(index,key,expected) {
    let prevented=false;
    tabs[index].listeners.keydown({key,preventDefault(){prevented=true;}});
    assert.equal(prevented,true); selected(expected);
    assert.equal(focused,tabs.find(tab => tab.attrs['data-campaign-tab']===expected));
  }
  key(0,'ArrowLeft','settings');
  key(2,'ArrowRight','members');
  key(0,'End','settings');
  key(2,'Home','members');
  let prevented=false;
  tabs[0].listeners.keydown({key:'Tab',preventDefault(){prevented=true;}});
  assert.equal(prevented,false);
  location.hash='#invites'; windowListeners.hashchange();
  selected('members'); assert.equal(sections.invites.scrolled,true);
  location.hash='#characters'; windowListeners.hashchange();
  selected('characters'); assert.equal(sections.characters.scrolled,true);
  console.log('campaign tabs passed');
});
'''
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"code": match.group(1)}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "campaign tabs passed"


def test_campaign_actions_redirect_to_the_panel_that_contains_their_result(monkeypatch):
    campaign = {"id": "eclipse", "name": "The Long Eclipse", "system": "pf2e"}
    monkeypatch.setattr(
        app, "_require_campaign_gm",
        lambda _cid: ({"id": "owner", "is_admin": False}, campaign),
    )

    with app.app.test_request_context(
        "/campaign/eclipse/system", method="POST", data={"system": "pf2e"}
    ):
        response = app.campaign_set_system.__wrapped__("eclipse")
    assert response.headers["Location"] == "/campaign/eclipse/invites?system_set=pf2e#system"

    with app.app.test_request_context(
        "/campaign/eclipse/system", method="POST", data={"system": "starfinder"}
    ):
        response = app.campaign_set_system.__wrapped__("eclipse")
    assert response.headers["Location"] == "/campaign/eclipse/invites?system_error=1#system"

    with app.app.test_request_context(
        "/campaign/eclipse/delete", method="POST", data={"confirm_name": "wrong"}
    ):
        response = app.campaign_delete.__wrapped__("eclipse")
    assert response.headers["Location"] == "/campaign/eclipse/invites?delete_error=1#danger"

    monkeypatch.setattr(app, "_is_gm", lambda: True)
    monkeypatch.setattr(
        app, "_load_cosmere_pc",
        lambda _pid: {"id": "radiant", "campaign_id": "eclipse", "owner_user_id": None},
    )
    monkeypatch.setattr(app._persistence_runtime, "sql_enabled", lambda: False)
    monkeypatch.setattr(app, "_save_cosmere_pc", lambda _doc: None)
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", "eclipse")
    with app.app.test_request_context("/cosmere/pc/radiant/release", method="POST"):
        response = app.cosmere_pc_release("radiant")
    assert response.headers["Location"] == "/campaign/eclipse/invites#characters"


def test_my_table_keeps_gm_and_player_form_and_role_contracts():
    gm_html = _render_account(gm=True)
    gm_inventory = Inventory(gm_html)
    assert Counter(gm_inventory.ids) == Counter({"bk-when": 1})
    assert Counter(gm_inventory.links) == Counter(ACCOUNT_GM_LINKS)
    assert _handler_values(gm_inventory) == Counter(ACCOUNT_GM_HANDLERS)
    assert Counter(gm_inventory.form_contracts) == Counter(ACCOUNT_GM_FORMS)
    assert not gm_inventory.aria
    assert _data_key_counts(gm_inventory) == Counter({"data-ts": 1})
    assert len(gm_inventory.forms) == 8
    assert Counter(gm_inventory.forms) == Counter({
        ("/campaign/campaign-one/activate", "POST", ()): 2,
        ("/campaign/campaign-two/activate", "POST", ()): 1,
        ("/campaign/campaign-one/activate", "POST", ("then",)): 1,
        ("/campaign/campaign-two/activate", "POST", ("then",)): 1,
        ("/campaign/trashed-table/restore", "POST", ()): 1,
        ("/campaign/trashed-table/purge", "POST", ("confirm_name",)): 1,
        ("/me/password", "POST", ("current_password", "new_password")): 1,
    })
    assert "/campaign/campaign-one/invites" in gm_inventory.hrefs
    assert "/campaign/campaign-one/export" in gm_inventory.hrefs
    assert "backupNow(this)" in gm_inventory.onclicks

    player_html = _render_account(gm=False)
    player_inventory = Inventory(player_html)
    assert not player_inventory.ids
    assert Counter(player_inventory.links) == Counter(ACCOUNT_PLAYER_LINKS)
    assert _handler_values(player_inventory) == Counter(ACCOUNT_PLAYER_HANDLERS)
    assert Counter(player_inventory.form_contracts) == Counter(ACCOUNT_PLAYER_FORMS)
    assert not player_inventory.aria
    assert not player_inventory.data_attrs
    assert len(player_inventory.forms) == 5
    assert not any(href.endswith("/invites") or href.endswith("/export")
                   for href in player_inventory.hrefs)
    assert "backupNow(this)" not in player_inventory.onclicks
    assert 'class="wide account-home"' in player_html


def test_my_table_revision_a_styles_are_scoped_without_reordering_content():
    source = (ROOT / "templates" / "account_home.html").read_text(encoding="utf-8")
    assert "input[name=confirm_name]" in _js_selector_literals(source)
    assert "bk-when" in _js_id_literals(source)
    assert "ts" in _js_dataset_keys(source)
    assert "wide account-home" in source
    assert ".account-home .cc-cover{height:34px;" in source
    assert '.account-home [aria-label="Your private drafts"]' in source
    campaigns = source.index("Your Campaigns")
    drafts = source.index("_character_draft_list.html")
    characters = source.index("Your Characters")
    trash = source.index("Recently deleted")
    account = source.index("Account settings")
    assert campaigns < drafts < characters < trash < account


def test_party_route_redirects_other_systems_without_loading_pf2e(monkeypatch):
    syncs = []
    monkeypatch.setattr(app, "_sync_party_from_disk", lambda: syncs.append("pf2e"))
    monkeypatch.setattr(app, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app, "_account_mode", lambda: False)
    monkeypatch.setattr(app, "_is_gm", lambda: True)
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    client = app.app.test_client()

    monkeypatch.setattr(app, "_active_system", lambda: "cosmere")
    response = client.get("/party", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert response.headers["Location"].endswith("/cosmere/pcs")
    assert syncs == []

    monkeypatch.setattr(app, "_active_system", lambda: "dnd5e")
    response = client.get("/party", follow_redirects=False)
    assert response.status_code in (301, 302)
    assert response.headers["Location"].endswith("/dnd5e/characters")
    assert syncs == []

    monkeypatch.setattr(app, "_active_system", lambda: "pf2e")
    response = client.get("/party", follow_redirects=False)
    assert response.status_code == 200
    assert syncs == ["pf2e"]
