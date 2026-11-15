"""PE-13b: checks 4-9, the ``expected`` model and the conflict body."""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace

import pytest

from core.commands import checks
from core.commands.checks import (
    CheckContext,
    CheckOrderError,
    CheckRunner,
    CheckSpec,
    CommandRefusal,
    ExpectedKind,
    run_checks,
)
from core.commands.conflicts import conflict_body
from core.persistence.command_locks import CommandLocks, LockOrderError
from core.persistence.models import Campaign, CampaignStateAuthority, Character, User
from core.persistence.state_repositories import (
    CharacterStateRepository,
    EncounterStateRepository,
    StateRecord,
)

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
PIN = {"profile": "pf2e", "content": CONTENT}
SHA = "a" * 64
DND_CONTENT = {"data_version": "2026.10", "db_sha256": SHA}
DND_PIN = {"profile": "dnd5e-2024", "content": DND_CONTENT}
EDITIONS = {"dnd5e-2014": "classic", "dnd5e-2024": "one"}


# --------------------------------------------------------------------------
# fakes


def rec(kind, id, revision=1, **payload):
    return StateRecord(kind, id, revision, 1, "pf2e", CONTENT, dict(payload))


def same_id(ctx, subject, reference):
    return reference.id == subject.id


def item_named_by_request(ctx, subject, reference):
    return reference.id in ctx.envelope.get("payload", {}).get("items", ())


class FakeLocks:
    def __init__(self, records, campaign_id="A", session=None):
        self.session = session if session is not None else object()
        self.campaign_id = campaign_id
        self.records = MappingProxyType(dict(records))


class FakeAuthorizer:
    def __init__(self, log=None, *, role=True, override=False, hidden=(), subject_role=True):
        self.log = [] if log is None else log
        self.role, self.override, self.subject_role = role, override, subject_role
        self.hidden = set(hidden)

    def campaign_role_allows(self, type):
        self.log.append("campaign_role")
        return self.role

    def override_allowed(self, type):
        self.log.append("override")
        return self.override

    def visible(self, record):
        self.log.append("visible")
        return (record.kind, record.id) not in self.hidden

    def subject_role_allows(self, type, subject):
        self.log.append("subject_role")
        return self.subject_role


class FakeAdapter:
    def __init__(self, log=None, *, edition=True, refuse=None):
        self.log = [] if log is None else log
        self.edition, self.refuse, self.calls = edition, refuse, []

    def build_edition_matches(self, subject_record, pin):
        self.log.append("edition")
        return self.edition(subject_record, pin) if callable(self.edition) else self.edition

    def check(self, ctx, override):
        self.log.append("adapter")
        self.calls.append(override)
        if self.refuse:
            raise CommandRefusal(self.refuse)


def project(principal, records):
    """Stub projection: drops rows marked hidden in their payload."""

    return {f"{kind}:{id}": {"hp": record.payload.get("hp")}
            for (kind, id), record in records.items()
            if not record.payload.get("projection_hidden")}


KINDS = {**checks.DEFAULT_KINDS,
         "item": ExpectedKind("item", "rev", lambda record: record.revision),
         "request": ExpectedKind("request", "rev", lambda record: record.revision)}


def ctx_for(records, envelope, authorizer=None, *, pin=PIN, content=CONTENT, flags=True,
            emit=None):
    locks = FakeLocks(records)
    return CheckContext(
        session=locks.session, locks=locks, principal="principal", campaign_id="A",
        envelope=envelope, authorizer=authorizer or FakeAuthorizer(), project=project,
        content_descriptor=content, pin=pin, emit=emit,
        envelope_checked=flags, fence_passed=flags, receipt_absent=flags,
    )


def envelope(subject, *, expected=(), payload=None, rules=None, override=None, **extra):
    body = {"subject": subject, "expected": list(expected), "payload": payload or {},
            "rules": rules if rules is not None else {"profile": "pf2e"}, **extra}
    if override is not None:
        body["override"] = override
    return body


def run(spec, ctx, adapter=None, kinds=KINDS):
    CheckRunner(spec, adapter or FakeAdapter(), kinds).run(ctx)


def refusal(spec, ctx, adapter=None, kinds=KINDS):
    with pytest.raises(CommandRefusal) as info:
        run(spec, ctx, adapter, kinds)
    return info.value


def signature(exc):
    return (type(exc), exc.args, str(exc), exc.code, exc.http_status, exc.conflicts,
            exc.current)


