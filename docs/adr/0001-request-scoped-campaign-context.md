# ADR-0001: Request-scoped campaign context

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Project owner and maintainers

## Context

The application authenticates a user's campaign through the request session, but
many PF2e handlers read and mutate process-wide campaign state such as the live
campaign, party library, and active encounter. A campaign switch can therefore
change the data addressed by another in-flight or subsequent request without
changing that request's authorization decision. The same ambient state also
prevents safe use of multiple web workers.

The remediation must preserve the existing Flask application and support an
incremental migration. PF2e and Cosmere campaigns must continue to coexist, and
production cannot wait for the full persistence migration before receiving a
tenant-isolation guard.

## Decision

Every authenticated campaign request will resolve one explicit, immutable
`CampaignContext` before application logic runs. The context will contain the
principal, campaign, membership, system, and campaign-scoped service/repository
handles used by the request.

Authorization and data access must resolve the same campaign ID. Repository and
service methods that read or mutate campaign data will require that ID (or the
context) explicitly; they must not infer it from a process-global live campaign.

Module-global state is allowed only for immutable, shareable objects such as
validated content catalogs and initialized extensions. Mutable objects such as
party members, encounters, current campaign paths, and event audiences are not
permitted as application globals in the target architecture.

During the transition, handlers that still depend on legacy live-campaign globals
will use a temporary live-campaign policy. If the authorized request campaign is
not the loaded live campaign, the request will fail with `409 campaign_not_live`
instead of operating on another campaign's state.

Event publication and subscription will also require the resolved campaign ID
and an explicit audience. Client-side filtering is not an authorization control.

## Options considered

### A. Continue using a server-wide live campaign

| Dimension | Assessment |
|---|---|
| Initial complexity | Low |
| Tenant isolation | Unsafe |
| Multi-worker support | Not viable |
| Migration cost | Deferred and increasing |

This preserves current behavior but cannot provide a defensible multi-campaign
security boundary.

### B. Add locks around the current globals

| Dimension | Assessment |
|---|---|
| Initial complexity | Medium |
| Tenant isolation | Fragile |
| Multi-worker support | No; locks are process-local |
| Long-term maintainability | Poor |

Locks could serialize some mutations but would not make authorization and data
selection refer to the same campaign, and would not coordinate multiple workers.

### C. Introduce request-scoped context and explicit repositories

| Dimension | Assessment |
|---|---|
| Initial complexity | Medium |
| Tenant isolation | Strong and testable |
| Multi-worker support | Yes, after shared persistence/events |
| Incremental adoption | Strong |

This is the selected option.

## Trade-off analysis

Explicit scope adds parameters and exposes previously hidden dependencies. That
is intentional: a campaign mutation should be difficult to write without naming
the campaign it affects. A temporary compatibility layer is acceptable only when
it fails closed on campaign mismatch and is covered by two-client, two-campaign
tests.

A complete application rewrite would remove ambient state more quickly on paper,
but would combine security, persistence, rules, and UI risks into one cutover.
Vertical extraction from the existing Flask app provides safer review and rollback.

## Consequences

- Route policy and campaign resolution become centralized and auditable.
- Cross-campaign tests can assert both authorization and addressed data.
- Campaign switching changes request/session context rather than shared content.
- Services and repositories become usable from multiple workers and background jobs.
- Existing handlers must be migrated deliberately; mixed scoped/global access is a
  temporary risk that requires inventory and static checks.
- Routes that rely on the current single-live-table model may temporarily return
  `409` until their state is repository-backed.

## Acceptance criteria

- Two authenticated clients in different campaigns cannot read, mutate, export,
  or subscribe to each other's resources.
- A stale request after a live-campaign switch fails rather than addressing the new
  campaign.
- Every campaign-scoped repository operation receives an explicit campaign ID.
- No mutable campaign data remains in module globals before multiple web workers
  are enabled.
- Event delivery is campaign-scoped and audience-filtered on the server.

## Action items

1. Add an exhaustive route-policy inventory and characterization tests.
2. Add `Principal` and `CampaignContext` resolution at the request boundary.
3. Apply the temporary live-campaign mismatch guard to legacy handlers.
4. Introduce campaign-scoped repositories and migrate vertical slices.
5. Add a static check preventing new access to banned mutable globals.
6. Remove the compatibility guard after all mutable state is repository-backed.
