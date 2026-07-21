"""Chronicle PR0 build tool: derive a spoiler-safe player vault from the GM
Obsidian vault. Runs on the GM's Mac. Stdlib-only core (optional Pillow later);
NOT imported by the Flask app.
"""
import re
from pathlib import Path

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


_OBSIDIAN_COMMENT = re.compile(r"%%.*?%%", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A line that is PURELY a comment (with or without a leading '>'), and
# nothing else. Used only to keep a bare comment line from severing a
# callout block during the continuation scan below - it must never be used
# to strip comments globally before the walk (see strip_gm_content).
_COMMENT_ONLY_LINE = re.compile(r"^>?\s*(?:%%.*?%%|<!--.*?-->)\s*$")


def _opens_unterminated_comment(line):
    """If `line` opens a multi-line comment that isn't also closed on the
    same line, return the delimiter that will close it ("-->" or "%%").
    Otherwise return None.

    Callers only reach this after `_COMMENT_ONLY_LINE` has already failed
    to match, so a line with a fully self-contained comment (open+close on
    one line) never gets here - this only fires for a genuine multi-line
    opener.
    """
    html_start = line.find("<!--")
    if html_start != -1 and "-->" not in line[html_start:]:
        return "-->"
    if line.count("%%") % 2 == 1:
        return "%%"
    return None


_CALLOUT_MARKER = re.compile(
    r"^>\s*\[!\s*(?P<kind>[^\]\s]+)\s*\][-+]?\s?(?P<title>.*?)\s*$")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")

_KEEP_KINDS = {"quote", "example"}       # kept verbatim, callout syntax intact
_HARVEST = {"check": "fact", "question": "question"}
# ALLOWLIST policy: only _KEEP_KINDS and _HARVEST kinds (plus "abstract",
# handled separately below) ever leave this function. Every other kind -
# the known GM ones (danger/info/tip/warning) AND any unknown/custom kind
# (e.g. "spoiler_alert", "lore-bomb", "gm", "secret") - is stripped. Default
# = STRIP; nothing is kept or harvested unless explicitly allowlisted.


def _strip_comments(text):
    text = _OBSIDIAN_COMMENT.sub("", text)
    return _HTML_COMMENT.sub("", text)


def strip_gm_content(body):
    """The spoiler firewall: split a note body into what players may see.

    Walks Obsidian callout blocks (`> [!type] ...` header + its `>`-prefixed
    continuation lines) on the RAW, uncleaned body. danger/info/tip/warning
    (and any callout kind not on the explicit allowlist above) are stripped
    entirely. quote/example are kept verbatim, callout syntax intact.
    check/question are harvested into ``mysteries``. abstract is pulled into
    ``recap_seed``. Obsidian ``%%comments%%`` and HTML ``<!--comments-->``
    are stripped from the FINAL player_body only, never globally before the
    walk - a comment line stripped up front would collapse a callout
    block's boundary and leak its tail with no marker at all.

    A false negative here leaks GM secrets to players, so the continuation
    scan stops as soon as it sees a line that itself opens a NEW callout —
    otherwise a kept block (e.g. [!quote]) immediately followed by a
    [!danger] block with no blank line between them would swallow the
    danger's body into the quote's continuation lines and re-emit it
    verbatim. A line that is PURELY a comment does NOT end the scan either -
    it is absorbed (and later scrubbed from the output) so the block's real
    tail line is never dropped out of the block unprotected. Only a genuine
    blank line, or a real non-comment non-'>' line, ends the block.

    The same protection applies to a MULTI-LINE comment (the opening
    delimiter on one physical line, the closing delimiter on a later one):
    an open-comment state is tracked across the continuation scan, so once
    a line opens an unterminated `<!--` or `%%` comment, every following
    line is absorbed into the block - regardless of what it contains,
    including a '>' continuation line or even a bare callout marker line -
    until the matching close is seen. An unterminated (never-closed)
    comment fails safe: it absorbs everything after it into the block, so
    the tail is stripped rather than leaked.
    """
    lines = body.split("\n")
    out_lines, mysteries, recap_seed = [], [], None
    i, n = 0, len(lines)
    while i < n:
        m = _CALLOUT_MARKER.match(lines[i])
        if not m:
            out_lines.append(lines[i])
            i += 1
            continue
        kind = m.group("kind").lower()
        title = m.group("title").strip()
        block = [lines[i]]
        j = i + 1
        comment_close = None  # None, or the delimiter ("-->"/"%%") needed
                               # to close a multi-line comment we're inside.
        while j < n:
            line = lines[j]
            if comment_close is not None:
                # Inside an unterminated multi-line comment: absorb no
                # matter what the line contains (it's a comment body, not
                # markdown), and clear the state once we see its close.
                block.append(line)
                j += 1
                if comment_close in line:
                    comment_close = None
                continue
            if _COMMENT_ONLY_LINE.match(line):
                # Absorb a bare comment line - it must not sever the block.
                block.append(line)
                j += 1
                continue
            opener = _opens_unterminated_comment(line)
            if opener:
                block.append(line)
                comment_close = opener
                j += 1
                continue
            if line.startswith(">") and not _CALLOUT_MARKER.match(line):
                block.append(line)
                j += 1
                continue
            break
        content = "\n".join(
            (_QUOTE_LINE.match(b).group(1) if _QUOTE_LINE.match(b) else b)
            for b in block[1:]
        ).strip()
        harvest_text = _strip_comments(content if content else title).strip()

        if kind in _KEEP_KINDS:
            out_lines.extend(block)              # verbatim
        elif kind == "abstract":
            seed = " ".join(x for x in (title, content) if x).strip()
            recap_seed = _strip_comments(seed).strip() or None
        elif kind in _HARVEST:
            if harvest_text:
                mysteries.append({"kind": _HARVEST[kind], "text": harvest_text})
        # else: every other kind (known GM kinds or unknown/custom) is
        # dropped entirely - see the ALLOWLIST policy note above.
        i = j

    player_body = _strip_comments("\n".join(out_lines))
    player_body = re.sub(r"\n{3,}", "\n\n", player_body).strip()
    return {
        "player_body": player_body + "\n" if player_body else "",
        "mysteries": mysteries,
        "recap_seed": recap_seed,
    }
