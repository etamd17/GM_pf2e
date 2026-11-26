"""PE-14e: ``session.start`` and ``session.end`` through ``execute`` on SQLite.

The real authorizer decides checks 4 and 5.  Every campaign has a db
``campaign_state_authority`` row and a ``campaigns.settings["rules"]`` pin
matching its system (PF2e for A, B and C; D&D for the control campaign D),
and every envelope names that pin's profile and the stream epoch.  ``execute``
gets ``content_descriptor = pin["content"]`` and a ``project`` returning
``"<kind>:<id>"``-keyed projections (PE-13b implementation review 1).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.commands import executor, registry, sessions
from core.commands.authorizer import load
from core.commands.checks import CommandRefusal
from core.commands.events import EventDraft
from core.commands.executor import execute
from core.commands.grant_commands import SET_QUARTERMASTER
from core.commands.session_history import LegacyHistory, backfill_session_history
from core.persistence import identity, runtime
from core.persistence.models import (
    Campaign,
    CampaignMembership,
    CampaignSession,
    CampaignStateAuthority,
    CommandReceipt,
)
from core.persistence.state_repositories import StateRecord
from core.request_context import Principal, PrincipalKind

from .test_command_authorizer import _token, _user
from .test_command_executor import NOW, Tracked, events, op, receipt

PF2E = {"profile": "pf2e", "content": {"ruleset_id": "pf2e-legacy-unknown",
                                       "manifest_sha256": None}}
DND = {"profile": "dnd5e-2014", "content": {"data_version": "v1", "db_sha256": "a" * 64}}
PINS = {"A": PF2E, "B": PF2E, "C": PF2E, "D": DND}
START, END = sessions.START, sessions.END
GM, ADMIN, P1 = Principal.user("gm"), Principal.user("admin"), Principal.user("p1")
T_LIVE, T_START, T_END, T_OVERRIDE = (Principal.integration_token(c, campaign_id="A")
                                      for c in ("t_live", "t_start", "t_end", "t_override"))
SID = "session-2026-11-26-ab12cd34"


@pytest.fixture
def db(sqlite_database, monkeypatch):
    with sqlite_database.transaction() as s:
        for uid in ("gm", "p1"):
            _user(s, uid)
        _user(s, "admin", admin=True)
        s.flush()
        for cid, pin in PINS.items():
            s.add(Campaign(id=cid, slug=cid, name=cid, system=pin["profile"].split("-")[0],
                           created_by_user_id="gm", created_at=NOW, settings={"rules": pin}))
        s.flush()
        for cid in PINS:
            s.add(CampaignStateAuthority(campaign_id=cid, authority="db", stream_epoch=1,
                                         changed_at=NOW))
            s.add(CampaignMembership(campaign_id=cid, user_id="gm", role="gm"))
        s.add(CampaignMembership(campaign_id="A", user_id="p1", role="player"))
        _token(s, "t_live", "A", ["@live"])
        _token(s, "t_start", "A", [START])
        _token(s, "t_end", "A", [END])
        _token(s, "t_override", "A", [END, "override:" + END])
        _token(s, "t_b", "B", ["@live"])
    tracked = Tracked(sqlite_database)
    monkeypatch.setattr(runtime, "database", lambda: tracked)
    return tracked


class Provider:
    """A test open-actions provider holding one open action until it is cancelled."""

    def __init__(self, open_ids=("act-1",)):
        self.open = list(open_ids)
        self.calls = []

    def open_actions(self, ctx):
        self.calls.append("open_actions")
        return list(self.open)

    def lock(self, ctx):
        self.calls.append("lock")

    def cancel_all(self, ctx):
        self.calls.append("cancel_all")
        for action_id in self.open:
            ctx.emit(EventDraft(type="action.cancelled", action_id=action_id,
                                subjects=[{"kind": "session", "id": ctx.session_id}],
                                visibility={"gm_integration": "full"}))
        self.open = []


@pytest.fixture
def provider(monkeypatch):
    test_provider = Provider()
    monkeypatch.setattr(sessions.OPEN_ACTIONS, "provider", test_provider)
    return test_provider


def env(type, subject, payload, *, cid="A", override=None, operation_id=None):
    body = {"schema": 1, "operation_id": operation_id or op(), "campaign_id": cid, "epoch": 1,
            "type": type, "subject": subject, "expected": [],
            "rules": {"profile": PINS[cid]["profile"]}, "payload": payload}
    if override is not None:
        body["override"] = {"reason": override}
    return body


def start_env(session_id=SID, cid="A", **kwargs):
    return env(START, {"campaign_id": cid}, {"session_id": session_id}, cid=cid, **kwargs)


def end_env(session_id=SID, cid="A", **kwargs):
    return env(END, {"session_id": session_id}, {}, cid=cid, **kwargs)


def project(principal, records):
    return {f"{kind}:{id}": dict(record.payload) for (kind, id), record in records.items()}


def held(authorizer, *, subjects, encounter_id):
    token = authorizer.principal_kind is PrincipalKind.INTEGRATION_TOKEN
    return frozenset({"gm_integration" if token else "gm"})


def call(body, principal=GM):
    return execute(body, principal, route_campaign_id=body["campaign_id"],
                   content_descriptor=PINS[body["campaign_id"]]["content"], project=project,
                   held_projections=held)


def run(body, principal=GM):
    response = call(body, principal)
    return response.status, (response.body or {}).get("code")


def rows(db, cid="A"):
    with db.database.session() as session:
        return {row.session_id: row for row in session.scalars(
            select(CampaignSession).where(CampaignSession.campaign_id == cid))}


def add_row(db, session_id, *, cid="A", status="ended", origin="command"):
    with db.database.transaction() as session:
        session.add(CampaignSession(campaign_id=cid, session_id=session_id, status=status,
                                    origin=origin, started_at=NOW,
                                    ended_at=NOW if status == "ended" else None))


def event_types(db, cid="A"):
    return [event.type for event in events(db, cid)]


def principal_id(principal):
    return principal.user_id or principal.credential_id


# --------------------------------------------------------------------------
# registration


def test_both_types_take_users_and_tokens():
    for name in (START, END):
        assert registry.get(name).allowed_principals == frozenset(
            {PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN})


def test_session_end_override_policy_registered_with_command_type():
    assert registry.get(END).override_gm_only is True
    assert registry.get(START).override_gm_only is False


def test_registration_shape():
    start, end = registry.get(START), registry.get(END)
    assert (start.subject, end.subject) == (("campaign_id", "campaign"), ("session_id", "session"))
    for t in (start, end):
        assert dict(t.dependencies) == {} and dict(t.payload_ids) == {}
        assert t.requires_expected is False
    assert "core.commands.sessions" in registry.COMMAND_MODULES


def test_session_types_register_rules_adapter(db, provider):
    start, end = registry.get(START), registry.get(END)
    subject = StateRecord.serialized("session", SID, {})
    for t in (start, end):
        assert t.derives_rules is False and t.rules_adapter is not None
        assert t.rules_adapter.build_edition_matches(subject, PF2E) is True
    add_row(db, "s-active-1", status="active")
    with db.database.transaction() as session:
        ctx = SimpleNamespace(session=session, campaign_id="A",
                              envelope={"payload": {"session_id": SID}})
        with pytest.raises(CommandRefusal) as second:
            start.rules_adapter.check(ctx, None)
        ctx.envelope = {"payload": {}}
        with pytest.raises(CommandRefusal) as blocked:
            end.rules_adapter.check(ctx, None)
        end.rules_adapter.check(ctx, {"reason": "table ran late"})  # override: passes
    assert (second.value.code, second.value.http_status) == ("rule_violation", 422)
    assert (blocked.value.code, blocked.value.http_status) == ("rule_violation", 422)
    assert blocked.value.actions == ["act-1"] and "act-1" in str(blocked.value)


def test_default_provider_is_no_action_table_and_none_is_refused():
    assert isinstance(sessions.OPEN_ACTIONS.provider, sessions.NoActionTable)
    with pytest.raises(ValueError):
        sessions.OpenActionsRegistry(None)
    with pytest.raises(ValueError):
        sessions.OpenActionsRegistry(object())
    registry_ = sessions.OpenActionsRegistry(sessions.NoActionTable())
    registry_.register(Provider())
    with pytest.raises(ValueError):
        registry_.register(Provider())  # never silently replaced


def test_slot_lock_key_is_not_the_account_creation_key():
    assert identity.PF2E_SESSION_SLOT_LOCK_KEY != identity.ACCOUNT_CREATION_LOCK_KEY
    assert identity.ACCOUNT_CREATION_LOCK_KEY == 73420401


# --------------------------------------------------------------------------
# session.start


@pytest.mark.parametrize("principal", [GM, ADMIN, T_LIVE, T_START],
                         ids=["gm", "site_admin", "live_token", "scoped_token"])
@pytest.mark.parametrize("session_id", ["abcdefgh", "a" * 100, "0_session-2026"],
                         ids=["8_chars", "100_chars", "mixed"])
def test_start_inserts_an_active_command_row(db, principal, session_id):
    response = call(start_env(session_id), principal)
    assert response.status == 200 and response.body["status"] == "committed"
    (row,) = rows(db).values()
    assert (row.session_id, row.status, row.origin) == (session_id, "active", "command")
    assert row.started_by == principal_id(principal) and row.started_at is not None
    assert row.ended_at is None and row.ended_by is None
    (event,) = events(db)
    assert (event.type, event.session_id) == ("session.started", session_id)
    assert response.body["events"] == [{"event_id": event.event_id, "seq": event.seq}]
    assert response.body["result"]["records"][f"session:{session_id}"]["status"] == "active"


@pytest.mark.parametrize("payload", [
    {"session_id": "ABCDEFGH"}, {"session_id": "abcdefg"}, {"session_id": "a" * 101},
    {"session_id": "_abcdefgh"}, {"session_id": "-abcdefgh"}, {"session_id": "abcd efgh"},
    {"session_id": "abcd.efgh"}, {}, {"session_id": None}, {"session_id": ["abcdefgh"]},
    {"session_id": {"id": "abcdefgh"}}, {"session_id": 12345678},
    {"session_id": "abcdefgh", "label": "Session one"},
], ids=["uppercase", "7_chars", "101_chars", "leading_underscore", "leading_dash", "space",
        "dot", "missing", "null", "list", "object", "number", "extra_field"])
def test_malformed_id_is_422_validation_failed_with_a_receipt(db, payload):
    body = env(START, {"campaign_id": "A"}, payload)
    assert run(body) == (422, "validation_failed")
    stored = receipt(db, body["operation_id"])
    assert (stored.status, stored.code) == ("rejected", "validation_failed")
    assert rows(db) == {} and events(db) == []


@pytest.mark.parametrize("existing, new, collide", [
    ("abcdefgh", "abcdefgh", True),  # a reused id
    ("a:b-session", "a-b-session", True),  # legacy ':' against the new form
    ("a.b-session", "a-b-session", False),  # '.' is not rewritten
    ("Session-One", "session-one", True),  # case only
    (" session-one", "session-one", True),  # a legacy leading space
    ("s" * 101, "s" * 100, True),  # a legacy id over 100: its 100-unit prefix
    ("s" * 99 + "x", "s" * 100, False),
])
@pytest.mark.parametrize("origin", ["command", "file_backfill"])
def test_reused_id_or_folder_collision_is_409_with_a_receipt(db, existing, new, collide, origin):
    add_row(db, existing, origin=origin)
    body = start_env(new)
    if not collide:
        assert run(body) == (200, None)
        assert rows(db)[new].status == "active"
        return
    assert run(body) == (409, "already_finalized")
    assert receipt(db, body["operation_id"]).code == "already_finalized"
    assert set(rows(db)) == {existing} and events(db) == []


def test_collision_is_checked_before_the_id_format(db):
    add_row(db, "abcdefgh")
    assert run(start_env("ABCDEFGH")) == (409, "already_finalized")  # check 6 before check 9


def test_rows_of_another_campaign_do_not_collide(db):
    add_row(db, SID, cid="B")
    assert run(start_env(SID)) == (200, None)


def test_second_active_session_is_422_rule_violation(db):
    assert run(start_env("first-session")) == (200, None)
    body = start_env("second-session")
    assert run(body) == (422, "rule_violation")
    assert receipt(db, body["operation_id"]).code == "rule_violation"
    assert set(rows(db)) == {"first-session"} and event_types(db) == ["session.started"]


def test_backfilled_file_era_id_is_409_after_reimport(db):
    history = LegacyHistory(ids=("s-file-era-01",), live_session=None, unreadable=(),
                            source_sha256="0" * 64)
    with db.database.transaction() as session:
        report = backfill_session_history(session, "A", history, frozenset())
    assert report.inserted == ("s-file-era-01",)
    assert run(start_env("s-file-era-01")) == (409, "already_finalized")
    assert run(start_env("S-File-Era-01")) == (409, "already_finalized")


def test_start_refusals_by_principal(db):
    assert run(start_env(), P1) == (403, "not_permitted")
    assert run(start_env(), T_END) == (403, "not_permitted")
    other = Principal.integration_token("t_b", campaign_id="B")
    assert run(start_env(), other) == (403, "not_permitted")  # check 1: another campaign's token
    assert run(env(START, {"campaign_id": "B"}, {"session_id": SID})) == (404, "not_found")
    assert rows(db) == {} and rows(db, "B") == {}


def test_gm_override_on_start_bypasses_no_start_rule(db):
    """C:137-140: an override never bypasses a check; ``session.start``'s rules ignore it."""
    add_row(db, "used-session")
    assert run(start_env("used-session", override="force")) == (409, "already_finalized")
    assert run(start_env("BAD", override="force")) == (422, "validation_failed")
    assert run(start_env("campaign-b-live", cid="B", override="force")) == (200, None)
    assert run(start_env("campaign-b-again", cid="B", override="force")) == (422, "rule_violation")
    assert run(start_env("campaign-a-live", override="force")) == (422, "rule_violation")
    assert set(rows(db)) == {"used-session"} and set(rows(db, "B")) == {"campaign-b-live"}


