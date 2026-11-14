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


def test_campaign_runs_do_not_change_the_runtime_export(imported_store, tmp_path):
    """PE-12: `_envelopes` reads legacy runs only; the history still lists every run."""
    from core.persistence import MigrationRun, MigrationRunRepository

    source, url, database = imported_store
    baseline = exporter.export_runtime(source, url, tmp_path / "before", quiesced=True)
    with database.session() as session:
        envelopes_before = exporter._envelopes(session)
    with database.transaction() as session:
        repository = MigrationRunRepository(session)
        for kind, status in (("import", "succeeded"), ("export", "exported")):
            run = repository.start_campaign_run(
                campaign_id=CAMPAIGN, kind=kind, source_checksum="f" * 64,
                stream_epoch=2, tool_version="test")
            repository.finish(run, status=status)
            # Newer than the legacy import, so an unscoped reader would pick it.
            run.completed_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with database.session() as session:
        assert exporter._envelopes(session) == envelopes_before
        campaign_runs = session.query(MigrationRun).filter(
            MigrationRun.campaign_id.isnot(None)).count()
    assert campaign_runs == 2

    report = exporter.export_runtime(source, url, tmp_path / "after", quiesced=True)
    assert report["verified"] is True
    assert report["source_digest"] == baseline["source_digest"]
    assert load(tmp_path / "after" / "users.json") == load(tmp_path / "before" / "users.json")
    history = load(tmp_path / "after" / exporter.HISTORY_FILENAME)
    runs = history["migration_runs"]
    assert len(runs) == 3
    assert sorted(run["kind"] for run in runs) == ["export", "import", "import"]


# --- D&D campaigns (PE-11a store layout, PE-11c file-authoritative export) --------

DND5E_PC = "d" * 32
DND5E_PC2 = "e" * 32
DND5E_NEW = "f" * 32


def _dnd5e_doc(character_id, owner=PLAYER, **extra):
    return {"id": character_id, "campaign_id": CAMPAIGN, "system": "dnd5e",
            "name": f"Hero {character_id[0]}", "owner_user_id": owner, **extra}


def _dnd5e_path(source, character_id, root="campaigns"):
    return source / root / CAMPAIGN / "dnd5e" / "characters" / f"{character_id}.json"


def _dnd5e_store(tmp_path, *, with_character, characters=()):
    source = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, source)
    campaign_path = source / "campaigns" / CAMPAIGN / "campaign.json"
    campaign = load(campaign_path)
    campaign["system"] = "dnd5e"
    for member in campaign["members"]:
        member.pop("character_id", None)
    write(campaign_path, campaign)
    shutil.rmtree(source / "campaigns" / CAMPAIGN / "party_data")
    invites = load(source / "invites.json")
    for invite in invites["invites"].values():
        invite["character_id"] = None
    write(source / "invites.json", invites)
    write(source / "campaigns" / CAMPAIGN / "dnd5e" / "encounter.json", {"combatants": []})
    for character_id in ((DND5E_PC,) if with_character else ()) + tuple(characters):
        write(_dnd5e_path(source, character_id), _dnd5e_doc(character_id))
    url = "sqlite+pysqlite:///" + str(tmp_path / "runtime.sqlite")
    database = Database(url)
    database.create_schema()
    plan = migration.build_plan(source)
    assert plan["import_allowed"], plan["blocking_conflicts"]
    migration.import_store(source, url, expected_digest=plan["source_digest"])
    return source, url, database


def test_export_of_a_dnd5e_campaign_without_characters_succeeds(tmp_path):
    source, url, database = _dnd5e_store(tmp_path, with_character=False)
    try:
        before = source_bytes(source)
        output = tmp_path / "rollback"
        exporter.export_runtime(source, url, output, quiesced=True)
        assert load(output / "campaigns" / CAMPAIGN / "campaign.json")["system"] == "dnd5e"
        assert migration.build_plan(output)["import_allowed"]
        assert source_bytes(source) == before
    finally:
        database.dispose()


