"""_save_campaign_config must NOT clobber the multi-campaign doc's identity keys.

It once rewrote campaign.json from the lossy config view, wiping id/slug/system/
members. (The session-ritual / recap UI that originally exercised this path was
removed; this guards the underlying config-save behavior on its own.)
"""
from __future__ import annotations

import json

import app


def test_save_campaign_config_preserves_doc_identity(tmp_path, monkeypatch):
    f = tmp_path / 'campaign.json'
    f.write_text(json.dumps({
        'id': 'abc123', 'slug': 'roshar', 'system': 'cosmere',
        'members': [{'user_id': 'u1', 'role': 'gm'}],
        'system_config': {'house_rule': True},
        'name': 'Roshar', 'session_number': 1,
    }))
    monkeypatch.setattr(app, 'CAMPAIGN_FILE', str(f))
    app._save_campaign_config({'tagline': 'A world of storms and stone.'})
    saved = json.loads(f.read_text())
    # multi-campaign doc identity survives a config write
    assert saved['system'] == 'cosmere'
    assert saved['id'] == 'abc123' and saved['slug'] == 'roshar'
    assert saved['members'] == [{'user_id': 'u1', 'role': 'gm'}]
    assert saved['system_config'] == {'house_rule': True}
    # and the config update lands
    assert saved['tagline'] == 'A world of storms and stone.'