def test_start_replays_without_a_second_row(db):
    body = start_env()
    first, again = call(body), call(body)
    assert first.status == again.status == 200 and again.body["replayed"] is True
    assert len(rows(db)) == 1 and event_types(db) == ["session.started"]


@pytest.mark.parametrize("make", [start_env, end_env], ids=["start", "end"])
def test_replay_lists_the_session_record_and_event(db, make):
    if make is end_env:
        assert run(start_env()) == (200, None)
    body = make()
    first, again = call(body), call(body)
    assert again.body["replayed"] is True
    assert again.body["events"] == first.body["events"] != []
    assert set(again.body["result"]["records"]) == {f"session:{SID}"}


# --------------------------------------------------------------------------
# the PF2e slot


class SlotSpy:
    """Stands in for ``app``: any attribute read (PARTY_LIBRARY, load_campaign, ...) is recorded."""

    def __init__(self):
        self.read = []

    def __getattr__(self, name):
        self.read.append(name)
        raise AttributeError(name)


def test_pf2e_start_refused_while_another_pf2e_campaign_is_active(db, monkeypatch):
    spy = SlotSpy()
    monkeypatch.setitem(sys.modules, "app", spy)
    assert run(start_env("campaign-a-live")) == (200, None)
    body = start_env("campaign-b-live", cid="B")
    assert run(body) == (422, "rule_violation")
    assert receipt(db, body["operation_id"], cid="B").code == "rule_violation"
    assert rows(db, "B") == {} and set(rows(db)) == {"campaign-a-live"}
    # D&D is not in the PF2e slot.
    assert run(start_env("campaign-d-live", cid="D")) == (200, None)
    # Once A ends, B may start; nothing touched the process slot.
    assert run(end_env("campaign-a-live")) == (200, None)
    assert run(start_env("campaign-b-live", cid="B")) == (200, None)
    assert spy.read == []


