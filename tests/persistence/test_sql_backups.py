"""Archives preserve sheet data without resurrecting stale file permissions."""

import io
import json
from pathlib import Path
import zipfile

import pytest

from core import backups, campaigns, storage
from core.persistence import runtime
from core.persistence.models import Character, CharacterAssignment, User


@pytest.fixture
def sql_archive(sqlite_database, monkeypatch, tmp_path):
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "campaigns"))
    monkeypatch.setattr(storage, "SERVER_STATE_FILE", str(tmp_path / "server.json"))
    monkeypatch.setattr(backups, "BACKUPS_DIR", str(tmp_path / "backups"))
    with sqlite_database.transaction() as session:
        session.add_all([
            User(id=uid, username=uid, normalized_username=uid,
                 display_name=uid, password_hash="unused")
            for uid in ("gm", "owner", "editor", "viewer", "outsider")
        ])
    campaign = campaigns.create_campaign("Current title", "pf2e", "gm")
    for uid in ("owner", "editor", "viewer"):
        campaigns.add_member(campaign["id"], uid, "player")
    return sqlite_database, campaign["id"]


def _sheet(cid, **updates):
    return {
        "id": "hero", "campaign_id": cid, "system": "pf2e",
        "build": {"name": "Hero"}, "hp": 42,
        "owner_user_id": "outsider", "owner_user_ids": ["outsider"],
        "editor_user_ids": ["outsider"], "viewer_user_ids": ["outsider"],
        **updates,
    }


def _register(database, cid, *, folder="party_data", system="pf2e"):
    with database.transaction() as session:
        session.add(Character(id="hero", campaign_id=cid, system=system,
                              legacy_storage=folder, legacy_file="hero.json",
                              content_checksum="a" * 64))
        session.flush()
        session.add_all([
            CharacterAssignment(campaign_id=cid, character_id="hero", user_id=role,
                                role=role)
            for role in ("owner", "editor", "viewer")
        ])


@pytest.mark.parametrize("legacy_metadata", [False, True])
def test_snapshot_uses_sql_campaign_metadata(sql_archive, legacy_metadata):
    _, cid = sql_archive
    if legacy_metadata:
        storage.atomic_write_json(storage.campaign_file(cid), {
            "id": cid, "name": "Stale title",
            "members": [{"user_id": "outsider", "role": "gm"}],
        })
    path = backups.snapshot_campaign(cid, stamp="metadata")
    with zipfile.ZipFile(path) as archive:
        assert "campaign.json" in archive.namelist()
        assert archive.namelist().count("campaign.json") == 1
        archived = json.loads(archive.read("campaign.json"))
    assert archived == campaigns.get_campaign(cid)


@pytest.mark.parametrize("folder,system", [("party_data", "pf2e"), ("cosmere_pcs", "cosmere")])
def test_snapshot_overlays_sql_character_grants_without_rewriting_source(sql_archive, folder, system):
    database, cid = sql_archive
    _register(database, cid, folder=folder, system=system)
    source = Path(storage.campaign_dir(cid)) / folder / "hero.json"
    original = _sheet(cid, system=system)
    storage.atomic_write_json(str(source), original)
    path = backups.snapshot_campaign(cid, stamp="grants")
    with zipfile.ZipFile(path) as archive:
        archived = json.loads(archive.read(f"{folder}/hero.json"))
    assert archived["owner_user_id"] == "owner"
    assert archived["editor_user_ids"] == ["editor"]
    assert archived["viewer_user_ids"] == ["viewer"]
    assert "owner_user_ids" not in archived
    assert archived["hp"] == 42
    assert json.loads(source.read_text()) == original


def test_unknown_character_is_preserved_without_payload_grants(sql_archive):
    _, cid = sql_archive
    storage.atomic_write_json(str(Path(storage.party_dir(cid)) / "orphan.json"), _sheet(cid))
    path = backups.snapshot_campaign(cid, stamp="orphan")
    with zipfile.ZipFile(path) as archive:
        archived = json.loads(archive.read("party_data/orphan.json"))
    assert archived["hp"] == 42
    assert archived["owner_user_id"] is None
    assert archived["editor_user_ids"] == archived["viewer_user_ids"] == []
    assert "owner_user_ids" not in archived


