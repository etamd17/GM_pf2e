"""Chronicle security hardening (post-merge audit follow-up).

Covers the two NEEDS_ATTENTION findings from the pre-merge adversarial audit:
  1. HTML sanitizer must be an ALLOWLIST (the old regex denylist let unquoted
     event handlers / <svg onload> / entity-encoded javascript: through).
  2. /chronicle/assets/<path> must be RECIPIENT-SCOPED: a player may fetch an
     asset only if a page VISIBLE to them references it.
Plus two smaller items: leak-scan the exact per-page rendered source (not just
a fixed extension allowlist), and cap the decompressed archive size (zip bomb).
"""
import io
import json
import os
import sys
import base64
import zipfile
import textwrap
import subprocess

import app as A

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


# ─────────────────────────────────────────────────────────────────────────
# 1. Allowlist HTML sanitizer
# ─────────────────────────────────────────────────────────────────────────

def test_sanitizer_strips_quoted_and_unquoted_event_handlers():
    for vec in ('<img src=x onerror="alert(1)">',
                '<img src=x onerror=alert(1)>',
                "<img src=x onerror='alert(1)'>",
                '<div onclick=steal()>hi</div>'):
        out = A._chronicle_sanitize_html(vec).lower()
        assert 'onerror' not in out, vec
        assert 'onclick' not in out, vec
        assert 'alert(1)' not in out, vec
        assert 'steal()' not in out, vec


def test_sanitizer_drops_non_allowlisted_tags_svg_script_iframe():
    for vec in ('<svg onload=alert(1)></svg>',
                '<svg><animate onbegin=alert(1)></svg>',
                '<script>alert(1)</script>',
                '<iframe src="javascript:alert(1)"></iframe>',
                '<object data="x"></object>',
                '<math><mtext></mtext></math>'):
        out = A._chronicle_sanitize_html(vec).lower()
        assert '<svg' not in out and '<script' not in out and '<iframe' not in out, vec
        assert '<object' not in out and '<animate' not in out, vec
        assert 'onload' not in out and 'onbegin' not in out, vec
        assert 'alert(1)' not in out, vec


def test_sanitizer_blocks_dangerous_url_schemes_including_entity_encoded():
    for vec in ('<a href="javascript:alert(1)">x</a>',
                '<a href="java&#115;cript:alert(1)">x</a>',
                '<a href="JaVaScRiPt:alert(1)">x</a>',
                '<a href="\tjavascript:alert(1)">x</a>',
                '<img src="javascript:alert(1)">',
                '<a href="data:text/html,<script>alert(1)</script>">x</a>',
                '<a href="vbscript:msgbox(1)">x</a>'):
        out = A._chronicle_sanitize_html(vec).lower()
        assert 'javascript:' not in out, vec
        assert 'vbscript:' not in out, vec
        assert 'data:text/html' not in out, vec
        assert 'alert(1)' not in out, vec


def test_sanitizer_drops_backslash_protocol_relative_url():
    # Some browsers normalize a leading "\\" to "//" -> external navigation.
    # Treat backslashes like slashes so the protocol-relative guard catches it.
    out = A._chronicle_sanitize_html('<a href="\\\\evil.com">x</a>').lower()
    assert 'evil.com' not in out
    out2 = A._chronicle_sanitize_html('<a href="\\/evil.com">x</a>').lower()
    assert 'evil.com' not in out2


def test_sanitizer_preserves_legitimate_content():
    out = A._chronicle_sanitize_html(
        '<h1 id="s">Session</h1>'
        '<p>The party met <strong>Romi</strong> and <em>fled</em>.</p>'
        '<a href="/chronicle/page/romi" title="Romi">link</a>'
        '<a href="https://example.com">ext</a>'
        '<img src="assets/romi.png" alt="Romi" width="80">'
        '<div class="chron-callout-quote"><p>read aloud</p></div>'
        '<blockquote><p>plain</p></blockquote>'
        '<ul><li>one</li></ul>'
        '<table><thead><tr><th scope="col">H</th></tr></thead>'
        '<tbody><tr><td colspan="2">cell</td></tr></tbody></table>')
    assert '<h1 id="s">Session</h1>' in out
    assert '<strong>Romi</strong>' in out and '<em>fled</em>' in out
    assert 'href="/chronicle/page/romi"' in out and 'title="Romi"' in out
    assert 'href="https://example.com"' in out
    assert 'src="assets/romi.png"' in out and 'alt="Romi"' in out and 'width="80"' in out
    assert 'class="chron-callout-quote"' in out
    assert '<blockquote>' in out
    assert 'colspan="2"' in out and 'scope="col"' in out


def test_sanitizer_escapes_text_and_keeps_code_content():
    out = A._chronicle_sanitize_html('<p>use <code>&lt;div&gt;</code> &amp; go</p>')
    assert '<code>&lt;div&gt;</code>' in out
    assert '&amp;' in out
    # No raw executable tag survived from the escaped text.
    assert '<div>' not in out


