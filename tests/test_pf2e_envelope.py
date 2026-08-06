"""Guard the PF2e in-app character envelope.

PF2e PCs created or re-imported through the app (save_new_character /
import_pathbuilder) must carry the FLAT-ADDITIVE campaign envelope (id /
owner_user_id / campaign_id / schema_version) or they fall out of the
invite -> claim -> My-Characters pipeline entirely, and re-importing a CLAIMED
PC must not un-claim it. These are pure-helper tests (no Flask / no party_data)
so they run in CI.
"""
from __future__ import annotations

import core.storage as storage

CID = storage.new_id()          # campaign ids are 32-char dashless uuid hex


def test_fresh_character_gets_unclaimed_envelope():
    doc = {'success': True, 'build': {'name': 'Bob', 'class': 'Fighter'}}
    out = storage.ensure_character_envelope(doc, CID)
    assert storage.is_wrapped(out)
    assert out['campaign_id'] == CID
    assert out['system'] == 'pf2e'
    assert out['owner_user_id'] is None          # GM-created, not yet claimed
    assert out['build']['name'] == 'Bob'         # native content preserved
    assert isinstance(out['id'], str) and out['id']


def test_reimport_preserves_ownership_and_id():
    existing = storage.ensure_character_envelope(
        {'success': True, 'build': {'name': 'Bob', 'level': 3}}, CID)
    existing['owner_user_id'] = 'user-7'          # a player has claimed it
    newdoc = {'success': True, 'build': {'name': 'Bob', 'level': 4}}   # PB re-import
    out = storage.ensure_character_envelope(newdoc, CID, existing=existing)
    assert out['id'] == existing['id']            # same identity, still claimable
    assert out['owner_user_id'] == 'user-7'       # ownership NOT dropped
    assert out['campaign_id'] == CID
    assert out['build']['level'] == 4             # new imported content wins


def test_already_wrapped_doc_is_idempotent():
    doc = storage.ensure_character_envelope(
        {'success': True, 'build': {'name': 'Z'}}, CID)
    out = storage.ensure_character_envelope(doc, CID)
    assert out['id'] == doc['id']
    assert out['owner_user_id'] is None
