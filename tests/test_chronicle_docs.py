"""GM document-upload Chronicle lane: conversion, auth, publish toggle, merge.

Auth here uses a REAL player session (legacy mode + a GM_PASSWORD, no
gm_authenticated flag) rather than monkeypatching _is_gm. That distinction is
load-bearing: `gm_required` and the GM_API_PREFIXES gate are both just
`if _is_gm()`, so a suite that stubs _is_gm proves nothing about whether a
player is kept out -- and with NO GM_PASSWORD set, _is_gm() returns True for
everyone, which would make every leak assertion below pass vacuously.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import pytest

import app
from core import chronicle_docs as cdocs, storage


CID = 'c' * 32


# ==========================================================================
# .docx construction — build real OOXML so the parser is tested against the
# actual format, not a mock of it.
# ==========================================================================
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

_DOC_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rHyper" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.com/lore" TargetMode="External"/>
</Relationships>"""

_NUMBERING = """<?xml version="1.0"?>
<w:numbering xmlns:w="%s">
  <w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl></w:abstractNum>
  <w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>""" % _W


def _docx_bytes(body_xml, *, numbering=True):
    document = ('<?xml version="1.0"?><w:document xmlns:w="%s" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<w:body>%s</w:body></w:document>' % (_W, body_xml))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('_rels/.rels', _RELS)
        zf.writestr('word/document.xml', document)
        zf.writestr('word/_rels/document.xml.rels', _DOC_RELS)
        if numbering:
            zf.writestr('word/numbering.xml', _NUMBERING)
    return buf.getvalue()


def _para(text, style=None, bold=False, italic=False):
    props = '<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style if style else ''
    rpr = ''
    if bold or italic:
        rpr = '<w:rPr>%s%s</w:rPr>' % ('<w:b/>' if bold else '', '<w:i/>' if italic else '')
    return '<w:p>%s<w:r>%s<w:t>%s</w:t></w:r></w:p>' % (props, rpr, text)


def _list_item(text, num_id='1', ilvl='0'):
    return ('<w:p><w:pPr><w:numPr><w:ilvl w:val="%s"/><w:numId w:val="%s"/></w:numPr></w:pPr>'
            '<w:r><w:t>%s</w:t></w:r></w:p>' % (ilvl, num_id, text))


# ==========================================================================
# Conversion (pure, no Flask)
# ==========================================================================

def test_docx_headings_paragraphs_and_emphasis(tmp_path):
    path = tmp_path / 'a.docx'
    path.write_bytes(_docx_bytes(
        _para('The Sunken Vault', style='Title')
        + _para('Approach', style='Heading1')
        + _para('bolded', bold=True)
        + _para('slanted', italic=True)
        + _para('plain prose')))
    html = cdocs.docx_to_html(str(path))
    assert '<h1>The Sunken Vault</h1>' in html
    assert '<h2>Approach</h2>' in html          # Word Heading 1 -> <h2>
    assert '<strong>bolded</strong>' in html
    assert '<em>slanted</em>' in html
    assert '<p>plain prose</p>' in html


def test_docx_bullet_and_numbered_lists(tmp_path):
    path = tmp_path / 'b.docx'
    path.write_bytes(_docx_bytes(
        _list_item('first bullet', num_id='1')
        + _list_item('second bullet', num_id='1')
        + _para('between')
        + _list_item('step one', num_id='2')))
    html = cdocs.docx_to_html(str(path))
    assert html.count('<ul>') == 1 and html.count('</ul>') == 1
    assert html.count('<ol>') == 1 and html.count('</ol>') == 1
    assert '<li>first bullet</li>' in html
    assert '<li>step one</li>' in html
    # The intervening paragraph must close the bullet list, not sit inside it.
    assert html.index('</ul>') < html.index('<p>between</p>')


