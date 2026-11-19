"""PE-14c's step-4 authorizer: checks 4 and 5 from a fresh SQL read."""

from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import update

from core.commands import authorizer as auth
from core.commands import registry
from core.commands.authorizer import RolePolicy, load
from core.commands.checks import CheckContext, CheckRunner, CheckSpec, CommandRefusal
from core.persistence.command_locks import CommandLocks
from core.persistence import (
    Campaign,
    CampaignGrant,
    CampaignMembership,
    CampaignSession,
    CampaignStateAuthority,
    Character,
    CharacterAssignment,
    EncounterStateRepository,
    IntegrationToken,
    StateRecord,
    User,
)
from core.request_context import Principal, PrincipalKind

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
CONTENT = {"ruleset_id": "pf2e-legacy-unknown", "manifest_sha256": None}
PIN = {"profile": "pf2e", "content": CONTENT}
QM_GRANT = "q" * 32
UNLOCK = "u" * 32
OTHER_QM = "o" * 32
T = "x.test"  # a throwaway type name used with registered policies and types


def _user(session, uid, *, admin=False):
    session.add(User(id=uid, username=uid, normalized_username=uid, display_name=uid,
                     password_hash="h", is_admin=admin, created_at=NOW))


def _token(session, cid, campaign, scope, **fields):
    session.add(IntegrationToken(credential_id=cid, campaign_id=campaign, label="Obsidian",
                                 token_digest=uuid.uuid4().hex * 2, last_four="abcd",
                                 scope=scope, created_at=NOW, **fields))


@pytest.fixture
def db(sqlite_database):
    with sqlite_database.transaction() as s:
        for uid in ("gm", "owner", "editor", "viewer", "party", "qm", "qm2", "outsider",
                    "gmb"):
            _user(s, uid)
        _user(s, "admin", admin=True)
        s.flush()
        for cid in ("A", "B"):
            s.add(Campaign(id=cid, slug=cid, name=cid, system="pf2e", created_by_user_id="gm",
                           created_at=NOW, settings={"rules": PIN}))
        s.flush()
        for ch, cid in (("c1", "A"), ("c2", "A"), ("b1", "B")):
            s.add(Character(id=ch, campaign_id=cid, system="pf2e", display_name=ch,
                            legacy_storage="party_data", legacy_file=ch + ".json",
                            content_checksum="0" * 64, created_at=NOW, updated_at=NOW))
        s.flush()
        s.add(CampaignMembership(campaign_id="A", user_id="gm", role="gm"))
        s.add(CampaignMembership(campaign_id="B", user_id="gmb", role="gm"))
        for uid in ("owner", "editor", "viewer", "party", "qm", "qm2"):
            s.add(CampaignMembership(campaign_id="A", user_id=uid, role="player"))
        s.flush()
        for uid in ("owner", "editor", "viewer"):
            s.add(CharacterAssignment(campaign_id="A", character_id="c1", user_id=uid, role=uid))
        s.add(CampaignGrant(grant_id=QM_GRANT, campaign_id="A", kind="quartermaster",
                            user_id="qm", issued_by_kind="user", issued_by_id="gm",
                            issued_at=NOW))
        s.add(CampaignGrant(grant_id=OTHER_QM, campaign_id="A", kind="quartermaster",
                            user_id="qm2", issued_by_kind="user", issued_by_id="gm",
                            issued_at=NOW))
        s.add(CampaignGrant(grant_id=UNLOCK, campaign_id="A", kind="level_unlock",
                            character_id="c1", target_level=2, issued_by_kind="user",
                            issued_by_id="gm", issued_at=NOW))
        _token(s, "t_live", "A", ["@live"])
        _token(s, "t_named", "A", [T])
        _token(s, "t_override", "A", [T, "override:" + T])
        _token(s, "t_revoked", "A", ["@live", T], revoked_at=NOW)
        _token(s, "t_b", "B", ["@live", T])
    return sqlite_database


def _who(name):
    if name.startswith("t_"):
        campaign = "B" if name == "t_b" else "A"
        return Principal.integration_token(name, campaign_id=campaign)
    return Principal.user(name)


