"""Guard the per-deploy asset/version scheme (auto-update without manual refresh).

A Railway deploy must (1) change every static asset's ?v= token so cache-first
clients + the service worker fetch new bytes, (2) change the service worker's
CACHE_NAME so it self-purges old caches, and (3) carry the deploy version on the
SSE 'connected' frame so an already-open tab learns a new build shipped and can
offer a reload. Before this, the ?v= was per-file mtime applied ONLY through
url_for('static'), the SW cache name was a hand-bumped constant, and two assets
were loaded via bare /static/ URLs that the cache-first SW pinned forever (stale
even after Cmd-Shift-R). See the cache-freshness audit (2026-07-13).
"""
from __future__ import annotations

import pathlib
import re

import app as app_module

_REPO = pathlib.Path(app_module.__file__).parent


# --------------------------------------------------------------------------
# The deploy token
# --------------------------------------------------------------------------

def test_deploy_version_nonempty():
    assert app_module.DEPLOY_VERSION, 'DEPLOY_VERSION must be a non-empty token'


def test_deploy_token_stable_within_process():
    # Must NOT change across calls within one deploy, or the "new version" toast
    # false-fires on every worker recycle (why we key on the git SHA, not a clock).
    assert app_module._deploy_token() == app_module._deploy_token()


def test_railway_sha_drives_token(monkeypatch):
    monkeypatch.setenv('RAILWAY_GIT_COMMIT_SHA', 'deadbeefcafef00dba5eba11c0ffee00')
    monkeypatch.delenv('RAILWAY_DEPLOYMENT_ID', raising=False)
    assert app_module._deploy_token() == 'deadbeefcafe'   # first 12 of the SHA


def test_railway_deployment_id_is_fallback(monkeypatch):
    monkeypatch.delenv('RAILWAY_GIT_COMMIT_SHA', raising=False)
    monkeypatch.setenv('RAILWAY_DEPLOYMENT_ID', 'dep_0123456789abcdef')
    assert app_module._deploy_token() == 'dep_01234567'


# --------------------------------------------------------------------------
# Static asset ?v= token
# --------------------------------------------------------------------------

def test_static_url_versioned_in_prod(monkeypatch):
    monkeypatch.setattr(app_module, '_RAILWAY_DEPLOY', True)
    monkeypatch.setattr(app_module, 'DEPLOY_VERSION', 'tok12345')
    from flask import url_for
    with app_module.app.test_request_context():
        u = url_for('static', filename='css/system.css')
    assert 'v=tok12345' in u, u


def test_static_url_versioned_in_dev(monkeypatch):
    # Off Railway (local dev / CI): per-file mtime so an edit auto-busts on save.
    monkeypatch.setattr(app_module, '_RAILWAY_DEPLOY', False)
    from flask import url_for
    with app_module.app.test_request_context():
        u = url_for('static', filename='css/system.css')
    assert re.search(r'[?&]v=\d+', u), u


# --------------------------------------------------------------------------
# Service worker keyed to the deploy token
# --------------------------------------------------------------------------

def test_sw_route_injects_deploy_version():
    r = app_module.app.test_client().get('/sw.js')
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert ('pf2e-gm-' + app_module.DEPLOY_VERSION) in body, \
        'service-worker CACHE_NAME is not keyed to the deploy version'
    assert 'pf2e-gm-v2' not in body, 'the stale hardcoded cache name is still served'
    assert 'no-cache' in (r.headers.get('Cache-Control') or '')


def test_sw_source_has_no_bare_versionless_shell_css():
    sw = (_REPO / 'static' / 'sw.js').read_text()
    m = re.search(r'SHELL_URLS\s*=\s*\[(.*?)\]', sw, re.S)
    assert m, 'SHELL_URLS array not found'
    assert '/static/css/system.css' not in m.group(1), \
        'a bare, unversioned css URL is still precached (the stale-forever trap)'


def test_sw_static_handler_is_version_aware():
    # Versioned URLs (?v=) are immutable within a deploy -> cache-first (no
    # redundant revalidation); a background refresh (only for unversioned URLs)
    # must be tied to the event lifetime so it can't be killed mid-write.
    sw = (_REPO / 'static' / 'sw.js').read_text()
    assert 'url.search' in sw and "'v='" in sw, \
        'static handler no longer distinguishes versioned (immutable) URLs'
    assert 'event.waitUntil' in sw, 'background revalidation is not tied to the event lifetime'


# --------------------------------------------------------------------------
# The two hardcoded /static leaks are closed
# --------------------------------------------------------------------------

def test_no_unversioned_static_in_leaky_templates():
    mc = (_REPO / 'templates' / 'mobile_combat.html').read_text()
    ps = (_REPO / 'templates' / 'player_sheet.html').read_text()
    assert 'href="/static/css/system.css"' not in mc, 'mobile_combat still hardcodes system.css'
    assert 'src="/static/js/dice-engine.js"' not in ps, 'player_sheet still hardcodes dice-engine.js'
    assert "url_for('static', filename='css/system.css')" in mc
    assert "url_for('static', filename='js/dice-engine.js')" in ps


# --------------------------------------------------------------------------
# SSE version signal (Layer 3)
# --------------------------------------------------------------------------

def test_connected_frame_carries_version():
    src = (_REPO / 'app.py').read_text()
    assert 'json.dumps({"v": DEPLOY_VERSION})' in src, \
        'the SSE connected frame does not embed DEPLOY_VERSION'


def test_context_processor_injects_app_version():
    with app_module.app.test_request_context('/'):
        ctx = app_module._inject_account_ctx()
    assert ctx.get('app_version') == app_module.DEPLOY_VERSION


def test_hub_compares_version_and_can_reload():
    hub = (_REPO / 'templates' / '_sse_hub.html').read_text()
    assert 'app_version' in hub, 'hub does not stamp the server-provided app_version'
    assert 'LOADED_VERSION' in hub
    assert 'location.reload' in hub, 'no reload path on a version mismatch'
