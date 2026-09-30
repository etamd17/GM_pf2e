"""Request-origin regression coverage for the explicit proxy trust model."""
from __future__ import annotations

import re

import app as A


def test_proxyfix_is_applied():
    # The middleware remains present, but local development trusts zero hops.
    assert A.app.wsgi_app.__class__.__name__ == 'ProxyFix'


def test_https_origin_same_host_is_allowed():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'},
               base_url='https://localhost',
               headers={'Origin': 'https://localhost'})
    assert r.status_code != 400
    assert b'Cross-origin request blocked' not in r.data


def test_untrusted_forwarded_proto_is_ignored_locally():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'},
               headers={'Origin': 'https://localhost', 'X-Forwarded-Proto': 'https'})
    assert r.status_code == 400


def test_cross_site_origin_is_blocked():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'},
               headers={'Origin': 'https://evil.example'})
    assert r.status_code == 400 and b'Cross-origin request blocked' in r.data


def test_no_origin_is_allowed():
    c = A.app.test_client()
    r = c.post('/login', data={'username': 'x', 'password': 'y'})
    assert b'Cross-origin request blocked' not in r.data


def test_identity_and_ip_limit_keys_use_distinct_dimensions():
    def key(remote, action, identity):
        with A.app.test_request_context(environ_base={'REMOTE_ADDR': remote}):
            return A._auth_rate_key(action, identity)

    assert key('192.0.2.1', 'login_identity', 'Alice') == key(
        '198.51.100.2', 'login_identity', 'alice'
    )
    assert key('192.0.2.1', 'login_ip', 'Alice') == key(
        '192.0.2.1', 'login_ip', 'Bob'
    )
    assert key('192.0.2.1', 'login_ip', 'Alice') != key(
        '198.51.100.2', 'login_ip', 'Alice'
    )


def test_join_rate_limit_runs_before_invite_lookup(monkeypatch):
    monkeypatch.setattr(A, '_consume_auth_limit', lambda name, identity='': ('limited', 429))

    def unexpected_lookup(_code):
        raise AssertionError('invite storage should not be read after rate limit denial')

    monkeypatch.setattr(A._auth, 'get_invite', unexpected_lookup)
    response = A.app.test_client().post('/join', data={'code': 'rotated-guess'})
    assert response.status_code == 429


def test_oversized_login_body_is_rejected_before_credential_verification(monkeypatch):
    calls = []
    monkeypatch.setattr(A._auth, 'account_mode_initialized', lambda: True)
    monkeypatch.setattr(
        A._auth,
        'verify_credentials',
        lambda *_args: calls.append('verify'),
    )

    response = A.app.test_client().post(
        '/login',
        data=b'x' * (A._AUTH_FORM_MAX_BYTES + 1),
        content_type='application/x-www-form-urlencoded',
    )

    assert response.status_code == 413
    assert calls == []


def test_legacy_gm_login_form_carries_a_csrf_token(monkeypatch):
    monkeypatch.setattr(A, '_account_mode', lambda: False)

    response = A.app.test_client().get('/gm/login')

    assert response.status_code == 200
    assert re.search(
        rb'name="_csrf" value="[^"]+"',
        response.data,
    )
