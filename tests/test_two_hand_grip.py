"""Grip toggle (1H <-> 2H) must actually swap the weapon's damage die.

Repro of the player-sheet bug: Pathbuilder exports a two-hand weapon with the
2h die already baked into `die` (Bastard Sword 2h -> die='d12') and only a
"(2h)" display string, no traits. The importer was (a) storing that baked d12
as the BASE damage and (b) re-forcing is_two_handed=True from the display on
every reload -- so toggling 1H/2H changed a flag nothing read and the die never
moved. With the reference's real base (1d8 + two-hand-d12 trait), the attacks
property can swap d8 <-> d12 from the flag the grip toggle owns.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_grip_toggle_swaps_bastard_sword_die():
    r = _run('''
        import tempfile, os, json
        from urllib.parse import quote
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        c = A.app.test_client(); J = {'X-Requested-With': 'XMLHttpRequest'}
        pb = json.load(open('tests/fixtures/goel_l10.json'))
        c.post('/api/import_pathbuilder', json=pb, headers=J)
        name = pb['build']['name']

        def grip():
            ps = c.get('/api/pc_state/' + quote(name), headers=J).get_json()
            atks = (ps.get('derived') or {}).get('attacks') or ps.get('attacks') or []
            bs = [a for a in atks if 'Bastard' in a.get('name', '')][0]
            return bs.get('damage', ''), bs.get('is_two_handed')

        d0 = grip()                                  # seeded 2h from "(2h)" display
        assert 'd12' in d0[0] and d0[1] is True, ('initial', d0)

        c.post('/api/toggle_two_hand/' + quote(name), json={'name': 'Bastard Sword'}, headers=J)
        d1 = grip()                                  # -> one-handed
        assert d1[1] is False, ('flag not flipped to 1H:', d1)
        assert 'd8' in d1[0], ('die did not drop to d8 in 1H:', d1)

        c.post('/api/toggle_two_hand/' + quote(name), json={'name': 'Bastard Sword'}, headers=J)
        d2 = grip()                                  # -> two-handed again
        assert d2[1] is True and 'd12' in d2[0], ('die did not return to d12 in 2H:', d2)
        print('GRIP_OK')
    ''')
    assert 'GRIP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
