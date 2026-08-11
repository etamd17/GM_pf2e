"""The Obsidian plugin and the server must agree about the wire contract.

Why this file exists: the plugin lived only in the GM's vault for its whole
life, so nothing tied it to the server it talks to. It drifted -- the plugin was
written against a v2 contract while only v1 was on trunk, and every roll button,
room control and reveal in the pane answered `400 unsupported command type` with
no test anywhere going red. Vendoring the plugin into the repo (tools/obsidian-
plugin/) is only half the fix; this is the other half.

These are source-parity checks, not behaviour tests. They read both sides as
text because the client is JavaScript and the server is Python, and a mismatch
between them cannot fail any single-language test. Keep them string-based and
cheap; the behavioural coverage lives in tests/test_obsidian_sync.py and
tests/test_obsidian_operations_v2.py.
"""
from __future__ import annotations

import json
import os
import re

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGIN_DIR = os.path.join(_REPO, 'tools', 'obsidian-plugin')
_MAIN_JS = os.path.join(_PLUGIN_DIR, 'main.js')
_SERVICE = os.path.join(_REPO, 'services', 'obsidian_sync.py')

# Sent through the dedicated /sessions/* endpoints rather than /commands, so the
# server sets `type` itself and the client never names them in a sendCommand
# payload. They still have to exist as dispatch arms.
_LIFECYCLE_TYPES = {'start_session', 'end_session'}


def _read(path):
    # encoding='utf-8' is mandatory: the suite runs on Windows too, where a bare
    # open() uses cp1252 and both files contain non-ASCII.
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _client_command_types(src):
    """Every command type the plugin can put on the wire.

    Matches both call shapes -- the one-liner `sendCommand('adjust_hp', {...})`
    and the wrapped form used by the session lifecycle, where the type sits on
    its own line after the paren.
    """
    return set(re.findall(r"sendCommand\(\s*'([a-z_]+)'", src))


def _server_command_types(src):
    return set(re.findall(r'kind == "([a-z_]+)"', src))


def _js_string_list(src, name):
    """Pull a flat JS array of single-quoted strings by variable name."""
    match = re.search(name + r'\s*=\s*\[(.*?)\]', src, re.DOTALL)
    assert match, f'{name} not found in the vendored plugin'
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def _py_string_set(src, name):
    match = re.search(name + r'\s*=\s*\{(.*?)\}', src, re.DOTALL)
    assert match, f'{name} not found in services/obsidian_sync.py'
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_plugin_source_is_vendored():
    """The repo, not the vault, is the source of truth for the plugin."""
    for name in ('main.js', 'manifest.json', 'styles.css'):
        assert os.path.isfile(os.path.join(_PLUGIN_DIR, name)), \
            f'tools/obsidian-plugin/{name} is missing -- the plugin is unversioned again'
    manifest = json.loads(_read(os.path.join(_PLUGIN_DIR, 'manifest.json')))
    assert manifest['id'] == 'session-operations-sync'


def test_no_credentials_are_vendored():
    """data.json holds a live bearer token. It belongs in the vault only.

    Obsidian writes it next to main.js, so copying "the plugin folder" into the
    repo is the natural mistake, and it would publish a working production
    credential to a public GitHub repo.
    """
    leaked = os.path.join(_PLUGIN_DIR, 'data.json')
    assert not os.path.exists(leaked), \
        'tools/obsidian-plugin/data.json must never be committed -- it holds the live token'
    for name in os.listdir(_PLUGIN_DIR):
        body = _read(os.path.join(_PLUGIN_DIR, name))
        # Match the minted shape (core/obsidian_sync.py:78), not the bare prefix:
        # main.js legitimately carries 'obs1_...' as the settings-field placeholder.
        found = re.search(r'obs1_[0-9a-f]{12}_[A-Za-z0-9_-]{20,}', body)
        assert not found, f'{name} looks like it contains a real integration token'


def test_every_client_command_type_has_a_server_dispatch_arm():
    """The drift that motivated this file: client ahead of server."""
    client = _client_command_types(_read(_MAIN_JS))
    server = _server_command_types(_read(_SERVICE))
    assert client, 'no sendCommand call sites found -- did the client refactor?'
    orphans = sorted(client - server)
    assert not orphans, (
        'the plugin sends command types the server will reject with 400 '
        f'"unsupported command type": {orphans}'
    )


def test_session_lifecycle_types_exist_server_side():
    server = _server_command_types(_read(_SERVICE))
    missing = sorted(_LIFECYCLE_TYPES - server)
    assert not missing, f'/sessions/* endpoints dispatch types the server does not handle: {missing}'


@pytest.mark.parametrize('name', ['NUMERIC_CONDITIONS', 'BOOLEAN_CONDITIONS'])
def test_condition_vocabularies_match(name):
    """A condition on one side only is a button that 400s, or a condition the
    GM can set from the website but never clear from Obsidian."""
    client = _js_string_list(_read(_MAIN_JS), name)
    server = _py_string_set(_read(_SERVICE), name)
    assert client == server, (
        f'{name} disagree -- client only: {sorted(client - server)}, '
        f'server only: {sorted(server - client)}'
    )


def test_api_prefix_matches():
    client = re.search(r"API_PREFIX\s*=\s*'([^']+)'", _read(_MAIN_JS))
    server = re.search(r'API_PREFIX\s*=\s*"([^"]+)"', _read(_SERVICE))
    assert client and server, 'API_PREFIX not found on one side'
    assert client.group(1) == server.group(1), (
        f'API prefix drift: client {client.group(1)!r} vs server {server.group(1)!r}'
    )
