"""Chronicle PR1 — publish endpoint, leak/manifest validation, markdown render,
status + rollback. Subprocess isolation (fresh DATA_DIR, legacy-open GM mode),
mirroring tests/test_campaign_backup.py."""
from __future__ import annotations
import io, os, sys, json, zipfile, subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    return subprocess.run(
        [sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
        capture_output=True, text=True, cwd=_REPO)


def test_chronicle_prefix_is_gm_gated():
    # The prefix must be present in the centralized GM gate so every
    # /api/chronicle/* route is GM-only with no per-route decorator.
    import app as A  # imported in-process here is fine: pure constant check
    assert '/api/chronicle' in A.GM_API_PREFIXES


def test_leak_scan_flags_forbidden_markers(tmp_path):
    import app as A
    (tmp_path / 'content').mkdir()
    (tmp_path / 'content' / 'clean.md').write_text('# Recap\nThe party arrived.\n')
    (tmp_path / 'content' / 'leaky.md').write_text('> [!danger] the lich is the mayor\n')
    (tmp_path / 'manifest.json').write_text('{"note": "has a [!secret] in json too"}')
    offenders = A._chronicle_leak_scan(str(tmp_path))
    assert any('leaky.md' in o and '[!danger]' in o for o in offenders), offenders
    assert any('manifest.json' in o and '[!secret]' in o for o in offenders), offenders
    assert not any('clean.md' in o for o in offenders), offenders
    # clean tree -> empty list
    (tmp_path / 'content' / 'leaky.md').unlink()
    (tmp_path / 'manifest.json').write_text('{"note": "ok"}')
    assert A._chronicle_leak_scan(str(tmp_path)) == []


def test_render_markdown_callouts_and_sanitize():
    import app as A
    md = (
        "# Session 3\n\n"
        "The party met **Romi**.\n\n"
        "> [!quote] Romi\n> We never had this conversation.\n\n"
        "> [!example] Handout\n> A torn ledger page.\n\n"
        "> [!note] table cue\n> keep this plain\n\n"
        "<script>alert(1)</script>\n\n"
        "[click](javascript:alert(2))\n"
    )
    html = A._chronicle_render_markdown(md)
    assert '<h1' in html and '<strong>Romi</strong>' in html
    assert 'class="callout-quote"' in html
    assert 'class="doc-frame"' in html
    assert '<blockquote>' in html          # unknown callout -> plain blockquote
    assert '[!quote]' not in html and '[!note]' not in html   # markers consumed
    assert '<script' not in html.lower()   # sanitized
    assert 'javascript:' not in html.lower()


def test_safe_slug():
    import app as A
    assert A._chronicle_safe_slug("Romi's Ledger") == 'romi-s-ledger'
    assert A._chronicle_safe_slug('../etc/passwd') == 'etc-passwd'
    assert A._chronicle_safe_slug('') == 'page'


_FIX = os.path.join(_REPO, 'tests', 'fixtures', 'chronicle_sample')


def _zip_dir_bytes(src_dir):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _d, files in os.walk(src_dir):
            for fn in files:
                if fn == '.gitkeep':
                    continue
                full = os.path.join(base, fn)
                z.write(full, os.path.relpath(full, src_dir))
    buf.seek(0)
    return buf.read()


def test_sample_fixture_is_valid_vault():
    man = json.load(open(os.path.join(_FIX, 'manifest.json')))
    assert man['schema_version'] == 1
    assert isinstance(man['pages'], list) and man['pages']
    for p in man['pages']:
        assert p['slug'] and p['source']
        assert os.path.isfile(os.path.join(_FIX, p['source'])), p['source']
    # zips without error and manifest sits at the archive root
    z = zipfile.ZipFile(io.BytesIO(_zip_dir_bytes(_FIX)))
    assert 'manifest.json' in z.namelist()


def test_validate_manifest_rejects_unsafe_slug():
    # Reconciliation Contract §6: every page slug must match
    # ^[a-z0-9][a-z0-9-]{0,80}$ so the html/<slug>.html fragment filename the
    # publish route writes is the exact key the reading routes look up by.
    import app as A
    ok, err = A._chronicle_validate_manifest({
        "schema_version": 1,
        "pages": [{"slug": "Bad Slug", "source": "content/x.md"}],
    })
    assert not ok
    assert 'slug' in err.lower()
    # A safe slug with a valid source passes the manifest-shape check.
    ok2, err2 = A._chronicle_validate_manifest({
        "schema_version": 1,
        "pages": [{"slug": "good-slug", "source": "content/x.md"}],
    })
    assert ok2, err2


def test_publish_happy_path_and_leak_and_zipslip():
    zb = _zip_dir_bytes(_FIX)
    # leaky variant: same manifest, one page carrying a forbidden marker
    lbuf = io.BytesIO()
    with zipfile.ZipFile(lbuf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            "schema_version": 1, "session_number": 3,
            "pages": [{"slug": "leak", "source": "content/leak.md", "recipients": "all"}]}))
        z.writestr('content/leak.md', '> [!danger] the mayor is the lich\n')
    lbuf.seek(0)
    lb = lbuf.read()
    # zip-slip variant
    sbuf = io.BytesIO()
    with zipfile.ZipFile(sbuf, 'w') as z:
        z.writestr('manifest.json', json.dumps({
            "schema_version": 1, "pages": [{"slug": "x", "source": "content/x.md", "recipients": "all"}]}))
        z.writestr('../evil.md', 'pwned')
    sbuf.seek(0)
    sb = sbuf.read()

    import base64
    body = '''
import tempfile, base64, io, os, json
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

good = base64.b64decode({good!r})
leak = base64.b64decode({leak!r})
slip = base64.b64decode({slip!r})

# happy path -> 200, fragments exist, current resolves
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(good), 'chronicle.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 200, (r.status_code, r.data)
j = r.get_json(); assert j['ok'] and j['pages'] == 2, j
content = A._chronicle_content_dir()
assert content and os.path.isfile(os.path.join(content, 'html', 'home.html'))
assert os.path.isfile(os.path.join(content, 'html', 'romi.html'))
assert '<div class="callout-quote">' in open(os.path.join(content, 'html', 'home.html')).read()
assert A._chronicle_manifest()['session_number'] == 3

# leak -> 400, and `current` is UNCHANGED (still the good publish)
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(leak), 'leak.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 400 and r.get_json().get('leaks'), r.data
assert A._chronicle_manifest()['session_number'] == 3   # not clobbered

# zip-slip -> 400 and no escape file written
r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(slip), 'slip.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 400, r.data
assert not os.path.exists(os.path.join(TMP, 'evil.md'))
assert not os.path.exists(os.path.join(TMP, 'chronicle', 'evil.md'))
print('PUBLISH_OK')
'''.format(good=base64.b64encode(zb).decode(),
           leak=base64.b64encode(lb).decode(),
           slip=base64.b64encode(sb).decode())
    r = _run(body)
    assert 'PUBLISH_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
