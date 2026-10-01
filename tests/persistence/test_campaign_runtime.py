"""The campaign API must take permissions exclusively from relational rows."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from core import campaigns, storage
from core.persistence import Campaign, CampaignMembership, Character, User
from core.persistence.models import AuditEvent


@pytest.fixture
def sql_campaigns(sqlite_database, monkeypatch, tmp_path):
    from core.persistence import runtime

    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setattr(storage, "CAMPAIGNS_TRASH_DIR", str(tmp_path / "trash"))
    monkeypatch.setattr(storage, "SERVER_STATE_FILE", str(tmp_path / "server.json"))
    with sqlite_database.transaction() as session:
        session.add_all([
            User(id=uid, username=uid, normalized_username=uid,
                 display_name=uid, password_hash="unused")
            for uid in ("gm", "player", "outsider", "other-gm")
        ])
    return sqlite_database


def test_sql_create_commits_campaign_creator_and_audit_together(sql_campaigns):
    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    assert doc["members"] == [{"user_id": "gm", "role": "gm"}]
    assert Path(storage.party_dir(doc["id"])).is_dir()
    assert not Path(storage.campaign_file(doc["id"])).exists()
    with sql_campaigns.session() as session:
        assert session.get(Campaign, doc["id"]).name == "The Vault"
        assert session.get(CampaignMembership, (doc["id"], "gm")).role == "gm"
        assert session.scalar(select(AuditEvent.action)) == "campaign.created"
    with pytest.raises(ValueError, match="user"):
        campaigns.create_campaign("Missing creator", "pf2e", "absent")
    assert len(campaigns.list_campaigns()) == 1


def test_sql_campaign_ignores_json_and_snapshot_permission_poison(sql_campaigns):
    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    forged = deepcopy(doc)
    forged["members"] = [{"user_id": "outsider", "role": "gm"}]
    storage.atomic_write_json(storage.campaign_file(doc["id"]), forged)
    with sql_campaigns.transaction() as session:
        row = session.get(Campaign, doc["id"])
        row.source_payload = forged
        row.settings = {"members": forged["members"], "id": "evil"}
    assert campaigns.get_campaign(doc["id"])["members"] == doc["members"]
    assert campaigns.user_role(forged, "outsider") is None
    assert campaigns.user_role(forged, "gm") == "gm"
    assert campaigns.campaigns_for_user("outsider") == []


def test_stale_campaign_save_cannot_replace_membership_or_reactivate(sql_campaigns):
    stale = campaigns.create_campaign("The Vault", "pf2e", "gm")
    campaigns.add_member(stale["id"], "player", "player")
    stale["name"] = "Renamed"
    stale["tagline"] = "A place for heroes"
    stale["created_by"] = "outsider"
    result = campaigns.save_campaign(stale)
    assert result["name"] == "Renamed"
    assert result["tagline"] == "A place for heroes"
    assert result["created_by"] == "gm"
    assert campaigns.user_role(result, "player") == "player"
    campaigns.delete_campaign(stale["id"])
    with pytest.raises(ValueError, match="campaign"):
        campaigns.save_campaign(stale)
    assert campaigns.get_campaign(stale["id"]) is None


def test_membership_mutations_protect_last_gm_and_campaign_boundary(sql_campaigns):
    first = campaigns.create_campaign("First", "pf2e", "gm")
    second = campaigns.create_campaign("Second", "pf2e", "other-gm")
    assert campaigns.remove_member(first["id"], "gm") is None
    assert campaigns.set_member_role(first["id"], "gm", "player") is None
    campaigns.add_member(first["id"], "gm", "player")
    assert campaigns.user_role(first, "gm") == "gm"
    assert campaigns.user_role(second, "gm") is None
    campaigns.add_member(first["id"], "player", "gm")
    assert campaigns.gm_count(first) == 2
    campaigns.set_member_role(first["id"], "gm", "player")
    assert campaigns.user_role(first, "gm") == "player"
    assert campaigns.remove_member(first["id"], "gm") is not None
    assert campaigns.remove_member(first["id"], "gm") is None
    assert campaigns.set_member_role(first["id"], "outsider", "gm") is None
    with sql_campaigns.transaction() as session:
        session.add(Character(id="f" * 32, campaign_id=second["id"],
                              system="pf2e", legacy_storage="party_data",
                              legacy_file="hero.json", content_checksum="f" * 64))
    with pytest.raises(ValueError, match="character"):
        campaigns.add_member(first["id"], "outsider", "player", "f" * 32)
    assert campaigns.user_role(first, "outsider") is None


def test_sql_trash_retains_assets_and_purge_fails_closed(sql_campaigns):
    from core.persistence.campaign_runtime import UnsupportedCampaignOperation

    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    asset = Path(storage.party_dir(doc["id"])) / "hero.json"
    storage.atomic_write_json(str(asset), {"name": "Keep me"})
    storage.set_live_campaign_id(doc["id"])
    campaigns.delete_campaign(doc["id"])
    assert campaigns.get_campaign(doc["id"]) is None
    assert campaigns.list_campaigns() == []
    assert campaigns.get_live_campaign_id() is None
    assert campaigns.trashed_for_user("gm")[0]["id"] == doc["id"]
    assert campaigns.trashed_for_user("outsider") == []
    assert asset.is_file()
    assert campaigns.purge_expired_trash(ttl_days=-1) == 0
    with pytest.raises(UnsupportedCampaignOperation):
        campaigns.purge_campaign(doc["id"])
    assert asset.is_file()
    restored = campaigns.restore_campaign(doc["id"])
    assert "_trashed_at" not in restored
    assert campaigns.get_campaign(doc["id"]) is not None


def test_sql_restore_copies_imported_trash_before_activation(sql_campaigns):
    doc = campaigns.create_campaign("Imported", "cosmere", "gm")
    campaigns.delete_campaign(doc["id"])
    storage.trash_campaign_dir(doc["id"])
    source = Path(storage.campaign_trash_dir(doc["id"])) / "cosmere_pcs" / "hero.json"
    storage.atomic_write_json(str(source), {"name": "Retained"})
    campaigns.restore_campaign(doc["id"])
    assert (Path(storage.cosmere_pc_dir(doc["id"])) / "hero.json").is_file()
    assert source.is_file()


def test_sql_permissions_do_not_accept_character_from_another_campaign(sql_campaigns):
    first = campaigns.create_campaign("First", "pf2e", "gm")
    second = campaigns.create_campaign("Second", "pf2e", "other-gm")
    with sql_campaigns.transaction() as session:
        session.add(Character(id="f" * 32, campaign_id=second["id"],
                              system="pf2e", legacy_storage="party_data",
                              legacy_file="hero.json", content_checksum="f" * 64))
    forged = {"id": "f" * 32, "campaign_id": second["id"], "owner_user_id": "player"}
    assert not campaigns.can_act_on_character({"id": "player"}, first, forged)
    assert not campaigns.can_act_on_character({"id": "gm"}, first, forged)
    assert not campaigns.can_act_on_character({"id": "player"}, second, forged)
    assert campaigns.can_act_on_character({"id": "other-gm"}, second, forged)


def test_sql_create_rolls_back_when_directory_creation_fails(sql_campaigns, monkeypatch):
    from core.persistence.runtime import StoreUnavailable

    def unavailable(cid):
        raise OSError("filesystem unavailable")

    monkeypatch.setattr(storage, "ensure_campaign_dirs", unavailable)
    with pytest.raises(StoreUnavailable):
        campaigns.create_campaign("Failed", "pf2e", "gm")
    assert campaigns.list_campaigns() == []
    with sql_campaigns.session() as session:
        assert session.scalar(select(CampaignMembership)) is None
        assert session.scalar(select(AuditEvent)) is None


def test_partial_imported_trash_copy_cannot_activate_incomplete_assets(sql_campaigns, monkeypatch):
    from core.persistence import campaign_runtime
    from core.persistence.runtime import StoreUnavailable

    doc = campaigns.create_campaign("Imported", "pf2e", "gm")
    campaigns.delete_campaign(doc["id"])
    storage.trash_campaign_dir(doc["id"])
    source = Path(storage.campaign_trash_dir(doc["id"])) / "party_data" / "hero.json"
    storage.atomic_write_json(str(source), {"name": "Keep me"})
    copytree = campaign_runtime.shutil.copytree

    def fail_partway(src, dst, **kwargs):
        storage.atomic_write_json(str(Path(dst) / "partial.json"), {})
        raise OSError("copy interrupted")

    monkeypatch.setattr(campaign_runtime.shutil, "copytree", fail_partway)
    with pytest.raises(StoreUnavailable):
        campaigns.restore_campaign(doc["id"])
    assert campaigns.get_campaign(doc["id"]) is None
    assert not Path(storage.campaign_dir(doc["id"])).exists()
    assert source.is_file()
    monkeypatch.setattr(campaign_runtime.shutil, "copytree", copytree)
    assert campaigns.restore_campaign(doc["id"])["id"] == doc["id"]
    assert (Path(storage.party_dir(doc["id"])) / "hero.json").is_file()


def test_campaign_shadow_reads_keep_json_authority_without_sql_writes(sql_campaigns, monkeypatch, caplog):
    doc = campaigns.create_campaign("SQL name", "pf2e", "gm")
    legacy = deepcopy(doc)
    legacy["name"] = "Legacy name"
    legacy["members"] = [{"user_id": "player", "role": "gm"}]
    storage.atomic_write_json(storage.campaign_file(doc["id"]), legacy)
    monkeypatch.setenv("OWNERSHIP_BACKEND", "shadow")
    assert campaigns.get_campaign(doc["id"]) == legacy
    assert campaigns.user_role(legacy, "player") == "gm"
    assert "ownership_shadow_mismatch" in caplog.text
    assert "Legacy name" not in caplog.text
    with sql_campaigns.session() as session:
        assert session.get(Campaign, doc["id"]).name == "SQL name"
        assert session.get(CampaignMembership, (doc["id"], "player")) is None


def test_campaign_shadow_normalizes_time_membership_order_and_optional_fields(
    sql_campaigns, monkeypatch, caplog
):
    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    campaigns.add_member(doc['id'], 'player', 'player')
    legacy = campaigns.get_campaign(doc['id'])
    legacy['members'].reverse()
    for member in legacy['members']:
        member['character_id'] = None
        member['old_display_label'] = 'ignored presentation metadata'
    legacy.pop('system_config')
    created_at = datetime.fromisoformat(legacy['created_at']).replace(tzinfo=timezone.utc)
    legacy['created_at'] = created_at.astimezone(timezone(timedelta(hours=-4))).isoformat(timespec='microseconds')
    storage.atomic_write_json(storage.campaign_file(doc['id']), legacy)
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'shadow')
    assert campaigns.get_campaign(doc['id']) == legacy
    assert 'ownership_shadow_mismatch' not in caplog.text
    assert 'ownership_shadow_unavailable' not in caplog.text


@pytest.mark.parametrize('change', ['role', 'character', 'creator', 'system', 'settings', 'duplicate_member', 'trash'])
def test_campaign_shadow_still_detects_domain_changes(
    sql_campaigns, monkeypatch, caplog, change
):
    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    legacy = deepcopy(doc)
    if change == 'role':
        legacy['members'][0]['role'] = 'player'
    elif change == 'character':
        legacy['members'][0]['character_id'] = 'different-character'
    elif change == 'creator':
        legacy['created_by'] = 'outsider'
    elif change == 'system':
        legacy['system'] = 'cosmere'
    elif change == 'settings':
        legacy['system_config'] = {'free_archetype': True}
    elif change == 'duplicate_member':
        legacy['members'].append(deepcopy(legacy['members'][0]))
    elif change == 'trash':
        legacy['_trashed_at'] = '2026-10-01T00:00:00Z'
    storage.atomic_write_json(storage.campaign_file(doc['id']), legacy)
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'shadow')
    assert campaigns.get_campaign(doc['id']) == legacy
    assert 'ownership_shadow_mismatch' in caplog.text
    assert 'different-character' not in caplog.text


def test_sql_store_failure_never_falls_back_to_legacy_campaign(sql_campaigns, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from core.persistence import runtime

    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    storage.atomic_write_json(storage.campaign_file(doc["id"]), doc)

    def unavailable():
        raise OperationalError("secret query", {}, RuntimeError("secret diagnostic"))

    monkeypatch.setattr(runtime, "database", unavailable)
    with pytest.raises(runtime.StoreUnavailable, match="database is unavailable") as exc:
        campaigns.get_campaign(doc["id"])
    assert "secret" not in str(exc.value)


def test_demoted_actor_cannot_mutate_with_stale_route_authorization(sql_campaigns):
    from core.persistence import campaign_runtime

    stale = campaigns.create_campaign("The Vault", "pf2e", "gm")
    cid = stale["id"]
    campaigns.add_member(cid, "other-gm", "gm")
    campaigns.set_member_role(cid, "gm", "player")
    operations = (
        lambda: campaign_runtime.add_member(cid, "outsider", "gm", actor_user_id="gm"),
        lambda: campaign_runtime.set_member_role(cid, "gm", "gm", actor_user_id="gm"),
        lambda: campaign_runtime.remove_member(cid, "other-gm", actor_user_id="gm"),
        lambda: campaign_runtime.save_campaign(stale, actor_user_id="gm"),
        lambda: campaign_runtime.delete_campaign(cid, actor_user_id="gm"),
    )
    for operation in operations:
        with pytest.raises(ValueError, match="GM permission"):
            operation()
    assert campaigns.user_role(stale, "outsider") is None
    assert campaigns.user_role(stale, "gm") == "player"
    assert campaigns.user_role(stale, "other-gm") == "gm"
    campaigns.delete_campaign(cid)
    with pytest.raises(ValueError, match="GM permission"):
        campaign_runtime.restore_campaign(cid, actor_user_id="gm")
    assert campaigns.get_campaign(cid) is None


def test_sql_system_repair_preserves_members_and_requires_current_actor(sql_campaigns):
    from core.persistence import campaign_runtime

    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    doc["system"] = "cosmere"
    doc["members"] = [{"user_id": "outsider", "role": "gm"}]
    saved = campaign_runtime.save_campaign(doc, actor_user_id="gm")
    assert saved["system"] == "cosmere"
    assert saved["members"] == [{"user_id": "gm", "role": "gm"}]
    doc["system"] = "invalid"
    with pytest.raises(ValueError, match="unknown campaign system"):
        campaign_runtime.save_campaign(doc, actor_user_id="gm")
    assert campaigns.get_campaign(doc["id"])["system"] == "cosmere"
    with sql_campaigns.transaction() as session:
        session.get(User, "outsider").is_admin = True
    doc["system"] = "pf2e"
    assert campaign_runtime.save_campaign(doc, actor_user_id="outsider")["system"] == "pf2e"


def test_disappearing_http_actor_does_not_become_trusted_internal_call(sql_campaigns, monkeypatch):
    from flask import Flask
    from core import auth

    doc = campaigns.create_campaign("The Vault", "pf2e", "gm")
    monkeypatch.setattr(auth, "current_user", lambda: None)
    with Flask(__name__).test_request_context():
        with pytest.raises(ValueError, match="valid account"):
            campaigns.save_campaign(doc)
    assert campaigns.get_campaign(doc["id"]) == doc