@pytest.mark.parametrize("payload", ["{broken", "[]", '{"id":"hero","campaign_id":"elsewhere"}'])
def test_invalid_character_cannot_publish_partial_snapshot(sql_archive, payload):
    _, cid = sql_archive
    (Path(storage.party_dir(cid)) / "hero.json").write_text(payload)
    with pytest.raises(ValueError):
        backups.snapshot_campaign(cid, stamp="invalid")
    target = Path(backups.BACKUPS_DIR) / cid
    assert not (target / "invalid.zip").exists()
    assert not (target / "invalid.zip.tmp").exists()


def test_automatic_backups_exclude_sql_trash_with_retained_assets(sql_archive):
    _, cid = sql_archive
    storage.atomic_write_json(storage.campaign_file(cid), {"id": cid})
    campaigns.delete_campaign(cid)
    assert Path(storage.campaign_dir(cid)).is_dir()
    assert backups._automatic_backup_due_ids() == ()
    assert backups.run_backup() == 0
    assert backups.latest_backup(cid) is None


def test_automatic_backups_include_sql_campaign_without_legacy_metadata(sql_archive):
    _, cid = sql_archive
    assert not Path(storage.campaign_file(cid)).exists()
    assert backups._automatic_backup_due_ids() == (cid,)
    assert backups.run_backup() == 1
    with zipfile.ZipFile(backups.latest_backup(cid)) as archive:
        assert json.loads(archive.read("campaign.json"))["name"] == "Current title"


def test_missing_sql_campaign_assets_do_not_advance_backup_completion(sql_archive, tmp_path, monkeypatch):
    _, cid = sql_archive
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(tmp_path / "missing-assets"))
    assert backups.run_backup() == 0
    assert backups.last_backup_at() is None
    assert cid not in storage.load_server_state().get(backups._CAMPAIGN_COMPLETION_KEY, {})
    assert backups._automatic_backup_due_ids() == (cid,)


def test_manual_snapshot_does_not_archive_sql_trash(sql_archive):
    _, cid = sql_archive
    campaigns.delete_campaign(cid)
    assert backups.snapshot_campaign(cid, stamp="trashed") is None
    assert backups.latest_backup(cid) is None


def test_campaign_export_includes_chronicle_and_excludes_transaction_artifacts(sql_archive):
    _, cid = sql_archive
    source = Path(storage.campaign_dir(cid))
    (source / "chronicle").mkdir()
    (source / "chronicle" / "index.html").write_text("Published Chronicle")
    (source / "party_data" / "hero.json.tmp").write_text("incomplete")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        assert backups.write_campaign_archive(archive, cid, include_chronicle=True)
    result.seek(0)
    with zipfile.ZipFile(result) as archive:
        assert "chronicle/index.html" in archive.namelist()
        assert "party_data/hero.json.tmp" not in archive.namelist()
        assert json.loads(archive.read("campaign.json"))["id"] == cid
    snapshot = backups.snapshot_campaign(cid, stamp="without-chronicle")
    with zipfile.ZipFile(snapshot) as archive:
        assert "chronicle/index.html" not in archive.namelist()


def test_export_blocks_unresolved_batch_journal(sql_archive):
    _, cid = sql_archive
    (Path(storage.party_dir(cid)) / "txn.character-batch-journal").write_text("{}")
    with zipfile.ZipFile(io.BytesIO(), "w") as archive:
        with pytest.raises(RuntimeError, match="unresolved character batch"):
            backups.write_campaign_archive(archive, cid, include_chronicle=True)
        assert archive.namelist() == []


def test_database_failure_never_publishes_legacy_archive(sql_archive, monkeypatch):
    _, cid = sql_archive
    storage.atomic_write_json(storage.campaign_file(cid), {"id": cid, "name": "legacy"})

    def unavailable():
        raise runtime.StoreUnavailable("database unavailable")

    monkeypatch.setattr(runtime, "database", unavailable)
    with pytest.raises(runtime.StoreUnavailable):
        backups.snapshot_campaign(cid, stamp="unavailable")
    assert backups.latest_backup(cid) is None
    assert not (Path(backups.BACKUPS_DIR) / cid / "unavailable.zip.tmp").exists()
