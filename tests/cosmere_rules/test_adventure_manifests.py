from dataclasses import replace
import hashlib
import json

import pytest

from systems.cosmere.adventures import (
    AdventureRecordIndex,
    compile_adventure_bundle,
    validate_adventure_bundle,
)
from systems.cosmere.private_assets import (
    PrivateAssetEntry,
    PrivateAssetManifest,
    import_private_asset,
    parse_private_asset_manifest,
    visible_private_assets,
)
from systems.cosmere.rules.schema import (
    AdventureManifest,
    ContentPack,
    RuleRecord,
    RulesValidationError,
    SourceArtifact,
    SourceRef,
)


SOURCE_SHA = "1" * 64


def _source():
    return SourceArtifact(
        product_id="adventure.demo.book",
        basename="Demo Adventure.pdf",
        edition="v1.0",
        sha256=SOURCE_SHA,
        byte_count=100,
        page_count=20,
        authority_tier="adventure",
        extraction_class="adventure",
        spoiler_scope="gm",
        rights_state="private",
    )


def _record(record_id, kind, *, spoiler="gm", references=(), page=1,
            overrides=None):
    return RuleRecord(
        id=record_id,
        version="1.0.0",
        kind=kind,
        name=record_id.rsplit(".", 1)[-1].title(),
        summary="An independently written mechanical summary.",
        citations=(SourceRef("adventure.demo.book", page, page),),
        worlds=("roshar",),
        eras=(),
        spoiler_scope=spoiler,
        automation="reference",
        audit_status="provisional",
        parameters={},
        references=references,
        overrides=overrides,
    )


def _bundle(*, asset_spoiler="player"):
    adversary = _record("adventure.demo.adversary.scout", "adversary")
    encounter = _record(
        "adventure.demo.encounter.ambush",
        "encounter",
        references=(adversary.id,),
    )
    reward = _record("adventure.demo.reward.cache", "reward")
    table = _record("adventure.demo.table.weather", "table")
    handout = _record(
        "adventure.demo.handout.map",
        "handout",
        spoiler=asset_spoiler,
    )
    scene = _record(
        "adventure.demo.scene.arrival",
        "scene",
        references=(encounter.id, reward.id, table.id, handout.id),
    )
    records = (scene, encounter, adversary, reward, table, handout)
    pack = ContentPack(
        id="adventure.demo.pack",
        version="1.0.0",
        kind="adventure",
        dependencies=("cosmere.core",),
        sources=("adventure.demo.book",),
        worlds=("roshar",),
        eras=(),
        spoiler_scope="gm",
        audit_status="provisional",
        records=records,
    )
    manifest = AdventureManifest(
        id="adventure.demo.manifest",
        version=pack.version,
        pack_id=pack.id,
        sources=pack.sources,
        worlds=pack.worlds,
        eras=pack.eras,
        spoiler_scope=pack.spoiler_scope,
        audit_status=pack.audit_status,
        records=records,
    )
    index = AdventureRecordIndex(
        scene_order=(scene.id,),
        encounters=(encounter.id,),
        adversaries=(adversary.id,),
        rewards=(reward.id,),
        tables=(table.id,),
        handouts=(handout.id,),
        assets=(handout.id,),
    )
    asset = PrivateAssetEntry(
        id=handout.id,
        asset_type="map",
        source_artifact_id="adventure.demo.book",
        expected_sha256="2" * 64,
        source_page=6,
        storage_ref="adventure-demo/maps/arrival.png",
    )
    assets = PrivateAssetManifest(
        adventure_id=manifest.id,
        entries=(asset,),
    )
    return manifest, pack, index, assets


def _validate(manifest=None, pack=None, index=None, assets=None,
              *, available_records=()):
    defaults = _bundle()
    return validate_adventure_bundle(
        manifest or defaults[0],
        pack or defaults[1],
        index or defaults[2],
        assets or defaults[3],
        source_artifacts={"adventure.demo.book": _source()},
        available_records=available_records,
    )


def test_adventure_bundle_resolves_typed_records_and_compiles_deterministically():
    manifest, pack, index, assets = _bundle()

    validated = _validate(manifest, pack, index, assets)
    first = compile_adventure_bundle(validated)

    reversed_pack = replace(pack, records=tuple(reversed(pack.records)))
    reversed_manifest = replace(manifest, records=tuple(reversed(manifest.records)))
    shuffled_index = replace(
        index,
        encounters=tuple(reversed(index.encounters)),
        adversaries=tuple(reversed(index.adversaries)),
    )
    second = compile_adventure_bundle(
        _validate(reversed_manifest, reversed_pack, shuffled_index, assets)
    )

    assert first == second
    payload = json.loads(first)
    assert payload["index"]["scene_order"] == [
        "adventure.demo.scene.arrival"
    ]
    assert payload["index"]["encounters"] == [
        "adventure.demo.encounter.ambush"
    ]
    assert [record["id"] for record in payload["manifest"]["records"]] == sorted(
        record.id for record in pack.records
    )
    assert payload["content_pack"]["id"] == pack.id
    assert payload["content_pack"]["dependencies"] == ["cosmere.core"]
    assert [record["id"] for record in payload["content_pack"]["records"]] == sorted(
        record.id for record in pack.records
    )


