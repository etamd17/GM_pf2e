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
