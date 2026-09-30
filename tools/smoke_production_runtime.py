#!/usr/bin/env python3
"""Exercise the production Gunicorn/gevent boundary over real loopback HTTP.

The smoke is intentionally standard-library-only.  It starts the exact command
from railway.toml with an isolated DATA_DIR, performs first-admin bootstrap and
campaign activation, then proves a long-lived authenticated SSE request does not
block readiness or a mutation handled by the single gevent worker.
"""

from __future__ import annotations

import http.client
import http.cookies
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import quote, urlencode


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_HOST = "runtime-smoke.invalid"
PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
RAILWAY_PROBE_HOST = "healthcheck.railway.app"
STARTUP_TIMEOUT_SECONDS = 75
REQUEST_TIMEOUT_SECONDS = 10
# Gunicorn's default graceful timeout is 30s.  A just-closed SSE handler may be
# waiting on its 30s queue timeout before it observes the client disconnect.
SHUTDOWN_TIMEOUT_SECONDS = 45

SECRET_KEY = "0dL7wQa9X2mK8vPc4sRf6uHy1eTz5nBj0dL7wQa9X2mK8vPc"
SETUP_TOKEN = "7zQ2mK8vPc4sRf6uHy1eTz5n"
ADMIN_USERNAME = "runtime-smoke-admin"
ADMIN_PASSWORD = "runtime-smoke-password-7zQ2mK8v"
CAMPAIGN_NAME = "Runtime Smoke Campaign"


class SmokeFailure(RuntimeError):
    """The production runtime did not satisfy its release boundary."""


class HttpResult:
    def __init__(
        self,
        status: int,
        reason: str,
        headers: list[tuple[str, str]],
        body: bytes,
    ) -> None:
        self.status = status
        self.reason = reason
        self.headers = tuple(headers)
        self.body = body

    def header(self, name: str) -> str | None:
        wanted = name.casefold()
        for header_name, value in self.headers:
            if header_name.casefold() == wanted:
                return value
        return None


def _body_excerpt(body: bytes, limit: int = 500) -> str:
    text = body.decode("utf-8", errors="replace").replace("\n", " ").strip()
    return text[:limit]


def _expect_status(result: HttpResult, expected: int, label: str) -> None:
    if result.status != expected:
        raise SmokeFailure(
            f"{label}: expected HTTP {expected}, got {result.status} "
            f"{result.reason}: {_body_excerpt(result.body)}"
        )


def _json_body(result: HttpResult, label: str) -> object:
    try:
        return json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure(
            f"{label}: response was not valid JSON: {_body_excerpt(result.body)}"
        ) from exc


def _request(
    port: int,
    method: str,
    path: str,
    *,
    host: str = PUBLIC_HOST,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> HttpResult:
    request_headers = {"Host": host, "Connection": "close"}
    if headers:
        request_headers.update(headers)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return HttpResult(
            response.status,
            response.reason,
            response.getheaders(),
            response.read(),
        )
    finally:
        connection.close()


def _post_form(
    port: int,
    path: str,
    values: dict[str, str],
    *,
    cookie: str | None = None,
) -> HttpResult:
    body = urlencode(values).encode("ascii")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": PUBLIC_ORIGIN,
    }
    if cookie:
        headers["Cookie"] = cookie
    return _request(port, "POST", path, body=body, headers=headers)


def _session_cookie(result: HttpResult, current: str | None = None) -> str:
    cookies = http.cookies.SimpleCookie()
    for name, value in result.headers:
        if name.casefold() == "set-cookie":
            cookies.load(value)
    morsel = cookies.get("session")
    if morsel is None or not morsel.value:
        if current is not None:
            return current
        raise SmokeFailure("first-admin setup did not issue a session cookie")
    # Production cookies are Secure; the loopback transport is deliberately HTTP,
    # so the stdlib client supplies the captured cookie explicitly on each request.
    return f"session={morsel.value}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _option_value(tokens: list[str], option: str) -> str | None:
    for index, token in enumerate(tokens):
        if token == option and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(option + "="):
            return token.split("=", 1)[1]
    return None


def _load_start_command() -> str:
    config_path = ROOT / "railway.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    try:
        command = str(config["deploy"]["startCommand"]).strip()
    except (KeyError, TypeError) as exc:
        raise SmokeFailure("railway.toml has no deploy.startCommand") from exc

    tokens = shlex.split(command)
    if tokens[:2] != ["gunicorn", "app:app"]:
        raise SmokeFailure("Railway startCommand must launch gunicorn app:app")
    if _option_value(tokens, "--bind") != "0.0.0.0:$PORT":
        raise SmokeFailure("Railway startCommand must bind Gunicorn to 0.0.0.0:$PORT")
    if _option_value(tokens, "--workers") != "1":
        raise SmokeFailure("Railway startCommand must use exactly one worker")
    if _option_value(tokens, "--worker-class") != "gevent":
        raise SmokeFailure("Railway startCommand must use the gevent worker class")
    return command


