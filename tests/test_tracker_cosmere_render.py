"""Regression: the Cosmere tracker must not crash on render.

The PF2e XP/difficulty bar (#xp-bar-wrap) is only emitted when active_system
!= 'cosmere'. renderMeta() reads that element and, in its else branch, called
xpWrap.classList.add('hidden') unconditionally -- so on a COSMERE tracker the
element is absent, getElementById returns null, and EVERY render threw
("can't access property classList, xpWrap is null"). Because apiPost swallows
render errors, HP/condition changes applied on the server but never repainted:
the Cosmere tracker looked frozen ("can't adjust HP at all").

This pins the two coupled facts so neither can drift back into a null-deref:
  1. the Cosmere tracker really does omit #xp-bar-wrap, and
  2. renderMeta guards the null before touching .classList.

Subprocess + throwaway DATA_DIR, mirroring tests/test_cosmere_campaign_binding.py.
"""
import os
import re
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_cosmere_tracker_renders_without_xp_bar_nulldef():
    r = _run('''
        import tempfile, os, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302

        page = c.get('/tracker')
        assert page.status_code == 200
        html = page.data.decode()
        # Cosmere mode omits the PF2e XP/difficulty bar entirely...
        assert 'id="xp-bar-wrap"' not in html, 'cosmere tracker unexpectedly emits #xp-bar-wrap'
        # ...so renderMeta MUST guard the (now-null) element before .classList.
        assert "else if (xpWrap)" in html, 'renderMeta lost its null guard -> cosmere render will throw'
        # and must NOT contain the old unguarded else that dereferenced a null xpWrap
        assert not re.search(r"\\}\\s*else\\s*\\{\\s*\\n\\s*xpWrap\\.classList", html), 'unguarded xpWrap.classList in else branch'
        print('COSMERE_TRACKER_RENDER_OK')
    ''')
    assert 'COSMERE_TRACKER_RENDER_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_pf2e_tracker_still_emits_xp_bar():
    """The PF2e tracker keeps its XP bar (the guard change is null-safe, not a removal)."""
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302
        html = c.get('/tracker').data.decode()
        assert 'id="xp-bar-wrap"' in html, 'pf2e tracker should still render the XP/difficulty bar'
        print('PF2E_TRACKER_XPBAR_OK')
    ''')
    assert 'PF2E_TRACKER_XPBAR_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
