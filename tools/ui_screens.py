#!/usr/bin/env python3
"""Before/after screenshots for design-system packages (DS-01 onward).

A design package must show what it changes: screenshots of the same routes,
seeded the same way, before and after. This tool reuses the fixtures of
tools/ui_inventory.py (the legacy-open PF2e party, or a fresh account-mode
campaign per system with the owner signed in), serves that app on a local port,
and screenshots each route in headless Chromium:

    python tools/ui_screens.py shoot --profile pf2e --out shots/after/pf2e
    python tools/ui_screens.py shoot --repo ../before-tree --profile pf2e --out shots/before/pf2e
    python tools/ui_screens.py compare shots/before/pf2e shots/after/pf2e

``--repo`` points at another checkout (for example a worktree of the base
commit), so the same harness photographs the code before the change.
``compare`` counts changed pixels per image and exits 1 if any image differs;
pass ``--diff DIR`` to write a highlight image for each changed one.

Pages are shot with reduced motion and CSS animations disabled, at 1440x900 and
390x844 (full page). Everything runs locally against a throwaway DATA_DIR; the
seeded account is a local fixture, never a real login. Mistborn needs
COSMERE_DATA_DIR and 5e needs DND5E_DATA_DIR, as for ui_inventory.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

VIEWPORTS = {"desktop": (1440, 900), "phone": (390, 844)}
HERE = Path(__file__).resolve().parent


def _slug(route: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", route).strip("_") or "root"


def shoot(repo: Path, profile: str, out: Path, routes=None, full_page: bool = True) -> list[Path]:
    sys.path.insert(0, str(repo / "tools"))
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    inv = importlib.import_module("ui_inventory")
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gm-pf2e-ui-screens-") as data_dir:
        cookie, pairs = None, []
        if profile == "pf2e":
            import shutil
            party = Path(data_dir) / "party_data"
            party.mkdir()
            for name in inv.FIXTURE_PCS:
                shutil.copy(repo / "tests" / "fixtures" / name, party / name)
            ctx = inv._isolated_app(data_dir)
            module = ctx.__enter__()
            pairs = [("gm", r) for r in (routes or inv.DEFAULT_ROUTES)]
        else:
            system, rules, default = inv.PROFILES[profile]
            ctx = inv._isolated_app(data_dir, gm_password="inventory-gm",
                                    dnd5e_data=os.environ.get("DND5E_DATA_DIR"),
                                    cosmere_data=os.environ.get("COSMERE_DATA_DIR"))
            module = ctx.__enter__()
            clients, cid, _ = inv._account_clients(module, system, rules)
            session = clients["gm"].get_cookie("session")
            cookie = session.value if session else None
            pairs = [(who, route.replace("{cid}", cid)) for who, route in
                     ([("gm", r) for r in routes] if routes else default)]
        from werkzeug.serving import make_server
        server = make_server("127.0.0.1", 0, module.app, threaded=True)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        written = []
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                for name, (w, h) in VIEWPORTS.items():
                    for who, route in pairs:
                        context = browser.new_context(viewport={"width": w, "height": h},
                                                      reduced_motion="reduce",
                                                      device_scale_factor=1)
                        if cookie and who == "gm":
                            context.add_cookies([{"name": "session", "value": cookie,
                                                  "domain": "127.0.0.1", "path": "/"}])
                        page = context.new_page()
                        path = out / f"{name}__{who}__{_slug(route)}.png"
                        try:
                            # Pages hold the SSE stream open, so the network
                            # never idles: wait for load, then let scripts paint.
                            page.goto(f"http://127.0.0.1:{port}{route}", wait_until="load")
                            # Web fonts landing after first paint leave controls
                            # rastered differently; wait for them explicitly.
                            page.evaluate("document.fonts.ready.then(() => true)")
                            page.wait_for_timeout(1500)
                            page.screenshot(path=str(path), full_page=full_page, animations="disabled")
                            written.append(path)
                        except Exception as error:   # one bad route must not stop the run
                            print(f"FAILED {name} {route}: {error}", file=sys.stderr)
                        finally:
                            context.close()
                browser.close()
        finally:
            server.shutdown()
    return written


def compare(before: Path, after: Path, diff_dir: Path | None = None) -> int:
    from PIL import Image, ImageChops
    names = sorted({p.name for p in before.glob("*.png")} | {p.name for p in after.glob("*.png")})
    changed = 0
    for name in names:
        a, b = before / name, after / name
        if not a.exists() or not b.exists():
            print(f"MISSING  {name} ({'before' if not a.exists() else 'after'})")
            changed += 1
            continue
        ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
        if ia.size != ib.size:
            print(f"SIZE     {name} {ia.size} -> {ib.size}")
            changed += 1
            continue
        delta = ImageChops.difference(ia, ib)
        box = delta.getbbox()
        if box is None:
            print(f"same     {name}")
            continue
        # Any channel differing counts; a greyscale convert would round a
        # one-level difference in one channel down to "same".
        r, g, b = delta.split()
        changed_mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
        count = changed_mask.size[0] * changed_mask.size[1] - changed_mask.histogram()[0]
        print(f"CHANGED  {name}: {count} px ({100 * count / (ia.size[0] * ia.size[1]):.2f}%) in {box}")
        changed += 1
        if diff_dir:
            diff_dir.mkdir(parents=True, exist_ok=True)
            Image.composite(Image.new("RGB", ia.size, (255, 0, 0)), ib, changed_mask).save(diff_dir / name)
    print(f"\n{len(names)} images, {changed} differ")
    return 1 if changed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shoot")
    s.add_argument("--profile", default="pf2e")
    s.add_argument("--out", required=True)
    s.add_argument("--repo", default=str(HERE.parent))
    s.add_argument("--routes", nargs="*")
    s.add_argument("--viewport-only", action="store_true",
                   help="shoot the viewport only (no full-page resize; see compare notes)")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--diff")
    args = ap.parse_args(argv)
    if args.cmd == "shoot":
        out = Path(args.out).resolve()
        if os.environ.get("PYTHONHASHSEED") != "0":   # same reason as ui_inventory
            import subprocess
            env = dict(os.environ, PYTHONHASHSEED="0")
            return subprocess.call([sys.executable, *sys.argv], env=env)
        files = shoot(Path(args.repo).resolve(), args.profile, out, args.routes,
                      full_page=not args.viewport_only)
        print(f"{len(files)} screenshots in {out}")
        return 0
    return compare(Path(args.before), Path(args.after), Path(args.diff) if args.diff else None)


if __name__ == "__main__":
    sys.exit(main())
