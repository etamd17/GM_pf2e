"""A GM's homebrew Cosmere adversary must save, list, and add to the tracker.

The Cosmere GM builds custom enemies and needs to drop them into encounters.
The quick-create form (POST /api/cosmere/add_custom_adversary) builds a
Foundry-shaped doc; this verifies the doc round-trips: it's stored, resolvable
by id, listed for the tracker's adversary picker (so saved enemies are
RE-addable, not one-off), and adds to the encounter with a working Strike.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_homebrew_cosmere_adversary_round_trips_to_tracker():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A

        # Build + save via the same helpers the create route uses.
        doc = A._build_cosmere_adversary_doc({
            'name': 'Chasmfiend Broodling', 'health': 40, 'tier': 2, 'role': 'Brute',
            'phy': 14, 'cog': 9, 'spi': 10, 'deflect': 2,
            'atk_name': 'Claw', 'atk_dmg': '1d12+4', 'atk_type': 'keen', 'atk_mod': 11})
        A._save_cosmere_custom_adversary(doc)
        aid = doc['_id']

        saved = A._load_cosmere_custom_adversaries()
        assert any(a.get('_id') == aid for a in saved), 'not persisted'
        assert A._cosmere_doc_by_id(aid), 'not resolvable by id (add-to-tracker would 404)'

        # It builds a valid CosmereActor with the Strike (and the bonus alias the
        # tracker serializer needs).
        actor = A._cosmere_combatant(aid)
        assert actor and actor.name == 'Chasmfiend Broodling'
        assert actor.strikes and actor.strikes[0]['name'] == 'Claw'
        assert 'bonus' in actor.strikes[0], 'strike missing bonus (tracker would 500)'

        # Add it to the live tracker and confirm it serializes with a Strike.
        c = A.app.test_client(); J = {'X-Requested-With': 'XMLHttpRequest'}
        c.post('/api/add_combatant', data={'type': 'cosmere', 'path': aid}, headers=J)
        st = c.get('/api/tracker_state', headers=J).get_json()
        adv = [cb for cb in st['combatants'] if 'Broodling' in cb.get('name', '')]
        assert adv, 'homebrew adversary not on the tracker'
        assert adv[0].get('strikes') and adv[0]['strikes'][0].get('hit'), ('strike not serialized', adv[0].get('strikes'))
        print('HOMEBREW_ADV_OK')
    ''')
    assert 'HOMEBREW_ADV_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