def test_docx_escapes_text_rather_than_emitting_markup(tmp_path):
    path = tmp_path / 'c.docx'
    path.write_bytes(_docx_bytes(_para('&lt;script&gt;alert(1)&lt;/script&gt; &amp; more')))
    html = cdocs.docx_to_html(str(path))
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_docx_external_hyperlink_is_kept(tmp_path):
    path = tmp_path / 'd.docx'
    path.write_bytes(_docx_bytes(
        '<w:p><w:hyperlink r:id="rHyper"><w:r><w:t>the lore</w:t></w:r></w:hyperlink></w:p>'))
    html = cdocs.docx_to_html(str(path))
    assert '<a href="https://example.com/lore">the lore</a>' in html


def test_docx_rejects_a_non_docx(tmp_path):
    path = tmp_path / 'e.docx'
    path.write_bytes(b'this is definitely not a zip')
    with pytest.raises(cdocs.DocxError):
        cdocs.docx_to_html(str(path))


def test_docx_rejects_a_zip_without_a_word_document(tmp_path):
    path = tmp_path / 'f.docx'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('hello.txt', 'not word')
    path.write_bytes(buf.getvalue())
    with pytest.raises(cdocs.DocxError):
        cdocs.docx_to_html(str(path))


def test_txt_is_literal_not_markdown():
    # A .txt is plain text; markdown would silently reinterpret these.
    html = cdocs.text_to_html('*not italic* and # not a heading\n\nsecond para')
    assert '<em>' not in html and '<h1>' not in html
    assert '*not italic*' in html
    assert html.count('<p>') == 2


def test_txt_escapes_html():
    assert '<script>' not in cdocs.text_to_html('<script>alert(1)</script>')


def test_leading_heading_is_lifted_out_of_the_body():
    # The page template already prints page.title above the body, so a
    # document that opens with its own title would otherwise show it twice.
    heading, rest = cdocs.split_leading_heading('<h1>The Salted Gull</h1>\n<p>Ale.</p>')
    assert heading == 'The Salted Gull'
    assert '<h1>' not in rest and '<p>Ale.</p>' in rest


def test_leading_heading_only_strips_a_LEADING_one():
    body = '<p>An opening line.</p>\n<h1>Later heading</h1>'
    heading, rest = cdocs.split_leading_heading(body)
    assert heading is None
    assert rest == body


def test_doc_slugs_are_prefixed_and_unique():
    assert cdocs.slugify_title('The Sunken Vault').startswith(cdocs.SLUG_PREFIX)
    index = {'docs': [{'id': '1', 'slug': 'd-vault'}]}
    assert cdocs.unique_slug(index, 'd-vault') == 'd-vault-2'
    assert cdocs.unique_slug(index, 'd-vault', ignore_id='1') == 'd-vault'


# ==========================================================================
# HTTP: real auth, upload -> preview -> publish -> player visibility
# ==========================================================================

class _Roles:
    """One test client whose session flips between GM and player.

    Deliberately ONE client: two concurrently-open `test_client()` context
    managers push two app contexts and Flask tears them down out of order
    ("Popped wrong app context"). Flipping the session on a single client
    exercises the same real _is_gm branches without that.
    """

    def __init__(self, client):
        self.client = client

    def gm(self):
        with self.client.session_transaction() as sess:
            sess['gm_authenticated'] = True
            sess.pop('player_name', None)
        return self.client

    def player(self):
        with self.client.session_transaction() as sess:
            sess.pop('gm_authenticated', None)
            sess['player_name'] = 'Kyle'
        return self.client


@pytest.fixture
def roles(tmp_path, monkeypatch):
    """Legacy mode with a GM_PASSWORD set, so _is_gm() runs for real.

    Without a GM_PASSWORD _is_gm() returns True for EVERYONE, and every
    player-visibility assertion in this file would pass vacuously.
    """
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', 'gm-secret')
    with app.app.test_client() as client:
        yield _Roles(client)


def test_the_role_fixture_actually_separates_gm_from_player(roles):
    # Guards every assertion below: if the player session were treated as GM,
    # the visibility tests would prove nothing.
    assert roles.gm().get('/api/chronicle/docs').status_code == 200
    assert roles.player().get('/api/chronicle/docs').status_code == 403


def _upload(client, name='Notes.md', data=b'# Ruins\n\nA cold wind.', section='lore', title=None):
    payload = {'document': (io.BytesIO(data), name), 'section': section}
    if title:
        payload['title'] = title
    return client.post('/api/chronicle/docs', data=payload,
                       content_type='multipart/form-data')


