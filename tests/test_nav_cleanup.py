"""Shared navigation retirement and bookmark compatibility guards."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import systems
import app


def test_nav_drops_threads_status_notes():
    for key in ('pf2e', 'cosmere'):
        ui = systems.get(key).ui
        gm_labels = {l.label for l in ui.gm_nav}
        player_labels = {l.label for l in ui.player_nav}
        for gone in ('Threads', 'Status', 'Notes'):
            assert gone not in gm_labels, f"{key} gm_nav still lists {gone}"
        assert 'Notes' not in player_labels, f"{key} player_nav still lists Notes"


def test_removed_routes_still_resolve():
    # Unlinked, not deleted — a bookmarked URL must still work.
    c = app.app.test_client()
    for path in ('/status', '/notes', '/gm/threads'):
        assert c.get(path).status_code in (200, 302), f"{path} 404'd"


def test_gm_shared_surfaces_do_not_discover_chronicle(monkeypatch):
    """Removing either shared link should fail this user-visible contract."""
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', '')

    response = app.app.test_client().get('/gm')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    top_nav = html[html.index('<nav'):html.index('</nav>')]
    assert 'href="/chronicle' not in top_nav
    assert '>Chronicle<' not in top_nav
    assert 'href="/chronicle/manage"' not in html


def test_empty_session_journal_uses_neutral_language():
    """An empty character journal must not advertise the retired feature."""
    pc = SimpleNamespace(
        pp=0,
        gp=0,
        sp=0,
        cp=0,
        total_bulk=0,
        max_bulk_limit=10,
        light_bulk_remainder=0,
        is_encumbered=False,
        encumbered_limit=5,
        equipment=[],
        notes='',
        session_notes=[],
    )

    html = app.app.jinja_env.get_template(
        '_pc_sheet/_tab_inventory.html'
    ).render(pc=pc, owner_private=True)

    assert 'No session entries yet.' in html
    assert 'No chronicle yet.' not in html


def _fail_chronicle_read(*_args, **_kwargs):
    raise AssertionError('normal page attempted a Chronicle filesystem read')


def test_gm_hub_renders_without_chronicle_filesystem_reads(monkeypatch):
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', '')
    for reader in (
        '_chronicle_content_dir',
        '_chronicle_doc_pages',
        '_chronicle_docs_index',
    ):
        monkeypatch.setattr(app, reader, _fail_chronicle_read)

    response = app.app.test_client().get('/gm')

    assert response.status_code == 200
    assert 'href="/chronicle' not in response.get_data(as_text=True)


def test_player_notes_renders_without_chronicle_filesystem_reads(monkeypatch):
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', 'sekret')
    for reader in (
        '_chronicle_content_dir',
        '_chronicle_doc_pages',
        '_chronicle_docs_index',
    ):
        monkeypatch.setattr(app, reader, _fail_chronicle_read)
    client = app.app.test_client()
    with client.session_transaction() as browser:
        browser['player_name'] = 'Aria'

    response = client.get('/notes')

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    player_nav = html[html.index('<nav id="player-nav"'):]
    player_nav = player_nav[:player_nav.index('</nav>')]
    assert 'href="/notes"' in player_nav
    assert 'href="/chronicle"' not in player_nav


def test_normal_surfaces_leave_seeded_chronicle_bytes_unchanged(
    tmp_path, monkeypatch
):
    chronicle_root = tmp_path / 'chronicle'
    docs = chronicle_root / 'docs'
    published = chronicle_root / 'content' / ('a' * 64)
    docs.mkdir(parents=True)
    published.mkdir(parents=True)
    sentinels = {
        docs / 'index.json': b'{"schema_version":1,"docs":[]}',
        published / 'manifest.json': b'{"schema_version":1,"pages":[]}',
        published / 'original.bin': b'chronicle-preservation-sentinel\x00\xff',
    }
    for path, payload in sentinels.items():
        path.write_bytes(payload)

    before = {
        path.relative_to(chronicle_root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sentinels
    }
    monkeypatch.setattr(app, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', '')
    assert app.app.test_client().get('/gm').status_code == 200

    monkeypatch.setattr(app, 'GM_PASSWORD', 'sekret')
    player = app.app.test_client()
    with player.session_transaction() as browser:
        browser['player_name'] = 'Aria'
    assert player.get('/notes').status_code == 200

    after = {
        path.relative_to(chronicle_root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sentinels
    }
    assert after == before


def test_chronicle_bookmarks_and_status_api_remain_live(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', '')
    client = app.app.test_client()

    for path in ('/chronicle', '/chronicle/manage', '/chronicle/journal'):
        response = client.get(path)
        assert response.status_code == 200, (
            f'{path} bookmark returned {response.status_code}, expected 200'
        )

    status = client.get('/api/chronicle/status')
    assert status.status_code == 200
    assert status.is_json
