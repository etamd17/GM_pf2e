#!/usr/bin/env python3
"""Pull server-side vault edits back to the local Obsidian vault.

Use this after a session-export markdown has been written into vault_data/
on the deployed server, or after editing a note via the website. Asks the
server for everything modified since the timestamp of the last push (or a
caller-supplied --since), unpacks it over the local vault.

Usage:
    python3 tools/pull_vault.py
    python3 tools/pull_vault.py --url https://yourapp.up.railway.app
    python3 tools/pull_vault.py --since 1715300000   # explicit epoch
    python3 tools/pull_vault.py --dry-run            # don't write, just list
"""
from __future__ import annotations

import argparse
import getpass
import io
import os
import sys
import tarfile
from pathlib import Path
from urllib import request as _req
from urllib import parse as _parse
from urllib import error as _err
from http.cookiejar import CookieJar


REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_vault(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("PF2E_VAULT_PATH")
    if env:
        return Path(env).expanduser().resolve()
    candidates = [
        REPO_ROOT / "obsidian_vault",
        Path.home() / "Documents" / "Pathfinder Campaigns",
    ]
    for cand in candidates:
        try:
            if cand.is_dir() or (cand.is_symlink() and cand.resolve().is_dir()):
                return cand.resolve()
        except OSError:
            continue
    sys.exit("Could not auto-detect local vault. Pass --vault PATH.")


def _login(base_url: str, password: str, cookie_jar: CookieJar) -> None:
    opener = _req.build_opener(_req.HTTPCookieProcessor(cookie_jar))
    data = _parse.urlencode({"password": password}).encode("utf-8")
    req = _req.Request(f"{base_url}/gm/login", data=data, method="POST")
    try:
        with opener.open(req, timeout=15) as resp:
            resp.read()
    except _err.HTTPError as e:
        if e.code in (302, 303):
            return
        sys.exit(f"GM login failed: HTTP {e.code}")
    except _err.URLError as e:
        sys.exit(f"GM login failed: {e}")


def _get_changes(base_url: str, since: float, cookie_jar: CookieJar) -> tuple[bytes, dict]:
    opener = _req.build_opener(_req.HTTPCookieProcessor(cookie_jar))
    qs = _parse.urlencode({"since": since})
    req = _req.Request(f"{base_url}/api/admin/vault/changes-since?{qs}")
    try:
        with opener.open(req, timeout=120) as resp:
            return resp.read(), {
                "file_count": int(resp.headers.get("X-Vault-File-Count") or "0"),
                "server_now": float(resp.headers.get("X-Vault-Server-Now") or "0"),
            }
    except _err.HTTPError as e:
        sys.exit(f"Pull failed: HTTP {e.code}")
    except _err.URLError as e:
        sys.exit(f"Pull failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("PF2E_URL"), help="Server base URL. Env: PF2E_URL")
    ap.add_argument("--vault", help="Local vault path (defaults to obsidian_vault/ symlink target)")
    ap.add_argument("--password", default=os.environ.get("PF2E_GM_PASSWORD"), help="GM password. Env: PF2E_GM_PASSWORD")
    ap.add_argument("--since", type=float, default=None, help="Epoch timestamp; default reads .vault_last_push_ts written by push_vault.py")
    ap.add_argument("--dry-run", action="store_true", help="List changed files; do not extract")
    args = ap.parse_args()

    if not args.url:
        sys.exit("Need --url or PF2E_URL")
    base_url = args.url.rstrip("/")

    if args.since is None:
        marker = REPO_ROOT / ".vault_last_push_ts"
        if marker.exists():
            try:
                args.since = float(marker.read_text().strip() or "0")
            except ValueError:
                args.since = 0.0
        else:
            args.since = 0.0
    print(f"Server: {base_url}")
    print(f"Since:  {args.since}")

    vault = _resolve_vault(args.vault)
    print(f"Vault:  {vault}")

    password = args.password or getpass.getpass("GM password: ")
    cookies = CookieJar()
    print("Logging in…")
    _login(base_url, password, cookies)
    print("Fetching changes…")
    blob, meta = _get_changes(base_url, args.since, cookies)
    print(f"  files: {meta['file_count']}")
    print(f"  bytes: {len(blob)}")

    # List members regardless; extract only when not dry
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        for m in members:
            print(f"    {m.name}  ({m.size}b)")
        if not args.dry_run:
            for m in members:
                # Path-traversal guard, mirrored from the server
                name = m.name
                if name.startswith("/") or ".." in Path(name).parts:
                    print(f"    SKIP (traversal): {name}")
                    continue
                tar.extract(m, path=vault, set_attrs=False)
            # Roll forward our last-push timestamp
            try:
                (REPO_ROOT / ".vault_last_push_ts").write_text(str(meta["server_now"]))
            except OSError:
                pass
    print("Done." if not args.dry_run else "Dry run — nothing extracted.")


if __name__ == "__main__":
    main()
