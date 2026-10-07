# Chronicle Surface Retirement

Chronicle is retired from the normal GM and player experience without deleting
campaign data or changing bookmarked route behavior.

## Product behavior

- The shared GM navigation and PF2e GM Hub no longer link to Chronicle.
- PF2e and Cosmere player navigation always links to the existing Notes page.
- The character-sheet journal empty state uses neutral session language.
- Ordinary page rendering no longer checks Chronicle publication state or
  reads the Chronicle document index.

## Compatibility boundary

This change deliberately preserves the Chronicle subsystem for recovery and
existing bookmarks. Chronicle browser routes, APIs, authorization, publishing,
handouts, exports, templates, tools, and stored files are unchanged. Removing
those compatibility paths requires a separate decommissioning plan with an
explicit archive and URL-retirement policy.

## Regression coverage

`tests/test_nav_cleanup.py` and `tests/test_chronicle_reading.py` cover the
retired discovery points, unconditional Notes navigation, the absence of
Chronicle reads on normal pages, byte preservation for seeded Chronicle data,
and continued bookmark/API availability.
