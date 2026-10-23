"""The VTT browser harness (M6, handoff 053 item 5).

A disposable app server with the VTT on, seeded with a GM account, a campaign,
two VTT scenes and one legacy /map scene with a background image. A GM browser
context and a table browser context (separate cookie jars, like a laptop and a
TV) drive the real built client. The canvas is read through the test-only
render probe (``window.__vttProbe``, enabled by localStorage ``vtt.probe``),
and screenshots are kept for a failing test.

Needs Playwright (requirements-browser.txt) with Chromium, and the client
built (``npm run build`` in frontend/vtt). Uses SQLite by default; set
``VTT_E2E_DATABASE_URL`` to a disposable PostgreSQL database to run it there.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DIST = ROOT / "frontend" / "vtt" / "dist"

pytestmark = pytest.mark.browser


def _free_port() -> int:
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SEED = r'''
import io, json, os
from PIL import Image
from core import auth, campaigns, storage
from core import scenes as legacy
from core.persistence.database import Database
from vtt.storage import writes as W

gm = auth.create_first_admin("gm", os.environ["E2E_PASSWORD"])
campaign = campaigns.create_campaign("VTT harness", "pf2e", gm["id"])
cid = campaign["id"]
storage.set_live_campaign_id(cid)

# U-4: a real, campaign-owned party character from a committed test export.
# Keep it out of the encounter so the existing turn story is unchanged.
with open("tests/fixtures/goel_l10.json", encoding="utf-8") as handle:
    scout = json.load(handle)
scout["build"]["name"] = "Table Scout"
scout["build"]["size"] = 2
scout["build"]["sizeName"] = "Medium"
scout = storage.wrap_character(storage.new_id(), cid, "pf2e", scout, owner_user_id=gm["id"])
os.makedirs(storage.party_dir(cid), exist_ok=True)
with open(os.path.join(storage.party_dir(cid), "table_scout.json"), "w", encoding="utf-8") as handle:
    json.dump(scout, handle)

db = Database(os.environ["DATABASE_URL"])
W.create_scene(db, cid, "scene_hall", name="Hall", width=1280, height=960, published=True)
W.upsert_entity(db, cid, "scene_hall", "token", "tok_hero",
                {"x": 224, "y": 224, "size": 1, "label": "Valeros", "color": 0x4F8A62, "artAssetId": None,
                 "actorId": None, "locked": False, "controllerIds": []})
# A wall down the middle (x = 640): revealing the hero's room uncovers only the
# left half, 10 of 20 columns.
W.upsert_entity(db, cid, "scene_hall", "wall", "wall_mid", {"x1": 640, "y1": 0, "x2": 640, "y2": 960, "doorState": "none"})
W.create_scene(db, cid, "scene_docks", name="Docks", width=1600, height=900, published=True)
# E-6: a lit scene. One wall down the middle and one torch on the left: the
# table is dark except the torch's light, which the wall stops.
W.create_scene(db, cid, "scene_dark", name="Dark Vault", width=1280, height=960, published=True,
               lighting_enabled=True, default_vision=0)
W.upsert_entity(db, cid, "scene_dark", "wall", "wall_vault", {"x1": 640, "y1": 0, "x2": 640, "y2": 960, "doorState": "none"})
W.upsert_entity(db, cid, "scene_dark", "wall", "door_vault", {"x1": 0, "y1": 480, "x2": 128, "y2": 480, "doorState": "open"})
W.upsert_entity(db, cid, "scene_dark", "light", "torch_vault", {"x": 320, "y": 320, "radius": 128, "color": 0xFFD580})
W.set_table_pointer(db, cid, "scene_hall")

# A legacy /map scene with a background image file, for the import.
old = legacy.create_scene(cid, "Old Crypt", width=1400, height=1000)
image = io.BytesIO()
Image.new("RGB", (140, 100), (120, 40, 160)).save(image, "PNG")
os.makedirs(storage.scene_assets_dir(cid), exist_ok=True)
with open(os.path.join(storage.scene_assets_dir(cid), old["id"] + ".png"), "wb") as handle:
    handle.write(image.getvalue())
old["background"] = {"filename": old["id"] + ".png"}
legacy.add_token(old, name="Skeleton", x=210, y=210)
old.setdefault("settings", {})["dynamic_lighting"] = True     # E-6: comes across
legacy.save_scene(cid, old)
empty = campaigns.create_campaign("Empty VTT harness", "pf2e", gm["id"])
print("E2E_RESULT=" + json.dumps({"cid": cid, "legacy": old["id"], "empty": empty["id"]}))
'''


class VttSite:
    def __init__(self, root: Path):
        self.root = root
        self.port = _free_port()
        # localhost (not 127.0.0.1): the pairing route and the browser both
        # treat it as a secure context, so the table cookie works over HTTP.
        self.url = f"http://localhost:{self.port}"
        self.password = secrets.token_urlsafe(18)
        database = os.environ.get("VTT_E2E_DATABASE_URL") or "sqlite+pysqlite:///" + str(root / "vtt.db")
        self.env = dict(os.environ, APP_ENV="development", DATA_DIR=str(root / "data"), DATABASE_URL=database,
                        OWNERSHIP_BACKEND="sql", GM_PASSWORD="", SECRET_KEY=secrets.token_urlsafe(32),
                        VTT_ENABLED="1", VTT_PILOT_CAMPAIGNS="*", VTT_DIST_DIR=str(DIST), VTT_ASSETS_BACKEND="local",
                        VTT_ASSETS_DIR=str(root / "assets"), E2E_PASSWORD=self.password,
                        PYTHONDONTWRITEBYTECODE="1")
        for key in ("PUBLIC_BASE_URL", "ALLOWED_HOSTS", "GUNICORN_CMD_ARGS", "VTT_ASYNC_MODE", "VTT_ALLOWED_ORIGINS"):
            self.env.pop(key, None)
        (root / "data").mkdir()
        migrated = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=self.env,
                                  capture_output=True, text=True, timeout=60)
        assert migrated.returncode == 0, migrated.stderr[-3000:]
        seeded = subprocess.run([sys.executable, "-c", SEED], cwd=ROOT, env=self.env, capture_output=True,
                                text=True, timeout=120)
        assert seeded.returncode == 0, seeded.stderr[-3000:]
        result = json.loads([line for line in seeded.stdout.splitlines() if line.startswith("E2E_RESULT=")][-1]
                            .split("=", 1)[1])
        self.cid, self.legacy_scene = result["cid"], result["legacy"]
        self.empty_cid = result["empty"]
        self.process = None
        self.log = None
        self.start()

    def start(self) -> None:
        self.log = (self.root / "server.log").open("ab")
        command = [sys.executable, "-m", "flask", "--app", "app", "run", "--host", "127.0.0.1",
                   "--port", str(self.port), "--no-debugger", "--no-reload"]
        self.process = subprocess.Popen(command, cwd=ROOT, env=self.env, stdout=self.log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            assert self.process.poll() is None, (self.root / "server.log").read_text(errors="replace")[-3000:]
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/live", timeout=1) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.2)
        self.stop()
        raise AssertionError("the harness server did not come up")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.log and not self.log.closed:
            self.log.close()

    def gm_url(self, scene: str | None = None) -> str:
        return f"{self.url}/vtt/gm?campaignId={self.cid}" + (f"&sceneId={scene}" if scene else "")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    pytest.importorskip("playwright.sync_api")       # skip before starting a server
    if not (DIST / "index.html").is_file():
        pytest.skip("the VTT client is not built (npm run build in frontend/vtt)")
    running = VttSite(tmp_path_factory.mktemp("vtt-e2e"))
    try:
        yield running
    finally:
        running.stop()


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as engine:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or shutil.which("chromium")
        launched = engine.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        yield launched
        launched.close()


@pytest.fixture
def screens(browser, site, request, tmp_path):
    """make(kind) -> a page in its own context: 'gm' is signed in, 'table' is
    a fresh display. Screenshots of every page are kept when the test fails."""
    contexts, opened = [], []

    def make(kind: str):
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        context.add_init_script("try { localStorage.setItem('vtt.probe', '1'); } catch (e) {}")
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.errors = []
        page.on("pageerror", lambda error: page.errors.append(str(error)))
        if kind == "gm":
            page.goto(site.url + "/login")
            page.locator("[name=username]").fill("gm")
            page.locator("[name=password]").fill(site.password)
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_url("**/me")
            # Pick the campaign, as a GM does: the VTT pages need it active.
            # Loading the full bestiary on campaign activation can exceed the
            # page's interaction timeout on a loaded Windows test host.
            page.locator(f'form[action="/campaign/{site.cid}/activate"] button').first.click(timeout=60000)
            page.wait_for_url(lambda url: not url.endswith("/me"), timeout=60000)
        opened.append(page)
        return page

    yield make
    if getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed:
        artifacts = Path(os.environ.get("BROWSER_ARTIFACT_DIR", tmp_path / "artifacts"))
        artifacts.mkdir(parents=True, exist_ok=True)
        for index, page in enumerate(opened):
            if not page.is_closed():
                page.screenshot(path=str(artifacts / f"{request.node.name}-{index}.png"))
    for context in contexts:
        context.close()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    setattr(item, "rep_" + report.when, report)