def _publish(roles, doc_id):
    """Preview, then publish -- publishing is gated on having previewed."""
    assert roles.gm().get('/chronicle/preview/' + doc_id).status_code == 200
    response = roles.gm().patch('/api/chronicle/docs/' + doc_id, json={'published': True})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def test_upload_then_publish_then_player_sees_it(roles):
    created = _upload(roles.gm(), title='The Ruins')
    assert created.status_code == 201, created.get_data(as_text=True)
    doc = created.get_json()['doc']
    assert doc['published'] is False        # uploads are private by default
    slug = doc['slug']

    # Unpublished: invisible to the player at every surface.
    assert roles.player().get('/chronicle/page/' + slug).status_code == 404
    assert 'The Ruins' not in roles.player().get('/chronicle/lore').get_data(as_text=True)

    published = _publish(roles, doc['id'])
    assert published.get_json()['doc']['published'] is True

    page = roles.player().get('/chronicle/page/' + slug)
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'Ruins' in body and 'A cold wind' in body
    assert 'The Ruins' in roles.player().get('/chronicle/lore').get_data(as_text=True)

    # Unpublishing takes it straight back out again -- the toggle IS the rollback.
    roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'published': False})
    assert roles.player().get('/chronicle/page/' + slug).status_code == 404


def test_docx_upload_renders_for_players(roles):
    created = _upload(roles.gm(), name='Lore.docx',
                      data=_docx_bytes(_para('Chapter One', style='Heading1')
                                       + _para('The gate stood open.')),
                      title='Field Notes')
    assert created.status_code == 201, created.get_data(as_text=True)
    doc = created.get_json()['doc']
    _publish(roles, doc['id'])
    body = roles.player().get('/chronicle/page/' + doc['slug']).get_data(as_text=True)
    assert 'Chapter One' in body and 'The gate stood open.' in body


def test_document_title_defaults_to_its_own_heading_not_the_filename(roles):
    created = _upload(roles.gm(), name='Tavern.md',
                      data=b'# The Salted Gull\n\nA dockside tavern.')
    assert created.status_code == 201
    doc = created.get_json()['doc']
    assert doc['title'] == 'The Salted Gull'      # not 'Tavern'
    assert doc['slug'] == 'd-the-salted-gull'

    _publish(roles, doc['id'])
    body = roles.player().get('/chronicle/page/' + doc['slug']).get_data(as_text=True)
    # Rendered once by the template heading, not twice (template + body).
    assert body.count('The Salted Gull') == 1


def test_an_explicit_title_still_wins_over_the_documents_heading(roles):
    doc = _upload(roles.gm(), name='Tavern.md', title='GM name for it',
                  data=b'# The Salted Gull\n\nA dockside tavern.').get_json()['doc']
    assert doc['title'] == 'GM name for it'


def test_uploaded_html_is_sanitized_before_it_can_reach_a_player(roles):
    # _chronicle_fragment hands its file to |safe with no checks, so the ONLY
    # thing standing between pasted markup and the table is the sanitize call
    # on the write path.
    created = _upload(roles.gm(), name='Bad.md', title='Bad',
                      data=b'<script>alert(1)</script>\n\n<img src=x onerror=alert(2)>\n\n'
                           b'[click](javascript:alert(3))')
    assert created.status_code == 201
    doc = created.get_json()['doc']

    # Assert on the STORED FRAGMENT, not the rendered page -- base.html has its
    # own legitimate <script> chrome, which would mask the thing under test.
    # This file is what _chronicle_fragment hands to |safe, so it is the
    # boundary that actually matters.
    fragment_path = os.path.join(storage.chronicle_dir(CID), 'docs', 'html',
                                 doc['slug'] + '.html')
    with open(fragment_path, encoding='utf-8') as f:
        fragment = f.read()
    assert '<script' not in fragment.lower()
    assert 'onerror' not in fragment.lower()
    assert 'javascript:' not in fragment.lower()

    _publish(roles, doc['id'])
    assert roles.player().get('/chronicle/page/' + doc['slug']).status_code == 200


