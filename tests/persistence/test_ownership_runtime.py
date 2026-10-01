from __future__ import annotations

import hashlib
import importlib
import json

import pytest
from sqlalchemy import select

from core.persistence.models import (
    AuditEvent,
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    Invitation,
    InviteRedemption,
    User,
    utc_now,
)


@pytest.fixture
def ownership_store(sqlite_database, monkeypatch):
    runtime = importlib.import_module("core.persistence.runtime")
    ownership = importlib.import_module("core.persistence.ownership")
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    with sqlite_database.transaction() as session:
        session.add_all([
            User(id=uid, username=uid, normalized_username=uid,
                 display_name=uid, password_hash="hash")
            for uid in ("gm", "owner", "editor", "viewer", "outsider")
        ])
        session.flush()
        session.add_all([
            Campaign(id=cid, slug=cid, name=cid, system="pf2e",
                     created_by_user_id="gm")
            for cid in ("campaign", "other-campaign")
        ])
        session.flush()
        session.add_all([
            CampaignMembership(campaign_id="campaign", user_id=uid,
                               role="gm" if uid == "gm" else "player")
            for uid in ("gm", "owner", "editor", "viewer")
        ])
        session.flush()
        session.add(Character(
            id="hero", campaign_id="campaign", system="pf2e",
            display_name="Hero", legacy_storage="party_data",
            legacy_file="hero.json", content_checksum="a" * 64,
        ))
        session.flush()
        session.add_all([
            CharacterAssignment(campaign_id="campaign", character_id="hero",
                                user_id=role, role=role)
            for role in ("owner", "editor", "viewer")
        ])
        session.get(CampaignMembership, ("campaign", "owner")).character_id = "hero"
    return ownership, sqlite_database


def _document(**updates):
    return {"id": "hero", "campaign_id": "campaign", "system": "pf2e",
            "schema_version": 1, "build": {"name": "Hero"},
            "owner_user_id": "outsider", "editor_user_ids": ["outsider"],
            "viewer_user_ids": ["outsider"], **updates}


def test_sql_authority_replaces_all_payload_grants(ownership_store):
    ownership, _ = ownership_store
    original = _document(owner_user_ids=["outsider"])
    result = ownership.authoritative_document("campaign", original)
    assert result["owner_user_id"] == "owner"
    assert result["editor_user_ids"] == ["editor"]
    assert result["viewer_user_ids"] == ["viewer"]
    assert "owner_user_ids" not in result
    assert original["owner_user_id"] == "outsider"


@pytest.mark.parametrize("identity", [None, "missing"])
def test_unknown_identity_never_uses_payload_grants(ownership_store, identity):
    ownership, _ = ownership_store
    result = ownership.authoritative_document("campaign", _document(id=identity))
    assert result["owner_user_id"] is None
    assert result["editor_user_ids"] == result["viewer_user_ids"] == []


def test_strict_identity_check_rejects_unknown_and_inactive_rows(ownership_store):
    ownership, database = ownership_store
    with pytest.raises(ValueError, match="identity"):
        ownership.authoritative_document("campaign", _document(id="missing"), require_identity=True)
    with database.transaction() as session:
        session.get(Campaign, "campaign").trashed_at = utc_now()
    with pytest.raises(ValueError, match="active campaign"):
        ownership.authoritative_document("campaign", _document(), require_identity=True)


def test_revoked_membership_and_trashed_campaign_remove_authority(ownership_store):
    ownership, database = ownership_store
    with database.transaction() as session:
        session.delete(session.get(CampaignMembership, ("campaign", "owner")))
    assert ownership.authoritative_document("campaign", _document())["owner_user_id"] is None
    with database.transaction() as session:
        session.get(Campaign, "campaign").trashed_at = utc_now()
    result = ownership.authoritative_document("campaign", _document())
    assert result["editor_user_ids"] == result["viewer_user_ids"] == []
    assert ownership.characters_for_user("editor") == []


def test_cross_campaign_identity_and_locator_mismatch_are_rejected(ownership_store):
    ownership, _ = ownership_store
    with pytest.raises(ValueError, match="campaign"):
        ownership.authoritative_document("other-campaign", _document(campaign_id="other-campaign"))
    with pytest.raises(ValueError, match="locator"):
        ownership.authoritative_document("campaign", _document(),
                                          legacy_storage="party_data", filename="copy.json")


def test_idless_lookup_requires_explicit_exact_locator(ownership_store):
    ownership, _ = ownership_store
    result = ownership.authoritative_document("campaign", _document(id=None),
                                               legacy_storage="party_data", filename="hero.json")
    assert result["id"] == "hero"
    assert result["owner_user_id"] == "owner"
    result = ownership.authoritative_document("campaign", _document(id="forged"),
                                               legacy_storage="party_data", filename="hero.json")
    assert result["owner_user_id"] is None


def test_json_and_shadow_keep_original_document(ownership_store, monkeypatch):
    ownership, _ = ownership_store
    original = _document()
    for mode in ("json", "shadow"):
        monkeypatch.setenv("OWNERSHIP_BACKEND", mode)
        assert ownership.authoritative_document("campaign", original) is original


