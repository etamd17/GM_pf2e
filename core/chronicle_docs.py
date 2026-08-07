"""GM-uploaded Chronicle documents -- storage index + document conversion.

A SECOND, independent publishing lane alongside the Obsidian vault pipeline.
The two do not share storage, and that is forced rather than preferred:

  * `app._chronicle_swap` is a WHOLE-TREE replace. `current` points at exactly
    one `content/<hash>` dir and every vault read derives from that single
    manifest.json, so a doc living there would vanish on the next vault
    publish -- and its prune loop deletes every content dir that is not
    `current` or `previous` outright.
  * The vault publish dir is named for the sha256 of the uploaded zip, and an
    already-existing hash is treated as "same content, already live". A doc
    lane deriving a stable hash would silently discard new content.
  * Granularity differs by an order of magnitude: the vault rotates two
    whole-vault publishes; docs toggle one page at a time.

So docs live in `<chronicle_dir>/docs/` with a plain JSON index, no symlinks
and no rotation -- the per-document `published` flag IS the rollback. Avoiding
symlinks also keeps this lane working on Windows, where there is no atomic
directory-symlink swap (see `app._chronicle_repoint`).

The two lanes are unioned at read time by `app._chronicle_visible_pages`.

This module stays free of Flask and of `app` (so `app` can import it without a
circular dependency) and therefore does NOT sanitize: `docx_to_html` and
`text_to_html` return UNTRUSTED html. The caller MUST pass their output
through `app._chronicle_sanitize_html` before writing it anywhere a player can
reach -- `_chronicle_fragment` hands fragments to `|safe` with no checks of
its own, so that call is the only thing standing between a pasted `<script>`
and the table.
"""
from __future__ import annotations

import html
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile

from core import storage


SCHEMA_VERSION = 1
INDEX_NAME = 'index.json'

# Slug prefix for every uploaded doc. The vault lane owns the unprefixed
# namespace, so this makes cross-lane collisions structurally impossible
# instead of something both pipelines have to keep checking for.
SLUG_PREFIX = 'd-'

# Sections that actually reach a nav tab (app._CHRONICLE_SECTION_TO_NAV).
# Anything else would publish successfully and then be invisible in the
# navigation -- reachable only by direct URL -- so the upload refuses it.
SECTIONS = ('recap', 'lore', 'cast', 'handout')
DEFAULT_SECTION = 'lore'

EXT_MARKDOWN = ('.md', '.markdown')
EXT_TEXT = ('.txt',)
EXT_DOCX = ('.docx',)
ALLOWED_EXTS = EXT_MARKDOWN + EXT_TEXT + EXT_DOCX

# A .docx is a zip; cap what we will inflate out of it (mirrors the vault
# lane's _CHRONICLE_MAX_UNCOMPRESSED, scaled down for a single document).
MAX_DOCX_UNCOMPRESSED = 64 * 1024 * 1024


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S')


# --------------------------------------------------------------------------
# Index
# --------------------------------------------------------------------------
def index_file(docs_dir):
    return os.path.join(docs_dir, INDEX_NAME)


def load_index(docs_dir):
    doc = storage.load_json(index_file(docs_dir), default=None)
    if not isinstance(doc, dict) or not isinstance(doc.get('docs'), list):
        return {'schema_version': SCHEMA_VERSION, 'docs': []}
    doc.setdefault('schema_version', SCHEMA_VERSION)
    return doc


def save_index(docs_dir, index):
    index['schema_version'] = SCHEMA_VERSION
    index['updated_at'] = _now()
    storage.atomic_write_json(index_file(docs_dir), index, indent=2)
    return index


def find(index, doc_id):
    return next((d for d in index.get('docs', []) if d.get('id') == doc_id), None)


def published_docs(index):
    return [d for d in index.get('docs', []) if d.get('published')]


def slugify_title(title, fallback='document'):
    """`title` -> a slug matching app._CHRONICLE_SLUG_RE, always SLUG_PREFIX'd."""
    base = re.sub(r'[^a-z0-9]+', '-', (title or '').strip().lower()).strip('-')
    if not base or not base[0].isalnum():
        base = fallback
    # The route regex caps the whole slug at 81 chars; leave room for the
    # prefix and for a uniquifying suffix.
    return (SLUG_PREFIX + base)[:70]


def unique_slug(index, desired, ignore_id=None):
    taken = {d.get('slug') for d in index.get('docs', []) if d.get('id') != ignore_id}
    if desired not in taken:
        return desired
    for n in range(2, 1000):
        candidate = '%s-%d' % (desired, n)
        if candidate not in taken:
            return candidate
    return '%s-%s' % (desired, storage.new_id()[:8])


def new_entry(*, doc_id, slug, title, section, original_filename, source_ext,
              byte_count, warnings=None):
    now = _now()
    return {
        'id': doc_id,
        'slug': slug,
        'title': title,
        'section': section if section in SECTIONS else DEFAULT_SECTION,
        'recipients': 'all',
        'published': False,          # uploads are always private until toggled
        'original_filename': original_filename,
        'source_ext': source_ext,
        'byte_count': int(byte_count or 0),
        'warnings': list(warnings or []),
        'uploaded_at': now,
        'updated_at': now,
    }


