"""Strict, offline contracts for independent AoN evidence and review ledgers."""
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "pf2e_rules" / "fixtures"
CENSUS_PATH = FIXTURES / "synthetic-aon-census.json"
LEDGER_PATH = FIXTURES / "synthetic-evidence-ledger.json"


def evidence():
    return importlib.import_module("systems.pf2e.rules.ingestion.evidence_inventory")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_census_normalizes_scoped_ids_redirects_and_order_without_mutating():
    document = load(CENSUS_PATH)
    original = deepcopy(document)
    document["records"].reverse()
    document["records"][0]["source_refs"].reverse()
    before_call = deepcopy(document)

    normalized = evidence().normalize_aon_census(document)

    assert document == before_call
    assert original["records"][0]["canonical_url"].endswith("&Redirected=1")
    assert normalized["records"][0]["identity"] == {
        "page_family": "Classes.aspx", "numeric_id": 7,
    }
    assert normalized["records"][0]["canonical_url"] == (
        "https://2e.aonprd.com/Classes.aspx?ID=7"
    )
    assert document["records"] != normalized["records"]


def test_same_numeric_id_and_display_name_are_distinct_across_page_families():
    normalized = evidence().normalize_aon_census(load(CENSUS_PATH))
    shared = [record for record in normalized["records"] if record["name"] == "Shared Example"]

    assert [(item["identity"]["page_family"], item["identity"]["numeric_id"])
            for item in shared] == [("Classes.aspx", 7), ("Feats.aspx", 7)]


def test_census_rejects_duplicate_external_identity():
    document = load(CENSUS_PATH)
    document["records"].append(deepcopy(document["records"][0]))

    with pytest.raises(ValueError) as error:
        evidence().normalize_aon_census(document)

    assert error.value.code == "duplicate_identity"
    assert error.value.path == "$.records[3].identity"


@pytest.mark.parametrize(("mutation", "code", "path"), [
    (lambda record: record.update(description="copied rules prose"),
     "unknown_field", "$.records[0]"),
    (lambda record: record["identity"].update(numeric_id=True),
     "invalid_type", "$.records[0].identity.numeric_id"),
    (lambda record: record.update(canonical_url="http://2e.aonprd.com/Classes.aspx?ID=7"),
     "invalid_url", "$.records[0].canonical_url"),
    (lambda record: record.update(canonical_url="https://example.com/Classes.aspx?ID=7"),
     "invalid_url", "$.records[0].canonical_url"),
    (lambda record: record.update(fingerprint="A" * 64),
     "invalid_value", "$.records[0].fingerprint"),
])
def test_census_rejects_malformed_or_prose_fields(mutation, code, path):
    document = load(CENSUS_PATH)
    mutation(document["records"][0])

    with pytest.raises(ValueError) as error:
        evidence().normalize_aon_census(document)

    assert error.value.code == code
    assert error.value.path == path


def test_claimed_fingerprint_is_pinned_and_tampering_fails():
    document = load(CENSUS_PATH)
    first = document["records"][0]

    assert evidence().evidence_fingerprint(first) == (
        "0b1b31cb6a4958d41b7c4020fd7f62df55152ba36a5875b46ab8a266a7d99638"
    )
    first["name"] = "Changed"

    with pytest.raises(ValueError) as error:
        evidence().normalize_aon_census(document)

    assert error.value.code == "fingerprint_mismatch"
    assert error.value.path == "$.records[0].fingerprint"


def test_ledger_normalizes_dispositions_reviews_and_order_without_mutating():
    document = load(LEDGER_PATH)
    original = deepcopy(document)
    document["entries"].reverse()
    before_call = deepcopy(document)

    normalized = evidence().normalize_evidence_ledger(document)

    assert document == before_call
    assert [entry["disposition"] for entry in normalized["entries"]] == [
        "mapped", "pending", "excluded",
    ]
    assert normalized["entries"][0]["rule_id"] == "pf2e.class.shared-example"
    assert document["entries"] != normalized["entries"]


@pytest.mark.parametrize(("mutate", "code", "path"), [
    (lambda entries: entries[1].update(disposition="mapped",
                                       rule_id="pf2e.class.shared-example", reason=None),
     "duplicate_id", "$.entries[1].rule_id"),
    (lambda entries: entries[0].update(reason="Not allowed for mapped"),
     "invalid_disposition", "$.entries[0]"),
    (lambda entries: entries[1].update(reason=None),
     "invalid_disposition", "$.entries[1]"),
    (lambda entries: entries[2]["review"].update(reviewer=None),
     "invalid_review", "$.entries[2].review"),
])
def test_ledger_rejects_conflicting_dispositions_and_reviews(mutate, code, path):
    document = load(LEDGER_PATH)
    mutate(document["entries"])

    with pytest.raises(ValueError) as error:
        evidence().normalize_evidence_ledger(document)

    assert error.value.code == code
    assert error.value.path == path


def test_ledger_rejects_overlong_mapped_rule_id():
    document = load(LEDGER_PATH)
    document["entries"][0]["rule_id"] = "pf2e.class." + "x" * 4097

    with pytest.raises(ValueError) as error:
        evidence().normalize_evidence_ledger(document)

    assert error.value.code == "invalid_disposition"
    assert error.value.path == "$.entries[0]"


def test_normalization_is_hash_seed_independent():
    script = """
import json
from pathlib import Path
from systems.pf2e.rules.ingestion.evidence_inventory import normalize_aon_census
value = json.loads(Path('tests/pf2e_rules/fixtures/synthetic-aon-census.json').read_text(encoding='utf-8'))
print(json.dumps(normalize_aon_census(value), sort_keys=True, ensure_ascii=False, separators=(',', ':')))
"""
    outputs = []
    for seed in ("1", "417"):
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
            text=True, encoding="utf-8", check=True,
        )
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
