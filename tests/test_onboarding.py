"""Onboarding & invites: scannable QR + copy on the manage page, and the join
page tells the player which campaign they're joining. Tuned for an in-person
table where players join on phones.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_invite_page_has_qr_and_copy_and_join_shows_campaign():
    r = _run('''
        import tempfile, os, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]

        # mint a generic player invite link
        assert c.post('/campaign/' + cid + '/invite', data={'role': 'player'}).status_code in (302, 200)
        page = c.get('/campaign/' + cid + '/invites').data
        assert b'<svg' in page, 'QR code should render on the invites page'
        assert b'copy-btn' in page, 'a copy-to-clipboard button should be present'
        codes = re.findall(rb'join\\?code=([A-Z2-9]{4}-[A-Z2-9]{4})', page)
        assert codes, 'a join link/code should be shown'
        code = codes[0].decode()

        # a brand-new player visiting the link sees WHICH campaign they're joining
        jp = A.app.test_client().get('/join?code=' + code).data
        assert b'Roshar' in jp and b'invited to join' in jp

        print('ONBOARDING_OK')
    ''')
    assert 'ONBOARDING_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_qr_svg_helper_degrades_without_segno(monkeypatch):
    """qr_svg must never break the page if segno is missing -- it returns ''."""
    import builtins
    import app
    real_import = builtins.__import__

    def _no_segno(name, *a, **k):
        if name == 'segno':
            raise ImportError('segno missing')
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, '__import__', _no_segno)
    assert app.qr_svg('https://example.com/join?code=ABCD-EFGH') == ''