DAMAGE = CheckSpec("character.play.apply_damage", ("character_id", "character.play"),
                   dependencies={"character.play": same_id})
PUBLISH = CheckSpec("character.build.publish", ("character_id", "character.build"),
                    dependencies={"character.build": same_id}, derives_rules=True)
FINALIZE = CheckSpec("loot.finalize", ("item_id", "item"),
                     dependencies={"item": same_id, "clock": lambda ctx, s, r: True},
                     payload_ids={"recipient": "character.play"})
APPROVE = CheckSpec("purchase.approve", ("request_id", "request"),
                    dependencies={"request": same_id},
                    terminal=lambda ctx, subject: subject.payload["status"] == "fulfilled")
INPUT = CheckSpec("action.input", ("action_id", "action"),
                  dependencies={"prompt": lambda ctx, subject, ref:
                                ref.payload["action_id"] == subject.id})


# --------------------------------------------------------------------------
# check order (worked examples 2-4)


def test_failing_visibility_and_revision_gives_404():
    # Example 4: the QM loses the finalize and holds no role on Bo's character.
    records = {("item", "L"): rec("item", "L", 4, owner="ana"),
               ("character.play", "bo"): rec("character.play", "bo")}
    ctx = ctx_for(records, envelope({"item_id": "L"},
                                    expected=[{"kind": "item", "id": "L", "rev": 3}],
                                    payload={"recipient": "bo"}),
                  FakeAuthorizer(hidden={("item", "L")}))
    assert refusal(FINALIZE, ctx).code == "not_found"


def test_failing_visibility_and_rules_gives_404_without_running_check_9():
    adapter = FakeAdapter(refuse="target_invalid")
    records = {("item", "L"): rec("item", "L", 3), ("character.play", "bo"): None}
    ctx = ctx_for(records, envelope({"item_id": "L"}, payload={"recipient": "bo"}))
    exc = refusal(FINALIZE, ctx, adapter)
    assert (exc.code, exc.http_status) == ("not_found", 404) and adapter.calls == []


def test_failing_terminal_and_revision_gives_already_finalized():
    # Example 3: the Obsidian approval arrives after op-A fulfilled request R.
    records = {("request", "R"): rec("request", "R", 2, status="fulfilled")}
    ctx = ctx_for(records, envelope({"request_id": "R"},
                                    expected=[{"kind": "request", "id": "R", "rev": 1}]))
    exc = refusal(APPROVE, ctx)
    assert (exc.code, exc.http_status) == ("already_finalized", 409)


def test_failing_revision_and_rules_gives_stale_revision():
    # Example 4: the GM can still see L (now Ana's); residency is check 9.
    adapter = FakeAdapter(refuse="rule_violation")
    records = {("item", "L"): rec("item", "L", 4, owner="ana"),
               ("character.play", "bo"): rec("character.play", "bo")}
    ctx = ctx_for(records, envelope({"item_id": "L"},
                                    expected=[{"kind": "item", "id": "L", "rev": 3}],
                                    payload={"recipient": "bo"}))
    exc = refusal(FINALIZE, ctx, adapter)
    assert exc.code == "stale_revision" and adapter.calls == []
    assert exc.conflicts == [{"kind": "item", "id": "L", "expected": 3, "current": 4}]


def test_second_tab_gets_stale_revision_while_intent_command_passes():
    # Example 2: the build is at rev 5 after the publish; play moved too.
    records = {("character.build", "ana"): rec("character.build", "ana", 5),
               ("character.play", "ana"): rec("character.play", "ana", 22)}
    stale = ctx_for(records, envelope(
        {"character_id": "ana"}, expected=[{"kind": "character.build", "id": "ana", "rev": 4}]))
    assert refusal(PUBLISH, stale).code == "stale_revision"
    adapter = FakeAdapter()
    run(DAMAGE, ctx_for(records, envelope({"character_id": "ana"}, payload={"amount": 7})),
        adapter)
    assert adapter.calls == [None]


# --------------------------------------------------------------------------
# check 5: one identical 404, and no revision compared before it passes


