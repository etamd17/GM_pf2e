"""PE-11a: the exact D&D character locator and the read-side identity adapter.

D&D character files stay authoritative until PE-15, so the shared adapter only
recognizes and resolves them here. Every write entry point refuses them.
"""

from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from core import storage
from core.persistence import character_files
from core.persistence.models import (
    Campaign,
    CampaignMembership,
    Character,
    CharacterAssignment,
    User,
)

CID = "a" * 32
PID = "b" * 32
OTHER_PID = "c" * 32
NEWLINE_ID = "a" * 32 + "\n"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


# --- the id predicate -------------------------------------------------------------

@pytest.mark.parametrize("value", [PID, "0123456789abcdef" * 2])
def test_exact_dnd5e_character_ids_are_accepted(value):
    assert character_files.is_dnd5e_character_id(value)


@pytest.mark.parametrize("value", [
    NEWLINE_ID, "A" * 32, "a" * 31, "a" * 33, "g" * 32, "hero", "", None, 7,
    "a" * 32 + " ", " " + "a" * 31, "a" * 16 + "-" + "a" * 15,
])
def test_nonconforming_dnd5e_character_ids_are_refused(value):
    assert not character_files.is_dnd5e_character_id(value)


def test_the_runtime_store_helper_is_deliberately_not_used():
    # store.valid_id uses re.match with "$", which also accepts a trailing
    # newline. The shared rule must not inherit that (Codex R1-2).
    from systems.dnd5e import store

    assert store.valid_id(NEWLINE_ID) is True
    assert character_files.is_dnd5e_character_id(NEWLINE_ID) is False


# --- locator ----------------------------------------------------------------------

@pytest.fixture
def campaigns_dir(tmp_path, monkeypatch):
    root = tmp_path / "campaigns"
    root.mkdir()
    monkeypatch.setattr(storage, "CAMPAIGNS_DIR", str(root))
    return root


def test_exact_dnd5e_path_resolves_to_its_logical_storage(campaigns_dir):
    path = campaigns_dir / CID / "dnd5e" / "characters" / f"{PID}.json"
    assert character_files.locator(path) == (CID, "dnd5e_characters", f"{PID}.json")


@pytest.mark.parametrize("storage_name", ["party_data", "cosmere_pcs"])
def test_existing_stores_keep_their_locator(campaigns_dir, storage_name):
    path = campaigns_dir / CID / storage_name / "hero.json"
    assert character_files.locator(path) == (CID, storage_name, "hero.json")


@pytest.mark.parametrize("relative", [
    f"dnd5e/plans/{PID}.json",
    "dnd5e/encounter.json",
    f"dnd5e/images/{PID}.png",
    f"dnd5e/characters/sub/{PID}.json",
    f"dnd5e/characters/{PID}.json.tmp",
    f"dnd5e/characters/{PID}.tmp",
    f"dnd5e/characters/{PID}",
    f"dnd5e/characters/{'B' * 32}.json",
    f"dnd5e/characters/{'b' * 31}.json",
    f"dnd5e/characters/{'b' * 33}.json",
    "dnd5e/characters/hero.json",
    f"dnd5e/characters/{NEWLINE_ID}.json",
    f"dnd5e_characters/{PID}.json",
    f"Dnd5e/characters/{PID}.json",
    f"dnd5e/Characters/{PID}.json",
    f"dnd5e/{PID}.json",
    f"characters/{PID}.json",
])
def test_dnd5e_lookalikes_have_no_locator(campaigns_dir, relative):
    assert character_files.locator(campaigns_dir / CID / relative) is None


def test_dnd5e_path_outside_a_campaign_has_no_locator(campaigns_dir, tmp_path):
    assert character_files.locator(
        campaigns_dir / "not-a-campaign" / "dnd5e" / "characters" / f"{PID}.json") is None
    assert character_files.locator(
        tmp_path / CID / "dnd5e" / "characters" / f"{PID}.json") is None
    traversal = campaigns_dir / CID / "dnd5e" / ".." / "dnd5e" / "characters" / f"{PID}.json"
    # abspath normalizes ".." before matching, so this is the exact path again.
    assert character_files.locator(traversal) == (CID, "dnd5e_characters", f"{PID}.json")
    escape = campaigns_dir / CID / "dnd5e" / "characters" / ".." / ".." / ".." / f"{PID}.json"
    assert character_files.locator(escape) is None


