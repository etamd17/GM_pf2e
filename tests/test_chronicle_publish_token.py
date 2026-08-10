"""The publish token should not have to travel in argv.

`--token` puts the secret in `ps` output for the duration of the publish and
in shell history forever after. That token unlocks /api/chronicle*, which is
the route that REPLACES the whole player-facing Chronicle -- so it is worth
being able to keep out of the command line.

resolve_token falls back to $CHRONICLE_PUBLISH_TOKEN, deliberately the same
variable name app.py's _chronicle_token_ok reads, so the GM exports one value
and both ends agree. The name being shared is asserted here against app.py's
actual source, because two independently-maintained spellings would fail as a
silent 403 with nothing to point at.
"""
from __future__ import annotations

import io
import pathlib

import pytest

from tools.chronicle_build import TOKEN_ENV_VAR, resolve_token


_APP_PY = pathlib.Path(__file__).parent.parent / 'app.py'


def test_cli_token_wins():
    """Existing scripted invocations must behave exactly as before."""
    assert resolve_token('from-cli', env={TOKEN_ENV_VAR: 'from-env'}) == 'from-cli'


def test_env_is_used_when_no_flag_is_passed():
    assert resolve_token(None, env={TOKEN_ENV_VAR: 'from-env'}) == 'from-env'


def test_neither_source_means_no_token():
    """Local legacy-open dev needs no token, and publish() then omits the
    header entirely rather than sending an empty one that could only 403."""
    assert resolve_token(None, env={}) is None


def test_blank_values_are_treated_as_unset():
    assert resolve_token('   ', env={TOKEN_ENV_VAR: 'from-env'}) == 'from-env'
    assert resolve_token(None, env={TOKEN_ENV_VAR: '  '}) is None
    assert resolve_token('', env={}) is None


def test_surrounding_whitespace_is_stripped():
    """`export TOK=$(cat secret)` picks up a trailing newline. Sent verbatim it
    fails hmac.compare_digest server-side and reads as an inexplicable 403."""
    assert resolve_token(None, env={TOKEN_ENV_VAR: 'secret\n'}) == 'secret'
    assert resolve_token(' secret ', env={}) == 'secret'


def test_it_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV_VAR, 'live-value')
    assert resolve_token(None) == 'live-value'
    monkeypatch.delenv(TOKEN_ENV_VAR)
    assert resolve_token(None) is None


def test_the_variable_name_matches_the_server():
    """A drift between these two spellings is invisible until a publish 403s."""
    source = io.open(_APP_PY, encoding='utf-8').read()
    assert "os.environ.get('CHRONICLE_PUBLISH_TOKEN'" in source
    assert TOKEN_ENV_VAR == 'CHRONICLE_PUBLISH_TOKEN'


def test_main_routes_the_token_through_the_resolver(monkeypatch, tmp_path):
    """Guards the wiring, not just the helper: main() must consult the env,
    otherwise resolve_token is dead code and the flag stays mandatory."""
    import tools.chronicle_build as cb

    seen = {}
    monkeypatch.setattr(cb, 'build_player_vault',
                        lambda *a, **k: {'review_summary': 'ok', 'leaks': []})
    monkeypatch.setattr(cb, 'leak_check', lambda *a, **k: [])
    monkeypatch.setattr(cb, 'make_zip', lambda out: str(tmp_path / 'x.zip'))
    monkeypatch.setattr(cb, 'publish',
                        lambda z, u, token=None: seen.update(token=token) or (True, '{}'))
    monkeypatch.setenv(TOKEN_ENV_VAR, 'env-secret')

    rc = cb.main(['--vault', str(tmp_path), '--out', str(tmp_path),
                  '--campaign-id', 'cid',
                  '--publish-url', 'https://example.invalid/api/chronicle/publish'])
    assert rc == 0
    assert seen['token'] == 'env-secret'
