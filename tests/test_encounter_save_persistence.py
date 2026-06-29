"""Manual save/load must not drop a delayed combatant or condition timers.

The autosave path persisted `delaying` + `condition_expiry`, but the MANUAL
"Save Encounter" / "Load Encounter" path omitted both on save and never restored
`delaying` -- so "save Round 3, resume next session" silently lost delayed status
and temp-condition auto-expiry timers (a frightened-2-for-3-rounds becomes a
permanent frightened 2). The Cosmere restore path also never restored `delaying`
(so a delayed Cosmere combatant lost it even across a Railway restart). Fixed by
mirroring the autosave field set into save_encounter + restoring `delaying` in
both the PF2e and Cosmere restore paths.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_manual_save_load_preserves_delaying_and_condition_expiry():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        c = A.app.test_client(); J = {'X-Requested-With': 'XMLHttpRequest'}

        # PF2e monster + a weapon-bearing Cosmere adversary, both delaying.
        path = next((p for p in A.MONSTER_LIBRARY if 'goblin' in p), next(iter(A.MONSTER_LIBRARY)))
        c.post('/api/add_combatant', data={'type': 'monster', 'path': path}, headers=J)
        import systems.cosmere as C
        adv = next((d['_id'] for d in C.adversary_docs()
                    if any(isinstance(i, dict) and i.get('type') == 'weapon' for i in (d.get('items') or []))), None)
        if adv:
            c.post('/api/add_combatant', data={'type': 'cosmere', 'path': adv}, headers=J)

        for cb in A.ACTIVE_ENCOUNTER:
            cb.delaying = True
            cb.conditions = {'frightened': 2}
            cb.condition_expiry = {'frightened': 5}

        c.post('/api/save_encounter', data={'encounter_name': 'resume_test'}, headers=J)
        # wipe live state, then reload from the saved file
        A.ACTIVE_ENCOUNTER.clear()
        c.post('/api/load_encounter', data={'encounter_name': 'resume_test'}, headers=J)

        assert A.ACTIVE_ENCOUNTER, 'nothing restored'
        for cb in A.ACTIVE_ENCOUNTER:
            assert getattr(cb, 'delaying', False) is True, ('delaying lost', cb.name)
            assert dict(getattr(cb, 'condition_expiry', {})).get('frightened') == 5, ('expiry lost', cb.name)
        print('SAVE_PERSIST_OK count=%d' % len(A.ACTIVE_ENCOUNTER))
    ''')
    assert 'SAVE_PERSIST_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