def _not_found_cases():
    base = {("item", "L"): rec("item", "L", 3),
            ("character.play", "bo"): rec("character.play", "bo"),
            ("item", "M"): rec("item", "M", 1),
            ("clock", "world"): rec("clock", "world", 9)}
    item_l = [{"kind": "item", "id": "L", "rev": 3}]
    return {
        "hidden subject": (base, envelope({"item_id": "L"}, expected=item_l,
                                          payload={"recipient": "bo"}),
                           {("item", "L")}),
        "hidden expected reference": (
            base, envelope({"item_id": "L"},
                           expected=item_l + [{"kind": "clock", "id": "world", "rev": 1}],
                           payload={"recipient": "bo"}),
            {("clock", "world")}),
        "missing payload id": ({**base, ("character.play", "bo"): None},
                               envelope({"item_id": "L"}, payload={"recipient": "bo"}), ()),
        "hidden payload id": (base, envelope({"item_id": "L"}, payload={"recipient": "bo"}),
                              {("character.play", "bo")}),
        "wrong-relation reference": (
            base, envelope({"item_id": "L"}, expected=[{"kind": "item", "id": "M", "rev": 1}],
                           payload={"recipient": "bo"}), ()),
        "subject registered as None": ({**base, ("item", "L"): None},
                                       envelope({"item_id": "L"}), ()),
    }


def test_every_not_found_cause_gives_an_identical_body():
    bodies = {}
    for name, (records, body, hidden) in _not_found_cases().items():
        exc = refusal(FINALIZE, ctx_for(records, body, FakeAuthorizer(hidden=hidden)))
        bodies[name] = signature(exc)
    assert len(set(bodies.values())) == 1, bodies
    assert next(iter(bodies.values()))[3:] == ("not_found", 404, None, None)


class RevisionSpy:
    """A revision that logs every comparison, truth test, hash and conversion."""

    def __init__(self, value, log):
        self.value, self.log = value, log

    def _touch(self, other=None):
        self.log.append("revision")
        return other.value if isinstance(other, RevisionSpy) else other

    def __eq__(self, other): return self.value == self._touch(other)
    def __ne__(self, other): return self.value != self._touch(other)
    def __lt__(self, other): return self.value < self._touch(other)
    def __le__(self, other): return self.value <= self._touch(other)
    def __gt__(self, other): return self.value > self._touch(other)
    def __ge__(self, other): return self.value >= self._touch(other)
    def __bool__(self): self._touch(); return bool(self.value)
    def __hash__(self): self._touch(); return hash(self.value)
    def __int__(self): self._touch(); return self.value
    def __index__(self): self._touch(); return self.value


def _spied(records, log):
    return {key: None if record is None else
            dataclasses.replace(record, revision=RevisionSpy(record.revision, log))
            for key, record in records.items()}


@pytest.mark.parametrize("case", list(_not_found_cases()))
def test_no_revision_is_touched_when_check_5_fails(case):
    records, body, hidden = _not_found_cases()[case]
    log = []
    ctx = ctx_for(_spied(records, log), body, FakeAuthorizer(log, hidden=hidden))
    assert refusal(FINALIZE, ctx).code == "not_found"
    assert "revision" not in log and "subject_role" not in log


def test_no_revision_is_touched_before_check_5_passes():
    log = []
    records = _spied({("item", "L"): rec("item", "L", 4),
                      ("character.play", "bo"): rec("character.play", "bo")}, log)
    ctx = ctx_for(records, envelope({"item_id": "L"},
                                    expected=[{"kind": "item", "id": "L", "rev": 3}],
                                    payload={"recipient": "bo"}),
                  FakeAuthorizer(log))
    assert refusal(FINALIZE, ctx).code == "stale_revision"
    assert "revision" in log
    assert log.index("revision") > log.index("subject_role")


def test_visible_subject_without_role_is_403_and_nothing_runs():
    # Example 1: a condition on a character Ana can still see.
    log, emitted = [], []
    adapter = FakeAdapter(log)
    records = {("character.play", "ana"): rec("character.play", "ana")}
    spec = dataclasses.replace(DAMAGE, terminal=lambda ctx, s: log.append("terminal"))
    ctx = ctx_for(records, envelope({"character_id": "ana"}),
                  FakeAuthorizer(log, subject_role=False), emit=emitted.append)
    exc = refusal(spec, ctx, adapter)
    assert (exc.code, exc.http_status) == ("not_permitted", 403)
    assert log[-1] == "subject_role" and emitted == [] and adapter.calls == []


# --------------------------------------------------------------------------
# override (Decision 9)