def test_render_markdown_sanitizes_script_embedded_in_callout():
    md = "> [!quote] Romi\n> Hi <img src=x onerror=alert(9)> there\n"
    html = A._chronicle_render_markdown(md).lower()
    assert 'class="chron-callout-quote"' in html   # callout structure preserved
    assert 'onerror' not in html and 'alert(9)' not in html


# ─────────────────────────────────────────────────────────────────────────
# 2. Recipient-scoped asset serving
# ─────────────────────────────────────────────────────────────────────────

def _asset_scenario(monkeypatch, *, cdir, pages, fragments, is_gm, owned=(), account=True):
    """Wire the asset-index helpers to an in-memory manifest/fragment set."""
    A._CHRONICLE_ASSET_INDEX['cdir'] = None   # force a fresh index build
    monkeypatch.setattr(A, '_chronicle_content_dir', lambda: cdir)
    monkeypatch.setattr(A, '_chronicle_manifest', lambda: {'pages': pages})
    monkeypatch.setattr(A, '_chronicle_fragment', lambda slug: fragments.get(slug))
    monkeypatch.setattr(A, '_is_gm', lambda: is_gm)
    monkeypatch.setattr(A, '_account_mode', lambda: account)
    monkeypatch.setattr(A, '_chronicle_current_user', lambda: {'id': 'u1'} if account else None)
    monkeypatch.setattr(A, '_chronicle_owned_pc_slugs', lambda uid: set(owned))


def test_asset_index_maps_basenames_to_referencing_slugs(monkeypatch):
    pages = [
        {'slug': 'cast-aria', 'recipients': 'all', 'portrait': 'assets/aria.png'},
        {'slug': 'lore-map', 'recipients': 'all'},
    ]
    fragments = {'cast-aria': '<p>hi</p>',
                 'lore-map': '<p><img src="assets/world%20map.png" alt="m"></p>'}
    _asset_scenario(monkeypatch, cdir='/fake/c1', pages=pages, fragments=fragments, is_gm=False)
    idx = A._chronicle_asset_index()
    assert idx.get('aria.png') == {'cast-aria'}
    assert idx.get('world map.png') == {'lore-map'}   # url-decoded basename


def test_asset_visible_when_public_page_refs_it(monkeypatch):
    pages = [{'slug': 'lore', 'recipients': 'all'}]
    fragments = {'lore': '<img src="assets/town.png">'}
    _asset_scenario(monkeypatch, cdir='/fake/c2', pages=pages, fragments=fragments, is_gm=False)
    assert A._chronicle_asset_visible('town.png') is True


def test_asset_hidden_when_only_a_secret_page_refs_it(monkeypatch):
    pages = [
        {'slug': 'public', 'recipients': 'all'},
        {'slug': 'secret', 'recipients': ['aria']},
    ]
    fragments = {'public': '<p>nothing here</p>',
                 'secret': '<img src="assets/secret-map.png">'}
    # Non-owner player: secret page hidden -> its asset must 404.
    _asset_scenario(monkeypatch, cdir='/fake/c3', pages=pages, fragments=fragments,
                    is_gm=False, owned=['bob'])
    assert A._chronicle_asset_visible('secret-map.png') is False
    # Owner player: secret page visible -> asset visible.
    _asset_scenario(monkeypatch, cdir='/fake/c3b', pages=pages, fragments=fragments,
                    is_gm=False, owned=['aria'])
    assert A._chronicle_asset_visible('secret-map.png') is True
    # GM: sees everything.
    _asset_scenario(monkeypatch, cdir='/fake/c3c', pages=pages, fragments=fragments,
                    is_gm=True, owned=[])
    assert A._chronicle_asset_visible('secret-map.png') is True


def test_asset_subdir_collision_does_not_leak_secret(monkeypatch):
    # A public page references a FLAT foo.png; a secret page references a
    # same-basename sub/foo.png. Keying on basename alone would let the public
    # reference authorize serving the secret subdir file -- scope on the full
    # relative path so the secret path stays hidden from a non-owner.
    pages = [
        {'slug': 'public', 'recipients': 'all'},
        {'slug': 'secret', 'recipients': ['aria']},
    ]
    fragments = {'public': '<img src="assets/foo.png">',
                 'secret': '<img src="assets/sub/foo.png">'}
    _asset_scenario(monkeypatch, cdir='/fake/c5', pages=pages, fragments=fragments,
                    is_gm=False, owned=['bob'])
    assert A._chronicle_asset_visible('foo.png') is True          # public flat file ok
    assert A._chronicle_asset_visible('sub/foo.png') is False     # secret subdir file hidden


def test_orphan_asset_is_gm_only(monkeypatch):
    pages = [{'slug': 'lore', 'recipients': 'all'}]
    fragments = {'lore': '<p>no images</p>'}
    _asset_scenario(monkeypatch, cdir='/fake/c4', pages=pages, fragments=fragments, is_gm=False)
    assert A._chronicle_asset_visible('orphan.png') is False
    _asset_scenario(monkeypatch, cdir='/fake/c4b', pages=pages, fragments=fragments, is_gm=True)
    assert A._chronicle_asset_visible('orphan.png') is True


