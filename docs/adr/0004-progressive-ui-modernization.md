# ADR-0004: Progressive UI modernization with Jinja and Vite

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Project owner and maintainers

## Context

The application is a server-rendered Flask and Jinja system. Authentication,
campaign authorization, PF2e rules, and game-state mutation already live on the
server and are covered by a broad Python test suite. Replacing that application
shell would require rebuilding and revalidating those boundaries before users
received the workflow, accessibility, mobile, and performance improvements that
prompted the remediation program.

The current UI is nevertheless expensive to evolve. The audited revision has 86
tracked Jinja templates. Several critical pages contain thousands of lines of
page-local CSS and JavaScript, embed large rules datasets into their HTML, and use
page-specific component and color conventions. Seven entry points also have
separate precompiled Tailwind outputs. The result is duplicated behavior,
multi-megabyte character-authoring responses, inconsistent keyboard and error
handling, and mobile layouts that cannot be repaired reliably through isolated
CSS patches.

The project needs a shared frontend architecture, but the migration must preserve
working routes, allow small reversible pull requests, and avoid introducing a
second implementation of authorization or PF2e rules in the browser.

## Decision

Modernize the existing server-rendered application incrementally. Jinja remains
the page-composition and first-render layer. Vite will compile shared JavaScript
modules and CSS for a common design system; it does not introduce a client-side
application framework or a Node.js production runtime.

The target structure is:

```text
frontend/
  entries/          # one small entry module per migrated surface
  components/       # behavior for dialogs, tabs, async actions, autosave, etc.
  styles/           # tokens, reset/base rules, primitives, layouts, utilities
  lib/              # API, event, focus, and error-handling helpers
templates/
  layouts/          # account, player, GM, and workflow shells
  components/       # Jinja macros/partials with semantic HTML contracts
static/dist/         # Vite manifest and hashed production assets
```

Flask will resolve Vite's manifest and emit hashed production assets. Development
may use Vite's development server, but deployed pages must never depend on it or
on a runtime CSS CDN. Because the current Railway deployment is Python-only, the
first migration will commit generated `static/dist/` output and CI will rebuild it
and fail on a diff. A later deployment change may build assets during release and
stop committing output only after that path is proven repeatable.

The shared design system will define:

- semantic color, spacing, typography, elevation, motion, and breakpoint tokens;
- accessible Jinja contracts for fields, buttons, dialogs, tabs, steppers,
  navigation, empty states, errors, loading states, and ownership/status badges;
- small JavaScript behavior modules that enhance those server-rendered controls;
- one responsive shell and documented exceptions for genuinely two-dimensional
  content such as maps and wide data tables.

Migration will proceed by complete user journey or page, not by replacing a
technology everywhere at once. A migrated page may coexist with legacy inline
styles, globals, and precompiled Tailwind output elsewhere. Temporary adapters may
publish a narrow compatibility API on `window`, but new modules must not add new
cross-page globals. The server remains authoritative for permissions, validation,
rules eligibility, and state transitions; browser code renders server contracts
and manages interaction state only.

## Options considered

### A. Replace the UI with a React, Vue, or other single-page application

| Dimension | Assessment |
|---|---|
| Initial delivery speed | Low |
| Cutover risk | Very high |
| Reuse of Jinja routes/tests | Low |
| Long-term component model | Strong |

This would provide a uniform client component model, but it would also create a
second routing and state layer, force a broad API conversion, and delay urgent
ownership, accessibility, and workflow repairs. It is rejected for this phase.

### B. Rewrite every Jinja template and stylesheet in one coordinated cutover

| Dimension | Assessment |
|---|---|
| Visual consistency | Potentially high |
| Reviewability | Low |
| Rollback granularity | Poor |
| Regression risk | High |

This retains server rendering but still combines too many behavioral and visual
changes into one release. It is rejected in favor of vertical migration.

### C. Keep page-local scripts, styles, and Tailwind builds

| Dimension | Assessment |
|---|---|
| Immediate cost | Low |
| Accessibility consistency | Poor |
| Bundle and page-size control | Poor |
| Maintenance cost | Increasing |

This avoids a toolchain addition but preserves the duplication responsible for
the current drift. It is rejected as the target architecture.

### D. Incremental Jinja migration with Vite and a shared design system

| Dimension | Assessment |
|---|---|
| Initial cost | Medium |
| Incremental delivery | Strong |
| Server contract reuse | Strong |
| Rollback granularity | Strong |

This is the selected option. Vite supplies module boundaries, dependency
management, minification, hashing, and bundle reporting without dictating a
client framework.

## Trade-off analysis