def test_character_cards_require_sql_owner_and_live_membership(ownership_store):
    ownership, database = ownership_store
    assert ownership.characters_for_user("owner") == [{
        "campaign_id": "campaign", "campaign_name": "campaign", "system": "pf2e",
        "file": "hero.json", "id": "hero", "name": "Hero",
    }]
    assert ownership.characters_for_user("editor") == []
    assert ownership.characters_for_user("outsider") == []
    with database.transaction() as session:
        session.get(Campaign, "campaign").trashed_at = utc_now()
    assert ownership.characters_for_user("owner") == []


def test_registration_ignores_payload_owner_and_updates_canonical_checksum(ownership_store):
    ownership, database = ownership_store
    doc = _document(id="new", owner_user_ids=["outsider"])
    saved = ownership.register_character("campaign", "party_data", "new.json", doc)
    assert saved["owner_user_id"] is None
    assert saved["editor_user_ids"] == saved["viewer_user_ids"] == []
    assert "owner_user_ids" not in saved
    checksum = hashlib.sha256(json.dumps(saved, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with database.session() as session:
        assert session.get(Character, "new").content_checksum == checksum
        assert session.scalar(select(CharacterAssignment).where(CharacterAssignment.character_id == "new")) is None


def test_registration_explicit_owner_requires_membership(ownership_store):
    ownership, database = ownership_store
    with pytest.raises(ValueError, match="membership"):
        ownership.register_character("campaign", "party_data", "new.json",
                                     _document(id="new"), owner_user_id="outsider")
    with database.session() as session:
        assert session.get(Character, "new") is None
    saved = ownership.register_character("campaign", "party_data", "new.json",
                                         _document(id="new"), owner_user_id="editor")
    assert saved["owner_user_id"] == "editor"
    with database.session() as session:
        assert session.get(CampaignMembership, ("campaign", "editor")).character_id == "new"


def test_new_idless_registration_generates_identity_and_passes_final_payload_to_writer(ownership_store):
    ownership, database = ownership_store
    written = []
    saved = ownership.register_character("campaign", "party_data", "new.json", _document(id=None),
                                         owner_user_id="editor", write_document=written.append)
    assert len(saved["id"]) == 32
    assert saved["owner_user_id"] == "editor"
    assert written == [saved]
    with database.session() as session:
        assert session.get(Character, saved["id"]).legacy_file == "new.json"


def test_registration_preserves_existing_owner_and_never_reclaims_released_identity(ownership_store):
    ownership, database = ownership_store
    result = ownership.register_character("campaign", "party_data", "hero.json",
                                          _document(), owner_user_id="editor")
    assert result["owner_user_id"] == "owner"
    ownership.release_character("campaign", "hero", "gm")
    result = ownership.register_character("campaign", "party_data", "hero.json",
                                          _document(), owner_user_id="owner")
    assert result["owner_user_id"] is None


@pytest.mark.parametrize("cid,filename,doc,match", [
    ("other-campaign", "hero.json", _document(campaign_id="other-campaign"), "campaign"),
    ("campaign", "copy.json", _document(), "locator"),
    ("campaign", "hero.json", _document(id="different"), "identity"),
    ("campaign", "../hero.json", _document(), "filename"),
    ("campaign", "new.json", _document(id="new", campaign_id="other-campaign"), "campaign"),
])
def test_registration_rejects_identity_and_locator_reassignment(ownership_store, cid, filename, doc, match):
    ownership, _ = ownership_store
    with pytest.raises(ValueError, match=match):
        ownership.register_character(cid, "party_data", filename, doc)


def test_registration_reuses_explicit_locator_for_missing_id(ownership_store):
    ownership, _ = ownership_store
    result = ownership.register_character("campaign", "party_data", "hero.json", _document(id=None))
    assert result["id"] == "hero"
    assert result["owner_user_id"] == "owner"


def test_failed_payload_write_rolls_back_registration(ownership_store):
    ownership, database = ownership_store
    def fail_write(_doc):
        raise RuntimeError("simulated write failure")
    with pytest.raises(RuntimeError, match="simulated"):
        ownership.register_character("campaign", "party_data", "new.json", _document(id="new"),
                                     owner_user_id="editor", write_document=fail_write)
    with database.session() as session:
        assert session.get(Character, "new") is None
        assert session.get(CampaignMembership, ("campaign", "editor")).character_id is None


def test_release_requires_gm_and_preserves_non_owner_assignments(ownership_store):
    ownership, database = ownership_store
    with pytest.raises(ValueError, match="GM"):
        ownership.release_character("campaign", "hero", "owner")
    ownership.release_character("campaign", "hero", "gm")
    with database.session() as session:
        assert session.get(CharacterAssignment, ("campaign", "hero", "owner")) is None
        assert session.get(CharacterAssignment, ("campaign", "hero", "editor")).role == "editor"
        assert session.get(CharacterAssignment, ("campaign", "hero", "viewer")).role == "viewer"
        assert session.get(CampaignMembership, ("campaign", "owner")).character_id is None
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "character.released")) is not None


