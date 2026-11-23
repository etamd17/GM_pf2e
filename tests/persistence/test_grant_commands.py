"""PE-14d: the four grant commands through ``execute`` on SQLite.

The real authorizer (``core.commands.authorizer.load``) decides checks 4 and
5; nothing here patches ``executor.authorizer_factory``.  Every campaign a
command runs against has a db ``campaign_state_authority`` row and a valid
``campaigns.settings["rules"]`` pin (a D&D pin for campaign D), and every
envelope names that pin's profile and the stream epoch.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core.commands import grant_commands, registry
from core.commands.executor import execute
from core.persistence import grants, runtime
from core.persistence.models import (
    Campaign,
    CampaignGrant,
    CampaignMembership,
    CampaignStateAuthority,
    Character,
    CharacterAssignment,
)
from core.persistence.state_repositories import CampaignPinRepository, CharacterStateRepository
from core.request_context import Principal, PrincipalKind

from .test_command_authorizer import _token, _user
from .test_command_executor import NOW, Tracked, counts, events, op, receipt, set_authority

CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
DND_CONTENT = {"data_version": "v1", "db_sha256": "a" * 64}
PINS = {"A": {"profile": "pf2e", "content": CONTENT}, "B": {"profile": "pf2e", "content": CONTENT},
        "D": {"profile": "dnd5e-2014", "content": DND_CONTENT}}
UNLOCK, RETRAIN = grant_commands.ISSUE_LEVEL_UNLOCK, grant_commands.ISSUE_RETRAIN
SET_QM, REVOKE = grant_commands.SET_QUARTERMASTER, grant_commands.REVOKE
GM, P1, P2, QM = (Principal.user(u) for u in ("gm", "p1", "p2", "qm"))
T_ISSUE, T_LIVE, T_ADMIN = (Principal.integration_token(c, campaign_id="A")
                            for c in ("t_issue", "t_live", "t_admin"))
MISSING = "f" * 32
# kind -> (type, subject, payload, the kind's own columns)
ISSUE = {
    "level_unlock": (UNLOCK, {"character_id": "a1"}, {"target_level": 2},
                     ("a1", 2, None, None)),
    "retrain_grant": (RETRAIN, {"character_id": "a1"}, {"build_revision": 1},
                      ("a1", None, 1, None)),
    "quartermaster": (SET_QM, {"campaign_id": "A"}, {"user_id": "p2"},
                      (None, None, None, "p2")),
}


@pytest.fixture
def db(sqlite_database, monkeypatch):
    with sqlite_database.transaction() as s:
        for uid in ("gm", "p1", "p2", "out", "qm"):
            _user(s, uid)
        s.flush()
        for cid, pin in PINS.items():
            s.add(Campaign(id=cid, slug=cid, name=cid, system=pin["profile"].split("-")[0],
                           created_by_user_id="gm", created_at=NOW, settings={"rules": pin}))
        s.flush()
        builds = (("a1", "A", {}), ("a2", "A", {}), ("b1", "B", {}),
                  ("d_one", "D", {"edition": "one"}), ("d_classic", "D", {"edition": "classic"}))
        for character_id, cid, _ in builds:
            system = PINS[cid]["profile"].split("-")[0]
            s.add(Character(id=character_id, campaign_id=cid, system=system,
                            display_name=character_id, legacy_file=character_id + ".json",
                            legacy_storage="party_data" if cid != "D" else "dnd5e_characters",
                            content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        for cid in PINS:
            s.add(CampaignStateAuthority(campaign_id=cid, authority="db", stream_epoch=1,
                                         changed_at=NOW))
            s.add(CampaignMembership(campaign_id=cid, user_id="gm", role="gm"))
        for cid, uid in (("A", "p1"), ("A", "p2"), ("A", "qm"), ("B", "out")):
            s.add(CampaignMembership(campaign_id=cid, user_id=uid, role="player"))
        s.flush()
        s.add(CharacterAssignment(campaign_id="A", character_id="a1", user_id="p1", role="owner"))
        _token(s, "t_issue", "A", [UNLOCK, RETRAIN])
        _token(s, "t_live", "A", ["@live"])
        _token(s, "t_admin", "A", [REVOKE, SET_QM, UNLOCK])
        s.flush()
        states = CharacterStateRepository(s)
        for character_id, cid, payload in builds:
            states.insert(cid, character_id, "build", rules_profile=PINS[cid]["profile"],
                          content=PINS[cid]["content"], payload=payload)
            # The play row is the record a character grant's event subject is judged by.
            states.insert(cid, character_id, "play", rules_profile=PINS[cid]["profile"],
                          content=PINS[cid]["content"], payload={"hp": 30})
    tracked = Tracked(sqlite_database)
    monkeypatch.setattr(runtime, "database", lambda: tracked)
    return tracked


def env(type, subject, payload, *, cid="A", operation_id=None):
    return {"schema": 1, "operation_id": operation_id or op(), "campaign_id": cid, "epoch": 1,
            "type": type, "subject": subject, "expected": [],
            "rules": {"profile": PINS[cid]["profile"]}, "payload": payload}


def project(principal, records):
    return {f"{kind}:{id}": dict(record.payload) for (kind, id), record in records.items()}


def held(authorizer, *, subjects, encounter_id):
    token = authorizer.principal_kind is PrincipalKind.INTEGRATION_TOKEN
    return frozenset({"gm_integration" if token else "gm"})


def run(body, principal=GM):
    response = execute(body, principal, route_campaign_id=body["campaign_id"],
                       content_descriptor=CONTENT, project=project, held_projections=held)
    return response.status, (response.body or {}).get("code")


def issue(db, kind="level_unlock", principal=GM, **changes):
    """Issue one grant and return its id (the newest row of that kind)."""

    type, subject, payload, _ = ISSUE[kind]
    body = env(type, changes.pop("subject", subject), changes.pop("payload", payload), **changes)
    assert run(body, principal) == (200, None)
    return rows(db, body["campaign_id"])[-1].grant_id


def rows(db, cid="A"):
    with db.database.session() as session:
        return list(session.scalars(select(CampaignGrant).where(
            CampaignGrant.campaign_id == cid).order_by(CampaignGrant.issued_at)))


def event_types(db, cid="A"):
    return [event.type for event in events(db, cid)]


# --------------------------------------------------------------------------
# the GM issues and revokes


@pytest.mark.parametrize("kind", list(ISSUE))
def test_gm_issues_replays_and_revokes(db, kind):
    type, subject, payload, columns = ISSUE[kind]
    body = env(type, subject, payload)
    first = execute(body, GM, route_campaign_id="A", content_descriptor=CONTENT,
                    project=project, held_projections=held)
    assert first.status == 200 and first.body["status"] == "committed"
    (row,) = rows(db)
    assert (row.kind, row.issued_by_kind, row.issued_by_id) == (kind, "user", "gm")
    assert (row.character_id, row.target_level, row.build_revision, row.user_id) == columns
    assert row.consumed_at is None and row.revoked_at is None
    assert event_types(db) == ["grant.issued"]
    assert receipt(db, body["operation_id"]).status == "committed"

    again = execute(body, GM, route_campaign_id="A", content_descriptor=CONTENT,
                    project=project, held_projections=held)
    assert again.status == 200 and again.body["replayed"] is True
    for field in ("ok", "operation_id", "status", "revisions", "watermark"):
        assert again.body[field] == first.body[field], field
    assert len(rows(db)) == 1 and event_types(db) == ["grant.issued"]

    assert run(env(REVOKE, {"grant_id": row.grant_id}, {})) == (200, None)
    (revoked,) = rows(db)
    assert revoked.revoked_at is not None and revoked.consumed_at is None
    assert event_types(db) == ["grant.issued", "grant.revoked"]
    # Every duplicate rule reads live grants only: the same grant can be issued again.
    assert run(env(type, subject, payload)) == (200, None)


# A grant record has no revision, so the receipt cannot name it: the replay
# re-reads the grant its events are about (``_Command.read_current``).
@pytest.mark.parametrize("kind", list(ISSUE))
def test_replay_returns_the_first_events_and_records(db, kind):
    body = env(*ISSUE[kind][:3])
    kwargs = dict(route_campaign_id="A", content_descriptor=CONTENT, project=project,
                  held_projections=held)
    first, again = execute(body, GM, **kwargs), execute(body, GM, **kwargs)
    assert first.body["events"] and first.body["result"]["records"]
    assert (again.body["events"], again.body["result"]) == (first.body["events"],
                                                            first.body["result"])


@pytest.mark.parametrize("kind", list(ISSUE))
def test_duplicate_live_grant_is_a_rule_violation(db, kind):
    type, subject, payload, _ = ISSUE[kind]
    issue(db, kind)
    assert run(env(type, subject, payload)) == (422, "rule_violation")
    assert len(rows(db)) == 1 and event_types(db) == ["grant.issued"]


@pytest.mark.parametrize("user_id", ["nobody", "out", "gm"],
                         ids=["unknown", "other_campaign_member", "the_gm"])
def test_set_quartermaster_needs_an_active_player_member(db, user_id):
    assert run(env(SET_QM, {"campaign_id": "A"}, {"user_id": user_id})) == (422, "rule_violation")
    assert rows(db) == [] and event_types(db) == []


@pytest.mark.parametrize("type, payload", [
    (UNLOCK, {}), (UNLOCK, {"target_level": 2, "build_revision": 1}),
    (UNLOCK, {"target_level": True}), (UNLOCK, {"target_level": 0}),
    (UNLOCK, {"target_level": "2"}), (RETRAIN, {"target_level": 2}),
    (RETRAIN, {"build_revision": -1}), (RETRAIN, {"build_revision": False}),
    (SET_QM, {}), (SET_QM, {"user_id": 7}), (SET_QM, {"user_id": "p2", "extra": 1}),
])
def test_malformed_payload_is_a_rule_violation(db, type, payload):
    subject = {"campaign_id": "A"} if type == SET_QM else {"character_id": "a1"}
    assert run(env(type, subject, payload)) == (422, "rule_violation")
    assert rows(db) == [] and event_types(db) == []


def test_revoke_takes_an_empty_payload(db):
    grant_id = issue(db)
    assert run(env(REVOKE, {"grant_id": grant_id}, {"reason": "x"})) == (422, "rule_violation")
    assert rows(db)[0].revoked_at is None


# --------------------------------------------------------------------------
# tokens, players and the quartermaster


def test_scoped_token_issues_unlock_and_retrain_only(db):
    for kind in ("level_unlock", "retrain_grant"):
        issue(db, kind, T_ISSUE)
    assert [(r.kind, r.issued_by_kind, r.issued_by_id) for r in rows(db)] == [
        ("level_unlock", "integration_token", "t_issue"),
        ("retrain_grant", "integration_token", "t_issue")]
    # "@live" names neither type, and a scope naming one type does not carry the other.
    for principal, type, payload in ((T_LIVE, UNLOCK, {"target_level": 3}),
                                     (T_LIVE, RETRAIN, {"build_revision": 3}),
                                     (T_ADMIN, RETRAIN, {"build_revision": 3})):
        assert run(env(type, {"character_id": "a1"}, payload), principal)[0] == 403
    assert run(env(UNLOCK, {"character_id": "a1"}, {"target_level": 3}), T_ADMIN) == (200, None)
    assert len(rows(db)) == 3


@pytest.mark.parametrize("token", [T_ADMIN, T_ISSUE, T_LIVE], ids=["named", "other", "live"])
def test_grant_revoke_and_set_quartermaster_refuse_token_at_check1(db, token):
    grant_id = issue(db)
    opened, before = db.transactions, counts(db)
    for type, subject, payload in ((SET_QM, {"campaign_id": "A"}, {"user_id": "p2"}),
                                   (REVOKE, {"grant_id": grant_id}, {})):
        body = env(type, subject, payload)
        assert run(body, token) == (403, "not_permitted")
        assert receipt(db, body["operation_id"], "integration_token", token.credential_id) is None
    assert db.transactions == opened and counts(db) == before  # check 1: no transaction
    assert [(r.kind, r.revoked_at) for r in rows(db)] == [("level_unlock", None)]


@pytest.mark.parametrize("principal", [QM, P1, P2], ids=["quartermaster", "owner", "player"])
def test_no_player_role_issues_or_revokes(db, principal):
    qm_grant = issue(db, "quartermaster", payload={"user_id": "qm"})
    for type, subject, payload in (
            (UNLOCK, {"character_id": "a1"}, {"target_level": 2}),
            (RETRAIN, {"character_id": "a1"}, {"build_revision": 1}),
            (SET_QM, {"campaign_id": "A"}, {"user_id": "p2"}),
            (REVOKE, {"grant_id": qm_grant}, {})):
        assert run(env(type, subject, payload), principal)[0] == 403, type
    assert [(r.kind, r.user_id, r.revoked_at) for r in rows(db)] == [("quartermaster", "qm", None)]
    assert event_types(db) == ["grant.issued"]


# --------------------------------------------------------------------------
# revoke: terminal and missing grants


@pytest.mark.parametrize("ended", ["revoked", "consumed"])
def test_revoking_a_finished_grant_is_already_finalized(db, ended):
    grant_id = issue(db)
    if ended == "revoked":
        assert run(env(REVOKE, {"grant_id": grant_id}, {})) == (200, None)
    else:
        with db.database.transaction() as session:
            grants.consume(session, grant_id, "op-consumer-0000000001", campaign_id="A")
    before = rows(db)[0]
    assert run(env(REVOKE, {"grant_id": grant_id}, {})) == (409, "already_finalized")
    after = rows(db)[0]
    assert (after.revoked_at, after.consumed_at) == (before.revoked_at, before.consumed_at)
    assert event_types(db).count("grant.revoked") == (1 if ended == "revoked" else 0)


def test_revoking_a_missing_or_other_campaign_grant_is_not_found(db):
    other = issue(db, subject={"character_id": "b1"}, cid="B")
    for grant_id in (MISSING, other):
        assert run(env(REVOKE, {"grant_id": grant_id}, {})) == (404, "not_found")
    assert rows(db) == [] and event_types(db) == []
    assert rows(db, "B")[0].revoked_at is None and event_types(db, "B") == ["grant.issued"]


# --------------------------------------------------------------------------
# subject registration, authority, registration and the edition rule


def test_grant_unlock_intent_registers_subject(db, monkeypatch):
    seen = []
    original = grant_commands._CharacterGrantRules.check

    def check(self, ctx, override):
        seen.append((ctx.envelope["expected"], ("character.build", "a1") in ctx.records))
        return original(self, ctx, override)

    monkeypatch.setattr(grant_commands._CharacterGrantRules, "check", check)
    # Empty ``expected``: check 5 still finds the subject (no CheckOrderError, which is a 500).
    assert run(env(UNLOCK, {"character_id": "a1"}, {"target_level": 2})) == (200, None)
    assert seen == [([], True)]
    # A character of another campaign registers None: 404 before any rule runs.
    assert run(env(UNLOCK, {"character_id": "b1"}, {"target_level": 2})) == (404, "not_found")
    assert len(seen) == 1 and [r.character_id for r in rows(db)] == ["a1"]
    assert rows(db, "B") == []


@pytest.mark.parametrize("type, subject, payload", [
    *(case[:3] for case in ISSUE.values()), (REVOKE, {"grant_id": MISSING}, {})],
    ids=[UNLOCK, RETRAIN, SET_QM, REVOKE])
def test_file_authority_answers_authority_unavailable(db, type, subject, payload):
    set_authority(db.database, authority="file")
    assert run(env(type, subject, payload)) == (503, "authority_unavailable")
    assert counts(db) == (0, 0, 0) and rows(db) == []


def test_grant_types_register_rules_adapter(db):
    with db.database.transaction() as session:
        states, pins = CharacterStateRepository(session), CampaignPinRepository(session)
        record = {(cid, character_id): states.get(cid, character_id, "build")
                  for cid, character_id in (("A", "a1"), ("D", "d_one"), ("D", "d_classic"))}
        pin = {cid: pins.pin(cid) for cid in ("A", "D")}
        payloads = {UNLOCK: ISSUE["level_unlock"], RETRAIN: ISSUE["retrain_grant"],
                    SET_QM: ISSUE["quartermaster"], REVOKE: (REVOKE, {"grant_id": MISSING}, {})}
        for type in (UNLOCK, RETRAIN, SET_QM, REVOKE):
            command_type = registry.get(type)
            adapter = command_type.rules_adapter
            assert command_type.derives_rules is False and adapter is not None, type
            ctx = SimpleNamespace(session=session, campaign_id="A",
                                  envelope=env(*payloads[type][:3]))
            assert adapter.check(ctx, None) is None, type
            character = type in (UNLOCK, RETRAIN)
            assert adapter.build_edition_matches(record["A", "a1"], pin["A"]) is True
            assert adapter.build_edition_matches(record["D", "d_classic"], pin["D"]) is True
            assert adapter.build_edition_matches(record["D", "d_one"], pin["D"]) is not character
    assert registry.get(REVOKE).allowed_principals == frozenset({PrincipalKind.USER})

    body = env(UNLOCK, {"character_id": "d_one"}, {"target_level": 2}, cid="D")
    assert run(body) == (422, "edition_mismatch")
    assert rows(db, "D") == [] and event_types(db, "D") == []
    assert issue(db, subject={"character_id": "d_classic"}, cid="D")
    assert [r.character_id for r in rows(db, "D")] == ["d_classic"]


# --------------------------------------------------------------------------
# adversarial review: expected builds, event subjects, NUL


@pytest.mark.parametrize("type, payload", [(UNLOCK, {"target_level": 2}),
                                           (RETRAIN, {"build_revision": 1})],
                         ids=[UNLOCK, RETRAIN])
def test_expected_builds_are_locked_and_checked(db, type, payload):
    def body(character_id, rev=1):
        entry = {"kind": "character.build", "id": character_id, "rev": rev}
        return {**env(type, {"character_id": "a1"}, payload), "expected": [entry]}

    # Another character's build, an unknown id, another campaign's character: 404, not 500.
    for other in ("a2", "nobody", "b1"):
        assert run(body(other)) == (404, "not_found"), other
    stale = execute(body("a1", 7), GM, route_campaign_id="A", content_descriptor=CONTENT,
                    project=project, held_projections=held)
    assert (stale.status, stale.body["code"]) == (409, "stale_revision")
    assert stale.body["conflicts"] == [
        {"kind": "character.build", "id": "a1", "expected": 7, "current": 1}]
    assert rows(db) == [] and event_types(db) == [] and counts(db)[1:] == (0, 0)
    assert run(body("a1")) == (200, None)
    assert [r.character_id for r in rows(db)] == ["a1"] and event_types(db) == ["grant.issued"]


@pytest.mark.parametrize("kind", list(ISSUE))
def test_event_subjects_and_visibility(db, kind):
    grant_id = issue(db, kind)
    response = execute(env(REVOKE, {"grant_id": grant_id}, {}), GM, route_campaign_id="A",
                       content_descriptor=CONTENT, project=project, held_projections=held)
    assert response.status == 200
    # The GM is shown the revoke event of a character grant as of any other.
    assert [event["event_id"] for event in response.body["events"]] == [
        event.event_id for event in events(db) if event.type == "grant.revoked"]
    grant ={"kind": "grant", "id": grant_id}
    if kind == "quartermaster":
        subjects, seen = [grant], {"member": "full"}
    else:
        subjects, seen = [grant, {"kind": "character", "id": "a1"}], {"owner": "full",
                                                                      "editor": "full"}
    stored = events(db)
    assert [event.type for event in stored] == ["grant.issued", "grant.revoked"]
    for event in stored:
        assert event.subjects == subjects, event.type
        # Stored normalized: every projection is named, the rest "none".
        full = {name for name, level in event.visibility.items() if level == "full"}
        assert full == {"gm", "gm_integration", *seen}, event.type


@pytest.mark.parametrize("type, subject, payload", [
    (UNLOCK, {"character_id": "a\x001"}, {"target_level": 2}),
    (RETRAIN, {"character_id": "a1\x00"}, {"build_revision": 1}),
    (REVOKE, {"grant_id": "\x00" + MISSING[1:]}, {}),
    (SET_QM, {"campaign_id": "A"}, {"user_id": "p\x002"}),
], ids=[UNLOCK, RETRAIN, REVOKE, SET_QM])
def test_nul_in_an_id_is_a_bad_envelope(db, type, subject, payload):
    opened, body = db.transactions, env(type, subject, payload)
    assert run(body) == (400, "bad_envelope")
    assert db.transactions == opened and counts(db) == (0, 0, 0)  # check 1: no transaction
    assert receipt(db, body["operation_id"]) is None and rows(db) == []
