"""Basic-save AOE resolver: roll each target's save, compute the degree, and
report the post-save damage (crit-success 0 / success half / failure full /
crit-failure double). The endpoint computes + logs only; the existing
/api/adjust_hp path applies the damage (so W/R/I, temp HP, and dying stay in one
tested place). Legacy open mode; subprocess + throwaway DATA_DIR.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_degree_of_success_helper():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        d = A._degree_of_success
        # boundaries vs DC 20
        assert d(30, 20) == 'crit_success'      # >= dc+10
        assert d(20, 20) == 'success'           # >= dc
        assert d(19, 20) == 'failure'           # below dc
        assert d(10, 20) == 'crit_failure'      # <= dc-10
        # nat 20 bumps one step up; nat 1 bumps one step down
        assert d(19, 20, d20=20) == 'success'   # failure -> success
        assert d(20, 20, d20=1) == 'failure'    # success -> failure
        assert d(30, 20, d20=20) == 'crit_success'   # cannot exceed crit success
        assert d(10, 20, d20=1) == 'crit_failure'    # cannot drop below crit failure
        # basic-save multipliers
        m = A._BASIC_SAVE_MULT
        assert m['crit_success'] == 0.0 and m['success'] == 0.5 and m['failure'] == 1.0 and m['crit_failure'] == 2.0
        print('DEGREE_OK')
    ''')
    assert 'DEGREE_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)


def test_multi_save_damage_endpoint():
    r = _run('''
        import tempfile, os, math
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        c = A.app.test_client()
        J = {'X-Requested-With': 'XMLHttpRequest'}

        # two real combatants with HP
        path = next((p for p in A.MONSTER_LIBRARY if 'goblin-commando' in p), None) \\
            or next(p for p, m in A.MONSTER_LIBRARY.items() if getattr(m, 'current_hp', 0) > 3)
        for _ in range(2):
            assert c.post('/api/add_combatant', data={'type': 'monster', 'path': path}, headers=J).status_code == 200
        st = c.get('/api/tracker_state', headers=J).get_json()['combatants']
        ids = [x['instance_id'] for x in st]
        hp_before = {x['instance_id']: x['current_hp'] for x in st}

        # resolve a 20-damage reflex burst, DC 20, with FIXED d20 rolls so the
        # degree is deterministic regardless of the creature's save bonus
        body = {'ids': ids, 'save': 'reflex', 'dc': 20, 'damage': 20,
                'damage_type': 'fire', 'rolls': {ids[0]: 20, ids[1]: 1}}
        rr = c.post('/api/multi_save_damage', json=body, headers=J)
        assert rr.status_code == 200, rr.status_code
        results = rr.get_json()['results']
        assert len(results) == 2
        by_id = {x['instance_id']: x for x in results}

        # each result's effective damage matches its own reported degree+multiplier
        for x in results:
            assert x['effective'] == math.floor(20 * A._BASIC_SAVE_MULT[x['degree']]), x
            assert x['d20'] in (1, 20)

        # nat-20 roll trends toward less damage than the nat-1 roll (same DC/damage)
        assert by_id[ids[0]]['effective'] <= by_id[ids[1]]['effective']

        # the COMPUTE endpoint must not change HP (the client applies via adjust_hp)
        st2 = c.get('/api/tracker_state', headers=J).get_json()['combatants']
        hp_after = {x['instance_id']: x['current_hp'] for x in st2}
        assert hp_after == hp_before, (hp_before, hp_after)

        # a stale id is ignored, not fatal; empty selection 400s
        assert c.post('/api/multi_save_damage', json={'ids': ['NOPE'], 'save': 'will', 'dc': 15, 'damage': 6}, headers=J).status_code == 200
        assert c.post('/api/multi_save_damage', json={'ids': [], 'save': 'will', 'dc': 15, 'damage': 6}, headers=J).status_code == 400

        print('MULTI_SAVE_OK')
    ''')
    assert 'MULTI_SAVE_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
