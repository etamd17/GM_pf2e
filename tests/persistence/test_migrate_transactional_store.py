from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select

from core.persistence.database import Database
from core.persistence.models import Base, Invitation, MigrationRun, User
from tools import migrate_transactional_store as migration


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "valid"
GM_ID = "11111111111111111111111111111111"
PLAYER_ID = "22222222222222222222222222222222"
CAMPAIGN_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CHARACTER_ID = "cccccccccccccccccccccccccccccccc"


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "source"
    shutil.copytree(FIXTURE_ROOT, target)
    return target


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _source_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _campaign_path(root: Path) -> Path:
    return root / "campaigns" / CAMPAIGN_ID / "campaign.json"


def _character_path(root: Path) -> Path:
    return root / "campaigns" / CAMPAIGN_ID / "party_data" / "hero.json"


def _cosmere_character_path(root: Path) -> Path:
    return root / "campaigns" / CAMPAIGN_ID / "cosmere_pcs" / "hero.json"


def _conflict_codes(plan: dict) -> set[str]:
    return {item["code"] for item in plan["blocking_conflicts"]}


def _sqlite_database(tmp_path: Path) -> tuple[str, Database]:
    url = f"sqlite+pysqlite:///{(tmp_path / 'transactional.sqlite3').as_posix()}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    return url, database


def test_plan_is_read_only_deterministic_and_complete(tmp_path):
    source = _copy_fixture(tmp_path)
    before = _source_bytes(source)

    first = migration.build_plan(source)
    second = migration.build_plan(source)

    assert first == second
    assert first["import_allowed"] is True
    assert first["blocking_conflicts"] == []
    assert first["warnings"] == []
    assert first["entity_counts"] == {
        "users": 2,
        "campaigns": 1,
        "campaign_memberships": 2,
        "characters": 1,
        "character_assignments": 2,
        "invites": 1,
        "invite_redemptions": 0,
        "character_drafts": 0,
        "character_workflow_receipts": 0,
        "audit_events": 0,
    }
    assert len(first["source_digest"]) == 64
    assert set(first["entity_digests"]) == set(migration.ENTITY_NAMES)
    assert first["snapshot"]["file_count"] == 4
    assert _source_bytes(source) == before


def test_canonical_digest_ignores_json_formatting_and_source_location(tmp_path):
    first_root = _copy_fixture(tmp_path / "one")
    second_root = tmp_path / "two" / "source"
    shutil.copytree(FIXTURE_ROOT, second_root)

    users_path = second_root / "users.json"
    users = _load(users_path)
    users_path.write_text(
        json.dumps(users, separators=(",", ":"), sort_keys=True), encoding="utf-8"
    )

    first = migration.build_plan(first_root)
    second = migration.build_plan(second_root)

    assert first["source_path"] != second["source_path"]
    assert first["snapshot"]["files"] != second["snapshot"]["files"]
    assert first["source_digest"] == second["source_digest"]
    assert first["entity_digests"] == second["entity_digests"]
    assert first["entity_counts"] == second["entity_counts"]