The migration temporarily creates two frontend worlds: legacy page-local assets
and new shared assets. That duplication is acceptable only while each migrated
surface has an owner and removal criterion. Shared primitives must not become a
second layer of one-off variants.

Vite adds Node.js tooling, manifest integration, and generated-asset review. In
return, it makes module ownership, cacheable assets, bundle sizes, and dependency
use observable. Committing initial build output is not ideal, but it preserves the
existing deployment contract and gives CI a deterministic comparison until the
release image builds assets itself.

Jinja macros can share markup but cannot provide the isolation of framework
components. The project will compensate with explicit HTML/accessibility
contracts, component-level browser tests, prefixed styles during transition, and
small behavior modules. A client framework may be reconsidered for a future
surface only after measurements show that progressive enhancement cannot meet its
interaction needs.

## Consequences

- Existing Flask routes, authorization, and rule calculations remain the source
  of truth.
- Users receive improvements one workflow at a time without a flag-day cutover.
- New UI work uses shared tokens, semantic templates, and module entry points.
- Production receives hashed, cacheable static assets rather than larger inline
  scripts and styles for migrated behavior.
- CI gains a Node build, manifest validation, bundle reporting, browser tests,
  accessibility checks, and viewport screenshots.
- Legacy Tailwind outputs and inline code remain temporarily; their inventory and
  deletion criteria must be tracked.
- Build artifacts are committed during the first phase and must never be edited by
  hand.
- Performance improvement is not assumed merely because Vite is present. HTML
  payloads, data hydration, asset transfer, render time, and interaction latency
  remain separately measured gates.

## Migration gates

### Gate 0: Baseline and ownership

- Record the audited revision, response sizes, template/test inventory, and UI
  spot checks in `docs/remediation/baseline-2026-09-24.md`.
- Assign an owner and rollback path to each migrated user journey.
- Do not combine a frontend migration with PF2e rule changes.

### Gate 1: Reproducible asset pipeline

- `npm ci` followed by the production build succeeds from a clean checkout.
- CI verifies the Vite manifest, rebuilt output, and absence of references to a
  development server or runtime CSS CDN in production templates.
- Flask has a tested manifest helper and a documented legacy fallback.
- A missing or corrupt manifest fails visibly during CI/startup rather than
  silently rendering an unstyled page.

### Gate 2: Design-system foundation

- Tokens and core primitives have documented states and accessibility contracts.
- Account and low-risk shell pages prove coexistence with legacy assets.
- Keyboard operation, focus behavior, reduced motion, 200% text, and 400% zoom
  checks pass for the primitives before high-risk pages adopt them.

### Gate 3: Vertical page migration

For each migrated route:

- route and authorization characterization tests remain green;
- primary tasks pass at 375x812, 768x1024, 1024x768, and 1440x900;
- axe reports no critical or serious findings, with manual keyboard and screen-
  reader checks for custom interactions;
- response and asset sizes do not exceed the recorded baseline, and the pull
  request states which embedded data or legacy asset it removes;
- loading, empty, validation, offline/network, conflict, and retry states are
  exercised where applicable;
- old and new implementations can be rolled back independently.

### Gate 4: Legacy removal

- All supported entry points for the surface use the new component contract.
- Browser telemetry and support observation show no unresolved regression during
  the agreed observation window.
- Legacy inline code, compatibility globals, and page-specific CSS are deleted in
  the same or an immediately following cleanup change.
- A legacy asset pipeline is removed only when no template references its output.

## Acceptance criteria

- The app remains usable with server-rendered navigation and forms when optional
  enhancement JavaScript fails; explicitly JavaScript-dependent game controls
  present a clear failure state.
- No browser module decides campaign authorization, character ownership, or PF2e
  eligibility independently of the server.
- Every new shared component has one semantic HTML contract and tested keyboard
  behavior.
- Production pages load only built, versioned assets from the application origin.
- The four-viewport, accessibility, and response-size gates run in CI for every
  migrated critical journey.
- No legacy implementation is removed before its replacement passes the same
  functional characterization tests and has a rollback path.

## Action items

1. Add the Vite workspace, manifest helper, deterministic CI build, and bundle
   report without migrating a high-risk page.
2. Define semantic tokens and the account/application shell primitives.
3. Add Playwright plus axe coverage and the standard viewport matrix.
4. Migrate player ownership and authoring journeys before visual-only page work.
5. Move large embedded compendia behind scoped data endpoints or deferred chunks.
6. Migrate the sheet, tracker, and encounter builder as separately reversible
   vertical slices.
7. Remove compatibility assets and globals as each surface clears Gate 4.
