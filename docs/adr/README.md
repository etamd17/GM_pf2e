# Architecture decision records

Architecture decision records (ADRs) capture consequential technical decisions,
their alternatives, and the conditions under which the decision remains valid.
They are append-only: a changed decision is recorded in a new ADR that supersedes
the old one rather than silently rewriting history.

## Status values

- **Proposed**: under review and not approved for implementation.
- **Accepted**: approved direction for implementation.
- **Deprecated**: retained for history but no longer recommended.
- **Superseded**: replaced by a named later ADR.

## Index

| ADR | Status | Decision |
|---|---|---|
| [0001](0001-request-scoped-campaign-context.md) | Accepted | Resolve authorization and mutable game state through an explicit request-scoped campaign context. |
| [0002](0002-transactional-persistence.md) | Accepted | Use PostgreSQL-backed repositories for transactional identity and mutable state. |
| [0003](0003-versioned-pf2e-rules-kernel.md) | Accepted | Make a versioned in-process PF2e rules kernel the sole legality and derivation authority. |
| [0004](0004-progressive-ui-modernization.md) | Accepted | Modernize the Jinja frontend incrementally with shared components and bundled modules. |

## ADR requirements

Each ADR records:

- context and constraints;
- the chosen decision;
- alternatives considered;
- trade-offs and consequences;
- acceptance criteria;
- implementation or follow-up actions.

Implementation pull requests should link the ADRs they advance and note any
accepted criterion that remains incomplete.