def test_document_envelope_metadata_is_private_digested_and_round_trips(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    baseline = migration.build_plan(source)
    users = _load(source / "users.json")
    invites = _load(source / "invites.json")
    users["deployment"] = {"secret": "do-not-print", "revision": 7}
    invites["format_version"] = 3
    _write(source / "users.json", users)
    _write(source / "invites.json", invites)

    bundle = migration.inspect_source(source)

    assert bundle.report["entity_counts"] == baseline["entity_counts"]
    assert bundle.report["entity_digests"] == baseline["entity_digests"]
    assert bundle.report["source_digest"] != baseline["source_digest"]
    assert bundle.report["envelope_metadata"] == {
        "invites": {
            "keys": ["format_version"],
            "digest": migration._canonical_sha256({"format_version": 3}),
        },
        "users": {
            "keys": ["deployment"],
            "digest": migration._canonical_sha256(
                {"deployment": {"secret": "do-not-print", "revision": 7}}
            ),
        },
    }
    assert "do-not-print" not in json.dumps(bundle.report)

    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(
        source,
        database_url,
        expected_digest=bundle.report["source_digest"],
    )
    assert migration.verify_store(source, database_url)["verified"] is True
    output = tmp_path / "envelope-export"
    migration.export_store(source, database_url, output)

    assert _load(output / "users.json")["deployment"] == users["deployment"]
    assert _load(output / "invites.json")["format_version"] == 3
    assert migration.build_plan(output)["source_digest"] == bundle.report["source_digest"]


def test_empty_entity_maps_preserve_document_envelope_metadata(tmp_path):
    source = tmp_path / "empty-source"
    source.mkdir()
    _write(source / "users.json", {"users": {}, "revision": 4})
    _write(source / "invites.json", {"invites": {}, "policy": {"ttl": None}})

    plan = migration.build_plan(source)
    assert plan["import_allowed"] is True
    assert all(count == 0 for count in plan["entity_counts"].values())
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(source, database_url, expected_digest=plan["source_digest"])
    output = tmp_path / "empty-export"
    migration.export_store(source, database_url, output)

    assert _load(output / "users.json") == {"users": {}, "revision": 4}
    assert _load(output / "invites.json") == {
        "invites": {},
        "policy": {"ttl": None},
    }
    assert migration.build_plan(output)["source_digest"] == plan["source_digest"]


@pytest.mark.parametrize(
    ("expires_at", "expected_relational", "warning"),
    [
        (1_893_456_000, 1_893_456_000.0, None),
        (0, None, "zero_invite_expiration_normalized"),
        (-0.0, None, "zero_invite_expiration_normalized"),
    ],
)
def test_invite_expiration_normalizes_without_losing_raw_json(
    tmp_path, expires_at, expected_relational, warning
):
    source = _copy_fixture(tmp_path / "legacy")
    invites = _load(source / "invites.json")
    invites["invites"]["ABCD-EFGH"]["expires_at"] = expires_at
    _write(source / "invites.json", invites)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True
    assert bundle.records["invites"][0]["expires_at"] == expected_relational
    warning_codes = {item["code"] for item in bundle.report["warnings"]}
    if warning is None:
        assert "zero_invite_expiration_normalized" not in warning_codes
    else:
        assert warning in warning_codes
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(
        source,
        database_url,
        expected_digest=bundle.report["source_digest"],
    )
    output = tmp_path / "expiry-export"
    migration.export_store(source, database_url, output)
    assert (
        _load(output / "invites.json")["invites"]["ABCD-EFGH"]["expires_at"]
        == expires_at
    )
    assert migration.build_plan(output)["source_digest"] == bundle.report["source_digest"]


@pytest.mark.parametrize("expires_at", [10**400, -1])
def test_invalid_invite_expiration_blocks_without_crashing(tmp_path, expires_at):
    source = _copy_fixture(tmp_path)
    invites = _load(source / "invites.json")
    invites["invites"]["ABCD-EFGH"]["expires_at"] = expires_at
    _write(source / "invites.json", invites)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "invalid_invite_expiration" in _conflict_codes(plan)


def test_overflowing_json_float_blocks_without_crashing(tmp_path):
    source = _copy_fixture(tmp_path)
    path = source / "invites.json"
    payload = path.read_text(encoding="utf-8").replace("1893456000.0", "1e400")
    path.write_text(payload, encoding="utf-8")

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "invalid_json" in _conflict_codes(plan)


def test_username_normalization_matches_authoritative_lower_semantics(tmp_path):
    source = _copy_fixture(tmp_path)
    users_path = source / "users.json"
    users = _load(users_path)
    users["users"][GM_ID]["username"] = "Straße"
    users["users"][PLAYER_ID]["username"] = "STRASSE"
    _write(users_path, users)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True
    assert [row["normalized_username"] for row in bundle.records["users"]] == [
        "straße",
        "strasse",
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("long_invite_code", "identifier_too_long"),
        ("int4_overflow", "invalid_invite_uses"),
        ("long_indexed_username", "indexed_username_too_long"),
        ("long_character_id", "identifier_too_long"),
        ("nul_text", "invalid_json"),
        ("lone_surrogate", "invalid_json"),
    ],
)
def test_plan_preflights_postgresql_storage_constraints(
    tmp_path, mutation, expected_code
):
    source = _copy_fixture(tmp_path)
    if mutation == "long_invite_code":
        path = source / "invites.json"
        document = _load(path)
        record = document["invites"].pop("ABCD-EFGH")
        code = "A" * 33
        record["code"] = code
        document["invites"][code] = record
        _write(path, document)
    elif mutation == "int4_overflow":
        path = source / "invites.json"
        document = _load(path)
        document["invites"]["ABCD-EFGH"]["uses_left"] = 2_147_483_648
        _write(path, document)
    elif mutation == "long_indexed_username":
        path = source / "users.json"
        document = _load(path)
        document["users"][GM_ID]["username"] = "x" * 2_001
        _write(path, document)
    elif mutation == "long_character_id":
        path = _character_path(source)
        document = _load(path)
        document["id"] = "c" * 65
        _write(path, document)
    elif mutation == "nul_text":
        path = source / "users.json"
        document = _load(path)
        document["users"][GM_ID]["display_name"] = "bad\x00value"
        _write(path, document)
    elif mutation == "lone_surrogate":
        path = source / "users.json"
        payload = path.read_text(encoding="utf-8").replace(
            '"display_name": "Game Master"',
            '"display_name": "\\ud800"',
        )
        path.write_text(payload, encoding="utf-8")
    else:  # pragma: no cover - parametrization exhausts this branch
        raise AssertionError(mutation)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert expected_code in _conflict_codes(plan)


