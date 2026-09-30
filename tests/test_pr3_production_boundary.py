"""HTTP-level gates for PR3A's production trust boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


REPO = Path(__file__).resolve().parents[1]
RESULT_PREFIX = "PR3_BOUNDARY_RESULT="


def _run_isolated(
    code: str,
    *,
    data_dir: Path,
    valid_config: bool,
    overrides: dict[str, str] | None = None,
) -> dict:
    env = os.environ.copy()
    for name in (
        "SECRET_KEY", "SETUP_TOKEN", "PUBLIC_BASE_URL", "ALLOWED_HOSTS",
        "GM_PASSWORD", "FLASK_DEBUG", "TRUST_PROXY_HOPS",
        "RAILWAY_DEPLOYMENT_ID", "RAILWAY_ENVIRONMENT_ID",
        "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_GIT_COMMIT_SHA",
    ):
        env.pop(name, None)
    env.update({
        "APP_ENV": "production",
        "DATA_DIR": str(data_dir),
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    if valid_config:
        env.update({
            "SECRET_KEY": "0dL7wQa9X2mK8vPc4sRf6uHy1eTz5nBj0dL7wQa9X2mK8vPc",
            "SETUP_TOKEN": "setup-7zQ2mK8vPc4sRf6uHy1eTz5n",
            "PUBLIC_BASE_URL": "https://table.example",
            "TRUST_PROXY_HOPS": "1",
        })
    if overrides:
        env.update(overrides)
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, (
        f"isolated production scenario failed\nstdout:\n{completed.stdout}"
        f"\nstderr:\n{completed.stderr}"
    )
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX):])
    raise AssertionError(f"missing result marker in stdout:\n{completed.stdout}")


def test_missing_production_configuration_blocks_everything_but_probes(tmp_path):
    result = _run_isolated(
        r'''
        import json
        import app as application

        client = application.app.test_client()
        result = {
            "live": client.get("/live").status_code,
            "ready": client.get("/ready").status_code,
            "setup": client.post("/setup", data={
                "username": "takeover", "password": "secret1",
            }).status_code,
            "gm_mutation": client.post(
                "/api/gm/advancement_mode", json={"mode": "xp"}
            ).status_code,
        }
        print("PR3_BOUNDARY_RESULT=" + json.dumps(result, sort_keys=True))
        ''',
        data_dir=tmp_path,
        valid_config=False,
    )
    assert result == {"live": 200, "ready": 503, "setup": 503, "gm_mutation": 503}


def test_valid_production_bootstrap_cookie_host_csrf_and_headers(tmp_path):
    result = _run_isolated(
        r'''
        import json
        import re
        import app as application

        client = application.app.test_client()
        base = "https://table.example"
        forwarded = {
            "X-Forwarded-Host": "table.example",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.10",
        }
        same_origin = dict(forwarded, Origin="https://table.example")

        ready = client.get("/ready", base_url=base, headers=forwarded)
        railway_ready = client.get(
            "/ready", base_url="http://healthcheck.railway.app"
        )
        railway_host_non_probe = client.get(
            "/me", base_url="http://healthcheck.railway.app"
        )
        setup_page = client.get("/setup", base_url=base, headers=forwarded)
        blocked_before_setup = client.post(
            "/api/gm/advancement_mode", base_url=base, headers=same_origin,
            json={"mode": "xp"},
        )
        wrong = client.post(
            "/setup", base_url=base, headers=same_origin,
            data={"username": "admin", "password": "secret1", "setup_token": "wrong"},
        )
        created = client.post(
            "/setup", base_url=base, headers=same_origin,
            data={
                "username": "admin", "password": "secret1",
                "setup_token": "setup-7zQ2mK8vPc4sRf6uHy1eTz5n",
            },
        )
        me = client.get("/me", base_url=base, headers=forwarded)
        token_match = re.search(r'name="csrf-token" content="([^"]+)"', me.get_data(as_text=True))
        token = token_match.group(1) if token_match else ""
        gm_login_page = client.get("/gm/login", base_url=base, headers=forwarded)
        cross_site = client.post(
            "/campaigns/new", base_url=base,
            headers=dict(forwarded, Origin="https://evil.example"),
            data={"name": "Blocked cross-site"},
        )
        missing_origin = client.post(
            "/campaigns/new", base_url=base, headers=forwarded,
            data={"name": "Blocked without provenance"},
        )
        token_only = client.post(
            "/campaigns/new", base_url=base,
            headers=dict(forwarded, **{"X-CSRF-Token": token}),
            data={"name": "Created with CSRF token"},
        )
        spoofed_host = client.get(
            "/me", base_url=base,
            headers=dict(forwarded, **{"X-Forwarded-Host": "evil.example"}),
        )
        private_api = client.get("/api/my_campaigns", base_url=base, headers=forwarded)

        result = {
            "ready": ready.status_code,
            "railway_ready": railway_ready.status_code,
            "railway_host_non_probe": railway_host_non_probe.status_code,
            "setup_page": setup_page.status_code,
            "setup_cache": setup_page.headers.get("Cache-Control", ""),
            "blocked_before_setup": blocked_before_setup.status_code,
            "wrong_token": wrong.status_code,
            "created": created.status_code,
            "cookie": created.headers.get("Set-Cookie", ""),
            "me": me.status_code,
            "has_token": bool(token),
            "gm_login": gm_login_page.status_code,
            "gm_login_location": gm_login_page.headers.get("Location", ""),
            "cross_site": cross_site.status_code,
            "missing_origin": missing_origin.status_code,
            "token_only": token_only.status_code,
            "spoofed_host": spoofed_host.status_code,
            "private_cache": private_api.headers.get("Cache-Control", ""),
            "csp": ready.headers.get("Content-Security-Policy", ""),
            "hsts": ready.headers.get("Strict-Transport-Security", ""),
            "nosniff": ready.headers.get("X-Content-Type-Options", ""),
            "request_id": ready.headers.get("X-Request-ID", ""),
        }
        print("PR3_BOUNDARY_RESULT=" + json.dumps(result, sort_keys=True))
        ''',
        data_dir=tmp_path,
        valid_config=True,
    )

    assert result["ready"] == 200
    assert result["railway_ready"] == 200
    assert result["railway_host_non_probe"] == 400
    assert result["setup_page"] == 200
    assert "private" in result["setup_cache"] and "no-store" in result["setup_cache"]
    assert result["blocked_before_setup"] == 503
    assert result["wrong_token"] == 403
    assert result["created"] == 302
    assert all(flag in result["cookie"] for flag in ("Secure", "HttpOnly", "SameSite=Lax"))
    assert result["me"] == 200 and result["has_token"] is True
    assert result["gm_login"] == 302
    assert "/login" in result["gm_login_location"]
    assert result["cross_site"] == 400
    assert result["missing_origin"] == 400
    assert result["token_only"] == 302
    assert result["spoofed_host"] == 400
    assert "private" in result["private_cache"] and "no-store" in result["private_cache"]
    assert "frame-ancestors 'self'" in result["csp"]
    assert result["hsts"].startswith("max-age=")
    assert result["nosniff"] == "nosniff"
    assert len(result["request_id"]) == 32


def test_unload_autosaves_use_csrf_aware_keepalive_fetch():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "templates/chronicle_journal.html",
        "templates/notes.html",
        "templates/cosmere_sheet.html",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "navigator.sendBeacon" not in source
        assert "keepalive:true" in source or "keepalive: true" in source


def test_invalid_allowed_host_configuration_fails_readiness(tmp_path):
    result = _run_isolated(
        r'''
        import json
        import app as application

        client = application.app.test_client()
        result = {
            "ready": client.get(
                "/ready",
                base_url="http://healthcheck.railway.app",
            ).status_code,
            "normal": client.get(
                "/setup",
                base_url="https://table.example",
                headers={
                    "X-Forwarded-Host": "table.example",
                    "X-Forwarded-Proto": "https",
                },
            ).status_code,
        }
        print("PR3_BOUNDARY_RESULT=" + json.dumps(result, sort_keys=True))
        ''',
        data_dir=tmp_path,
        valid_config=True,
        overrides={"ALLOWED_HOSTS": "https://bad.example"},
    )
    assert result == {"normal": 503, "ready": 503}


def test_railway_uses_x_real_ip_without_trusting_forwarded_for(tmp_path):
    result = _run_isolated(
        r'''
        import json
        import app as application

        with application.app.test_request_context(
            "/login",
            headers={
                "X-Real-IP": "203.0.113.42",
                "X-Forwarded-For": "198.51.100.9, 10.0.0.2",
            },
            environ_base={"REMOTE_ADDR": "10.0.0.3"},
        ):
            client_ip = application._auth_client_ip()
        proxy = application.app.wsgi_app
        result = {
            "client_ip": client_ip,
            "x_for": proxy.x_for,
            "x_host": proxy.x_host,
            "x_proto": proxy.x_proto,
        }
        print("PR3_BOUNDARY_RESULT=" + json.dumps(result, sort_keys=True))
        ''',
        data_dir=tmp_path,
        valid_config=True,
        overrides={"RAILWAY_DEPLOYMENT_ID": "deployment-1"},
    )
    assert result == {
        "client_ip": "203.0.113.42",
        "x_for": 0,
        "x_host": 1,
        "x_proto": 1,
    }


def test_direct_production_does_not_trust_forwarded_headers_by_default(tmp_path):
    result = _run_isolated(
        r'''
        import json
        import app as application

        proxy = application.app.wsgi_app
        print("PR3_BOUNDARY_RESULT=" + json.dumps({
            "x_for": proxy.x_for,
            "x_host": proxy.x_host,
            "x_proto": proxy.x_proto,
        }, sort_keys=True))
        ''',
        data_dir=tmp_path,
        valid_config=True,
        overrides={"TRUST_PROXY_HOPS": ""},
    )
    assert result == {"x_for": 0, "x_host": 0, "x_proto": 0}
