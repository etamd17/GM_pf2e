"""Failure-injection tests for whole-party character persistence.

The batch helper is the durability boundary used by multi-character rests and
daily preparations.  A failure must leave every file and every published actor
at the pre-request version; success broadcasts are forbidden until the whole
batch has committed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from werkzeug.exceptions import HTTPException

import app


_FIXTURE = Path(__file__).parent / "fixtures" / "kyle_l10.json"


@pytest.fixture
def two_character_party(tmp_path, monkeypatch):
    first_doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    second_doc = copy.deepcopy(first_doc)
    second_doc.get("build", second_doc)["name"] = "Second Hero"

    first_path = tmp_path / "Kyle.json"
    second_path = tmp_path / "Second_Hero.json"
    first_path.write_text(json.dumps(first_doc), encoding="utf-8")
    second_path.write_text(json.dumps(second_doc), encoding="utf-8")

    first = app.Character(first_doc, file_path=str(first_path))
    second = app.Character(second_doc, file_path=str(second_path))
    party = {first.name: first, second.name: second}
    paths = {first.name: str(first_path), second.name: str(second_path)}

    monkeypatch.setattr(app, "PARTY_LIBRARY", party)
    monkeypatch.setattr(app, "_PC_FILE_CACHE", {
        first.name: first_path.name,
        second.name: second_path.name,
    })
    monkeypatch.setattr(app, "_PC_PERSIST_DIRTY", set())
    monkeypatch.setattr(app, "get_pc_file_path", lambda name: paths.get(name))
    monkeypatch.setattr(app, "_is_gm", lambda: True)

    return {
        "actors": party,
        "paths": {first.name: first_path, second.name: second_path},
        "docs": {first.name: first_doc, second.name: second_doc},
        "tmp_path": tmp_path,
    }


def _updates(fixture):
    updates = []
    for index, (name, actor) in enumerate(fixture["actors"].items(), start=1):
        doc = copy.deepcopy(fixture["docs"][name])
        build = doc.get("build", doc)
        build["current_hp"] = max(1, actor.current_hp - (index * 7))
        build["notes"] = f"batch version {index}"
        updates.append((name, doc, str(fixture["paths"][name])))
    return updates


def _snapshot(fixture):
    return {
        "files": {
            name: path.read_bytes()
            for name, path in fixture["paths"].items()
        },
        "actors": dict(fixture["actors"]),
        "cache": dict(app._PC_FILE_CACHE),
    }


def _assert_snapshot_unchanged(fixture, before):
    assert {
        name: path.read_bytes()
        for name, path in fixture["paths"].items()
    } == before["files"]
    for name, actor in before["actors"].items():
        assert app.PARTY_LIBRARY[name] is actor
    assert app._PC_FILE_CACHE == before["cache"]
    assert list(fixture["tmp_path"].glob("*.batch-stage")) == []
    assert list(fixture["tmp_path"].glob("*.batch-backup")) == []


def _fail_second_stage(monkeypatch):
    original_write = app._atomic_write_json
    staged = 0

    def injected_write(path, *args, **kwargs):
        nonlocal staged
        if str(path).endswith(".batch-stage"):
            staged += 1
            if staged == 2:
                raise OSError("injected second-stage failure")
        return original_write(path, *args, **kwargs)

    monkeypatch.setattr(app, "_atomic_write_json", injected_write)


def _fail_second_commit(monkeypatch, fixture):
    second_path = str(fixture["paths"]["Second Hero"].resolve())
    original_replace = app.os.replace
    state = {"injected": False}

    def injected_replace(source, destination):
        if (
            not state["injected"]
            and str(source).endswith(".batch-stage")
            and str(Path(destination).resolve()) == second_path
        ):
            state["injected"] = True
            raise OSError("injected second commit failure")
        return original_replace(source, destination)

    monkeypatch.setattr(app.os, "replace", injected_replace)
    return state


def _crash_journal(fixture, state):
    entries = []
    for index, (name, path) in enumerate(fixture["paths"].items(), start=1):
        doc = copy.deepcopy(fixture["docs"][name])
        build = doc.get("build", doc)
        build["notes"] = f"new crash version {index}"
        build["current_hp"] = max(
            1, fixture["actors"][name].current_hp - (index * 9)
        )
        stage = fixture["tmp_path"] / f"{index}.batch-stage"
        backup = fixture["tmp_path"] / f"{index}.batch-backup"
        app._atomic_write_json(str(stage), doc, indent=4)
        app.os.link(str(path), str(backup))
        entries.append({
            "name": name,
            "path": str(path.resolve()),
            "stage": str(stage.resolve()),
            "backup": str(backup.resolve()),
        })
    journal = fixture["tmp_path"] / ".test.character-batch-journal"
    app._write_character_batch_journal(str(journal), state, entries)
    return entries, journal


def test_batch_staging_failure_leaves_all_files_and_actors_unchanged(
    two_character_party, monkeypatch
):
    before = _snapshot(two_character_party)
    _fail_second_stage(monkeypatch)

    with pytest.raises(HTTPException) as caught:
        app._save_and_reload_character_batch(_updates(two_character_party))

    assert caught.value.code == 503
    assert "No changes were applied" in caught.value.description
    _assert_snapshot_unchanged(two_character_party, before)


def test_batch_commit_failure_rolls_back_prior_file_and_publishes_nothing(
    two_character_party, monkeypatch
):
    before = _snapshot(two_character_party)
    state = _fail_second_commit(monkeypatch, two_character_party)

    with pytest.raises(HTTPException) as caught:
        app._save_and_reload_character_batch(_updates(two_character_party))

    assert state["injected"] is True
    assert caught.value.code == 503
    assert "No changes were applied" in caught.value.description
    _assert_snapshot_unchanged(two_character_party, before)


def test_rollback_failure_preserves_backup_and_reloads_actual_live_files(
    two_character_party, monkeypatch
):
    before = _snapshot(two_character_party)
    names = list(two_character_party["actors"])
    first_name, second_name = names
    first_path = two_character_party["paths"][first_name].resolve()
    second_path = two_character_party["paths"][second_name].resolve()
    broadcasts = []
    state = {"commit_failed": False, "rollback_failed": False}
    original_replace = app.os.replace

    def fail_commit_then_rollback(source, destination):
        source_text = str(source)
        destination_path = Path(destination).resolve()
        if (
            not state["commit_failed"]
            and source_text.endswith(".batch-stage")
            and destination_path == second_path
        ):
            state["commit_failed"] = True
            raise OSError("injected second commit failure")
        if (
            state["commit_failed"]
            and not state["rollback_failed"]
            and source_text.endswith(".batch-backup")
            and destination_path == first_path
        ):
            state["rollback_failed"] = True
            raise OSError("injected rollback failure")
        return original_replace(source, destination)

    monkeypatch.setattr(app.os, "replace", fail_commit_then_rollback)
    monkeypatch.setattr(
        app,
        "_broadcast_pc_state",
        lambda name: broadcasts.append(name),
    )

    response = app.app.test_client().post(
        "/api/rest/apply",
        json={"type": "long"},
    )

    assert state == {"commit_failed": True, "rollback_failed": True}
    assert response.status_code == 503
    assert "Do not retry until the server files are checked" in (
        response.get_json()["error"]
    )
    assert broadcasts == []

    # The first commit remains live because its rollback failed; the untouched
    # second file remains at the pre-batch version.  The sole preserved backup
    # is the exact recovery copy for that partially committed first file.
    assert first_path.read_bytes() != before["files"][first_name]
    assert second_path.read_bytes() == before["files"][second_name]
    backups = list(two_character_party["tmp_path"].glob("*.batch-backup"))
    assert len(backups) == 2
    assert sorted(path.read_bytes() for path in backups) == sorted(
        before["files"].values()
    )
    journals = list(
        two_character_party["tmp_path"].glob("*.character-batch-journal")
    )
    assert len(journals) == 1

    # Catastrophic rollback cannot promise the old in-memory identities.  It
    # must instead reload each actor from the bytes that actually remain live.
    for name, path in two_character_party["paths"].items():
        doc = json.loads(path.read_text(encoding="utf-8"))
        expected = app._actors_from_character_doc(doc, str(path))[0]
        actual = app.PARTY_LIBRARY[name]
        assert actual is not before["actors"][name]
        assert actual.name == expected.name
        assert actual.current_hp == expected.current_hp
        assert actual.current_focus == expected.current_focus
        assert actual.temp_hp_manual == expected.temp_hp_manual
        assert actual.conditions == expected.conditions


def test_startup_recovery_prepared_journal_restores_entire_old_batch(
    two_character_party,
):
    before = _snapshot(two_character_party)
    entries, journal = _crash_journal(two_character_party, "prepared")

    # Simulate a process death after only the first staged file became live.
    app.os.replace(entries[0]["stage"], entries[0]["path"])
    assert Path(entries[0]["path"]).read_bytes() != (
        before["files"][entries[0]["name"]]
    )

    app._recover_character_batch_transactions(two_character_party["tmp_path"])

    assert {
        name: path.read_bytes()
        for name, path in two_character_party["paths"].items()
    } == before["files"]
    assert not journal.exists()
    assert list(two_character_party["tmp_path"].glob("*.batch-stage")) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-backup")) == []


def test_startup_recovery_committed_journal_keeps_new_batch_and_cleans_artifacts(
    two_character_party,
):
    entries, journal = _crash_journal(two_character_party, "prepared")
    for entry in entries:
        app.os.replace(entry["stage"], entry["path"])
    app._write_character_batch_journal(str(journal), "committed", entries)
    committed_bytes = {
        name: path.read_bytes()
        for name, path in two_character_party["paths"].items()
    }

    app._recover_character_batch_transactions(two_character_party["tmp_path"])

    assert {
        name: path.read_bytes()
        for name, path in two_character_party["paths"].items()
    } == committed_bytes
    assert not journal.exists()
    assert list(two_character_party["tmp_path"].glob("*.batch-stage")) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-backup")) == []


def test_startup_recovery_malformed_journal_preserves_unrelated_paths(tmp_path):
    external = tmp_path / "external" / "do-not-delete.json"
    external.parent.mkdir()
    external.write_bytes(b"operator recovery copy")
    journal = tmp_path / ".broken.character-batch-journal"
    journal.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="cannot recover character batch"):
        app._recover_character_batch_transactions(tmp_path)

    assert external.read_bytes() == b"operator recovery copy"
    assert journal.exists()


def test_startup_recovery_escaping_journal_preserves_external_paths(tmp_path):
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external = {
        key: external_dir / f"{key}.json"
        for key in ("path", "stage", "backup")
    }
    for key, path in external.items():
        path.write_bytes(f"external {key}".encode("utf-8"))
    journal = tmp_path / ".escaping.character-batch-journal"
    journal.write_text(
        json.dumps({
            "version": 1,
            "state": "prepared",
            "entries": [{
                "name": "Escaping Actor",
                **{key: str(path) for key, path in external.items()},
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="escapes the character directory"):
        app._recover_character_batch_transactions(tmp_path)

    for key, path in external.items():
        assert path.read_bytes() == f"external {key}".encode("utf-8")
    assert journal.exists()


def test_pending_prepared_journal_is_recovered_before_retry_can_commit(
    two_character_party, monkeypatch
):
    before = _snapshot(two_character_party)
    _fail_second_commit(monkeypatch, two_character_party)
    original_remove_journal = app._remove_character_batch_journal
    remove_calls = 0

    def fail_first_journal_removal(path):
        nonlocal remove_calls
        remove_calls += 1
        if remove_calls == 1:
            raise OSError("injected journal removal failure")
        return original_remove_journal(path)

    monkeypatch.setattr(
        app,
        "_remove_character_batch_journal",
        fail_first_journal_removal,
    )

    with pytest.raises(HTTPException) as first_failure:
        app._save_and_reload_character_batch(_updates(two_character_party))
    assert first_failure.value.code == 503
    assert {
        name: path.read_bytes()
        for name, path in two_character_party["paths"].items()
    } == before["files"]
    assert len(list(
        two_character_party["tmp_path"].glob("*.character-batch-journal")
    )) == 1

    retry = _updates(two_character_party)
    for _name, document, _path in retry:
        document.get("build", document)["notes"] = "must not overlay baseline"
    with pytest.raises(HTTPException) as retry_failure:
        app._save_and_reload_character_batch(retry)

    assert retry_failure.value.code == 503
    assert "previous interrupted character operation was recovered" in (
        retry_failure.value.description
    )
    assert {
        name: path.read_bytes()
        for name, path in two_character_party["paths"].items()
    } == before["files"]
    assert remove_calls == 2
    assert list(
        two_character_party["tmp_path"].glob("*.character-batch-journal")
    ) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-stage")) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-backup")) == []


def test_pending_batch_recovery_aborts_stale_single_character_save(
    two_character_party, monkeypatch
):
    """A document read from a partial generation must never survive recovery."""
    before = _snapshot(two_character_party)
    monkeypatch.setattr(app, "PARTY_DIR", str(two_character_party["tmp_path"]))
    entries, journal = _crash_journal(two_character_party, "prepared")

    # Model a crash after one member reached the new generation.  The caller's
    # document was prepared while that mixed generation was observable.
    app.os.replace(entries[0]["stage"], entries[0]["path"])
    name = entries[0]["name"]
    stale_document = json.loads(
        Path(entries[0]["path"]).read_text(encoding="utf-8")
    )
    stale_document.get("build", stale_document)["notes"] = (
        "stale request must not overlay recovered baseline"
    )

    with pytest.raises(HTTPException) as caught:
        app.save_and_reload_character(
            name,
            stale_document,
            str(two_character_party["paths"][name]),
        )

    assert caught.value.code == 503
    assert "previous interrupted character operation was recovered" in (
        caught.value.description
    )
    assert {
        actor_name: path.read_bytes()
        for actor_name, path in two_character_party["paths"].items()
    } == before["files"]
    assert not journal.exists()
    assert list(two_character_party["tmp_path"].glob("*.batch-stage")) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-backup")) == []


def test_background_dirty_flush_recovers_pending_batch_without_writing(
    two_character_party, monkeypatch
):
    """Recovery aborts the debounce write and its dirty marker is retained."""
    before = _snapshot(two_character_party)
    monkeypatch.setattr(app, "PARTY_DIR", str(two_character_party["tmp_path"]))
    monkeypatch.setattr(app, "_PERSIST_DIRTY", False)
    entries, journal = _crash_journal(two_character_party, "prepared")
    app.os.replace(entries[0]["stage"], entries[0]["path"])

    name = entries[0]["name"]
    app.PARTY_LIBRARY[name].current_hp = max(
        1, app.PARTY_LIBRARY[name].current_hp - 17
    )
    app._PC_PERSIST_DIRTY.add(name)

    assert app._flush_pending_persistence_locked() is False

    assert name in app._PC_PERSIST_DIRTY
    assert {
        actor_name: path.read_bytes()
        for actor_name, path in two_character_party["paths"].items()
    } == before["files"]
    assert not journal.exists()
    assert list(two_character_party["tmp_path"].glob("*.batch-stage")) == []
    assert list(two_character_party["tmp_path"].glob("*.batch-backup")) == []


def test_advancement_aborts_after_pending_batch_recovery_without_xp_mutation(
    two_character_party, monkeypatch
):
    before = _snapshot(two_character_party)
    monkeypatch.setattr(app, "PARTY_DIR", str(two_character_party["tmp_path"]))
    entries, journal = _crash_journal(two_character_party, "prepared")
    app.os.replace(entries[0]["stage"], entries[0]["path"])
    name = entries[0]["name"]
    baseline_document = json.loads(before["files"][name])
    baseline_build = baseline_document.get("build", baseline_document)
    baseline_xp = int(baseline_build.get("xp", 0) or 0)

    with pytest.raises(HTTPException) as caught:
        app._pf2e_set_pc_advancement(name, add_xp=275)

    assert caught.value.code == 503
    assert "previous interrupted character operation was recovered" in (
        caught.value.description
    )
    recovered_document = json.loads(
        two_character_party["paths"][name].read_text(encoding="utf-8")
    )
    recovered_build = recovered_document.get("build", recovered_document)
    assert int(recovered_build.get("xp", 0) or 0) == baseline_xp
    assert {
        actor_name: path.read_bytes()
        for actor_name, path in two_character_party["paths"].items()
    } == before["files"]
    assert not journal.exists()


@pytest.mark.parametrize("invalid_live", ["missing", "actor-invalid"])
def test_committed_recovery_preserves_backups_when_live_generation_is_invalid(
    two_character_party, monkeypatch, invalid_live
):
    before = _snapshot(two_character_party)
    entries, journal = _crash_journal(two_character_party, "prepared")
    for entry in entries:
        app.os.replace(entry["stage"], entry["path"])
    app._write_character_batch_journal(str(journal), "committed", entries)

    damaged_path = Path(entries[0]["path"])
    if invalid_live == "missing":
        damaged_path.unlink()
    else:
        damaged_path.write_text(
            json.dumps({"force_test_actor_rejection": True}),
            encoding="utf-8",
        )
        original_actor_builder = app._actors_from_character_doc

        def reject_invalid_actor(document, source):
            if document.get("force_test_actor_rejection"):
                raise ValueError("injected actor validation failure")
            return original_actor_builder(document, source)

        monkeypatch.setattr(
            app,
            "_actors_from_character_doc",
            reject_invalid_actor,
        )

    with pytest.raises(RuntimeError, match="cannot recover character batch"):
        app._recover_character_batch_transactions(two_character_party["tmp_path"])

    assert journal.exists()
    for entry in entries:
        backup = Path(entry["backup"])
        assert backup.exists()
        assert backup.read_bytes() == before["files"][entry["name"]]
    if invalid_live == "missing":
        assert not damaged_path.exists()
    else:
        assert json.loads(damaged_path.read_text(encoding="utf-8")) == {
            "force_test_actor_rejection": True
        }


@pytest.mark.parametrize("violation", ["wrong-suffix", "overlap"])
def test_startup_recovery_rejects_invalid_artifact_layout_without_cleanup(
    tmp_path, violation
):
    document = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def write_json(path):
        path.write_text(json.dumps(document), encoding="utf-8")
        return str(path.resolve())

    live = write_json(tmp_path / "Kyle.json")
    stage_path = tmp_path / (
        "Kyle.wrong-stage" if violation == "wrong-suffix" else "Kyle.batch-stage"
    )
    stage = write_json(stage_path)
    backup = write_json(tmp_path / "Kyle.batch-backup")
    entries = [{
        "name": "Kyle",
        "path": live,
        "stage": stage,
        "backup": backup,
    }]
    if violation == "overlap":
        entries.append({
            "name": "Other Hero",
            "path": live,
            "stage": write_json(tmp_path / "Other.batch-stage"),
            "backup": write_json(tmp_path / "Other.batch-backup"),
        })
    journal = tmp_path / ".invalid-layout.character-batch-journal"
    journal.write_text(
        json.dumps({"version": 1, "state": "prepared", "entries": entries}),
        encoding="utf-8",
    )
    all_paths = [
        path for path in tmp_path.iterdir()
        if path != journal
    ]
    before = {path: path.read_bytes() for path in all_paths}

    expected = "wrong artifact type" if violation == "wrong-suffix" else "overlap"
    with pytest.raises(RuntimeError, match=expected):
        app._recover_character_batch_transactions(tmp_path)

    assert journal.exists()
    assert {path: path.read_bytes() for path in all_paths} == before


def test_load_libraries_recovers_batches_before_constructing_actors(
    tmp_path, monkeypatch
):
    party_dir = tmp_path / "party"
    party_dir.mkdir()
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    (party_dir / "Kyle.json").write_text(json.dumps(doc), encoding="utf-8")
    order = []

    monkeypatch.setattr(app, "PARTY_DIR", str(party_dir))
    monkeypatch.setattr(app, "MONSTER_DIR", str(tmp_path / "no-monsters"))
    monkeypatch.setattr(app, "BASE_DIR", str(tmp_path / "no-repo-data"))
    monkeypatch.setattr(app, "BUILDER_WEAPONS", [])
    monkeypatch.setattr(app, "MONSTER_LIBRARY", {})
    monkeypatch.setattr(app, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app, "load_compendium", lambda: None)
    monkeypatch.setattr(app, "_build_pc_file_cache", lambda: None)

    def recover(directory):
        assert directory == str(party_dir)
        order.append("recover")

    def build_actor(character_doc, source):
        order.append("actor")
        assert "recover" in order
        return SimpleNamespace(
            name=character_doc.get("build", character_doc)["name"],
            source=source,
        )

    monkeypatch.setattr(app, "_recover_character_batch_transactions", recover)
    monkeypatch.setattr(app, "make_actor", build_actor)

    app.load_libraries(restore_autosave=False)

    assert order == ["recover", "actor"]


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/api/rest/apply", {"type": "long"}),
        ("/api/daily_prep_all", {"heal_full": True}),
    ],
)
@pytest.mark.parametrize("failure_phase", ["staging", "commit"])
def test_whole_party_routes_do_not_publish_partial_batch_failure(
    two_character_party, monkeypatch, endpoint, payload, failure_phase
):
    before = _snapshot(two_character_party)
    broadcasts = []
    monkeypatch.setattr(
        app,
        "_broadcast_pc_state",
        lambda name: broadcasts.append(name),
    )
    if failure_phase == "staging":
        _fail_second_stage(monkeypatch)
    else:
        state = _fail_second_commit(monkeypatch, two_character_party)

    response = app.app.test_client().post(endpoint, json=payload)

    assert response.status_code == 503
    if failure_phase == "commit":
        assert state["injected"] is True
    _assert_snapshot_unchanged(two_character_party, before)
    assert broadcasts == []