def test_override_from_a_principal_not_allowed_is_403_before_check_5():
    log, emitted = [], []
    records = {("character.play", "ana"): rec("character.play", "ana")}
    ctx = ctx_for(records, envelope({"character_id": "ana"}, override={"reason": "x"}),
                  FakeAuthorizer(log), emit=emitted.append)
    exc = refusal(DAMAGE, ctx)
    assert exc.code == "not_permitted" and log == ["campaign_role", "override"]
    assert emitted == []


def _override_cases():
    ana = {("character.build", "ana"): rec("character.build", "ana", 5, edition="one")}
    old = [{"kind": "character.build", "id": "ana", "rev": 4}]
    current = [{"kind": "character.build", "id": "ana", "rev": 5}]
    return {
        "4 role": (dict(role=False), ana, current, {}, "not_permitted"),
        "5 visibility": (dict(hidden={("character.build", "ana")}), ana, current, {},
                         "not_found"),
        "5 subject role": (dict(subject_role=False), ana, current, {}, "not_permitted"),
        "6 terminal": ({}, ana, current, dict(terminal=lambda ctx, s: True),
                       "already_finalized"),
        "7 revision": ({}, ana, old, {}, "stale_revision"),
        "8 rules": ({}, ana, current, dict(rules={"profile": "dnd5e-2014"}),
                    "rules_mismatch"),
        "8 content": ({}, ana, current, dict(content={"ruleset_id": "x"}),
                      "content_mismatch"),
        "8 edition": ({}, ana, current, dict(edition=False), "edition_mismatch"),
    }


@pytest.mark.parametrize("case", list(_override_cases()))
def test_allowed_override_bypasses_none_of_checks_4_to_8(case):
    auth, records, expected, extra, code = _override_cases()[case]
    adapter = FakeAdapter(edition=extra.get("edition", True))
    spec = dataclasses.replace(PUBLISH, terminal=extra.get("terminal", PUBLISH.terminal))
    body = envelope({"character_id": "ana"}, expected=expected, override={"reason": "gm"},
                    rules=extra.get("rules"))
    ctx = ctx_for(records, body, FakeAuthorizer(override=True, **auth),
                  content=extra.get("content", CONTENT))
    assert refusal(spec, ctx, adapter).code == code and adapter.calls == []


def test_allowed_override_reaches_check_9():
    adapter = FakeAdapter()
    records = {("character.play", "ana"): rec("character.play", "ana")}
    run(DAMAGE, ctx_for(records, envelope({"character_id": "ana"},
                                          override={"reason": "gm"}),
                        FakeAuthorizer(override=True)), adapter)
    assert adapter.calls == [{"reason": "gm"}]


# --------------------------------------------------------------------------
# check 7 and the conflict body


def test_stale_revision_lists_every_mismatch_and_matches_the_contract_body():
    spec = CheckSpec("character.rebuild", ("character_id", "character.build"),
                     dependencies={"character.build": same_id, "character.play": same_id,
                                   "clock": lambda ctx, s, r: True})
    records = {("character.build", "c7"): rec("character.build", "c7", 5, hp=1),
               ("character.play", "c7"): rec("character.play", "c7", 22, hp=24),
               ("clock", "world"): rec("clock", "world", 9, projection_hidden=True)}
    body = envelope({"character_id": "c7"}, expected=[
        {"kind": "character.build", "id": "c7", "rev": 4},
        {"kind": "clock", "id": "world", "rev": 8},
        {"kind": "character.play", "id": "c7", "rev": 21}])
    exc = refusal(spec, ctx_for(records, body))
    assert conflict_body("op-1", exc.conflicts, exc.current) == {
        "ok": False, "code": "stale_revision", "operation_id": "op-1",
        "conflicts": [{"kind": "character.build", "id": "c7", "expected": 4, "current": 5},
                      {"kind": "character.play", "id": "c7", "expected": 21, "current": 22}],
        "current": {"character.build:c7": {"hp": 1}, "character.play:c7": {"hp": 24}},
    }
    assert exc.http_status == 409


def test_conflict_body_has_exactly_the_contract_keys():
    body = conflict_body("op-2", [{"kind": "clock", "id": "world", "expected": 1,
                                   "current": 2, "extra": "dropped"}], {"clock:world": {}})
    assert list(body) == ["ok", "code", "operation_id", "conflicts", "current"]
    assert body["conflicts"] == [{"kind": "clock", "id": "world", "expected": 1, "current": 2}]


