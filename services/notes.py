"""Obsidian vault read-through service.

The vault lives outside the repo (a symlink in obsidian_vault/) so the website
treats it as a read-mostly source-of-truth. This module provides:

  * tree(include_rules)        — folder/file tree of the vault
  * render(rel_path)           — markdown → HTML with WikiLink + callout passes
  * render_excerpt(rel_path, n) — first N rendered blocks (for the GM hub)
  * search(query, ...)         — title + body search across the vault
  * backlinks(rel_path)        — every note that links to this one
  * resolve_wikilink(target)   — Obsidian-style title→path resolution
  * save(rel_path, body, mtime) — write a note, with mtime conflict guard
  * note_exists(name)          — for the per-PC / per-monster integrations

The 16k+ `zzrules/` SRD subtree is filtered out of the default surface — it
duplicates `/gmscreen` and would drown the GM's actual campaign notes.
"""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import dataclass
from html import escape as _html_escape
from pathlib import Path
from threading import RLock
from typing import Optional

import markdown
import yaml


# ─── Vault root resolution ────────────────────────────────────────────────────

# Two source modes, in priority order:
#
#   1. vault_data/ — a real directory inside the repo (or attached as a
#      Railway volume at /app/vault_data). This is what production reads
#      from. Populated by `tools/push_vault.py` from the GM's local
#      Obsidian vault. Edits via /api/notes/save land here too.
#
#   2. obsidian_vault/ — a symlink to the GM's local Obsidian vault.
#      Used for local-only development on the GM's machine. Won't work
#      under Railway (no filesystem access to the user's machine).
#
# The path can be overridden by the PF2E_VAULT_DATA env var so deployment
# environments can point it at a mounted volume without code changes.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_VAULT_DATA_DIR = Path(os.environ.get("PF2E_VAULT_DATA", str(_REPO_ROOT / "vault_data")))
_VAULT_SYMLINK = _REPO_ROOT / "obsidian_vault"


def _resolve_vault_root() -> Optional[Path]:
    # Prefer the writable vault_data/ directory (production / synced).
    try:
        if _VAULT_DATA_DIR.is_dir():
            # Empty directory still counts; the upload endpoint will fill it.
            return _VAULT_DATA_DIR.resolve()
    except (OSError, RuntimeError):
        pass
    # Fall back to the read-only obsidian_vault/ symlink (local dev).
    try:
        if _VAULT_SYMLINK.exists():
            resolved = _VAULT_SYMLINK.resolve(strict=True)
            if resolved.is_dir():
                return resolved
    except (OSError, RuntimeError):
        pass
    return None


def get_vault_root() -> Optional[Path]:
    """Always resolve fresh — used by handlers that mutate vault contents
    (upload / save) so a brand-new vault_data/ directory is picked up
    without a process restart."""
    return _resolve_vault_root()


def get_vault_data_dir() -> Path:
    """The canonical writable vault directory, regardless of whether it
    currently exists (the upload endpoint will create it on first push)."""
    return _VAULT_DATA_DIR


def vault_source() -> str:
    """Identifier for which source mode is active — surfaced on the health
    endpoint so the GM can confirm sync is wired correctly."""
    if _VAULT_DATA_DIR.is_dir():
        return "vault_data"
    if _VAULT_SYMLINK.exists():
        return "obsidian_symlink"
    return "missing"


VAULT_ROOT: Optional[Path] = _resolve_vault_root()

# Subtree filtered from the default browse / search. Stored as a normalized
# lowercase prefix so case-insensitive filtering works on macOS APFS.
_RULES_PREFIX = "zzrules/"

# Templates contain raw Templater syntax (<%* tp.* %>) that confuses the
# markdown renderer. Browseable but rendered with a banner.
_TEMPLATES_PREFIX = "zz_templates/"


def _is_rules(rel: str) -> bool:
    return rel.lower().startswith(_RULES_PREFIX)


def _is_template(rel: str) -> bool:
    return rel.lower().startswith(_TEMPLATES_PREFIX)


# ─── Internal caches ──────────────────────────────────────────────────────────

_TREE_CACHE: dict[bool, tuple[float, list[dict]]] = {}
_TREE_TTL_SEC = 60.0

_RENDER_CACHE: dict[str, tuple[float, str, dict]] = {}  # rel → (mtime, html, meta)
_RENDER_CACHE_MAX = 64

_INDEX_LOCK = RLock()
_TITLE_INDEX: Optional[dict[str, list[str]]] = None  # lowercase title → rel paths
_OUTBOUND: Optional[dict[str, list[str]]] = None     # rel → list of wikilink targets
_INBOUND: Optional[dict[str, list[str]]] = None      # rel → list of origin rels
_INDEX_BUILT_FOR_RULES: bool = False                 # whether the index includes zzrules


# ─── Path safety ──────────────────────────────────────────────────────────────

class NotePathError(ValueError):
    """Raised when a request would escape the vault root."""


def _safe_join(rel: str) -> Path:
    """Resolve `rel` (URL-style) inside VAULT_ROOT, raising on traversal."""
    root = get_vault_root()
    if root is None:
        raise NotePathError("Vault is not available")
    rel = rel.lstrip("/").replace("\\", "/")
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise NotePathError(f"Path escapes vault: {rel}")
    return candidate


# ─── Tree ─────────────────────────────────────────────────────────────────────

def _walk_tree(root: Path, vault_root: Path, include_rules: bool) -> list[dict]:
    """Return a list of {name, kind, path, children?} ordered by folders-first
    then alphabetic. Skips dotfiles and the .obsidian config directory."""
    out: list[dict] = []
    try:
        entries = sorted(
            os.scandir(root),
            key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()),
        )
    except (OSError, PermissionError):
        return out
    for entry in entries:
        name = entry.name
        if name.startswith(".") or name in {"node_modules"}:
            continue
        try:
            rel = str(Path(entry.path).relative_to(vault_root)).replace(os.sep, "/")
        except ValueError:
            continue
        if not include_rules and _is_rules(rel):
            continue
        if entry.is_dir(follow_symlinks=False):
            children = _walk_tree(Path(entry.path), vault_root, include_rules)
            # Hide empty folders entirely
            if not children:
                continue
            out.append({"name": name, "kind": "dir", "path": rel, "children": children})
        else:
            # Only surface markdown + canvas files; everything else is an asset.
            if not (name.endswith(".md") or name.endswith(".canvas")):
                continue
            out.append({"name": name, "kind": "canvas" if name.endswith(".canvas") else "note", "path": rel})
    return out


