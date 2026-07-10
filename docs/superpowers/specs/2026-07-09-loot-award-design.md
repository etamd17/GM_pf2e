# Loot ledger → sheet award (design)

**Date:** 2026-07-09
**Status:** Approved queue item #4 (locked 2026-07-04). Compact spec — most
infrastructure already exists.

## Goal

The GM's loot ledger (`/gm/loot`) can AWARD an item/coins to a specific PC,
depositing it straight into that character's inventory/wallet and notifying
their sheet — not just logging it.

## What already exists (verified 2026-07-09)

- `/api/send_loot` (app.py ~13904): deposits items→`build['equipment']` +
  coins→`pc.pp/gp/sp/cp`, persists, appends a ledger entry, and broadcasts
  SSE `loot_received`. Called from the tracker + party view.
- The player sheet listens for `loot_received` (player_sheet.html ~9191):
  toast + refresh.
- The loot ledger page has an "Award Loot" form, but it only calls
  `/api/loot_ledger/add` (LOG-ONLY — nothing reaches the PC), and its
  history entries have no way to be pushed to a sheet.

## The gap / design

1. **Extract** the deposit core of `send_loot` into
   `_deposit_loot_to_pc(pc_name, items, coins)` (returns the applied totals);
   `send_loot` thin-wraps it. No behavior change to the existing callers.
2. **Ledger award-to-sheet route** `POST /api/loot_ledger/<entry_id>/award`:
   deposits that ledger entry's items+coins into a target PC (defaults to
   the entry's recipient when it names a real PC), marks the entry
   `awarded_to` / `awarded_at`, broadcasts `loot_received`. Refuses to
   double-award an already-awarded entry (unless `force`). 404 unknown
   entry; 400 when the target isn't a party PC.
3. **Ledger UI**: the "Award Loot" form gains a "deposit to sheet"
   affordance — when the recipient is a real PC, submitting deposits via
   `send_loot` (so it lands on the sheet) instead of log-only; history rows
   for a PC recipient that were log-only get an "Award to sheet" button +
   an "awarded" badge once deposited.

## Constraints

- Reuse the existing deposit + SSE + ledger internals; no second inventory
  write path. GM-gated (`@gm_required` / the loot prefix).
- Inline-handler escaping for PC/item names in the ledger rows.
- Idempotence: an entry marked awarded shows the badge and its button is
  disabled; the route rejects a repeat without `force`.

## Testing

- TDD: `_deposit_loot_to_pc` applies items (dedup by name) + coins and
  persists; the award route deposits + marks + 404/400/double-award guard;
  `send_loot` still works via the shared core.
- Browser: award an entry from `/gm/loot` → the PC's inventory + wallet
  update, the sheet toasts, the entry shows "awarded".
