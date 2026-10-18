"""Cosmere loot ledger -- the Rosharan sibling of the PF2e /gm/loot ledger.

Spheres & spoils awarded to the party, persisted in the campaign-scoped ledger,
with exact gem-aware diamond-mark totals. GM-only and Cosmere-mode-gated.
Runs in a subprocess with a throwaway DATA_DIR (mirrors the other Cosmere e2e
tests) so it's CI-safe.
"""
import os
import sys
import textwrap
import subprocess
from pathlib import Path

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_cosmere_loot_ledger_end_to_end():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302

        # the page renders with the Cosmere nav (brand flipped), no PF2e bleed
        page = c.get('/cosmere/loot')
        assert page.status_code == 200 and b'Spheres' in page.data and b'COSMERE' in page.data

        # Diamond award: 1 broam + 2 marks + 3 chips = 33 diamond chips = 6.6 mk.
        add = c.post('/api/cosmere/loot/add', json={
            'recipient': 'Kaladin', 'gem': 'diamond',
            'items': [{'name': 'Shardblade', 'qty': 1}],
            'spheres': {'broam': 1, 'mark': 2, 'chip': 3}, 'note': 'from the gemheart'})
        assert add.status_code == 200 and add.get_json()['success']

        data = c.get('/api/cosmere/loot').get_json()
        assert len(data['entries']) == 1
        assert data['total_chips'] == 33, data['total_chips']
        assert data['total_marks'] == '6.6'
        e = data['entries'][0]
        assert e['recipient'] == 'Kaladin' and e['spheres'] == {'broam': 1, 'mark': 2, 'chip': 3}
        assert e['items'][0]['name'] == 'Shardblade' and e['gem'] == 'diamond'
        assert e['value_chips'] == 33 and e['value_marks'] == '6.6'
        assert data['gems'] == [
            'diamond', 'garnet', 'heliodor', 'topaz', 'ruby',
            'smokestone', 'zircon', 'amethyst', 'sapphire', 'emerald']

        # the ledger persists under THIS campaign (campaign-scoped storage)
        led = storage.load_json(storage.loot_ledger_file(cid))
        assert led and len(led['entries']) == 1

        # delete it
        assert c.post('/api/cosmere/loot/delete', json={'id': e['id']}).status_code == 200
        assert c.get('/api/cosmere/loot').get_json()['entries'] == []

        # a recipient is required
        assert c.post('/api/cosmere/loot/add', json={'spheres': {'chip': 5}}).status_code == 400

        print('COSMERE_LOOT_OK')
    ''')
    assert 'COSMERE_LOOT_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_cosmere_loot_redirects_out_in_pf2e_mode():
    """The Cosmere loot ledger is Cosmere-only -- it redirects to the active
    system's GM home (and the API refuses) when a PF2e campaign is active."""
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
        assert c.get('/cosmere/loot').status_code == 302           # redirects out
        assert c.post('/api/cosmere/loot/add', json={'recipient': 'X', 'spheres': {'chip': 1}}).status_code == 400
        assert c.post('/api/cosmere/loot/delete', json={'id': 'old-entry'}).status_code == 400
        print('COSMERE_LOOT_GATED_OK')
    ''')
    assert 'COSMERE_LOOT_GATED_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_cosmere_loot_client_keeps_form_state_and_reports_api_errors():
    source = Path(_REPO, 'templates', 'cosmere_loot.html').read_text(encoding='utf-8')
    helper = source[source.index('async function postJSON'):source.index('function load()')]
    assert '!response.ok' in helper
    assert '!data.success' in helper
    assert 'data.error || data.message' in helper

    delete = source[source.index('async function del(id)'):source.index("$('cl-additem').onclick")]
    assert 'await postJSON' in delete
    assert 'showError(error)' in delete

    submit = source[source.index("$('cl-form').onsubmit"):source.index('load();\n})()')]
    assert 'await postJSON' in submit
    assert 'showError(error)' in submit
    assert submit.index("await postJSON") < submit.index("$('cl-items').innerHTML = ''")
    assert submit.index("$('cl-items').innerHTML = ''") < submit.index('} catch (error)')
