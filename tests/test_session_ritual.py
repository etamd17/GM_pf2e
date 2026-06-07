"""Session-ritual tools for Cosmere + the campaign-config preservation fix.

The opening ritual (Begin Session curtain + "Previously on..." recap) reuses the
system-agnostic session routes; the Cosmere GM hub gains the control card. The
underlying `_save_campaign_config` must NOT clobber the multi-campaign doc's
identity keys (it once rewrote campaign.json from the lossy config view, wiping
id/slug/system/members -- a recap save would break the campaign).
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
    # the recap-save path
    app._save_campaign_config({'last_recap': 'Previously, the heroes...'})
    saved = json.loads(f.read_text())
    # multi-campaign doc identity survives
    assert saved['system'] == 'cosmere'
    assert saved['id'] == 'abc123' and saved['slug'] == 'roshar'
    assert saved['members'] == [{'user_id': 'u1', 'role': 'gm'}]
    assert saved['system_config'] == {'house_rule': True}
    # and the config update lands
    assert saved['last_recap'] == 'Previously, the heroes...'


def test_cosmere_gm_hub_has_session_ritual_card(monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    body = app.app.test_client().get('/cosmere/gm').data.decode()
    assert 'Begin the Session' in body           # the ritual card header
    assert 'cos-recap' in body                   # the recap editor
    assert "cosBeginSession()" in body           # drops the curtain via /api/session/begin
    assert 'Previously on' in body


def test_session_begin_and_recap_routes_are_system_agnostic():
    """The curtain begin + recap-save routes exist and respond (campaign-scoped,
    no PF2e assumptions)."""
    c = app.app.test_client()
    r = c.post('/api/session/recap', json={'recap': 'A test recap.'})
    assert r.status_code == 200 and r.get_json().get('success')
    r2 = c.post('/api/session/begin', json={'bump_session': False})
    assert r2.status_code == 200 and r2.get_json().get('success')