def test_prompt_entry_passes_while_open_and_conflicts_once_resolved():
    records = {("action", "x1"): rec("action", "x1", 3),
               ("prompt", "p1"): rec("prompt", "p1", 1, action_id="x1", status="open")}
    body = envelope({"action_id": "x1"},
                    expected=[{"kind": "prompt", "id": "p1", "status": "open"}])
    run(INPUT, ctx_for(records, body))
    records[("prompt", "p1")] = rec("prompt", "p1", 2, action_id="x1", status="resolved")
    exc = refusal(INPUT, ctx_for(records, body))
    assert exc.conflicts == [{"kind": "prompt", "id": "p1", "expected": "open",
                              "current": "resolved"}]
    # A client copying the row's status does not get past the open-prompt rule.
    copied = envelope({"action_id": "x1"},
                      expected=[{"kind": "prompt", "id": "p1", "status": "resolved"}])
    assert signature(refusal(INPUT, ctx_for(records, copied)))[5] == exc.conflicts


def test_two_prompt_inputs_to_one_action_do_not_conflict():
    # G1's save resolves p1 and bumps the action; G2's input still expects only p2.
    records = {("action", "x1"): rec("action", "x1", 4),
               ("prompt", "p1"): rec("prompt", "p1", 2, action_id="x1", status="resolved"),
               ("prompt", "p2"): rec("prompt", "p2", 1, action_id="x1", status="open")}
    adapter = FakeAdapter()
    run(INPUT, ctx_for(records, envelope(
        {"action_id": "x1"}, expected=[{"kind": "prompt", "id": "p2", "status": "open"}])),
        adapter)
    assert adapter.calls == [None]


# --------------------------------------------------------------------------
# check 8 pins


def test_rules_mismatch_wins_over_content_mismatch():
    records = {("character.build", "ana"): rec("character.build", "ana", 5)}
    ctx = ctx_for(records, envelope({"character_id": "ana"},
                                    rules={"profile": "dnd5e-2024"}),
                  content={"ruleset_id": "other", "manifest_sha256": SHA})
    exc = refusal(PUBLISH, ctx)
    assert (exc.code, exc.http_status) == ("rules_mismatch", 409)


def test_content_mismatch_only_for_rule_deriving_types():
    records = {("character.build", "ana"): rec("character.build", "ana", 5),
               ("character.play", "ana"): rec("character.play", "ana", 5)}
    other = {"ruleset_id": "other", "manifest_sha256": SHA}
    exc = refusal(PUBLISH, ctx_for(records, envelope({"character_id": "ana"}), content=other))
    assert (exc.code, exc.http_status) == ("content_mismatch", 503)
    adapter = FakeAdapter()
    run(DAMAGE, ctx_for(records, envelope({"character_id": "ana"}), content=other), adapter)
    assert adapter.calls == [None]


def test_rules_carrying_content_must_match_the_pin_content():
    records = {("character.play", "ana"): rec("character.play", "ana")}
    body = envelope({"character_id": "ana"},
                    rules={"profile": "pf2e", "content": {"ruleset_id": "x"}})
    assert refusal(DAMAGE, ctx_for(records, body)).code == "rules_mismatch"


# --------------------------------------------------------------------------
# preconditions


@pytest.mark.parametrize("flag", ["envelope_checked", "fence_passed", "receipt_absent"])
def test_unset_flag_is_a_check_order_error(flag):
    log = []
    records = {("character.play", "ana"): rec("character.play", "ana")}
    ctx = ctx_for(records, envelope({"character_id": "ana"}), FakeAuthorizer(log))
    setattr(ctx, flag, False)
    with pytest.raises(CheckOrderError, match=flag):
        run(DAMAGE, ctx)
    assert log == []


@pytest.mark.parametrize("missing", ["subject", "payload id", "expected reference"])
def test_a_reference_lock_plan_did_not_lock_is_a_check_order_error(missing):
    records = {("item", "L"): rec("item", "L", 3),
               ("character.play", "bo"): rec("character.play", "bo"),
               ("item", "M"): rec("item", "M")}
    drop = {"subject": ("item", "L"), "payload id": ("character.play", "bo"),
            "expected reference": ("item", "M")}[missing]
    del records[drop]
    body = envelope({"item_id": "L"}, expected=[{"kind": "item", "id": "M", "rev": 1}],
                    payload={"recipient": "bo"})
    with pytest.raises(CheckOrderError, match="not locked"):
        run(FINALIZE, ctx_for(records, body, FakeAuthorizer(hidden={("item", "L")})))