def _load(db, name, campaign="A"):
    with db.transaction() as session:
        return load(session, _who(name), campaign)


def _build(cid="c1"):
    return StateRecord("character.build", cid, 1, 1, "pf2e", CONTENT, {})


def _play(cid="c1"):
    return StateRecord("character.play", cid, 1, 1, "pf2e", CONTENT, {})


def _grant_record(grant_id, **payload):
    return StateRecord.serialized("grant", grant_id, payload)


@pytest.fixture
def policy(monkeypatch):
    def install(type=T, **fields):
        monkeypatch.setitem(auth._POLICIES, type, RolePolicy(**fields))
    return install


@pytest.fixture
def command_type(monkeypatch):
    class Adapter:
        def check(self, ctx, override):
            return None

        def build_edition_matches(self, subject, pin):
            return True

    def install(type=T, *, gm_only=False):
        t = registry.CommandType(
            type=type, subject=("character", "character.build"), dependencies={},
            payload_ids={}, requires_expected=False, derives_rules=False,
            allowed_principals=frozenset({PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN}),
            rules_adapter=Adapter(), lock_plan=lambda ctx: None, apply=lambda ctx: None,
            override_gm_only=gm_only)
        monkeypatch.delitem(registry._TYPES, type, raising=False)
        registry.register(t)
        monkeypatch.setitem(registry._TYPES, type, t)  # restored on teardown
        return t
    return install


# --------------------------------------------------------------------------
# Session types (Decision 3)


@pytest.mark.parametrize("type", ["session.start", "session.end"])
def test_session_types_allow_gm_admin_and_scoped_tokens_only(db, monkeypatch, type):
    monkeypatch.setitem(auth._POLICIES, "x.other", RolePolicy())
    for name in ("gm", "admin", "t_live"):
        assert _load(db, name).campaign_role_allows(type), name
    with db.transaction() as s:
        _token(s, "t_session", "A", [type])
        _token(s, "t_elsewhere", "A", ["x.other"])
    assert _load(db, "t_session").campaign_role_allows(type)
    for name in ("owner", "editor", "viewer", "party", "qm", "t_elsewhere", "outsider",
                 "t_revoked"):
        assert not _load(db, name).campaign_role_allows(type), name


def test_a_registered_resolver_reads_the_public_attributes(db, monkeypatch):
    seen = []

    def resolver(a, record):
        seen.append((a.is_gm, a.is_site_admin, a.principal_kind, a.user_id,
                     a.token_campaign_id, a.campaign_id))
        return a.is_gm or a.token_campaign_id == a.campaign_id

    monkeypatch.setitem(auth._RESOLVERS, "session", resolver)
    record = StateRecord.serialized("session", "s1", {})
    assert _load(db, "gm").visible(record)
    assert _load(db, "admin").visible(record)
    assert _load(db, "t_live").visible(record)
    assert not _load(db, "owner").visible(record)
    assert seen[0] == (True, False, PrincipalKind.USER, "gm", None, "A")
    assert seen[1] == (True, True, PrincipalKind.USER, "admin", None, "A")
    assert seen[2] == (False, False, PrincipalKind.INTEGRATION_TOKEN, None, "A", "A")


def test_unregistered_kind_is_not_visible_and_registration_refuses_duplicates(db):
    assert not _load(db, "gm").visible(StateRecord.serialized("mystery", "m1", {}))
    with pytest.raises(ValueError):
        auth.register_visibility("grant", lambda a, r: True)
    with pytest.raises(ValueError):
        auth.register_role_policy("session.start", RolePolicy())
    with pytest.raises(ValueError):
        RolePolicy(roles=frozenset({"gm"}))


# --------------------------------------------------------------------------
# Fresh reads


def test_flask_g_caches_are_ignored(db, monkeypatch):
    from flask import Flask, g

    import core.auth
    import core.campaigns

    def boom(*args, **kwargs):
        raise AssertionError("the authorizer read a request cache")

    monkeypatch.setattr(core.auth, "_load_users", boom)
    monkeypatch.setattr(core.campaigns, "get_campaign", boom)
    app = Flask(__name__)
    with app.test_request_context("/"):
        # Stale caches that would make the viewer an admin and the GM of A.
        setattr(g, core.auth._USERS_MEMO, {"viewer": {"is_admin": True}})
        setattr(g, core.campaigns._CAMPAIGN_MEMO, {"A": {"gm_user_id": "viewer"}})
        viewer = _load(db, "viewer")
        assert not viewer.is_gm and not viewer.is_site_admin
        assert not viewer.campaign_role_allows("session.start")


