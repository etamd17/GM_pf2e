# Chronicle Surface Retirement Design

## Goal

Remove Chronicle from the normal GM and player product experience without
destroying campaign data, breaking saved links, or introducing a replacement
interface that would require a new UI decision.

## Approved interpretation

"Get rid of the Chronicle section of the website" is implemented as a
reversible product-surface retirement:

- Remove the GM top-navigation entry and GM Hub tile.
- Always show the already-shipped Notes entry in both player navigation bars.
- Remove Chronicle publication checks and document-index reads from ordinary
  page rendering.
- Replace the remaining `No chronicle yet` inventory copy with neutral session
  language.
- Keep the existing Chronicle routes and APIs available only to callers with
  saved URLs, so this bounded change does not also become an authorization and
  data-lifecycle rewrite.

No new tile, navigation layout, styling, or replacement feature is introduced.

## Data and compatibility boundary

Existing Chronicle files under campaign-scoped and legacy data directories
remain untouched. Explicit campaign export continues to include those files,
and `.gitignore` continues to exclude local Chronicle output. The retirement
must never call an unpublish or deletion helper.

`/notes` and `/api/notes` remain the player note system. Independent handouts,
their visibility rules, and every Chronicle bookmark/API retain their current
behavior in this PR.

## Acceptance

- PF2e and Cosmere players see Notes whether Chronicle data exists or not.
- GM navigation and the PF2e GM Hub contain no Chronicle discovery point.
- Rendering normal GM/player pages performs no Chronicle publication or
  document-index lookup.
- Seeded Chronicle bytes are unchanged after exercising the normal surfaces.
- Existing bookmarked Chronicle routes still resolve.
- Templates parse and the full regression suite remains green.

## Follow-up boundary

Hard retirement of the 13 Chronicle endpoints, publish-token exceptions,
templates, CSS, publishing tool, and storage helpers is a separate
decommissioning project. It requires explicit URL behavior, recovery archive,
and authorization migration acceptance criteria; it is not part of this
surface-removal PR.