def test_payload_id_locked_under_another_kind_is_a_check_order_error():
    records = {("item", "L"): rec("item", "L", 3),
               ("character.build", "bo"): rec("character.build", "bo")}
    with pytest.raises(CheckOrderError):
        run(FINALIZE, ctx_for(records, envelope({"item_id": "L"}, payload={"recipient": "bo"})))


def test_a_record_registered_under_another_key_is_a_check_order_error():
    records = {("item", "L"): rec("item", "M", 3)}
    with pytest.raises(CheckOrderError, match="another record"):
        run(FINALIZE, ctx_for(records, envelope({"item_id": "L"})))


def test_a_campaign_subject_must_be_this_campaign():
    spec = CheckSpec("session.start", ("campaign_id", "campaign"))
    records = {("campaign", "B"): StateRecord.serialized("campaign", "B", {})}
    assert refusal(spec, ctx_for(records, envelope({"campaign_id": "B"}))).code == "not_found"


def test_expected_kind_outside_the_dependency_set_is_a_check_order_error():
    records = {("character.play", "ana"): rec("character.play", "ana"),
               ("clock", "world"): rec("clock", "world")}
    body = envelope({"character_id": "ana"},
                    expected=[{"kind": "clock", "id": "world", "rev": 1}])
    with pytest.raises(CheckOrderError):
        run(DAMAGE, ctx_for(records, body))


@pytest.mark.parametrize("adapter", [None, object(), SimpleNamespace(check=lambda c, o: None)])
def test_runner_requires_a_complete_rules_adapter(adapter):
    with pytest.raises(ValueError):
        CheckRunner(DAMAGE, adapter)
    with pytest.raises(TypeError):
        CheckRunner(DAMAGE)


def test_run_checks_uses_the_registered_kinds():
    adapter = FakeAdapter()
    records = {("character.play", "ana"): rec("character.play", "ana", 3)}
    body = envelope({"character_id": "ana"},
                    expected=[{"kind": "character.play", "id": "ana", "rev": 3}])
    run_checks(ctx_for(records, body), DAMAGE, adapter)
    assert adapter.calls == [None]
    stale = envelope({"character_id": "ana"},
                     expected=[{"kind": "character.play", "id": "ana", "rev": 2}])
    with pytest.raises(CommandRefusal, match="stale_revision"):
        run_checks(ctx_for(records, stale), DAMAGE, adapter)


def test_register_expected_kind_adds_and_refuses_duplicates(monkeypatch):
    monkeypatch.setattr(checks, "_KINDS", dict(checks.DEFAULT_KINDS))
    wallet = ExpectedKind("wallet", "rev", lambda record: record.revision)
    checks.register_expected_kind(wallet)
    assert checks.expected_kinds()["wallet"] is wallet
    with pytest.raises(ValueError):
        checks.register_expected_kind(wallet)
    with pytest.raises(ValueError):
        checks.register_expected_kind(ExpectedKind("clock", "rev", lambda r: r.revision))


# --------------------------------------------------------------------------
# CommandRefusal and the code table (C:352-366)

CODE_TABLE = {
    "bad_envelope": 400, "operation_id_reused": 409, "not_found": 404,
    "already_finalized": 409, "stale_revision": 409, "rules_mismatch": 409,
    "item_locked": 409, "insufficient_funds": 409, "insufficient_stock": 409,
    "insufficient_quantity": 409, "abandoned": 409, "validation_failed": 422,
    "rule_violation": 422, "not_live": 422, "in_encounter": 422, "unlock_required": 422,
    "edition_mismatch": 422, "target_invalid": 422, "not_permitted": 403,
    "resync_required": 409, "migration_in_progress": 503, "authority_unavailable": 503,
    "content_mismatch": 503, "temporarily_unavailable": 503, "server_error": 500,
}


def test_command_refusal_covers_the_code_table_exactly():
    assert dict(checks.CODES) == CODE_TABLE
    for code, status in CODE_TABLE.items():
        exc = CommandRefusal(code)
        assert (exc.code, exc.http_status, exc.conflicts, exc.current) == (code, status,
                                                                          None, None)
    with pytest.raises(ValueError):
        CommandRefusal("teapot")


# --------------------------------------------------------------------------
# composing with PE-13a's real CommandLocks