def tree(include_rules: bool = False) -> list[dict]:
    """Cached folder/file tree of the vault. ``include_rules=True`` exposes the
    16k zzrules/ SRD subtree; defaults to off."""
    root = get_vault_root()
    if root is None:
        return []
    now = time.monotonic()
    cached = _TREE_CACHE.get(include_rules)
    if cached and (now - cached[0] < _TREE_TTL_SEC):
        return cached[1]
    snapshot = _walk_tree(root, root, include_rules)
    _TREE_CACHE[include_rules] = (now, snapshot)
    return snapshot


def invalidate_tree_cache() -> None:
    _TREE_CACHE.clear()


# ─── Frontmatter ──────────────────────────────────────────────────────────────

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) for an Obsidian note. Tolerates
    malformed YAML — we'd rather render the body than 500."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    try:
        data = yaml.safe_load(raw) or {}
        if not isinstance(data, dict):
            data = {}
    except yaml.YAMLError:
        data = {}
    return data, text[m.end():]


# ─── WikiLink resolution ─────────────────────────────────────────────────────

# Matches both [[Note]] / [[Note|Alias]] / [[Path/Note#Heading|Alias]] forms.
# Triple-bracket and code-fence handling are deferred to the markdown
# extension layer; for now we run on the raw body before markdown lib sees it.
_WIKILINK_RE = re.compile(
    r"(?P<embed>!?)\[\[(?P<target>[^\]\|\n]+?)(?:#(?P<heading>[^\]\|\n]+?))?(?:\|(?P<alias>[^\]\n]+?))?\]\]"
)