def test_storage_name_used_as_a_directory_fails_closed_in_app_paths(campaigns_dir):
    # app.py's _WorkflowCharacterFiles._path joins record.storage onto the
    # campaign directory. For D&D that is not a character path, so the
    # locator comparison there fails closed until PE-15 maps it.
    joined = campaigns_dir / CID / "dnd5e_characters" / f"{PID}.json"
    assert character_files.locator(joined) is None


@pytest.mark.parametrize("link", ["dnd5e", "dnd5e/characters"])
def test_symlinked_dnd5e_directories_are_unsafe(campaigns_dir, tmp_path, link):
    real = tmp_path / "elsewhere"
    (real / "characters").mkdir(parents=True)
    campaign = campaigns_dir / CID
    (campaign / "dnd5e").mkdir(parents=True, exist_ok=link != "dnd5e")
    if link == "dnd5e":
        (campaign / "dnd5e").rmdir()
        target = real
    else:
        target = real / "characters"
    try:
        os.symlink(target, campaign / link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable here")
    with pytest.raises(Exception) as caught:
        character_files.locator(campaign / "dnd5e" / "characters" / f"{PID}.json")
    assert type(caught.value).__name__ == "StoreUnavailable"


def test_storage_layout_maps_are_complete():
    assert set(character_files.LEGACY_STORAGE_DIRS) == {
        "party_data", "cosmere_pcs", "dnd5e_characters"}
    assert character_files.LEGACY_STORAGE_DIRS["dnd5e_characters"] == ("dnd5e", "characters")
    assert character_files.storage_system("dnd5e_characters") == "dnd5e"
    for bad in ("dnd5e", "characters", None, ["party_data"]):
        with pytest.raises(ValueError):
            character_files.storage_relative_parts(bad)
        with pytest.raises(ValueError):
            character_files.storage_system(bad)


@pytest.mark.parametrize("backend", ["json", "sql"])
def test_write_document_refuses_dnd5e_files_without_writing(
        campaigns_dir, monkeypatch, sqlite_database, backend):
    runtime = importlib.import_module("core.persistence.runtime")
    monkeypatch.setenv("OWNERSHIP_BACKEND", backend)
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    path = campaigns_dir / CID / "dnd5e" / "characters" / f"{PID}.json"
    written = []
    with pytest.raises(ValueError, match="not written through the shared adapter"):
        character_files.write_document(path, {"id": PID}, written.append)
    assert written == []
    assert not path.exists()


# --- SQL identity -----------------------------------------------------------------

def _dnd5e_document(**updates):
    return {"id": PID, "campaign_id": CID, "system": "dnd5e", "name": "Bram",
            "owner_user_id": "outsider", **updates}


@pytest.fixture
def dnd5e_store(sqlite_database, monkeypatch):
    runtime = importlib.import_module("core.persistence.runtime")
    ownership = importlib.import_module("core.persistence.ownership")
    monkeypatch.setenv("OWNERSHIP_BACKEND", "sql")
    monkeypatch.setattr(runtime, "database", lambda: sqlite_database)
    with sqlite_database.transaction() as session:
        session.add_all([
            User(id=uid, username=uid, normalized_username=uid,
                 display_name=uid, password_hash="hash")
            for uid in ("gm", "owner", "outsider")
        ])
        session.flush()
        session.add_all([
            Campaign(id=CID, slug="d", name="D&D", system="dnd5e", created_by_user_id="gm"),
            Campaign(id="f" * 32, slug="p", name="PF2e", system="pf2e", created_by_user_id="gm"),
        ])
        session.flush()
        session.add_all([
            CampaignMembership(campaign_id=cid, user_id=uid, role=role)
            for cid in (CID, "f" * 32)
            for uid, role in (("gm", "gm"), ("owner", "player"))
        ])
        session.flush()
        session.add_all([
            Character(id=PID, campaign_id=CID, system="dnd5e", display_name="Bram",
                      legacy_storage="dnd5e_characters", legacy_file=f"{PID}.json",
                      content_checksum="a" * 64),
            Character(id="hero", campaign_id="f" * 32, system="pf2e", display_name="Hero",
                      legacy_storage="party_data", legacy_file="hero.json",
                      content_checksum="a" * 64),
        ])
        session.flush()
        session.add_all([
            CharacterAssignment(campaign_id=CID, character_id=PID, user_id="owner", role="owner"),
            CharacterAssignment(campaign_id="f" * 32, character_id="hero",
                                user_id="owner", role="owner"),
        ])
    return ownership, sqlite_database


def test_dnd5e_identity_resolves_through_its_exact_locator(dnd5e_store):
    ownership, _ = dnd5e_store
    result = ownership.authoritative_document(
        CID, _dnd5e_document(), legacy_storage="dnd5e_characters", filename=f"{PID}.json",
        require_identity=True)
    assert result["owner_user_id"] == "owner"
    assert result["id"] == PID and result["system"] == "dnd5e"


@pytest.mark.parametrize("document,filename,match", [
    (_dnd5e_document(id=None), f"{PID}.json", "must equal its file name"),
    (_dnd5e_document(id=""), f"{PID}.json", "must equal its file name"),
    (_dnd5e_document(id=OTHER_PID), f"{PID}.json", "must equal its file name"),
    (_dnd5e_document(system="pf2e"), f"{PID}.json", "system does not match"),
    (_dnd5e_document(system=None), f"{PID}.json", "system does not match"),
    (_dnd5e_document(campaign_id="f" * 32), f"{PID}.json", "scoped campaign"),
    (_dnd5e_document(id=NEWLINE_ID), f"{NEWLINE_ID}.json", "invalid D&D character file name"),
    (_dnd5e_document(id="Bram"), "Bram.json", "invalid D&D character file name"),
])
def test_mismatched_dnd5e_identity_fails_closed(dnd5e_store, document, filename, match):
    ownership, _ = dnd5e_store
    with pytest.raises(ValueError, match=match):
        ownership.authoritative_document(
            CID, document, legacy_storage="dnd5e_characters", filename=filename)


def test_unknown_dnd5e_identity_never_uses_file_supplied_ownership(dnd5e_store):
    ownership, _ = dnd5e_store
    document = _dnd5e_document(id=OTHER_PID, owner_user_id="owner")
    result = ownership.authoritative_document(
        CID, document, legacy_storage="dnd5e_characters", filename=f"{OTHER_PID}.json")
    assert result["owner_user_id"] is None
    assert result["editor_user_ids"] == [] and result["viewer_user_ids"] == []
    with pytest.raises(ValueError, match="does not exist"):
        ownership.authoritative_document(
            CID, document, legacy_storage="dnd5e_characters",
            filename=f"{OTHER_PID}.json", require_identity=True)


def test_unknown_storage_still_fails_closed(dnd5e_store):
    ownership, _ = dnd5e_store
    with pytest.raises(ValueError, match="unknown character storage"):
        ownership.authoritative_document(
            CID, _dnd5e_document(), legacy_storage="dnd5e", filename=f"{PID}.json")


def _sql_state(database):
    with database.session() as session:
        return (
            [(row.id, row.content_checksum, row.display_name)
             for row in session.scalars(select(Character).order_by(Character.id))],
            [(row.character_id, row.user_id, row.role)
             for row in session.scalars(select(CharacterAssignment).order_by(
                 CharacterAssignment.character_id, CharacterAssignment.user_id))],
        )


@pytest.mark.parametrize("pid", [PID, OTHER_PID])
@pytest.mark.parametrize("entry_point", [
    "register_character", "register_character_in_session", "write_character_batch"])
def test_every_adapter_write_refuses_dnd5e(dnd5e_store, entry_point, pid):
    ownership, database = dnd5e_store
    before = _sql_state(database)
    written = []
    document = _dnd5e_document(id=pid, name="Renamed")
    with pytest.raises(ValueError, match="not written through the shared adapter"):
        if entry_point == "register_character":
            ownership.register_character(
                CID, "dnd5e_characters", f"{pid}.json", document,
                owner_user_id="owner", write_document=written.append)
        elif entry_point == "register_character_in_session":
            with database.transaction() as session:
                ownership.register_character_in_session(
                    session, CID, "dnd5e_characters", f"{pid}.json", document)
        else:
            ownership.write_character_batch(
                [(CID, "dnd5e_characters", f"{pid}.json", document)],
                write_documents=written.extend)
    assert written == []
    assert _sql_state(database) == before


def test_dnd5e_write_refusal_holds_in_json_mode(dnd5e_store, monkeypatch):
    ownership, _ = dnd5e_store
    monkeypatch.setenv("OWNERSHIP_BACKEND", "json")
    written = []
    with pytest.raises(ValueError, match="not written through the shared adapter"):
        ownership.register_character(
            CID, "dnd5e_characters", f"{PID}.json", _dnd5e_document(),
            write_document=written.append)
    with pytest.raises(ValueError, match="not written through the shared adapter"):
        ownership.write_character_batch(
            [(CID, "dnd5e_characters", f"{PID}.json", _dnd5e_document())],
            write_documents=written.extend)
    assert written == []


def test_owned_dnd5e_rows_stay_off_the_sql_character_list(dnd5e_store):
    ownership, _ = dnd5e_store
    cards = ownership.characters_for_user("owner")
    assert [(card["campaign_id"], card["id"], card["system"]) for card in cards] == [
        ("f" * 32, "hero", "pf2e")]


def test_pending_publication_never_matches_dnd5e_files(dnd5e_store, campaigns_dir, monkeypatch):
    from types import SimpleNamespace

    from core.character_workflows import recovery

    receipt = SimpleNamespace(
        campaign_id=CID, target_id="hero",
        metadata={"storage": "party_data", "filename": f"{PID}.json", "system": "pf2e"})
    monkeypatch.setattr(recovery, "pending_receipts", lambda cid: (receipt,) if cid == CID else ())
    party_path = campaigns_dir / CID / "party_data" / f"{PID}.json"
    dnd5e_path = campaigns_dir / CID / "dnd5e" / "characters" / f"{PID}.json"

    # Positive control: the same file name in an old store is held.
    assert character_files.pending_publication(party_path) is receipt
    # The D&D store has its own logical name, so the old store's receipt never matches.
    assert character_files.pending_publication(dnd5e_path) is None
    character_files.assert_available(dnd5e_path)


def test_no_workflow_receipt_can_target_a_dnd5e_character():
    # pending_for has no D&D special case; the guarantee is that no D&D receipt
    # can be turned into a publishable record.
    from types import SimpleNamespace

    from core.character_workflows import recovery

    receipt = SimpleNamespace(
        campaign_id=CID, target_id=PID,
        metadata={"storage": "dnd5e_characters", "filename": f"{PID}.json", "system": "dnd5e"})
    with pytest.raises(recovery.WorkflowError) as caught:
        recovery.record_from_receipt(receipt)
    assert caught.value.code == "publication_repair_required"


# --- no D&D route adopts the adapter yet ------------------------------------------

def test_dnd5e_modules_do_not_import_the_shared_character_adapter():
    forbidden = {"core.persistence.character_files", "core.persistence.ownership"}
    offenders = []
    for path in sorted((REPOSITORY_ROOT / "systems" / "dnd5e").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            if any(name in forbidden for name in names):
                offenders.append(path.relative_to(REPOSITORY_ROOT).as_posix())
    assert offenders == []
