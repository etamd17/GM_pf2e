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


# --- PE-11b: D&D characters in a SQL-mode archive restore -------------------------

P1, P2 = 'd' * 32, 'e' * 32
SOURCE_CID = 'a' * 32
PNG = b'\x89PNG\r\n\x1a\nnot really an image'


def stage_dnd5e(*, system='dnd5e', sheets=True):
    """A staged archive with only canonical directories that the case tests avoid."""
    cid = 'c' * 32
    stage = Path(tempfile.mkdtemp(dir=storage.CAMPAIGNS_DIR,
                                 prefix=f'.campaign-import-{cid}-'))
    doc = storage.new_campaign(cid, 'Restored', system, 'importer')
    doc['members'] = [storage.campaign_member('importer', 'gm')]
    storage.atomic_write_json(str(stage / 'campaign.json'), doc)
    if sheets:
        characters = stage / 'dnd5e' / 'characters'
        characters.mkdir(parents=True)
        storage.atomic_write_json(str(characters / f'{P1}.json'), {
            'id': P1, 'campaign_id': SOURCE_CID, 'system': 'dnd5e', 'name': 'Bram',
            'owner_user_id': 'source-owner'})
        storage.atomic_write_json(str(characters / f'{P2}.json'), {
            'id': P2, 'system': 'dnd5e', 'name': 'Cora'})
        storage.atomic_write_json(str(stage / 'dnd5e' / 'encounter.json'), {
            'combatants': [{'pc_id': P1}, {'pc_id': P2}, {'name': 'Goblin'}]})
        (stage / 'dnd5e' / 'plans').mkdir()
        storage.atomic_write_json(str(stage / 'dnd5e' / 'plans' / 'ambush.json'), {'title': 'Ambush'})
        (stage / 'dnd5e' / 'images').mkdir()
        (stage / 'dnd5e' / 'images' / 'map.png').write_bytes(PNG)
    return stage, Path(storage.campaign_dir(cid)), doc


def _assert_refused(sql_import, stage, final, doc):
    assert stage.is_dir() and not final.exists()
    with sql_import.session() as session:
        assert session.get(Campaign, doc['id']) is None
        assert session.scalar(select(func.count()).select_from(Character)) == 1


