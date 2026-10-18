"""Campaign soft-delete (data-safety): deleting a campaign moves it to a trash
dir (restorable for ~30 days) instead of an immediate rmtree, so a misclick can't
destroy a multi-month campaign. Restore moves it back (data intact); purge is the
permanent delete; expired trash self-cleans by trashed-dir mtime.
"""
from __future__ import annotations

import os
import re
from html import unescape

import pytest

import core.storage as S
import core.campaigns as C


@pytest.fixture
def tmpdata(tmp_path, monkeypatch):
    monkeypatch.setattr(S, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    monkeypatch.setattr(S, 'CAMPAIGNS_TRASH_DIR', str(tmp_path / 'campaigns_trash'))
    monkeypatch.setattr(S, 'SERVER_STATE_FILE', str(tmp_path / 'server_state.json'))
    yield tmp_path


def _mk(name='Saga', user='u1'):
    cid = C.create_campaign(name, 'pf2e', user)['id']
    os.makedirs(S.party_dir(cid), exist_ok=True)
    with open(os.path.join(S.party_dir(cid), 'pc.json'), 'w', encoding='utf-8') as f:
        f.write('{"keep":1}')
    return cid


def test_delete_is_soft_and_restorable(tmpdata):
    cid = _mk()
    C.delete_campaign(cid)
    assert C.get_campaign(cid) is None                       # gone from active
    assert cid in [c['id'] for c in C.list_trashed()]        # in the trash
    assert '_trashed_at' in (C.get_trashed_campaign(cid) or {})
    assert C.restore_campaign(cid) is not None
    assert C.get_campaign(cid) is not None                   # back in active
    assert '_trashed_at' not in C.get_campaign(cid)          # marker cleared
    assert os.path.exists(os.path.join(S.party_dir(cid), 'pc.json'))  # data intact


def test_delete_frees_the_live_slot(tmpdata):
    cid = _mk()
    S.set_live_campaign_id(cid)
    C.delete_campaign(cid)
    assert S.get_live_campaign_id() != cid


def test_purge_is_permanent(tmpdata):
    cid = _mk()
    C.delete_campaign(cid)
    C.purge_campaign(cid)
    assert C.get_campaign(cid) is None and C.get_trashed_campaign(cid) is None
    assert not os.path.isdir(S.campaign_trash_dir(cid))


def test_purge_expired_uses_age(tmpdata):
    cid = _mk()
    C.delete_campaign(cid)
    # not yet old -> survives
    assert C.purge_expired_trash(ttl_days=30) == 0 and cid in [c['id'] for c in C.list_trashed()]
    # backdate the trashed dir 40 days -> purged
    old = os.path.getmtime(S.campaign_trash_dir(cid)) - 40 * 86400
    os.utime(S.campaign_trash_dir(cid), (old, old))
    assert C.purge_expired_trash(ttl_days=30) == 1
    assert cid not in [c['id'] for c in C.list_trashed()]


def test_trashed_for_user_is_gm_scoped(tmpdata):
    cid = _mk(user='gm1')
    C.delete_campaign(cid)
    assert cid in [c['id'] for c in C.trashed_for_user('gm1')]   # GM sees own trash
    assert C.trashed_for_user('someone_else') == []             # others don't


def test_account_home_renders_trash_section():
    import app
    import flask
    with app.app.test_request_context('/me'):
        html = flask.render_template(
            'account_home.html',
            user={'id': 'u1', 'username': 'gm', 'is_admin': False},
            campaigns=[], gm_campaign_ids=[], characters=[],
            active_campaign_id=None, live_campaign_id=None, last_campaign=None,
            trashed_campaigns=[{'id': 'c' * 32, 'name': 'Fallen Saga', 'system': 'pf2e',
                                'members': [{'user_id': 'u1', 'role': 'gm'}]}],
            trash_ttl_days=30)
    assert 'Recently deleted' in html and 'Fallen Saga' in html
    assert '/restore' in html and '/purge' in html and 'purgeConfirm' in html


def test_campaign_manage_describes_restorable_trash_with_configured_ttl():
    import app
    import flask

    cid = 'c' * 32
    campaign_name = "GM's Last Stand"
    with app.app.test_request_context(f'/campaign/{cid}/invites'):
        rendered = flask.render_template(
            'campaign_invites.html',
            campaign={
                'id': cid,
                'name': campaign_name,
                'system': 'pf2e',
                'members': [{'user_id': 'u1', 'role': 'gm'}],
            },
            rows=[],
            members=[],
            gm_total=1,
            open_invites=[],
            trash_ttl_days=C.TRASH_TTL_DAYS,
        )

    lowered = rendered.lower()
    assert 'trash' in lowered
    assert 'restore' in lowered
    assert f'{C.TRASH_TTL_DAYS} days' in rendered
    assert '>Move to trash<' in rendered
    assert 'cannot be undone' not in lowered
    assert 'permanent' not in lowered

    delete_form = re.search(
        rf'<form method="POST" action="/campaign/{cid}/delete"\s+'
        r'onsubmit="([^"]*)"',
        rendered,
    )
    assert delete_form, 'the trash confirmation form must retain an onsubmit guard'
    handler = unescape(delete_form.group(1))
    assert r"GM\u0027s Last Stand" in handler
    assert campaign_name not in handler


def test_campaign_invites_route_passes_the_configured_trash_ttl(tmp_path, monkeypatch):
    import app

    cid = 'c' * 32
    campaign = {
        'id': cid,
        'name': 'Restorable Saga',
        'system': 'pf2e',
        'members': [{'user_id': 'u1', 'role': 'gm'}],
    }
    captured = {}

    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'u1', 'is_admin': False})
    monkeypatch.setattr(app._auth, 'list_active_invites', lambda _cid: [])
    monkeypatch.setattr(app._campaigns, 'get_campaign', lambda _cid: campaign)
    monkeypatch.setattr(app._campaigns, 'is_gm', lambda _campaign, _uid: True)
    monkeypatch.setattr(app._storage, 'party_dir', lambda _cid: str(tmp_path / 'party'))
    monkeypatch.setattr(app._storage, 'cosmere_pc_dir', lambda _cid: str(tmp_path / 'cosmere'))

    def capture_template(name, **context):
        captured['name'] = name
        captured.update(context)
        return 'rendered'

    monkeypatch.setattr(app, 'render_template', capture_template)
    with app.app.test_request_context(f'/campaign/{cid}/invites'):
        response = app.campaign_invites.__wrapped__(cid)

    assert response == 'rendered'
    assert captured['name'] == 'campaign_invites.html'
    assert captured['trash_ttl_days'] == C.TRASH_TTL_DAYS
