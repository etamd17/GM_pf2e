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
    assert 'class="chron-callout-quote"' in html
    assert 'class="chron-doc-frame"' in html
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


def _zip_dir_bytes_with_session(src_dir, session_number):
    """Same as _zip_dir_bytes, but with manifest.json's session_number bumped
    so the zip's bytes -- and therefore its content hash -- differ from a
    plain republish of src_dir. Publish hashes are content-derived (see
    chronicle_publish), so re-publishing byte-identical content dedups to the
    SAME hash and _chronicle_swap never rotates `previous` (no-op republish).
    Tests that need a real `previous` to exist must publish two DISTINCT
    payloads, which this produces."""
    man = json.load(open(os.path.join(src_dir, 'manifest.json')))
    man['session_number'] = session_number
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for base, _d, files in os.walk(src_dir):
            for fn in files:
                if fn == '.gitkeep':
                    continue
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, src_dir)
                if rel == 'manifest.json':
                    z.writestr(rel, json.dumps(man))
                else:
                    z.write(full, rel)
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
assert '<div class="chron-callout-quote">' in open(os.path.join(content, 'html', 'home.html')).read()
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


def test_publish_missing_manifest_is_400_and_current_unchanged():
    # A zip with no manifest.json at all: _storage.load_json returns None for
    # the missing file, and _chronicle_validate_manifest(None) must reject it
    # cleanly (400), not blow up with a 500 (e.g. a KeyError/AttributeError
    # from code that assumed a dict).
    nbuf = io.BytesIO()
    with zipfile.ZipFile(nbuf, 'w') as z:
        z.writestr('content/home.md', '# Just a page, no manifest.json\n')
    nbuf.seek(0)
    nb = nbuf.read()

    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

no_manifest = base64.b64decode({no_manifest!r})

r = c.post('/api/chronicle/publish',
           data={{'archive': (io.BytesIO(no_manifest), 'no_manifest.zip')}},
           content_type='multipart/form-data')
assert r.status_code == 400, (r.status_code, r.data)
assert A._chronicle_manifest() is None   # nothing was ever published
print('MISSING_MANIFEST_OK')
'''.format(no_manifest=base64.b64encode(nb).decode())
    r = _run(body)
    assert 'MISSING_MANIFEST_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_status_reports_last_publish():
    zb = _zip_dir_bytes(_FIX)
    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

# before any publish
j = c.get('/api/chronicle/status').get_json()
assert j['published'] is False, j

c.post('/api/chronicle/publish',
       data={{'archive': (io.BytesIO(base64.b64decode({good!r})), 'c.zip')}},
       content_type='multipart/form-data')
j = c.get('/api/chronicle/status').get_json()
assert j['published'] is True and j['session_number'] == 3 and j['pages'] == 2, j
assert j['can_rollback'] is False, j   # first publish -> no previous yet
print('STATUS_OK')
'''.format(good=base64.b64encode(zb).decode())
    r = _run(body)
    assert 'STATUS_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_status_can_rollback_true_after_second_distinct_publish():
    # Task 11 review: can_rollback must reflect a `previous` that
    # _chronicle_rollback() would actually act on, not just os.path.lexists.
    # Publish two DISTINCT payloads (same-content republish dedups to one
    # hash and creates no `previous` -- see _zip_dir_bytes_with_session) so a
    # real rollback target exists, then assert status reports it.
    zb1 = _zip_dir_bytes(_FIX)
    zb2 = _zip_dir_bytes_with_session(_FIX, 4)
    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

r1 = c.post('/api/chronicle/publish',
            data={{'archive': (io.BytesIO(base64.b64decode({first!r})), 'c1.zip')}},
            content_type='multipart/form-data')
assert r1.status_code == 200, r1.data
r2 = c.post('/api/chronicle/publish',
            data={{'archive': (io.BytesIO(base64.b64decode({second!r})), 'c2.zip')}},
            content_type='multipart/form-data')
assert r2.status_code == 200, r2.data
assert r1.get_json()['hash'] != r2.get_json()['hash']   # distinct content -> distinct hash

j = c.get('/api/chronicle/status').get_json()
assert j['published'] is True and j['session_number'] == 4, j
assert j['can_rollback'] is True, j
print('ROLLBACK_STATUS_OK')
'''.format(first=base64.b64encode(zb1).decode(), second=base64.b64encode(zb2).decode())
    r = _run(body)
    assert 'ROLLBACK_STATUS_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_rollback_restores_previous_publish():
    # Publish two DISTINCT payloads (session 2, then session 3) so a real
    # `previous` exists (see _zip_dir_bytes_with_session), then roll back and
    # confirm `current` now resolves to session 2's publish.
    zb2 = _zip_dir_bytes_with_session(_FIX, 2)
    zb3 = _zip_dir_bytes_with_session(_FIX, 3)
    import base64
    body = '''
import tempfile, base64, io, os
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client()

def pub(b):
    return c.post('/api/chronicle/publish',
                  data={{'archive': (io.BytesIO(b), 'c.zip')}},
                  content_type='multipart/form-data')

# rollback with nothing to roll back to -> 400
assert c.post('/api/chronicle/rollback').status_code == 400

pub(base64.b64decode({s2!r}))   # session 2 (becomes previous)
pub(base64.b64decode({s3!r}))   # session 3 (current)
assert A._chronicle_manifest()['session_number'] == 3

r = c.post('/api/chronicle/rollback')
assert r.status_code == 200 and r.get_json()['ok'], r.data
assert A._chronicle_manifest()['session_number'] == 2   # current now points at prev
print('ROLLBACK_OK')
'''.format(s2=base64.b64encode(zb2).decode(),
           s3=base64.b64encode(zb3).decode())
    r = _run(body)
    assert 'ROLLBACK_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
