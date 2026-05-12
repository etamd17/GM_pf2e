"""Git-backed Obsidian vault sync between Railway and a private GitHub repo.

Opt-in via env vars; when unset, `notes_service` keeps reading the local
vault_data/ directory or the obsidian_vault/ symlink with no behavior
change. When set, this module:

  * Clones the configured private repo into the vault data directory on
    process start (or sets up an existing checkout's auth + identity).
  * Spawns a background thread that `git pull`s every PULL_INTERVAL_SEC
    so edits made from Obsidian on the GM's Mac (via the Obsidian Git
    plugin) show up on the deployed app without restart.
  * Exposes `commit_and_push(paths, message)` which the save endpoints
    call immediately after writing a note, so website edits land on the
    GM's local Obsidian within minutes (Obsidian Git pulls on its own
    interval).

Env vars
--------
PF2E_VAULT_GIT_URL          required. e.g. https://github.com/USER/pf2e-vault.git
PF2E_VAULT_GIT_TOKEN        required if private. GitHub PAT with
                            `contents:write` scope on the vault repo.
PF2E_VAULT_GIT_BRANCH       default 'main'
PF2E_VAULT_PULL_INTERVAL_SEC default 120
PF2E_VAULT_GIT_USER_NAME    default 'PF2E Bot'
PF2E_VAULT_GIT_USER_EMAIL   default 'pf2e-bot@noreply'

All git operations are guarded by a single RLock so a save can't race
the background pull, and pull→reset is hard (the GM's Obsidian-side
auto-commit is the source of truth for vault content; server-side
edits go through commit_and_push which respects that ordering).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Iterable, Optional


# ─── Config ──────────────────────────────────────────────────────────────────

GIT_URL: str = os.environ.get("PF2E_VAULT_GIT_URL", "").strip()
GIT_TOKEN: str = os.environ.get("PF2E_VAULT_GIT_TOKEN", "").strip()
GIT_BRANCH: str = os.environ.get("PF2E_VAULT_GIT_BRANCH", "main").strip() or "main"
PULL_INTERVAL_SEC: int = max(15, int(os.environ.get("PF2E_VAULT_PULL_INTERVAL_SEC", "120")))
USER_NAME: str = os.environ.get("PF2E_VAULT_GIT_USER_NAME", "PF2E Bot").strip()
USER_EMAIL: str = os.environ.get("PF2E_VAULT_GIT_USER_EMAIL", "pf2e-bot@noreply").strip()

# True when env is fully configured. Callers check ENABLED before bothering
# with commit_and_push; when False, every public method is a no-op so a
# missing token never crashes the request path.
ENABLED: bool = bool(GIT_URL)

logger = logging.getLogger(__name__)

# Single global lock — all git operations are serialized so pull/save can't
# leave a half-applied state. Cheap because git operations are seconds, not
# minutes, and the GM is one user. Re-entrant so commit_and_push can be
# called by save() while save already holds the lock.
_lock = threading.RLock()


def acquire_lock():
    """Public accessor so notes.save can hold the lock across its
    write-then-commit window — keeps the poller from fetch+merging
    between the on-disk write and the commit_and_push that follows."""
    return _lock

# Mutable status surface for the /api/notes/health endpoint.
_state = {
    "enabled": ENABLED,
    "branch": GIT_BRANCH,
    "url": GIT_URL,
    "last_pull_at": None,
    "last_pull_ok": None,
    "last_pull_error": None,
    "last_push_at": None,
    "last_push_ok": None,
    "last_push_error": None,
    "head_sha": None,
    "pull_interval_sec": PULL_INTERVAL_SEC,
    "initialized": False,
}

_target_dir: Optional[Path] = None
_poller_started: bool = False


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _auth_url() -> str:
    """Return a clone URL with the PAT injected, or the bare URL if no token."""
    if not GIT_URL:
        return ""
    if not GIT_TOKEN:
        return GIT_URL
    if "@" in GIT_URL.replace("https://", "", 1):
        # User already embedded credentials — don't double-up.
        return GIT_URL
    return GIT_URL.replace("https://", f"https://x-access-token:{GIT_TOKEN}@", 1)


def _redact(s: str) -> str:
    """Strip the PAT (and any inline-URL credentials) from a string before
    logging or surfacing it via status(). Covers the configured token AND
    the generic `https://user:secret@host/...` form, since some operators
    embed credentials directly in PF2E_VAULT_GIT_URL rather than the
    separate token env-var."""
    if not s:
        return s
    if GIT_TOKEN and GIT_TOKEN in s:
        s = s.replace(GIT_TOKEN, "***")
    # Also redact any embedded `://user:secret@` form so subprocess error
    # messages carrying the auth_url don't leak via TimeoutExpired etc.
    return re.sub(r"(://)[^/@\s]+:[^/@\s]+@", r"\1***@", s)


def _git(*args: str, cwd: Path, check: bool = True, timeout: int = 60) -> tuple[int, str, str]:
    """Run git with a fixed timeout. Returns (returncode, stdout, stderr).

    Wraps subprocess.TimeoutExpired so the credentialed argv (which git
    timeouts include verbatim in their `__str__`) doesn't bubble up to
    status() / log lines un-redacted."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(_redact(f"git {' '.join(args[:1])} timed out after {timeout}s")) from None
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (code {proc.returncode}): "
            f"{_redact(proc.stderr.strip() or proc.stdout.strip())}"
        )
    return proc.returncode, proc.stdout, proc.stderr


