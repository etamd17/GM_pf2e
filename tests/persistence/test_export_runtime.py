from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from core.persistence import (
    AuditEvent, Campaign, CampaignMembership, Character, CharacterAssignment,
    Database, Invitation, User,
)
from tools import export_transactional_runtime as exporter
from tools import migrate_transactional_store as migration


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"
GM = "11111111111111111111111111111111"
PLAYER = "22222222222222222222222222222222"
CAMPAIGN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CHARACTER = "cccccccccccccccccccccccccccccccc"
NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def source_bytes(source):
    return {str(path.relative_to(source)): path.read_bytes()
            for path in source.rglob("*") if path.is_file()}


@pytest.fixture
def imported_store(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, source)
    users = load(source / "users.json")
    users["metadata"] = {"private-note": "private-envelope"}
    write(source / "users.json", users)
    campaign_path = source / "campaigns" / CAMPAIGN / "campaign.json"
    campaign = load(campaign_path)
    campaign["members"][0]["custom_member_metadata"] = "retain me"
    write(campaign_path, campaign)
    url = "sqlite+pysqlite:///" + str(tmp_path / "runtime.sqlite")
    database = Database(url)
    database.create_schema()
    migration.import_store(
        source, url,
        expected_digest=migration.build_plan(source)["source_digest"],
    )
    try:
        yield source, url, database
    finally:
        database.dispose()


def test_export_uses_current_sql_authority_and_current_payloads(imported_store, tmp_path):
    source, url, database = imported_store
    with database.transaction() as session:
        user = session.get(User, PLAYER)
        user.display_name = "Current Player"
        user.password_hash = "current-password-hash"
        user.session_version = 9
        user.is_admin = True
        campaign = session.get(Campaign, CAMPAIGN)
        campaign.name = "Current Campaign"
        campaign.settings = {**campaign.settings, "tagline": "Current tagline"}
        session.get(CampaignMembership, (CAMPAIGN, PLAYER)).character_id = None
        session.get(CampaignMembership, (CAMPAIGN, GM)).character_id = CHARACTER
        session.get(CharacterAssignment, (CAMPAIGN, CHARACTER, PLAYER)).role = "viewer"
        session.flush()
        session.get(CharacterAssignment, (CAMPAIGN, CHARACTER, GM)).role = "owner"
        session.get(Invitation, "ABCD-EFGH").revoked_at = NOW
        session.add(AuditEvent(action="runtime.changed", details={"private": "private-audit"}))

    character_path = source / "campaigns" / CAMPAIGN / "party_data" / "hero.json"
    character = load(character_path)
    character["build"]["level"] = 8
    character["owner_user_id"] = "forged-owner"
    character["owner_user_ids"] = ["another-forged-owner"]
    character["editor_user_ids"] = ["forged-editor"]
    write(character_path, character)
    # Post-cutover legacy identity documents are never consulted for authority.
    write(source / "users.json", {"users": {"forged": {"is_admin": True}}})
    before = source_bytes(source)

    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert report["verified"] is True
    assert source_bytes(source) == before
    assert "current-password-hash" not in json.dumps(report)
    assert "private-envelope" not in json.dumps(report)
    assert "private-audit" not in json.dumps(report)
    if os.name != 'nt':
        assert output.stat().st_mode & 0o077 == 0

    users = load(output / "users.json")
    assert users["users"][PLAYER]["password_hash"] == "current-password-hash"
    assert users["users"][PLAYER]["is_admin"] is True
    assert users["users"][PLAYER]["session_version"] == 9
    assert users["users"][GM]["theme"] == "night"
    assert users["metadata"] == {"private-note": "private-envelope"}
    campaign = load(output / "campaigns" / CAMPAIGN / "campaign.json")
    assert campaign["name"] == "Current Campaign"
    assert campaign["tagline"] == "Current tagline"
    assert next(row for row in campaign["members"] if row["user_id"] == GM)["character_id"] == CHARACTER
    assert next(row for row in campaign["members"] if row["user_id"] == GM)["custom_member_metadata"] == "retain me"
    exported_character = load(output / "campaigns" / CAMPAIGN / "party_data" / "hero.json")
    assert exported_character["build"]["level"] == 8
    assert exported_character["owner_user_id"] == GM
    assert exported_character.get("owner_user_ids", [GM]) == [GM]
    assert exported_character["editor_user_ids"] == []
    assert exported_character["viewer_user_ids"] == [PLAYER]
    invite = load(output / "invites.json")["invites"]["ABCD-EFGH"]
    assert invite["uses_left"] == 0
    assert invite["note"] == "table invite"
    history = load(output / exporter.HISTORY_FILENAME)
    assert history["records"]["audit_events"][0]["details"] == {"private": "private-audit"}
    assert history["records"]["users"][1]["password_hash"] == "current-password-hash"

    plan = migration.build_plan(output)
    assert plan["import_allowed"] is True
    assert report["source_digest"] == plan["source_digest"]
    restore_url = "sqlite+pysqlite:///" + str(tmp_path / "restored.sqlite")
    restored = Database(restore_url)
    restored.create_schema()
    try:
        migration.import_store(output, restore_url, expected_digest=plan["source_digest"])
        assert migration.verify_store(output, restore_url)["verified"]
        with restored.session() as session:
            assert session.get(User, PLAYER).session_version == 9
            assert session.get(CharacterAssignment, (CAMPAIGN, CHARACTER, GM)).role == "owner"
            assert session.get(Invitation, "ABCD-EFGH").remaining_uses == 0
    finally:
        restored.dispose()


