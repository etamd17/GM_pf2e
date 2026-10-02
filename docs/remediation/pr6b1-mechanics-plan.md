# PR6B1: offline proficiency derivation contract

This small increment prepares the source-backed PR6B vertical slice while the
source ledger is under review. It uses synthetic facts only. No package in this
increment claims the full September 2026 ruleset, and the application does not
load these facts during play.

## Contract

- Preserve schema v1 compilation and loading byte for byte.
- Add schema v2 class mechanics with an explicit set of supported levels and
  closed proficiency grants: level, statistic ID and rank. Other record kinds
  cannot claim derived class proficiency behavior.
- Keep normalization deterministic; reject duplicate grants, unknown fields,
  unsupported automation combinations and malformed levels/ranks.
- Provide a pure `derive_class_proficiencies` function whose input contract is
  an immutable package returned by `load_package`. It returns immutable ranks and rejects
  levels absent from that record's explicit supported set. This function
  projects only fixed proficiency grants. It does not resolve skill choices,
  class features, spellcasting or a complete character statistic block.
- Test the path with synthetic values and package round trips. No real Fighter
  golden is accepted until the exact Player Core printing and pages, applicable
  errata, notices and named reviews are in the ledger.

The next PR6B increments will add reviewed choices, class/spell/linked-actor
records and source-backed goldens. PR6C adds shadow comparison; PR7 connects
server-authoritative character transitions. GM/player UI decisions require
approved mockups before implementation.

## Local verification

The synthetic derivation tests first failed against the PR6A schema. After
implementation, all 92 focused package tests pass on Windows/Python 3.11,
including v1's exact package hash and a later supported-level upgrade. The
new modules compile and `git diff --check` passes. The application has no
imports of this derivation path. The pushed PR's CI is the full-suite gate;
the source ledger and synthetic rules package do not enable production rules.