def test_publishing_requires_a_preview_first(roles):
    """The preview gate is this lane's entire safety story.

    There is no automated spoiler strip here -- the GM's own eyes are the
    firewall -- so 'you looked at it' is the one precondition worth enforcing
    before a document becomes visible to the table.
    """
    doc = _upload(roles.gm(), title='Unseen').get_json()['doc']
    assert doc['previewed_at'] is None

    blocked = roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'published': True})
    assert blocked.status_code == 409
    assert 'preview' in blocked.get_json()['error'].lower()
    assert roles.player().get('/chronicle/page/' + doc['slug']).status_code == 404

    assert roles.gm().get('/chronicle/preview/' + doc['id']).status_code == 200
    allowed = roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'published': True})
    assert allowed.status_code == 200
    assert allowed.get_json()['doc']['published'] is True


def test_unpublishing_is_never_gated(roles):
    doc = _upload(roles.gm(), title='Retractable').get_json()['doc']
    _publish(roles, doc['id'])
    off = roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'published': False})
    assert off.status_code == 200
    assert off.get_json()['doc']['published'] is False


def test_preview_records_itself_once(roles):
    doc = _upload(roles.gm(), title='Seen Once').get_json()['doc']
    roles.gm().get('/chronicle/preview/' + doc['id'])
    listed = roles.gm().get('/api/chronicle/docs').get_json()['docs']
    first = next(d for d in listed if d['id'] == doc['id'])
    assert first['previewed_at']

    # A second preview must not keep rewriting the index.
    roles.gm().get('/chronicle/preview/' + doc['id'])
    again = next(d for d in roles.gm().get('/api/chronicle/docs').get_json()['docs']
                 if d['id'] == doc['id'])
    assert again['previewed_at'] == first['previewed_at']


# ==========================================================================
# Renaming. The manage screen tells the GM to "rename this document to publish
# it" when its address collides with a vault page -- advice that was
# unfollowable, because PATCH changed the title and left the slug alone.
# ==========================================================================

def test_renaming_a_private_doc_moves_its_address(roles):
    doc = _upload(roles.gm(), title='First Name').get_json()['doc']
    assert doc['slug'] == 'd-first-name'

    renamed = roles.gm().patch('/api/chronicle/docs/' + doc['id'],
                               json={'title': 'Second Name'})
    assert renamed.status_code == 200
    body = renamed.get_json()['doc']
    assert body['title'] == 'Second Name'
    assert body['slug'] == 'd-second-name', 'the address must follow while private'


def test_renaming_moves_the_rendered_fragment_too(roles):
    """Otherwise the page 404s at its own new address."""
    doc = _upload(roles.gm(), title='Movable').get_json()['doc']
    roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'title': 'Moved'})

    html_dir = os.path.join(storage.chronicle_dir(CID), 'docs', 'html')
    assert not os.path.isfile(os.path.join(html_dir, 'd-movable.html'))
    assert os.path.isfile(os.path.join(html_dir, 'd-moved.html'))

    # And it is reachable at the new address once published.
    _publish(roles, doc['id'])
    assert roles.player().get('/chronicle/page/d-moved').status_code == 200


