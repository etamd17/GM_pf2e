"""Regression: behind Railway's TLS-terminating proxy, a same-site POST carries
an `https://` Origin while the app sees the proxy->app hop as http. The CSRF
guard must compare HOSTS (not full URLs) so the legit POST isn't blocked, while a
true cross-site POST still is. This is the bug that blocked admin /setup in prod.

These exercise the guard via /login (same `_CSRF_GUARD_PREFIXES`, but no state
mutation, so the shared test DATA_DIR stays clean -- /setup/setup-token share the
exact same before_request guard).
"""
from __future__ import annotations

import app as A


def test_proxyfix_is_applied():
    # X-Forwarded-Proto/Host/For are trusted (one hop) so request.scheme/host
    # reflect the real HTTPS origin -- otherwise request.host_url is http://...
    assert A.app.wsgi_app.__class__.__name__ == 'ProxyFix'


def test_https_origin_same_host_is_allowed():
    c = A.app.test_client()
    # Browser sends an https Origin; the proxy forwards as http. Host matches, so
    # the guard must NOT 400 this -- that mismatch was the prod /setup breakage.
    r = c.post('/login', data={'username': 'x', 'password': 'y'},
               headers={'Origin': 'https://localhost', 'X-Forwarded-Proto': 'https'})
    assert r.status_code != 400
    assert b'Cross-origin request blocked' not in r.data


def test_cross_site_origin_is_blocked():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'},
               headers={'Origin': 'https://evil.example'})
    assert r.status_code == 400 and b'Cross-origin request blocked' in r.data


def test_no_origin_is_allowed():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'})
    assert b'Cross-origin request blocked' not in r.data
