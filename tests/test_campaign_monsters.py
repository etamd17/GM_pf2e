"""GM-made PF2e monsters belong to their campaign (user decision 2026-10-21).

Imported and authored monsters are written to campaigns/<cid>/monster_data,
keyed 'campaign/<file>' in MONSTER_LIBRARY, and loaded only while their
campaign is live. Another campaign, including another GM's, never sees them.
Runs the real app in a subprocess: the switch rebinds process globals."""
import os
import subprocess
import sys
import textwrap

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO,
                          timeout=600)


def test_custom_monsters_stay_in_their_campaign():
    r = _run('''
        import tempfile, os, glob, json
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'x'
        import app as A
        c = A.app.test_client()
        c.post('/setup', data={'username': 'gm', 'password': 'pw12345', 'display_name': 'GM'})
        c.post('/campaigns/new', data={'name': 'One', 'system': 'pf2e'})
        c.post('/campaigns/new', data={'name': 'Two', 'system': 'pf2e'})
        DD = os.environ['DATA_DIR']
        ids = {json.load(open(f, encoding='utf-8'))['name']: json.load(open(f, encoding='utf-8'))['id']
               for f in glob.glob(DD + '/campaigns/*/campaign.json')}
        one, two = ids['One'], ids['Two']

        c.post('/campaign/%s/activate' % one)
        assert c.post('/api/create_monster', json={'name': 'Bog Hag', 'level': 3}).status_code == 200
        r = c.post('/api/import_monster', json={'name': 'Mire Imp', 'type': 'npc',
                                                'system': {'details': {'level': {'value': 1}}}})
        assert r.status_code == 200, r.get_data(as_text=True)
        own = os.path.join(DD, 'campaigns', one, 'monster_data')
        assert sorted(os.listdir(own)) == ['Bog_Hag.json', 'Mire_Imp.json']
        assert not os.path.exists(os.path.join(DD, 'monster_data', 'Bog_Hag.json'))   # not shared
        assert {'campaign/Bog_Hag.json', 'campaign/Mire_Imp.json'} <= set(A.MONSTER_LIBRARY)
        shared = len(A.MONSTER_LIBRARY) - 2

        # An encounter that names one survives the switch away and back (autosave restore).
        r = c.post('/api/add_combatant', json={'type': 'monster', 'path': 'campaign/Bog_Hag.json'})
        assert r.status_code == 200, r.get_data(as_text=True)
        A._flush_pending_persistence()

        c.post('/campaign/%s/activate' % two)
        assert not any(k.startswith('campaign/') for k in A.MONSTER_LIBRARY)
        assert len(A.MONSTER_LIBRARY) == shared
        found = c.get('/api/monster_search?q=bog')
        assert found.status_code == 200
        assert 'Bog Hag' not in [m['name'] for m in found.get_json()['results']]

        c.post('/campaign/%s/activate' % one)
        assert {'campaign/Bog_Hag.json', 'campaign/Mire_Imp.json'} <= set(A.MONSTER_LIBRARY)
        assert any(getattr(x, 'name', '') == 'Bog Hag' for x in A.ACTIVE_ENCOUNTER), 'encounter restored'
        print('CAMPAIGN_MONSTERS_OK')
    ''')
    assert 'CAMPAIGN_MONSTERS_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout[-3000:], r.stderr[-3000:])


def test_without_campaigns_a_custom_monster_goes_to_the_shared_folder():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        A.load_campaign(None)
        key, _m = A._save_custom_monster({'name': 'Flat Rat', 'type': 'npc',
                                          'system': {'details': {'level': {'value': 0}}}}, 'Flat Rat')
        assert key == 'Flat_Rat.json' and os.path.exists(os.path.join(A.MONSTER_DIR, 'Flat_Rat.json'))
        assert key in A.MONSTER_LIBRARY and key not in A.CAMPAIGN_MONSTER_KEYS
        print('FLAT_OK')
    ''')
    assert 'FLAT_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