def test_renaming_clears_a_vault_collision(roles):
    """The exact scenario the blocked message describes, end to end."""
    chron = storage.chronicle_dir(CID)
    content = os.path.join(chron, 'content', 'vaulthash')
    os.makedirs(os.path.join(content, 'html'), exist_ok=True)
    with open(os.path.join(content, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'schema_version': app.CHRONICLE_SCHEMA_VERSION, 'pages': [
            {'slug': 'd-taken', 'source': 'content/a.md', 'title': 'Vault Page',
             'section': 'lore'}]}, f)
    with open(os.path.join(content, 'html', 'd-taken.html'), 'w', encoding='utf-8') as f:
        f.write('<p>vault</p>')
    app._chronicle_repoint(os.path.join(chron, 'current'), content)

    # An upload no longer LANDS on the collision: unique_slug reserves the
    # vault's slugs, so the document steps around it and is publishable at once.
    doc = _upload(roles.gm(), title='Taken').get_json()['doc']
    assert doc['slug'] != 'd-taken'
    listed = roles.gm().get('/api/chronicle/docs').get_json()['docs']
    assert next(d for d in listed if d['id'] == doc['id'])['shadowed_by_vault'] is False

    # A document uploaded BEFORE that fix can still be holding a colliding
    # slug, so the way out has to keep working. Force one into that state.
    docs_root = app._chronicle_docs_root()
    index = app._chronicle_lib.load_index(docs_root)
    entry = next(d for d in index['docs'] if d['id'] == doc['id'])
    stale_fragment = app._chronicle_doc_fragment_path(entry['slug'])
    entry['slug'] = 'd-taken'
    app._chronicle_lib.save_index(docs_root, index)
    if stale_fragment and os.path.isfile(stale_fragment):
        os.replace(stale_fragment, app._chronicle_doc_fragment_path('d-taken'))

    listed = roles.gm().get('/api/chronicle/docs').get_json()['docs']
    assert next(d for d in listed if d['id'] == doc['id'])['shadowed_by_vault'] is True

    renamed = roles.gm().patch('/api/chronicle/docs/' + doc['id'],
                               json={'title': 'Not Taken'}).get_json()['doc']
    assert renamed['slug'] == 'd-not-taken'
    assert renamed['shadowed_by_vault'] is False, 'the rename must clear the block'

    # And it can now actually be published, which is what the message promised.
    _publish(roles, doc['id'])
    assert roles.player().get('/chronicle/page/d-not-taken').status_code == 200


def test_retyping_the_same_title_frees_a_stuck_document(roles):
    """The manage screen says "rename this document to publish it", so retyping
    the same name is the first thing anyone tries. It used to regenerate the
    identical slug and change nothing, leaving the GM stuck with no signal."""
    chron = storage.chronicle_dir(CID)
    content = os.path.join(chron, 'content', 'vaulthash2')
    os.makedirs(os.path.join(content, 'html'), exist_ok=True)
    with open(os.path.join(content, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'schema_version': app.CHRONICLE_SCHEMA_VERSION, 'pages': [
            {'slug': 'd-story-so-far', 'source': 'content/s.md',
             'title': 'Story So Far', 'section': 'story'}]}, f)
    app._chronicle_repoint(os.path.join(chron, 'current'), content)

    doc = _upload(roles.gm(), title='Story So Far').get_json()['doc']
    docs_root = app._chronicle_docs_root()
    index = app._chronicle_lib.load_index(docs_root)
    entry = next(d for d in index['docs'] if d['id'] == doc['id'])
    entry['slug'] = 'd-story-so-far'          # the pre-fix state
    app._chronicle_lib.save_index(docs_root, index)

    same = roles.gm().patch('/api/chronicle/docs/' + doc['id'],
                            json={'title': 'Story So Far'}).get_json()['doc']
    assert same['slug'] != 'd-story-so-far'
    assert same['shadowed_by_vault'] is False

    # And it is publishable at its new address.
    _publish(roles, doc['id'])
    assert roles.player().get('/chronicle/page/' + same['slug']).status_code == 200


def test_a_published_doc_keeps_its_address_when_renamed(roles):
    """A shared /chronicle/page/<slug> URL must not silently 404."""
    doc = _upload(roles.gm(), title='Public Name').get_json()['doc']
    _publish(roles, doc['id'])
    original = doc['slug']

    renamed = roles.gm().patch('/api/chronicle/docs/' + doc['id'],
                               json={'title': 'Renamed While Live'}).get_json()['doc']
    assert renamed['title'] == 'Renamed While Live'
    assert renamed['slug'] == original, 'a live address must stay put'
    assert roles.player().get('/chronicle/page/' + original).status_code == 200


def test_renaming_does_not_collide_with_another_doc(roles):
    a = _upload(roles.gm(), title='Alpha').get_json()['doc']
    _upload(roles.gm(), title='Beta')
    renamed = roles.gm().patch('/api/chronicle/docs/' + a['id'],
                               json={'title': 'Beta'}).get_json()['doc']
    assert renamed['slug'] != 'd-beta', 'must not steal a sibling address'
    assert renamed['slug'].startswith('d-beta')