def as_page(entry):
    """Project an index entry into the page dict shape the read side expects.

    Mirrors a vault manifest page closely enough that every existing
    `/chronicle*` route and template renders it with no changes. `_lane` tells
    `_chronicle_fragment` which store to read from and is stripped before the
    dict reaches a template.
    """
    return {
        'slug': entry['slug'],
        'title': entry.get('title') or 'Untitled',
        'section': entry.get('section') or DEFAULT_SECTION,
        'recipients': entry.get('recipients', 'all'),
        'session_updated': entry.get('session_updated'),
        '_lane': 'docs',
    }


# --------------------------------------------------------------------------
# Plain text
# --------------------------------------------------------------------------
def text_to_html(text):
    """Render .txt as literal prose: escaped, blank-line-separated paragraphs.

    Deliberately NOT run through the markdown renderer. A .txt file is plain
    text by definition, and markdown would silently reinterpret a GM's
    asterisks, underscores and leading '#' as formatting.
    """
    out = []
    for block in re.split(r'\n\s*\n', (text or '').replace('\r\n', '\n').strip()):
        block = block.strip()
        if block:
            out.append('<p>' + html.escape(block).replace('\n', '<br>') + '</p>')
    return '\n'.join(out)


# --------------------------------------------------------------------------
# .docx  (Office Open XML, parsed with the stdlib -- no new dependency)
# --------------------------------------------------------------------------
_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'

_HEADING_RE = re.compile(r'^heading([1-9])$', re.I)


class DocxError(ValueError):
    """A .docx that cannot be read as one."""


def _docx_member(zf, name):
    try:
        return zf.read(name)
    except KeyError:
        return None


