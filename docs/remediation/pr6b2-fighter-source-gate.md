# PR6B2: Fighter source-evidence gate

## Outcome

Retain a reviewable, non-executable Player Core/Fighter evidence shell and prove
that the existing schema fails closed while the supplied artifact's printing,
complete errata chain, rules normalization, and distribution treatment remain
unverified. This increment does not create a source-backed Fighter golden and
does not activate rules in the Flask application.

## Ruling

The supplied Player Core PDF establishes an artifact hash, page locators, and
candidate level-1 proficiency ranks. It does not establish a publisher printing
or revision designation. Inspection also shows text that predates identified
Fall 2023, Spring 2025, and Spring 2026 corrections. Therefore PR6B2 must not
mark the source verified, invent named reviews, or weaken schema-v2 publication
gates. The executable Fighter golden is deferred until those gates are closed.

## Task 1: freeze the review evidence

- Update `docs/remediation/pr6b-source-ledger-draft.md` with independently
  checked artifact metadata and the observed pre-errata markers.
- Keep exact printing, complete errata applicability, armor normalization, and
  license conclusions explicitly open.
- Retain no rules prose or PDF content in the repository.

## Task 2: add a fail-closed candidate package

- Add one schema-v2 authoring document under
  `systems/pf2e/rules/candidates/`.
- Identify it as a draft candidate with a distinct partial ruleset ID.
- Retain the unverified source declaration and a quarantined Fighter stub only.
- Keep `supported_classes` empty, list Fighter as an exclusion, and retain no
  mechanics on the quarantined record.
- Bind the candidate's quarantined evidence citations to its retained source
  inventory in focused tests without changing the versioned generic loader.
- Add focused tests that compile and load the candidate, bind its deterministic
  package hash, deny lookup/derivation, and prove that attempted activation with
  the independently authored 13 candidate grants is rejected while source
  review remains unresolved.

## Task 3: verify the boundary

- Run the focused PF2e rules suite.
- Run the same non-browser/non-PostgreSQL suite as the primary CI job.
- Run the template parser and `git diff --check`.
- Confirm that the Flask application imports neither the candidate authoring
  file nor the derivation path.

## Acceptance criteria

- The source artifact hash and page locators are accurate.
- The candidate cannot be retrieved or derived as an enabled class.
- Both unresolved rights/review metadata and unresolved printing/revision
  metadata independently prevent activation.
- The 13 attempted grants are literal test inputs transcribed independently of
  the candidate file; they are not a named rules review or an approved golden.
- No copyrighted rules prose, PDF, GM/player UI, routes, persistence, or runtime
  activation is added.
- Existing schema-v1 bytes and schema-v2 behavior remain unchanged; this slice
  adds no compatibility-affecting generic schema invariant.

## Deferred gate for the executable golden

The real Fighter level-1 golden requires an authoritative printing/revision
designation, a complete applicable errata inventory, a named rules review of
the 13 normalized tuples (including the three-category armor split), and a
named license review with approved notices and distribution treatment.