def test_rename_rejects_an_empty_title(roles):
    doc = _upload(roles.gm(), title='Keeps Its Name').get_json()['doc']
    blank = roles.gm().patch('/api/chronicle/docs/' + doc['id'], json={'title': '   '})
    assert blank.status_code == 400
    listed = roles.gm().get('/api/chronicle/docs').get_json()['docs']
    assert next(d for d in listed if d['id'] == doc['id'])['title'] == 'Keeps Its Name'


def test_the_manage_screen_actually_offers_a_rename_control():
    """The defect was an instruction with no control. Guard the pairing."""
    src = (app.Path(app.BASE_DIR) / 'templates' / 'chronicle_manage.html').read_text(
        encoding='utf-8')
    assert 'Rename this document to publish it' in src
    assert 'chron-doc-row__rename' in src, 'the message promises a rename with no control'


def test_player_cannot_reach_any_doc_route(roles):
    assert roles.player().get('/api/chronicle/docs').status_code == 403
    assert roles.player().post('/api/chronicle/docs').status_code == 403
    assert roles.player().patch('/api/chronicle/docs/whatever',
                               json={'published': True}).status_code == 403
    assert roles.player().delete('/api/chronicle/docs/whatever').status_code == 403
    assert roles.player().get('/chronicle/manage').status_code == 403
    assert roles.player().get('/chronicle/preview/whatever').status_code == 403


def test_upload_rejects_unknown_extension_and_oversize(roles, monkeypatch):
    bad = _upload(roles.gm(), name='sheet.xlsx', data=b'nope')
    assert bad.status_code == 400

    monkeypatch.setattr(app, '_CHRONICLE_DOC_MAX_BYTES', 16)
    big = _upload(roles.gm(), name='big.md', data=b'x' * 64)
    assert big.status_code == 413


def test_upload_rejects_empty_and_textless_documents(roles):
    assert _upload(roles.gm(), name='empty.md', data=b'').status_code == 400
    assert _upload(roles.gm(), name='blank.md', data=b'   \n\n  ').status_code == 400


def test_upload_rejects_an_unknown_section(roles):
    assert _upload(roles.gm(), section='atlas').status_code == 400


def test_spoiler_marker_warns_but_does_not_block(roles):
    # The vault lane hard-aborts on these. Here the GM previews each document
    # personally, so a marker is surfaced rather than refused.
    created = _upload(roles.gm(), name='Secrets.md', title='Secrets',
                      data=b'The vault holds a [!secret] worth keeping.')
    assert created.status_code == 201
    assert created.get_json()['doc']['warnings']


def test_delete_removes_the_entry_and_the_fragment(roles):
    doc = _upload(roles.gm(), title='Temporary').get_json()['doc']
    _publish(roles, doc['id'])
    assert roles.player().get('/chronicle/page/' + doc['slug']).status_code == 200

    assert roles.gm().delete('/api/chronicle/docs/' + doc['id']).status_code == 200
    assert roles.player().get('/chronicle/page/' + doc['slug']).status_code == 404
    docs_root = os.path.join(storage.chronicle_dir(CID), 'docs')
    assert not os.path.isfile(os.path.join(docs_root, 'html', doc['slug'] + '.html'))
    assert cdocs.load_index(docs_root)['docs'] == []


def test_document_cards_get_an_excerpt_and_a_document_glyph(roles):
    """An uploaded doc has no portrait and no epithet.

    Without these the lore grid is letter tiles and bare titles -- and since
    most titles start with "The", a wall of identical "T"s.
    """
    doc = _upload(roles.gm(), name='Ruins.md', title='The Drowned Ruins',
                  data=b'# The Drowned Ruins\n\nThe water had gone out, '
                       b'and the doors stood open.').get_json()['doc']
    assert 'water had gone out' in doc['excerpt']

    _publish(roles, doc['id'])
    lore = roles.player().get('/chronicle/lore').get_data(as_text=True)
    assert 'chron-docmark' in lore          # document glyph, not the monogram
    assert 'chron-monogram' not in lore
    assert 'water had gone out' in lore