@pytest.mark.parametrize(
    ("target", "field", "value", "expected_code"),
    [
        ("user", "display_name", False, "invalid_display_name"),
        ("campaign", "name", 0, "invalid_campaign_name"),
        ("campaign", "slug", [], "invalid_campaign_slug"),
        ("campaign", "system", False, "invalid_campaign_system"),
        ("character", "system", {}, "invalid_character_system"),
        (
            "invitation",
            "creates_account",
            0,
            "invalid_invite_creates_account",
        ),
    ],
)
def test_plan_rejects_falsy_values_with_invalid_types(
    tmp_path, target, field, value, expected_code
):
    source = _copy_fixture(tmp_path)
    if target == "user":
        path = source / "users.json"
        document = _load(path)
        document["users"][GM_ID][field] = value
    elif target == "campaign":
        path = _campaign_path(source)
        document = _load(path)
        document[field] = value
    elif target == "character":
        path = _character_path(source)
        document = _load(path)
        document[field] = value
    else:
        path = source / "invites.json"
        document = _load(path)
        document["invites"]["ABCD-EFGH"][field] = value
    _write(path, document)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert expected_code in _conflict_codes(plan)


def test_discovery_matches_runtime_campaign_and_json_suffix_rules(tmp_path):
    source = _copy_fixture(tmp_path)
    ignored_campaign = source / "campaigns" / "not-runtime-visible"
    shutil.copytree(source / "campaigns" / CAMPAIGN_ID, ignored_campaign)
    shutil.copy2(
        _character_path(source),
        _character_path(source).with_name("hidden.JSON"),
    )

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is True
    assert plan["entity_counts"]["campaigns"] == 1
    assert plan["entity_counts"]["characters"] == 1
    assert {item["code"] for item in plan["warnings"]} == {
        "ignored_noncanonical_campaign_directory",
        "ignored_noncanonical_character_file",
    }


def test_symlinked_runtime_character_blocks_migration(tmp_path):
    source = _copy_fixture(tmp_path)
    character = _character_path(source)
    target = character.with_suffix(".source")
    character.rename(target)
    try:
        character.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "unsupported_source_symlink" in _conflict_codes(plan)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("duplicate_username", "duplicate_username"),
        ("missing_membership_user", "missing_membership_user"),
        ("zero_gm", "campaign_has_no_gm"),
        ("dangling_invite", "dangling_invite_character"),
        ("ambiguous_ownership", "ambiguous_character_owner"),
        ("duplicate_character", "duplicate_character_id"),
    ],
)
def test_plan_blocks_unsafe_identity_and_ownership_states(
    tmp_path, mutation, expected_code
):
    source = _copy_fixture(tmp_path)
    users_path = source / "users.json"
    campaign_path = _campaign_path(source)
    character_path = _character_path(source)
    invites_path = source / "invites.json"

    if mutation == "duplicate_username":
        users = _load(users_path)
        users["users"][PLAYER_ID]["username"] = " gamemaster "
        _write(users_path, users)
    elif mutation == "missing_membership_user":
        campaign = _load(campaign_path)
        campaign["members"][1]["user_id"] = "9" * 32
        _write(campaign_path, campaign)
    elif mutation == "zero_gm":
        campaign = _load(campaign_path)
        campaign["members"][0]["role"] = "player"
        _write(campaign_path, campaign)
    elif mutation == "dangling_invite":
        invites = _load(invites_path)
        invites["invites"]["ABCD-EFGH"]["character_id"] = "9" * 32
        _write(invites_path, invites)
    elif mutation == "ambiguous_ownership":
        character = _load(character_path)
        character["owner_user_ids"] = [GM_ID]
        _write(character_path, character)
    elif mutation == "duplicate_character":
        duplicate = character_path.with_name("duplicate.json")
        shutil.copy2(character_path, duplicate)
    else:  # pragma: no cover - parametrization exhausts this branch
        raise AssertionError(mutation)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert expected_code in _conflict_codes(plan)
    assert plan["blocking_conflicts"] == sorted(
        plan["blocking_conflicts"],
        key=lambda item: (
            item["code"],
            item["entity"],
            item["entity_id"],
            item["path"],
            item["message"],
        ),
    )


def test_stale_membership_character_link_warns_but_does_not_guess_ownership(tmp_path):
    source = _copy_fixture(tmp_path)
    character_path = _character_path(source)
    character = _load(character_path)
    character["owner_user_id"] = GM_ID
    character["editor_user_ids"] = []
    _write(character_path, character)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is True
    assert plan["blocking_conflicts"] == []
    assert {item["code"] for item in plan["warnings"]} == {
        "stale_membership_character_link"
    }


@pytest.mark.parametrize("campaign_id_mode", ["missing", "null"])
def test_legacy_cosmere_missing_campaign_id_imports_and_round_trips(
    tmp_path, campaign_id_mode
):
    source = _copy_fixture(tmp_path / "legacy")
    campaign_path = _campaign_path(source)
    campaign = _load(campaign_path)
    campaign["system"] = "cosmere"
    _write(campaign_path, campaign)
    pf2e_path = _character_path(source)
    character = _load(pf2e_path)
    character["system"] = "cosmere"
    if campaign_id_mode == "missing":
        character.pop("campaign_id")
    else:
        character["campaign_id"] = None
    cosmere_path = _cosmere_character_path(source)
    _write(cosmere_path, character)
    pf2e_path.unlink()

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True
    assert {item["code"] for item in bundle.report["warnings"]} == {
        "cosmere_character_campaign_inferred"
    }
    assert bundle.records["characters"][0]["campaign_id"] == CAMPAIGN_ID
    assert bundle.records["characters"][0]["legacy_storage"] == "cosmere_pcs"
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(
        source,
        database_url,
        expected_digest=bundle.report["source_digest"],
    )
    assert migration.verify_store(source, database_url)["verified"] is True
    output = tmp_path / "cosmere-export"
    migration.export_store(source, database_url, output)

    exported_character = _load(_cosmere_character_path(output))
    if campaign_id_mode == "missing":
        assert "campaign_id" not in exported_character
    else:
        assert exported_character["campaign_id"] is None
    assert migration.build_plan(output)["source_digest"] == bundle.report["source_digest"]