def _server_environment(data_dir: Path, port: int) -> dict[str, str]:
    environment = dict(os.environ)
    controlled = {
        "ALLOWED_HOSTS",
        "APP_ENV",
        "DATA_DIR",
        "ENVIRONMENT",
        "FLASK_DEBUG",
        "FLASK_ENV",
        "GM_PASSWORD",
        "GUNICORN_CMD_ARGS",
        "PF2E_AUDIO_DIR",
        "PORT",
        "PUBLIC_BASE_URL",
        "PYTEST_CURRENT_TEST",
        "SECRET_KEY",
        "SESSION_STATE_PATH",
        "SETUP_TOKEN",
        "TRUST_PROXY_HOPS",
        "WERKZEUG_RUN_MAIN",
    }
    for name in list(environment):
        if name in controlled or name.startswith("RAILWAY_"):
            environment.pop(name, None)
    environment.update(
        {
            "APP_ENV": "production",
            "DATA_DIR": str(data_dir),
            "FLASK_DEBUG": "false",
            "PORT": str(port),
            "PUBLIC_BASE_URL": PUBLIC_ORIGIN,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONUNBUFFERED": "1",
            "SECRET_KEY": SECRET_KEY,
            "SESSION_STATE_PATH": str(data_dir / "session_state.json"),
            "SETUP_TOKEN": SETUP_TOKEN,
            "TRUST_PROXY_HOPS": "0",
        }
    )
    return environment


def _wait_until_ready(process: subprocess.Popen[bytes], port: int) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    last_error = "server did not accept a connection"
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise SmokeFailure(
                f"Gunicorn exited before readiness with status {return_code}"
            )
        try:
            response = _request(
                port,
                "GET",
                "/ready",
                host=RAILWAY_PROBE_HOST,
                timeout=1,
            )
            if response.status == 200:
                payload = _json_body(response, "startup readiness")
                if payload != {"status": "ready"}:
                    raise SmokeFailure(
                        f"startup readiness returned an unexpected payload: {payload!r}"
                    )
                return
            last_error = (
                f"HTTP {response.status}: {_body_excerpt(response.body)}"
            )
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise SmokeFailure(
        f"Gunicorn did not become ready within {STARTUP_TIMEOUT_SECONDS}s: "
        f"{last_error}"
    )


