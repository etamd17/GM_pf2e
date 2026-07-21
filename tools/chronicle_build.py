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


_CALLOUT_HEADER = re.compile(r"^>\s*\[!\s*(?P<kind>[^\]\s]+)\s*\]")
_QUOTE_LINE = re.compile(r"^>\s?(.*)$")

_KEEP_KINDS = {"quote", "example"}       # kept verbatim, callout syntax intact
_HARVEST = {"check": "fact", "question": "question"}
# ALLOWLIST policy: only _KEEP_KINDS and _HARVEST kinds (plus "abstract",
# handled separately below) ever leave this function. Every other kind -
# the known GM ones (danger/info/tip/warning) AND any unknown/custom kind
# (e.g. "spoiler_alert", "lore-bomb", "gm", "secret") - is stripped. Default
# = STRIP; nothing is kept or harvested unless explicitly allowlisted.


def strip_gm_content(body):
    """The spoiler firewall: split a note body into what players may see.

    Deliberately simple, provably-leak-safe model (PR0 redesign - the prior
    multi-line-comment state machine layered on the block walk had three
    incremental patches each close one leak while opening another):

    1. Strip ALL comments (Obsidian ``%%...%%`` and HTML ``<!--...-->``)
       from the RAW body FIRST, multi-line aware. This is safe precisely
       because of step 3 below: a comment that severs a callout block only
       ever orphans a bare '>' tail, and step 3 strips every bare '>' line
       unconditionally, regardless of why it ended up bare.
    2. Walk lines, grouping '>'-prefixed runs into callout blocks. A block
       is a header line matching ``> [!kind] ...`` plus every immediately
       following '>' line, up to (but not including) a non-'>' line or
       another header line (a new block starting with no blank line
       between them).
    3. Of those blocks, ONLY [!quote]/[!example] are kept verbatim
       (callout syntax intact). [!check]/[!question] are harvested into
       ``mysteries``; [!abstract] is harvested into ``recap_seed``. Every
       other kind - known GM kinds (danger/info/tip/warning) and any
       unknown/custom kind alike - is dropped entirely, and so is any bare
       '>' blockquote that never had a callout header in the first place.
       In short: among '>'-prefixed content, only quote/example survive.
    4. Non-'>' lines are ordinary prose and pass through untouched. (A
       secret the GM authored as plain non-blockquote prose outside any
       callout is an accepted residual per the vault convention that
       secrets live in '>' callouts - the GM reviews the build output.)
    """
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = re.sub(r"%%.*?%%", "", body, flags=re.DOTALL)

    lines = body.split("\n")
    out_lines, mysteries, recap_seed = [], [], None
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if not line.startswith(">"):
            out_lines.append(line)
            i += 1
            continue

        header = _CALLOUT_HEADER.match(line)
        block = [line]
        j = i + 1
        while j < n and lines[j].startswith(">") and not _CALLOUT_HEADER.match(lines[j]):
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
            content = "\n".join(
                (_QUOTE_LINE.match(b).group(1) if _QUOTE_LINE.match(b) else b)
                for b in block[1:]
            ).strip()
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