def test_ended_and_backfilled_rows_do_not_hold_the_slot(db):
    add_row(db, "old-session", cid="B")
    add_row(db, "file-era", cid="C", origin="file_backfill")
    assert run(start_env()) == (200, None)


def test_active_dnd_session_does_not_block_pf2e(db):
    assert run(start_env("dnd-session", cid="D")) == (200, None)
    assert run(start_env()) == (200, None)


# --------------------------------------------------------------------------
# session.end


@pytest.mark.parametrize("principal", [GM, ADMIN, T_LIVE, T_END],
                         ids=["gm", "site_admin", "live_token", "scoped_token"])
def test_end_marks_the_row_ended(db, principal):
    assert run(start_env()) == (200, None)
    response = call(end_env(), principal)
    assert response.status == 200
    row = rows(db)[SID]
    assert (row.status, row.ended_by) == ("ended", principal_id(principal))
    assert row.ended_at is not None and row.started_by == "gm"
    assert event_types(db) == ["session.started", "session.ended"]
    assert events(db)[-1].session_id == SID
    assert response.body["result"]["records"][f"session:{SID}"]["status"] == "ended"


def test_end_missing_or_other_campaign_session_is_404(db):
    body = end_env("no-such-session")
    assert run(body) == (404, "not_found")
    assert receipt(db, body["operation_id"]).code == "not_found"
    add_row(db, SID, cid="B", status="active")
    assert run(end_env(SID)) == (404, "not_found")
    assert rows(db, "B")[SID].status == "active"


