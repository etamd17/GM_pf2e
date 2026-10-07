"""Independent coverage and drift reports must expose omissions and rekeys."""
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


def evidence():
    return importlib.import_module("systems.pf2e.rules.ingestion.evidence_inventory")


def documents():
    census = json.loads(
        (FIXTURES / "synthetic-aon-census.json").read_text(encoding="utf-8")
    )
    ledger = json.loads(
        (FIXTURES / "synthetic-evidence-ledger.json").read_text(encoding="utf-8")
    )
    return census, ledger


def snapshot(census, ledger):
    return evidence().audit_evidence_inventory(census, ledger)


def test_audit_proves_complete_independent_coverage_with_stable_counts():
    census, ledger = documents()

    report = snapshot(census, ledger)

    assert report["complete"] is True
    assert report["missing"] == []
    assert report["coverage"]["by_disposition"] == {
        "excluded": 1, "mapped": 1, "missing": 0, "pending": 1,
    }
    assert report["coverage"]["by_kind"] == {
        "class": {"excluded": 0, "mapped": 1, "missing": 0, "pending": 0,
                  "observed": 1},
        "feat": {"excluded": 0, "mapped": 0, "missing": 0, "pending": 1,
                 "observed": 1},
        "spell": {"excluded": 1, "mapped": 0, "missing": 0, "pending": 0,
                  "observed": 1},
    }
    assert report["coverage"]["by_rules_era"] == {
        "legacy": {"excluded": 0, "mapped": 0, "missing": 0, "pending": 1,
                   "observed": 1},
        "remaster": {"excluded": 1, "mapped": 1, "missing": 0, "pending": 0,
                     "observed": 2},
    }
    assert [record["identity_key"] for record in report["records"]] == [
        "Classes.aspx:7", "Feats.aspx:7", "Spells.aspx:11",
    ]


def test_v2_census_and_v1_ledger_produce_v2_audit_with_unverified_coverage():
    census, ledger = documents()
    census["schema_version"] = 2
    census["records"][0]["rules_era"] = "unverified"
    census["records"][0]["fingerprint"] = evidence().evidence_fingerprint(
        census["records"][0], schema_version=2
    )

    report = snapshot(census, ledger)

    assert ledger["schema_version"] == 1
    assert report["schema_version"] == 2
    assert report["coverage"]["by_rules_era"]["unverified"] == {
        "excluded": 0, "mapped": 1, "missing": 0, "pending": 0,
        "observed": 1,
    }


def test_diff_accepts_v1_to_v2_audit_transition_without_false_drift():
    census, ledger = documents()
    before = snapshot(census, ledger)
    census["schema_version"] = 2
    for record in census["records"]:
        record["fingerprint"] = evidence().evidence_fingerprint(
            record, schema_version=2
        )
    after = snapshot(census, ledger)

    result = evidence().diff_evidence_inventories(before, after)

    assert before["schema_version"] == 1
    assert after["schema_version"] == 2
    assert all(result[field] == [] for field in (
        "added", "removed", "evidence_changed", "newly_excluded", "restored",
        "exclusion_changed", "disposition_changed", "review_changed",
    ))


def test_v2_verified_era_change_is_evidence_drift():
    census, ledger = documents()
    census["schema_version"] = 2
    census["records"][0]["rules_era"] = "unverified"
    census["records"][0]["fingerprint"] = evidence().evidence_fingerprint(
        census["records"][0], schema_version=2
    )
    before = snapshot(census, ledger)
    census["records"][0]["rules_era"] = "remaster"
    census["records"][0]["fingerprint"] = evidence().evidence_fingerprint(
        census["records"][0], schema_version=2
    )
    after = snapshot(census, ledger)

    result = evidence().diff_evidence_inventories(before, after)

    assert result["evidence_changed"] == ["Classes.aspx:7"]