@pytest.mark.parametrize("storage", ["party_data", "cosmere_pcs"])
def test_character_campaign_id_remains_strict_when_not_legacy_cosmere_null(
    tmp_path, storage
):
    source = _copy_fixture(tmp_path)
    original = _character_path(source)
    character = _load(original)
    if storage == "party_data":
        character.pop("campaign_id")
        _write(original, character)
    else:
        character["system"] = "cosmere"
        character["campaign_id"] = "b" * 32
        destination = _cosmere_character_path(source)
        _write(destination, character)
        original.unlink()

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "character_campaign_mismatch" in _conflict_codes(plan)


def test_stale_last_campaign_hint_is_preserved_through_round_trip(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    users_path = source / "users.json"
    users = _load(users_path)
    purged_campaign_id = "9" * 32
    users["users"][PLAYER_ID]["last_campaign_id"] = purged_campaign_id
    _write(users_path, users)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True
    assert "stale_last_campaign" in {
        item["code"] for item in bundle.report["warnings"]
    }
    player = next(row for row in bundle.records["users"] if row["id"] == PLAYER_ID)
    assert player["last_campaign_id"] == purged_campaign_id
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(
        source,
        database_url,
        expected_digest=bundle.report["source_digest"],
    )
    output = tmp_path / "stale-campaign-export"
    migration.export_store(source, database_url, output)

    exported_users = _load(output / "users.json")["users"]
    assert exported_users[PLAYER_ID]["last_campaign_id"] == purged_campaign_id
    assert migration.build_plan(output)["source_digest"] == bundle.report["source_digest"]


def test_missing_membership_character_is_cleared_relationally_and_round_trips(
    tmp_path,
):
    source = _copy_fixture(tmp_path / "legacy")
    campaign_path = _campaign_path(source)
    campaign = _load(campaign_path)
    missing_character_id = "9" * 32
    campaign["members"][1]["character_id"] = missing_character_id
    _write(campaign_path, campaign)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True
    warning_codes = {item["code"] for item in bundle.report["warnings"]}
    assert "stale_membership_character_link" in warning_codes
    player_membership = next(
        row
        for row in bundle.records["campaign_memberships"]
        if row["user_id"] == PLAYER_ID
    )
    assert player_membership["character_id"] is None
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(
        source,
        database_url,
        expected_digest=bundle.report["source_digest"],
    )
    assert migration.verify_store(source, database_url)["verified"] is True
    output = tmp_path / "stale-membership-export"
    migration.export_store(source, database_url, output)

    exported_campaign = _load(_campaign_path(output))
    exported_player = next(
        member
        for member in exported_campaign["members"]
        if member["user_id"] == PLAYER_ID
    )
    assert exported_player["character_id"] == missing_character_id
    exported_bundle = migration.inspect_source(output)
    exported_membership = next(
        row
        for row in exported_bundle.records["campaign_memberships"]
        if row["user_id"] == PLAYER_ID
    )
    assert exported_membership["character_id"] is None
    assert exported_bundle.report["source_digest"] == bundle.report["source_digest"]


def test_membership_character_link_to_another_campaign_still_blocks(tmp_path):
    source = _copy_fixture(tmp_path)
    second_campaign_id = "b" * 32
    campaign = _load(_campaign_path(source))
    campaign.update(
        {
            "id": second_campaign_id,
            "slug": "second-table",
            "name": "Second Table",
            "members": [
                {"user_id": GM_ID, "role": "gm"},
                {
                    "user_id": PLAYER_ID,
                    "role": "player",
                    "character_id": CHARACTER_ID,
                },
            ],
        }
    )
    _write(
        source / "campaigns" / second_campaign_id / "campaign.json",
        campaign,
    )

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "membership_character_campaign_mismatch" in _conflict_codes(plan)


def test_plan_rejects_duplicate_json_object_keys(tmp_path):
    source = _copy_fixture(tmp_path)
    users_path = source / "users.json"
    users_path.write_text(
        '{"users":{"same":{"id":"same"},"same":{"id":"same"}}}',
        encoding="utf-8",
    )

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "duplicate_json_key" in _conflict_codes(plan)


def test_plan_blocks_when_source_changes_during_scan(tmp_path, monkeypatch):
    source = _copy_fixture(tmp_path)
    real_snapshot = migration._snapshot_files
    calls = 0

    def changed_on_rescan(root):
        nonlocal calls
        calls += 1
        snapshot = real_snapshot(root)
        if calls == 2:
            snapshot[0] = dict(snapshot[0], sha256="0" * 64)
        return snapshot

    monkeypatch.setattr(migration, "_snapshot_files", changed_on_rescan)

    plan = migration.build_plan(source)

    assert calls == 2
    assert plan["import_allowed"] is False
    assert "source_changed_during_scan" in _conflict_codes(plan)


def test_import_is_atomic_idempotent_verifiable_and_reverse_exportable(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    source_before = _source_bytes(source)
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()

    first = migration.import_store(
        source, database_url, expected_digest=plan["source_digest"]
    )
    second = migration.import_store(
        source, database_url, expected_digest=plan["source_digest"]
    )
    verification = migration.verify_store(source, database_url)

    assert first["status"] == "imported"
    assert second["status"] == "already_imported"
    assert second["migration_run_id"] == first["migration_run_id"]
    assert verification["verified"] is True
    assert verification["mismatches"] == []
    database = Database(database_url)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(MigrationRun)) == 1
        assert session.scalar(select(func.count()).select_from(User)) == 2
        assert session.scalar(select(func.count()).select_from(Invitation)) == 1
    database.dispose()

    export_dir = tmp_path / "reverse-export"
    result = migration.export_store(source, database_url, export_dir)

    assert result["verified"] is True
    assert migration.build_plan(export_dir)["source_digest"] == plan["source_digest"]
    assert _load(export_dir / "users.json")["users"][GM_ID]["theme"] == "night"
    assert _load(export_dir / "invites.json")["invites"]["ABCD-EFGH"]["note"] == "table invite"
    assert _load(_campaign_path(export_dir))["tagline"] == "The lighthouse calls."
    assert (export_dir / migration.EXPORT_MARKER).is_file()
    assert _source_bytes(source) == source_before


def test_verify_rejects_tampered_migration_evidence(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(source, database_url, expected_digest=plan["source_digest"])

    database = Database(database_url)
    with database.transaction() as session:
        run = session.scalar(select(MigrationRun))
        details = dict(run.details)
        details["entity_digests"] = dict(details["entity_digests"])
        details["entity_digests"]["users"] = "0" * 64
        run.details = details
    database.dispose()

    verification = migration.verify_store(source, database_url)
    assert verification["verified"] is False
    assert any(
        mismatch["kind"] == "evidence_entity_digests"
        for mismatch in verification["mismatches"]
    )
    with pytest.raises(migration.VerificationError):
        migration.import_store(
            source,
            database_url,
            expected_digest=plan["source_digest"],
        )


def test_import_refuses_digest_change_before_touching_database(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    plan = migration.build_plan(source)
    users = _load(source / "users.json")
    users["users"][PLAYER_ID]["display_name"] = "Changed after review"
    _write(source / "users.json", users)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()

    with pytest.raises(migration.SourceDigestMismatch):
        migration.import_store(
            source, database_url, expected_digest=plan["source_digest"]
        )

    database = Database(database_url)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(MigrationRun)) == 0
    database.dispose()


def test_import_rolls_back_every_entity_and_migration_run_on_failure(
    tmp_path, monkeypatch
):
    source = _copy_fixture(tmp_path / "legacy")
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()

    from core.persistence.repositories import InvitationRepository

    def fail_invitation(*_args, **_kwargs):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(InvitationRepository, "upsert", fail_invitation)

    with pytest.raises(RuntimeError, match="injected failure"):
        migration.import_store(
            source, database_url, expected_digest=plan["source_digest"]
        )

    database = Database(database_url)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0
        assert session.scalar(select(func.count()).select_from(MigrationRun)) == 0
    database.dispose()


def test_export_refuses_nonempty_or_nested_target(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()
    migration.import_store(source, database_url, expected_digest=plan["source_digest"])

    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(migration.ExportTargetError):
        migration.export_store(source, database_url, nonempty)
    assert (nonempty / "keep.txt").read_text(encoding="utf-8") == "keep"

    with pytest.raises(migration.ExportTargetError):
        migration.export_store(source, database_url, source / "nested-export")


def test_trashed_campaigns_round_trip_to_their_original_root(tmp_path):
    source = _copy_fixture(tmp_path / "legacy")
    active = source / "campaigns" / CAMPAIGN_ID
    trashed = source / "campaigns_trash" / CAMPAIGN_ID
    trashed.parent.mkdir()
    shutil.move(active, trashed)
    campaign = _load(trashed / "campaign.json")
    campaign["_trashed_at"] = "2026-02-01T12:00:00"
    _write(trashed / "campaign.json", campaign)
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()

    assert plan["import_allowed"] is True
    migration.import_store(source, database_url, expected_digest=plan["source_digest"])
    output = tmp_path / "trashed-export"
    migration.export_store(source, database_url, output)

    assert (output / "campaigns_trash" / CAMPAIGN_ID / "campaign.json").is_file()
    assert not (output / "campaigns" / CAMPAIGN_ID).exists()
    assert migration.build_plan(output)["source_digest"] == plan["source_digest"]


def test_duplicate_campaign_id_across_live_and_trash_blocks_import(tmp_path):
    source = _copy_fixture(tmp_path)
    active = source / "campaigns" / CAMPAIGN_ID
    trashed = source / "campaigns_trash" / CAMPAIGN_ID
    shutil.copytree(active, trashed)
    campaign = _load(trashed / "campaign.json")
    campaign["_trashed_at"] = "2026-02-01T12:00:00"
    _write(trashed / "campaign.json", campaign)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "duplicate_campaign_id" in _conflict_codes(plan)


def test_legacy_import_ignores_per_campaign_runs(tmp_path):
    """PE-12: a campaign run sharing the snapshot checksum is a different stream."""
    from core.persistence import MigrationRunRepository

    source = _copy_fixture(tmp_path / "legacy")
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    with database.transaction() as session:
        campaign_run = MigrationRunRepository(session).start_campaign_run(
            campaign_id=CAMPAIGN_ID, kind="import", source_checksum=plan["source_digest"],
            stream_epoch=1, tool_version="test")
        MigrationRunRepository(session).finish(campaign_run, status="succeeded")
        campaign_run_id = campaign_run.id
    database.dispose()

    first = migration.import_store(source, database_url, expected_digest=plan["source_digest"])
    second = migration.import_store(source, database_url, expected_digest=plan["source_digest"])

    assert first["status"] == "imported"
    assert first["migration_run_id"] != campaign_run_id
    assert second["status"] == "already_imported"
    assert second["migration_run_id"] == first["migration_run_id"]
    assert migration.verify_store(source, database_url)["verified"] is True


# --- PE-11a: D&D character files -------------------------------------------------

DND5E_OWNED = "d" * 32
DND5E_UNOWNED = "e" * 32
DND5E_STALE = "f" * 32


def _dnd5e_dir(root: Path) -> Path:
    return root / "campaigns" / CAMPAIGN_ID / "dnd5e" / "characters"


def _dnd5e_document(character_id, owner=PLAYER_ID, **updates):
    return {
        "id": character_id,
        "campaign_id": CAMPAIGN_ID,
        "system": "dnd5e",
        "name": f"PC {str(character_id)[:4]}",
        "owner_user_id": owner,
        **updates,
    }


def _dnd5e_fixture(tmp_path: Path, *, stale_owner: bool = False) -> Path:
    """The valid fixture turned into a dnd5e campaign with D&D characters."""
    source = _copy_fixture(tmp_path / "legacy")
    campaign = _load(_campaign_path(source))
    campaign["system"] = "dnd5e"
    for member in campaign["members"]:
        member.pop("character_id", None)
    _write(_campaign_path(source), campaign)
    shutil.rmtree(source / "campaigns" / CAMPAIGN_ID / "party_data")
    _write(_dnd5e_dir(source) / f"{DND5E_OWNED}.json", _dnd5e_document(DND5E_OWNED))
    _write(_dnd5e_dir(source) / f"{DND5E_UNOWNED}.json", _dnd5e_document(DND5E_UNOWNED, None))
    if stale_owner:
        _write(
            _dnd5e_dir(source) / f"{DND5E_STALE}.json",
            _dnd5e_document(DND5E_STALE, "9" * 32),
        )
    # Non-character D&D files sit next to the store and must stay out of it.
    _write(source / "campaigns" / CAMPAIGN_ID / "dnd5e" / "encounter.json", {"combatants": []})
    _write(source / "campaigns" / CAMPAIGN_ID / "dnd5e" / "plans" / f"{DND5E_OWNED}.json", {})
    _write(_dnd5e_dir(source) / "nested" / f"{DND5E_UNOWNED}.json", _dnd5e_document(DND5E_UNOWNED))
    (_dnd5e_dir(source) / f"{DND5E_OWNED}.json.tmp").write_text("{", encoding="utf-8")
    return source


def _warning_codes(plan: dict) -> list:
    return sorted(item["code"] for item in plan["warnings"])


def _assignments(records: dict) -> list:
    return sorted(
        (row["character_id"], row["user_id"], row["role"])
        for row in records["character_assignments"]
    )


def test_dnd5e_campaign_plans_owned_and_unowned_characters(tmp_path):
    source = _dnd5e_fixture(tmp_path)
    bundle = migration.inspect_source(source)
    plan = bundle.report

    assert plan["import_allowed"] is True, plan["blocking_conflicts"]
    assert plan["warnings"] == []
    characters = sorted(
        (row["id"], row["system"], row["legacy_storage"], row["legacy_file"])
        for row in bundle.records["characters"]
    )
    assert characters == [
        (DND5E_OWNED, "dnd5e", "dnd5e_characters", f"{DND5E_OWNED}.json"),
        (DND5E_UNOWNED, "dnd5e", "dnd5e_characters", f"{DND5E_UNOWNED}.json"),
    ]
    assert _assignments(bundle.records) == [(DND5E_OWNED, PLAYER_ID, "owner")]


def test_dnd5e_stale_owner_blocks_import(tmp_path):
    plan = migration.build_plan(_dnd5e_fixture(tmp_path, stale_owner=True))

    assert plan["import_allowed"] is False
    stale = [
        item for item in plan["blocking_conflicts"]
        if item["code"] == "assignment_user_not_member"
    ]
    assert [item["entity_id"] for item in stale] == [f"{DND5E_STALE}:{'9' * 32}"]


def test_dnd5e_nonconforming_stems_warn_and_are_excluded(tmp_path):
    source = _dnd5e_fixture(tmp_path)
    # "F" * 32 has no lowercase twin here, so it is safe on case-insensitive disks.
    stems = ["hero", "F" * 32, "d" * 31]
    for stem in stems:
        _write(_dnd5e_dir(source) / f"{stem}.json", _dnd5e_document(stem))
    newline_stem = "a" * 32 + "\n"
    try:
        _write(_dnd5e_dir(source) / f"{newline_stem}.json", _dnd5e_document(newline_stem))
    except OSError:
        pass  # Windows cannot create a newline in a file name.
    else:
        stems.append(newline_stem)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True, bundle.report["blocking_conflicts"]
    assert _warning_codes(bundle.report) == ["dnd5e_nonconforming_character_file"] * len(stems)
    assert {row["id"] for row in bundle.records["characters"]} == {DND5E_OWNED, DND5E_UNOWNED}


@pytest.mark.parametrize("document_id", [DND5E_UNOWNED, "Bram", None, ""])
def test_dnd5e_id_stem_mismatch_blocks(tmp_path, document_id):
    source = _dnd5e_fixture(tmp_path)
    path = _dnd5e_dir(source) / f"{DND5E_STALE}.json"
    _write(path, _dnd5e_document(DND5E_STALE, id=document_id))

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "dnd5e_locator_mismatch" in _conflict_codes(plan)


@pytest.mark.parametrize("campaign_id", ["b" * 32, None, "missing"])
def test_dnd5e_stale_campaign_id_warns_and_uses_the_containing_campaign(
    tmp_path, campaign_id
):
    source = _dnd5e_fixture(tmp_path)
    document = _dnd5e_document(DND5E_OWNED, campaign_id=campaign_id)
    if campaign_id == "missing":
        document.pop("campaign_id")
    _write(_dnd5e_dir(source) / f"{DND5E_OWNED}.json", document)

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True, bundle.report["blocking_conflicts"]
    assert _warning_codes(bundle.report) == ["dnd5e_character_campaign_inferred"]
    row = next(r for r in bundle.records["characters"] if r["id"] == DND5E_OWNED)
    assert row["campaign_id"] == CAMPAIGN_ID


def test_dnd5e_grant_lists_warn_and_only_the_scalar_owner_is_assigned(tmp_path):
    source = _dnd5e_fixture(tmp_path)
    _write(
        _dnd5e_dir(source) / f"{DND5E_OWNED}.json",
        _dnd5e_document(
            DND5E_OWNED,
            owner_user_ids=[GM_ID],
            editor_user_ids=[GM_ID],
            viewer_user_ids=[],
        ),
    )

    bundle = migration.inspect_source(source)

    assert bundle.report["import_allowed"] is True, bundle.report["blocking_conflicts"]
    assert _warning_codes(bundle.report) == ["dnd5e_ignored_grant_field"]
    assert "owner_user_ids, editor_user_ids" in bundle.report["warnings"][0]["message"]
    assert _assignments(bundle.records) == [(DND5E_OWNED, PLAYER_ID, "owner")]


def test_dnd5e_file_in_a_pf2e_campaign_blocks(tmp_path):
    source = _dnd5e_fixture(tmp_path)
    campaign = _load(_campaign_path(source))
    campaign["system"] = "pf2e"
    _write(_campaign_path(source), campaign)

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "dnd5e_character_campaign_system" in _conflict_codes(plan)


@pytest.mark.parametrize("system", ["pf2e", "cosmere", None, ""])
def test_non_dnd5e_system_in_the_dnd5e_store_blocks(tmp_path, system):
    source = _dnd5e_fixture(tmp_path)
    _write(
        _dnd5e_dir(source) / f"{DND5E_OWNED}.json",
        _dnd5e_document(DND5E_OWNED, system=system),
    )

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "invalid_character_system" in _conflict_codes(plan)


@pytest.mark.parametrize("storage", ["party_data", "cosmere_pcs"])
def test_dnd5e_system_in_an_old_store_blocks(tmp_path, storage):
    source = _dnd5e_fixture(tmp_path)
    _write(
        source / "campaigns" / CAMPAIGN_ID / storage / "hero.json",
        _dnd5e_document("hero"),
    )

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    assert "invalid_character_system" in _conflict_codes(plan)


def test_unknown_campaign_system_still_blocks(tmp_path):
    source = _copy_fixture(tmp_path)
    campaign = _load(_campaign_path(source))
    campaign["system"] = "foo"
    _write(_campaign_path(source), campaign)

    plan = migration.build_plan(source)

    assert "invalid_campaign_system" in _conflict_codes(plan)


@pytest.mark.parametrize("parts", [("dnd5e",), ("dnd5e", "characters")])
def test_symlinked_dnd5e_directory_blocks(tmp_path, parts):
    source = _dnd5e_fixture(tmp_path)
    store = source.joinpath("campaigns", CAMPAIGN_ID, *parts)
    elsewhere = tmp_path / "elsewhere"
    shutil.move(store, elsewhere)
    try:
        store.symlink_to(elsewhere, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable here")

    plan = migration.build_plan(source)

    assert plan["import_allowed"] is False
    conflicts = [
        item for item in plan["blocking_conflicts"]
        if item["code"] == "unsupported_source_symlink"
    ]
    assert [item["path"] for item in conflicts] == ["/".join(("campaigns", CAMPAIGN_ID, *parts))]
    assert not [
        entry for entry in migration.build_plan(source)["snapshot"]["files"]
        if "/dnd5e/characters/" in entry["path"]
    ]


def test_dnd5e_import_verify_and_export_round_trip(tmp_path, monkeypatch):
    import importlib

    source = _dnd5e_fixture(tmp_path)
    source_before = _source_bytes(source)
    plan = migration.build_plan(source)
    database_url, database = _sqlite_database(tmp_path)
    database.dispose()

    assert plan["import_allowed"] is True, plan["blocking_conflicts"]
    migration.import_store(source, database_url, expected_digest=plan["source_digest"])
    verification = migration.verify_store(source, database_url)
    assert verification["verified"] is True, verification["mismatches"]

    output = tmp_path / "dnd5e-export"
    result = migration.export_store(source, database_url, output)

    assert result["verified"] is True
    for character_id in (DND5E_OWNED, DND5E_UNOWNED):
        relative = f"campaigns/{CAMPAIGN_ID}/dnd5e/characters/{character_id}.json"
        assert (output / relative).read_bytes() == source_before[relative]
    assert not (output / "campaigns" / CAMPAIGN_ID / "dnd5e_characters").exists()
    assert migration.build_plan(output)["source_digest"] == plan["source_digest"]
    assert _source_bytes(source) == source_before

    # SQL-mode /me leaves the imported D&D character out until PE-15.
    runtime = importlib.import_module("core.persistence.runtime")
    ownership = importlib.import_module("core.persistence.ownership")
    database = Database(database_url)
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: database)
    try:
        assert ownership.characters_for_user(PLAYER_ID) == []
    finally:
        database.dispose()


# --- Offline planning needs no SQL packages (PE-11a implementation R1-1) -----------

_WITHOUT_SQL = r'''
import json, runpy, sys

class _NoSql:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in {"sqlalchemy", "psycopg", "psycopg2", "alembic"}:
            raise ModuleNotFoundError(f"No module named {name!r} (blocked for test)")
        return None

sys.meta_path.insert(0, _NoSql())
tool = runpy.run_path(sys.argv[1], run_name="offline_probe")
code = tool["main"](sys.argv[2:])
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in {"sqlalchemy", "psycopg", "psycopg2", "alembic"}
    or name.startswith("core.persistence")
)
print("\n" + json.dumps({"exit": code, "loaded": loaded}))
'''


def _run_without_sql(*argv: str):
    import subprocess
    import sys

    tool = Path(migration.__file__).resolve()
    completed = subprocess.run(
        [sys.executable, "-c", _WITHOUT_SQL, str(tool), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=tool.parents[1],
    )
    assert completed.returncode == 0, completed.stderr
    *output, status = completed.stdout.rstrip("\n").split("\n")
    return json.loads(status), "\n".join(output), completed.stderr


@pytest.mark.parametrize("fixture", ["existing_stores", "dnd5e"])
def test_plan_runs_without_sql_packages(tmp_path, fixture):
    if fixture == "dnd5e":
        source = _dnd5e_fixture(tmp_path)
    else:
        source = _copy_fixture(tmp_path)
        _write(
            _cosmere_character_path(source),
            {**_load(_character_path(source)), "id": "hero2", "system": "cosmere"},
        )
    expected = migration.build_plan(source)

    status, stdout, stderr = _run_without_sql("plan", "--source", str(source))

    assert status == {"exit": 0, "loaded": []}, stderr
    report = json.loads(stdout)
    assert report["import_allowed"] is True
    assert report["source_digest"] == expected["source_digest"]
    assert report["entity_counts"] == expected["entity_counts"]


def test_database_subcommands_keep_their_missing_dependency_error(tmp_path):
    source = _copy_fixture(tmp_path)
    digest = migration.build_plan(source)["source_digest"]
    url = f"sqlite+pysqlite:///{(tmp_path / 'never.sqlite3').as_posix()}"

    status, _, stderr = _run_without_sql(
        "import", "--source", str(source), "--expect-digest", digest, "--database-url", url
    )

    assert status["exit"] == 2
    assert "SQLAlchemy persistence dependencies are not installed" in stderr
    assert not (tmp_path / "never.sqlite3").exists()