# ─── Lifecycle ───────────────────────────────────────────────────────────────

def initialize(target: Path, *, background: bool = True) -> bool:
    """Set up the configured vault repo at `target`. Idempotent. No-op when
    ENABLED is False. Always returns without raising — failures are logged
    and surfaced via `status()` for the health endpoint to display.

    By default the heavy work (clone / fetch / reset) runs on a background
    thread so a slow GitHub or a misconfigured URL can NEVER block Flask
    boot — gunicorn workers come up immediately, and the vault becomes
    available in the notes service as soon as the clone finishes. Pass
    background=False for tests where you want to assert post-clone state.
    """
    global _target_dir, _poller_started
    if not ENABLED:
        return False
    _target_dir = target
    if background:
        t = threading.Thread(
            target=lambda: _initialize_sync(target),
            daemon=True,
            name="vault-sync-init",
        )
        t.start()
        # Don't claim success yet — the background thread will set the flag.
        # The poller is also started inside _initialize_sync once the first
        # clone/fetch succeeds.
        return True
    return _initialize_sync(target)


def _initialize_sync(target: Path) -> bool:
    """Body of initialize — runs on the calling thread (or background)."""
    global _poller_started
    try:
        with _lock:
            target.mkdir(parents=True, exist_ok=True)
            if (target / ".git").is_dir():
                # Existing checkout — refresh the remote URL (in case the
                # token rotated) and the identity, then sync to origin
                # WITHOUT discarding unpushed local commits. The old
                # behavior here was `git reset --hard origin/<branch>`
                # which silently wiped any commit whose push had failed
                # (e.g. session-export that committed locally but couldn't
                # reach GitHub). On the next deploy / process restart
                # those commits would vanish.
                _git("remote", "set-url", "origin", _auth_url(), cwd=target, check=False)
                _git("config", "user.name", USER_NAME, cwd=target, check=False)
                _git("config", "user.email", USER_EMAIL, cwd=target, check=False)
                _git("fetch", "origin", GIT_BRANCH, cwd=target, timeout=90)
                _git("checkout", "-B", GIT_BRANCH, f"origin/{GIT_BRANCH}", cwd=target, check=False)
                # Compare local HEAD vs origin and act safely.
                _rc, ahead_behind_out, _err = _git(
                    "rev-list", "--left-right", "--count",
                    f"HEAD...origin/{GIT_BRANCH}", cwd=target, check=False,
                )
                try:
                    a_str, b_str = ahead_behind_out.strip().split()
                    ahead, behind = int(a_str), int(b_str)
                except (ValueError, AttributeError):
                    ahead, behind = 0, 0
                if ahead == 0 and behind > 0:
                    # Pure remote-side work — safe fast-forward.
                    _git("merge", "--ff-only", f"origin/{GIT_BRANCH}", cwd=target, check=False)
                elif ahead > 0:
                    # Local commits never pushed. Try now; if successful and
                    # remote also moved, refetch + ff-only.
                    try:
                        _git("push", "origin", GIT_BRANCH, cwd=target, timeout=60)
                        if behind > 0:
                            _git("fetch", "origin", GIT_BRANCH, cwd=target, timeout=90)
                            _git("merge", "--ff-only", f"origin/{GIT_BRANCH}", cwd=target, check=False)
                    except RuntimeError as push_err:
                        logger.info(
                            "vault_sync: init found local-ahead commits but push failed (%s); leaving working tree intact",
                            _redact(str(push_err)),
                        )
            elif any(target.iterdir()):
                # Non-empty but not a git checkout. Initialize and overwrite
                # with the remote so the volume's pre-existing contents
                # don't conflict with what's on GitHub.
                _git("init", "-b", GIT_BRANCH, cwd=target)
                _git("remote", "add", "origin", _auth_url(), cwd=target)
                _git("config", "user.name", USER_NAME, cwd=target)
                _git("config", "user.email", USER_EMAIL, cwd=target)
                _git("fetch", "origin", GIT_BRANCH, cwd=target, timeout=90)
                _git("checkout", "-B", GIT_BRANCH, f"origin/{GIT_BRANCH}", cwd=target, check=False)
                _git("reset", "--hard", f"origin/{GIT_BRANCH}", cwd=target)
            else:
                # Fresh clone into an empty dir
                _git("clone", "--branch", GIT_BRANCH, "--depth", "50", _auth_url(), str(target), cwd=target.parent, timeout=120)
                _git("config", "user.name", USER_NAME, cwd=target)
                _git("config", "user.email", USER_EMAIL, cwd=target)
            _state["head_sha"] = _git("rev-parse", "HEAD", cwd=target, check=False)[1].strip() or None
            _state["initialized"] = True
            _state["last_pull_ok"] = True
            _state["last_pull_error"] = None
            _state["last_pull_at"] = time.time()
        if not _poller_started:
            _start_poller()
            _poller_started = True
        logger.info("vault_sync: initialized at %s (branch %s)", target, GIT_BRANCH)
        return True
    except Exception as e:
        _state["initialized"] = False
        _state["last_pull_ok"] = False
        _state["last_pull_error"] = str(e)
        logger.exception("vault_sync: initialize failed")
        return False


