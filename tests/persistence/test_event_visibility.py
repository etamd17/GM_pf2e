"""PE-14f: ``held_projections`` and the event-ID projector (Decision 7).

Event visibility is fixed at commit (C:409-417): a reader sees an event id
listed when a projection it holds now has ``full`` or a well-formed
``redact:[classes]`` in the event's stored map, judged like
``events.event_views``.  The fixture is ``test_operation_status``'s.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from core.commands import event_visibility, operations
from core.commands.authorizer import held_projections, load
from core.commands.event_visibility import MissingEvent, event_visible_to, visible_event_ids
from core.persistence.models import CampaignGrant, CampaignMembership, CharacterAssignment
from core.request_context import Principal

from .test_command_executor import op
from .test_operation_status import (  # noqa: F401  (``db`` is the shared fixture)
    A, B, add_event, add_receipt, db, read, revoke, who,
)

A1 = [{"kind": "character", "id": "a1"}]


def authorizer(db, name, cid=A):
    principal = name if isinstance(name, Principal) else who(name)
    with db.database.transaction() as session:
        return load(session, principal, cid)


def held(db, name, subjects=A1, encounter_id=None, cid=A):
    return held_projections(authorizer(db, name, cid), subjects=subjects,
                            encounter_id=encounter_id)


def ids(db, name, event_ids, cid=A):
    with db.database.transaction() as session:
        return visible_event_ids(session, load(session, who(name), cid), cid, event_ids)


def events(db, *specs, cid=A):
    """Commit one event per spec (``add_event`` keywords); returns their ids in order."""

    with db.database.transaction() as session:
        return [add_event(session, cid, **spec) for spec in specs]


# --------------------------------------------------------------------------
# held_projections


def test_held_projections_per_role(db):
    expect = {
        "p1": {"member", "owner"}, "p2": {"member", "editor"}, "viewer": {"member", "viewer"},
        "party": {"member", "party"}, "qm": {"member", "party", "qm"},
        "gm": {"gm"}, "admin": {"gm"}, "t1": {"gm_integration"},
        "out": set(),  # a member of B only: a non-member of A
    }
    for name, projections in expect.items():
        assert held(db, name) == frozenset(projections), name
    # ``member`` whatever the subjects; ``party`` only for a character subject
    assert held(db, "party", subjects=[]) == {"member"}
    assert held(db, "p1", subjects=[{"kind": "grant", "id": "a1"}, "junk", {"kind": "character"}]
                ) == {"member"}
    assert held(db, "p1", subjects=[*A1, {"kind": "character", "id": "zz"}]) == {
        "member", "owner", "party"}
    assert held(db, "p1", subjects=None) == {"member"}
    # disabled: the users table has no disabled column, so an account with no
    # SQL row (removed) is the disabled user; the Principal's admin flag is ignored
    for ghost in (Principal.user("ghost"), Principal.user("ghost", is_admin=True)):
        assert held(db, ghost) == frozenset()
    with db.database.transaction() as s:
        s.execute(delete(CampaignGrant).where(CampaignGrant.user_id == "qm"))
    assert held(db, "qm") == {"member", "party"}


def test_held_projections_precedence(db):
    """Invalid holds nothing; GM, admin and tokens need no membership; others do."""

    # enabled GM/admin and a valid token, none a member of B
    assert held(db, "admin", cid=B) == {"gm"}
    assert held(db, "tb", cid=B) == {"gm_integration"}
    assert held(db, "gmb", cid=B) == {"gm"}
    # other users require membership: a player of A is nobody in B
    for name in ("p1", "qm", "gm"):
        assert held(db, name, cid=B) == frozenset(), name
    # a revoked token and a token presented for another campaign hold nothing
    revoke(db, "tb")
    assert held(db, "tb", cid=B) == frozenset()
    assert held(db, "t1", cid=B) == frozenset()
    # removing the GM's membership removes ``gm``; the site admin keeps it
    with db.database.transaction() as s:
        s.execute(delete(CampaignMembership).where(CampaignMembership.user_id == "gm",
                                                   CampaignMembership.campaign_id == A))
    assert held(db, "gm") == frozenset() and held(db, "admin") == {"gm"}


def test_held_projections_encounter_player_only_with_encounter_id(db):
    assert "encounter_player" not in held(db, "party")
    assert "encounter_player" in held(db, "party", encounter_id="e" * 32)
    assert "encounter_player" in held(db, "p1", subjects=[], encounter_id="e" * 32)
    for name in ("gm", "t1", "out"):
        assert "encounter_player" not in held(db, name, encounter_id="e" * 32), name


# --------------------------------------------------------------------------
# event_visible_to and visible_event_ids


def test_gm_sees_all_event_ids(db):
    listed = events(db, {"visibility": {}}, {"raw": None}, {"raw": {"gm": "none"}},
                    {"raw": "junk"}, {"visibility": {"owner": "full"}, "subjects": A1})
    assert ids(db, "gm", listed) == ids(db, "admin", listed) == listed


def test_owner_sees_full_and_redacted_ids(db):
    full, redacted, hidden = events(
        db, {"visibility": {"owner": "full"}, "subjects": A1},
        {"visibility": {"owner": "redact:[exact_hp,secret_roll]"}, "subjects": A1},
        {"visibility": {"owner": "none", "editor": "full"}, "subjects": A1})
    assert ids(db, "p1", [full, redacted, hidden]) == [full, redacted]
    assert ids(db, "p2", [full, redacted, hidden]) == [hidden]


def test_unlisted_projection_is_omitted(db):
    (only_party,) = events(db, {"visibility": {"party": "full"}, "subjects": A1})
    assert ids(db, "party", [only_party]) == [only_party]
    for name in ("p1", "p2", "viewer", "t1", "out"):
        assert ids(db, name, [only_party]) == [], name


def test_member_projection_event_visible_to_unassigned_player(db):
    (member,) = events(db, {"visibility": {"member": "full"}})
    for name in ("party", "p1", "qm", "gm"):
        assert ids(db, name, [member]) == [member], name
    assert ids(db, "t1", [member]) == [] and ids(db, "out", [member]) == []


def test_null_visibility_map_gm_only(db):
    listed = events(db, {"raw": None, "subjects": A1}, {"raw": ["owner"], "subjects": A1},
                    {"raw": {}, "subjects": A1})
    assert ids(db, "gm", listed) == listed
    for name in ("p1", "party", "t1"):
        assert ids(db, name, listed) == [], name


def test_malformed_redact_value_counts_as_none_like_event_views(db):
    """PE-14c's carried note: aligned with ``events.event_views``, not "starts with redact:"."""

    bad = ["redact:", "redact:[]", "redact:[exact_hp", "redact:[nonsense]", "redact:exact_hp",
           "FULL", "redact:[Exact_HP]", 1]
    listed = events(db, *({"raw": {"owner": value}, "subjects": A1} for value in bad))
    assert ids(db, "p1", listed) == []
    assert ids(db, "gm", listed) == listed