def test_load_works_with_no_authority_row_and_with_file_authority(db, policy):
    policy(roles=frozenset({"owner"}))
    assert _load(db, "owner").campaign_role_allows(T)
    with db.transaction() as s:
        s.add(CampaignStateAuthority(campaign_id="A", authority="file", stream_epoch=1,
                                     changed_at=NOW))
    assert _load(db, "owner").campaign_role_allows(T)
    assert _load(db, "gm").is_gm


def test_principal_is_admin_flag_not_trusted(db, command_type):
    command_type()
    a = _load_principal(db, Principal.user("viewer", is_admin=True))
    assert not a.is_gm and not a.is_site_admin
    assert not a.override_allowed(T)
    assert not a.campaign_role_allows(T)  # a GM-only type (no policy)
    assert not a.campaign_role_allows("session.start")


def _load_principal(db, principal, campaign="A"):
    with db.transaction() as session:
        return load(session, principal, campaign)


def test_changes_take_effect_on_the_next_load(db):
    assert _load(db, "gm").is_gm
    with db.transaction() as s:
        s.execute(update(CampaignMembership).where(CampaignMembership.user_id == "gm")
                  .values(role="player"))
    assert not _load(db, "gm").is_gm
    with db.transaction() as s:
        s.execute(update(Campaign).where(Campaign.id == "A").values(trashed_at=NOW))
    for name in ("admin", "t_live"):
        a = _load(db, name)
        assert not a.campaign_role_allows("session.start") and not a.visible(_play())


def test_unsupported_and_unknown_principals_refuse_everything(db):
    for principal in (Principal.legacy_gm("A"), Principal.anonymous(),
                      Principal.publish_token("t_live", campaign_id="A"),
                      Principal.user("nobody"), Principal.user("outsider"),
                      Principal.integration_token("t_b", campaign_id="A"),
                      Principal.integration_token("missing", campaign_id="A")):
        a = _load_principal(db, principal)
        assert not a.campaign_role_allows("session.start"), principal
        assert not a.visible(StateRecord.serialized("campaign", "A", {})), principal
        assert not a.override_allowed("session.start"), principal
    assert not _load(db, "gm", "missing").campaign_role_allows("session.start")
    with pytest.raises(TypeError):
        _load_principal(db, "gm")


def test_a_token_is_bound_to_its_campaign(db):
    assert _load(db, "t_b", "B").campaign_role_allows("session.start")
    assert not _load_principal(db, Principal.integration_token("t_b", campaign_id="B"),
                               "A").campaign_role_allows("session.start")


# --------------------------------------------------------------------------
# The matrix through the check runner: 404 before 403 (C:116-117)


class _Locks:
    def __init__(self, session, campaign_id, records):
        self.session, self.campaign_id, self.records = session, campaign_id, records


class _Adapter:
    def check(self, ctx, override):
        return None

    def build_edition_matches(self, subject, pin):
        return True


def _run(db, name, subject, kind="character.build", type=T):
    spec = CheckSpec(type=type, subject=("character", kind))
    with db.transaction() as session:
        a = load(session, _who(name), "A")
        ctx = CheckContext(
            session=session, locks=_Locks(session, "A", {(kind, subject.id): subject}),
            principal=_who(name), campaign_id="A",
            envelope={"subject": {"character": subject.id}, "payload": {}, "expected": [],
                      "rules": {"profile": "pf2e"}},
            authorizer=a, project=lambda p, rows: {}, content_descriptor=CONTENT, pin=PIN,
            envelope_checked=True, fence_passed=True, receipt_absent=True)
        try:
            CheckRunner(spec, _Adapter()).run(ctx)
        except CommandRefusal as refusal:
            return refusal.http_status
        return 200