def test_missing_observed_identity_is_reported_instead_of_hidden():
    census, ledger = documents()
    ledger["entries"].pop(1)

    report = snapshot(census, ledger)

    assert report["complete"] is False
    assert report["missing"] == ["Feats.aspx:7"]
    assert report["coverage"]["by_disposition"]["missing"] == 1
    assert report["coverage"]["by_kind"]["feat"]["missing"] == 1


def test_stale_exclusion_is_rejected():
    census, ledger = documents()
    census["records"].pop()

    with pytest.raises(ValueError) as error:
        snapshot(census, ledger)

    assert error.value.code == "stale_exclusion"
    assert error.value.path == "$.ledger.entries[2]"


def test_census_binding_must_match_ledger():
    census, ledger = documents()
    ledger["census_captured_at"] = "2026-10-07T00:00:00Z"

    with pytest.raises(ValueError) as error:
        snapshot(census, ledger)

    assert error.value.code == "census_mismatch"
    assert error.value.path == "$.ledger.census_captured_at"


def _with_changed_fingerprint(census, index, **changes):
    census["records"][index].update(changes)
    census["records"][index]["fingerprint"] = evidence().evidence_fingerprint(
        census["records"][index]
    )


def _added_documents(census, ledger):
    record = deepcopy(census["records"][0])
    record.update(
        identity={"page_family": "Actions.aspx", "numeric_id": 3},
        canonical_url="https://2e.aonprd.com/Actions.aspx?ID=3",
        name="Added Example",
        kind="action",
        evidence_sha256="d" * 64,
    )
    record["fingerprint"] = evidence().evidence_fingerprint(record)
    census["records"].append(record)
    ledger["entries"].append({
        "identity": deepcopy(record["identity"]),
        "disposition": "pending",
        "rule_id": None,
        "reason": "Awaiting review.",
        "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
    })


def _removed_documents(census, ledger):
    census["records"] = census["records"][1:]
    ledger["entries"] = ledger["entries"][1:]


def _newly_excluded(_census, ledger):
    ledger["entries"][0].update(
        disposition="excluded", rule_id=None, reason="Temporarily excluded.",
    )


def _restored(_census, ledger):
    ledger["entries"][2].update(
        disposition="pending", reason="Review reopened.",
        review={"status": "pending", "reviewer": None, "reviewed_at": None},
    )


def _exclusion_changed(_census, ledger):
    ledger["entries"][2]["reason"] = "Different explicit exclusion."


@pytest.mark.parametrize(("mutate", "category", "identity"), [
    (_added_documents, "added", "Actions.aspx:3"),
    (_removed_documents, "removed", "Classes.aspx:7"),
    (lambda census, _ledger: _with_changed_fingerprint(census, 0, name="Renamed"),
     "evidence_changed", "Classes.aspx:7"),
    (_newly_excluded, "newly_excluded", "Classes.aspx:7"),
    (_restored, "restored", "Spells.aspx:11"),
    (_exclusion_changed, "exclusion_changed", "Spells.aspx:11"),
])
def test_each_primary_drift_is_reported_once(mutate, category, identity):
    before_census, before_ledger = documents()
    after_census, after_ledger = documents()
    mutate(after_census, after_ledger)

    report = evidence().diff_evidence_inventories(
        snapshot(before_census, before_ledger),
        snapshot(after_census, after_ledger),
    )

    categories = (
        "added", "removed", "evidence_changed", "newly_excluded", "restored",
        "exclusion_changed", "disposition_changed", "review_changed",
    )
    assert report[category] == [identity]
    assert sum(identity in report[name] for name in categories) == 1


def test_clean_diff_has_every_stable_category():
    census, ledger = documents()
    current = snapshot(census, ledger)

    assert evidence().diff_evidence_inventories(current, current) == {
        "before_census": "2026-10-06T00:00:00Z",
        "after_census": "2026-10-06T00:00:00Z",
        "added": [],
        "removed": [],
        "evidence_changed": [],
        "newly_excluded": [],
        "restored": [],
        "exclusion_changed": [],
        "disposition_changed": [],
        "review_changed": [],
    }


