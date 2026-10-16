"""The /vtt namespace is wired only behind VTT_ENABLED=1."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask

from vtt.transport import app_wiring

ROOT = Path(__file__).resolve().parents[3]


def test_with_the_flag_off_the_real_app_imports_and_builds_nothing_vtt(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "VTT_ENABLED"}
    env.update(DATA_DIR=str(tmp_path), GM_PASSWORD="")
    code = ("import json, sys, app; print(json.dumps({'service': app.VTT_SERVICE is None, "
            "'socketio': 'flask_socketio' in sys.modules, 'transport': 'vtt.transport.socket' in sys.modules}))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout.strip().splitlines()[-1]
    assert json.loads(out) == {"service": True, "socketio": False, "transport": False}


def test_with_the_flag_on_it_needs_a_database_and_registers_the_namespace(tmp_path):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "wiring-secret"
    with pytest.raises(RuntimeError):
        app_wiring.init_from_app(app, environ={"VTT_ENABLED": "1"})          # no DATABASE_URL
    url = f"sqlite+pysqlite:///{(tmp_path / 'w.sqlite').as_posix()}"
    app2 = Flask(__name__)
    app2.config["SECRET_KEY"] = "wiring-secret"
    service = app_wiring.init_from_app(app2, environ={"VTT_ENABLED": "1", "DATABASE_URL": url})
    assert service is not None
    assert "/vtt" in service.socketio.server.handlers
    assert app_wiring.init_from_app(Flask(__name__), environ={}) is None


def test_app_host_csrf_matches_only_the_session_token():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "s"
    host = app_wiring.AppHost()
    with app.test_request_context("/"):
        from flask import session
        session["_csrf"] = "abc123abc123"
        assert host.csrf_valid("abc123abc123")
        assert not host.csrf_valid("abc123abc12X")
        assert not host.csrf_valid(None)
        session.pop("_csrf")
        assert not host.csrf_valid("")


def test_with_the_flag_on_the_real_app_classifies_and_reaches_the_pairing_routes(tmp_path):
    # M3: the app's own guards run before any VTT view. An endpoint without a
    # reviewed policy is a 500 and a session-less POST meets the CSRF guard, so
    # the pairing routes are proved through the real app, not a bare Flask one.
    url = f"sqlite+pysqlite:///{(tmp_path / 'w.sqlite').as_posix()}"
    env = dict(os.environ, VTT_ENABLED="1", DATABASE_URL=url, DATA_DIR=str(tmp_path), GM_PASSWORD="")
    code = r'''
import json
from core.persistence import Database
Database(__import__("os").environ["DATABASE_URL"]).create_schema()
import app
from core.route_policy import require_complete_inventory, policy_for
require_complete_inventory(app.app.url_map)
client = app.app.test_client()
redeem = client.post("/vtt/api/table-pairing/redeem", json={"code": "ZZZZ-ZZZZ"},
                     headers={"Origin": "https://localhost"}, base_url="https://localhost")
issue = client.post("/vtt/api/table-pairing", json={"campaignId": "camp_a"},
                    headers={"Origin": "https://localhost"}, base_url="https://localhost")
print(json.dumps({"redeem": [redeem.status_code, redeem.get_json()],
                  "issue": issue.status_code, "issue_body": issue.get_json(),
                  "policy": policy_for("vtt_pairing.redeem", "POST").value}))
'''
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True,
                         text=True, encoding="utf-8").stdout.strip().splitlines()[-1]
    result = json.loads(out)
    # The device route reaches its own handler: a wrong code, not a policy or CSRF refusal.
    assert result["redeem"] == [400, {"error": "pairing-failed"}]
    assert result["policy"] == "public"
    # The GM route is reached too and refuses an anonymous caller itself (no 500).
    assert result["issue"] in (401, 403), result