@pytest.mark.parametrize("location", ["campaigns", "campaigns_trash"])
def test_export_trashed_campaign_resolves_retained_or_imported_payload(
    imported_store, tmp_path, location
):
    source, url, database = imported_store
    with database.transaction() as session:
        session.get(Campaign, CAMPAIGN).trashed_at = NOW
    if location == "campaigns_trash":
        (source / location).mkdir()
        shutil.move(source / "campaigns" / CAMPAIGN, source / location / CAMPAIGN)
    before = source_bytes(source)
    output = tmp_path / "rollback"
    exporter.export_runtime(source, url, output, quiesced=True)
    assert (output / "campaigns_trash" / CAMPAIGN / "party_data" / "hero.json").exists()
    assert not (output / "campaigns" / CAMPAIGN).exists()
    assert migration.build_plan(output)["import_allowed"]
    assert source_bytes(source) == before


def test_export_requires_explicit_quiesced_operator_prerequisite(imported_store, tmp_path):
    source, url, database = imported_store
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="quiesc"):
        exporter.export_runtime(source, url, output)
    assert not output.exists()


@pytest.mark.parametrize("kind", ["inside-source", "existing-empty", "existing-data", "symlink"])
def test_export_refuses_unsafe_or_existing_targets(imported_store, tmp_path, kind):
    source, url, database = imported_store
    output = tmp_path / "rollback"
    if kind == "inside-source":
        output = source / "rollback"
    elif kind == "existing-empty":
        output.mkdir()
    elif kind == "existing-data":
        output.mkdir()
        write(output / "keep.json", {"keep": True})
    elif kind == "symlink":
        output.symlink_to(source, target_is_directory=True)
    before = source_bytes(source)
    with pytest.raises(migration.ExportTargetError):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert source_bytes(source) == before
    if kind == "existing-data":
        assert load(output / "keep.json") == {"keep": True}


@pytest.mark.parametrize("kind", ["missing", "identity-mismatch", "symlink", "traversal"])
def test_export_rejects_unsafe_character_sources(imported_store, tmp_path, kind):
    source, url, database = imported_store
    path = source / "campaigns" / CAMPAIGN / "party_data" / "hero.json"
    if kind == "missing":
        path.unlink()
    elif kind == "identity-mismatch":
        document = load(path)
        document["id"] = "different-character"
        write(path, document)
    elif kind == "symlink":
        real_path = tmp_path / "outside.json"
        shutil.move(path, real_path)
        path.symlink_to(real_path)
    elif kind == "traversal":
        with database.transaction() as session:
            session.get(Character, CHARACTER).legacy_file = "../../outside.json"
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []


def test_source_payload_change_during_export_refuses_publication(
    imported_store, tmp_path, monkeypatch
):
    source, url, database = imported_store
    original = migration.build_plan
    path = source / "campaigns" / CAMPAIGN / "party_data" / "hero.json"

    def mutate_after_read(staging):
        document = load(path)
        document["build"]["level"] += 1
        write(path, document)
        return original(staging)

    monkeypatch.setattr(migration, "build_plan", mutate_after_read)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="changed"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()


def test_cli_database_errors_do_not_disclose_url_or_query(imported_store, tmp_path, capsys):
    source, url, database = imported_store
    result = exporter.main([
        "--source", str(source), "--output-dir", str(tmp_path / "rollback"),
        "--database-url", "unsupported://private-user:private-password@host/db",
        "--quiesced",
    ])
    assert result == 2
    captured = capsys.readouterr()
    assert "private-user" not in captured.err
    assert "private-password" not in captured.err


@pytest.mark.parametrize("location", ["campaigns", "campaigns_trash"])
@pytest.mark.parametrize("when", ["before", "during"])
def test_unresolved_character_batch_blocks_export_without_source_writes(
    imported_store, tmp_path, monkeypatch, location, when
):
    source, url, database = imported_store
    if location == "campaigns_trash":
        with database.transaction() as session:
            session.get(Campaign, CAMPAIGN).trashed_at = NOW
        (source / location).mkdir()
        shutil.move(source / "campaigns" / CAMPAIGN, source / location / CAMPAIGN)
    journal = source / location / CAMPAIGN / "party_data" / ".prepared.character-batch-journal"
    original_bytes = source_bytes(source)
    if when == "before":
        write(journal, {"status": "prepared"})
    else:
        original_plan = migration.build_plan

        def leave_journal_during_export(staging):
            write(journal, {"status": "prepared"})
            return original_plan(staging)

        monkeypatch.setattr(migration, "build_plan", leave_journal_during_export)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="batch"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()
    actual_bytes = source_bytes(source)
    assert actual_bytes.pop(str(journal.relative_to(source)))
    assert actual_bytes == original_bytes


def test_windows_export_publishes_without_replacing_a_reserved_directory(
    imported_store, tmp_path, monkeypatch
):
    source, url, database = imported_store
    original_rename = exporter.os.rename
    original_replace = exporter.os.replace
    published = []

    def windows_rename(source_path, destination):
        if Path(destination).exists() or Path(destination).is_symlink():
            raise FileExistsError('Windows rename refuses an existing destination')
        published.append(Path(destination))
        return original_rename(source_path, destination)

    def windows_replace(source_path, destination):
        if Path(source_path).is_dir() and Path(destination).exists():
            raise PermissionError('Windows cannot replace an existing directory')
        return original_replace(source_path, destination)

    monkeypatch.setattr(exporter, '_is_windows', lambda: True, raising=False)
    monkeypatch.setattr(exporter.os, 'rename', windows_rename)
    monkeypatch.setattr(exporter.os, 'replace', windows_replace)
    output = tmp_path / 'rollback'
    before = source_bytes(source)
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert report['verified']
    assert published == [output]
    assert migration.build_plan(output)['import_allowed']
    assert source_bytes(source) == before


@pytest.mark.parametrize('has_contents', [False, True])
def test_windows_export_preserves_destination_created_during_publication(
    imported_store, tmp_path, monkeypatch, has_contents
):
    source, url, database = imported_store
    output = tmp_path / 'rollback'

    def windows_rename(source_path, destination):
        destination = Path(destination)
        destination.mkdir()
        if has_contents:
            write(destination / 'keep.json', {'keep': True})
        raise FileExistsError('Windows rename refuses an existing destination')

    monkeypatch.setattr(exporter, '_is_windows', lambda: True, raising=False)
    monkeypatch.setattr(exporter.os, 'rename', windows_rename)
    with pytest.raises(exporter.RuntimeExportError):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert output.is_dir()
    assert not (output / exporter.MANIFEST_FILENAME).exists()
    assert list(tmp_path.glob('.rollback.staging-*')) == []
    if has_contents:
        assert load(output / 'keep.json') == {'keep': True}
    else:
        assert list(output.iterdir()) == []
