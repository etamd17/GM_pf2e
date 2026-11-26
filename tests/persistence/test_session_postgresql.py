"""PE-14e on PostgreSQL: the PF2e session slot and its lock order.

CI only (``-m postgresql``; Docker is retired locally).  SQLite's single
writer serializes everything, so the PF2e slot key and the order "stream row,
then slot key" are proved here.  Commands run through ``execute`` with the
real authorizer; the seed (PF2e campaigns A and B in db authority with a pin)
and the pause helpers are PE-14b's and PE-14d's.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select

from core.commands import registry, sessions
from core.persistence import identity, streams
from core.persistence.models import CampaignSession
from tests.persistence.test_auth_writes_postgresql import (
    _interleave, _invite, _Pause, _prepare, _signup, _state,
)
from tests.persistence.test_command_executor import HOOKS
from tests.persistence.test_postgresql_concurrency import _race, postgres_database  # noqa: F401
from tests.persistence.test_session_commands import call, end_env, start_env

pytestmark = pytest.mark.postgresql


@pytest.fixture
def pg(postgres_database, monkeypatch, tmp_path):
    _prepare(postgres_database, monkeypatch, tmp_path)  # campaigns A and B, PF2e, db authority
    with postgres_database.transaction() as session:  # existing stream rows: waits are row locks
        streams.lock_stream(session, "B")
    yield postgres_database
    HOOKS.clear()


def _active(database):
    with database.session() as session:
        return sorted(session.execute(select(CampaignSession.campaign_id,
                                             CampaignSession.session_id)
                                      .where(CampaignSession.status == "active")).all())


def _pause_start_apply(monkeypatch):
    """Pause ``session.start`` after its insert: it holds the stream row and the slot key."""

    registered = registry.get(sessions.START)
    pause = _Pause(registered.apply, lambda args: args[0].session)
    monkeypatch.setitem(registry._TYPES, sessions.START,
                        dataclasses.replace(registered, apply=pause))
    return pause


def _status(response):
    if isinstance(response, Exception):
        return response
    return response.status, (response.body or {}).get("code")


def test_second_pf2e_campaign_waits_on_the_slot_key_then_is_refused(pg, monkeypatch):
    pause = _pause_start_apply(monkeypatch)
    first, second = _interleave(pg, pause, lambda: call(start_env("campaign-a-live", "A")),
                                lambda: call(start_env("campaign-b-live", "B")))
    assert _status(first) == (200, None)
    assert _status(second) == (422, "rule_violation")
    assert _active(pg) == [("A", "campaign-a-live")]


def test_two_pf2e_campaigns_start_concurrently_exactly_one_succeeds(pg):
    for n in range(10):
        a, b = f"race-a-{n:04d}", f"race-b-{n:04d}"
        outcomes = [_status(r) for r in _race([lambda: call(start_env(a, "A")),
                                               lambda: call(start_env(b, "B"))])]
        assert sorted(outcomes, key=str) == [(200, None), (422, "rule_violation")], outcomes
        (winner,) = _active(pg)
        assert winner in (("A", a), ("B", b))
        assert _status(call(end_env(winner[1], winner[0]))) == (200, None)


def test_start_holding_stream_and_account_signup_both_finish(pg, monkeypatch):
    """Start holds stream A and then takes the slot key; signup holds 73420401 and waits on A.

    If the slot key were the account-creation key, this would deadlock.
    """

    inv = _invite(pg)
    pause = _Pause(streams.lock_stream)
    monkeypatch.setattr(streams, "lock_stream", pause)
    started, signup = _interleave(pg, pause, lambda: call(start_env("with-signup-1", "A")),
                                  lambda: _signup(inv))
    assert _status(started) == (200, None)
    assert not isinstance(signup, Exception), signup
    assert _state(pg, inv).signup_id == signup[0]["id"]
    assert _active(pg) == [("A", "with-signup-1")]


def test_signup_holding_stream_and_start_both_finish(pg, monkeypatch):
    inv = _invite(pg)
    pause = _Pause(identity._insert_user)  # signup holds 73420401, stream A and its user
    monkeypatch.setattr(identity, "_insert_user", pause)
    signup, started = _interleave(pg, pause, lambda: _signup(inv),
                                  lambda: call(start_env("with-signup-2", "A")))
    assert not isinstance(signup, Exception), signup
    assert _status(started) == (200, None)
    assert _active(pg) == [("A", "with-signup-2")]


def test_start_and_signup_race_completes(pg):
    for n in range(10):
        inv = _invite(pg)
        sid = f"race-signup-{n:04d}"
        started, signup = _race([lambda: call(start_env(sid, "A")), lambda: _signup(inv)])
        assert _status(started) == (200, None)
        assert not isinstance(signup, Exception), signup
        assert _status(call(end_env(sid, "A"))) == (200, None)