def test_asset_manifest_order_is_not_semantic():
    manifest, pack, index, assets = _bundle()
    second_handout = _record(
        "adventure.demo.handout.portrait",
        "handout",
        spoiler="player",
    )
    scene = replace(
        pack.records[0],
        references=pack.records[0].references + (second_handout.id,),
    )
    records = (scene,) + pack.records[1:] + (second_handout,)
    second_asset = PrivateAssetEntry(
        id=second_handout.id,
        asset_type="handout",
        source_artifact_id="adventure.demo.book",
        expected_sha256="3" * 64,
        source_page=7,
        storage_ref="adventure-demo/handouts/portrait.webp",
    )
    index = replace(
        index,
        handouts=index.handouts + (second_handout.id,),
        assets=index.assets + (second_handout.id,),
    )
    assets = replace(assets, entries=(second_asset,) + assets.entries)

    validated = _validate(
        replace(manifest, records=records),
        replace(pack, records=records),
        index,
        assets,
    )

    assert {entry.id for entry in validated.assets.entries} == set(index.assets)


def test_adventure_bundle_rejects_unresolved_record_reference():
    manifest, pack, index, assets = _bundle()
    broken_scene = replace(
        pack.records[0],
        references=("adventure.demo.encounter.missing",),
    )
    broken_records = (broken_scene,) + pack.records[1:]

    with pytest.raises(RulesValidationError, match="unresolved adventure reference"):
        _validate(
            replace(manifest, records=broken_records),
            replace(pack, records=broken_records),
            index,
            assets,
        )


def test_adventure_bundle_rejects_unlisted_or_wrong_kind_record():
    manifest, pack, index, assets = _bundle()

    with pytest.raises(RulesValidationError, match="encounter index does not match"):
        _validate(manifest, pack, replace(index, encounters=()), assets)

    with pytest.raises(RulesValidationError, match="expected encounter"):
        _validate(
            manifest,
            pack,
            replace(index, encounters=("adventure.demo.reward.cache",)),
            assets,
        )


def test_adventure_bundle_rejects_core_collision_and_override_attempts():
    manifest, pack, index, assets = _bundle()
    colliding_core = _record(pack.records[2].id, "adversary")

    with pytest.raises(RulesValidationError, match="collides with an enabled rule"):
        _validate(
            manifest,
            pack,
            index,
            assets,
            available_records=(colliding_core,),
        )

    with pytest.raises(RulesValidationError, match="adventure may not override"):
        replace(pack, records=(replace(pack.records[0], overrides="cosmere.core.rule"),))


def test_adventure_asset_provenance_must_resolve_and_stay_within_source_pages():
    manifest, pack, index, assets = _bundle()
    bad_page = replace(assets.entries[0], source_page=21)

    with pytest.raises(RulesValidationError, match="asset page exceeds source"):
        _validate(
            manifest,
            pack,
            index,
            replace(assets, entries=(bad_page,)),
        )

    missing_source = replace(
        assets.entries[0], source_artifact_id="adventure.unknown.book"
    )
    with pytest.raises(RulesValidationError, match="unknown asset source"):
        _validate(
            manifest,
            pack,
            index,
            replace(assets, entries=(missing_source,)),
        )


def test_adventure_pack_provenance_requires_canonical_source_index_keys():
    manifest, pack, index, assets = _bundle()

    with pytest.raises(RulesValidationError, match="unknown adventure pack source"):
        validate_adventure_bundle(
            manifest,
            pack,
            index,
            assets,
            source_artifacts={},
        )

    with pytest.raises(RulesValidationError, match="source index identity mismatch"):
        validate_adventure_bundle(
            manifest,
            pack,
            index,
            assets,
            source_artifacts={"adventure.demo.alias": _source()},
        )


def test_private_asset_wire_parser_rejects_paths_prose_binary_and_extra_fields():
    manifest, _, _, assets = _bundle()
    wire = {
        "adventure_id": manifest.id,
        "entries": [
            {
                "id": assets.entries[0].id,
                "asset_type": "map",
                "source_artifact_id": "adventure.demo.book",
                "expected_sha256": "2" * 64,
                "source_page": 6,
                "storage_ref": "adventure-demo/maps/arrival.png",
            }
        ],
    }
    assert parse_private_asset_manifest(wire) == assets

    for forbidden in ("local_path", "source_text", "binary_payload"):
        tainted = json.loads(json.dumps(wire))
        tainted["entries"][0][forbidden] = "not allowed"
        with pytest.raises(RulesValidationError, match="invalid asset entry fields"):
            parse_private_asset_manifest(tainted)

    tainted = json.loads(json.dumps(wire))
    tainted["entries"][0]["storage_ref"] = "../../outside.png"
    with pytest.raises(RulesValidationError, match="private storage reference"):
        parse_private_asset_manifest(tainted)


