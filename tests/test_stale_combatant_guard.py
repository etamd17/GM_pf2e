"""Per-combatant actions on a STALE combatant id must fail loudly, not silently.

Before: adjust_hp/toggle_condition/etc. looped ACTIVE_ENCOUNTER for a matching
instance_id and, finding none, fell through to a 200 with unchanged state. The
client read that as success (it even toasted "Took N damage") so a stale tracker
tab looked like "nothing happened" -- HP wouldn't move, no error. Now these
return 409 + {"stale": true} so the UI surfaces it and re-syncs.

Legacy open mode (GM_PASSWORD='') so the GM gate passes; subprocess + throwaway
DATA_DIR per tests/test_cosmere_campaign_binding.py.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_stale_combatant_actions_409_valid_still_works():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        c = A.app.test_client()
        J = {'X-Requested-With': 'XMLHttpRequest'}

        # add a real combatant (one with HP > 3) so the encounter is non-empty
        path = next((p for p in A.MONSTER_LIBRARY if 'goblin-commando' in p), None) \\
            or next(p for p, m in A.MONSTER_LIBRARY.items() if getattr(m, 'current_hp', 0) > 3)
        assert c.post('/api/add_combatant', data={'type': 'monster', 'path': path}, headers=J).status_code == 200
        cb = c.get('/api/tracker_state', headers=J).get_json()['combatants'][0]
        iid = cb['instance_id']

        # a VALID id still applies (no regression): HP drops by 3
        before = cb['current_hp']
        assert before > 3, before
        ok = c.post('/api/adjust_hp/' + iid, data={'amount': '3', 'action': 'damage'}, headers=J)
        assert ok.status_code == 200
        after = c.get('/api/tracker_state', headers=J).get_json()['combatants'][0]['current_hp']
        assert after == before - 3, (before, after)

        # a STALE id on each per-combatant edit action now 409s with stale=true
        # (no more silent 200 no-op)
        actions = [
            ('/api/adjust_hp/', {'amount': '5', 'action': 'damage'}),
            ('/api/toggle_condition/', {'condition': 'frightened', 'action': 'increase'}),
            ('/api/update_initiative/', {'initiative': '12'}),
            ('/api/set_persistent_damage/', {'damage': '1d6', 'type': 'fire'}),
            ('/api/toggle_elite_weak/', {'mode': 'elite'}),
            ('/api/toggle_combatant_visibility/', {}),
            ('/api/set_combatant_tactics/', {'tactics': 'x'}),
        ]
        for ep, data in actions:
            rr = c.post(ep + 'NOT-A-REAL-INSTANCE-ID', data=data, headers=J)
            assert rr.status_code == 409, (ep, rr.status_code)
            body = rr.get_json() or {}
            assert body.get('stale') is True, (ep, body)

        # the real combatant is untouched by all those stale calls
        final = c.get('/api/tracker_state', headers=J).get_json()['combatants']
        assert len(final) == 1 and final[0]['instance_id'] == iid

        print('STALE_GUARD_OK')
    ''')
    assert 'STALE_GUARD_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