def test_hidden_actor_event_omitted_for_encounter_player(db):
    e = "e" * 32
    hidden, redacted, outside = events(
        db, {"visibility": {"encounter_player": "none", "gm_integration": "full"},
             "encounter_id": e},
        {"visibility": {"encounter_player": "redact:[hidden_actor]"}, "encounter_id": e},
        {"visibility": {"encounter_player": "full"}})
    assert ids(db, "party", [hidden, redacted, outside]) == [redacted]
    assert ids(db, "t1", [hidden, redacted, outside]) == [hidden]


def test_later_reveal_does_not_expose_old_event(db):
    """A hidden event, then a reveal event: a player sees only the reveal's id."""

    e = "e" * 32
    o = op()
    hidden, reveal = events(
        db, {"visibility": {"gm_integration": "full"}, "encounter_id": e,
             "subjects": [{"kind": "combatant", "id": "n" * 32}]},
        {"visibility": {"encounter_player": "full", "member": "full"}, "encounter_id": e,
         "subjects": [{"kind": "combatant", "id": "n" * 32}]})
    add_receipt(db, "user:party", o, event_ids=[hidden, reveal])
    status, body = read("party", "user:party", o)
    assert (status, body["state"], body["event_ids"]) == (200, "committed", [reveal])
    assert read("gm", "user:party", o)[1]["event_ids"] == [hidden, reveal]


def test_demoted_reader_loses_owner_projection(db):
    o = op()
    (owned,) = events(db, {"visibility": {"owner": "full"}, "subjects": A1})
    add_receipt(db, "user:p1", o, event_ids=[owned])
    assert read("p1", "user:p1", o)[1]["event_ids"] == [owned]
    with db.database.transaction() as s:
        s.execute(delete(CharacterAssignment).where(CharacterAssignment.user_id == "p1",
                                                    CharacterAssignment.campaign_id == A))
    status, body = read("p1", "user:p1", o)
    assert (status, body["state"], body["event_ids"]) == (200, "committed", [])


def test_token_reader_uses_gm_integration(db):
    o = op()
    mirrored, owner_only = events(db, {"visibility": {"gm_integration": "full"}},
                                  {"visibility": {"owner": "full"}, "subjects": A1})
    add_receipt(db, "integration_token:t1", o, event_ids=[mirrored, owner_only])
    assert read("t1", "integration_token:t1", o)[1]["event_ids"] == [mirrored]
    assert read("t1r", "integration_token:t1", o)[1]["event_ids"] == [mirrored]
    assert read("gm", "integration_token:t1", o)[1]["event_ids"] == [mirrored, owner_only]


def test_missing_event_row_is_server_error(db):
    o = op()
    (kept,) = events(db, {"visibility": {"member": "full"}})
    with pytest.raises(MissingEvent):
        ids(db, "gm", [kept, uuid.uuid4().hex])
    with pytest.raises(MissingEvent):
        ids(db, "gm", [kept, 7])
    add_receipt(db, "user:p1", o, event_ids=[uuid.uuid4().hex])
    assert read("gm", "user:p1", o) == (
        500, {"ok": False, "code": "server_error", "operation_id": o})


def test_order_preserved(db):
    listed = events(db, *({"visibility": {"member": "full"}} for _ in range(5)))
    shuffled = [listed[3], listed[0], listed[4], listed[1], listed[2]]
    assert ids(db, "party", shuffled) == shuffled
    assert ids(db, "party", []) == []


def test_event_visible_to_takes_held_projections_from_the_event(db, monkeypatch):
    seen = []
    real = event_visibility.held_projections

    def spy(a, *, subjects, encounter_id):
        seen.append((subjects, encounter_id))
        return real(a, subjects=subjects, encounter_id=encounter_id)

    monkeypatch.setattr(event_visibility, "held_projections", spy)
    (listed,) = events(db, {"visibility": {"owner": "full"}, "subjects": A1,
                            "encounter_id": "e" * 32})
    assert ids(db, "p1", [listed]) == [listed]
    assert seen == [(A1, "e" * 32)]
    assert operations.visible_event_ids is visible_event_ids
    row = type("Row", (), {"subjects": A1, "encounter_id": None, "visibility": {"owner": "full"}})
    assert event_visible_to(authorizer(db, "p1"), row)
    assert not event_visible_to(authorizer(db, "p2"), row)
