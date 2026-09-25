# GM_pf2e remediation program

This program turns the 2026-09-24 engineering, UX, security, performance, and
PF2e-rules audit into independently reviewable changes. The measured starting
point is recorded in [baseline-2026-09-24.md](baseline-2026-09-24.md); architectural
decisions are indexed in [the ADR directory](../adr/README.md).

## Delivery principles

- Never combine all remediation work into one rewrite or deployment.
- Keep each pull request deployable and independently reversible where practical.
- Preserve PF2e and Cosmere behavior unless a reviewed change explicitly says
  otherwise.
- Use additive schemas, dry-run migrations, shadow comparison, and verified
  backups before a data-authority cutover.
- Treat browser behavior as presentation; authorization and rules legality remain
  server responsibilities.
- Do not increase web-worker count until mutable campaign globals and process-local
  event delivery are removed.
- Do not merge directly to `main`; `main` is the Railway deployment branch.

## Planned pull requests

| PR | Scope | Required exit condition |
|---:|---|---|
| 1 | ADRs, audited baseline, route-policy inventory, and characterization gates | Documentation is accepted; every current endpoint is inventoried; known architectural coupling cannot grow silently. |
| 2 | Request-scoped campaign containment and centralized authorization | Two-client/two-campaign isolation tests pass; stale live-state requests fail closed. |
| 3 | Campaign-scoped events and production hardening | Player streams contain no GM secrets; CSRF, configuration, uploads, and dependencies pass security gates. |
| 4 | Transactional identity, membership, ownership, and migration tooling | PostgreSQL and legacy counts reconcile; concurrency invariants and rollback/export pass. |
| 5 | Character IDs, capabilities, drafts, create/import, and ownership UX | A player completes creation/import without a GM endpoint and cannot affect another character. |
| 6 | PF2e manifest, schema, vertical-slice package, and shadow validation | Deterministic package and source-backed goldens pass without behavior cutover. |
| 7 | Authoritative builder, level-up, spellcasting, and current-rules cutover | Forged payloads fail; current class progression and spell models pass reviewed goldens. |
| 8 | App factory, repositories, outbox, content catalog, and payload reduction | No mutable campaign globals remain; multi-worker and performance gates pass. |
| 9 | Shared UI system, responsive migration, accessibility, and browser testing | Core journeys meet the documented viewport, keyboard, and WCAG gates. |
| 10+ | Product-completeness vertical slices | Each workflow is end-to-end, source-backed where rules-sensitive, and independently releasable. |

## Pull-request gate

Every implementation pull request must include the applicable subset of:

- existing pytest suite and Jinja template parsing;
- focused unit, HTTP integration, and authorization-boundary tests;
- PostgreSQL integration tests for transactional behavior;
- deterministic PF2e golden and migration tests;
- Playwright interaction, viewport, and accessibility checks;
- migration dry-run, verification, and rollback evidence;
- payload/performance comparison with the accepted baseline;
- Railway preview smoke test before merge.

Any intentionally deferred acceptance criterion must be named in the pull request
with the later PR that owns it.
