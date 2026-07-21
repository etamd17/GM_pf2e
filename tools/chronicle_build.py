"""Chronicle PR0 build tool: derive a spoiler-safe player vault from the GM
Obsidian vault. Runs on the GM's Mac. Stdlib-only core (optional Pillow later);
NOT imported by the Flask app.
"""
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("chronicle_build")

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")
_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


def _coerce_scalar(v):
    v = v.strip()
    if not v:
        return ""
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _split_flow(s):
    parts, cur, quote = [], "", None
    for ch in s:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def _parse_frontmatter(text):
    data, key = {}, None
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        item = _ITEM_RE.match(raw)
        if item and key is not None:
            if not isinstance(data.get(key), list):
                data[key] = []
            data[key].append(_coerce_scalar(item.group(1)))
            continue
        kv = _KV_RE.match(raw)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            if val == "":
                data[key] = ""  # may be overwritten by a following block list
            elif val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                data[key] = [_coerce_scalar(x) for x in _split_flow(inner)] if inner else []
            else:
                data[key] = _coerce_scalar(val)
    return data


def parse_note(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    frontmatter, body = {}, text
    m = _FRONTMATTER_RE.match(text)
    if m:
        frontmatter = _parse_frontmatter(m.group(1))
        body = m.group(2)
    return {"frontmatter": frontmatter, "body": body, "path": str(path)}


_SLUG_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


def slugify(title):
    """Turn a note title into a PR1-safe slug.

    Lowercases, collapses any run of non [a-z0-9] characters into a single
    '-', strips leading/trailing '-', and caps length to the 81-char ceiling
    PR1's manifest validation enforces (^[a-z0-9][a-z0-9-]{0,80}$). Falls
    back to "page" for empty, None, or all-punctuation input so a slug is
    always produced.
    """
    s = (title or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")[:81].strip("-")
    if not s or not _SLUG_OK.match(s):
        return "page"
    return s


_CALLOUT_INNER = re.compile(r"^\[!\s*(?P<kind>[^\]\s]+)\s*\]\s*(?P<title>.*)$")
_CALLOUT_MARKER_ANY = re.compile(r"\[!\s*[^\]\s]+\s*\]")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_OBSIDIAN_COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)

_KEEP_KINDS = {"quote", "example"}       # kept verbatim, callout syntax intact
_HARVEST = {"check": "fact", "question": "question"}
# ALLOWLIST policy: only _KEEP_KINDS and _HARVEST kinds (plus "abstract",
# handled separately below) ever leave this function. Every other kind -
# the known GM ones (danger/info/tip/warning) AND any unknown/custom kind
# (e.g. "spoiler_alert", "lore-bomb", "gm", "secret") - is stripped. Default
# = STRIP; nothing is kept or harvested unless explicitly allowlisted.


def _is_blockquote_line(line):
    """A line is blockquote content if it starts with '>' after leading
    whitespace is tolerated (indentation must not hide a callout)."""
    return line.lstrip().startswith(">")


def _strip_quote_markers(line):
    """Strip leading whitespace, then every leading '>' marker and the
    whitespace around it, at ANY depth ('>', '>>', '> >', indented or not).
    Returns the remaining inner text, used both to detect a callout header
    (regardless of nesting/indentation) and to recover a continuation
    line's content."""
    s = line.lstrip()
    while s.startswith(">"):
        s = s[1:].lstrip()
    return s


def _match_callout_header(line):
    """Detect a callout header at ANY '>' depth/indentation. Returns the
    match against the marker-stripped remainder (groups: kind, title), or
    None if the line - after stripping leading whitespace and '>' markers -
    isn't a ``[!kind] title`` header."""
    return _CALLOUT_INNER.match(_strip_quote_markers(line))


def _line_bounds(text, pos):
    """Return (start, end) offsets of the physical line containing `pos`
    within `text`. `start` is just past the previous newline (or 0);
    `end` is the index of that line's own trailing '\\n', or len(text) if
    the line runs to the end of the text with no trailing newline."""
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    if end == -1:
        end = len(text)
    return start, end


def _strip_comment_pattern(body, pattern):
    """Remove every match of a comment `pattern` (HTML ``<!--...-->`` or
    Obsidian ``%%...%%``) from `body`.

    A comment whose own text contains a callout marker (``[!kind]``) is
    treated as suspicious: it may be hiding a marker behind comment
    syntax so a callout header disappears while its secret body rides
    along as an orphaned blockquote continuation. To detect this, the
    text that a NORMAL strip would leave behind on the comment's line(s)
    - the prefix before the comment on its opening line, joined to the
    suffix after the comment on its closing line - is checked: if THAT
    would read as blockquote content (starts with '>'), the marker is
    live ammunition, not decoration, so the entire physical line(s) the
    comment spans are dropped wholesale (prefix and suffix both), rather
    than just the comment substring. This closes the case whether the
    comment is single-line (``> <!--[!danger] X-->secret``) or spans
    multiple physical lines (``> <!--`` / ``[!danger] X`` / ``-->secret``),
    since in both shapes the surviving prefix+suffix is what would have
    read as a kept/harvested blockquote line.

    A comment with no marker - or one whose surrounding prefix+suffix
    would NOT read as blockquote content - is stripped normally: only
    the comment text is removed, the rest of its line is kept intact.
    """
    out = []
    pos = 0
    for m in pattern.finditer(body):
        if m.start() < pos:
            continue  # already consumed by an earlier whole-line drop
        out.append(body[pos:m.start()])
        if _CALLOUT_MARKER_ANY.search(m.group(0)):
            open_start, _open_end = _line_bounds(body, m.start())
            _close_start, close_end = _line_bounds(body, m.end())
            prefix = body[open_start:m.start()]
            suffix = body[m.end():close_end]
            if (prefix + suffix).lstrip().startswith(">"):
                if out and out[-1].endswith(prefix):
                    out[-1] = out[-1][: len(out[-1]) - len(prefix)]
                has_nl = close_end < len(body) and body[close_end] == "\n"
                pos = close_end + 1 if has_nl else close_end
                continue
        pos = m.end()
    out.append(body[pos:])
    return "".join(out)


def strip_gm_content(body):
    """The spoiler firewall: split a note body into what players may see.

    Deliberately simple, provably-leak-safe model (PR0 redesign - the prior
    multi-line-comment state machine layered on the block walk had three
    incremental patches each close one leak while opening another):

    1. Strip ALL comments (Obsidian ``%%...%%`` and HTML ``<!--...-->``)
       from the RAW body FIRST, multi-line aware, via
       ``_strip_comment_pattern``. This is safe precisely because of step
       3 below: a comment that severs a callout block only ever orphans a
       bare '>' tail, and step 3 strips every bare '>' line
       unconditionally, regardless of why it ended up bare. The one case
       that isn't automatically safe is a comment that itself CONTAINS a
       callout marker ([!kind]) and, once normally stripped, would leave
       blockquote content behind on its line (e.g. a marker splice like
       ``> <!--[!danger] X-->secret`` hiding a header inside a comment so
       the block walk never sees it to reject) - ``_strip_comment_pattern``
       detects that case and drops the comment's ENTIRE physical line(s)
       instead of just the comment substring, so no orphaned secret prose
       ever reaches step 2/3 in the first place.
    2. Walk lines, grouping '>'-prefixed runs into callout blocks. Leading
       whitespace and '>'-depth are normalized throughout: a line is
       "blockquote content" if ``line.lstrip().startswith(">")`` (so an
       indented `` > [!danger]`` is still recognized), and a callout header
       is detected by stripping leading whitespace then ALL leading '>'
       markers (and the whitespace around them) before matching
       ``[!kind] title`` on the remainder - so ``>> [!danger] X``,
       ``  > [!danger] X`` and ``> [!danger] X`` all detect the same way.
       A block is a header line plus every immediately following
       blockquote line (at ANY depth/indent), up to (but not including) a
       non-blockquote line or ANOTHER callout header at any depth - so a
       nested ``>> [!danger]`` inside a kept ``[!quote]`` block STARTS A
       NEW block instead of riding along as the quote's continuation.
    3. Of those blocks, ONLY [!quote]/[!example] are kept verbatim
       (callout syntax intact). [!check]/[!question] are harvested into
       ``mysteries``; [!abstract] is harvested into ``recap_seed`` - built
       from the header line's own title text plus every continuation
       line's inner text (so a callout written entirely on the header
       line, with no '>' continuation at all, still harvests/seeds its
       title). Every other kind - known GM kinds (danger/info/tip/warning)
       and any unknown/custom kind alike, at any nesting depth - is
       dropped entirely, and so is any bare '>' blockquote that never had
       a callout header in the first place. In short: among '>'-prefixed
       content, only quote/example survive.
    4. Non-'>' lines are ordinary prose and pass through untouched. (A
       secret the GM authored as plain non-blockquote prose outside any
       callout is an accepted residual per the vault convention that
       secrets live in '>' callouts - the GM reviews the build output.)
    """
    body = _strip_comment_pattern(body, _HTML_COMMENT_RE)
    body = _strip_comment_pattern(body, _OBSIDIAN_COMMENT_RE)

    lines = body.split("\n")
    out_lines, mysteries, recap_seed = [], [], None
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if not _is_blockquote_line(line):
            out_lines.append(line)
            i += 1
            continue

        header = _match_callout_header(line)
        block = [line]
        j = i + 1
        while j < n and _is_blockquote_line(lines[j]) and _match_callout_header(lines[j]) is None:
            block.append(lines[j])
            j += 1

        if header is None:
            # A bare blockquote with no callout header at all - never part
            # of a kept block, so it never reaches player_body (allowlist
            # policy: only quote/example callouts survive among '>' lines).
            i = j
            continue

        kind = header.group("kind").lower()
        if kind in _KEEP_KINDS:
            out_lines.extend(block)              # verbatim
        else:
            title = (header.group("title") or "").strip()
            continuation = "\n".join(
                _strip_quote_markers(b) for b in block[1:]
            ).strip()
            content = "\n".join(p for p in (title, continuation) if p).strip()
            if kind == "abstract":
                recap_seed = content or None
            elif kind in _HARVEST:
                if content:
                    mysteries.append({"kind": _HARVEST[kind], "text": content})
            # else: every other kind (known GM kinds or unknown/custom) is
            # dropped entirely - see the ALLOWLIST policy note above.
        i = j

    player_body = "\n".join(out_lines)
    player_body = re.sub(r"\n{3,}", "\n\n", player_body).strip()
    return {
        "player_body": player_body + "\n" if player_body else "",
        "mysteries": mysteries,
        "recap_seed": recap_seed,
    }


def _iter_markdown(vault_dir):
    for root, _dirs, files in os.walk(str(vault_dir)):
        for name in files:
            if name.endswith(".md"):
                yield os.path.join(root, name)


def _is_completed(status):
    return str(status or "").strip().lower() in ("complete", "completed")


def select_entities(vault_dir):
    """Auto-propose which entities become player pages.

    Unions `npcs_encountered` / `areas_covered` across every note with
    `type: session_notes` and `status: completed`/`complete` (default-EXCLUDE:
    an entity never becomes a player page just for existing). `chronicle:
    true` on an `npc`/`location` note force-includes it even if never
    encountered; `chronicle: false` force-excludes it even if encountered -
    the override always wins over the encountered-union. `sessions` is the
    list of completed session-note dicts, sorted by `session_number`.
    """
    npcs, areas, sessions = set(), set(), []
    inc_npc, exc_npc, inc_area, exc_area = set(), set(), set(), set()

    for path in _iter_markdown(vault_dir):
        note = parse_note(path)
        fm = note.get("frontmatter") or {}
        ntype = fm.get("type")

        if ntype == "session_notes":
            if _is_completed(fm.get("status")):
                sessions.append(note)
                for n in fm.get("npcs_encountered") or []:
                    npcs.add(str(n).strip())
                for a in fm.get("areas_covered") or []:
                    areas.add(str(a).strip())

        elif ntype == "npc":
            name = str(fm.get("name") or "").strip()
            ch = fm.get("chronicle")
            if name and ch is True:
                inc_npc.add(name)
            elif name and ch is False:
                exc_npc.add(name)

        elif ntype == "location":
            code = str(fm.get("area_code") or fm.get("name") or "").strip()
            ch = fm.get("chronicle")
            if code and ch is True:
                inc_area.add(code)
            elif code and ch is False:
                exc_area.add(code)

    npcs = (npcs | inc_npc) - exc_npc
    areas = (areas | inc_area) - exc_area
    sessions.sort(key=lambda note: note.get("frontmatter", {}).get("session_number", 0))
    return {"npcs": npcs, "areas": areas, "sessions": sessions}


_WIKILINK_RE = re.compile(r"!?\[\[([^\]]+?)\]\]")


def resolve_wikilinks(body, title_to_slug):
    """Resolve Obsidian `[[wikilinks]]` against the published-title/asset map.

    `title_to_slug` double-duties as both membership sets: published page
    title -> slug, and copied asset filename -> asset path.

    - `[[X]]` / `[[X|alt]]`: if `X` is a published title, becomes a markdown
      link `[display](/chronicle/page/<slug>)` (display = alt if given, else
      X). If `X` is not published, degrades to the plain `display` text with
      no link syntax at all - unlinked text is the "not discovered yet"
      signal, never a broken/leaky link.
    - `[[X#Heading]]` / `[[X^blockref]]` (with or without `|alt`): the
      `#heading`/`^blockref` suffix is dropped before the title lookup (`X`
      is the dict key) and dropped from the display too when there's no
      alias - players never see raw `#`/`^` anchor syntax.
    - `![[img.png]]` embeds: becomes a markdown image `![img.png](<path>)`
      only when `img.png` is a copied asset; otherwise the embed is stripped
      entirely (an un-copied asset is never referenced).
    """

    def _repl(m):
        raw = m.group(0)
        target, _sep, alt = m.group(1).partition("|")
        target = target.strip()
        if raw.startswith("!"):  # embed
            if target in title_to_slug:
                return "![{}]({})".format(target, title_to_slug[target])
            return ""
        # Split off a `#heading` or `^blockref` suffix before the title lookup -
        # the dict is keyed on the base page title, and players should never see
        # the raw anchor syntax as link display text.
        base_title = re.split(r"[#^]", target, maxsplit=1)[0].strip()
        display = alt.strip() if alt else base_title
        if base_title in title_to_slug:
            return "[{}](/chronicle/page/{})".format(display, title_to_slug[base_title])
        return display

    return _WIKILINK_RE.sub(_repl, body)


_PAGE_LINK_RE = re.compile(r"/chronicle/page/([a-z0-9][a-z0-9-]{0,80})")


def build_backlinks(pages):
    """Invert the resolved outbound-link map: for each page, who links to it.

    `pages` is a list of `{slug, title, body}` dicts where `body` has already
    been through `resolve_wikilinks` (so outbound links appear as
    `/chronicle/page/<slug>`). Returns `{slug: [{"slug", "title"}, ...]}` -
    deduped (a page linking twice to the same target counts once) and
    self-links (a page linking to itself) are dropped. Ordering is
    deterministic: source pages appear in the backlink list in the same
    order as the input `pages` list.
    """
    title_by_slug = {p["slug"]: p.get("title", p["slug"]) for p in pages}
    backlinks = {p["slug"]: [] for p in pages}
    seen = {p["slug"]: set() for p in pages}
    for p in pages:
        src = p["slug"]
        for target in _PAGE_LINK_RE.findall(p.get("body") or ""):
            if target in backlinks and target != src and src not in seen[target]:
                backlinks[target].append({"slug": src, "title": title_by_slug[src]})
                seen[target].add(src)
    return backlinks


_SECTIONS = frozenset(("home", "recap", "cast", "atlas", "lore", "handout", "fieldguide"))
_PAGE_OPTIONAL = (
    "epithet", "tags", "session_introduced", "session_updated",
    "portrait", "pull_quote", "chapter", "backlinks",
)


def build_manifest(campaign_id, session_number, pages, mysteries, spine, calendar):
    """Emit the exact `manifest.json` shape PR1's `_chronicle_validate_manifest`
    accepts: `{schema_version: 1, campaign_id, session_number, generated_at,
    pages, mysteries, calendar, fieldguide: [], spine}`.

    Each input page dict carries the required `slug/section/title/source/
    recipients` plus any of the optional keys in `_PAGE_OPTIONAL`; optional
    keys are copied through only when present (and not None) so PR1 sees them
    absent rather than null. `title`/`source` fall back to their per-slug
    default on ANY falsy value (missing, "", or None), not just an absent
    key -- PR1's ingest requires `source` truthy and 400s otherwise, so an
    explicit `{"source": ""}` must not slip through as a truthy-looking key.
    `recipients` deliberately keeps the absent-only default (see the NOTE
    below). Raises `ValueError` - fail fast on the build machine - if a
    `slug` doesn't match `^[a-z0-9][a-z0-9-]{0,80}$` or a `section` isn't
    one of the allowed PR1 sections, rather than letting PR1's ingest 400
    on a malformed manifest.
    """
    out_pages = []
    for p in pages:
        slug = p.get("slug", "")
        if not _SLUG_OK.match(slug or ""):
            raise ValueError("invalid slug: {!r}".format(slug))
        section = p.get("section")
        if section not in _SECTIONS:
            raise ValueError("invalid section {!r} for slug {!r}".format(section, slug))
        entry = {
            "slug": slug,
            "section": section,
            "title": p.get("title") or slug,
            "source": p.get("source") or "content/{}.md".format(slug),
            # NOTE: recipients is deliberately NOT falsy-guarded -- an explicit
            # empty list is a meaningful "hidden from every player" value (see
            # app.py _chronicle_page_visible), distinct from an absent key
            # (-> "all"). Coercing [] to "all" here would silently make a
            # GM-only page public.
            "recipients": p.get("recipients", "all"),
        }
        for key in _PAGE_OPTIONAL:
            if p.get(key) is not None:
                entry[key] = p[key]
        out_pages.append(entry)

    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "session_number": session_number,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pages": out_pages,
        "mysteries": list(mysteries or []),
        "calendar": dict(calendar or {}),
        "fieldguide": [],
        "spine": list(spine or []),
    }


