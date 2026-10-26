"""Validate private source-review evidence without changing runtime rule behavior."""
from collections import Counter
from copy import deepcopy
import hashlib
from .mistborn_authoring import MistbornAuthoringError, canonical_bytes

OUTCOMES = frozenset({'verified', 'corrected and verified', 'removed', 'still blocked'})


def outcomes_hash(rows):
    return hashlib.sha256(canonical_bytes(rows)).hexdigest()


def _require(condition, message):
    if not condition:
        raise MistbornAuthoringError(message)


def _records(bundle):
    return {r['id']: (p['id'], r) for p in bundle['packages'] for r in p['records']}


def _mechanics(record):
    result = deepcopy(record)
    for key in ('version', 'audit_status', 'citations'):
        result.pop(key, None)
    for key in ('verification_gaps', 'source_pages'):
        result.get('parameters', {}).pop(key, None)
    return result


def validate_source_review(original, current):
    """Require a complete outcome ledger for the original blocked set.

    The ledger attests a human source check; it is not proof of book correctness.
    Keep automation dispositions separate and reject inconsistent attestations.
    """
    meta = current['manifest']['source_review']
    proof = current['coverage'].get('source_review', {})
    rows = proof.get('records', [])
    _require(meta.get('baseline_bundle_hash') == original['manifest']['bundle_hash'],
             'review baseline mismatch')
    _require(meta.get('outcomes_hash') == outcomes_hash(rows), 'review outcomes hash mismatch')
    before, after = _records(original), _records(current)
    blocked = {r['id'] for r in original['coverage']['records']
               if r['review_state'] == 'human_verification_required'}
    ids = [row.get('id') for row in rows]
    _require(len(ids) == len(set(ids)) and set(ids) == blocked, 'review outcome IDs mismatch')
    _require(dict(Counter(r.get('outcome') for r in rows)) == proof.get('counts'),
             'review outcome counts mismatch')
    coverage_rows = current['coverage']['records']
    coverage = {r['id']: r for r in coverage_rows}
    _require(len(coverage) == len(coverage_rows) and set(coverage) == set(after),
             'review coverage IDs mismatch')
    replacements = set()
    for row in rows:
        rid, outcome = row['id'], row.get('outcome')
        _require(outcome in OUTCOMES, 'invalid review outcome')
        _require(isinstance(row.get('reason'), str) and row['reason'].strip(), 'missing review reason')
        _require(row.get('citations'), 'missing review source location')
        pack_id, old = before[rid]
        _require(row.get('pack_id') == pack_id, 'review package mismatch')
        if outcome == 'removed':
            _require(rid not in after, 'removed record still present')
            replacement = row.get('replacement_id')
            if replacement:
                _require(replacement in after and replacement != rid, 'missing replacement record')
                target = after[replacement][1]
                _require(target['audit_status'] == 'verified' and target['automation'] == old['automation'],
                         'replacement changed automation or lacks source verification')
                replacements.add(replacement)
            continue
        _require(rid in after and after[rid][0] == pack_id, 'review record missing or moved')
        new = after[rid][1]
        _require(new['automation'] == old['automation'], 'source review changed automation')
        if outcome == 'still blocked':
            _require(isinstance(row.get('question'), str) and row['question'].strip(),
                     'blocked review needs a specific question')
            _require(new['audit_status'] == 'blocked' and new['automation'] != 'calculated',
                     'blocked review claimed verified automation')
        else:
            _require(new['audit_status'] == 'verified', 'review status mismatch')
            if outcome == 'verified':
                _require(_mechanics(old) == _mechanics(new), 'verified record changed mechanics')
            else:
                _require(_mechanics(old) != _mechanics(new), 'correction has no mechanics change')
    _require(set(after) - set(before) <= replacements, 'unexplained new review records')
    _require(set(before) - set(after) == {r['id'] for r in rows if r['outcome'] == 'removed'},
             'unexplained removed review records')
    related = set(proof.get('related_record_ids', []))
    _require(related <= set(before) - blocked, 'invalid related review records')
    for rid in set(before) & set(after):
        old, new = before[rid][1], after[rid][1]
        _require(new['automation'] == old['automation'], 'source review changed automation')
        if rid not in blocked and rid not in related:
            _require(_mechanics(old) == _mechanics(new), 'unexplained change outside review')
    for rid, (_, record) in after.items():
        entry = coverage[rid]
        expected = 'human_verification_required' if record['audit_status'] == 'blocked' else 'verified'
        _require(entry['review_state'] == expected and entry['automation'] == record['automation'],
                 'review coverage disposition mismatch')