def test_session_rows_are_visible_to_gm_admin_and_this_campaigns_tokens_only(db):
    record = StateRecord.serialized("session", SID, {"campaign_id": "A", "session_id": SID})
    stray = StateRecord.serialized("session", SID, {"campaign_id": "B", "session_id": SID})
    with db.database.transaction() as session:
        def seen(principal, cid="A"):
            return load(session, principal, cid).visible(record)
        assert seen(GM) and seen(ADMIN) and seen(T_LIVE) and seen(T_START)
        assert not seen(P1)
        assert not seen(Principal.user("nobody"))
        assert not seen(Principal.integration_token("t_b", campaign_id="B"))
        assert not load(session, GM, "A").visible(stray)


def test_end_of_an_ended_session_is_409(db):
    add_row(db, SID)
    body = end_env()
    assert run(body) == (409, "already_finalized")
    assert receipt(db, body["operation_id"]).code == "already_finalized"


def test_end_with_a_payload_is_422_validation_failed(db):
    add_row(db, SID, status="active")
    assert run(env(END, {"session_id": SID}, {"session_id": SID})) == (422, "validation_failed")
    assert rows(db)[SID].status == "active"


def test_end_refused_for_a_player(db):
    add_row(db, SID, status="active")
    assert run(end_env(), P1) == (403, "not_permitted")
    assert run(end_env(), T_START) == (403, "not_permitted")
    assert rows(db)[SID].status == "active"