def _rel_targets(zf):
    """r:id -> target URL, for hyperlinks."""
    raw = _docx_member(zf, 'word/_rels/document.xml.rels')
    if not raw:
        return {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    out = {}
    for rel in root.findall(_PKG_REL + 'Relationship'):
        rid, target = rel.get('Id'), rel.get('Target')
        if rid and target and rel.get('TargetMode') == 'External':
            out[rid] = target
    return out


def _list_formats(zf):
    """numId -> {ilvl: numFmt}, so an ordered list renders as <ol>."""
    raw = _docx_member(zf, 'word/numbering.xml')
    if not raw:
        return {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return {}
    abstract = {}
    for node in root.findall(_W + 'abstractNum'):
        aid = node.get(_W + 'abstractNumId')
        levels = {}
        for lvl in node.findall(_W + 'lvl'):
            fmt = lvl.find(_W + 'numFmt')
            levels[lvl.get(_W + 'ilvl') or '0'] = (fmt.get(_W + 'val') if fmt is not None else 'bullet')
        abstract[aid] = levels
    out = {}
    for num in root.findall(_W + 'num'):
        ref = num.find(_W + 'abstractNumId')
        if ref is not None:
            out[num.get(_W + 'numId')] = abstract.get(ref.get(_W + 'val'), {})
    return out


def _run_html(run):
    """One w:r -> escaped, emphasis-wrapped HTML."""
    props = run.find(_W + 'rPr')
    parts = []
    for node in run:
        if node.tag == _W + 't':
            parts.append(html.escape(node.text or ''))
        elif node.tag == _W + 'br':
            parts.append('<br>')
        elif node.tag == _W + 'tab':
            parts.append(' ')
    text = ''.join(parts)
    if not text:
        return ''
    if props is not None:
        # w:b with w:val="0"/"false" means explicitly OFF, not on.
        def _on(tag):
            el = props.find(_W + tag)
            return el is not None and (el.get(_W + 'val') or 'true') not in ('0', 'false')
        if _on('b'):
            text = '<strong>%s</strong>' % text
        if _on('i'):
            text = '<em>%s</em>' % text
        if _on('strike'):
            text = '<s>%s</s>' % text
        if props.find(_W + 'u') is not None:
            text = '<u>%s</u>' % text
    return text


def _para_inline_html(para, rels):
    """Runs + hyperlinks of one w:p, in document order."""
    out = []
    for node in para:
        if node.tag == _W + 'r':
            out.append(_run_html(node))
        elif node.tag == _W + 'hyperlink':
            inner = ''.join(_run_html(r) for r in node.findall(_W + 'r'))
            if not inner:
                continue
            target = rels.get(node.get(_R + 'id'))
            # No scheme filtering here -- the caller's sanitizer owns URL
            # policy (_chronicle_safe_url) and is the single source of truth.
            out.append('<a href="%s">%s</a>' % (html.escape(target, quote=True), inner)
                       if target else inner)
    return ''.join(out).strip()


def _para_meta(para):
    """(style, numId, ilvl) for one w:p."""
    props = para.find(_W + 'pPr')
    if props is None:
        return '', None, 0        # '' not None -- callers regex-match `style`
    style_el = props.find(_W + 'pStyle')
    style = (style_el.get(_W + 'val') if style_el is not None else None) or ''
    num_pr = props.find(_W + 'numPr')
    num_id = ilvl = None
    if num_pr is not None:
        n, l = num_pr.find(_W + 'numId'), num_pr.find(_W + 'ilvl')
        num_id = n.get(_W + 'val') if n is not None else None
        ilvl = l.get(_W + 'val') if l is not None else '0'
    return style, num_id, int(ilvl or 0)


def _table_html(tbl, rels):
    rows = []
    for tr in tbl.findall(_W + 'tr'):
        cells = []
        for tc in tr.findall(_W + 'tc'):
            inner = ' '.join(filter(None, (_para_inline_html(p, rels)
                                           for p in tc.findall(_W + 'p'))))
            cells.append('<td>%s</td>' % inner)
        if cells:
            rows.append('<tr>%s</tr>' % ''.join(cells))
    return '<table><tbody>%s</tbody></table>' % ''.join(rows) if rows else ''


def docx_to_html(path):
    """Convert a .docx to an HTML fragment. Returns UNSANITIZED html.

    Stdlib only -- a .docx is a zip of XML, and adding a converter library to
    a service that auto-deploys `main` is not worth it for prose. Handles
    headings, paragraphs, bold/italic/underline/strike, ordered and unordered
    lists (with nesting), tables and external hyperlinks. Word constructs with
    no allowlisted HTML equivalent -- text boxes, colours, fonts, alignment,
    headers/footers, page layout -- are dropped, exactly as they would be by
    the sanitizer even if we emitted them.

    Raises DocxError if the file is not a readable .docx.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxError('not a readable .docx file') from exc

    with zf:
        total = sum(i.file_size for i in zf.infolist())
        if total > MAX_DOCX_UNCOMPRESSED:
            raise DocxError('document is too large when decompressed')
        raw = _docx_member(zf, 'word/document.xml')
        if raw is None:
            raise DocxError('not a Word document (no word/document.xml)')
        rels = _rel_targets(zf)
        formats = _list_formats(zf)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise DocxError('the document XML is malformed') from exc

    body = root.find(_W + 'body')
    if body is None:
        raise DocxError('the document has no body')

    out = []
    list_stack = []          # [(tag, ilvl)] -- innermost last

    def close_lists(to_depth=0):
        while len(list_stack) > to_depth:
            out.append('</%s>' % list_stack.pop()[0])

    for node in body:
        if node.tag == _W + 'tbl':
            close_lists()
            table = _table_html(node, rels)
            if table:
                out.append(table)
            continue
        if node.tag != _W + 'p':
            continue

        style, num_id, ilvl = _para_meta(node)
        inline = _para_inline_html(node, rels)

        if num_id is not None:
            if not inline:
                continue
            fmt = (formats.get(num_id) or {}).get(str(ilvl), 'bullet')
            tag = 'ul' if fmt == 'bullet' else 'ol'
            close_lists(ilvl + 1)
            if len(list_stack) == ilvl + 1 and list_stack[-1][0] != tag:
                out.append('</%s>' % list_stack.pop()[0])   # same depth, new kind
            while len(list_stack) < ilvl + 1:
                out.append('<%s>' % tag)
                list_stack.append((tag, len(list_stack)))
            out.append('<li>%s</li>' % inline)
            continue

        close_lists()
        if not inline:
            continue
        heading = _HEADING_RE.match(style)
        if heading:
            level = min(int(heading.group(1)) + 1, 6)   # Word Heading 1 -> <h2>
            out.append('<h%d>%s</h%d>' % (level, inline, level))
        elif style.lower() == 'title':
            out.append('<h1>%s</h1>' % inline)
        elif style.lower() in ('quote', 'intensequote'):
            out.append('<blockquote><p>%s</p></blockquote>' % inline)
        else:
            out.append('<p>%s</p>' % inline)

    close_lists()
    return '\n'.join(out)


_LEADING_H1_RE = re.compile(r'\A\s*<h1[^>]*>(.*?)</h1>\s*', re.I | re.S)


def split_leading_heading(fragment):
    """(heading_text_or_None, fragment_without_it).

    A GM's document almost always opens with its own title -- a Word `Title`
    paragraph, or a markdown `# Heading`. The page template already prints
    `page.title` above the body, so leaving that first heading in place
    renders the name twice. Lifting it out gives a better default title than
    the filename ("The Salted Gull" rather than "Tavern") AND removes the
    duplicate in one step. Only the FIRST heading, and only if the document
    leads with it -- a document that opens with prose is left untouched.
    """
    match = _LEADING_H1_RE.match(fragment or '')
    if not match:
        return None, fragment
    text = re.sub(r'<[^>]+>', '', match.group(1))
    text = html.unescape(text).strip()
    if not text:
        return None, fragment
    return text, fragment[match.end():]


def plain_text_from_html(fragment, limit=400):
    """A short preview blurb: tags stripped, entities decoded, collapsed."""
    text = re.sub(r'<[^>]+>', ' ', fragment or '')
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]