def test_uploaded_cast_docs_are_not_shown_as_party_members(roles):
    doc = _upload(roles.gm(), section='cast', title='A Rival Captain').get_json()['doc']
    _publish(roles, doc['id'])
    home = roles.player().get('/chronicle').get_data(as_text=True)
    # It belongs on the Cast tab, not in the home page's "The party" block.
    assert 'A Rival Captain' in roles.player().get('/chronicle/cast').get_data(as_text=True)
    assert 'The party' not in home


def test_manage_screen_uses_shared_controls_not_inline_styles():
    """Assert on the TEMPLATE, not the rendered page.

    base.html carries its own inline styles (the dice panel and friends), so a
    rendered-page assertion would be testing the wrong file. The house rule --
    'consume classes from this file and never inline colors, gradients,
    shadows, or paddings' (system.css header) -- applies to what we author.
    """
    source = (app.Path(app.BASE_DIR) / 'templates' / 'chronicle_manage.html').read_text(
        encoding='utf-8')
    assert 'style="' not in source
    assert 'class="tb primary"' in source and 'class="tb danger' in source
    assert 'class="tinput"' in source and 'class="tselect"' in source
    # The console must not sit on the 65ch Alegreya reading surface. Match the
    # class being APPLIED, not merely named -- the template's own comment
    # explains why it is avoided.
    assert 'class="chron-prose' not in source


def test_manage_screen_renders_with_nothing_published(roles):
    # The chicken-and-egg guard: this screen is where the first publish
    # happens, so it must not be hidden behind "nothing is published yet".
    page = roles.gm().get('/chronicle/manage')
    assert page.status_code == 200
    assert b'Chronicle documents' in page.data
    assert b'No documents yet' in page.data


def test_preview_shows_an_unpublished_document_to_the_gm(roles):
    doc = _upload(roles.gm(), title='Draft').get_json()['doc']
    page = roles.gm().get('/chronicle/preview/' + doc['id'])
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'Preview' in body and 'not published' in body
    assert 'A cold wind' in body       # the real fragment, not a placeholder


def test_publishing_a_doc_opens_the_player_chronicle(roles):
    # With no vault publish at all, a published doc alone must lift the
    # chronicle out of its empty state rather than leaving players staring at
    # "The chronicle opens after your first session."
    empty = roles.player().get('/chronicle').get_data(as_text=True)
    assert 'The chronicle opens after your first session' in empty

    doc = _upload(roles.gm(), title='First Light').get_json()['doc']
    _publish(roles, doc['id'])

    opened = roles.player().get('/chronicle').get_data(as_text=True)
    assert 'The chronicle opens after your first session' not in opened


def test_doc_lane_does_not_disturb_the_vault_lane(roles):
    """A doc publish must not touch content/, current or previous."""
    chron = storage.chronicle_dir(CID)
    content = os.path.join(chron, 'content', 'vaulthash')
    os.makedirs(os.path.join(content, 'html'), exist_ok=True)
    with open(os.path.join(content, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump({'schema_version': app.CHRONICLE_SCHEMA_VERSION, 'session_number': 3,
                   'pages': [{'slug': 'vault-page', 'source': 'content/a.md',
                              'title': 'Vault Page', 'section': 'lore'}]}, f)
    with open(os.path.join(content, 'html', 'vault-page.html'), 'w', encoding='utf-8') as f:
        f.write('<p>from the vault</p>')
    app._chronicle_repoint(os.path.join(chron, 'current'), content)

    doc = _upload(roles.gm(), title='Uploaded').get_json()['doc']
    _publish(roles, doc['id'])

    # Vault page still resolves, and both lanes appear together.
    assert os.path.realpath(os.path.join(chron, 'current')) == os.path.realpath(content)
    lore = roles.gm().get('/chronicle/lore').get_data(as_text=True)
    assert 'Vault Page' in lore and 'Uploaded' in lore
    assert roles.gm().get('/chronicle/page/vault-page').status_code == 200
    assert roles.gm().get('/chronicle/page/' + doc['slug']).status_code == 200