def test_matrix_build_subject_owner_editor_type(db, policy):
    # "Save draft / publish": owner and editor.  Check 4 is subject-independent, so a
    # caller holding neither role anywhere is refused there (403, nothing revealed).
    policy(roles=frozenset({"owner", "editor"}), live=False)
    expected = {"owner": 200, "editor": 200, "viewer": 403, "party": 403, "qm": 403,
                "gm": 200, "admin": 200, "t_named": 200, "t_live": 403, "outsider": 403}
    got = {name: _run(db, name, _build()) for name in expected}
    assert got == expected
    # Past check 4, a build the caller cannot see is 404, never 403.
    assert _run(db, "owner", _build("c2")) == 404
    assert _run(db, "editor", _build("c2")) == 404


def test_matrix_viewer_type_sees_404_before_403(db, policy):
    # A type the viewer role may send: a viewer passes check 4 everywhere, so an
    # unassigned build is 404 and its own build passes.
    policy(roles=frozenset({"viewer"}))
    assert _run(db, "viewer", _build()) == 200
    assert _run(db, "viewer", _build("c2")) == 404
    assert _run(db, "owner", _build()) == 403  # owner sees it but holds no viewer role


def test_matrix_play_subject_shows_404_only_for_the_unseen(db, policy):
    # A type for owner/editor on the play record: party sees it (403), a viewer too.
    policy(roles=frozenset({"owner", "editor"}), live=True)
    expected = {"owner": 200, "editor": 200, "viewer": 403, "party": 403, "qm": 403,
                "gm": 200, "admin": 200, "t_live": 200, "t_named": 200}
    assert {name: _run(db, name, _play(), "character.play") for name in expected} == expected
    # A party member passing check 4 (a party type) cannot see a build: 404.
    auth._POLICIES[T] = RolePolicy(roles=frozenset({"party"}))
    assert _run(db, "party", _build()) == 404
    assert _run(db, "party", _play(), "character.play") == 200
    assert _run(db, "owner", _play(), "character.play") == 403  # owner of c1, not party
    assert _run(db, "owner", _play("c2"), "character.play") == 200  # party for c2


def test_role_check_4_refuses_before_visibility(db, policy):
    policy(roles=frozenset({"qm"}))
    assert _run(db, "party", _build("c2")) == 403  # check 4: party holds no qm role
    assert _run(db, "qm", _play(), "character.play") == 200


@pytest.mark.parametrize("name, visible", [
    ("gm", True), ("admin", True), ("owner", True), ("editor", True),
    ("viewer", False), ("party", False), ("qm", False), ("qm2", False),
    ("t_live", False), ("t_override", False), ("outsider", False),
])
def test_grant_visibility_for_a_character_grant(db, name, visible):
    record = _grant_record(UNLOCK, kind="level_unlock", character_id="c1")
    assert _load(db, name).visible(record) is visible


@pytest.mark.parametrize("name, visible", [
    ("gm", True), ("qm", True), ("qm2", False), ("owner", False), ("party", False),
    ("t_live", False),
])
def test_grant_visibility_for_a_qm_grant(db, name, visible):
    record = _grant_record(QM_GRANT, kind="quartermaster", user_id="qm")
    assert _load(db, name).visible(record) is visible


def test_grant_subject_404_before_403_through_the_runner(db, policy):
    policy(roles=frozenset({"owner", "editor"}))
    record = _grant_record(UNLOCK, kind="level_unlock", character_id="c1")
    unseen = _grant_record("v" * 32, kind="level_unlock", character_id="c2")
    assert _run(db, "owner", unseen, "grant") == 404
    assert _run(db, "t_named", record, "grant") == 404  # tokens never see grants
    assert _run(db, "owner", record, "grant") == 403  # owner sees it, holds no role on it
    assert _run(db, "gm", record, "grant") == 200