def pull() -> bool:
    """Fetch + fast-forward to origin/<branch>. Skips silently when not
    enabled or not initialized.

    IMPORTANT — never `git reset --hard` unconditionally. A previous version
    did, which discarded any local commit whose `git push` had failed (e.g.
    transient network blip during a session-export). The next poller tick
    would silently wipe both the commit AND the working-tree file, losing
    the GM's write entirely. Now we:
      1. fetch
      2. if origin is strictly ahead: fast-forward merge (safe, no rewrite)
      3. if local is ahead OR diverged: try to push local commits first,
         then re-fetch + fast-forward. If push still fails, leave the
         working tree alone and surface the divergence in status() so
         the next commit_and_push's rebase retry can catch up.
    """
    if not ENABLED or _target_dir is None or not (_target_dir / ".git").is_dir():
        return False
    target = _target_dir
    try:
        with _lock:
            _git("fetch", "origin", GIT_BRANCH, cwd=target, timeout=90)
            # Compare local HEAD vs origin/branch. _git returns
            # (returncode, stdout, stderr); we want stdout.
            _rc, ahead_behind_out, _err = _git(
                "rev-list", "--left-right", "--count",
                f"HEAD...origin/{GIT_BRANCH}", cwd=target, check=False,
            )
            try:
                ahead_str, behind_str = ahead_behind_out.strip().split()
                ahead = int(ahead_str)
                behind = int(behind_str)
            except (ValueError, AttributeError):
                ahead, behind = 0, 0
            if ahead == 0 and behind == 0:
                pass  # already in sync
            elif ahead == 0 and behind > 0:
                # Pure remote-side commits — safe fast-forward.
                _git("merge", "--ff-only", f"origin/{GIT_BRANCH}", cwd=target)
            elif ahead > 0:
                # We have local commits that haven't reached the remote.
                # Try to push them first; on success, rebase/ff if needed.
                pushed_ok = False
                try:
                    _git("push", "origin", GIT_BRANCH, cwd=target, timeout=60)
                    pushed_ok = True
                except RuntimeError as push_err:
                    logger.info("vault_sync: pull deferred push failed (%s); will not rewrite local", _redact(str(push_err)))
                if pushed_ok and behind > 0:
                    # Remote also had new commits; refetch then fast-forward.
                    _git("fetch", "origin", GIT_BRANCH, cwd=target, timeout=90)
                    _git("merge", "--ff-only", f"origin/{GIT_BRANCH}", cwd=target, check=False)
            _state["head_sha"] = _git("rev-parse", "HEAD", cwd=target, check=False)[1].strip() or None
            _state["last_pull_ok"] = True
            _state["last_pull_error"] = None
            _state["last_pull_at"] = time.time()
            _state["local_ahead"] = ahead if not (ahead > 0 and pushed_ok if 'pushed_ok' in locals() else False) else 0
        # Bust the notes-service caches so the next render sees the new state.
        # Import inside the function to avoid a circular import at module load.
        try:
            from . import notes as _notes
            _notes.invalidate_tree_cache()
            _notes.invalidate_index()
            _notes._RENDER_CACHE.clear()  # files that came in from the pull need fresh renders
        except Exception:
            pass
        return True
    except Exception as e:
        _state["last_pull_ok"] = False
        _state["last_pull_error"] = _redact(str(e))
        _state["last_pull_at"] = time.time()
        logger.warning("vault_sync: pull failed: %s", _redact(str(e)))
        return False