def test_release_does_not_clear_an_owners_unrelated_membership_link(ownership_store):
    ownership, database = ownership_store
    saved = ownership.register_character("campaign", "party_data", "new.json", _document(id="new"),
                                         owner_user_id="owner")
    ownership.release_character("campaign", "hero", "gm")
    with database.session() as session:
        assert session.get(CampaignMembership, ("campaign", "owner")).character_id == saved["id"]


def test_delete_clears_links_and_preserves_redemption_history(ownership_store):
    ownership, database = ownership_store
    with database.transaction() as session:
        session.add(Invitation(code="JOIN", campaign_id="campaign", role="player",
                               character_id="hero", remaining_uses=0))
        session.flush()
        session.add(InviteRedemption(invitation_code="JOIN", campaign_id="campaign",
                                     character_id="hero", user_id="owner"))
    with pytest.raises(ValueError, match="allowed"):
        ownership.delete_character("campaign", "hero", "viewer")
    ownership.delete_character("campaign", "hero", "owner")
    with database.session() as session:
        assert session.get(Character, "hero") is None
        assert session.get(CampaignMembership, ("campaign", "owner")).character_id is None
        invitation = session.get(Invitation, "JOIN")
        assert invitation.character_id is None
        assert invitation.revoked_at is not None
        redemption = session.scalar(select(InviteRedemption))
        assert redemption.character_id is None
        assert redemption.details["deleted_character_id"] == "hero"
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "character.deleted")) is not None


def test_failed_unlink_rolls_back_identity_and_membership_delete(ownership_store):
    ownership, database = ownership_store
    def fail_delete():
        raise RuntimeError("simulated delete failure")
    with pytest.raises(RuntimeError, match="simulated"):
        ownership.delete_character("campaign", "hero", "owner", delete_document=fail_delete)
    with database.session() as session:
        assert session.get(Character, "hero") is not None
        assert session.get(CampaignMembership, ("campaign", "owner")).character_id == "hero"
        assert session.get(CharacterAssignment, ("campaign", "hero", "owner")) is not None
        assert session.scalar(select(AuditEvent).where(AuditEvent.action == "character.deleted")) is None


def test_batch_normalizes_every_payload_and_updates_metadata_together(ownership_store):
    ownership, database = ownership_store
    ownership.register_character("campaign", "party_data", "second.json", _document(id="second"))
    documents = [_document(build={"name": "Updated Hero"}), _document(id="second")]
    written = []
    def write_all(normalized):
        written.extend(normalized)
        return ["saved", "both"]
    result = ownership.write_character_batch([
        ("campaign", "party_data", "hero.json", documents[0]),
        ("campaign", "party_data", "second.json", documents[1]),
    ], write_documents=write_all)
    assert result == ["saved", "both"]
    assert [doc["owner_user_id"] for doc in written] == ["owner", None]
    with database.session() as session:
        for doc in written:
            checksum = hashlib.sha256(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                                 separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            assert session.get(Character, doc["id"]).content_checksum == checksum
        assert session.get(Character, "hero").display_name == "Updated Hero"


@pytest.mark.parametrize("failure", ["writer", "unknown_identity", "duplicate"])
def test_batch_failure_rolls_back_all_sql_metadata(ownership_store, failure):
    ownership, database = ownership_store
    entries = [("campaign", "party_data", "hero.json", _document(build={"name": "New Name"}))]
    if failure == "unknown_identity":
        entries.append(("campaign", "party_data", "missing.json", _document(id="missing")))
    elif failure == "duplicate":
        entries += entries
    def fail_write(_documents):
        if failure == "writer":
            raise RuntimeError("simulated batch failure")
        pytest.fail("invalid batch must never reach its file writer")
    with pytest.raises((RuntimeError, ValueError)):
        ownership.write_character_batch(entries, write_documents=fail_write)
    with database.session() as session:
        hero = session.get(Character, "hero")
        assert hero.content_checksum == "a" * 64
        assert hero.display_name == "Hero"


def test_claim_rejects_trashed_campaign_even_for_existing_owner(ownership_store):
    from core.persistence import RecordNotFoundError, TransactionalStore
    _, database = ownership_store
    with database.transaction() as session:
        session.get(Campaign, "campaign").trashed_at = utc_now()
    with pytest.raises(RecordNotFoundError, match="campaign"):
        TransactionalStore(database.session_factory).claim_character(
            character_id="hero", user_id="owner",
        )


def test_claim_rejects_character_outside_explicit_campaign_scope(ownership_store):
    from core.persistence import RecordNotFoundError, TransactionalStore
    _, database = ownership_store
    with pytest.raises(RecordNotFoundError, match="campaign"):
        TransactionalStore(database.session_factory).claim_character(
            campaign_id="other-campaign", character_id="hero", user_id="owner",
        )