ASSET_BUDGET_BYTES = 48 * 1024 * 1024  # keep the whole zip under PR1's 48 MB cap
PLAYER_HANDOUTS = "Player Handouts"
_IMG_EXTS = frozenset((".png", ".jpg", ".jpeg", ".gif", ".webp"))
_EMBED_RE = re.compile(r"!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]")
_MD_IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _iter_asset_refs(pages):
    """Union every image reference a page carries: its `portrait` field,
    Obsidian `![[embed]]` syntax, and markdown `![alt](path)` syntax."""
    for page in pages:
        portrait = page.get("portrait")
        if portrait:
            yield portrait
        body = page.get("body") or ""
        for m in _EMBED_RE.finditer(body):
            yield m.group(1)
        for m in _MD_IMG_RE.finditer(body):
            yield m.group(1)


def _find_asset(vault_dir, base):
    """Locate a referenced image by basename anywhere under the vault's
    `Player Handouts/` tree (the only place secret-free assets live)."""
    handouts = os.path.join(str(vault_dir), PLAYER_HANDOUTS)
    for root, _dirs, files in os.walk(handouts):
        if base in files:
            return os.path.join(root, base)
    return None


def _strip_exif(src, dst):
    """Re-save an image without its metadata (EXIF, GPS, etc.) when Pillow
    is importable; otherwise (or on any decode/save failure) fall back to a
    plain byte copy. Pillow is a dev-only convenience -- the app runtime
    never needs it, so its absence must never be a crash."""
    try:
        from PIL import Image  # dev-only; absent in the app runtime
    except ImportError:
        shutil.copy2(src, dst)
        return
    try:
        img = Image.open(src)
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(dst)
    except Exception:  # any decode/save failure -> keep the pixels, drop the metadata concern
        shutil.copy2(src, dst)


