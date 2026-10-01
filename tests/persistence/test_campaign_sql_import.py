"""Archive restore publishes new relational identities with retained files."""

import os
from pathlib import Path
import tempfile

import pytest
from sqlalchemy import event, func, select

from core import storage
from core.persistence import runtime
from core.persistence.models import (
    AuditEvent, Campaign, CampaignMembership, Character, CharacterAssignment, User,
)


@pytest.fixture
def sql_import(sqlite_database, tmp_path, monkeypatch):
    monkeypatch.setenv('OWNERSHIP_BACKEND', 'sql')
    monkeypatch.setattr(runtime, 'database', lambda: sqlite_database)
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    os.mkdir(storage.CAMPAIGNS_DIR)
    with sqlite_database.transaction() as session:
        session.add(User(id='importer', username='importer', normalized_username='importer',
                         display_name='Importer', password_hash='hash'))
        session.flush()
        session.add(Campaign(id='a' * 32, slug='source', name='Source', system='pf2e'))
        session.flush()
        session.add(Character(id='b' * 32, campaign_id='a' * 32, system='pf2e',
                              legacy_storage='party_data', legacy_file='hero.json',
                              content_checksum='0' * 64))
    return sqlite_database


def stage_campaign(*, system='pf2e'):
    cid = 'c' * 32
    stage = Path(tempfile.mkdtemp(dir=storage.CAMPAIGNS_DIR,
                                 prefix=f'.campaign-import-{cid}-'))
    doc = storage.new_campaign(cid, 'Restored', system, 'importer')
    doc['members'] = [storage.campaign_member('importer', 'gm')]
    storage.atomic_write_json(str(stage / 'campaign.json'), doc)
    for dirname in ('party_data', 'cosmere_pcs', 'scenes'):
        (stage / dirname).mkdir()
    dirname = 'party_data' if system == 'pf2e' else 'cosmere_pcs'
    filename = 'hero.json' if system == 'pf2e' else 'b' * 32 + '.json'
    storage.atomic_write_json(str(stage / dirname / filename), {
        'id': 'b' * 32, 'campaign_id': cid, 'system': system, 'schema_version': 1,
        'name': 'Hero', 'owner_user_id': 'source-owner',
        'editor_user_ids': ['source-editor'], 'viewer_user_ids': ['source-viewer'],
        'nested': {'owner_user_id': 'hidden-owner'},
    })
    storage.atomic_write_json(str(stage / 'scenes' / 'scene.json'), {
        'tokens': {'b' * 32: {'character_id': 'b' * 32}},
        'prose': 'Reference ' + 'b' * 32 + ' stays literal',
    })
    return stage, Path(storage.campaign_dir(cid)), doc


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_import_rekeys_existing_sql_characters_and_all_exact_references(sql_import, system):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign(system=system)
    import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert final.is_dir() and not stage.exists()
    with sql_import.session() as session:
        characters = session.scalars(select(Character).where(Character.campaign_id == doc['id'])).all()
        assert len(characters) == 1
        character = characters[0]
        assert character.id != 'b' * 32
        assert session.get(Character, 'b' * 32).campaign_id == 'a' * 32
        assert session.get(CampaignMembership, (doc['id'], 'importer')).role == 'gm'
        assert session.scalar(select(func.count()).select_from(CharacterAssignment)) == 0
        assert session.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'campaign.imported')) == 1
        restored = storage.load_json(str(final / character.legacy_storage / character.legacy_file))
        assert restored['id'] == character.id
        assert restored['owner_user_id'] is None
        assert restored['editor_user_ids'] == []
        assert restored['viewer_user_ids'] == []
        assert restored['nested']['owner_user_id'] is None
        if system == 'cosmere':
            assert character.legacy_file == character.id + '.json'
        scene = storage.load_json(str(final / 'scenes' / 'scene.json'))
        assert scene['tokens'] == {character.id: {'character_id': character.id}}
        assert scene['prose'] == 'Reference ' + 'b' * 32 + ' stays literal'