def test_record_visibility_by_kind(db):
    pc = StateRecord("combatant", "k1", 1, None, None, None, {"kind": "pc"})
    foe = StateRecord("combatant", "k2", 1, None, None, None, {"kind": "creature"})
    shown = StateRecord("combatant", "k3", 1, None, None, None,
                        {"kind": "creature", "revealed": True})
    enc = StateRecord("encounter", "e1", 1, 1, "pf2e", CONTENT, {})
    clock = StateRecord("clock", "world", 1, None, None, None, {})
    camp, other = (StateRecord.serialized("campaign", c, {}) for c in ("A", "B"))
    party, gm, token = _load(db, "party"), _load(db, "gm"), _load(db, "t_live")
    for record in (pc, enc, clock, camp, _play()):
        assert party.visible(record) and gm.visible(record) and token.visible(record)
    for record in (foe, shown):  # no trusted reveal state yet (PE-30)
        assert not party.visible(record) and gm.visible(record) and token.visible(record)
    assert not party.visible(_build()) and token.visible(_build())
    for a in (party, gm, token):
        assert not a.visible(other)
    assert not _load(db, "outsider").visible(_play())


# --------------------------------------------------------------------------
# Override (Decision 6)


def test_override_allowed_unrestricted_type_scoped_token(db, command_type, policy):
    command_type(gm_only=False)
    policy(token=True)
    assert _load(db, "t_override").override_allowed(T)


def test_registered_type_without_a_role_policy_is_gm_and_admin_only(db, command_type):
    # The type admits token principals at check 1 but has no role policy.
    command_type(gm_only=False)
    assert T not in auth._POLICIES
    for name in ("t_named", "t_override", "t_live"):
        a = _load(db, name)
        assert not a.campaign_role_allows(T), name
        assert not a.subject_role_allows(T, _build()), name
        assert not a.override_allowed(T), name
    for name in ("owner", "editor", "qm"):
        assert not _load(db, name).campaign_role_allows(T), name
    for name in ("gm", "admin"):
        a = _load(db, name)
        assert a.campaign_role_allows(T) and a.override_allowed(T), name


def test_an_explicit_token_policy_admits_the_scoped_token(db, command_type, policy):
    command_type(gm_only=False)
    policy(token=True)
    assert _load(db, "t_named").campaign_role_allows(T)
    assert _load(db, "t_named").subject_role_allows(T, _build())
    assert not _load(db, "t_named").override_allowed(T)  # no override: scope
    assert _load(db, "t_override").override_allowed(T)
    assert not _load(db, "t_live").campaign_role_allows(T)  # not a live type


def test_override_gm_only_type_allows_gm_and_site_admin(db, command_type):
    command_type(gm_only=True)
    assert _load(db, "gm").override_allowed(T)
    assert _load(db, "admin").override_allowed(T)


def test_override_gm_only_type_refuses_override_scoped_token(db, command_type):
    command_type(gm_only=True)
    assert not _load(db, "t_override").override_allowed(T)


def test_override_gm_only_type_refuses_qm_owner_editor(db, command_type):
    command_type(gm_only=True)
    for name in ("qm", "owner", "editor"):
        assert not _load(db, name).override_allowed(T), name


def test_override_unregistered_type_refuses_token(db, monkeypatch):
    monkeypatch.delitem(registry._TYPES, T, raising=False)
    assert not _load(db, "t_override").override_allowed(T)
    assert _load(db, "gm").override_allowed(T)


def test_override_refused_for_players_and_unscoped_tokens(db, command_type):
    command_type(gm_only=False)
    for name in ("owner", "editor", "viewer", "party", "qm", "t_live", "t_named",
                 "t_revoked", "outsider"):
        assert not _load(db, name).override_allowed(T), name


def test_override_reaches_the_runner_as_403(db, policy, command_type):
    command_type(gm_only=True)
    policy(roles=frozenset({"owner"}))
    spec = CheckSpec(type=T, subject=("character", "character.build"))
    with db.transaction() as session:
        a = load(session, _who("owner"), "A")
        ctx = CheckContext(
            session=session, locks=_Locks(session, "A", {("character.build", "c1"): _build()}),
            principal=_who("owner"), campaign_id="A",
            envelope={"subject": {"character": "c1"}, "payload": {}, "expected": [],
                      "rules": {"profile": "pf2e"}, "override": {"reason": "x"}},
            authorizer=a, project=lambda p, rows: {}, content_descriptor=CONTENT, pin=PIN,
            envelope_checked=True, fence_passed=True, receipt_absent=True)
        with pytest.raises(CommandRefusal) as refused:
            CheckRunner(spec, _Adapter()).run(ctx)
    assert refused.value.http_status == 403