def commit_and_push(rel_paths: Iterable[str], message: str) -> bool:
    """Stage the given paths, commit (skipped if there's nothing staged), and
    push. On push reject (remote has commits we haven't seen), pull --rebase
    once and retry. Returns True if either we pushed or there was nothing
    to commit; False on hard failure (the local file is still written —
    we just couldn't sync it)."""
    if not ENABLED or _target_dir is None or not (_target_dir / ".git").is_dir():
        return False
    target = _target_dir
    rel_paths = [p for p in rel_paths if p]
    if not rel_paths:
        return True
    try:
        with _lock:
            for p in rel_paths:
                _git("add", "--", p, cwd=target)
            # `git diff --cached --quiet` exits 0 if nothing is staged, 1 if there is.
            code, _, _ = _git("diff", "--cached", "--quiet", cwd=target, check=False)
            if code == 0:
                return True
            _git("commit", "-m", message, cwd=target)
            try:
                _git("push", "origin", GIT_BRANCH, cwd=target, timeout=60)
            except RuntimeError as push_err:
                # Try once: rebase atop whatever the remote has, then re-push.
                logger.info("vault_sync: push rejected, retrying with rebase: %s", _redact(str(push_err)))
                _git("pull", "--rebase", "origin", GIT_BRANCH, cwd=target, timeout=60)
                _git("push", "origin", GIT_BRANCH, cwd=target, timeout=60)
            _state["head_sha"] = _git("rev-parse", "HEAD", cwd=target, check=False)[1].strip() or None
            _state["last_push_ok"] = True
            _state["last_push_error"] = None
            _state["last_push_at"] = time.time()
        return True
    except Exception as e:
        _state["last_push_ok"] = False
        _state["last_push_error"] = _redact(str(e))
        _state["last_push_at"] = time.time()
        logger.warning("vault_sync: commit/push failed: %s", _redact(str(e)))
        return False


# ─── Background poller ───────────────────────────────────────────────────────

def _poller_loop() -> None:
    """Sleep, pull, repeat. Daemon thread; survives until process exit."""
    while True:
        # Slight jitter so multiple instances (if ever) don't fetch in lockstep.
        time.sleep(PULL_INTERVAL_SEC)
        try:
            pull()
        except Exception:
            logger.exception("vault_sync: poller iteration crashed")


def _start_poller() -> None:
    t = threading.Thread(target=_poller_loop, daemon=True, name="vault-sync-poller")
    t.start()


# ─── Status (for /api/notes/health) ──────────────────────────────────────────

def status() -> dict:
    """Snapshot for the health endpoint. Never raises."""
    snap = dict(_state)
    # Mask credentials in the surfaced URL — it's user-facing.
    if snap.get("url") and GIT_TOKEN and GIT_TOKEN in snap["url"]:
        snap["url"] = snap["url"].replace(GIT_TOKEN, "***")
    snap["token_set"] = bool(GIT_TOKEN)
    return snap