def test_stable_rule_id_cannot_move_to_another_external_identity():
    before_census, before_ledger = documents()
    after_census, after_ledger = documents()
    after_ledger["entries"][0].update(
        disposition="pending", rule_id=None, reason="Moved incorrectly.",
    )
    after_ledger["entries"][1].update(
        disposition="mapped", rule_id="pf2e.class.shared-example", reason=None,
    )

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(
            snapshot(before_census, before_ledger),
            snapshot(after_census, after_ledger),
        )

    assert error.value.code == "unstable_id"
    assert error.value.path == "$.after.records[Feats.aspx:7].rule_id"


def test_external_identity_cannot_change_stable_rule_id():
    before_census, before_ledger = documents()
    after_census, after_ledger = documents()
    after_ledger["entries"][0]["rule_id"] = "pf2e.class.different-example"

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(
            snapshot(before_census, before_ledger),
            snapshot(after_census, after_ledger),
        )

    assert error.value.code == "unstable_id"
    assert error.value.path == "$.after.records[Classes.aspx:7].rule_id"


def test_saved_report_rejects_duplicate_stable_rule_ids():
    census, ledger = documents()
    report = snapshot(census, ledger)
    report["records"][1].update(
        disposition="mapped",
        rule_id=report["records"][0]["rule_id"],
        reason=None,
    )

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(report, report)

    assert error.value.code == "duplicate_id"
    assert error.value.path == "$.before.records[1].rule_id"


def test_saved_report_rejects_forged_completeness_and_coverage():
    census, ledger = documents()
    ledger["entries"].pop(1)
    report = snapshot(census, ledger)
    report["complete"] = True
    report["missing"] = []

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(report, report)

    assert error.value.code == "invalid_value"
    assert error.value.path == "$.before.coverage.by_disposition.missing"


@pytest.mark.parametrize(("field", "value", "code", "path"), [
    ("schema_version", True, "invalid_type", "$.before.schema_version"),
    ("inventory_id", "not-an-id", "invalid_id", "$.before.inventory_id"),
])
def test_saved_report_revalidates_scalar_contracts(field, value, code, path):
    census, ledger = documents()
    report = snapshot(census, ledger)
    report[field] = value

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(report, report)

    assert error.value.code == code
    assert error.value.path == path


def test_diff_applies_bounds_before_deep_copy():
    value = {}
    cursor = value
    for _ in range(1500):
        child = {}
        cursor["nested"] = child
        cursor = child

    with pytest.raises(ValueError) as error:
        evidence().diff_evidence_inventories(value, value)

    assert error.value.code == "limit_exceeded"
    assert error.value.path == "$"


def test_unstable_id_error_is_hash_seed_independent():
    script = """
import json
from pathlib import Path
from systems.pf2e.rules.ingestion.evidence_inventory import audit_evidence_inventory, diff_evidence_inventories
root = Path('tests/pf2e_rules/fixtures')
census = json.loads((root / 'synthetic-aon-census.json').read_text(encoding='utf-8'))
ledger = json.loads((root / 'synthetic-evidence-ledger.json').read_text(encoding='utf-8'))
ids = ['pf2e.class.alpha', 'pf2e.feat.beta', 'pf2e.spell.gamma']
for entry, rule_id in zip(ledger['entries'], ids):
    entry.update(disposition='mapped', rule_id=rule_id, reason=None)
before = audit_evidence_inventory(census, ledger)
for entry, rule_id in zip(ledger['entries'], ids[1:] + ids[:1]):
    entry['rule_id'] = rule_id
after = audit_evidence_inventory(census, ledger)
try:
    diff_evidence_inventories(before, after)
except ValueError as error:
    print(error.path)
"""
    outputs = []
    for seed in ("1", "2", "417"):
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True,
            text=True, encoding="utf-8", check=True,
        )
        outputs.append(result.stdout)

    assert outputs == ["$.after.records[Spells.aspx:11].rule_id\n"] * 3
