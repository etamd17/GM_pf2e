"""Security hardening: with open registration, untrusted users must not be able
to read backend source or server file paths.

- A 500 on an /api route returns a generic message (the raw exception text, which
  carries absolute server paths, is logged server-side, never sent to the client).
- Intentional HTTP errors keep their developer-set message.
- Source files aren't reachable and path traversal on file-serving routes 404s.
"""
from __future__ import annotations

import json

from werkzeug.exceptions import Forbidden

import app as A


def test_api_500_does_not_leak_server_paths():
    leaky = FileNotFoundError("[Errno 2] No such file or directory: '/Users/evananderson/GM_pf2e/users.json'")
    with A.app.test_request_context('/api/anything'):
        resp, code = A.handle_uncaught(leaky)
    assert code == 500
    body = resp.get_json()
    assert body['error'] == 'Internal server error'
    assert '/Users/' not in json.dumps(body) and 'users.json' not in json.dumps(body)


def test_api_httpexception_keeps_intended_message():
    with A.app.test_request_context('/api/x'):
        resp, code = A.handle_uncaught(Forbidden('GM access required'))
    assert code == 403 and 'GM access required' in resp.get_json()['error']


def test_source_files_not_served():
    c = A.app.test_client()
    for path in ('/app.py', '/core/auth.py', '/.env', '/users.json'):
        r = c.get(path)
        assert r.status_code == 404, path
        assert b'Flask(' not in r.data and b'password_hash' not in r.data


def test_traversal_on_file_routes_is_blocked():
    c = A.app.test_client()
    # slash-rejecting <filename> + send_from_directory safe_join -> never source
    for path in ('/handouts/..%2f..%2fapp.py', '/portraits/..%2f..%2fcore%2fauth.py',
                 '/campaign_audio/..%2f..%2fapp.py'):
        r = c.get(path)
        assert r.status_code in (400, 404), (path, r.status_code)
        assert b'Flask(' not in r.data
