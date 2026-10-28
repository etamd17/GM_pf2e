#!/usr/bin/env python3
"""Capture and diff the behaviour hooks of rendered pages (design-system DS-00).

A design pass may change how a page looks, never what its scripts and tests
hold on to. This tool records those hooks for a set of routes and compares two
recordings:

    python tools/ui_inventory.py capture --out before.json
    ...edit templates/CSS...
    python tools/ui_inventory.py capture --out after.json
    python tools/ui_inventory.py diff before.json after.json

Recorded per route: HTTP status, ids, ``data-*`` keys with values, inline
``on*`` handlers, form contracts (action, method, every field's functional
attributes and options), ``role``/``aria-*``/``tabindex`` tuples *including the
element's full class string*, link targets, the ``<body>`` class string, and a
count of every class token. ``diff`` fails on any removal or changed value and
lists additions, which section 1.2 of docs/design/DESIGN_DIRECTION.md allows only
in narrow cases. Adding a class to an element that carries ARIA, or changing the
body class, therefore fails, because tests freeze those strings exactly.

Two capture modes:

* ``--mode html`` (default) renders through the Flask test client in-process,
  using a throwaway DATA_DIR seeded with the committed fixture characters and
  legacy-open GM access. It sees server-rendered markup only.
* ``--mode dom --base-url URL`` loads each route in headless Chromium against a
  running server, waits for scripts, then records the live DOM, once on load and
  once after selecting each ``[role=tab]`` control (``--click`` picks another
  selector, for example ``.tab-btn`` for the PF2e sheet). This sees ids and
  handlers that JavaScript builds (``content-<type>``, ``rhp-<id>``,
  ``cond-btn-<cond>``). It clicks nothing else; only pass ``--click`` a selector
  for controls that switch panels, never one that saves or rolls.

Hooks are keyed to their element (own id, else nearest ancestor id + tag).
Script order (external and inline, by content hash) and tab order are compared
exactly, an id that appears more often fails, and inline ``style`` display or
visibility, ``hidden``, ``open`` and ``inert`` are recorded per element. Editing an
inline ``<script>`` therefore fails: a design package must not change page JS.
``capture`` exits 1 if a route does not answer 200 (``--allow-status`` lists
expected exceptions), because an unrendered route is not checked at all.

Optional ``--hooks FILE`` (JSON ``{"route": ["visible text", ...]}``) also counts
copy that scripts or tests match on; a hook that disappears fails the diff.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
from collections import Counter
from html.parser import HTMLParser
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PCS = ("amadeus_l11.json", "gavin_l11.json", "goel_l10.json", "kyle_l10.json")

# PF2e routes reachable in legacy-open mode with the fixture party. Routes that
# need other systems or seeded campaigns are listed in docs/handoff/tasks/DS-00-inventory-diff-tool.md.
DEFAULT_ROUTES = (
    "/", "/gm", "/party", "/tracker", "/gmscreen", "/encounter_builder",
    "/generator", "/gm/loot", "/gm/calendar", "/gm/stats", "/gm/threads",
    "/gm/encounter_templates", "/gm/skill_challenge", "/gm/rest",
    "/player", "/player/sheet/Kyle", "/player/builder", "/player/levelup/Kyle",
)

# Multisets: removing one of two identical hooks is caught as well as removing
# the only one. Ordered lists are compared exactly.
MULTISET_CATEGORIES = ("ids", "data", "handlers", "aria", "controls", "state", "forms",
                       "links", "classes", "text_hooks")
ORDERED_CATEGORIES = ("script_order", "tab_order")
CONTROL_ATTRS = {
    "name", "type", "value", "min", "max", "step", "accept", "autocomplete",
    "placeholder", "required", "readonly", "disabled", "multiple", "checked",
    "pattern", "maxlength", "minlength", "list", "form", "formaction", "formmethod",
    "formenctype", "formnovalidate", "accesskey", "autofocus", "for",
}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
# Per-session values that differ between two captures of an unchanged page.
VOLATILE_VALUES = {"_csrf", "csrf_token"}


def _static(url: str) -> str:
    # The ?v= cache-buster changes with every edit to the file.
    return url.split("?", 1)[0] if url.startswith("/static/") else url


def _control_desc(tag: str, a: dict) -> str:
    parts = []
    for n, v in sorted(a.items()):
        if n in CONTROL_ATTRS:
            if n == "value" and a.get("name") in VOLATILE_VALUES:
                v = ""
            parts.append(f"{n}={v}")
    return tag + " " + " ".join(parts)


class _Parser(HTMLParser):
    """One pass over a document, collecting every frozen hook.

    Hooks are keyed to their element: its own id, else the nearest ancestor id
    plus the tag. Swapping two handlers inside one container is therefore not
    caught; swapping them between containers, or onto an element with an id, is.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.c = {cat: Counter() for cat in MULTISET_CATEGORIES}
        self.script_order: list[str] = []
        self.tab_order: list[str] = []
        self.body_class: str | None = None
        self.text: list[str] = []
        self._stack: list[tuple[str, str]] = []
        self._form: dict | None = None
        self._select: dict | None = None
        self._option: dict | None = None
        self._textarea: dict | None = None
        self._script: list[str] | None = None
        self._skip_text = 0

    def _anchor(self, tag, own_id):
        if own_id:
            return "#" + own_id
        for _t, anc in reversed(self._stack):
            if anc:
                return f"#{anc}>{tag}"
        return tag

    def handle_starttag(self, tag, attrs):
        a = {}
        for name, value in attrs:  # browsers keep the first of a duplicated attribute
            a.setdefault(name, "" if value is None else value)
        cls = a.get("class", "")
        anchor = self._anchor(tag, a.get("id"))
        c = self.c
        c["classes"].update(cls.split())
        if tag == "body":
            self.body_class = cls
        if a.get("id"):
            c["ids"][a["id"]] += 1
        semantic = []
        for name, value in a.items():
            if name.startswith("data-"):
                c["data"][f"{anchor} {name}={value}"] += 1
            elif name.startswith("on"):
                c["handlers"][f"{anchor} {name}={value}"] += 1
            if name in ("role", "tabindex") or name.startswith("aria-"):
                semantic.append(f"{name}={value}")
        if semantic:
            c["aria"][f"<{tag} id={a.get('id', '')!r} class={cls!r}> " + " ".join(sorted(semantic))] += 1
        tabindex = a.get("tabindex", "")
        if a.get("role") == "tab" or (tabindex.isdigit() and int(tabindex) > 0):
            self.tab_order.append(anchor)
        for flag in ("hidden", "open", "inert"):
            if flag in a:
                c["state"][f"{anchor} {flag}"] += 1
        for decl in a.get("style", "").split(";"):
            if ":" in decl:
                prop, val = (x.strip() for x in decl.split(":", 1))
                if prop.lower() in ("display", "visibility"):
                    c["state"][f"{anchor} style {prop.lower()}:{val}"] += 1
        for attr in ("href", "action", "formaction", "src"):
            if attr in a and tag in ("a", "form", "button", "input", "iframe", "link", "img", "script"):
                c["links"][f"{tag} {attr}={_static(a[attr])} rel={a.get('rel', '')} target={a.get('target', '')}"] += 1
        if tag == "script":
            flags = " ".join(f for f in ("defer", "async") if f in a)
            if "type" in a:
                flags += f" type={a['type']}"
            if "src" in a:
                self.script_order.append(f"src={_static(a['src'])} {flags}".strip())
            self._script = []
        if tag in ("script", "style"):
            self._skip_text += 1
        if tag in ("input", "select", "textarea", "button"):
            c["controls"][anchor + " " + _control_desc(tag, a)] += 1
        if tag == "form":
            if self._form is not None:
                self._flush_form()  # a nested <form> is ignored by browsers; keep what we have
            head = " ".join(f"{k}={a.get(k, d)}" for k, d in (
                ("action", ""), ("method", "get"), ("enctype", ""), ("target", ""), ("id", "")))
            self._form = {"head": head + (" novalidate" if "novalidate" in a else ""), "fields": []}
        elif self._form is not None and tag in ("input", "select", "textarea", "button"):
            field = {"desc": _control_desc(tag, a), "options": [], "text": []}
            self._form["fields"].append(field)
            if tag == "select":
                self._select = field
            if tag == "textarea":
                self._textarea = field
        if tag == "option" and self._select is not None:
            self._option = {"value": a.get("value"), "selected": "selected" in a, "text": []}
        if tag not in VOID:
            self._stack.append((tag, a.get("id", "")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "script" and self._script is not None:
            body = "".join(self._script).strip()
            if body:
                digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]
                self.script_order.append("inline sha1=" + digest)
            self._script = None
        if tag in ("script", "style") and self._skip_text:
            self._skip_text -= 1
        if tag == "option" and self._option is not None and self._select is not None:
            o = self._option
            value = o["value"] if o["value"] is not None else " ".join("".join(o["text"]).split())
            self._select["options"].append(value + ("*" if o["selected"] else ""))
            self._option = None
        if tag == "select":
            self._select = None
        if tag == "textarea":
            self._textarea = None
        if tag == "form" and self._form is not None:
            self._flush_form()
        if tag in VOID:
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def _flush_form(self):
        fields = " | ".join(
            f["desc"] + (" [" + ",".join(f["options"]) + "]" if f["options"] else "")
            + (" text=" + "".join(f["text"]).strip() if f["text"] else "")
            for f in self._form["fields"])
        self.c["forms"][self._form["head"] + " :: " + fields] += 1
        self._form = None

    def handle_data(self, data):
        if self._script is not None:
            self._script.append(data)
        if self._option is not None:
            self._option["text"].append(data)
        if self._textarea is not None:
            self._textarea["text"].append(data)
        if not self._skip_text:
            self.text.append(data)

    def close(self):
        super().close()
        if self._form is not None:  # an unclosed <form> still counts
            self._flush_form()


def inventory_html(html: str, text_hooks: list[str] | None = None) -> dict:
    """Return the JSON-ready inventory of one document."""

    p = _Parser()
    p.feed(html)
    p.close()
    text = " ".join("".join(p.text).split())
    p.c["text_hooks"] = Counter({h: text.count(h) for h in (text_hooks or [])})
    out = {"body_class": p.body_class, "script_order": p.script_order, "tab_order": p.tab_order}
    out.update({cat: dict(counter) for cat, counter in p.c.items()})
    return out


def diff_inventories(before: dict, after: dict) -> tuple[list[str], list[str]]:
    """Compare two capture files. Returns (failures, additions)."""

    failures: list[str] = []
    additions: list[str] = []
    b_routes, a_routes = before.get("routes", {}), after.get("routes", {})
    for route in sorted(set(b_routes) | set(a_routes)):
        if route not in a_routes:
            failures.append(f"{route}: route missing from the after capture")
            continue
        if route not in b_routes:
            additions.append(f"{route}: new route in the after capture")
            continue
        b, a = b_routes[route], a_routes[route]
        if b.get("status") != a.get("status"):
            failures.append(f"{route}: status {b.get('status')} -> {a.get('status')}")
            continue
        if b.get("body_class") != a.get("body_class"):
            failures.append(f"{route}: <body> class {b.get('body_class')!r} -> {a.get('body_class')!r}")
        for cat in ORDERED_CATEGORIES:
            bl, al = b.get(cat, []), a.get(cat, [])
            if bl != al:
                i = next((i for i, (x, y) in enumerate(zip(bl, al)) if x != y), min(len(bl), len(al)))
                was = bl[i] if i < len(bl) else "(end)"
                now = al[i] if i < len(al) else "(end)"
                failures.append(f"{route}: {cat} differs at position {i}: {was} -> {now}")
        for cat in MULTISET_CATEGORIES:
            bc, ac = Counter(b.get(cat, {})), Counter(a.get(cat, {}))
            for key in sorted(set(bc) | set(ac)):
                if ac[key] < bc[key]:
                    failures.append(f"{route}: {cat} removed x{bc[key] - ac[key]}: {key}")
                elif ac[key] > bc[key]:
                    if cat == "ids" and bc[key]:
                        # getElementById would now find a different element.
                        failures.append(f"{route}: ids duplicated: {key} x{bc[key]} -> x{ac[key]}")
                    else:
                        additions.append(f"{route}: {cat} added x{ac[key] - bc[key]}: {key}")
    return failures, additions


@contextlib.contextmanager
def _seeded_app():
    """Import the real app against a throwaway, fixture-seeded DATA_DIR."""

    with tempfile.TemporaryDirectory(prefix="gm-pf2e-ui-inventory-") as data_dir:
        party = Path(data_dir) / "party_data"
        party.mkdir()
        for name in FIXTURE_PCS:
            shutil.copy(REPO_ROOT / "tests" / "fixtures" / name, party / name)
        if "app" in sys.modules:
            raise RuntimeError("capture must run in a fresh process; app is already imported")
        # Mirrors tests/browser/conftest.py: everything the app writes stays in the
        # throwaway directory, and host/database settings from the shell are dropped.
        for key in ("DATABASE_URL", "PUBLIC_BASE_URL", "ALLOWED_HOSTS", "GUNICORN_CMD_ARGS"):
            os.environ.pop(key, None)
        os.environ.update({
            "APP_ENV": "development", "DATA_DIR": data_dir, "GM_PASSWORD": "",
            "SECRET_KEY": "ui-inventory-not-for-runtime",
            "SESSION_STATE_PATH": str(Path(data_dir) / "session_state.json"),
            # Same exemption the test suite uses: no persistence or probe threads.
            "PYTEST_CURRENT_TEST": "ui-inventory",
        })
        with contextlib.redirect_stdout(sys.stderr):
            module = importlib.import_module("app")
        yield module.app


def capture_html(routes, hooks) -> dict:
    out = {}
    with _seeded_app() as flask_app:
        client = flask_app.test_client()
        for route in routes:
            resp = client.get(route)
            entry = {"status": resp.status_code}
            if resp.status_code == 200 and "html" in (resp.content_type or ""):
                entry.update(inventory_html(resp.get_data(as_text=True), hooks.get(route)))
            out[route] = entry
    return out


def capture_dom(routes, hooks, base_url: str, click: str = '[role="tab"]') -> dict:
    from playwright.sync_api import sync_playwright

    out = {}
    with sync_playwright() as engine:
        browser = engine.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for route in routes:
            resp = page.goto(base_url.rstrip("/") + route, wait_until="load")
            page.wait_for_timeout(1500)
            status = resp.status if resp else 0
            out[route] = {"status": status}
            if status != 200:
                continue
            out[route].update(inventory_html(page.content(), hooks.get(route)))
            for i in range(page.locator(click).count()):
                # Fresh load per tab, so one tab's state never leaks into the next.
                page.goto(base_url.rstrip("/") + route, wait_until="load")
                page.wait_for_timeout(1500)
                tab = page.locator(click).nth(i)
                if not tab.is_visible():
                    continue
                key = f"{route}#tab{i}:{tab.get_attribute('id') or ''}"
                tab.click()
                page.wait_for_timeout(300)
                out[key] = {"status": status, **inventory_html(page.content(), hooks.get(route))}
        browser.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capture", help="record the hooks of each route")
    cap.add_argument("--out", required=True)
    cap.add_argument("--mode", choices=("html", "dom"), default="html")
    cap.add_argument("--base-url", help="running server for --mode dom")
    cap.add_argument("--routes", nargs="*", help="routes to capture (default: the PF2e set)")
    cap.add_argument("--hooks", help="JSON file: {route: [visible text, ...]}")
    cap.add_argument("--allow-status", nargs="*", default=[], metavar="ROUTE=CODE",
                     help="routes expected not to answer 200, e.g. /gm/rest=302")
    cap.add_argument("--click", default='[role="tab"]',
                     help="--mode dom: panel-switching controls to select one by one")
    d = sub.add_parser("diff", help="fail on any removed or changed hook")
    d.add_argument("before")
    d.add_argument("after")
    args = ap.parse_args(argv)

    if args.cmd == "capture":
        routes = args.routes or list(DEFAULT_ROUTES)
        hooks = json.loads(Path(args.hooks).read_text(encoding="utf-8")) if args.hooks else {}
        if args.mode == "dom":
            if not args.base_url:
                ap.error("--mode dom needs --base-url")
            data = capture_dom(routes, hooks, args.base_url, args.click)
        else:
            data = capture_html(routes, hooks)
        payload = {"mode": args.mode, "routes": data}
        Path(args.out).write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        allowed = dict(item.split("=", 1) for item in args.allow_status)
        bad = [f"{r}={e['status']}" for r, e in data.items()
               if e["status"] != 200 and allowed.get(r.split("#")[0]) != str(e["status"])]
        print(f"captured {len(data)} routes to {args.out}")
        if bad:
            # A route that does not render is not checked at all, so say so loudly.
            print("NOT CHECKED (unexpected status): " + ", ".join(bad))
            return 1
        return 0

    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    if before.get("mode") != after.get("mode"):
        print(f"capture modes differ ({before.get('mode')} vs {after.get('mode')}); compare like with like")
        return 2
    failures, additions = diff_inventories(before, after)
    for line in additions:
        print("ADDED   " + line)
    for line in failures:
        print("FAILED  " + line)
    print(f"{len(failures)} failure(s), {len(additions)} addition(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