# --------------------------------------------------------------------------
# Token scope


@pytest.mark.parametrize("type", ["membership.set_role", "character.assignment.set",
                                  "campaign.ownership.transfer", "token.mint", "token.revoke",
                                  "grant.set_quartermaster", "campaign.members.remove",
                                  "character.set_owner", "campaign.add_member",
                                  "campaign.transfer_ownership", "character.assign_editor",
                                  "integration_token.revoke", "grant.issue_quartermaster"])
def test_a_token_is_never_authorized_for_membership_ownership_token_or_qm_types(
        db, monkeypatch, command_type, type):
    monkeypatch.setitem(auth._POLICIES, type, RolePolicy(live=True))
    command_type(type)
    with db.transaction() as s:
        _token(s, "t_all", "A", ["@live", type, "override:" + type])
    a = _load(db, "t_all")
    assert not a.campaign_role_allows(type)
    assert not a.subject_role_allows(type, _build())
    assert not a.override_allowed(type)
    assert _load(db, "gm").campaign_role_allows(type)


def test_live_scope_covers_only_live_types_and_token_false_refuses(db, policy, monkeypatch):
    policy(live=False)
    assert not _load(db, "t_live").campaign_role_allows(T)
    assert _load(db, "t_named").campaign_role_allows(T)
    monkeypatch.setitem(auth._POLICIES, T, RolePolicy(token=False))
    assert not _load(db, "t_named").campaign_role_allows(T)


def test_malformed_scope_entries_are_ignored(db):
    with db.transaction() as s:
        _token(s, "t_odd", "A", ["@live", 7, None, {"x": 1}])
        _token(s, "t_obj", "A", {"@live": True})
    assert _load(db, "t_odd").campaign_role_allows("session.start")
    assert not _load(db, "t_obj").campaign_role_allows("session.start")


# --------------------------------------------------------------------------
# Replacement and revocation (Decision 4; PE-14f helpers)


@pytest.fixture
def mint(db, monkeypatch):
    """``integration_tokens.insert_minted`` against this test's database."""

    from core.persistence import integration_tokens, runtime

    monkeypatch.setattr(runtime, "database", lambda: db)

    def run(cid, campaign="A", **fields):
        record = {"id": cid, "label": "Obsidian", "token_hash": uuid.uuid4().hex * 2,
                  "last_four": "abcd", "created_at": "2026-11-18T12:00:00Z"}
        integration_tokens.insert_minted(campaign, record, **fields)
    return run


def _set_role(db, user, role):
    with db.transaction() as s:
        s.execute(update(CampaignMembership).where(
            CampaignMembership.campaign_id == "A", CampaignMembership.user_id == user)
            .values(role=role))


def _minted_as(db, cid):
    with db.transaction() as s:
        return s.get(IntegrationToken, cid).minted_as


def test_replacement_minted_by_the_gm_survives_a_later_demotion(db, mint):
    mint("t_new", replaces_credential_id="t_live", minted_by_user_id="gm")
    assert _minted_as(db, "t_new") == "gm"
    assert _load(db, "t_new").is_recorded_replacement("t_live")
    _set_role(db, "gm", "player")  # GM handoff after the mint
    assert _load(db, "t_new").is_recorded_replacement("t_live")
    assert not _load(db, "t_new").is_recorded_replacement("t_named")


def test_replacement_minted_by_a_site_admin_records_admin(db, mint):
    mint("t_adm", replaces_credential_id="t_live", minted_by_user_id="admin")
    assert _minted_as(db, "t_adm") == "admin"
    assert _load(db, "t_adm").is_recorded_replacement("t_live")


@pytest.mark.parametrize("minter, original", [
    ("owner", "t_live"),      # a player is not the GM at mint
    ("nobody", "t_live"),     # no such user
    (None, "t_live"),         # no recorded minter
    ("gm", "t_b"),            # the original is another campaign's credential
    ("gm", "missing"),        # no such original
])
def test_replacement_mint_without_authority_or_same_campaign_original_writes_nothing(
        db, mint, minter, original):
    from core.persistence.integration_tokens import ReplacementNotAuthorized

    with pytest.raises(ReplacementNotAuthorized):
        mint("t_bad", replaces_credential_id=original, minted_by_user_id=minter)
    with db.transaction() as s:
        assert s.get(IntegrationToken, "t_bad") is None


