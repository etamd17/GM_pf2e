"""The tracker's 'Custom Monster' quick form posted to /api/add_custom_monster,
which did not exist (the fallback to /api/add_combatant has no 'custom' branch),
so adding a custom monster silently did nothing. The route now creates the
monster, persists it as a reusable bestiary entry (atomic), and adds it to the
live encounter.
"""
from __future__ import annotations

import os

import app


def test_add_custom_monster_creates_persists_and_adds(tmp_path, monkeypatch):
    mon_dir = tmp_path / 'mon'
    enc_dir = tmp_path / 'enc'
    mon_dir.mkdir()
    enc_dir.mkdir()
    monkeypatch.setattr(app, 'MONSTER_DIR', str(mon_dir))
    monkeypatch.setattr(app, 'ENCOUNTER_DIR', str(enc_dir))
    saved = list(app.ACTIVE_ENCOUNTER)
    try:
        app.ACTIVE_ENCOUNTER.clear()
        r = app.app.test_client().post('/api/add_custom_monster', json={
            'name': 'Test Ogre', 'level': 3, 'hp': 50, 'ac': 18,
            'fort': 12, 'ref': 8, 'will': 6, 'speed': 25, 'perception': 9,
            'atk_name': 'Club', 'atk_mod': 12, 'atk_dmg': '1d12+6'})
        assert r.status_code == 200
        # added to the LIVE encounter (the bug: it added nothing)
        added = [c for c in app.ACTIVE_ENCOUNTER if getattr(c, 'name', '') == 'Test Ogre']
        assert len(added) == 1
        assert added[0].instance_id
        # persisted as a reusable bestiary entry (atomic) + in the library
        assert os.path.exists(mon_dir / 'Test_Ogre.json')
        assert 'Test_Ogre.json' in app.MONSTER_LIBRARY
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.MONSTER_LIBRARY.pop('Test_Ogre.json', None)
        app._invalidate_tracker_cache()
