"""GM-authored Cosmere custom adversaries: stat a creature from a quick form,
persist it to the campaign's homebrew store (reusable + survives a restart), and
add it to the live encounter. The Cosmere sibling of /api/add_custom_monster.
"""
from __future__ import annotations

import app
import systems.cosmere as cos


def test_build_cosmere_adversary_doc_uses_overrides():
    doc = app._build_cosmere_adversary_doc({
        'name': 'Chasmfiend', 'level': 7, 'phy': 18, 'cog': 12, 'spi': 14,
        'health': 90, 'deflect': 4, 'atk_name': 'Claw', 'atk_mod': 16, 'atk_dmg': '2d10+8'})
    a = cos.CosmereActor(doc)
    assert a.name == 'Chasmfiend' and a.type == 'adversary'
    assert a.defenses == {'phy': 18, 'cog': 12, 'spi': 14}    # overrides honored
    assert a.health_max == 90 and a.deflect['value'] == 4
    assert len(a.strikes) == 1
    assert a.strikes[0]['name'] == 'Claw' and a.strikes[0]['mod'] == 16
    assert a.strikes[0]['damage'] == '2d10+8'


def test_add_custom_adversary_route_adds_persists_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, 'COSMERE_ADVERSARIES_FILE', str(tmp_path / 'adv.json'))
    monkeypatch.setattr(app, 'ENCOUNTER_DIR', str(tmp_path))
    saved = list(app.ACTIVE_ENCOUNTER)
    try:
        app.ACTIVE_ENCOUNTER.clear()
        r = app.app.test_client().post('/api/cosmere/add_custom_adversary', json={
            'name': 'Voidbringer', 'phy': 15, 'cog': 13, 'spi': 16, 'health': 40,
            'deflect': 3, 'atk_name': 'Smite', 'atk_mod': 11, 'atk_dmg': '1d12+5'})
        assert r.status_code == 200 and r.get_json()['success'] is True
        added = [c for c in app.ACTIVE_ENCOUNTER if getattr(c, 'name', '') == 'Voidbringer']
        assert len(added) == 1 and getattr(added[0], 'system', '') == 'cosmere'
        # persisted so it resolves by id (reusable + restart-safe)
        advs = app._load_cosmere_custom_adversaries()
        assert any(d.get('name') == 'Voidbringer' for d in advs)
        adv_id = added[0].restore_id
        assert app._cosmere_doc_by_id(adv_id) is not None

        # simulate a restart: force the autosave flush (the route only marks it
        # dirty; a background thread writes it live), then rehydrate.
        app._do_persist_encounter_state()
        app.ACTIVE_ENCOUNTER.clear()
        app._restore_encounter_autosave()
        names = [getattr(c, 'name', '') for c in app.ACTIVE_ENCOUNTER]
        assert 'Voidbringer' in names
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app._invalidate_tracker_cache()