def _override_end(db, provider, principal):
    assert run(start_env()) == (200, None)
    before = db.transactions
    response = call(end_env(override="table ran late"), principal)
    assert response.status == 200
    assert db.transactions == before + 1  # cancel, then end, in one transaction
    assert provider.calls == ["lock", "cancel_all"]  # check 9 is skipped with an override
    assert rows(db)[SID].status == "ended"
    stored = events(db)
    assert [e.type for e in stored] == ["session.started", "action.cancelled", "session.ended"]
    assert stored[1].action_id == "act-1"
    assert {"override": {"reason": "table ran late"}} in stored[-1].explain
    applied = [r for r in _receipts(db) if r.status == "committed"][-1]
    assert applied.event_ids == [e.event_id for e in stored[1:]]


def test_session_end_gm_override_cancels_then_ends(db, provider):
    _override_end(db, provider, GM)


def test_session_end_site_admin_override_cancels_then_ends(db, provider):
    _override_end(db, provider, ADMIN)


def _receipts(db):
    with db.database.session() as session:
        return list(session.scalars(select(CommandReceipt).order_by(CommandReceipt.seq_first)))


def test_session_end_override_scoped_token_refused_403_nothing_cancelled(db, provider):
    assert run(start_env()) == (200, None)
    assert run(end_env(override="table ran late"), T_OVERRIDE) == (403, "not_permitted")
    assert "cancel_all" not in provider.calls and "open_actions" not in provider.calls
    assert rows(db)[SID].status == "active"
    assert event_types(db) == ["session.started"]


def test_session_end_scoped_token_without_override_open_actions_422(db, provider, monkeypatch):
    assert run(start_env()) == (200, None)
    refused = []
    real = executor._failure

    def record(exc, operation_id):
        refused.append(exc)
        return real(exc, operation_id)
    monkeypatch.setattr(executor, "_failure", record)
    body = end_env()
    assert run(body, T_END) == (422, "rule_violation")
    assert refused[-1].actions == ["act-1"]  # the refusal names the open action
    assert receipt(db, body["operation_id"], "integration_token", "t_end").code == "rule_violation"
    assert "cancel_all" not in provider.calls
    assert rows(db)[SID].status == "active" and event_types(db) == ["session.started"]


def test_session_end_scoped_token_no_open_actions_ends(db):
    assert isinstance(sessions.OPEN_ACTIONS.provider, sessions.NoActionTable)
    assert run(start_env()) == (200, None)
    assert run(end_env(), T_END) == (200, None)
    assert rows(db)[SID].status == "ended"


# --------------------------------------------------------------------------
# session ids on events


def test_lifecycle_events_carry_session_id(db, provider):
    assert run(start_env()) == (200, None)
    assert run(end_env(override="cancel the open action")) == (200, None)
    stored = events(db)
    assert [(e.type, e.session_id) for e in stored] == [
        ("session.started", SID), ("action.cancelled", SID), ("session.ended", SID)]


def test_event_after_session_end_has_null_session(db):
    assert run(start_env()) == (200, None)
    assert run(env(SET_QUARTERMASTER, {"campaign_id": "A"}, {"user_id": "p1"})) == (200, None)
    assert run(end_env()) == (200, None)
    assert run(env("grant.revoke", {"grant_id": _grant_id(db)}, {})) == (200, None)
    assert [(e.type, e.session_id) for e in events(db)] == [
        ("session.started", SID), ("grant.issued", SID), ("session.ended", SID),
        ("grant.revoked", None)]


def _grant_id(db):
    from core.persistence.models import CampaignGrant
    with db.database.session() as session:
        return session.scalar(select(CampaignGrant.grant_id))
