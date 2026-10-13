"""D&D 5e through the real Flask app (account mode): routing, authorization,
and the sheet <-> tracker sync over HTTP.

Subprocess + throwaway DATA_DIR, like tests/test_gm_system_repair.py, because
app.py binds DATA_DIR and campaign globals at import."""
import os
import subprocess
import sys
import textwrap

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from systems.dnd5e import content as _content  # noqa: E402
pytestmark = pytest.mark.skipif(not _content.available(), reason='dnd5e.db not built')


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO,
                          encoding='utf-8')


def test_dnd5e_campaign_end_to_end():
    r = _run('''
        import tempfile, json
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns

        admin = A.app.test_client()
        assert admin.post('/setup', data={'username': 'owner', 'password': 'secret1', 'display_name': 'Owner'}).status_code == 302
        gm = A.app.test_client()
        assert gm.post('/register', data={'username': 'dm', 'password': 'pw12345', 'display_name': 'DM'}).status_code == 302
        assert gm.post('/campaigns/new', data={'name': 'Phandelver', 'system': 'dnd5e'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Phandelver'][0]
        assert campaigns.get_campaign(cid)['system'] == 'dnd5e'
        ar = gm.post('/campaign/' + cid + '/activate')
        assert ar.status_code == 302 and ar.headers['Location'].endswith('/dnd5e/gm'), ar.headers.get('Location')

        # every GM page renders
        for url in ('/dnd5e/gm', '/dnd5e/tracker', '/dnd5e/encounters', '/dnd5e/bestiary',
                    '/dnd5e/compendium', '/dnd5e/characters', '/dnd5e/combat', '/dnd5e/gmscreen',
                    '/dnd5e/gmscreen?edition=classic'):
            resp = gm.get(url)
            assert resp.status_code == 200, (url, resp.status_code)
        # PF2e entry points bounce to the 5e equivalents for a 5e table
        assert gm.get('/tracker').headers['Location'].endswith('/dnd5e/tracker')
        assert gm.get('/gmscreen').headers['Location'].endswith('/dnd5e/gmscreen')
        screen = gm.get('/dnd5e/gmscreen').data.decode()
        assert 'Grappled' in screen and 'Three-quarters' in screen and 'Moderate' in screen
        nav = gm.get('/dnd5e/gm').data.decode()
        assert 'D&amp;D 5E' in nav and '/dnd5e/encounters' in nav

        # two players join
        def player(name):
            c = A.app.test_client()
            assert c.post('/register', data={'username': name, 'password': 'pw12345', 'display_name': name}).status_code == 302
            campaigns.add_member(cid, auth.get_user_by_username(name)['id'], 'player')
            act = c.post('/campaign/' + cid + '/activate')
            assert act.status_code == 302 and act.headers['Location'].endswith('/dnd5e/player'), act.headers.get('Location')
            return c
        p1, p2 = player('ana'), player('ben')

        # players are kept out of GM tools
        assert p1.get('/dnd5e/tracker').status_code in (302, 403)
        assert p1.post('/api/dnd5e/gm/combat/add_party', json={}).status_code == 403
        # players may browse beasts (Wild Shape, familiars) but not the rest of the bestiary
        assert p1.get('/api/dnd5e/content/monster?q=goblin').get_json()['results'] == []
        wolves = p1.get('/api/dnd5e/content/monster?q=wolf').get_json()['results']
        assert wolves and all(m['type'] == 'beast' for m in wolves)
        assert p1.get('/api/dnd5e/content/monster/goblin warrior|xmm').status_code == 403
        assert p1.get('/api/dnd5e/content/monster/wolf|xmm').status_code == 200

        # a player creates and builds their own character
        r = p1.post('/dnd5e/characters/new', json={'name': 'Ana the Bold', 'edition': 'one'})
        assert r.status_code == 200, r.data
        pid = r.get_json()['id']
        b = p1.post('/api/dnd5e/pc/' + pid + '/build', json={'patch': {
            'race': 'human|xphb', 'background': 'soldier|xphb', 'levels': [{'class': 'fighter|xphb', 'hp': None}],
            'ability_method': 'standard', 'base_scores': {'str': 15, 'dex': 14, 'con': 13, 'int': 8, 'wis': 12, 'cha': 10},
            'choices': {'bg:ability#alt': [0], 'bg:ability#0': {'str': 2, 'con': 1}}}})
        assert b.status_code == 200, b.data
        sheet = b.get_json()['sheet']
        assert sheet['hp_max'] == 12 and sheet['abilities']['str']['score'] == 17
        # an illegal pick is refused with a reason
        bad = p1.post('/api/dnd5e/pc/' + pid + '/build', json={'patch': {'choices': {'cls:fighter|xphb:skill:c0': ['arcana']}}})
        assert bad.status_code == 422 and 'not an option' in bad.get_json()['error']
        assert p1.get('/dnd5e/character/' + pid).status_code == 200
        # extras: a player adds a beast companion, but not a goblin
        r = p1.post('/api/dnd5e/pc/' + pid + '/extras', json={'action': 'add', 'monster_id': 'wolf|xmm'})
        assert r.status_code == 200 and r.get_json()['extras'][0]['hp'] == 11
        assert p1.post('/api/dnd5e/pc/' + pid + '/extras', json={'action': 'add', 'monster_id': 'goblin warrior|xmm'}).status_code == 403
        assert p1.get('/dnd5e/character/' + pid + '/build').status_code == 200

        # the other player can't read or edit it; the GM can
        assert p2.get('/api/dnd5e/pc/' + pid).status_code == 403
        assert p2.post('/api/dnd5e/pc/' + pid + '/vitals', json={'action': 'damage', 'amount': 5}).status_code == 403
        assert gm.get('/api/dnd5e/pc/' + pid).status_code == 200
        # a restored backup can carry a stale campaign_id inside the file: the
        # file's location scopes it, so access is unchanged (not locked out).
        from systems.dnd5e import store as d5store
        path = d5store.pc_path(cid, pid)
        raw = json.load(open(path, encoding='utf-8'))
        raw['campaign_id'] = 'f' * 32
        json.dump(raw, open(path, 'w', encoding='utf-8'))
        assert gm.get('/api/dnd5e/pc/' + pid).status_code == 200
        assert p1.get('/api/dnd5e/pc/' + pid).status_code == 200
        assert p2.get('/api/dnd5e/pc/' + pid).status_code == 403
        # /me lists the player's 5e character with a working link
        me = p1.get('/me').data.decode()
        assert 'Ana the Bold' in me and '/dnd5e/character/' + pid in me

        # GM runs combat: party + 2 goblins, damage the PC from the tracker
        r = gm.post('/api/dnd5e/gm/combat/add_party', json={})
        assert r.status_code == 200
        r = gm.post('/api/dnd5e/gm/combat/add_monster', json={'monster_id': 'goblin warrior|xmm', 'count': 2, 'hidden': False})
        enc = r.get_json()['encounter']
        pc_row = [c for c in enc['combatants'] if c['kind'] == 'pc'][0]
        r = gm.post('/api/dnd5e/gm/combat/damage', json={'cid': pc_row['cid'], 'amount': 5, 'type': 'slashing'})
        assert r.status_code == 200
        # ... the player's sheet sees it
        assert p1.get('/api/dnd5e/pc/' + pid).get_json()['live']['hp'] == 7
        # ... and the player healing on the sheet shows in the tracker
        assert p1.post('/api/dnd5e/pc/' + pid + '/vitals', json={'action': 'heal', 'amount': 2}).status_code == 200
        gm_view = gm.get('/api/dnd5e/combat').get_json()
        assert [c['hp'] for c in gm_view['combatants'] if c['kind'] == 'pc'] == [9]
        # players see monsters as a status word, never numbers
        pv = p1.get('/api/dnd5e/combat').get_json()
        mons = [c for c in pv['combatants'] if c['kind'] == 'monster']
        assert len(mons) == 2 and all('hp' not in c and c['status'] == 'Unhurt' for c in mons)
        # the player enters their own initiative
        assert p1.post('/api/dnd5e/pc/' + pid + '/initiative', json={'value': 17}).status_code == 200
        r = gm.post('/api/dnd5e/gm/combat/start', json={})
        assert r.status_code == 200, r.data
        assert r.get_json()['encounter']['started']

        # players get the 5e bottom nav with their own character for the turn ping
        page = p1.get('/dnd5e/combat').data.decode()
        assert 'id="player-nav"' in page and '"Ana the Bold"' in page
        assert 'id="player-nav"' not in gm.get('/dnd5e/combat').data.decode()

        # D&D Beyond import: a player uploads an export and owns the result
        raw = open(os.path.join('tests', 'fixtures', 'dnd5e_ddb_paladin_2024.json'), encoding='utf-8').read()
        r = p2.post('/api/dnd5e/import/ddb', json={'data': raw})
        assert r.status_code == 200, r.data
        imp = r.get_json()
        assert imp['report']['unmatched'] == [] and imp['url'].endswith('/build')
        got = p2.get('/api/dnd5e/pc/' + imp['id']).get_json()
        assert got['sheet']['hp_max'] == 52 and got['import_report']['edition'] == 'one'
        assert p1.get('/api/dnd5e/pc/' + imp['id']).status_code == 403      # not p1's character
        assert p2.post('/api/dnd5e/import/ddb', json={'data': 'nonsense'}).status_code == 422
        assert p2.post('/api/dnd5e/import/ddb', json={'url': 'https://evil.example/characters/1'}).status_code == 422

        # PDF import: multipart upload, owned by the uploader, report kept for the builder
        import io
        sys.path.insert(0, 'tests')
        from test_dnd5e_pdf_import import FIGHTER, make_pdf
        r = p2.post('/api/dnd5e/import/pdf', data={'file': (io.BytesIO(make_pdf(FIGHTER)), 'sheet.pdf')},
                    content_type='multipart/form-data')
        assert r.status_code == 200, r.data
        got = p2.get('/api/dnd5e/pc/' + r.get_json()['id']).get_json()
        assert got['sheet']['hp_max'] == 31 and got['import_report']['source'] == 'PDF'
        assert p1.get('/api/dnd5e/pc/' + r.get_json()['id']).status_code == 403
        bad = p2.post('/api/dnd5e/import/pdf', data={'file': (io.BytesIO(b'hello'), 'x.pdf')},
                      content_type='multipart/form-data')
        assert bad.status_code == 422 and 'not a PDF' in bad.get_json()['error']
        assert p2.post('/api/dnd5e/import/pdf', data={}, content_type='multipart/form-data').status_code == 400

        # encounter builder: difficulty for both editions
        d = gm.post('/api/dnd5e/gm/difficulty', json={'party_levels': [1, 1, 1, 1], 'monsters': [{'id': 'goblin warrior|xmm', 'count': 4}]}).get_json()
        assert d['one']['base_xp'] == 200 and d['classic']['multiplier'] == 2

        # content API renders statblocks + spells
        sb = gm.get('/api/dnd5e/content/monster/goblin warrior|xmm').get_json()
        assert sb['statblock']['ac'].startswith('15')
        sp = p1.get('/api/dnd5e/content/spell/fireball|xphb').get_json()
        assert 'Level 3' in sp['meta'][0] and 'd5-roll' in sp['html']
        # pictures: the owner uploads a portrait; it shows on the tracker row
        from PIL import Image
        buf = io.BytesIO(); Image.new('RGB', (40, 30), (1, 2, 3)).save(buf, 'PNG')
        png = buf.getvalue()
        up = lambda c, url, **form: c.post(url, data=dict(form), content_type='multipart/form-data')
        assert up(p2, '/api/dnd5e/pc/' + pid + '/portrait', file=(io.BytesIO(png), 'me.png')).status_code == 403
        r = up(p1, '/api/dnd5e/pc/' + pid + '/portrait', file=(io.BytesIO(png), 'me.png'))
        assert r.status_code == 200, r.data
        purl = r.get_json()['portrait']
        assert purl.startswith('/dnd5e/img/')
        got = p2.get(purl)                                 # any member of the table may see it
        assert got.status_code == 200 and got.mimetype == 'image/webp'
        got.close()                                        # send_file holds the file open until closed (Windows can't delete it then)
        assert gm.get('/dnd5e/img/' + 'f' * 32 + '.webp').status_code == 404
        assert gm.get('/dnd5e/img/..%2f..%2fcampaign.json').status_code == 404
        rows = gm.get('/api/dnd5e/combat').get_json()['combatants']
        assert [c['image'] for c in rows if c['kind'] == 'pc'] == [purl]
        assert up(p1, '/api/dnd5e/pc/' + pid + '/portrait', file=(io.BytesIO(b'nope'), 'x.png')).status_code == 422
        # replacing it removes the old file
        r2 = up(p1, '/api/dnd5e/pc/' + pid + '/portrait', file=(io.BytesIO(png), 'me2.png'))
        assert r2.get_json()['portrait'] != purl and gm.get(purl).status_code == 404
        # GM art for a monster combatant; players can't set it
        mon = [c for c in rows if c['kind'] == 'monster'][0]
        assert up(p1, '/api/dnd5e/gm/combat/image', cid=mon['cid'], file=(io.BytesIO(png), 'g.png')).status_code == 403
        r = up(gm, '/api/dnd5e/gm/combat/image', cid=mon['cid'], file=(io.BytesIO(png), 'g.png'))
        assert r.status_code == 200, r.data
        rows = gm.get('/api/dnd5e/combat').get_json()['combatants']
        assert [c['image'] for c in rows if c['cid'] == mon['cid']] == [r.get_json()['image']]
        assert up(gm, '/api/dnd5e/gm/combat/image', cid='nope', file=(io.BytesIO(png), 'g.png')).status_code == 404
        r = up(gm, '/api/dnd5e/gm/combat/image', cid=mon['cid'], clear='1')
        assert r.status_code == 200 and r.get_json()['image'] is None
        print('DND5E_E2E_OK')
    ''')
    assert 'DND5E_E2E_OK' in r.stdout, 'stdout:\n%s\nstderr:\n%s' % (r.stdout[-3000:], r.stderr[-5000:])
