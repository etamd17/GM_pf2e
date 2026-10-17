"""M6 (handoff 053 item 6): with the store unreachable, every VTT HTTP route
answers a clean, retryable 503 instead of the app's generic 500."""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

from .conftest import CSRF

ORIGIN = "https://localhost"


def _down(stack, monkeypatch, error):
    @contextlib.contextmanager
    def unreachable(*_args, **_kwargs):
        raise error
        yield  # pragma: no cover

    monkeypatch.setattr(stack.db, "session", unreachable)
    monkeypatch.setattr(stack.db, "transaction", unreachable)


ROUTES = [
    ("post", "/vtt/api/table-pairing", {"campaignId": "camp_a"}),
    ("post", "/vtt/api/import-legacy", {"campaignId": "camp_a"}),
    ("get", "/vtt/api/scenes?campaignId=camp_a", None),
    ("get", "/vtt/api/table-grants?campaignId=camp_a", None),
]


@pytest.mark.parametrize("error", [
    OperationalError("SELECT 1", {}, Exception("connection timeout expired")),
    PoolTimeout("QueuePool limit reached"),
], ids=["connect-timeout", "pool-timeout"])
@pytest.mark.parametrize("method,path,body", ROUTES, ids=[r[1].split("?")[0] for r in ROUTES])
def test_a_store_outage_is_a_retryable_503(stack, monkeypatch, error, method, path, body):
    _down(stack, monkeypatch, error)
    client = stack.app.test_client()
    kwargs = {"headers": {"X-CSRF-Token": CSRF}, "base_url": ORIGIN}
    if body is not None:
        kwargs["json"] = body
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 503, response.get_data(as_text=True)
    assert response.get_json() == {"error": "temporarily-unavailable"}
    assert response.headers["Retry-After"] == "5"
    assert response.headers["Cache-Control"] == "no-store"


def test_the_handler_is_scoped_to_the_vtt_routes(stack, monkeypatch):
    # An unrelated route in the same app keeps the app's own error handling.
    @stack.app.route("/not-vtt")
    def not_vtt():
        raise OperationalError("SELECT 1", {}, Exception("down"))

    stack.app.config["PROPAGATE_EXCEPTIONS"] = False
    response = stack.app.test_client().get("/not-vtt")
    assert response.status_code == 500