def test_private_asset_manifest_rejects_case_insensitive_storage_collisions():
    first = _bundle()[3].entries[0]
    second = replace(
        first,
        id="adventure.demo.handout.second-map",
        storage_ref=first.storage_ref.upper(),
    )

    with pytest.raises(RulesValidationError, match="duplicate private storage"):
        PrivateAssetManifest(
            adventure_id="adventure.demo.manifest",
            entries=(first, second),
        )


def test_private_asset_import_requires_gm_and_atomically_copies_verified_bytes(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"private-map-bytes"
    source = tmp_path / "owned-map.png"
    source.write_bytes(png)
    entry = replace(
        _bundle()[3].entries[0],
        expected_sha256=hashlib.sha256(png).hexdigest(),
    )
    storage = tmp_path / "private-storage"

    with pytest.raises(PermissionError, match="GM authorization"):
        import_private_asset(
            entry,
            source_path=source,
            storage_root=storage,
            campaign_id="campaign-1",
            declared_mime="image/png",
            gm_authorized=False,
        )

    imported = import_private_asset(
        entry,
        source_path=source,
        storage_root=storage,
        campaign_id="campaign-1",
        declared_mime="image/png",
        gm_authorized=True,
    )

    assert imported == storage / "campaigns" / "campaign-1" / entry.storage_ref
    assert imported.read_bytes() == png
    assert not list(imported.parent.glob("*.tmp"))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"expected_sha256": "f" * 64}, "asset hash mismatch"),
        ({"declared_mime": "image/jpeg"}, "declared MIME does not match"),
        ({"asset_type": "token", "declared_mime": "application/pdf"},
         "not allowed for token"),
    ],
)
def test_private_asset_import_rejects_hash_mime_and_type_mismatches(
    tmp_path, change, message
):
    pdf = b"%PDF-1.7\nprivate fixture"
    source = tmp_path / "owned.pdf"
    source.write_bytes(pdf)
    entry = replace(
        _bundle()[3].entries[0],
        storage_ref="adventure-demo/maps/arrival.pdf",
        expected_sha256=hashlib.sha256(pdf).hexdigest(),
    )
    declared_mime = "application/pdf"
    entry_changes = {
        key: value for key, value in change.items() if key != "declared_mime"
    }
    if entry_changes:
        entry = replace(entry, **entry_changes)
    declared_mime = change.get("declared_mime", declared_mime)

    with pytest.raises(RulesValidationError, match=message):
        import_private_asset(
            entry,
            source_path=source,
            storage_root=tmp_path / "private-storage",
            campaign_id="campaign-1",
            declared_mime=declared_mime,
            gm_authorized=True,
        )


def test_private_asset_import_rejects_campaign_traversal(tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"private-map-bytes"
    source = tmp_path / "owned.png"
    source.write_bytes(png)
    entry = replace(
        _bundle()[3].entries[0],
        expected_sha256=hashlib.sha256(png).hexdigest(),
    )

    with pytest.raises(RulesValidationError, match="campaign identity"):
        import_private_asset(
            entry,
            source_path=source,
            storage_root=tmp_path / "private-storage",
            campaign_id="../outside",
            declared_mime="image/png",
            gm_authorized=True,
        )


def test_player_asset_filter_hides_gm_records_by_default():
    player_manifest, player_pack, _, player_assets = _bundle(asset_spoiler="player")
    gm_manifest, gm_pack, _, gm_assets = _bundle(asset_spoiler="gm")

    assert visible_private_assets(
        player_assets,
        records={record.id: record for record in player_pack.records},
    ) == player_assets.entries
    assert visible_private_assets(
        gm_assets,
        records={record.id: record for record in gm_pack.records},
    ) == ()
    assert visible_private_assets(
        gm_assets,
        records={record.id: record for record in gm_pack.records},
        include_gm=True,
    ) == gm_assets.entries


def test_asset_manifest_serialization_contains_only_private_metadata():
    manifest, pack, index, assets = _bundle()
    payload = compile_adventure_bundle(_validate(manifest, pack, index, assets))
    lowered = payload.lower()

    assert b"local_path" not in lowered
    assert b"source_text" not in lowered
    assert b"binary_payload" not in lowered
    assert b"c:\\" not in lowered
    assert b"%pdf" not in lowered
    document = json.loads(payload)
    assert set(document["assets"]["entries"][0]) == {
        "id",
        "asset_type",
        "source_artifact_id",
        "expected_sha256",
        "source_page",
        "storage_ref",
    }
