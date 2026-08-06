"""The Flask secret key must survive restarts so sessions don't drop on deploy.

A random key per process boot re-signed the session cookie on every restart,
logging out every player + GM the moment a Railway deploy landed mid-session
(and dropping them back to a stale campaign). The key is now persisted on the
data volume (or taken from the SECRET_KEY env), so it's stable across restarts.
"""
import os
import sys
import subprocess
import tempfile


def _boot_key(data_dir, env_key=None):
    env = dict(os.environ, DATA_DIR=data_dir, GM_PASSWORD='')
    env.pop('SECRET_KEY', None)
    if env_key is not None:
        env['SECRET_KEY'] = env_key
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, '-c', 'import app; print("KEY:" + app.app.secret_key)'],
        capture_output=True, text=True, cwd=repo, env=env)
    line = next((l for l in r.stdout.splitlines() if l.startswith('KEY:')), None)
    assert line, "no key emitted:\n%s\n%s" % (r.stdout, r.stderr)
    return line[4:]


def test_persisted_key_is_stable_across_restarts():
    dd = tempfile.mkdtemp()
    k1 = _boot_key(dd)
    k2 = _boot_key(dd)               # simulate a restart with the same volume
    assert k1 and k1 == k2, ("key changed across restarts", k1, k2)
    assert os.path.isfile(os.path.join(dd, '.secret_key'))


def test_distinct_volumes_get_distinct_keys():
    assert _boot_key(tempfile.mkdtemp()) != _boot_key(tempfile.mkdtemp())


def test_env_var_wins():
    dd = tempfile.mkdtemp()
    assert _boot_key(dd, env_key='explicit-key-123') == 'explicit-key-123'
    # env key shouldn't have written a keyfile
    assert not os.path.isfile(os.path.join(dd, '.secret_key'))
