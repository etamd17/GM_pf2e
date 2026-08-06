"""Monster Strikes must not show phantom duplicates.

Foundry NPC data carries BOTH a `weapon` inventory item (no NPC attack bonus or
damageRolls) and the real `melee`/`ranged` Strike for the same weapon. The parser
used to emit a strike for `weapon` too -> every such monster showed each weapon
twice on the tracker: a phantom "+0 / Check Details" beside the real Strike. Now
melee/ranged are the Strikes; a `weapon` item is a fallback only when no
melee/ranged Strike covers that name (so weapon-only creatures still get a Strike).
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_real_goblin_has_no_phantom_duplicate_strikes():
    r = _run('''
        import tempfile, os, copy
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        path = next((p for p in A.MONSTER_LIBRARY if 'goblin-warrior' in p), None)
        assert path, 'goblin-warrior not in library'
        m = copy.deepcopy(A.MONSTER_LIBRARY[path])
        names = [s['name'] for s in m.strikes]
        assert len(names) == len(set(names)), ('duplicate strike names', names)
        assert m.strikes, 'no strikes parsed'
        assert all(s['damage'] != 'Check Details' for s in m.strikes), ('phantom remains', m.strikes)
        assert all(s['bonus'] != 0 for s in m.strikes), ('phantom +0 remains', m.strikes)
        print('GOBLIN_DEDUP_OK', names)
    ''')
    assert 'GOBLIN_DEDUP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)


def test_ranged_strikes_parse_and_weapon_only_falls_back():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        doc = {
            'name': 'Test Brute', 'type': 'npc',
            'system': {'attributes': {'hp': {'value': 30, 'max': 30}, 'ac': {'value': 16}},
                       'details': {'level': {'value': 2}}, 'traits': {'value': []}},
            'items': [
                # real melee strike + its inventory weapon (phantom source)
                {'type': 'weapon', 'name': 'Greatclub', 'system': {}},
                {'type': 'melee', 'name': 'Greatclub', 'system': {'bonus': {'value': 9},
                    'damageRolls': {'0': {'damage': '1d10+4', 'damageType': 'bludgeoning'}}}},
                # a real RANGED strike (parser used to ignore `ranged` entirely)
                {'type': 'ranged', 'name': 'Javelin', 'system': {'bonus': {'value': 7},
                    'damageRolls': {'0': {'damage': '1d6+4', 'damageType': 'piercing'}}}},
                # a weapon with NO melee/ranged counterpart -> must still appear
                {'type': 'weapon', 'name': 'Net', 'system': {}},
            ],
        }
        m = A.Monster(doc, 'test-brute.json')
        by = {s['name']: s for s in m.strikes}
        names = [s['name'] for s in m.strikes]
        assert len(names) == len(set(names)), ('duplicates', names)
        assert by['Greatclub']['bonus'] == 9 and 'Check Details' not in by['Greatclub']['damage'], by.get('Greatclub')
        assert 'Javelin' in by and by['Javelin']['bonus'] == 7, ('ranged not parsed', names)
        assert 'Net' in by, ('weapon-only fallback dropped', names)
        print('DEDUP_FALLBACK_OK', names)
    ''')
    assert 'DEDUP_FALLBACK_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