def test_import_ignores_archived_campaign_grants(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign()
    stored = dict(doc, members=[{'user_id': 'source-owner', 'role': 'gm'}],
                  created_by='source-owner', _trashed_at='yesterday')
    storage.atomic_write_json(str(stage / 'campaign.json'), stored)
    import_staged_campaign(str(stage), str(final), doc, 'importer')
    with sql_import.session() as session:
        campaign = session.get(Campaign, doc['id'])
        assert campaign.created_by_user_id == 'importer'
        assert campaign.trashed_at is None
        assert session.scalars(select(CampaignMembership.user_id).where(
            CampaignMembership.campaign_id == doc['id'])).all() == ['importer']


@pytest.mark.parametrize('failure', ['publish', 'after_publish', 'commit'])
def test_import_failure_rolls_back_sql_and_published_directory(sql_import, monkeypatch, failure):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign()
    publication_method = 'rename' if campaign_import._WINDOWS else 'replace'
    original_publish = getattr(os, publication_method)
    def fail_publish(source, target):
        if str(source) == str(stage):
            if failure == 'after_publish':
                original_publish(source, target)
            raise OSError('injected publication failure')
        return original_publish(source, target)
    def fail_commit(session):
        if final.exists() and not stage.exists():
            raise RuntimeError('injected commit failure')
    if failure in {'publish', 'after_publish'}:
        monkeypatch.setattr(campaign_import.os, publication_method, fail_publish)
    else:
        event.listen(sql_import.session_factory, 'before_commit', fail_commit)
    try:
        with pytest.raises((runtime.StoreUnavailable, RuntimeError)):
            campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    finally:
        if failure == 'commit':
            event.remove(sql_import.session_factory, 'before_commit', fail_commit)
    assert stage.is_dir()
    assert not final.exists()
    with sql_import.session() as session:
        assert session.get(Campaign, doc['id']) is None
        assert session.scalar(select(func.count()).select_from(Character)) == 1
        assert session.scalar(select(func.count()).select_from(AuditEvent)) == 0


def test_import_never_overwrites_existing_destination(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign()
    final.mkdir()
    with pytest.raises(ValueError, match='destination'):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert final.is_dir() and stage.is_dir()


def test_import_rejects_missing_importer_without_publishing(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign()
    with pytest.raises(ValueError, match='importer'):
        import_staged_campaign(str(stage), str(final), doc, 'missing-user')
    assert stage.is_dir() and not final.exists()


def test_import_retries_generated_ids_that_already_exist(sql_import, monkeypatch):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign()
    generated = iter(['b' * 32, 'd' * 32])
    monkeypatch.setattr(campaign_import, 'new_id', lambda: next(generated))
    campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    with sql_import.session() as session:
        assert session.get(Character, 'b' * 32).campaign_id == 'a' * 32
        assert session.get(Character, 'd' * 32).campaign_id == doc['id']


@pytest.mark.parametrize('outcome', ['success', 'commit_failure', 'after_rename', 'collision'])
def test_windows_directory_publication_is_exclusive_and_compensated(sql_import, monkeypatch, outcome):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign()
    # Patching os.name itself would break pathlib on this Linux test host.
    monkeypatch.setattr(campaign_import, '_WINDOWS', True, raising=False)
    real_rename, real_replace = os.rename, os.replace
    rename_calls = []
    def windows_rename(source, destination):
        rename_calls.append((Path(source), Path(destination)))
        if Path(source) == stage and outcome == 'collision':
            final.mkdir()
            storage.atomic_write_json(str(final / 'existing.json'), {'keep': True})
        if os.path.lexists(destination):
            raise FileExistsError('Windows rename does not replace existing destinations')
        real_rename(source, destination)
        if Path(source) == stage and outcome == 'after_rename':
            raise OSError('injected post-publication error')
    def windows_replace(source, destination):
        if os.path.isdir(source) and os.path.lexists(destination):
            raise PermissionError('Windows cannot replace an existing directory')
        return real_replace(source, destination)
    def fail_commit(session):
        if final.exists() and not stage.exists():
            raise RuntimeError('injected commit failure')
    monkeypatch.setattr(campaign_import.os, 'rename', windows_rename)
    monkeypatch.setattr(campaign_import.os, 'replace', windows_replace)
    if outcome == 'commit_failure':
        event.listen(sql_import.session_factory, 'before_commit', fail_commit)
    try:
        if outcome == 'success':
            campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
        else:
            with pytest.raises((ValueError, runtime.StoreUnavailable, RuntimeError)):
                campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    finally:
        if outcome == 'commit_failure':
            event.remove(sql_import.session_factory, 'before_commit', fail_commit)
    assert rename_calls[0] == (stage, final)
    with sql_import.session() as session:
        if outcome == 'success':
            assert final.is_dir() and not stage.exists()
            assert session.get(Campaign, doc['id']) is not None
        else:
            assert stage.is_dir()
            assert session.get(Campaign, doc['id']) is None
            if outcome == 'collision':
                assert storage.load_json(str(final / 'existing.json')) == {'keep': True}
            else:
                assert not final.exists()
                assert rename_calls[-1] == (final, stage)


def test_windows_staging_privacy_does_not_use_posix_permission_bits(sql_import, monkeypatch):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign()
    stage.chmod(0o777)  # Windows stat bits do not describe its directory ACL.
    monkeypatch.setattr(campaign_import, '_WINDOWS', True, raising=False)
    campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert final.is_dir()


def test_posix_staging_still_requires_private_permission_bits(sql_import, monkeypatch):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign()
    stage.chmod(0o777)
    monkeypatch.setattr(campaign_import, '_WINDOWS', False)
    with pytest.raises(ValueError, match='private'):
        campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert stage.is_dir() and not final.exists()


@pytest.mark.parametrize('hostile', ['symlink', 'nested_character', 'bad_id', 'wrong_destination'])
def test_import_rejects_hostile_staged_paths_and_ids(sql_import, hostile, tmp_path):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign()
    if hostile == 'symlink':
        (stage / 'scenes' / 'outside.json').symlink_to(tmp_path / 'private.json')
    elif hostile == 'nested_character':
        (stage / 'party_data' / 'nested').mkdir()
        storage.atomic_write_json(str(stage / 'party_data' / 'nested' / 'hidden.json'), {'id': 'd' * 32})
    elif hostile == 'bad_id':
        storage.atomic_write_json(str(stage / 'party_data' / 'hero.json'), {'id': '../outside'})
    else:
        final = tmp_path / 'elsewhere'
    with pytest.raises(ValueError):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert stage.exists() and not final.exists()
