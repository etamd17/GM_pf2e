#!/usr/bin/env python3
"""Push the local Obsidian vault to a deployed GM_pf2e instance.

Tar-gzips ~/Documents/<your vault> (or the symlinked obsidian_vault/
inside the repo), POSTs to /api/admin/vault/upload on the server, and
prints the new note count + server timestamp.

Usage:
    python3 tools/push_vault.py
    python3 tools/push_vault.py --url https://yourapp.up.railway.app
    python3 tools/push_vault.py --include-rules     # also ship zzrules/
    python3 tools/push_vault.py --replace full      # nuke server vault first

Auth:
    The server gates the upload behind the GM password (gm_required). The
    CLI prompts for it, then reuses the cookie for the rest of the session.
    Override via env: PF2E_URL, PF2E_GM_PASSWORD, PF2E_VAULT_PATH.

The default exclusions cover the things you almost never want shipped:
    - .obsidian/workspace.json (per-machine UI state)
    - .DS_Store, .git, node_modules
    - zzrules/ (16k SRD notes — overlap with /gmscreen, default-off)
    - Files larger than 5 MB (override with --max-mb)
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import sys
import tarfile
from pathlib import Path
from urllib import request as _req
from urllib import parse as _parse
from urllib import error as _err
from http.cookiejar import CookieJar


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VAULT_CANDIDATES = [
    REPO_ROOT / "obsidian_vault",
    Path.home() / "Documents" / "Pathfinder Campaigns",
]


# Files / directories ignored under merge-tar regardless of CLI flags.
EXCLUDE_DIR_NAMES = {".git", "node_modules", ".trash"}
EXCLUDE_FILE_NAMES = {".DS_Store", ".vault_last_push"}
# .obsidian/* is mostly per-machine UI; exclude unless --include-config.
EXCLUDE_OBSIDIAN_INTERNAL = True


def _resolve_vault(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_dir():
            sys.exit(f"Vault path does not exist or is not a directory: {p}")
        return p
    env = os.environ.get("PF2E_VAULT_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        if not p.is_dir():
            sys.exit(f"PF2E_VAULT_PATH is not a directory: {p}")
        return p
    for cand in DEFAULT_VAULT_CANDIDATES:
        try:
            if cand.is_dir() or (cand.is_symlink() and cand.resolve().is_dir()):
                return cand.resolve()
        except OSError:
            continue
    sys.exit(
        "Could not auto-detect vault. Pass --vault PATH, set PF2E_VAULT_PATH, "
        f"or symlink obsidian_vault/ at the repo root.\nLooked in: "
        + ", ".join(str(c) for c in DEFAULT_VAULT_CANDIDATES)
    )


def _should_skip(rel: Path, *, include_rules: bool, include_config: bool, max_bytes: int, full_path: Path) -> tuple[bool, str]:
    parts = rel.parts
    if not parts:
        return True, "empty"
    top = parts[0]
    if top in EXCLUDE_DIR_NAMES:
        return True, top
    if not include_rules and top.lower() == "zzrules":
        return True, "zzrules"
    if not include_config and top == ".obsidian":
        return True, ".obsidian"
    if rel.name in EXCLUDE_FILE_NAMES:
        return True, rel.name
    try:
        if full_path.stat().st_size > max_bytes:
            return True, f"size>{max_bytes}"
    except OSError:
        return True, "stat-failed"
    return False, ""


def build_tarball(vault: Path, *, include_rules: bool, include_config: bool, max_mb: int, since_mtime: float = 0.0) -> tuple[bytes, int, dict]:
    """Build the upload tarball. Pass `since_mtime` > 0 to ship only files
    modified after that epoch — keeps incremental pushes tiny."""
    max_bytes = max_mb * 1024 * 1024
    buf = io.BytesIO()
    file_count = 0
    skipped: dict[str, int] = {}
    total_bytes = 0
    older_count = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for dirpath, dirnames, filenames in os.walk(vault):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
            for fname in filenames:
                full = Path(dirpath) / fname
                rel = full.relative_to(vault)
                skip, reason = _should_skip(rel, include_rules=include_rules, include_config=include_config, max_bytes=max_bytes, full_path=full)
                if skip:
                    skipped[reason] = skipped.get(reason, 0) + 1
                    continue
                if since_mtime > 0:
                    try:
                        if full.stat().st_mtime <= since_mtime:
                            older_count += 1
                            continue
                    except OSError:
                        pass
                try:
                    tar.add(str(full), arcname=str(rel))
                    file_count += 1
                    total_bytes += full.stat().st_size
                except OSError as e:
                    skipped[f"oserr:{e.errno}"] = skipped.get(f"oserr:{e.errno}", 0) + 1
    if older_count:
        skipped["older_than_since"] = older_count
    return buf.getvalue(), file_count, {"skipped": skipped, "raw_total_bytes": total_bytes}


def _login(base_url: str, password: str, cookie_jar: CookieJar) -> None:
    """POST the GM password to /gm/login. Sessions cookie is captured."""
    opener = _req.build_opener(_req.HTTPCookieProcessor(cookie_jar))
    data = _parse.urlencode({"password": password}).encode("utf-8")
    req = _req.Request(f"{base_url}/gm/login", data=data, method="POST")
    try:
        with opener.open(req, timeout=15) as resp:
            # 200 + redirect to /gm = success; check by trying a protected endpoint
            resp.read()
    except _err.HTTPError as e:
        if e.code in (302, 303):
            return  # redirect = login OK
        sys.exit(f"GM login failed: HTTP {e.code}")
    except _err.URLError as e:
        sys.exit(f"GM login failed: {e}")


def _post_upload(base_url: str, tar_bytes: bytes, mode: str, cookie_jar: CookieJar) -> dict:
    boundary = "----pf2e-vault-push-" + os.urandom(8).hex()
    body = io.BytesIO()
    def w(b):
        if isinstance(b, str): b = b.encode("utf-8")
        body.write(b)
    # Multipart: vault file + replace mode
    w(f"--{boundary}\r\n")
    w("Content-Disposition: form-data; name=\"replace\"\r\n\r\n")
    w(mode + "\r\n")
    w(f"--{boundary}\r\n")
    w("Content-Disposition: form-data; name=\"vault\"; filename=\"vault.tar.gz\"\r\n")
    w("Content-Type: application/gzip\r\n\r\n")
    w(tar_bytes)
    w(f"\r\n--{boundary}--\r\n")
    body_bytes = body.getvalue()
    opener = _req.build_opener(_req.HTTPCookieProcessor(cookie_jar))
    req = _req.Request(
        f"{base_url}/api/admin/vault/upload",
        data=body_bytes,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body_bytes)),
        },
    )
    try:
        with opener.open(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except _err.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            err_body = ""
        sys.exit(f"Upload failed: HTTP {e.code}\n{err_body}")
    except _err.URLError as e:
        sys.exit(f"Upload failed: {e}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("PF2E_URL"), help="Server base URL (e.g. https://pf2ebuilder.up.railway.app). Env: PF2E_URL")
    ap.add_argument("--vault", help="Local vault path (defaults to obsidian_vault/ symlink or ~/Documents/Pathfinder Campaigns)")
    ap.add_argument("--password", default=os.environ.get("PF2E_GM_PASSWORD"), help="GM password (prompted if unset). Env: PF2E_GM_PASSWORD")
    ap.add_argument("--include-rules", action="store_true", help="Include the zzrules/ SRD subtree")
    ap.add_argument("--include-config", action="store_true", help="Include .obsidian/ config (per-machine UI state)")
    ap.add_argument("--replace", choices=("merge", "full"), default="merge", help="merge=overlay onto existing vault_data/; full=wipe first (atomic). Default: merge")
    ap.add_argument("--max-mb", type=int, default=5, help="Skip files larger than this many MB (default 5)")
    ap.add_argument("--full", action="store_true", help="Send every file (skip the incremental mtime filter)")
    ap.add_argument("--since", type=float, default=None, help="Epoch — only ship files newer than this. Defaults to the last successful push timestamp.")
    ap.add_argument("--dry-run", action="store_true", help="Build the tarball, print summary, don't upload")
    args = ap.parse_args()

    if not args.url:
        sys.exit("Need --url or PF2E_URL (e.g. https://pf2ebuilder.up.railway.app)")
    base_url = args.url.rstrip("/")

    vault = _resolve_vault(args.vault)
    print(f"Vault: {vault}")
    print(f"Server: {base_url}")

    # Resolve the incremental mtime threshold
    since_marker = REPO_ROOT / ".vault_local_last_push_ts"
    if args.full:
        since_mtime = 0.0
    elif args.since is not None:
        since_mtime = args.since
    elif since_marker.exists():
        try:
            since_mtime = float(since_marker.read_text().strip() or "0")
        except ValueError:
            since_mtime = 0.0
    else:
        since_mtime = 0.0
    if since_mtime:
        from datetime import datetime as _dt
        print(f"Incremental push since {_dt.fromtimestamp(since_mtime).isoformat(timespec='seconds')}")
    else:
        print("Full push (no prior --since marker)")

    print("Building tarball…")
    tar_bytes, file_count, info = build_tarball(
        vault, include_rules=args.include_rules, include_config=args.include_config, max_mb=args.max_mb, since_mtime=since_mtime
    )
    raw_mb = info["raw_total_bytes"] / 1024 / 1024
    tar_mb = len(tar_bytes) / 1024 / 1024
    print(f"  files: {file_count}")
    print(f"  raw:   {raw_mb:.2f} MB")
    print(f"  gz:    {tar_mb:.2f} MB")
    if info["skipped"]:
        print("  skipped:")
        for k, v in sorted(info["skipped"].items(), key=lambda kv: -kv[1]):
            print(f"    {k:24s} {v}")

    if args.dry_run:
        print("Dry run — not uploading.")
        return

    password = args.password or getpass.getpass("GM password: ")
    cookies = CookieJar()
    print("Logging in…")
    _login(base_url, password, cookies)
    print("Uploading…")
    result = _post_upload(base_url, tar_bytes, args.replace, cookies)
    if not result.get("success"):
        sys.exit(f"Upload error: {result}")
    print(f"OK  ·  {result.get('note_count')} notes on server")
    print(f"      ·  vault_root: {result.get('vault_root')}")
    print(f"      ·  server_now: {result.get('server_now')}")
    # Persist two timestamps:
    #   .vault_last_push_ts        — server's clock at upload time, used by pull
    #   .vault_local_last_push_ts  — our local clock right now, used to filter
    #                                future incremental pushes by mtime
    try:
        (REPO_ROOT / ".vault_last_push_ts").write_text(str(result.get("server_now", "")))
        import time as _t
        (REPO_ROOT / ".vault_local_last_push_ts").write_text(str(_t.time()))
    except OSError:
        pass


if __name__ == "__main__":
    main()