def _open_sse(
    port: int,
    cookie: str,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    connection = http.client.HTTPConnection(
        "127.0.0.1", port, timeout=REQUEST_TIMEOUT_SECONDS
    )
    connection.request(
        "GET",
        "/api/events",
        headers={
            "Accept": "text/event-stream",
            "Cookie": cookie,
            "Host": PUBLIC_HOST,
        },
    )
    response = connection.getresponse()
    if response.status != 200:
        body = response.read()
        response.close()
        connection.close()
        raise SmokeFailure(
            "authenticated SSE connection failed: "
            f"HTTP {response.status} {response.reason}: {_body_excerpt(body)}"
        )
    content_type = response.getheader("Content-Type", "")
    if not content_type.casefold().startswith("text/event-stream"):
        response.close()
        connection.close()
        raise SmokeFailure(f"SSE returned unexpected Content-Type {content_type!r}")
    if response.getheader("X-Accel-Buffering", "").casefold() != "no":
        response.close()
        connection.close()
        raise SmokeFailure("SSE response did not disable proxy buffering")
    return connection, response


def _read_sse_frame(response: http.client.HTTPResponse) -> tuple[str | None, str]:
    lines: list[str] = []
    while True:
        try:
            raw_line = response.readline()
        except (OSError, TimeoutError) as exc:
            raise SmokeFailure(f"timed out waiting for an SSE frame: {exc}") from exc
        if not raw_line:
            raise SmokeFailure("SSE stream closed before the expected event")
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line:
            if not lines:
                continue
            event = next(
                (item[6:].strip() for item in lines if item.startswith("event:")),
                None,
            )
            return event, "\n".join(lines)
        lines.append(line)


def _expect_sse_event(
    response: http.client.HTTPResponse,
    expected: str,
    *,
    max_frames: int = 5,
) -> str:
    observed: list[str] = []
    for _ in range(max_frames):
        event, frame = _read_sse_frame(response)
        observed.append(event or "comment")
        if event == expected:
            return frame
    raise SmokeFailure(
        f"SSE never delivered event {expected!r}; observed {observed!r}"
    )


def _bootstrap_and_activate(port: int) -> tuple[str, str]:
    setup = _post_form(
        port,
        "/setup",
        {
            "display_name": "Runtime Smoke Admin",
            "password": ADMIN_PASSWORD,
            "setup_token": SETUP_TOKEN,
            "username": ADMIN_USERNAME,
        },
    )
    _expect_status(setup, 302, "first-admin setup")
    cookie = _session_cookie(setup)

    created = _post_form(
        port,
        "/campaigns/new",
        {"name": CAMPAIGN_NAME, "system": "pf2e"},
        cookie=cookie,
    )
    _expect_status(created, 302, "campaign creation")
    cookie = _session_cookie(created, cookie)

    campaigns = _request(
        port,
        "GET",
        "/api/my_campaigns",
        headers={"Cookie": cookie},
    )
    _expect_status(campaigns, 200, "authenticated campaign listing")
    payload = _json_body(campaigns, "authenticated campaign listing")
    if not isinstance(payload, dict) or not isinstance(payload.get("campaigns"), list):
        raise SmokeFailure("campaign listing had an unexpected shape")
    matches = [
        campaign
        for campaign in payload["campaigns"]
        if isinstance(campaign, dict) and campaign.get("name") == CAMPAIGN_NAME
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
        raise SmokeFailure("new campaign was not visible to the authenticated admin")
    campaign_id = matches[0]["id"]

    activated = _post_form(
        port,
        f"/campaign/{quote(campaign_id, safe='')}/activate",
        {},
        cookie=cookie,
    )
    _expect_status(activated, 302, "campaign activation")
    cookie = _session_cookie(activated, cookie)

    live_campaigns = _request(
        port,
        "GET",
        "/api/my_campaigns",
        headers={"Cookie": cookie},
    )
    _expect_status(live_campaigns, 200, "live campaign verification")
    live_payload = _json_body(live_campaigns, "live campaign verification")
    live_matches = [
        campaign
        for campaign in live_payload.get("campaigns", [])
        if isinstance(campaign, dict) and campaign.get("id") == campaign_id
    ] if isinstance(live_payload, dict) else []
    if len(live_matches) != 1 or not (
        live_matches[0].get("is_active") and live_matches[0].get("is_live")
    ):
        raise SmokeFailure("created campaign did not become active and live")
    return cookie, campaign_id


def _exercise_runtime(port: int) -> None:
    cookie, _campaign_id = _bootstrap_and_activate(port)
    sse_connection: http.client.HTTPConnection | None = None
    sse_response: http.client.HTTPResponse | None = None
    try:
        sse_connection, sse_response = _open_sse(port, cookie)
        connected = _expect_sse_event(sse_response, "connected")
        if "data:" not in connected:
            raise SmokeFailure("SSE connected frame did not include deploy data")

        started = time.monotonic()
        ready = _request(
            port,
            "GET",
            "/ready",
            host=RAILWAY_PROBE_HOST,
            timeout=5,
        )
        ready_elapsed = time.monotonic() - started
        _expect_status(ready, 200, "readiness while SSE was open")
        if _json_body(ready, "readiness while SSE was open") != {"status": "ready"}:
            raise SmokeFailure("concurrent readiness returned an unexpected payload")
        if ready_elapsed >= 5:
            raise SmokeFailure(
                f"readiness took {ready_elapsed:.2f}s while SSE was open"
            )

        mutated = _request(
            port,
            "POST",
            "/api/session/roll_initiative",
            body=b"",
            headers={"Cookie": cookie, "Origin": PUBLIC_ORIGIN},
        )
        _expect_status(mutated, 200, "authenticated GM mutation")
        if _json_body(mutated, "authenticated GM mutation") != {"success": True}:
            raise SmokeFailure("GM mutation returned an unexpected payload")
        _expect_sse_event(sse_response, "roll_initiative")
    finally:
        if sse_response is not None:
            sse_response.close()
        if sse_connection is not None:
            sse_connection.close()


def _stop_server(process: subprocess.Popen[bytes]) -> str | None:
    if process.poll() is not None:
        return f"Gunicorn exited before the smoke sent SIGTERM (status {process.returncode})"
    process.send_signal(signal.SIGTERM)
    try:
        return_code = process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        return (
            f"Gunicorn did not stop within {SHUTDOWN_TIMEOUT_SECONDS}s "
            "after SIGTERM"
        )
    if return_code != 0:
        return f"Gunicorn exited with status {return_code}"
    return None


def _log_tail(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as exc:
        return f"<could not read Gunicorn log: {exc}>"


def run_smoke() -> None:
    if not sys.platform.startswith("linux"):
        raise SmokeFailure(
            "the Gunicorn runtime smoke requires Linux; run it in WSL or Linux CI"
        )

    command = _load_start_command()
    port = _free_port()
    smoke_error: Exception | None = None
    shutdown_error: str | None = None

    with tempfile.TemporaryDirectory(prefix="gm-pf2e-production-smoke-") as temp_root:
        temp_path = Path(temp_root)
        data_dir = temp_path / "data"
        data_dir.mkdir()
        log_path = temp_path / "gunicorn.log"
        with log_path.open("wb") as log_file:
            process = subprocess.Popen(
                ["/bin/sh", "-c", f"exec {command}"],
                cwd=ROOT,
                env=_server_environment(data_dir, port),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                _wait_until_ready(process, port)
                _exercise_runtime(port)
            except Exception as exc:
                smoke_error = exc
            finally:
                shutdown_error = _stop_server(process)

        if smoke_error is not None or shutdown_error is not None:
            details = []
            if smoke_error is not None:
                details.append(str(smoke_error))
            if shutdown_error is not None:
                details.append(shutdown_error)
            details.append("Gunicorn log tail:\n" + _log_tail(log_path))
            raise SmokeFailure("\n".join(details)) from smoke_error


def main() -> int:
    try:
        run_smoke()
    except SmokeFailure as exc:
        print(f"production runtime smoke failed: {exc}", file=sys.stderr)
        return 1
    print("production runtime smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
