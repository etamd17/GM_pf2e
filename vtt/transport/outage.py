"""A store outage on a VTT HTTP route is a clean, retryable 503 (M6, handoff 053).

PostgreSQL being unreachable, a pool checkout timing out, or a statement or lock
timeout is not a server bug: the request may simply be retried. The socket path
already answers these with ``temporarily-unavailable``; the HTTP routes used to
fall through to the app's generic 500 with a stack trace. Installed per
blueprint, so it covers exactly the VTT routes and nothing else in the app.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout

log = logging.getLogger(__name__)
RETRY_AFTER_SECONDS = 5


def install(bp: Blueprint) -> Blueprint:
    @bp.errorhandler(OperationalError)
    @bp.errorhandler(DBAPIError)
    @bp.errorhandler(PoolTimeout)
    def store_unavailable(exc):  # noqa: ARG001 - logged without the payload
        log.warning("VTT store unavailable on an HTTP route: %s", type(exc).__name__)
        response = jsonify({"error": "temporarily-unavailable"})
        response.status_code = 503
        response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
        response.headers["Cache-Control"] = "no-store"
        return response

    return bp