def _seed(database):
    """Campaign A (PF2e) and D (D&D 2024), with characters, states and encounter e1."""

    with database.transaction() as session:
        session.add(User(id="gm", username="gm", normalized_username="gm",
                         display_name="gm", password_hash="h", created_at=NOW))
        session.flush()
        for cid, pin in (("A", PIN), ("B", PIN), ("D", DND_PIN)):
            session.add(Campaign(id=cid, slug=cid, name=cid, system=pin["profile"][:5],
                                 created_by_user_id="gm", created_at=NOW,
                                 settings={"rules": pin}))
        session.flush()
        for character_id, cid in (("a1", "A"), ("a2", "A"), ("b1", "B"),
                                  ("d1", "D"), ("d2", "D")):
            session.add(Character(id=character_id, campaign_id=cid, system="pf2e",
                                  display_name=character_id, legacy_storage="party_data",
                                  legacy_file=character_id + ".json",
                                  content_checksum="0" * 64, created_at=NOW,
                                  updated_at=NOW))
        for cid in ("A", "B", "D"):
            session.add(CampaignStateAuthority(campaign_id=cid, authority="db",
                                               stream_epoch=1, changed_at=NOW))
        session.flush()
        states = CharacterStateRepository(session)
        for cid, character_id in (("A", "a1"), ("A", "a2"), ("B", "b1")):
            states.insert(cid, character_id, "play", rules_profile="pf2e", content=CONTENT,
                          payload={"hp": 10})
        for character_id, edition in (("d1", "one"), ("d2", "classic")):
            states.insert("D", character_id, "build", rules_profile="dnd5e-2024",
                          content=DND_CONTENT, payload={"edition": edition})
        encounters = EncounterStateRepository(session)
        for eid, status in (("e1", "active"), ("e2", "ended")):
            encounters.insert("A", eid, rules_profile="pf2e", content=CONTENT, status=status)
        encounters.add_combatant("A", "e1", read_revision=1, combatant_id="g1",
                                 kind="creature", creature_ref="goblin")
        encounters.add_combatant("A", "e1", read_revision=2, combatant_id="pc1",
                                 kind="pc", character_id="a1")
        encounters.add_combatant("A", "e2", read_revision=1, combatant_id="g9",
                                 kind="creature", creature_ref="goblin")


def _locks(session, campaign_id="A"):
    locks = CommandLocks(session, campaign_id, lock_stream=lambda s, c: {"campaign_id": c})
    locks.stream()
    return locks


def _real_ctx(session, locks, body, authorizer=None, *, pin=PIN, campaign_id="A"):
    return CheckContext(session=session, locks=locks, principal="principal",
                        campaign_id=campaign_id, envelope=body,
                        authorizer=authorizer or FakeAuthorizer(), project=project,
                        content_descriptor=pin["content"], pin=pin, envelope_checked=True,
                        fence_passed=True, receipt_absent=True)