def test_a_later_promotion_never_qualifies_a_replacement(db, mint):
    # A legacy row from before provenance: replacement link and minter, no role at mint.
    with db.transaction() as s:
        _token(s, "t_legacy", "A", ["@live"], replaces_credential_id="t_live",
               minted_by_user_id="owner")
    _set_role(db, "owner", "gm")  # promoted after the mint
    assert not _load(db, "t_legacy").is_recorded_replacement("t_live")
    # An ordinary mint by a non-GM records no role, and promotion does not add one.
    mint("t_plain", minted_by_user_id="editor")
    assert _minted_as(db, "t_plain") is None


def test_replacement_rows_are_checked_against_this_campaign_at_read(db):
    with db.transaction() as s:  # forged rows that bypass insert_minted
        _token(s, "t_cross", "A", ["@live"], replaces_credential_id="t_b",
               minted_by_user_id="gm", minted_as="gm")
        _token(s, "t_import", "A", ["@live"], replaces_credential_id="t_live")
    assert not _load(db, "t_cross").is_recorded_replacement("t_b")
    assert not _load(db, "t_import").is_recorded_replacement("t_live")
    assert not _load(db, "gm").is_recorded_replacement("t_live")


def test_a_revoked_replacement_is_not_a_valid_reader(db, mint):
    mint("t_new", replaces_credential_id="t_live", minted_by_user_id="gm")
    with db.transaction() as s:
        s.get(IntegrationToken, "t_new").revoked_at = NOW
    assert not _load(db, "t_new").is_recorded_replacement("t_live")


@pytest.mark.parametrize("fields", [dict(minted_as="owner", minted_by_user_id="gm"),
                                    dict(minted_as="gm")])
def test_minted_as_constraint(db, fields):
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError, match="minted_as_known"), db.transaction() as s:
        _token(s, "t_check", "A", ["@live"], **fields)
        s.flush()


def test_token_revoked_reads_sql_in_this_campaign_only(db):
    gm = _load(db, "gm")
    assert gm.token_revoked("t_revoked")
    assert not gm.token_revoked("t_live")
    assert not gm.token_revoked("t_b")  # another campaign's row
    assert not gm.token_revoked("missing")


# --------------------------------------------------------------------------
# Live (C:842-843)


def _encounter(db, *, session_active):
    with db.transaction() as s:
        s.add(CampaignStateAuthority(campaign_id="A", authority="db", stream_epoch=1,
                                     changed_at=NOW))
        if session_active:
            s.add(CampaignSession(campaign_id="A", session_id="s1", status="active",
                                  started_at=NOW, origin="website"))
    with db.transaction() as s:
        repo = EncounterStateRepository(s)
        repo.insert("A", "e1", rules_profile="pf2e", content=CONTENT)
        repo.add_combatant("A", "e1", read_revision=1, combatant_id="k1", kind="pc",
                           character_id="c1")


def _combat(db):
    _encounter(db, session_active=True)
    with db.transaction() as s:
        repo = EncounterStateRepository(s)
        repo.add_combatant("A", "e1", read_revision=2, combatant_id="k2", kind="pc",
                           character_id="c2")
        repo.add_combatant("A", "e1", read_revision=3, combatant_id="k3",
                           kind="creature", creature_ref="goblin")


def _locked_combatants(session):
    """The combatant records exactly as the executor's lock manager registers them."""

    locks = CommandLocks(session, "A", lock_stream=lambda session, campaign_id: None)
    locks.stream()
    locks.encounter("e1")
    return locks


