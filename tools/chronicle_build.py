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
_CALLOUT_MARKER = re.compile(
    r"^>\s*\[!(?P<kind>[A-Za-z]+)\][-+]?\s?(?P<title>.*?)\s*$")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")

_KEEP_KINDS = {"quote", "example"}       # kept verbatim, callout syntax intact
_HARVEST = {"check": "fact", "question": "question"}
# every other kind (danger/info/tip/warning + any unknown) is stripped


def strip_gm_content(body):
    """The spoiler firewall: split a note body into what players may see.

    Walks Obsidian callout blocks (`> [!type] ...` header + its `>`-prefixed
    continuation lines). danger/info/tip/warning (and any callout kind not on
    the explicit allowlist below) are stripped entirely. quote/example are
    kept verbatim, callout syntax intact. check/question are harvested into
    ``mysteries``. abstract is pulled into ``recap_seed``. Obsidian
    ``%%comments%%`` and HTML ``<!--comments-->`` are stripped globally.

    A false negative here leaks GM secrets to players, so the continuation
    scan stops as soon as it sees a line that itself opens a NEW callout —
    otherwise a kept block (e.g. [!quote]) immediately followed by a
    [!danger] block with no blank line between them would swallow the
    danger's body into the quote's continuation lines and re-emit it
    verbatim.
    """
    body = _OBSIDIAN_COMMENT.sub("", body)
    body = _HTML_COMMENT.sub("", body)
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
        while j < n and lines[j].startswith(">") and not _CALLOUT_MARKER.match(lines[j]):
            block.append(lines[j])
            j += 1
        content = "\n".join(
            (_QUOTE_LINE.match(b).group(1) if _QUOTE_LINE.match(b) else b)
            for b in block[1:]
        ).strip()
        harvest_text = content if content else title

        if kind in _KEEP_KINDS:
            out_lines.extend(block)              # verbatim
        elif kind == "abstract":
            seed = " ".join(x for x in (title, content) if x).strip()
            recap_seed = seed or None
        elif kind in _HARVEST:
            if harvest_text:
                mysteries.append({"kind": _HARVEST[kind], "text": harvest_text})
        # else: danger/info/tip/warning/unknown -> dropped entirely
        i = j

    player_body = re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines)).strip()
    return {
        "player_body": player_body + "\n" if player_body else "",
        "mysteries": mysteries,
        "recap_seed": recap_seed,
    }
