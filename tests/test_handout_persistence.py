"""GM handout records must survive a restart/redeploy. They were an in-memory
list (HANDOUTS) capped at 50 with no disk write, so every Railway redeploy
dropped every handout pushed earlier in the campaign (only the uploaded images
persisted). Now persisted to a campaign-scoped handouts.json.
"""
from __future__ import annotations

import app


def test_handouts_persist_across_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'HANDOUTS_FILE', str(tmp_path / 'handouts.json'))
    saved = list(app.HANDOUTS)
    try:
        app.HANDOUTS[:] = []
        client = app.app.test_client()
        r = client.post('/api/handouts', json={'title': 'Treasure Map',
                                                'content': 'X marks the spot',
                                                'recipients': ['all']})
        assert r.status_code == 200

        # simulate a worker restart: wipe in-memory state, reload from disk
        app.HANDOUTS[:] = []
        app._load_handouts()
        assert any(h['title'] == 'Treasure Map' for h in app.HANDOUTS)

        # deletion is persisted too
        hid = next(h['id'] for h in app.HANDOUTS if h['title'] == 'Treasure Map')
        client.delete('/api/handouts/' + hid)
        app.HANDOUTS[:] = []
        app._load_handouts()
        assert not any(h['title'] == 'Treasure Map' for h in app.HANDOUTS)
    finally:
        app.HANDOUTS[:] = saved
