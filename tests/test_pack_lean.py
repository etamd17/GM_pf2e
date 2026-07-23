"""Guards for the feat/spell payload trimming that keeps the builder + level-up
pages light (descriptions shipped out-of-band via /api/pack_detail).

The builder/level-up pickers inline ~8,600 feats + ~1,800 spells but only ever
render one description at a time (on click). `_pack_list_lean` strips the heavy
`description` field for the list payload; `/api/pack_detail` serves the clicked
one from the in-RAM packs. These tests pin: (1) the projection drops descriptions
but keeps the fields the picker LIST + prereq eligibility read, (2) the
`grants_focus` flag survives for the level-up recap, (3) the endpoint round-trips
a real feat (by _id) and spell (by name)."""
from __future__ import annotations

import pytest

import app


def test_pack_list_lean_strips_description_keeps_rest():
    items = [{
        '_id': 'abc123', 'name': 'Sample Feat', 'level': 4,
        'traits': ['general'], 'prereqs_struct': {'level': 4},
        'prerequisites_raw': 'level 4', 'description': '<p>Long HTML body…</p>',
    }]
    lean = app._pack_list_lean(items)
    assert 'description' not in lean[0]
    for k in ('_id', 'name', 'level', 'traits', 'prereqs_struct', 'prerequisites_raw'):
        assert lean[0][k] == items[0][k], f"lost picker field {k}"


def test_pack_list_lean_feat_flag_preserves_grants_focus():
    items = [
        {'name': 'Focus Feat', 'description': 'You gain a focus point and increase your maximum.'},
        {'name': 'Plain Feat', 'description': 'You hit harder.'},
    ]
    lean = app._pack_list_lean(items, feat=True)
    assert lean[0].get('grants_focus') is True
    assert 'grants_focus' not in lean[1]
    # Spells (feat=False) never get the flag even if the text matches.
    assert 'grants_focus' not in app._pack_list_lean(items)[0]


def test_builder_feats_lean_is_description_free():
    lean = app._builder_feats_lean()
    assert set(lean.keys()) == set(app.BUILDER_FEATS.keys())
    for cat, arr in lean.items():
        for it in arr:
            assert 'description' not in it, f"{cat} feat still carries a description"


def _first_with(items, pred):
    for it in items:
        if pred(it):
            return it
    return None


def test_pack_detail_endpoint_round_trips_feat_and_spell():
    client = app.app.test_client()

    # A real feat that has both an _id and a non-empty description.
    feat = None
    for arr in app.BUILDER_FEATS.values():
        feat = _first_with(arr, lambda it: it.get('_id') and it.get('description'))
        if feat:
            break
    if feat is None:
        pytest.skip("no feat with _id + description in the loaded packs")
    r = client.get(f"/api/pack_detail/feat/{feat['_id']}")
    assert r.status_code == 200
    assert r.get_json()['description'] == feat['description']

    # A real spell (keyed by name — spells carry no _id).
    spell = _first_with(app.BUILDER_SPELLS, lambda it: it.get('name') and it.get('description'))
    if spell is None:
        pytest.skip("no spell with a description in the loaded packs")
    r = client.get(f"/api/pack_detail/spell/{spell['name']}")
    assert r.status_code == 200
    assert r.get_json()['description'] == spell['description']


def test_pack_detail_unknown_key_returns_empty_not_error():
    client = app.app.test_client()
    r = client.get("/api/pack_detail/feat/definitely-not-a-real-id-xyz")
    assert r.status_code == 200
    assert r.get_json()['description'] == ''
