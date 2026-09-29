"""Fail-closed regressions for the account-mode initialization boundary.

The application binds ``DATA_DIR`` at import time, so each scenario runs in a
fresh subprocess.  This exercises the real earliest ``before_request`` guard
without mutating the repository's local account or campaign stores.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


_REPO = Path(__file__).resolve().parent.parent
_RESULT_PREFIX = "ACCOUNT_STORE_RESULT="


@pytest.fixture
def isolated_account_process(tmp_path):
    counter = 0

    def run(
        body: str,
        *,
        prelude: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal counter
        counter += 1
        data_dir = tmp_path / f"account-store-{counter}"
        data_dir.mkdir()
        environment = dict(os.environ)
        environment.update(
            DATA_DIR=str(data_dir),
            GM_PASSWORD="legacy-gm-password",
            SETUP_TOKEN="setup-token",
            SECRET_KEY="account-store-test-only",
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONHASHSEED="0",
            PYTHONUTF8="1",
        )
        if extra_env:
            environment.update(extra_env)
        script = (
            "import json, os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            + textwrap.dedent(prelude)
            + "\nimport app as application\n"
            + "from core import auth, storage\n"
            + "application.app.config.update(TESTING=True)\n"
            + textwrap.dedent(body)
        )
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=_REPO,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

    return run


def _result(completed: subprocess.CompletedProcess[str]):
    assert completed.returncode == 0, (
        "isolated account-store scenario failed\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            return json.loads(line[len(_RESULT_PREFIX):])
    pytest.fail(
        "isolated account-store scenario emitted no result marker\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )


def test_pristine_boot_remains_bootstrappable_and_records_initialization(
    isolated_account_process,
):
    result = _result(isolated_account_process(r'''
        client = application.app.test_client()
        before = {
            "state": auth.account_store_state(),
            "setup": client.get("/setup").status_code,
            "login": client.get("/login").status_code,
            "login_location": client.get("/login").headers.get("Location"),
            "marker": os.path.exists(auth._account_mode_marker_path()),
        }
        created = client.post("/setup", data={
            "username": "gm",
            "password": "secret1",
            "display_name": "GM",
            "setup_token": "setup-token",
        })
        after = {
            "status": created.status_code,
            "state": auth.account_store_state(),
            "marker": os.path.exists(auth._account_mode_marker_path()),
            "setup": client.get("/setup").status_code,
            "setup_location": client.get("/setup").headers.get("Location"),
        }
        print("ACCOUNT_STORE_RESULT=" + json.dumps({"before": before, "after": after}))
    '''))

    assert result["before"] == {
        "state": "uninitialized",
        "setup": 200,
        "login": 302,
        "login_location": "/setup",
        "marker": False,
    }
    assert result["after"] == {
        "status": 302,
        "state": "ready",
        "marker": True,
        "setup": 302,
        "setup_location": "/login",
    }


def test_valid_pre_marker_users_store_is_upgraded_without_reopening_setup(
    isolated_account_process,
):
    result = _result(isolated_account_process(
        r'''
            client = application.app.test_client()
            login = client.get("/login")
            setup = client.get("/setup")
            print("ACCOUNT_STORE_RESULT=" + json.dumps({
                "state": auth.account_store_state(),
                "login": login.status_code,
                "setup": setup.status_code,
                "setup_location": setup.headers.get("Location"),
                "marker": os.path.exists(auth._account_mode_marker_path()),
            }))
        ''',
        prelude=r'''
            import json, os
            uid = "a" * 32
            with open(os.path.join(os.environ["DATA_DIR"], "users.json"), "w", encoding="utf-8") as f:
                json.dump({"users": {uid: {
                    "id": uid,
                    "username": "gm",
                    "password_hash": "pbkdf2:sha256:1$placeholder$hash",
                    "is_admin": True,
                }}}, f)
        ''',
    ))

    assert result == {
        "state": "ready",
        "login": 200,
        "setup": 302,
        "setup_location": "/login",
        "marker": True,
    }


def test_missing_initialized_users_store_returns_503_until_restored(
    isolated_account_process,
):
    result = _result(isolated_account_process(r'''
        client = application.app.test_client()
        created = client.post("/setup", data={
            "username": "gm",
            "password": "secret1",
            "setup_token": "setup-token",
        })
        assert created.status_code == 302
        users_path = storage.USERS_FILE
        original = open(users_path, "rb").read()
        os.remove(users_path)

        setup_get = client.get("/setup")
        setup_post = client.post("/setup", data={
            "username": "takeover",
            "password": "secret2",
            "setup_token": "setup-token",
        })
        login_get = client.get("/login")
        login_post = client.post("/login", data={
            "username": "gm",
            "password": "secret1",
        })
        register_get = client.get("/register")
        join = client.post("/api/join_campaign", json={"name": "Any PC"})
        health = client.get("/health")
        with client.session_transaction() as session:
            selected = session.get("player_name")
        users_missing_after_takeover_attempt = not os.path.exists(users_path)

        with open(users_path, "wb") as f:
            f.write(original)
        recovered_login = client.get("/login")
        recovered_setup = client.get("/setup")

        print("ACCOUNT_STORE_RESULT=" + json.dumps({
            "setup_get": setup_get.status_code,
            "setup_post": setup_post.status_code,
            "login_get": login_get.status_code,
            "login_post": login_post.status_code,
            "register_get": register_get.status_code,
            "join": join.status_code,
            "join_body": join.get_json(),
            "health": health.status_code,
            "health_body": health.get_json(),
            "retry_after": health.headers.get("Retry-After"),
            "selected": selected,
            "users_missing_after_takeover_attempt": users_missing_after_takeover_attempt,
            "recovered_login": recovered_login.status_code,
            "recovered_setup": recovered_setup.status_code,
            "recovered_setup_location": recovered_setup.headers.get("Location"),
        }))
    '''))

    assert result == {
        "setup_get": 503,
        "setup_post": 503,
        "login_get": 503,
        "login_post": 503,
        "register_get": 503,
        "join": 503,
        "join_body": {
            "error": "account_store_unavailable",
            "message": "Account data is temporarily unavailable.",
        },
        "health": 503,
        "health_body": {
            "status": "unhealthy",
            "account_store": "unavailable",
        },
        "retry_after": "60",
        "selected": None,
        "users_missing_after_takeover_attempt": True,
        "recovered_login": 200,
        "recovered_setup": 302,
        "recovered_setup_location": "/login",
    }


@pytest.mark.parametrize(
    "replacement",
    [
        "{not json",
        "{}",
        '{"users": []}',
        '{"users": {}}',
    ],
)
def test_initialized_corrupt_or_empty_users_store_never_reopens_setup(
    isolated_account_process,
    replacement,
):
    result = _result(isolated_account_process(
        r'''
            client = application.app.test_client()
            assert client.post("/setup", data={
                "username": "gm",
                "password": "secret1",
                "setup_token": "setup-token",
            }).status_code == 302
            with open(storage.USERS_FILE, "w", encoding="utf-8") as f:
                f.write(os.environ["BROKEN_USERS_DOCUMENT"])
            setup = client.get("/setup")
            login = client.get("/login")
            health = client.get("/health")
            print("ACCOUNT_STORE_RESULT=" + json.dumps({
                "setup": setup.status_code,
                "login": login.status_code,
                "health": health.status_code,
                "state": auth.account_store_state(),
            }))
        ''',
        extra_env={"BROKEN_USERS_DOCUMENT": replacement},
    ))

    assert result == {
        "setup": 503,
        "login": 503,
        "health": 503,
        "state": "unavailable",
    }


def test_unmarked_corrupt_users_or_existing_campaign_evidence_fail_closed(
    isolated_account_process,
):
    corrupt = _result(isolated_account_process(
        r'''
            client = application.app.test_client()
            print("ACCOUNT_STORE_RESULT=" + json.dumps({
                "setup": client.get("/setup").status_code,
                "login": client.get("/login").status_code,
                "marker": os.path.exists(auth._account_mode_marker_path()),
            }))
        ''',
        prelude=r'''
            import os
            with open(os.path.join(os.environ["DATA_DIR"], "users.json"), "w", encoding="utf-8") as f:
                f.write("{broken")
        ''',
    ))
    campaign = _result(isolated_account_process(
        r'''
            client = application.app.test_client()
            print("ACCOUNT_STORE_RESULT=" + json.dumps({
                "setup": client.get("/setup").status_code,
                "login": client.get("/login").status_code,
                "marker": os.path.exists(auth._account_mode_marker_path()),
            }))
        ''',
        prelude=r'''
            import json, os
            cid = "b" * 32
            root = os.path.join(os.environ["DATA_DIR"], "campaigns", cid)
            os.makedirs(root)
            with open(os.path.join(root, "campaign.json"), "w", encoding="utf-8") as f:
                json.dump({"id": cid, "members": []}, f)
        ''',
    ))

    expected = {"setup": 503, "login": 503, "marker": False}
    assert corrupt == expected
    assert campaign == expected


def test_marker_contents_are_not_a_fail_open_input(isolated_account_process):
    result = _result(isolated_account_process(
        r'''
            client = application.app.test_client()
            print("ACCOUNT_STORE_RESULT=" + json.dumps({
                "state": auth.account_store_state(),
                "login": client.get("/login").status_code,
                "setup": client.get("/setup").status_code,
            }))
        ''',
        prelude=r'''
            import json, os
            uid = "c" * 32
            with open(os.path.join(os.environ["DATA_DIR"], "users.json"), "w", encoding="utf-8") as f:
                json.dump({"users": {uid: {
                    "id": uid,
                    "username": "gm",
                    "password_hash": "hash",
                    "is_admin": True,
                }}}, f)
            with open(os.path.join(os.environ["DATA_DIR"], ".account-mode-initialized"), "w", encoding="utf-8") as f:
                f.write("not json and intentionally ignored")
        ''',
    ))

    assert result == {"state": "ready", "login": 200, "setup": 302}


def test_concurrent_setup_requests_create_exactly_one_site_admin(
    isolated_account_process,
):
    result = _result(isolated_account_process(r'''
        import threading
        from concurrent.futures import ThreadPoolExecutor

        original_bootstrap = auth.create_first_admin
        both_requests_passed_early_check = threading.Barrier(2)

        def synchronized_bootstrap(*args, **kwargs):
            both_requests_passed_early_check.wait(timeout=10)
            return original_bootstrap(*args, **kwargs)

        auth.create_first_admin = synchronized_bootstrap

        def submit(username):
            client = application.app.test_client()
            response = client.post('/setup', data={
                'username': username,
                'password': 'secret1',
                'display_name': username,
                'setup_token': 'setup-token',
            })
            return {
                'status': response.status_code,
                'location': response.headers.get('Location'),
            }

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(submit, 'first-admin')
            second = pool.submit(submit, 'second-admin')
            responses = sorted(
                (first.result(timeout=30), second.result(timeout=30)),
                key=lambda item: item['location'] or '',
            )

        with open(storage.USERS_FILE, encoding='utf-8') as handle:
            users = list(json.load(handle)['users'].values())
        print('ACCOUNT_STORE_RESULT=' + json.dumps({
            'responses': responses,
            'user_count': len(users),
            'admin_count': sum(bool(user.get('is_admin')) for user in users),
            'username': users[0]['username'],
            'marker': os.path.exists(auth._account_mode_marker_path()),
            'state': auth.account_store_state(),
        }, sort_keys=True))
    '''))

    assert result['responses'] == [
        {'status': 302, 'location': '/login'},
        {'status': 302, 'location': '/me'},
    ]
    assert result['user_count'] == 1
    assert result['admin_count'] == 1
    assert result['username'] in {'first-admin', 'second-admin'}
    assert result['marker'] is True
    assert result['state'] == 'ready'


def test_concurrent_registrations_preserve_both_users(isolated_account_process):
    result = _result(isolated_account_process(r'''
        import threading

        # Password derivation is unrelated to this interleaving and would make
        # the scheduling assertion timing-dependent, so use a valid non-empty
        # test value while exercising the real create/read/save transaction.
        auth.generate_password_hash = (
            lambda password, method=None: "test-password-hash:" + password
        )

        original_atomic_write = storage.atomic_write_json
        first_write_entered = threading.Event()
        release_first_write = threading.Event()
        write_counter_lock = threading.Lock()
        users_write_count = [0]

        def controlled_atomic_write(path, obj, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) == os.path.abspath(storage.USERS_FILE):
                with write_counter_lock:
                    write_number = users_write_count[0]
                    users_write_count[0] += 1
                if write_number == 0:
                    first_write_entered.set()
                    if not release_first_write.wait(10):
                        raise RuntimeError("timed out waiting to release first users write")
            return original_atomic_write(path, obj, *args, **kwargs)

        storage.atomic_write_json = controlled_atomic_write
        errors = []
        errors_lock = threading.Lock()
        first_done = threading.Event()
        second_done = threading.Event()

        def register(username, done):
            try:
                auth.create_user(username, "secret1")
            except BaseException as exc:
                with errors_lock:
                    errors.append(type(exc).__name__ + ": " + str(exc))
            finally:
                done.set()

        first = threading.Thread(target=register, args=("alice", first_done))
        second = threading.Thread(target=register, args=("bob", second_done))
        first.start()
        assert first_write_entered.wait(10), "first registration never reached save"
        second.start()
        second_finished_before_release = second_done.wait(0.5)
        release_first_write.set()
        first.join(10)
        second.join(10)

        with open(storage.USERS_FILE, encoding="utf-8") as f:
            users = json.load(f)["users"]
        print("ACCOUNT_STORE_RESULT=" + json.dumps({
            "errors": errors,
            "second_finished_before_release": second_finished_before_release,
            "threads_alive": [first.is_alive(), second.is_alive()],
            "usernames": sorted(user["username"] for user in users.values()),
            "users_write_count": users_write_count[0],
            "marker": os.path.exists(auth._account_mode_marker_path()),
        }))
    '''))

    assert result == {
        "errors": [],
        "second_finished_before_release": False,
        "threads_alive": [False, False],
        "usernames": ["alice", "bob"],
        "users_write_count": 2,
        "marker": True,
    }


def test_concurrent_password_login_and_campaign_updates_do_not_clobber(
    isolated_account_process,
):
    result = _result(isolated_account_process(r'''
        import threading

        user = auth.create_user("gm", "oldsecret")
        user_id = user["id"]
        new_hash = auth.generate_password_hash("newsecret", method=auth._PW_METHOD)
        auth.generate_password_hash = lambda password, method=None: new_hash

        original_atomic_write = storage.atomic_write_json
        first_write_entered = threading.Event()
        release_first_write = threading.Event()
        write_counter_lock = threading.Lock()
        users_write_count = [0]

        def controlled_atomic_write(path, obj, *args, **kwargs):
            if os.path.abspath(os.fspath(path)) == os.path.abspath(storage.USERS_FILE):
                with write_counter_lock:
                    write_number = users_write_count[0]
                    users_write_count[0] += 1
                if write_number == 0:
                    first_write_entered.set()
                    if not release_first_write.wait(10):
                        raise RuntimeError("timed out waiting to release password write")
            return original_atomic_write(path, obj, *args, **kwargs)

        storage.atomic_write_json = controlled_atomic_write
        errors = []
        errors_lock = threading.Lock()
        password_done = threading.Event()
        activity_done = threading.Event()

        def change_password():
            try:
                auth.set_password(user_id, "newsecret")
            except BaseException as exc:
                with errors_lock:
                    errors.append(type(exc).__name__ + ": " + str(exc))
            finally:
                password_done.set()

        def record_account_activity():
            try:
                auth._touch_login(user_id)
                auth.set_last_campaign(user_id, "campaign-after-login")
            except BaseException as exc:
                with errors_lock:
                    errors.append(type(exc).__name__ + ": " + str(exc))
            finally:
                activity_done.set()

        password_thread = threading.Thread(target=change_password)
        activity_thread = threading.Thread(target=record_account_activity)
        password_thread.start()
        assert first_write_entered.wait(10), "password update never reached save"
        activity_thread.start()
        activity_finished_before_release = activity_done.wait(0.5)
        release_first_write.set()
        password_thread.join(10)
        activity_thread.join(10)

        with open(storage.USERS_FILE, encoding="utf-8") as f:
            persisted = json.load(f)["users"][user_id]
        print("ACCOUNT_STORE_RESULT=" + json.dumps({
            "errors": errors,
            "activity_finished_before_release": activity_finished_before_release,
            "threads_alive": [password_thread.is_alive(), activity_thread.is_alive()],
            "new_password_valid": auth.check_password_hash(
                persisted["password_hash"], "newsecret"
            ),
            "old_password_valid": auth.check_password_hash(
                persisted["password_hash"], "oldsecret"
            ),
            "last_login_recorded": bool(persisted.get("last_login")),
            "last_campaign_id": persisted.get("last_campaign_id"),
            "users_write_count": users_write_count[0],
        }))
    '''))

    assert result == {
        "errors": [],
        "activity_finished_before_release": False,
        "threads_alive": [False, False],
        "new_password_valid": True,
        "old_password_valid": False,
        "last_login_recorded": True,
        "last_campaign_id": "campaign-after-login",
        "users_write_count": 3,
    }