def test_dnd5e_archive_restores_with_fresh_registered_identities(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    from core.persistence.ownership import _checksum
    stage, final, doc = stage_dnd5e()
    import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert final.is_dir() and not stage.exists()
    with sql_import.session() as session:
        rows = session.scalars(select(Character).where(Character.campaign_id == doc['id'])).all()
        assert len(rows) == 2
        new_ids = {row.id for row in rows}
        assert new_ids.isdisjoint({P1, P2})
        for row in rows:
            assert (row.system, row.legacy_storage, row.legacy_file) == (
                'dnd5e', 'dnd5e_characters', row.id + '.json')
            sheet = storage.load_json(str(final / 'dnd5e' / 'characters' / row.legacy_file))
            assert (sheet['id'], sheet['campaign_id'], sheet['system']) == (row.id, doc['id'], 'dnd5e')
            assert sheet['owner_user_id'] is None
            assert sheet['editor_user_ids'] == [] and sheet['viewer_user_ids'] == []
            assert row.content_checksum == _checksum(sheet)
        assert session.scalars(select(CampaignMembership.user_id).where(
            CampaignMembership.campaign_id == doc['id'])).all() == ['importer']
        assert session.scalar(select(func.count()).select_from(CharacterAssignment)) == 0
    assert sorted(path.name for path in (final / 'dnd5e' / 'characters').iterdir()) == sorted(
        row_id + '.json' for row_id in new_ids)
    encounter = storage.load_json(str(final / 'dnd5e' / 'encounter.json'))
    assert {item['pc_id'] for item in encounter['combatants'] if 'pc_id' in item} == new_ids
    assert storage.load_json(str(final / 'dnd5e' / 'plans' / 'ambush.json')) == {'title': 'Ambush'}
    assert (final / 'dnd5e' / 'images' / 'map.png').read_bytes() == PNG


@pytest.mark.parametrize('defect', [
    'non_hex_stem', 'non_hex_id', 'id_newline', 'stem_differs_from_id',
    'missing_system', 'empty_system', 'pf2e_system', 'nested', 'duplicate_across_stores',
])
def test_dnd5e_archive_refuses_bad_sheets_without_publishing(sql_import, defect):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_dnd5e()
    characters = stage / 'dnd5e' / 'characters'
    sheet = storage.load_json(str(characters / f'{P1}.json'))
    if defect == 'non_hex_stem':
        (characters / f'{P1}.json').rename(characters / 'hero.json')
    elif defect == 'non_hex_id':
        storage.atomic_write_json(str(characters / f'{P1}.json'), {**sheet, 'id': 'hero'})
    elif defect == 'id_newline':
        storage.atomic_write_json(str(characters / f'{P1}.json'), {**sheet, 'id': P1 + '\n'})
    elif defect == 'stem_differs_from_id':
        storage.atomic_write_json(str(characters / f'{P1}.json'), {**sheet, 'id': 'f' * 32})
    elif defect.endswith('_system'):
        if defect == 'missing_system':
            sheet.pop('system')
        else:
            sheet['system'] = '' if defect == 'empty_system' else 'pf2e'
        storage.atomic_write_json(str(characters / f'{P1}.json'), sheet)
    elif defect == 'nested':
        (characters / 'sub').mkdir()
        storage.atomic_write_json(str(characters / 'sub' / 'x.json'), {'id': 'f' * 32})
    else:
        (stage / 'party_data').mkdir()
        storage.atomic_write_json(str(stage / 'party_data' / 'hero.json'),
                                  {'id': P1, 'campaign_id': doc['id'], 'system': 'pf2e'})
    with pytest.raises(ValueError, match='duplicate' if defect == 'duplicate_across_stores' else None):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


@pytest.mark.skipif(os.name == 'nt', reason='Windows cannot create a file name with a newline')
def test_dnd5e_archive_refuses_a_stem_with_a_trailing_newline(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_dnd5e()
    characters = stage / 'dnd5e' / 'characters'
    (characters / f'{P1}.json').rename(characters / f'{P1}\n.json')
    with pytest.raises(ValueError):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


@pytest.mark.parametrize('system', ['dnd5e', 'pf2e'])
@pytest.mark.parametrize('variant', [('DnD5e', 'characters'), ('dnd5e', 'Characters'),
                                     ('Party_Data',), ('COSMERE_PCS',)])
def test_case_variant_store_prefix_is_refused_in_every_system(sql_import, system, variant):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_dnd5e(system=system, sheets=False)
    directory = stage.joinpath(*variant)
    directory.mkdir(parents=True)
    storage.atomic_write_json(str(directory / f'{P1}.json'), {
        'id': P1, 'campaign_id': doc['id'], 'system': system})
    with pytest.raises(ValueError, match='exact name'):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


def test_nested_dnd5e_character_directory_is_refused_in_a_pf2e_campaign(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_dnd5e(system='pf2e', sheets=False)
    nested = stage / 'dnd5e' / 'characters' / 'sub'
    nested.mkdir(parents=True)
    storage.atomic_write_json(str(nested / 'x.json'), {'id': P1})
    with pytest.raises(ValueError, match='nested'):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


def test_pf2e_archive_keeps_dnd5e_files_as_inert_json(sql_import):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign()
    characters = stage / 'dnd5e' / 'characters'
    characters.mkdir(parents=True)
    storage.atomic_write_json(str(characters / f'{P1}.json'), {
        'id': P1, 'campaign_id': SOURCE_CID, 'system': 'dnd5e', 'name': 'Bram'})
    storage.atomic_write_json(str(characters / 'not-hex.json'), {'id': 'not-hex', 'name': 'Odd'})
    import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert sorted(path.name for path in (final / 'dnd5e' / 'characters').iterdir()) == [
        P1 + '.json', 'not-hex.json']
    assert storage.load_json(str(final / 'dnd5e' / 'characters' / f'{P1}.json'))['id'] == P1
    with sql_import.session() as session:
        assert session.scalars(select(Character.legacy_storage).where(
            Character.campaign_id == doc['id'])).all() == ['party_data']


def test_cosmere_rename_collision_is_refused(sql_import, monkeypatch):
    from core.persistence import campaign_import
    stage, final, doc = stage_campaign(system='cosmere')
    pcs = stage / 'cosmere_pcs'
    sheet = storage.load_json(str(pcs / ('b' * 32 + '.json')))
    (pcs / ('b' * 32 + '.json')).unlink()
    # Sorts first, so it is renamed while the sibling named after its fresh id exists.
    storage.atomic_write_json(str(pcs / ('0' * 32 + '.json')), sheet)
    storage.atomic_write_json(str(pcs / ('9' * 32 + '.json')), {**sheet, 'id': '8' * 32})
    generated = iter(['7' * 32, '9' * 32])  # '8'*32 -> '7'*32, then 'b'*32 -> '9'*32
    monkeypatch.setattr(campaign_import, 'new_id', lambda: next(generated))
    with pytest.raises(ValueError, match='collision'):
        campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


def test_dnd5e_rename_collision_is_refused(sql_import, monkeypatch):
    from core.persistence import campaign_import
    stage, final, doc = stage_dnd5e()
    real_lexists = os.path.lexists
    staged_names = {P1 + '.json', P2 + '.json'}

    def target_present(path):
        path = Path(path)
        if path.parent.name == 'characters' and path.name not in staged_names:
            return True  # defence in depth: stem == id makes this unreachable
        return real_lexists(path)

    monkeypatch.setattr(campaign_import.os.path, 'lexists', target_present)
    with pytest.raises(ValueError, match='collision'):
        campaign_import.import_staged_campaign(str(stage), str(final), doc, 'importer')
    monkeypatch.undo()
    _assert_refused(sql_import, stage, final, doc)


@pytest.mark.parametrize('system', ['dnd5e', 'pf2e'])
@pytest.mark.parametrize('directory', [('dnd5e', 'characters'), ('party_data',)])
def test_case_variant_json_extension_in_a_store_is_refused(sql_import, system, directory):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_dnd5e(system=system, sheets=False)
    store = stage.joinpath(*directory)
    store.mkdir(parents=True)
    storage.atomic_write_json(str(store / f'{P1}.JSON'), {
        'id': P1, 'campaign_id': doc['id'], 'system': system, 'owner_user_id': 'source-owner'})
    with pytest.raises(ValueError, match='exact .json extension'):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    _assert_refused(sql_import, stage, final, doc)


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_import_keeps_strict_campaign_check_for_pf2e_and_cosmere(sql_import, system):
    from core.persistence.campaign_import import import_staged_campaign
    stage, final, doc = stage_campaign(system=system)
    path = (stage / 'party_data' / 'hero.json' if system == 'pf2e'
            else stage / 'cosmere_pcs' / ('b' * 32 + '.json'))
    sheet = storage.load_json(str(path))
    sheet['campaign_id'] = SOURCE_CID
    storage.atomic_write_json(str(path), sheet)
    with pytest.raises(ValueError, match='does not match'):
        import_staged_campaign(str(stage), str(final), doc, 'importer')
    assert stage.is_dir() and not final.exists()
    with sql_import.session() as session:
        assert session.get(Campaign, doc['id']) is None
