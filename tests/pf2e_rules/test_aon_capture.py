"""Fail-closed tests for the developer-only AoN metadata capture compiler."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "pf2e_rules" / "fixtures" / "aon-capture"
INDEX = "aon-20261007-063536"


def capture():
    return importlib.import_module("tools.pf2e_aon_capture")


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_queries_pin_endpoint_fields_predicates_and_partitions():
    module = capture()

    scope = module.build_scope_query()
    census = module.build_category_query("class", "census")
    ledger = module.build_category_query("class", "ledger")
    equipment = module.build_category_query("equipment", "census")

    assert module.SEARCH_ENDPOINT == "https://elasticsearch.aonprd.com/aon/_search"
    assert scope["size"] == 0
    assert scope["_source"] is False
    assert scope["track_total_hits"] is True
    assert scope["aggs"]["categories"] == {
        "terms": {"field": "category", "size": 1000}
    }
    assert scope["aggs"]["indices"] == {
        "terms": {"field": "_index", "size": 10}
    }
    assert scope["aggs"]["equipment"]["aggs"]["children"] == {
        "filter": {"exists": {"field": "item_parent_id"}}
    }
    assert scope["aggs"]["weapon"]["aggs"]["melee_projections"] == {
        "filter": {"wildcard": {"id.keyword": "*--melee"}}
    }
    for query in (scope, census, ledger, equipment):
        assert {json.dumps(item, sort_keys=True)
                for item in query["query"]["bool"]["must_not"]} >= {
            json.dumps({"term": {"exclude_from_search": True}}, sort_keys=True),
            json.dumps({"exists": {"field": "remaster_id"}}, sort_keys=True),
        }
    assert census["_source"] == list(module.CENSUS_SOURCE_FIELDS)
    assert ledger["_source"] == list(module.LEDGER_SOURCE_FIELDS)
    assert census["_source"] != ledger["_source"]
    assert {"term": {"category": "class"}} in census["query"]["bool"]["filter"]
    assert {"exists": {"field": "item_parent_id"}} in (
        equipment["query"]["bool"]["must_not"]
    )


def test_v1_query_contract_has_literal_golden_hashes():
    module = capture()
    queries = {
        "scope": module.build_scope_query_v1(),
        "class-census": module.build_category_query_v1("class", "census"),
        "class-ledger": module.build_category_query_v1("class", "ledger"),
        "equipment-census": module.build_category_query_v1(
            "equipment", "census"
        ),
        "weapon-census": module.build_category_query_v1("weapon", "census"),
    }

    assert module.QUERY_CONTRACT_VERSION == 1
    with pytest.raises(TypeError):
        module.CATEGORY_SPECS_V1["invented-category"] = (
            "rule", frozenset({"Invented.aspx"})
        )
    assert {
        name: hashlib.sha256(module.canonical_query_bytes(query)).hexdigest()
        for name, query in queries.items()
    } == {
        "scope": "71eef82695b7a3047436b19e09f7111d6caf6032faec38dfa8162d1fe806193d",
        "class-census": "5e5aeb2715592073ceca406d68cc2dbe8ce3026afee201a4de280036812e3eba",
        "class-ledger": "4eda1c50ef4348e38cf25b5ce539c56dc730bd3f9b56895f7f7b66bac8a89928",
        "equipment-census": "a80556e0e90cd85cbf547951a1a49d30ea26b5dda96a587fb44738ac88dbfe8c",
        "weapon-census": "18037137e1a0f2c8d32b7dbf46181653b90c66dc541a0470d75bd8f3ab2edc15",
    }

    complete_query_set = [
        {
            "category": category,
            "mode": mode,
            "sha256": hashlib.sha256(module.canonical_query_bytes(
                module.build_category_query_v1(category, mode)
            )).hexdigest(),
        }
        for category in sorted(module.CATEGORY_SPECS_V1)
        for mode in ("census", "ledger")
    ]
    assert len(complete_query_set) == 132
    assert hashlib.sha256(module.canonical_json(complete_query_set)).hexdigest() == (
        "2dd00f9ecee6b0a1c9eafd66accfc6ce49ff0763b7dc6ab1a0d7e627043089e9"
    )


def test_unknown_category_or_mode_cannot_expand_the_query_surface():
    with pytest.raises(ValueError) as category_error:
        capture().build_category_query("invented-category", "census")
    assert category_error.value.code == "invalid_value"
    assert category_error.value.path == "$.category"

    with pytest.raises(ValueError) as mode_error:
        capture().build_category_query("class", "combined")
    assert mode_error.value.code == "invalid_value"
    assert mode_error.value.path == "$.mode"

    with pytest.raises(ValueError) as deferred_error:
        capture().build_category_query("siege-weapon", "census")
    assert deferred_error.value.code == "invalid_value"
    assert deferred_error.value.path == "$.category"


def test_scope_observation_binds_exact_index_total_and_complete_facets():
    observed = capture().compile_scope_observation(load("scope-response.json"))

    assert observed == {
        "resolved_index": INDEX,
        "reported_records": 5,
        "returned_records": 5,
        "categories": [
            {"name": "class", "observed_records": 2},
            {"name": "creature", "observed_records": 1},
            {"name": "equipment", "observed_records": 2},
        ],
        "partitions": {
            "equipment": {"included_records": 1, "excluded_records": 1},
            "weapon": {"included_records": 0, "excluded_records": 0},
        },
    }


def _complete_observation(module) -> dict:
    categories = [
        {"name": name, "observed_records": 1}
        for name in sorted(module.REVIEWED_CATEGORIES)
    ]
    by_name = {item["name"]: item for item in categories}
    by_name["equipment"]["observed_records"] = 3
    by_name["weapon"]["observed_records"] = 2
    return {
        "resolved_index": INDEX,
        "reported_records": len(categories) + 3,
        "returned_records": len(categories) + 3,
        "categories": categories,
        "partitions": {
            "equipment": {"included_records": 2, "excluded_records": 1},
            "weapon": {"included_records": 1, "excluded_records": 1},
        },
    }


def test_scope_policy_accounts_for_every_reviewed_category_and_partition():
    module = capture()
    observation = _complete_observation(module)

    policy = module.build_scope_policy(
        observation, scope_id="pf2e-player-build-current-v1",
        site_update_date="2026-10-07",
    )

    assert policy["total_records"] == observation["reported_records"]
    assert [item["name"] for item in policy["categories"]] == sorted(
        module.REVIEWED_CATEGORIES
    )
    by_name = {item["name"]: item for item in policy["categories"]}
    assert by_name["class"]["included_records"] == 1
    assert by_name["animal-companion"]["deferred_records"] == 1
    assert by_name["siege-weapon"]["deferred_records"] == 1
    assert by_name["class-feature"]["excluded_records"] == 1
    assert by_name["equipment"] == {
        "name": "equipment", "observed_records": 3, "included_records": 2,
        "deferred_records": 0, "excluded_records": 1,
        "reason": "Canonical equipment pages are included; embedded child variants are excluded.",
    }
    assert by_name["weapon"]["included_records"] == 1
    assert by_name["weapon"]["excluded_records"] == 1


def test_scope_policy_stops_for_new_or_missing_upstream_category():
    module = capture()
    observation = _complete_observation(module)
    observation["categories"].append({"name": "new-upstream-kind", "observed_records": 1})
    observation["reported_records"] += 1
    observation["returned_records"] += 1

    with pytest.raises(ValueError) as unknown:
        module.build_scope_policy(
            observation, scope_id="pf2e-player-build-current-v1",
            site_update_date="2026-10-07",
        )
    assert unknown.value.code == "scope_mismatch"
    assert unknown.value.path == "$.categories"

    observation = _complete_observation(module)
    observation["categories"].pop()
    observation["reported_records"] -= 1
    observation["returned_records"] -= 1
    with pytest.raises(ValueError) as missing:
        module.build_scope_policy(
            observation, scope_id="pf2e-player-build-current-v1",
            site_update_date="2026-10-07",
        )
    assert missing.value.code == "scope_mismatch"
    assert missing.value.path == "$.categories"


def test_census_compiler_is_deterministic_metadata_only_and_conservative():
    module = capture()
    response = load("class-census-response.json")

    first = module.compile_census_shard(
        response, category="class", kind="class", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )
    response["hits"]["hits"].reverse()
    second = module.compile_census_shard(
        response, category="class", kind="class", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )

    assert first == second
    assert first["schema_version"] == 2
    assert [record["name"] for record in first["records"]] == [
        "Gunslinger", "Synthetic Unlinked Class",
    ]
    assert [record["rules_era"] for record in first["records"]] == [
        "remaster", "unverified",
    ]
    assert first["records"][0]["identity"] == {
        "page_family": "Classes.aspx", "numeric_id": 20,
    }
    assert first["records"][0]["canonical_url"] == (
        "https://2e.aonprd.com/Classes.aspx?ID=20"
    )
    assert first["records"][0]["source_refs"] == [{
        "title": "Guns & Gears (Remastered)",
        "locator": "Guns & Gears (Remastered) pg. 105",
    }]
    assert set(first["records"][0]) == {
        "identity", "canonical_url", "name", "kind", "rules_era",
        "source_refs", "evidence_sha256", "fingerprint",
    }
    assert module.compile_census_shard(
        load("class-census-response.json"), category="class", kind="class",
        expected_index=INDEX, captured_at="2026-10-07T12:00:00Z",
        site_update_date="2026-10-07",
    ) == first

    response = load("class-census-response.json")
    response["hits"]["hits"][1]["_source"]["release_date"] = "2000-01-01"
    older = module.compile_census_shard(
        response, category="class", kind="class", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )
    assert older["records"][1]["rules_era"] == "unverified"


def test_census_normalizes_upstream_crlf_and_duplicate_source_pairs():
    response = load("class-census-response.json")
    source = response["hits"]["hits"][0]["_source"]
    source["primary_source"] = "Guns & Gears\r\n(Remastered)"
    source["primary_source_raw"] = "Guns & Gears\r\n(Remastered) pg. 105"
    source["source"] = [source["primary_source"], source["primary_source"]]
    source["source_raw"] = [
        source["primary_source_raw"], source["primary_source_raw"],
    ]

    document = capture().compile_census_shard(
        response, category="class", kind="class", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )

    assert document["records"][0]["source_refs"] == [{
        "title": "Guns & Gears (Remastered)",
        "locator": "Guns & Gears (Remastered) pg. 105",
    }]

    response = load("class-census-response.json")
    source = response["hits"]["hits"][0]["_source"]
    source["primary_source"] = source["source"][0] = "Pathfinder #217\r\n"
    source["primary_source_raw"] = source["source_raw"][0] = (
        "Pathfinder #217 pg. 12\r\n"
    )
    terminal = capture().compile_census_shard(
        response, category="class", kind="class", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )
    assert terminal["records"][0]["source_refs"] == [{
        "title": "Pathfinder #217",
        "locator": "Pathfinder #217 pg. 12",
    }]


def test_census_uses_explicit_placeholder_for_upstream_unnamed_action():
    response = load("class-census-response.json")
    response["hits"]["total"]["value"] = 1
    response["hits"]["hits"] = [response["hits"]["hits"][0]]
    hit = response["hits"]["hits"][0]
    hit["_id"] = "action-3841"
    hit["_source"].update(
        id="action-3841", category="action", type="Action", name="",
        url="/Actions.aspx?ID=3841",
    )

    document = capture().compile_census_shard(
        response, category="action", kind="action", expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )

    assert document["records"][0]["name"] == "Unnamed action (Actions.aspx:3841)"


@pytest.mark.parametrize(("category", "kind", "page_family"), [
    ("spell", "spell", "MythicSpells.aspx"),
    ("ritual", "ritual", "MythicRituals.aspx"),
])
def test_census_allows_reviewed_mythic_page_families(category, kind, page_family):
    response = load("class-census-response.json")
    response["hits"]["total"]["value"] = 1
    response["hits"]["hits"] = [response["hits"]["hits"][0]]
    hit = response["hits"]["hits"][0]
    hit["_id"] = f"{category}-20"
    hit["_source"].update(
        id=f"{category}-20", category=category, type=kind.title(),
        name="Synthetic Mythic Example", url=f"/{page_family}?ID=20",
    )

    document = capture().compile_census_shard(
        response, category=category, kind=kind, expected_index=INDEX,
        captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
    )

    assert document["records"][0]["identity"]["page_family"] == page_family


def test_ledger_compiler_uses_identity_only_response_and_all_pending_rows():
    document = capture().compile_ledger_shard(
        load("class-ledger-response.json"), category="class",
        expected_index=INDEX, inventory_id="pf2e-aon-2026-10-07-class",
        census_captured_at="2026-10-07T12:00:00Z",
        created_at="2026-10-07T13:00:00Z",
    )

    assert document["schema_version"] == 1
    assert [entry["identity"]["numeric_id"] for entry in document["entries"]] == [20, 21]
    assert all(entry == {
        "identity": entry["identity"],
        "disposition": "pending",
        "rule_id": None,
        "reason": "Awaiting Paizo source, rules, and license review.",
        "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
    } for entry in document["entries"])

    with pytest.raises(ValueError) as error:
        capture().compile_ledger_shard(
            load("class-census-response.json"), category="class",
            expected_index=INDEX, inventory_id="pf2e-aon-2026-10-07-class",
            census_captured_at="2026-10-07T12:00:00Z",
            created_at="2026-10-07T13:00:00Z",
        )
    assert error.value.code == "unknown_field"
    assert error.value.path == "$.hits.hits[0]._source"


@pytest.mark.parametrize(("mutate", "code", "path"), [
    (lambda response: response["hits"]["total"].update(value=10_000),
     "limit_exceeded", "$.hits.total.value"),
    (lambda response: response["hits"]["total"].update(relation="gte"),
     "partial_results", "$.hits.total.relation"),
    (lambda response: response["hits"]["hits"].pop(),
     "count_mismatch", "$.hits.hits"),
    (lambda response: response["hits"]["hits"][0].update(_index="aon-20260902-000000"),
     "capture_mismatch", "$.hits.hits[0]._index"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        url="/Classes.aspx?ID=20&Type=Advancement"),
     "invalid_url", "$.hits.hits[0]._source.url"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        markdown="copied rules prose"),
     "unknown_field", "$.hits.hits[0]._source"),
    (lambda response: response["hits"]["hits"][0]["_source"]["source_raw"].clear(),
     "source_mismatch", "$.hits.hits[0]._source.source_raw"),
    (lambda response: response["hits"]["hits"][1]["_source"].update(
        url="/Classes.aspx?ID=20"),
     "duplicate_identity", "$.hits.hits[1]._source.url"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        exclude_from_search=True),
     "predicate_mismatch", "$.hits.hits[0]._source.exclude_from_search"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        remaster_id=["class-999"]),
     "predicate_mismatch", "$.hits.hits[0]._source.remaster_id"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(legacy_id=[]),
     "invalid_value", "$.hits.hits[0]._source.legacy_id"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        primary_source_raw="Different Source pg. 1"),
     "source_mismatch", "$.hits.hits[0]._source.primary_source_raw"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        url="https://2e.aonprd.com/Classes.aspx?ID=20"),
     "invalid_url", "$.hits.hits[0]._source.url"),
    (lambda response: response["hits"]["hits"][0]["_source"].update(
        url="/Feats.aspx?ID=20"),
     "capture_mismatch", "$.hits.hits[0]._source.url"),
    (lambda response: response["hits"]["hits"][0].update(_id="class-999"),
     "capture_mismatch", "$.hits.hits[0]._id"),
    (lambda response: response.update(timed_out=True),
     "partial_results", "$.timed_out"),
    (lambda response: response["_shards"].update(failed=1),
     "partial_results", "$._shards.failed"),
    (lambda response: response["_shards"].update(successful=0),
     "partial_results", "$._shards.successful"),
    (lambda response: response.update(terminated_early=True),
     "partial_results", "$.terminated_early"),
])
def test_census_refuses_partial_inconsistent_or_prose_bearing_results(
        mutate, code, path):
    response = load("class-census-response.json")
    mutate(response)

    with pytest.raises(ValueError) as error:
        capture().compile_census_shard(
            response, category="class", kind="class", expected_index=INDEX,
            captured_at="2026-10-07T12:00:00Z", site_update_date="2026-10-07",
        )

    assert error.value.code == code
    assert error.value.path == path


def test_scope_refuses_incomplete_aggregation_and_unallowlisted_index():
    response = load("scope-response.json")
    response["aggregations"]["categories"]["sum_other_doc_count"] = 1
    with pytest.raises(ValueError) as partial:
        capture().compile_scope_observation(response)
    assert partial.value.code == "partial_results"
    assert partial.value.path == "$.aggregations.categories.sum_other_doc_count"

    response = load("scope-response.json")
    response["aggregations"]["indices"]["buckets"][0]["key"] = "private-index"
    with pytest.raises(ValueError) as index:
        capture().compile_scope_observation(response)
    assert index.value.code == "invalid_value"
    assert index.value.path == "$.aggregations.indices.buckets[0].key"


@pytest.mark.parametrize(("mutate", "path"), [
    (lambda response: response["aggregations"]["categories"].update(
        sum_other_doc_count=False
    ), "$.aggregations.categories.sum_other_doc_count"),
    (lambda response: response["aggregations"]["categories"].update(
        doc_count_error_upper_bound=0.0
    ), "$.aggregations.categories.doc_count_error_upper_bound"),
    (lambda response: response["aggregations"]["indices"].update(
        sum_other_doc_count=False
    ), "$.aggregations.indices.sum_other_doc_count"),
    (lambda response: response["aggregations"]["indices"].update(
        doc_count_error_upper_bound=0.0
    ), "$.aggregations.indices.doc_count_error_upper_bound"),
    (lambda response: response["aggregations"]["indices"]["buckets"][0].update(
        doc_count=5.0
    ), "$.aggregations.indices.buckets[0].doc_count"),
])
def test_scope_refuses_noninteger_aggregation_counters(mutate, path):
    response = load("scope-response.json")
    mutate(response)

    with pytest.raises(ValueError) as error:
        capture().compile_scope_observation(response)

    assert error.value.code == "invalid_type"
    assert error.value.path == path


def test_search_client_uses_fixed_endpoint_headers_payload_and_bounds():
    module = capture()
    calls = []
    raw = (FIXTURES / "scope-response.json").read_bytes()

    def transport(url, payload, headers, timeout, max_bytes):
        calls.append((url, payload, headers, timeout, max_bytes))
        return raw

    client = module.SearchClient(transport=transport)
    response, response_bytes = client.search(module.build_scope_query())

    assert response_bytes == raw
    assert response["hits"]["total"]["value"] == 5
    assert len(calls) == 1
    url, payload, headers, timeout, max_bytes = calls[0]
    assert url == module.SEARCH_ENDPOINT
    assert payload == module.canonical_query_bytes(module.build_scope_query())
    assert headers == {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "User-Agent": module.USER_AGENT,
    }
    assert timeout == module.REQUEST_TIMEOUT_SECONDS
    assert max_bytes == module.MAX_RESPONSE_BYTES


@pytest.mark.parametrize(("payload", "code"), [
    (b'{"hits":{},"hits":{}}', "invalid_json"),
    (b'{"value":NaN}', "invalid_json"),
    (b"\xff", "invalid_json"),
])
def test_search_client_rejects_noncanonical_json(payload, code):
    client = capture().SearchClient(
        transport=lambda *_args: payload,
    )

    with pytest.raises(ValueError) as error:
        client.search(capture().build_scope_query())

    assert error.value.code == code
    assert error.value.path == "$network.response"


def test_search_client_rejects_oversized_response():
    module = capture()
    client = module.SearchClient(
        transport=lambda *_args: b" " * (module.MAX_RESPONSE_BYTES + 1),
    )

    with pytest.raises(ValueError) as error:
        client.search(module.build_scope_query())

    assert error.value.code == "limit_exceeded"
    assert error.value.path == "$network.response"


def test_search_client_retries_only_allowlisted_transient_statuses():
    module = capture()
    raw = (FIXTURES / "scope-response.json").read_bytes()
    attempts = []

    def transient(*_args):
        attempts.append(1)
        if len(attempts) < 3:
            raise HTTPError(module.SEARCH_ENDPOINT, 503, "busy", {}, None)
        return raw

    response, _ = module.SearchClient(
        transport=transient, sleep=lambda _seconds: None,
    ).search(module.build_scope_query())
    assert response["hits"]["total"]["value"] == 5
    assert len(attempts) == 3

    def permanent(*_args):
        raise HTTPError(module.SEARCH_ENDPOINT, 400, "bad request", {}, None)

    with pytest.raises(ValueError) as error:
        module.SearchClient(
            transport=permanent, sleep=lambda _seconds: None,
        ).search(module.build_scope_query())
    assert error.value.code == "http_error"
    assert error.value.path == "$network"


def test_search_client_paces_consecutive_requests():
    module = capture()
    raw = (FIXTURES / "scope-response.json").read_bytes()
    times = iter((0.0, 0.1, 0.6))
    waits = []
    client = module.SearchClient(
        transport=lambda *_args: raw,
        sleep=waits.append,
        monotonic=lambda: next(times),
    )

    client.search(module.build_scope_query())
    client.search(module.build_scope_query())

    assert waits == [pytest.approx(module.MIN_REQUEST_INTERVAL_SECONDS - 0.1)]


def test_scope_capture_requires_exact_preapproved_concrete_index(tmp_path):
    module = capture()
    raw = (FIXTURES / "scope-response.json").read_bytes()

    with pytest.raises(ValueError) as error:
        module.capture_scope(
            client=module.SearchClient(transport=lambda *_args: raw),
            scope_id="pf2e-player-build-current-v1",
            site_update_date="2026-10-07",
            expected_index="aon-20260902-000000",
            run_id="aon-scope-test",
            captured_at="2026-10-07T11:00:00Z",
            output_root=tmp_path / "output",
        )

    assert error.value.code == "capture_mismatch"
    assert error.value.path == "$.resolved_index"
    assert not (tmp_path / "output" / "scope-receipt.json").exists()


def test_scope_capture_validates_preapproved_index_before_network(tmp_path):
    module = capture()
    called = False

    class RefusingClient:
        def search(self, _query):
            nonlocal called
            called = True
            raise AssertionError("network must not run")

    with pytest.raises(ValueError) as error:
        module.capture_scope(
            client=RefusingClient(),
            scope_id="pf2e-player-build-current-v1",
            site_update_date="2026-10-07",
            expected_index="aon-latest",
            run_id="aon-scope-test",
            captured_at="2026-10-07T11:00:00Z",
            output_root=tmp_path / "output",
        )

    assert error.value.code == "invalid_id"
    assert error.value.path == "$.expected_index"
    assert called is False


def _class_policy(path: Path) -> Path:
    value = {
        "schema_version": 1,
        "scope_id": "pf2e-player-build-current-v1",
        "authority": "archives-of-nethys",
        "resolved_index": INDEX,
        "site_update_date": "2026-10-07",
        "total_records": 2,
        "categories": [{
            "name": "class",
            "observed_records": 2,
            "included_records": 2,
            "deferred_records": 0,
            "excluded_records": 0,
            "reason": "Synthetic focused policy.",
        }],
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_mode_capture_writes_canonical_shards_and_authoritative_receipt_last(tmp_path):
    module = capture()
    policy_path = _class_policy(tmp_path / "scope-policy.json")
    census_raw = (FIXTURES / "class-census-response.json").read_bytes()
    ledger_raw = (FIXTURES / "class-ledger-response.json").read_bytes()

    census_result = module.capture_categories(
        client=module.SearchClient(transport=lambda *_args: census_raw),
        mode="census",
        policy_path=policy_path,
        run_id="aon-census-test",
        captured_at="2026-10-07T12:00:00Z",
        output_root=tmp_path / "census-output",
    )
    ledger_result = module.capture_categories(
        client=module.SearchClient(transport=lambda *_args: ledger_raw),
        mode="ledger",
        policy_path=policy_path,
        run_id="aon-ledger-test",
        captured_at="2026-10-07T13:00:00Z",
        output_root=tmp_path / "ledger-output",
        census_captured_at="2026-10-07T12:00:00Z",
        snapshot_id="pf2e-aon-2026-10-07-player-build-v1",
    )

    assert census_result["records"] == ledger_result["records"] == 2
    census_path = tmp_path / "census-output" / "census" / "class.json"
    census = json.loads(census_path.read_text(encoding="utf-8"))
    assert census_path.read_bytes().endswith(b"\n")
    assert all("markdown" not in record for record in census["records"])
    ledger = json.loads(
        (tmp_path / "ledger-output" / "ledger" / "class.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(entry["disposition"] == "pending" for entry in ledger["entries"])
    census_receipt = json.loads(
        (tmp_path / "census-output" / "census-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    ledger_receipt = json.loads(
        (tmp_path / "ledger-output" / "ledger-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert census_receipt["artifacts"][0]["response_sha256"] != (
        ledger_receipt["artifacts"][0]["response_sha256"]
    )
    assert census_receipt["artifacts"][0]["query_sha256"] != (
        ledger_receipt["artifacts"][0]["query_sha256"]
    )


def test_mode_capture_refuses_existing_outputs_before_network(tmp_path):
    module = capture()
    policy_path = _class_policy(tmp_path / "scope-policy.json")
    output = tmp_path / "output"
    (output / "census").mkdir(parents=True)
    (output / "census" / "class.json").write_text("existing", encoding="utf-8")
    called = False

    def transport(*_args):
        nonlocal called
        called = True
        raise AssertionError("network must not run")

    with pytest.raises(ValueError) as error:
        module.capture_categories(
            client=module.SearchClient(transport=transport),
            mode="census",
            policy_path=policy_path,
            run_id="aon-census-test",
            captured_at="2026-10-07T12:00:00Z",
            output_root=output,
        )

    assert error.value.code == "output_exists"
    assert called is False


def test_scope_capture_refuses_file_as_output_root_before_network(tmp_path):
    module = capture()
    output = tmp_path / "output"
    output.write_text("not a directory", encoding="utf-8")
    called = False

    class RefusingClient:
        def search(self, _query):
            nonlocal called
            called = True
            raise AssertionError("network must not run")

    with pytest.raises(ValueError) as error:
        module.capture_scope(
            client=RefusingClient(),
            scope_id="pf2e-player-build-current-v1",
            site_update_date="2026-10-07",
            expected_index=INDEX,
            run_id="aon-scope-test",
            captured_at="2026-10-07T11:00:00Z",
            output_root=output,
        )

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$output"
    assert called is False


def test_mode_capture_refuses_file_as_shard_parent_before_network(tmp_path):
    module = capture()
    policy_path = _class_policy(tmp_path / "scope-policy.json")
    output = tmp_path / "output"
    output.mkdir()
    (output / "census").write_text("not a directory", encoding="utf-8")
    called = False

    class RefusingClient:
        def search(self, _query):
            nonlocal called
            called = True
            raise AssertionError("network must not run")

    with pytest.raises(ValueError) as error:
        module.capture_categories(
            client=RefusingClient(),
            mode="census",
            policy_path=policy_path,
            run_id="aon-census-test",
            captured_at="2026-10-07T12:00:00Z",
            output_root=output,
        )

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$output"
    assert called is False


def test_mode_capture_reports_malformed_response_without_keyerror(tmp_path):
    module = capture()
    policy_path = _class_policy(tmp_path / "scope-policy.json")

    class MalformedClient:
        def search(self, _query):
            return {}, b"{}"

    with pytest.raises(ValueError) as error:
        module.capture_categories(
            client=MalformedClient(),
            mode="census",
            policy_path=policy_path,
            run_id="aon-census-test",
            captured_at="2026-10-07T12:00:00Z",
            output_root=tmp_path / "output",
        )

    assert error.value.code == "partial_results"
    assert error.value.path == "$.timed_out"
    assert not (tmp_path / "output" / "census-receipt.json").exists()


def test_mode_capture_validates_supplied_identity_before_network(tmp_path):
    module = capture()
    policy_path = _class_policy(tmp_path / "scope-policy.json")
    called = False

    class RefusingClient:
        def search(self, _query):
            nonlocal called
            called = True
            raise AssertionError("network must not run")

    with pytest.raises(ValueError) as error:
        module.capture_categories(
            client=RefusingClient(),
            mode="census",
            policy_path=policy_path,
            run_id="INVALID RUN",
            captured_at="2026-10-07T12:00:00Z",
            output_root=tmp_path / "output",
        )

    assert error.value.code == "invalid_id"
    assert error.value.path == "$.run_id"
    assert called is False


def test_ledger_cli_has_no_census_artifact_argument():
    module = capture()
    with pytest.raises(SystemExit) as error:
        module.build_parser().parse_args([
            "ledger",
            "--scope-policy", "scope-policy.json",
            "--run-id", "aon-ledger-test",
            "--captured-at", "2026-10-07T13:00:00Z",
            "--output-root", "output",
            "--census-captured-at", "2026-10-07T12:00:00Z",
            "--snapshot-id", "pf2e-aon-2026-10-07-player-build-v1",
            "--census", "census/class.json",
        ])
    assert error.value.code == 2