def collect_assets(pages, vault_dir, out_assets_dir):
    """Copy the player images referenced by `pages` from the vault into
    `out_assets_dir`, EXIF-stripped when possible, and return the sorted
    list of copied basenames.

    Refs are the union of each page's `portrait` field, `![[embed]]`, and
    `![alt](path)` -- only the basename is used both for lookup (via
    `_find_asset`, scoped to `Player Handouts/**`) and for the copy
    destination, so a path-traversal ref (e.g. `../../secret.png`) can
    only ever resolve to a same-named file actually found under
    `Player Handouts/`, never escape `out_assets_dir`. An unreferenced
    image, or a referenced non-image file, is never copied. A running
    total enforces `ASSET_BUDGET_BYTES`: once adding an asset would
    exceed the budget, that asset (and only that asset) is logged and
    skipped -- smaller assets encountered later still get a chance.
    """
    os.makedirs(str(out_assets_dir), exist_ok=True)
    copied = []
    seen = set()
    total = 0
    for ref in _iter_asset_refs(pages):
        base = os.path.basename(str(ref).strip())
        if not base or base in seen:
            continue
        if os.path.splitext(base)[1].lower() not in _IMG_EXTS:
            continue
        src = _find_asset(vault_dir, base)
        if not src:
            log.warning("chronicle: referenced asset not found: %s", base)
            continue
        size = os.path.getsize(src)
        if total + size > ASSET_BUDGET_BYTES:
            log.warning("chronicle: asset budget exceeded, skipping %s (%d bytes)", base, size)
            continue
        _strip_exif(src, os.path.join(str(out_assets_dir), base))
        seen.add(base)
        total += size
        copied.append(base)
    return sorted(copied)