def test_check_context_records_are_lock_records(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        ctx = _real_ctx(session, locks, envelope({"character_id": "a1"}))
        assert ctx.records is locks.records
        locks.character_states([("a1", "play")])
        assert ctx.records[("character.play", "a1")].payload == {"hp": 10}
        assert (ctx.emit, ctx.session_id, ctx.bind_started_session) == (None, None, None)
        defaults = CheckContext(session=session, locks=locks, principal="p",
                                campaign_id="A", envelope={}, authorizer=FakeAuthorizer(),
                                project=project, content_descriptor=CONTENT, pin=PIN)
        assert (defaults.envelope_checked, defaults.fence_passed,
                defaults.receipt_absent) == (False, False, False)


def test_check_context_refuses_mismatched_locks_and_a_missing_authorizer(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session, sqlite_database.session() as other:
        locks = _locks(session)
        with pytest.raises(CheckOrderError):
            _real_ctx(session, locks, {}, campaign_id="B")
        with pytest.raises(CheckOrderError):
            _real_ctx(other, locks, {})
        with pytest.raises(TypeError):
            CheckContext(session=session, locks=locks, principal="p", campaign_id="A",
                         envelope={}, project=project, content_descriptor=CONTENT, pin=PIN)


def test_real_locks_none_key_gives_the_same_404_as_a_hidden_record(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        locks.character_states([("a1", "play"), ("b1", "play")])  # b1 is campaign B's
        assert locks.records[("character.play", "b1")] is None
        missing = refusal(DAMAGE, _real_ctx(session, locks, envelope({"character_id": "b1"})))
        hidden = refusal(DAMAGE, _real_ctx(
            session, locks, envelope({"character_id": "a1"}),
            FakeAuthorizer(hidden={("character.play", "a1")})))
    assert signature(missing) == signature(hidden)


def test_serialized_subject_and_combatant_payload_ids_pass_check_5(sqlite_database):
    _seed(sqlite_database)
    declare = CheckSpec("action.declare", ("campaign_id", "campaign"),
                        payload_ids={"actor": "combatant", "targets": "combatant"})
    adapter = FakeAdapter()
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        locks.register_serialized("campaign", "A", StateRecord.serialized(
            "campaign", "A", {"name": "A"}))
        locks.encounter("e1")
        body = envelope({"campaign_id": "A"}, payload={"actor": "pc1", "targets": ["g1"]})
        run(declare, _real_ctx(session, locks, body), adapter)
        assert adapter.calls == [None]
        hidden = refusal(declare, _real_ctx(session, locks, body,
                                            FakeAuthorizer(hidden={("campaign", "A")})))
        assert hidden.code == "not_found"


def test_edition_mismatch_reads_the_build_written_through_pe13a(sqlite_database):
    _seed(sqlite_database)

    def matches(subject, pin):
        return subject.payload.get("edition") == EDITIONS[pin["profile"]]

    rebuild = CheckSpec("character.build.save", ("character_id", "character.build"))
    with sqlite_database.transaction() as session:
        locks = _locks(session, "D")
        locks.character_states([("d1", "build"), ("d2", "build")])
        assert locks.records[("character.build", "d2")].rules_profile == "dnd5e-2024"
        rules = {"profile": "dnd5e-2024"}
        adapter = FakeAdapter(edition=matches)
        run(rebuild, _real_ctx(session, locks, envelope({"character_id": "d1"}, rules=rules),
                               pin=DND_PIN, campaign_id="D"), adapter)
        exc = refusal(rebuild, _real_ctx(session, locks,
                                         envelope({"character_id": "d2"}, rules=rules),
                                         pin=DND_PIN, campaign_id="D"), adapter)
    assert (exc.code, exc.http_status) == ("edition_mismatch", 422)


# --------------------------------------------------------------------------
# CommandLocks.combatants (the PE-13a follow-up reconciled for check 5)

TARGET = CheckSpec("action.target", ("encounter_id", "encounter"),
                   payload_ids={"targets": "combatant"})


def test_combatants_accounts_for_every_requested_id(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        locks.encounter("e1")
        found = locks.combatants("e1", ["g1", "zz", "g9"])  # g9 is e2's
        assert found["g1"].payload["encounter_id"] == "e1"
        assert found["zz"] is None and found["g9"] is None
        assert locks.records[("combatant", "g9")] is None
        locks.character_states([("a1", "play")])  # took no lock: step order unchanged
        assert locks.combatants("e1", ["g1"])["g1"] is found["g1"]
        bodies = [signature(refusal(TARGET, _real_ctx(
            session, locks, envelope({"encounter_id": "e1"}, payload={"targets": [t]}))))
            for t in ("zz", "g9")]
        bodies.append(signature(refusal(TARGET, _real_ctx(
            session, locks, envelope({"encounter_id": "e1"}, payload={"targets": ["g1"]}),
            FakeAuthorizer(hidden={("combatant", "g1")})))))
        assert len(set(bodies)) == 1 and bodies[0][3] == "not_found"
        unplanned = envelope({"encounter_id": "e1"}, payload={"targets": ["never-asked"]})
        with pytest.raises(CheckOrderError):
            run(TARGET, _real_ctx(session, locks, unplanned))


def test_combatants_needs_its_encounter_locked_first(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        with pytest.raises(LockOrderError):
            locks.combatants("e1", ["g1"])
        locks.encounter("e1")
        with pytest.raises(LockOrderError):
            locks.combatants("e2", ["g9"])


def test_combatants_of_a_missing_encounter_are_all_none(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        assert locks.encounter("e0") is None
        assert locks.combatants("e0", ["g1"]) == {"g1": None}


def test_a_combatant_id_cannot_be_claimed_by_two_encounters(sqlite_database):
    _seed(sqlite_database)
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        locks.encounter("e1")
        locks.combatants("e1", ["g9"])  # absent from e1
        with pytest.raises(LockOrderError):
            locks.encounter("e2")  # would register e2's g9 over e1's None
    with sqlite_database.transaction() as session:
        locks = _locks(session)
        locks.encounter("e1")
        locks.encounter("e2")
        with pytest.raises(LockOrderError):
            locks.combatants("e1", ["g9"])  # registered under e2
