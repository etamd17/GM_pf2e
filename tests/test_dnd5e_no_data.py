"""A server without the 5e rules data (they are not in git): the 5e pages say
so instead of crashing, and the site admin can install a bundle. Synthetic data,
subprocess app (app.py binds DATA_DIR at import)."""
import os
import subprocess
import sys
import textwrap

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_missing_data_guard_and_admin_install():
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent('''
        import io, tempfile
        root = tempfile.mkdtemp()
        os.environ['DATA_DIR'] = os.path.join(root, 'data'); os.environ['GM_PASSWORD'] = ''
        os.environ['DND5E_DATA_DIR'] = os.path.join(root, 'd5')          # empty: nothing installed
        sys.path.insert(0, 'tests')
        from test_dnd5e_datapack import _fake_data
        from systems.dnd5e import datapack, content
        assert not content.available()
        import app as A
        from core import storage, campaigns

        admin = A.app.test_client()
        assert admin.post('/setup', data={'username': 'owner', 'password': 'secret1', 'display_name': 'O'}).status_code == 302
        assert admin.post('/campaigns/new', data={'name': 'T', 'system': 'dnd5e'}).status_code == 302
        cid = storage.list_campaign_ids()[0]
        admin.post('/campaign/' + cid + '/activate')

        r = admin.get('/dnd5e/characters')
        assert r.status_code == 503 and b"isn't installed" in r.data and b'/dnd5e/admin/data' in r.data
        r = admin.get('/api/dnd5e/content/spell?q=fire')
        assert r.status_code == 503 and 'not installed' in r.get_json()['error']

        # only the site admin reaches the installer
        p = A.app.test_client()
        assert p.post('/register', data={'username': 'pl', 'password': 'pw12345', 'display_name': 'P'}).status_code == 302
        assert p.get('/dnd5e/admin/data').status_code == 403
        assert p.post('/api/dnd5e/admin/data', data={}, content_type='multipart/form-data').status_code == 403
        page = admin.get('/dnd5e/admin/data')
        assert page.status_code == 200 and b'Not installed' in page.data

        # build a (synthetic) bundle and install it through the route
        src = os.path.join(root, 'src'); _fake_data(src, images=2)
        parts = datapack.build(src, os.path.join(root, 'out'))
        for part in parts:
            with open(part, 'rb') as f:
                r = admin.post('/api/dnd5e/admin/data', data={'file': (io.BytesIO(f.read()), os.path.basename(part))},
                               content_type='multipart/form-data')
            assert r.status_code == 200, r.data
        assert r.get_json()['status']['installed'] and content.available()
        bad = admin.post('/api/dnd5e/admin/data', data={'file': (io.BytesIO(b'nope' * 100), 'x.tar')},
                         content_type='multipart/form-data')
        assert bad.status_code == 422

        # art is served from the installed data, to signed-in users only
        art = '/dnd5e/art/art/' + '0' * 20 + '.webp'
        got = admin.get(art)
        assert got.status_code == 200 and got.mimetype == 'image/webp'
        got.close()
        assert A.app.test_client().get(art).status_code in (302, 401)
        assert admin.get('/dnd5e/art/art/..%2f..%2fdnd5e.db').status_code == 404
        assert admin.get('/dnd5e/art/other/' + '0' * 20 + '.webp').status_code == 404
        print('NO_DATA_OK')
    ''')
    r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO, encoding='utf-8')
    assert 'NO_DATA_OK' in r.stdout, 'stdout:\n%s\nstderr:\n%s' % (r.stdout[-3000:], r.stderr[-5000:])