def _build_index(include_rules: bool) -> None:
    """Walk the vault and build title/outbound/inbound link maps. Cheap to
    rebuild — ~100ms for the 525 campaign notes; ~3-4s with rules included."""
    global _TITLE_INDEX, _OUTBOUND, _INBOUND, _INDEX_BUILT_FOR_RULES
    root = get_vault_root()
    if root is None:
        with _INDEX_LOCK:
            _TITLE_INDEX, _OUTBOUND, _INBOUND = {}, {}, {}
            _INDEX_BUILT_FOR_RULES = include_rules
        return
    title_index: dict[str, list[str]] = {}
    outbound: dict[str, list[str]] = {}
    inbound: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune .obsidian and other dot dirs in-place
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            try:
                rel = str(Path(dirpath, fname).relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
            if not include_rules and _is_rules(rel):
                continue
            title = fname[:-3]
            title_index.setdefault(title.lower(), []).append(rel)
            try:
                with open(os.path.join(dirpath, fname), "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            outs: list[str] = []
            for m in _WIKILINK_RE.finditer(body):
                outs.append(m.group("target").strip())
            if outs:
                outbound[rel] = outs
                for tgt in outs:
                    inbound.setdefault(tgt.lower(), []).append(rel)
    with _INDEX_LOCK:
        _TITLE_INDEX = title_index
        _OUTBOUND = outbound
        _INBOUND = inbound
        _INDEX_BUILT_FOR_RULES = include_rules


def _ensure_index(include_rules: bool = False) -> None:
    global _INDEX_BUILT_FOR_RULES
    with _INDEX_LOCK:
        if _TITLE_INDEX is None:
            _need_build = True
        elif include_rules and not _INDEX_BUILT_FOR_RULES:
            _need_build = True
        else:
            _need_build = False
    if _need_build:
        _build_index(include_rules=include_rules)


def invalidate_index() -> None:
    global _TITLE_INDEX, _OUTBOUND, _INBOUND
    with _INDEX_LOCK:
        _TITLE_INDEX = None
        _OUTBOUND = None
        _INBOUND = None


def resolve_wikilink(target: str, *, include_rules: bool = False) -> Optional[str]:
    """Obsidian resolution: exact path > unique title > shortest match >
    most recent mtime. Returns the rel path or None for broken links."""
    if not target:
        return None
    root = get_vault_root()
    target = target.strip()
    _ensure_index(include_rules=include_rules)
    # 1. Exact relative path (with or without .md)
    candidates = []
    direct_md = target if target.lower().endswith(".md") else f"{target}.md"
    try:
        p = _safe_join(direct_md)
        if p.is_file() and root:
            return str(p.relative_to(root)).replace(os.sep, "/")
    except (NotePathError, AttributeError):
        pass
    # 2. Title lookup (case-insensitive); allow the trailing path component
    title = target.split("/")[-1]
    if title.lower().endswith(".md"):
        title = title[:-3]
    with _INDEX_LOCK:
        if _TITLE_INDEX is not None:
            candidates = list(_TITLE_INDEX.get(title.lower(), []))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Multiple matches: prefer non-rules, then shortest path, then newest mtime.
    def _score(rel: str) -> tuple:
        in_rules = _is_rules(rel)
        try:
            mtime = (root / rel).stat().st_mtime if root else 0
        except OSError:
            mtime = 0
        return (in_rules, len(rel), -mtime)
    candidates.sort(key=_score)
    return candidates[0]


def note_exists(name: str, *, include_rules: bool = False) -> Optional[str]:
    """Lightweight version of resolve_wikilink for the per-PC / per-monster
    integrations. Returns the rel path if a note titled `name` exists."""
    return resolve_wikilink(name, include_rules=include_rules)


def extract_state_lines(rel_path: str = "Now Playing.md") -> dict:
    """Extract the campaign-state bullets from `Now Playing.md`.

    Looks for lines like ``- **Active Campaign:** Shades of Blood`` in the
    body and key/value pairs in the frontmatter. Returns whatever it found,
    keyed by the label (lowercased, stripped).

    Used by the public homepage to surface a "where the party stands"
    headline. Failures are quiet — a missing note returns an empty dict.
    """
    out: dict[str, str] = {}
    try:
        p = _safe_join(rel_path)
        if not p.is_file():
            return {}
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except (NotePathError, OSError):
        return {}
    # Obsidian on Windows / iCloud sometimes writes a UTF-8 BOM at the
    # head of the file. Strip it so the frontmatter regex (`^---`) can
    # still match line 1.
    if raw.startswith("﻿"):
        raw = raw[1:]
    fm, body = _split_frontmatter(raw)
    # Frontmatter values pass through (lower-cased keys for stable lookup)
    for k, v in (fm or {}).items():
        if isinstance(v, (str, int, float)):
            out[str(k).strip().lower()] = str(v).strip()
    # Body bullets like `- **Label:** value` (Obsidian-style key:value)
    bullet_re = re.compile(
        r"^\s*[-*]\s+\*\*(?P<label>[^:*]+?):\*\*\s*(?P<value>.+?)\s*$",
        re.MULTILINE,
    )
    for m in bullet_re.finditer(body):
        label = m.group("label").strip().lower()
        if label in out:
            continue
        value = m.group("value").strip()
        # Strip trailing wikilink display alias remnants for cleanliness:
        # the homepage pre-renders, so leave [[X]] markers intact.
        out[label] = value
    return out


# ─── Markdown render pipeline ────────────────────────────────────────────────

# Obsidian callout syntax: `> [!note] Title\n> body`. We match a leading
# block-quote line that starts with `[!type]` and rewrite it into an
# attribute-laden div the markdown lib will leave alone (because it's HTML
# inside the block).
_CALLOUT_HEAD_RE = re.compile(r"^>\s*\[!(?P<kind>\w+)\]\s*(?P<title>[^\n]*)$", re.MULTILINE)


def _preprocess_callouts(body: str) -> str:
    """Rewrite Obsidian callouts to <div class="cal cal-<kind>"> blocks.
    We collapse the entire blockquote into the div — every following `>`
    line up to a blank line counts as part of the callout."""
    out_lines: list[str] = []
    lines = body.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _CALLOUT_HEAD_RE.match(line)
        if not m:
            out_lines.append(line)
            i += 1
            continue
        kind = m.group("kind").lower()
        title = (m.group("title") or kind).strip()
        # Collect following `>` lines
        body_lines: list[str] = []
        j = i + 1
        while j < len(lines) and lines[j].lstrip().startswith(">"):
            body_lines.append(re.sub(r"^>\s?", "", lines[j]))
            j += 1
        out_lines.append(f'<div class="cal cal-{kind}">')
        if title:
            out_lines.append(f'<div class="cal-h">{title}</div>')
        # Re-render the inner body as markdown by leaving it as a paragraph
        # block, which the markdown lib will pick up. We separate from the
        # opening div with a blank line so the block parser activates.
        out_lines.append("")
        out_lines.extend(body_lines)
        out_lines.append("")
        out_lines.append("</div>")
        i = j
    return "\n".join(out_lines)


def _replace_wikilink(match: re.Match, *, include_rules: bool) -> str:
    target = match.group("target").strip()
    heading = (match.group("heading") or "").strip()
    alias = (match.group("alias") or "").strip()
    is_embed = bool(match.group("embed"))
    display = alias or target.split("/")[-1].rstrip(".md")
    # Image embed: ![[image.png]]
    if is_embed:
        # Image extensions are routed through the asset endpoint
        if any(target.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            resolved = _resolve_asset(target)
            if resolved:
                from urllib.parse import quote
                return f'<img class="note-embed-img" alt="{display}" src="/api/notes/asset/{quote(resolved)}" loading="lazy">'
            return f'<span class="wikilink wikilink-broken" title="Image not found">{display}</span>'
        # Embedded note transclusion is rendered as a stub link for now.
        rel = resolve_wikilink(target, include_rules=include_rules)
        if rel:
            from urllib.parse import quote
            return f'<a class="wikilink wikilink-embed" href="/gm/notes/view/{quote(rel)}">↳ {display}</a>'
        return f'<span class="wikilink wikilink-broken">{display}</span>'
    # Plain link
    rel = resolve_wikilink(target, include_rules=include_rules)
    if rel:
        from urllib.parse import quote
        anchor = f"#{heading.lower().replace(' ', '-')}" if heading else ""
        return f'<a class="wikilink" href="/gm/notes/view/{quote(rel)}{anchor}">{display}</a>'
    return f'<span class="wikilink wikilink-broken" title="No matching note">{display}</span>'


def _resolve_asset(target: str) -> Optional[str]:
    """Look up an image embed. Obsidian stores attachments anywhere; we
    accept either a direct path or a filename anywhere in the vault."""
    root = get_vault_root()
    if root is None:
        return None
    try:
        p = _safe_join(target)
        if p.is_file():
            return str(p.relative_to(root)).replace(os.sep, "/")
    except NotePathError:
        pass
    # Filename search — attachments live in zz_Attachments/ typically.
    name = target.split("/")[-1]
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if name in filenames:
            try:
                return str(Path(dirpath, name).relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
    return None


def _preprocess_wikilinks(body: str, *, include_rules: bool) -> str:
    return _WIKILINK_RE.sub(lambda m: _replace_wikilink(m, include_rules=include_rules), body)


# Tags inside the body: hashtags like #pf2e, #npc/major. Skip code fences.
_TAG_RE = re.compile(r"(?<![\w/&#])#([a-zA-Z][\w/-]*)")


def _preprocess_tags(body: str) -> str:
    # Don't munge inside fenced code blocks
    parts: list[str] = []
    in_code = False
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_code = not in_code
            parts.append(line)
            continue
        if in_code:
            parts.append(line)
            continue
        parts.append(_TAG_RE.sub(r'<span class="note-tag">#\1</span>', line))
    return "\n".join(parts)


def _md_renderer() -> markdown.Markdown:
    return markdown.Markdown(
        extensions=["tables", "fenced_code", "attr_list", "nl2br", "sane_lists", "toc"],
        output_format="html5",
    )


# ─── Transclusion + query blocks (Phase 3) ─────────────────────────────────────

_EMBED_IMG_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
_TRANSCLUDE_MAX_DEPTH = 4
_QUERY_FENCE_RE = re.compile(r"```query[ \t]*\n(.*?)\n```", re.DOTALL)


def query_notes(filters: dict, *, include_rules: bool = False, limit: int = 200) -> list[dict]:
    """Notes matching ALL filters, as [{title, path}] sorted by title. Keys:
    `folder` (path prefix), `tag` (a #tag in the body or a `tags:` frontmatter
    entry), and any other key = an exact case-insensitive frontmatter match
    (e.g. `type: npc`, `status: open`). Powers the ```query block + future
    'loose ends' / roster views."""
    root = get_vault_root()
    if root is None:
        return []
    folder = (filters.get("folder") or "").strip().strip("/").lower()
    tag = (filters.get("tag") or "").lstrip("#").strip().lower()
    fm_filters = {k.lower(): str(v).lower() for k, v in filters.items()
                  if k.lower() not in ("folder", "tag") and v}
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            try:
                rel = str(Path(dirpath, fn).relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
            if not include_rules and _is_rules(rel):
                continue
            if folder and not rel.lower().startswith(folder + "/"):
                continue
            try:
                fm, fbody = _split_frontmatter(Path(dirpath, fn).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            ok = True
            for k, v in fm_filters.items():
                fv = fm.get(k)
                if isinstance(fv, list):
                    if v not in [str(x).lower() for x in fv]:
                        ok = False
                        break
                elif str(fv).lower() != v:
                    ok = False
                    break
            if ok and tag:
                tags_fm = fm.get("tags") or []
                if not isinstance(tags_fm, list):
                    tags_fm = [tags_fm]
                tags_fm = [str(x).lower().lstrip("#") for x in tags_fm]
                in_body = re.search(r"(?<![\w/])#" + re.escape(tag) + r"\b", fbody.lower())
                if tag not in tags_fm and not in_body:
                    ok = False
            if ok:
                out.append({"title": fn[:-3], "path": rel})
    out.sort(key=lambda x: x["title"].lower())
    return out[:limit]


def _expand_queries(body: str, *, include_rules: bool, sink: list) -> str:
    """Replace each ```query block with a placeholder, rendering the matching
    notes into `sink` as an HTML list."""
    from urllib.parse import quote

    def repl(m):
        filters = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                filters[k.strip()] = v.strip()
        results = query_notes(filters, include_rules=include_rules)
        if results:
            items = "".join(
                f'<li><a class="wikilink" href="/gm/notes/view/{quote(r["path"])}">{_html_escape(r["title"])}</a></li>'
                for r in results
            )
            block = (f'<div class="note-query"><ul>{items}</ul>'
                     f'<div class="note-query-foot">{len(results)} note(s)</div></div>')
        else:
            block = '<div class="note-query note-query-empty">No matching notes.</div>'
        sink.append(block)
        return f"\x00NTX{len(sink) - 1}\x00"

    return _QUERY_FENCE_RE.sub(repl, body)


def _expand_transclusions(body: str, *, include_rules: bool, seen: set, depth: int, sink: list) -> str:
    """Replace non-image ![[note]] embeds with a placeholder, rendering the
    target note recursively into `sink`. Cycle- and depth-guarded."""
    from urllib.parse import quote

    def repl(m):
        if not m.group("embed"):
            return m.group(0)                    # plain [[link]] — handled later
        target = m.group("target").strip()
        if any(target.lower().endswith(ext) for ext in _EMBED_IMG_EXTS):
            return m.group(0)                    # image embed — handled later
        rel = resolve_wikilink(target, include_rules=include_rules)
        alias = (m.group("alias") or "").strip()
        display = alias or (rel.rsplit("/", 1)[-1][:-3] if rel else target.split("/")[-1])
        if not rel:
            return f'<span class="wikilink wikilink-broken">{_html_escape(display)}</span>'
        link = f'<a class="wikilink wikilink-embed" href="/gm/notes/view/{quote(rel)}">{_html_escape(display)}</a>'
        if depth >= _TRANSCLUDE_MAX_DEPTH or rel in seen:
            sink.append(f'<div class="note-transclude note-transclude-skip"><div class="note-transclude-h">{link}'
                        f' <span class="note-transclude-note">(embed not expanded)</span></div></div>')
            return f"\x00NTX{len(sink) - 1}\x00"
        try:
            _fm, tbody = _split_frontmatter(_safe_join(rel).read_text(encoding="utf-8", errors="replace"))
        except (NotePathError, OSError):
            return f'<span class="wikilink wikilink-broken">{_html_escape(display)}</span>'
        inner = _render_body_to_html(tbody, include_rules=include_rules, seen=seen | {rel}, depth=depth + 1)
        sink.append(f'<div class="note-transclude"><div class="note-transclude-h">{link}</div>{inner}</div>')
        return f"\x00NTX{len(sink) - 1}\x00"

    return _WIKILINK_RE.sub(repl, body)


def _render_body_to_html(body: str, *, include_rules: bool = False, seen: set = None, depth: int = 0) -> str:
    """Shared body→HTML pipeline: transclusions + ```query blocks → callouts →
    wikilinks → tags → markdown. The transcluded/query HTML is held aside behind
    NUL-delimited placeholders so the markdown pass can't mangle it, then
    re-injected (unwrapping the <p> the markdown lib puts around a placeholder)."""
    seen = seen if seen is not None else set()
    sink: list = []
    body = _expand_transclusions(body, include_rules=include_rules, seen=seen, depth=depth, sink=sink)
    body = _expand_queries(body, include_rules=include_rules, sink=sink)
    body = _preprocess_callouts(body)
    body = _preprocess_wikilinks(body, include_rules=include_rules)
    body = _preprocess_tags(body)
    html = _md_renderer().convert(body)

    def _inject(m):
        i = int(m.group(1))
        return sink[i] if 0 <= i < len(sink) else ""
    html = re.sub(r"<p>\s*\x00NTX(\d+)\x00\s*</p>", _inject, html)
    html = re.sub(r"\x00NTX(\d+)\x00", _inject, html)
    return html


@dataclass
class RenderedNote:
    rel_path: str
    title: str
    frontmatter: dict
    html: str
    mtime: float
    is_template: bool
    raw: str  # raw markdown (for the editor)


def render(rel_path: str, *, include_rules: bool = False) -> RenderedNote:
    root = get_vault_root()
    if root is None:
        raise NotePathError("Vault is not available")
    p = _safe_join(rel_path)
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    rel = str(p.relative_to(root)).replace(os.sep, "/")
    mtime = p.stat().st_mtime
    cached = _RENDER_CACHE.get(rel)
    if cached and cached[0] == mtime:
        meta = cached[2]
        return RenderedNote(
            rel_path=rel,
            title=meta["title"],
            frontmatter=meta["frontmatter"],
            html=cached[1],
            mtime=mtime,
            is_template=_is_template(rel),
            raw=meta["raw"],
        )
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    fm, body = _split_frontmatter(raw)
    title = (
        fm.get("title")
        or rel.rsplit("/", 1)[-1].rsplit(".md", 1)[0]
    )
    html = _render_body_to_html(body, include_rules=include_rules)
    # Bound the LRU
    if len(_RENDER_CACHE) >= _RENDER_CACHE_MAX:
        # Drop the oldest entry by mtime
        oldest = min(_RENDER_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _RENDER_CACHE.pop(oldest, None)
    _RENDER_CACHE[rel] = (mtime, html, {"title": title, "frontmatter": fm, "raw": raw})
    return RenderedNote(
        rel_path=rel,
        title=str(title),
        frontmatter=fm,
        html=html,
        mtime=mtime,
        is_template=_is_template(rel),
        raw=raw,
    )


# ─── Excerpt for inline embeds (Phase 3) ──────────────────────────────────────

def render_excerpt(rel_path: str, *, max_chars: int = 1800, include_rules: bool = False) -> RenderedNote:
    """Render a truncated version of a note for inline embedding (e.g. the
    Now Playing card on the GM hub). Truncates the BODY before render so
    the resulting HTML is always well-formed."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    p = _safe_join(rel_path)
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    fm, body = _split_frontmatter(raw)
    title = fm.get("title") or rel_path.rsplit("/", 1)[-1].rsplit(".md", 1)[0]
    if len(body) > max_chars:
        # Cut at a paragraph boundary near max_chars
        cut = body.rfind("\n\n", 0, max_chars)
        body = body[: cut if cut > 0 else max_chars] + "\n\n*…*"
    body = _preprocess_callouts(body)
    body = _preprocess_wikilinks(body, include_rules=include_rules)
    body = _preprocess_tags(body)
    html = _md_renderer().convert(body)
    return RenderedNote(
        rel_path=rel_path,
        title=str(title),
        frontmatter=fm,
        html=html,
        mtime=p.stat().st_mtime,
        is_template=_is_template(rel_path),
        raw="",
    )


# ─── Search ──────────────────────────────────────────────────────────────────

@dataclass
class SearchHit:
    rel_path: str
    title: str
    snippet: str
    kind: str  # "title" or "body"


def _build_snippet(body: str, query_lc: str, ctx: int = 60) -> str:
    idx = body.lower().find(query_lc)
    if idx < 0:
        return body[: 2 * ctx]
    start = max(0, idx - ctx)
    end = min(len(body), idx + len(query_lc) + ctx)
    snippet = body[start:end].replace("\n", " ").strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(body):
        snippet = snippet + "…"
    return snippet


def search(query: str, *, include_rules: bool = False, limit: int = 50) -> list[SearchHit]:
    """Two-phase search: filename match first (fast, lots of signal), then
    body grep across notes. Skip zzrules unless `include_rules=True`."""
    root = get_vault_root()
    if root is None or not query:
        return []
    q = query.strip()
    if len(q) < 2:
        return []
    q_lc = q.lower()
    hits: list[SearchHit] = []
    seen: set[str] = set()
    # Phase 1: title hits
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            try:
                rel = str(Path(dirpath, fname).relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
            if not include_rules and _is_rules(rel):
                continue
            if q_lc in fname.lower():
                title = fname[:-3]
                hits.append(SearchHit(rel_path=rel, title=title, snippet="", kind="title"))
                seen.add(rel)
                if len(hits) >= limit:
                    return hits
    # Phase 2: body hits
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            try:
                rel = str(Path(dirpath, fname).relative_to(root)).replace(os.sep, "/")
            except ValueError:
                continue
            if rel in seen:
                continue
            if not include_rules and _is_rules(rel):
                continue
            try:
                with open(Path(dirpath, fname), "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                continue
            if q_lc in body.lower():
                title = fname[:-3]
                hits.append(SearchHit(rel_path=rel, title=title, snippet=_build_snippet(body, q_lc), kind="body"))
                if len(hits) >= limit:
                    return hits
    return hits


# ─── Backlinks ────────────────────────────────────────────────────────────────

def backlinks(rel_path: str, *, include_rules: bool = False) -> list[dict]:
    """Return [{path, title, snippet}] for every note that links to `rel_path`.
    Resolution treats both the basename (e.g. `[[Romi]]`) and the full path."""
    _ensure_index(include_rules=include_rules)
    root = get_vault_root()
    if root is None:
        return []
    target_basename = rel_path.rsplit("/", 1)[-1]
    target_title = target_basename[:-3] if target_basename.endswith(".md") else target_basename
    keys = {target_title.lower(), rel_path.lower()}
    if rel_path.endswith(".md"):
        keys.add(rel_path[:-3].lower())
    results: list[dict] = []
    seen: set[str] = set()
    with _INDEX_LOCK:
        if _INBOUND is None:
            return []
        for k in keys:
            for origin in _INBOUND.get(k, []):
                if origin in seen:
                    continue
                seen.add(origin)
                title = origin.rsplit("/", 1)[-1].rsplit(".md", 1)[0]
                snippet = ""
                try:
                    with open((root / origin), "r", encoding="utf-8", errors="replace") as f:
                        body = f.read()
                    snippet = _build_snippet(body, target_title.lower(), ctx=50)[:160]
                except OSError:
                    pass
                results.append({"path": origin, "title": title, "snippet": snippet})
    results.sort(key=lambda h: h["title"].lower())
    return results


# ─── Save ────────────────────────────────────────────────────────────────────

class NoteConflict(Exception):
    """The note on disk has changed since the editor loaded it."""


def save(rel_path: str, body: str, expected_mtime: Optional[float] = None,
         *, commit_message: Optional[str] = None) -> RenderedNote:
    """Write a note. If `expected_mtime` is provided and the on-disk file's
    mtime differs (with a 1-second tolerance for filesystem precision), the
    write is rejected with ``NoteConflict`` so the editor can show a diff.

    The vault on the Railway volume is the source of truth; writes land
    directly in vault_data/. (`commit_message` is accepted for backward
    compatibility with existing callers but is no longer used.)"""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    p = _safe_join(rel_path)
    # Auto-create parent directories. Edits-from-the-website often land in
    # paths like Sessions/2026-05-10.md that don't exist yet on first push.
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and expected_mtime is not None:
        actual = p.stat().st_mtime
        if abs(actual - expected_mtime) > 1.0:
            raise NoteConflict(
                f"Disk mtime {actual} differs from expected {expected_mtime}"
            )
    # Atomic write — write to a sibling temp file, fsync, rename.
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        # Clean up the temp file on any failure so /tmp doesn't accrue
        # half-written files between disk-full / permission errors.
        try: tmp.unlink(missing_ok=True)
        except Exception: pass
        raise
    # Bust caches and rebuild the index lazily.
    _RENDER_CACHE.clear()  # links/transclusions/queries in OTHER notes may now be stale
    invalidate_tree_cache()
    invalidate_index()
    return render(rel_path)


# ─── Templates (for create-on-new) ────────────────────────────────────────────

# Built-in note templates surfaced by the "New note" picker. Kept in code (not
# in the vault) so create works on a brand-new vault and doesn't depend on the
# GM's own zz_templates/. `{title}` / `{date}` are filled at create time.
NOTE_TEMPLATES: dict[str, dict] = {
    "blank": {"label": "Blank", "body": ""},
    "session": {
        "label": "Session outline",
        "body": (
            "---\ntype: session\ndate: {date}\nstatus: planned\n---\n"
            "# {title}\n\n## Recap\n\n## Beats\n\n## NPCs present\n\n## Loose ends\n"
        ),
    },
    "npc": {
        "label": "NPC",
        "body": (
            "---\ntype: npc\nstatus: active\nlocation: \nfaction: \n---\n"
            "# {title}\n\n## Description\n\n## Hooks\n\n## Connections\n"
        ),
    },
    "beat": {
        "label": "Story beat",
        "body": (
            "---\ntype: beat\nstatus: open\n---\n"
            "# {title}\n\n## What happens\n\n## Stakes\n\n## Next step\n"
        ),
    },
}


def list_templates() -> list[dict]:
    """[{key, label}] for the New-note picker, in a stable order."""
    return [{"key": k, "label": v["label"]} for k, v in NOTE_TEMPLATES.items()]


def template_body(key: str, title: str) -> str:
    """Fill a template's `{title}`/`{date}` placeholders. Unknown key -> blank.
    Uses str.replace (not .format) so stray braces in a body never raise."""
    tpl = NOTE_TEMPLATES.get(key or "blank") or NOTE_TEMPLATES["blank"]
    return (
        tpl["body"]
        .replace("{title}", title)
        .replace("{date}", time.strftime("%Y-%m-%d"))
    )


# ─── Snapshots (restore points before destructive ops) ─────────────────────────

# Hidden dir inside the vault. Every walker here (tree / search / index build /
# .zip export) skips dot-dirs, so snapshots never clutter the vault or bloat a
# backup, but they persist on the Railway volume for manual recovery.
_SNAPSHOT_DIRNAME = ".snapshots"


def snapshot(rel_paths, *, label: str = "") -> Optional[str]:
    """Copy the given vault-relative notes into a timestamped folder under
    .snapshots/ before a destructive op (delete / rename-rewrite). Best-effort:
    returns the snapshot's rel dir on success, or None if nothing was copied.
    Never raises — a failed snapshot must not block the caller, which has
    already decided to act."""
    root = get_vault_root()
    if root is None:
        return None
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", (label or "snap")).strip("-")[:40] or "snap"
    snap_rel_dir = f"{_SNAPSHOT_DIRNAME}/{time.strftime('%Y%m%d-%H%M%S')}-{safe_label}"
    snap_root = root / snap_rel_dir
    copied = 0
    for rel in rel_paths or []:
        try:
            src = _safe_join(rel)
        except NotePathError:
            continue
        if not src.is_file():
            continue
        dest = snap_root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        except OSError:
            continue
    return snap_rel_dir if copied else None


# ─── Note CRUD ─────────────────────────────────────────────────────────────────

def create_note(rel_path: str, *, body: str = "") -> RenderedNote:
    """Create a new note (appends .md if missing). Raises FileExistsError if a
    note already exists at the path, NotePathError on traversal."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    if not rel_path.endswith(".md"):
        rel_path = rel_path + ".md"
    p = _safe_join(rel_path)
    if p.exists():
        raise FileExistsError(rel_path)
    # save() does the atomic write + parent mkdir + cache/index invalidation.
    return save(rel_path, body)


def delete_note(rel_path: str, *, take_snapshot: bool = True) -> Optional[str]:
    """Hard-delete a note. Snapshots it first by default so the GM can recover.
    Returns the snapshot dir (or None). Raises FileNotFoundError if the note
    doesn't exist, NotePathError on traversal."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    p = _safe_join(rel_path)
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    snap = snapshot([rel_path], label=f"delete-{Path(rel_path).name}") if take_snapshot else None
    p.unlink()
    _RENDER_CACHE.clear()
    invalidate_tree_cache()
    invalidate_index()
    return snap


def _rewrite_links(text, old_title, new_title, old_path_noext, new_path_noext, ambiguous):
    """Rewrite wikilink targets that point at the renamed note. Preserves the
    author's style (bare-title vs full-path) plus embed/heading/alias. Bare
    `[[Title]]` links are left untouched when the title is ambiguous (more than
    one note shares it) so we never hijack links meant for a same-named note.
    Returns (new_text, n_rewritten)."""
    count = 0

    def repl(m):
        nonlocal count
        target = m.group("target").strip()
        t_noext = target[:-3] if target.lower().endswith(".md") else target
        new_target = None
        if "/" in t_noext:
            # Path-style reference (unambiguous) → follow it to the new path.
            if t_noext.lower() == old_path_noext.lower():
                new_target = new_path_noext
        else:
            # Bare-title reference → new title, but skip when the title is
            # ambiguous so we never hijack a same-named note's links.
            if t_noext.lower() == old_title.lower() and not ambiguous:
                new_target = new_title
        if new_target is None or new_target == t_noext:
            return m.group(0)                      # no match, or a no-op rewrite
        count += 1
        out = f"{m.group('embed') or ''}[[{new_target}"
        if m.group("heading"):
            out += f"#{m.group('heading')}"
        if m.group("alias"):
            out += f"|{m.group('alias')}"
        return out + "]]"

    return _WIKILINK_RE.sub(repl, text), count


def rename_note(from_rel: str, to_rel: str) -> dict:
    """Rename/move a note and rewrite [[wikilinks]] across the vault to follow
    it (Obsidian-style). Snapshots the moved note + every referrer first, so a
    bad rewrite is recoverable. Returns {to, snapshot, referrers, rewritten}.
    Raises FileNotFoundError (source missing), FileExistsError (dest exists),
    NotePathError (traversal)."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    if not from_rel.endswith(".md"):
        from_rel += ".md"
    if not to_rel.endswith(".md"):
        to_rel += ".md"
    src = _safe_join(from_rel)
    dst = _safe_join(to_rel)
    if not src.is_file():
        raise FileNotFoundError(from_rel)
    if dst.exists():
        raise FileExistsError(to_rel)

    # Resolve referrers + ambiguity from the index BEFORE moving (keyed off the
    # old title/path).
    _ensure_index(include_rules=False)
    referrer_rels = [b["path"] for b in backlinks(from_rel)]
    old_title = Path(from_rel).name[:-3]
    with _INDEX_LOCK:
        ambiguous = len((_TITLE_INDEX or {}).get(old_title.lower(), [])) > 1

    # Snapshot the note + every referrer so the multi-file rewrite is undoable.
    snap = snapshot([from_rel] + referrer_rels, label=f"rename-{Path(from_rel).name}")

    # Move the file.
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)

    new_title = Path(to_rel).name[:-3]
    old_path_noext, new_path_noext = from_rel[:-3], to_rel[:-3]
    rewritten = 0
    for rel in referrer_rels:
        try:
            rp = _safe_join(rel)
            text = rp.read_text(encoding="utf-8", errors="replace")
        except (NotePathError, OSError):
            continue
        new_text, n = _rewrite_links(text, old_title, new_title, old_path_noext, new_path_noext, ambiguous)
        if n and new_text != text:
            try:
                tmp = rp.with_suffix(rp.suffix + ".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(new_text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, rp)
                rewritten += n
                _RENDER_CACHE.pop(rel, None)
            except OSError:
                continue

    _RENDER_CACHE.clear()
    invalidate_tree_cache()
    invalidate_index()
    return {"to": to_rel, "snapshot": snap, "referrers": len(referrer_rels), "rewritten": rewritten}


# ─── Folder operations ─────────────────────────────────────────────────────────

def _notes_under(rel_dir: str) -> list[str]:
    """All `.md` rel paths under a folder (recursive), excluding dot-dirs."""
    root = get_vault_root()
    base = _safe_join(rel_dir)
    out: list[str] = []
    if root is None or not base.is_dir():
        return out
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            if fn.endswith(".md"):
                out.append(str(Path(dp, fn).relative_to(root)).replace(os.sep, "/"))
    return out


def _atomic_write(rp: Path, text: str) -> bool:
    """Write text to rp atomically (temp + fsync + replace). Returns success."""
    try:
        tmp = rp.with_suffix(rp.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, rp)
        return True
    except OSError:
        return False


def _rewrite_path_links(text, pathmap):
    """Rewrite path-style wikilinks (target contains '/') whose target maps in
    `pathmap` (old_path_noext.lower() -> new_path_noext). Preserves embed/
    heading/alias. Returns (new_text, n_rewritten)."""
    count = 0

    def repl(m):
        nonlocal count
        target = m.group("target").strip()
        t_noext = target[:-3] if target.lower().endswith(".md") else target
        if "/" not in t_noext:
            return m.group(0)
        new_target = pathmap.get(t_noext.lower())
        if not new_target or new_target == t_noext:
            return m.group(0)
        count += 1
        out = f"{m.group('embed') or ''}[[{new_target}"
        if m.group("heading"):
            out += f"#{m.group('heading')}"
        if m.group("alias"):
            out += f"|{m.group('alias')}"
        return out + "]]"

    return _WIKILINK_RE.sub(repl, text), count


def create_folder(rel_dir: str) -> str:
    """Create an (empty) folder. Raises FileExistsError if it exists,
    NotePathError on traversal. Note: the browse tree hides empty folders, so
    a folder shows up once it has a note in it."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    d = _safe_join(rel_dir)
    if d.exists():
        raise FileExistsError(rel_dir)
    d.mkdir(parents=True)
    invalidate_tree_cache()
    return rel_dir.strip("/")


def delete_folder(rel_dir: str, *, take_snapshot: bool = True) -> dict:
    """Delete a folder and everything under it. Snapshots all contained notes
    first. Returns {deleted, snapshot}. Raises FileNotFoundError, NotePathError."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    d = _safe_join(rel_dir)
    if not d.is_dir():
        raise FileNotFoundError(rel_dir)
    contained = _notes_under(rel_dir)
    snap = snapshot(contained, label=f"delete-folder-{Path(rel_dir).name}") if (take_snapshot and contained) else None
    shutil.rmtree(d)
    _RENDER_CACHE.clear()
    invalidate_tree_cache()
    invalidate_index()
    return {"deleted": len(contained), "snapshot": snap}


def rename_folder(from_dir: str, to_dir: str) -> dict:
    """Rename/move a folder and rewrite path-style [[links]] across the vault to
    follow the notes inside it (titles are unchanged by a folder move, so only
    path-style links need rewriting). Snapshots moved notes + referrers first.
    Returns {to, moved, rewritten, snapshot}."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    src = _safe_join(from_dir)
    dst = _safe_join(to_dir)
    if not src.is_dir():
        raise FileNotFoundError(from_dir)
    if dst.exists():
        raise FileExistsError(to_dir)
    from_n, to_n = from_dir.strip("/"), to_dir.strip("/")

    moved = _notes_under(from_dir)                    # old rel paths
    remap = {rel: to_n + rel[len(from_n):] for rel in moved}   # old rel -> new rel

    _ensure_index(include_rules=False)
    referrers = set()
    for rel in moved:
        referrers.update(b["path"] for b in backlinks(rel))
    snap = snapshot(list(set(moved) | referrers), label=f"rename-folder-{Path(from_dir).name}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    os.rename(src, dst)

    # old_path_noext.lower() -> new_path_noext for every moved note.
    pathmap = {old[:-3].lower(): remap[old][:-3] for old in moved}
    rewritten = 0
    for rel in referrers:
        actual = remap.get(rel, rel)                  # referrer may itself have moved
        try:
            rp = _safe_join(actual)
            text = rp.read_text(encoding="utf-8", errors="replace")
        except (NotePathError, OSError):
            continue
        new_text, n = _rewrite_path_links(text, pathmap)
        if n and new_text != text and _atomic_write(rp, new_text):
            rewritten += n
            _RENDER_CACHE.pop(actual, None)

    _RENDER_CACHE.clear()
    invalidate_tree_cache()
    invalidate_index()
    return {"to": to_n, "moved": len(moved), "rewritten": rewritten, "snapshot": snap}


# ─── Editor support (Phase 2: live preview / autocomplete / attachments) ────────

def render_preview(raw: str, *, include_rules: bool = False) -> str:
    """Render an unsaved markdown buffer to HTML using the SAME callout/wikilink/
    tag passes as render(), so the split-pane live preview matches the saved
    view exactly. Frontmatter is stripped (not rendered), same as render()."""
    _fm, body = _split_frontmatter(raw or "")
    return _render_body_to_html(body, include_rules=include_rules)


def list_titles(*, include_rules: bool = False) -> list[dict]:
    """[{title, path}] for every note, for `[[` wikilink autocomplete. Title is
    the basename (case preserved); sorted case-insensitively by title."""
    _ensure_index(include_rules=include_rules)
    out: list[dict] = []
    with _INDEX_LOCK:
        for rels in (_TITLE_INDEX or {}).values():
            for rel in rels:
                name = rel.rsplit("/", 1)[-1]
                if name.endswith(".md"):
                    name = name[:-3]
                out.append({"title": name, "path": rel})
    out.sort(key=lambda x: x["title"].lower())
    return out


_ATTACHMENT_DIRNAME = "zz_Attachments"


def save_attachment(filename: str, data: bytes) -> str:
    """Save an uploaded attachment into zz_Attachments/, de-duping the filename.
    Returns the vault-relative path (for inserting an ![[..]] embed)."""
    if get_vault_root() is None:
        raise NotePathError("Vault is not available")
    # Use only the basename of the upload (drop any path components), then strip
    # characters illegal in filenames — prevents traversal regardless of input.
    base = (filename or '').replace('\\', '/').split('/')[-1]
    safe = re.sub(r'[:*?"<>|]+', '_', base).strip().lstrip('.') or "attachment"
    dest_dir = _safe_join(_ATTACHMENT_DIRNAME)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem, dot, ext = safe.rpartition(".")
    target = dest_dir / safe
    i = 1
    while target.exists():
        target = dest_dir / (f"{stem}-{i}.{ext}" if dot else f"{safe}-{i}")
        i += 1
    target.write_bytes(data)
    invalidate_tree_cache()
    root = get_vault_root()
    return str(target.relative_to(root)).replace(os.sep, "/")


# ─── Connection graph (Phase 4) ────────────────────────────────────────────────

def _note_type(rel: str) -> str:
    """Frontmatter `type` of a note (for graph node coloring), or ''."""
    try:
        fm, _ = _split_frontmatter(_safe_join(rel).read_text(encoding="utf-8", errors="replace"))
        t = fm.get("type")
        return str(t).lower() if t else ""
    except (NotePathError, OSError):
        return ""


def _graph_node(rel: str, *, with_type: bool) -> dict:
    name = rel.rsplit("/", 1)[-1]
    if name.endswith(".md"):
        name = name[:-3]
    node = {"id": rel, "title": name}
    if with_type:
        node["type"] = _note_type(rel)
    return node


def neighbors(rel_path: str, *, depth: int = 1, include_rules: bool = False) -> dict:
    """Local link graph around a note: the note plus everything within `depth`
    link-hops (outbound + inbound). Nodes carry frontmatter `type` for coloring.
    This is the default 'webs' view — a global graph hairballs on a big vault."""
    _ensure_index(include_rules=include_rules)
    if get_vault_root() is None:
        return {"nodes": [], "edges": [], "center": rel_path}
    if not rel_path.endswith(".md"):
        rel_path += ".md"
    with _INDEX_LOCK:
        outbound = dict(_OUTBOUND or {})

    def out_links(r):
        res = set()
        for tgt in outbound.get(r, []):
            d = resolve_wikilink(tgt, include_rules=include_rules)
            if d:
                res.add(d)
        return res

    def in_links(r):
        return {b["path"] for b in backlinks(r, include_rules=include_rules)}

    nodes = {rel_path}
    edges = set()
    frontier = {rel_path}
    for _ in range(max(1, depth)):
        nxt = set()
        for r in frontier:
            for d in out_links(r):
                edges.add((r, d))
                nxt.add(d)
            for s in in_links(r):
                edges.add((s, r))
                nxt.add(s)
        nxt -= nodes
        nodes |= nxt
        frontier = nxt
        if not frontier:
            break
    nodes = {n for n in nodes if include_rules or not _is_rules(n)}
    node_list = [_graph_node(n, with_type=True) for n in sorted(nodes)]
    edge_list = [{"source": s, "target": t} for (s, t) in edges
                 if s in nodes and t in nodes and s != t]
    return {"nodes": node_list, "edges": edge_list, "center": rel_path}


def graph(*, include_rules: bool = False) -> dict:
    """Whole-vault link graph {nodes, edges}. Omits per-node frontmatter type to
    stay cheap on large vaults; use neighbors() for the typed local view."""
    _ensure_index(include_rules=include_rules)
    if get_vault_root() is None:
        return {"nodes": [], "edges": []}
    with _INDEX_LOCK:
        outbound = dict(_OUTBOUND or {})
        title_index = dict(_TITLE_INDEX or {})
    rels = set()
    for rs in title_index.values():
        rels.update(rs)
    rels = {r for r in rels if include_rules or not _is_rules(r)}
    edges = []
    seen = set()
    for r in list(outbound.keys()):
        if r not in rels:
            continue
        for tgt in outbound[r]:
            d = resolve_wikilink(tgt, include_rules=include_rules)
            if d and d in rels and d != r and (r, d) not in seen:
                seen.add((r, d))
                edges.append({"source": r, "target": d})
    return {"nodes": [_graph_node(r, with_type=False) for r in sorted(rels)], "edges": edges}


# ─── Diagnostics ─────────────────────────────────────────────────────────────

def vault_status() -> dict:
    """Lightweight health check used by the `/gm/notes` empty state and a
    /api/notes/health endpoint. Verifies the source resolves AND that the
    process has read access — macOS TCC will block Documents/ access for
    processes that don't have Full Disk Access when reading the local
    symlink, and the empty tree that results is a confusing failure mode
    to debug otherwise."""
    src = vault_source()
    root = get_vault_root()
    if root is None:
        return {
            "available": False,
            "source": src,
            "vault_data_dir": str(_VAULT_DATA_DIR),
            "symlink_path": str(_VAULT_SYMLINK),
            "reason": "missing",
            "detail": (
                "No vault available. Run `python tools/push_vault.py` to push "
                "your local Obsidian vault to this server, or symlink "
                f"obsidian_vault/ at the project root for local development."
            ),
        }
    # Probe read access — raises PermissionError under macOS TCC if the
    # current process doesn't have Full Disk Access for ~/Documents.
    try:
        os.scandir(root).close()
    except PermissionError as e:
        return {
            "available": False,
            "source": src,
            "vault_data_dir": str(_VAULT_DATA_DIR),
            "symlink_path": str(_VAULT_SYMLINK),
            "vault_root": str(root),
            "reason": "permission",
            "detail": (
                "macOS denied read access. Grant Full Disk Access (or just "
                "Files & Folders → Documents) to the Terminal / Python "
                "binary running the Flask server: System Settings → Privacy "
                f"& Security → Full Disk Access. Underlying error: {e}"
            ),
        }
    except OSError as e:
        return {
            "available": False,
            "source": src,
            "vault_data_dir": str(_VAULT_DATA_DIR),
            "symlink_path": str(_VAULT_SYMLINK),
            "vault_root": str(root),
            "reason": "io",
            "detail": str(e),
        }
    # Count files for a quick "synced" indicator
    file_count = 0
    try:
        for _, _, files in os.walk(root):
            file_count += sum(1 for f in files if f.endswith(".md"))
            if file_count > 9999:
                break
    except OSError:
        pass
    last_push_at = None
    try:
        marker = root / ".vault_last_push"
        if marker.is_file():
            last_push_at = marker.stat().st_mtime
    except OSError:
        pass
    return {
        "available": True,
        "source": src,
        "vault_data_dir": str(_VAULT_DATA_DIR),
        "symlink_path": str(_VAULT_SYMLINK),
        "vault_root": str(root),
        "note_count": file_count,
        "last_push_at": last_push_at,
    }
