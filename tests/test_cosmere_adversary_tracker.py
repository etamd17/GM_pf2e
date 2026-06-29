"""A Cosmere adversary with a weapon Strike must not crash the tracker.

Live bug: the non-PC strike serializers (tracker_state + the stat-detail view)
hard-indexed s['bonus'], but a CosmereActor's Strikes only carried 'mod'. Adding
a weapon-bearing Cosmere adversary (e.g. the bestiary's "Ellar", or any custom
adversary with a weapon) therefore 500'd /api/tracker_state -> a dead tracker
mid-session. Fix: CosmereActor Strikes now carry 'bonus' (mirrors 'mod'), and
both serializers fall back to .get('bonus', .get('mod', 0)) defensively.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_weapon_bearing_cosmere_adversary_serializes_on_tracker():
    r = _run('''
        import tempfile, os, json
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        import systems.cosmere as C

        # Find a bestiary adversary that has a weapon item (the crash trigger).
        adv_id = None
        for d in C.adversary_docs():
            if any(isinstance(it, dict) and it.get('type') == 'weapon' for it in (d.get('items') or [])):
                adv_id = d.get('_id'); break
        assert adv_id, 'no weapon-bearing Cosmere adversary in the bestiary to test'

        c = A.app.test_client(); J = {'X-Requested-With': 'XMLHttpRequest'}
        c.post('/api/add_combatant', data={'type': 'cosmere', 'path': adv_id}, headers=J)

        resp = c.get('/api/tracker_state', headers=J)
        assert resp.status_code == 200, ('tracker_state status', resp.status_code)
        state = resp.get_json()
        cos = [cb for cb in state['combatants'] if cb.get('system') == 'cosmere']
        assert cos, 'cosmere adversary not on the tracker'
        # Its strikes must serialize with a usable 'hit' (no KeyError).
        for cb in cos:
            for st in cb.get('strikes', []):
                assert 'hit' in st and st['hit'], ('strike missing hit', st)
        print('COSMERE_ADV_TRACKER_OK')
    ''')
    assert 'COSMERE_ADV_TRACKER_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