def test_asset_route_404s_for_nonowner_secret_asset():
    # End-to-end through the route in legacy-password mode (a real non-GM player).
    r = _run('''
import tempfile, os, json
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'sekret'
import app as A
h = 'abcd' * 16
content = os.path.join(A.CHRONICLE_DIR, 'content', h)
os.makedirs(os.path.join(content, 'html')); os.makedirs(os.path.join(content, 'assets'))
pages = [{'slug':'pub','section':'lore','title':'P','recipients':'all'},
         {'slug':'sec','section':'lore','title':'S','recipients':['aria']}]
json.dump({'schema_version':1,'session_number':1,'generated_at':'x','pages':pages,
           'mysteries':[],'calendar':{},'fieldguide':[],'spine':[]},
          open(os.path.join(content,'manifest.json'),'w'))
open(os.path.join(content,'html','pub.html'),'w').write('<p>public</p>')
open(os.path.join(content,'html','sec.html'),'w').write('<img src="assets/secret.png">')
open(os.path.join(content,'assets','secret.png'),'wb').write(b'PNG-SECRET')
link = os.path.join(A.CHRONICLE_DIR,'current'); tmp=link+'.tmp'
os.symlink(content, tmp); os.replace(tmp, link)
c = A.app.test_client()
with c.session_transaction() as s: s['player_name'] = 'aria'   # joined, non-GM, legacy-open secret => GM-only
r = c.get('/chronicle/assets/secret.png')
assert r.status_code == 404, (r.status_code, r.data)
print('OK')
''')
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ─────────────────────────────────────────────────────────────────────────
# 3. Leak-scan the exact rendered page source (extension-independent)
# ─────────────────────────────────────────────────────────────────────────

def test_publish_rejects_marker_in_unscanned_extension_source():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            'schema_version': 1, 'session_number': 1,
            'pages': [{'slug': 'x', 'source': 'content/x.mdx', 'recipients': 'all'}]}))
        # .mdx is NOT in the fixed scan-extension list, so the tree walk skips it;
        # the per-source scan must still catch the marker before it renders.
        z.writestr('content/x.mdx', 'Intro\n\n> [!danger] the duke is the vampire\n')
    zb = base64.b64encode(buf.getvalue()).decode()
    r = _run('''
import tempfile, base64, io, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()
z = base64.b64decode("%s")
r = c.post('/api/chronicle/publish', data={'archive': (io.BytesIO(z), 'c.zip')},
           content_type='multipart/form-data')
assert r.status_code == 400, (r.status_code, r.data)
assert A._chronicle_content_dir() is None   # nothing published
print('OK')
''' % zb)
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


# ─────────────────────────────────────────────────────────────────────────
# 4. Decompressed-size / entry-count cap (zip bomb)
# ─────────────────────────────────────────────────────────────────────────

def test_publish_rejects_oversized_decompressed_archive():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('manifest.json', json.dumps({
            'schema_version': 1, 'pages': [{'slug': 'x', 'source': 'content/x.md', 'recipients': 'all'}]}))
        z.writestr('content/x.md', 'ok\n')
        z.writestr('content/big.bin', b'\0' * 200000)   # compresses tiny, 200KB inflated
    zb = base64.b64encode(buf.getvalue()).decode()
    r = _run('''
import tempfile, base64, io, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
A._CHRONICLE_MAX_UNCOMPRESSED = 50000   # 50KB cap for the test
c = A.app.test_client()
z = base64.b64decode("%s")
r = c.post('/api/chronicle/publish', data={'archive': (io.BytesIO(z), 'c.zip')},
           content_type='multipart/form-data')
assert r.status_code == 400, (r.status_code, r.data)
assert A._chronicle_content_dir() is None
print('OK')
''' % zb)
    assert 'OK' in r.stdout, (r.stdout, r.stderr)


def test_publish_rejects_too_many_entries():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            'schema_version': 1, 'pages': [{'slug': 'x', 'source': 'content/x.md', 'recipients': 'all'}]}))
        z.writestr('content/x.md', 'ok\n')
        for i in range(60):
            z.writestr('assets/f%d.bin' % i, b'x')
    zb = base64.b64encode(buf.getvalue()).decode()
    r = _run('''
import tempfile, base64, io, os
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
A._CHRONICLE_MAX_ENTRIES = 10
c = A.app.test_client()
z = base64.b64decode("%s")
r = c.post('/api/chronicle/publish', data={'archive': (io.BytesIO(z), 'c.zip')},
           content_type='multipart/form-data')
assert r.status_code == 400, (r.status_code, r.data)
print('OK')
''' % zb)
    assert 'OK' in r.stdout, (r.stdout, r.stderr)