def _declare(db, name):
    """Run the checks for an actor-subject command on each combatant; the HTTP status."""

    out = {}
    for combatant in ("k1", "k2", "k3"):
        with db.transaction() as session:
            locks = _locked_combatants(session)
            ctx = CheckContext(
                session=session, locks=locks, principal=_who(name), campaign_id="A",
                envelope={"subject": {"actor": combatant}, "payload": {}, "expected": [],
                          "rules": {"profile": "pf2e"}},
                authorizer=load(session, _who(name), "A"), project=lambda p, rows: {},
                content_descriptor=CONTENT, pin=PIN, envelope_checked=True,
                fence_passed=True, receipt_absent=True)
            try:
                CheckRunner(CheckSpec(type=T, subject=("actor", "combatant")),
                            _Adapter()).run(ctx)
                out[combatant] = 200
            except CommandRefusal as refused:
                out[combatant] = refused.http_status
    return out


@pytest.mark.parametrize("name, expected", [
    ("owner", {"k1": 200, "k2": 403, "k3": 404}),   # own PC; another's PC; hidden NPC
    ("editor", {"k1": 200, "k2": 403, "k3": 404}),
    ("viewer", {"k1": 403, "k2": 403, "k3": 403}),  # check 4: no owner/editor role
    ("party", {"k1": 403, "k2": 403, "k3": 403}),
    ("gm", {"k1": 200, "k2": 200, "k3": 200}),
    ("t_named", {"k1": 200, "k2": 200, "k3": 200}),
])
def test_actor_combatant_subject_through_the_real_lock_path(db, policy, name, expected):
    _combat(db)
    policy(roles=frozenset({"owner", "editor"}), token=True)
    assert _declare(db, name) == expected


def test_combatant_records_from_the_repository_carry_the_character(db):
    _combat(db)
    with db.transaction() as session:
        records = _locked_combatants(session).records
        assert records[("combatant", "k1")].payload["character_id"] == "c1"
        assert records[("combatant", "k3")].payload["character_id"] is None
        a = load(session, _who("owner"), "A")
        assert a.visible(records[("combatant", "k2")])  # another player's PC is visible
        assert not a.visible(records[("combatant", "k3")])


def test_a_creature_is_hidden_from_players_whatever_its_payload_claims(db):
    # No trusted reveal state exists yet (PE-30 owns it): a payload flag is not trusted.
    forged = StateRecord("combatant", "k9", 1, None, None, None,
                         {"kind": "creature", "revealed": True, "encounter_id": "e1"})
    assert not _load(db, "owner").visible(forged)
    assert _load(db, "gm").visible(forged) and _load(db, "t_live").visible(forged)


def test_is_live_needs_an_active_session(db):
    _encounter(db, session_active=False)
    with db.transaction() as session:
        assert not load(session, _who("owner"), "A").is_live("c1")


def test_is_live_needs_the_character_in_the_active_encounter(db):
    _encounter(db, session_active=True)
    with db.transaction() as session:
        a = load(session, _who("owner"), "A")
        assert a.is_live("c1")
        assert not a.is_live("c2")


# --------------------------------------------------------------------------
# The file side is never reached from core/commands


def test_authorizer_does_not_import_integration_tokens():
    tree = ast.parse((REPO_ROOT / "core" / "commands" / "authorizer.py")
                     .read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names += [module] + [f"{module}.{alias.name}" for alias in node.names]
    banned = ("core.persistence.integration_tokens", "core.obsidian_sync")
    assert not [n for n in names if any(n == b or n.startswith(b + ".") for b in banned)
                or n.endswith("integration_tokens") or n.endswith("obsidian_sync")]


def test_load_and_checks_never_touch_the_token_file(db, monkeypatch):
    import core.obsidian_sync as store
    from core.persistence import integration_tokens

    def boom(*args, **kwargs):
        raise AssertionError("the authorizer reached the token file")

    for module, name in ((store, "campaign_lock"), (store, "tokens_file"),
                         (store, "_load_tokens"), (store, "_load_tokens_strict"),
                         (integration_tokens, "repair_token_mirror"),
                         (integration_tokens, "import_file_records")):
        monkeypatch.setattr(module, name, boom)
    principal = Principal.integration_token("never-imported", campaign_id="A")
    a = _load_principal(db, principal)
    assert not a.campaign_role_allows("session.start")
    assert not a.override_allowed("session.start")
    assert not a.visible(_play())
    live = _load(db, "t_live")
    assert live.campaign_role_allows("session.start") and live.visible(_play())