def test_unresolved_batch_in_the_dnd5e_store_blocks_export(tmp_path):
    source, url, database = _dnd5e_store(tmp_path, with_character=False)
    try:
        journal = (source / "campaigns" / CAMPAIGN / "dnd5e" / "characters"
                   / ".prepared.character-batch-journal")
        write(journal, {"status": "prepared"})
        output = tmp_path / "rollback"
        with pytest.raises(exporter.RuntimeExportError, match="batch"):
            exporter.export_runtime(source, url, output, quiesced=True)
        assert not output.exists()
    finally:
        database.dispose()


@pytest.mark.parametrize("parts", [("dnd5e",), ("dnd5e", "characters")])
def test_symlinked_dnd5e_store_blocks_export(tmp_path, parts):
    source, url, database = _dnd5e_store(tmp_path, with_character=False)
    try:
        store = source.joinpath("campaigns", CAMPAIGN, *parts)
        store.mkdir(parents=True, exist_ok=True)
        elsewhere = tmp_path / "elsewhere"
        shutil.move(store, elsewhere)
        try:
            store.symlink_to(elsewhere, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks are unavailable here")
        output = tmp_path / "rollback"
        with pytest.raises(exporter.RuntimeExportError, match="symbolic link"):
            exporter.export_runtime(source, url, output, quiesced=True)
        assert not output.exists()
    finally:
        database.dispose()


@pytest.fixture
def dnd5e_store(tmp_path):
    """Two imported D&D characters (DND5E_PC, DND5E_PC2), both owned by PLAYER."""
    source, url, database = _dnd5e_store(tmp_path, with_character=True, characters=(DND5E_PC2,))
    with database.session() as session:
        assert session.get(Character, DND5E_PC).legacy_storage == "dnd5e_characters"
    try:
        yield source, url, database
    finally:
        database.dispose()


def _sql_snapshot(database):
    with database.session() as session:
        return migration._database_records(session)


def _dnd5e_locators(records):
    return sorted(
        ({key: row[key] for key in exporter.LOCATOR_KEYS} for row in records["characters"]
         if row["legacy_storage"] == "dnd5e_characters"),
        key=lambda locator: locator["id"],
    )


def _reimport_allowed(output):
    plan = migration.build_plan(output)
    assert plan["import_allowed"], plan["blocking_conflicts"]
    return migration.inspect_source(output).records


def _kinds(report):
    return sorted((entry["kind"], entry.get("character_id")) for entry in report["dnd5e_drift"])


def test_dnd5e_drift_after_import_is_exported_from_the_files(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    write(_dnd5e_path(source, DND5E_PC), _dnd5e_doc(DND5E_PC, owner=GM))  # reassigned
    write(_dnd5e_path(source, DND5E_NEW), _dnd5e_doc(DND5E_NEW))  # created
    _dnd5e_path(source, DND5E_PC2).unlink()  # deleted
    before, sql_before = source_bytes(source), _sql_snapshot(database)

    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)

    assert load(_dnd5e_path(output, DND5E_PC))["owner_user_id"] == GM
    assert load(_dnd5e_path(output, DND5E_NEW))["owner_user_id"] == PLAYER
    assert not _dnd5e_path(output, DND5E_PC2).exists()
    assert _kinds(report) == [("file_without_row", DND5E_NEW), ("owner_differs", DND5E_PC),
                              ("row_file_missing", DND5E_PC2)]
    owner_drift = next(entry for entry in report["dnd5e_drift"] if entry["kind"] == "owner_differs")
    assert (owner_drift["file_owner"], owner_drift["sql_owner"]) == (GM, PLAYER)
    assert load(output / exporter.MANIFEST_FILENAME)["dnd5e_drift"] == report["dnd5e_drift"]
    assert [row["id"] for row in _dnd5e_locators(_reimport_allowed(output))] == [DND5E_PC, DND5E_NEW]
    assert source_bytes(source) == before
    assert _sql_snapshot(database) == sql_before


def test_dnd5e_files_without_drift_export_byte_identical(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert report["dnd5e_drift"] == []
    for character_id in (DND5E_PC, DND5E_PC2):
        assert _dnd5e_path(output, character_id).read_bytes() == _dnd5e_path(source, character_id).read_bytes()
    assert _dnd5e_locators(_reimport_allowed(output)) == _dnd5e_locators(_sql_snapshot(database))
    # The encounter and other non-character D&D files return with the DATA_DIR merge.
    assert not (output / "campaigns" / CAMPAIGN / "dnd5e" / "encounter.json").exists()


def test_dnd5e_stale_owner_is_cleared_in_the_staged_copy_only(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    with database.transaction() as session:
        session.delete(session.get(CampaignMembership, (CAMPAIGN, PLAYER)))
    before = source_bytes(source)

    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)

    staged = load(_dnd5e_path(output, DND5E_PC))
    assert staged["owner_user_id"] is None
    assert {key: value for key, value in staged.items() if key != "owner_user_id"} == {
        key: value for key, value in _dnd5e_doc(DND5E_PC).items() if key != "owner_user_id"}
    cleared = [entry for entry in report["dnd5e_drift"] if entry["kind"] == "stale_owner_cleared"]
    assert sorted((entry["character_id"], entry["owner_user_id"]) for entry in cleared) == [
        (DND5E_PC, PLAYER), (DND5E_PC2, PLAYER)]
    assert source_bytes(source) == before
    _reimport_allowed(output)


def test_dnd5e_grant_lists_are_copied_unchanged(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    document = _dnd5e_doc(DND5E_PC, owner_user_ids=["someone"], editor_user_ids=["x"],
                          viewer_user_ids=["y"])
    write(_dnd5e_path(source, DND5E_PC), document)
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert _dnd5e_path(output, DND5E_PC).read_bytes() == _dnd5e_path(source, DND5E_PC).read_bytes()
    assert report["dnd5e_drift"] == []
    _reimport_allowed(output)


@pytest.mark.parametrize("defect", ["id-mismatch", "missing-system", "empty-system", "pf2e-system"])
def test_bad_dnd5e_file_refuses_export_naming_the_file(dnd5e_store, tmp_path, defect):
    source, url, database = dnd5e_store
    document = _dnd5e_doc(DND5E_PC)
    if defect == "id-mismatch":
        document["id"] = "0" * 32
    elif defect == "missing-system":
        del document["system"]
    else:
        document["system"] = "" if defect == "empty-system" else "pf2e"
    write(_dnd5e_path(source, DND5E_PC), document)
    code = "dnd5e_locator_mismatch" if defect == "id-mismatch" else "invalid_character_system"
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError) as caught:
        exporter.export_runtime(source, url, output, quiesced=True)
    assert str(caught.value) == (
        f"D&D character file campaigns/{CAMPAIGN}/dnd5e/characters/{DND5E_PC}.json "
        f"cannot be exported ({code})"
    )
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []


def test_other_blocking_conflicts_keep_the_generic_message(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    # An unparseable D&D file is not one of the two named codes.
    _dnd5e_path(source, DND5E_PC).write_text("{not json", encoding="utf-8")
    with pytest.raises(exporter.RuntimeExportError,
                       match="^Current SQL state cannot form a valid legacy rollback tree$"):
        exporter.export_runtime(source, url, tmp_path / "rollback", quiesced=True)


def test_stale_dnd5e_campaign_id_exports_and_is_reported(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    write(_dnd5e_path(source, DND5E_PC), {**_dnd5e_doc(DND5E_PC), "campaign_id": "b" * 32})
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert _kinds(report) == [("dnd5e_character_campaign_inferred", DND5E_PC)]
    assert _dnd5e_path(output, DND5E_PC).read_bytes() == _dnd5e_path(source, DND5E_PC).read_bytes()
    _reimport_allowed(output)


def test_trashed_dnd5e_campaign_exports_from_both_roots(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    with database.transaction() as session:
        session.get(Campaign, CAMPAIGN).trashed_at = NOW
    trashed = _dnd5e_path(source, DND5E_PC2, root="campaigns_trash")
    trashed.parent.mkdir(parents=True)
    shutil.move(_dnd5e_path(source, DND5E_PC2), trashed)
    # When both roots hold a stem, the active copy wins.
    write(_dnd5e_path(source, DND5E_PC, root="campaigns_trash"), _dnd5e_doc(DND5E_PC, owner=GM))

    output = tmp_path / "rollback"
    exporter.export_runtime(source, url, output, quiesced=True)

    assert not (output / "campaigns" / CAMPAIGN).exists()
    for character_id in (DND5E_PC, DND5E_PC2):
        assert _dnd5e_path(output, character_id, root="campaigns_trash").exists()
    assert load(_dnd5e_path(output, DND5E_PC, root="campaigns_trash"))["owner_user_id"] == PLAYER
    records = _reimport_allowed(output)
    assert {row["legacy_root"] for row in _dnd5e_locators(records)} == {"campaigns_trash"}


def test_excluded_dnd5e_id_is_left_out_entirely(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True,
                                     excluded_character_ids=frozenset({DND5E_PC}))
    assert not _dnd5e_path(output, DND5E_PC).exists()
    assert _dnd5e_path(output, DND5E_PC2).exists()
    assert report["dnd5e_drift"] == []
    assert [row["id"] for row in _dnd5e_locators(_reimport_allowed(output))] == [DND5E_PC2]


@pytest.mark.parametrize("variant", ["deleted", "excluded"])
@pytest.mark.parametrize("case", ["live", "exhausted", "revoked", "selection"])
def test_references_to_an_omitted_dnd5e_character(dnd5e_store, tmp_path, case, variant):
    source, url, database = dnd5e_store
    with database.transaction() as session:
        if case == "selection":
            session.get(CampaignMembership, (CAMPAIGN, PLAYER)).character_id = DND5E_PC
        else:
            session.add(Invitation(
                code="SCOP-EDPC", campaign_id=CAMPAIGN, role="player", character_id=DND5E_PC,
                remaining_uses=0 if case == "exhausted" else 2, created_by_user_id=GM,
                revoked_at=NOW if case == "revoked" else None,
            ))
    excluded = frozenset()
    if variant == "deleted":
        _dnd5e_path(source, DND5E_PC).unlink()
    else:
        excluded = frozenset({DND5E_PC})
    sql_before = _sql_snapshot(database)

    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True,
                                     excluded_character_ids=excluded)

    expected = [] if variant == "excluded" else [("row_file_missing", DND5E_PC)]
    if case == "selection":
        expected.append(("membership_selection_cleared", DND5E_PC))
        member = next(row for row in load(output / "campaigns" / CAMPAIGN / "campaign.json")["members"]
                      if row["user_id"] == PLAYER)
        assert (member["role"], member["character_id"]) == ("player", None)
    else:
        expected.append(("invite_omitted", DND5E_PC))
        staged = load(output / "invites.json")["invites"]
        assert "SCOP-EDPC" not in staged
        assert all(invite["character_id"] is None for invite in staged.values())
        history = load(output / exporter.HISTORY_FILENAME)
        sql_invite = next(row for row in sql_before["invites"] if row["code"] == "SCOP-EDPC")
        assert next(row for row in history["records"]["invites"]
                    if row["code"] == "SCOP-EDPC") == json.loads(json.dumps(sql_invite))
        omitted = next(entry for entry in report["dnd5e_drift"] if entry["kind"] == "invite_omitted")
        assert (omitted["code"], omitted["state"]) == ("SCOP-EDPC", case)
    assert _kinds(report) == sorted(expected)
    _reimport_allowed(output)
    assert _sql_snapshot(database) == sql_before


@pytest.mark.parametrize("reference", ["invite", "selection"])
def test_references_to_a_missing_old_store_character_still_refuse(imported_store, tmp_path, reference):
    source, url, database = imported_store
    if reference == "invite":
        with database.transaction() as session:
            session.add(Invitation(code="SCOP-EHER", campaign_id=CAMPAIGN, role="player",
                                   character_id=CHARACTER, remaining_uses=1, created_by_user_id=GM))
    (source / "campaigns" / CAMPAIGN / "party_data" / "hero.json").unlink()
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="missing"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot create a file name with a newline")
def test_dnd5e_stem_with_a_trailing_newline_is_not_a_character(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    odd = _dnd5e_path(source, DND5E_PC).with_name("a" * 32 + "\n.json")
    write(odd, _dnd5e_doc("a" * 32))
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert not (output / odd.relative_to(source)).exists()
    assert report["dnd5e_drift"] == []


@pytest.mark.parametrize("stem", ["F" * 32, "g" * 32, "d" * 31, "hero"])
def test_nonconforming_dnd5e_stems_are_not_characters(dnd5e_store, tmp_path, stem):
    source, url, database = dnd5e_store
    odd = _dnd5e_path(source, DND5E_PC).with_name(stem + ".json")
    write(odd, _dnd5e_doc(stem))
    output = tmp_path / "rollback"
    report = exporter.export_runtime(source, url, output, quiesced=True)
    assert sorted(path.name for path in _dnd5e_path(output, DND5E_PC).parent.iterdir()) == [
        DND5E_PC + ".json", DND5E_PC2 + ".json"]
    assert report["dnd5e_drift"] == []


def test_authority_check_compares_dnd5e_locators_with_the_staged_files(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    output = tmp_path / "rollback"
    exporter.export_runtime(source, url, output, quiesced=True)
    records = _sql_snapshot(database)
    state = {row["code"]: {"revoked_at": None} for row in records["invites"]}
    staged = _dnd5e_locators(migration.inspect_source(output).records)
    exporter._verify_authority(output, records, state, {"omitted": set(), "locators": staged})
    with pytest.raises(exporter.RuntimeExportError, match="Character registry changed"):
        exporter._verify_authority(output, records, state, {"omitted": set(), "locators": staged[:1]})


def test_symlinked_dnd5e_character_file_refuses_export(dnd5e_store, tmp_path):
    source, url, database = dnd5e_store
    real = tmp_path / "elsewhere.json"
    shutil.move(_dnd5e_path(source, DND5E_PC), real)
    try:
        _dnd5e_path(source, DND5E_PC).symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable here")
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="symbolic link"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_dnd5e_store_junction_outside_data_dir_refuses_export(dnd5e_store, tmp_path):
    import subprocess

    source, url, database = dnd5e_store
    store = _dnd5e_path(source, DND5E_PC).parent
    outside = tmp_path / "outside"
    shutil.move(store, outside)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(store), str(outside)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("junctions are unavailable here")
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="not at its own location"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()


OTHER_CAMPAIGN = "b" * 32


def _junction(link, target):
    import subprocess

    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True)
    if made.returncode != 0:
        pytest.skip("junctions are unavailable here")


def _assert_nothing_published(source, before, database, records, tmp_path, output):
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []
    assert source_bytes(source) == before
    assert _sql_snapshot(database) == records


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_dnd5e_store_junction_to_another_campaign_inside_data_dir_refuses(tmp_path):
    # Codex PE-11c R1-1: containment in DATA_DIR admitted another campaign's store.
    source, url, database = _dnd5e_store(tmp_path, with_character=False)
    try:
        foreign = source / "campaigns" / OTHER_CAMPAIGN / "dnd5e" / "characters"
        write(foreign / f"{DND5E_NEW}.json", {**_dnd5e_doc(DND5E_NEW), "campaign_id": OTHER_CAMPAIGN})
        store = _dnd5e_path(source, DND5E_NEW).parent
        store.parent.mkdir(parents=True, exist_ok=True)
        _junction(store, foreign)
        assert not store.is_symlink() and store.resolve().is_relative_to(source.resolve())
        before, records = source_bytes(source), _sql_snapshot(database)
        output = tmp_path / "rollback"
        with pytest.raises(exporter.RuntimeExportError, match="not at its own location"):
            exporter.export_runtime(source, url, output, quiesced=True)
        _assert_nothing_published(source, before, database, records, tmp_path, output)
    finally:
        database.dispose()


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_pf2e_store_junction_inside_data_dir_refuses(imported_store, tmp_path):
    source, url, database = imported_store
    store = source / "campaigns" / CAMPAIGN / "party_data"
    moved = source / "campaigns" / OTHER_CAMPAIGN / "party_data"
    moved.parent.mkdir(parents=True)
    shutil.move(store, moved)
    _junction(store, moved)
    before, records = source_bytes(source), _sql_snapshot(database)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="not at its own location"):
        exporter.export_runtime(source, url, output, quiesced=True)
    _assert_nothing_published(source, before, database, records, tmp_path, output)


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_identical_store_swapped_for_a_junction_during_export_refuses(
        dnd5e_store, tmp_path, monkeypatch):
    # Same bytes and the same listing; the canonical rule in the fingerprint
    # recheck and the D&D re-listing both refuse it.
    source, url, database = dnd5e_store
    store = _dnd5e_path(source, DND5E_PC).parent
    records = _sql_snapshot(database)
    original = migration.build_plan

    def swap_after_staging(staging):
        copy = source / "campaigns" / OTHER_CAMPAIGN / "dnd5e" / "characters"
        copy.parent.mkdir(parents=True)
        shutil.move(store, copy)
        _junction(store, copy)
        return original(staging)

    monkeypatch.setattr(migration, "build_plan", swap_after_staging)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="changed during export"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []
    assert _sql_snapshot(database) == records


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_pf2e_store_swapped_for_a_junction_during_export_refuses(
        imported_store, tmp_path, monkeypatch):
    # PF2e/Cosmere files have no re-listing; the fingerprint recheck is the only guard.
    source, url, database = imported_store
    store = source / "campaigns" / CAMPAIGN / "party_data"
    records = _sql_snapshot(database)
    original = migration.build_plan

    def swap_after_staging(staging):
        copy = source / "campaigns" / OTHER_CAMPAIGN / "party_data"
        copy.parent.mkdir(parents=True)
        shutil.move(store, copy)
        _junction(store, copy)
        return original(staging)

    monkeypatch.setattr(migration, "build_plan", swap_after_staging)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="changed during export"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []
    assert _sql_snapshot(database) == records


def test_pf2e_payload_is_read_once_so_its_fingerprint_covers_the_exported_bytes(
        imported_store, tmp_path, monkeypatch):
    # A second read by path could see a different file than the fingerprinted one.
    source, url, _database = imported_store
    hero = source / "campaigns" / CAMPAIGN / "party_data" / "hero.json"
    opened, inside_read_bytes = [], []
    real_read_bytes, real_open = Path.read_bytes, Path.open

    def read_bytes(self):
        if self == hero:
            opened.append("read_bytes")
        inside_read_bytes.append(True)
        try:
            return real_read_bytes(self)
        finally:
            inside_read_bytes.pop()

    def path_open(self, *args, **kwargs):
        if self == hero and not inside_read_bytes:  # read_bytes opens through Path.open
            opened.append("open")
        return real_open(self, *args, **kwargs)

    reads_before_recheck = []
    original = migration.build_plan

    def count_at_plan(staging):
        reads_before_recheck.append(list(opened))
        return original(staging)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(migration, "build_plan", count_at_plan)
    report = exporter.export_runtime(source, url, tmp_path / "rollback", quiesced=True)
    assert report["verified"] is True
    assert reads_before_recheck == [["read_bytes"]]


@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_dnd5e_file_change_during_export_refuses(dnd5e_store, tmp_path, monkeypatch, change):
    source, url, database = dnd5e_store
    original = migration.build_plan

    def mutate_after_staging(staging):
        if change == "edit":
            write(_dnd5e_path(source, DND5E_PC), _dnd5e_doc(DND5E_PC, owner=GM))
        elif change == "add":
            write(_dnd5e_path(source, DND5E_NEW), _dnd5e_doc(DND5E_NEW))
        else:
            _dnd5e_path(source, DND5E_PC).unlink()
        return original(staging)

    monkeypatch.setattr(migration, "build_plan", mutate_after_staging)
    output = tmp_path / "rollback"
    with pytest.raises(exporter.RuntimeExportError, match="changed during export"):
        exporter.export_runtime(source, url, output, quiesced=True)
    assert not output.exists()
    assert list(tmp_path.glob(".rollback.staging-*")) == []
