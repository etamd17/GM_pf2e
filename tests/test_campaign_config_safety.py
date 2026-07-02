"""Campaign-doc safety for _save_campaign_config.

campaign.json is BOTH the per-campaign config (mood, session number, cosmere
toggles...) AND the campaign identity doc (id / system / members / name).
Config writes happen constantly during play, so they must never be able to
destroy the identity keys:

  1. If the existing file fails to parse (e.g. truncated by a redeploy
     SIGKILL mid-write), the config write must ABORT -- never rebuild the doc
     from an empty dict, which wipes system/members and silently turns a
     Cosmere campaign into a PF2e one (the recurring wrong-system bug).
  2. A normal config write must round-trip the identity keys untouched.
  3. The write itself must be atomic (temp file + os.replace), so a crash
     mid-write can't leave a half-written doc for case 1 to trip over later.
"""
from __future__ import annotations

import json
import os

import pytest

import app


@pytest.fixture
def campaign_file(tmp_path, monkeypatch):
    path = str(tmp_path / 'campaign.json')
    doc = {
        'id': 'abc123', 'slug': 'stormlight', 'name': 'Stormlight',
        'system': 'cosmere',
        'members': [{'user_id': 'u1', 'role': 'gm'}],
        'session_number': 7,
    }
    with open(path, 'w', encoding='utf-8') as fp:
        json.dump(doc, fp)
    monkeypatch.setattr(app, 'CAMPAIGN_FILE', path)
    return path


def test_config_write_preserves_identity_keys(campaign_file):
    app._save_campaign_config({'scene_mood': 'tension'})
    with open(campaign_file, encoding='utf-8') as fp:
        doc = json.load(fp)
    assert doc['system'] == 'cosmere'
    assert doc['id'] == 'abc123'
    assert doc['members'] == [{'user_id': 'u1', 'role': 'gm'}]
    assert doc['name'] == 'Stormlight'
    assert doc['scene_mood'] == 'tension'


def test_config_write_aborts_on_corrupt_doc(campaign_file):
    """A truncated/corrupt campaign.json must not be clobbered by the next
    config write -- the doc (with its recoverable partial content) stays
    byte-identical and the write is dropped."""
    with open(campaign_file, 'w', encoding='utf-8') as fp:
        fp.write('{"id": "abc123", "system": "cosm')   # truncated mid-write
    before = open(campaign_file, encoding='utf-8').read()
    app._save_campaign_config({'scene_mood': 'dread'})
    after = open(campaign_file, encoding='utf-8').read()
    assert after == before, 'corrupt campaign doc was overwritten by a config write'


def test_config_write_creates_fresh_file_when_absent(campaign_file):
    """First-ever write in the legacy layout (no file yet) still works -- the
    abort guard only applies when a file EXISTS but cannot be parsed."""
    os.remove(campaign_file)
    app._save_campaign_config({'scene_mood': 'calm'})
    with open(campaign_file, encoding='utf-8') as fp:
        doc = json.load(fp)
    assert doc['scene_mood'] == 'calm'
