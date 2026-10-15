# UI-05: Gem values for Cosmere spheres (loot ledger and wallets)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (strongest model, high effort: it changes a saved format, the PC `wallet`)
- **Branch:** `codex/ui-05-gem-values` from current `origin/main` **after PR #189 (UI-03) merges**
  (UI-03 rewrote the loot add/delete routes and added `_debit_cosmere_loot`; build on that)
- **Depends on:** UI-03 / PR #189
- **Context:** user decision 2026-10-15: yes, the gem changes a sphere's value. Today
  `_sphere_value` (`app.py`) counts chip 1 / mark 5 / broam 20 for every gem, the ledger stores a
  `gem` per entry, but the PC wallet (`doc['wallet']['spheres']`) keeps only chip/mark/broam
  totals with no gem. `_COSMERE_GEMS` lists 6 gems. The player sheet shows the wallet in
  `templates/cosmere_sheet.html` (~825 and the live repaint ~1681).

## Goal
A sphere's worth depends on its gem and size, using the rulebook's table. Wallets remember which
gems a PC holds. Ledger and wallet totals are shown in one common unit. Existing wallets and
ledgers keep working.

## The values (from the repo's own rules text, not invented)
`systems/cosmere/content/starter-rules.json` has a "Sphere Values in Diamond Marks" table. Read the
numbers from there, not from memory, and keep the unit **diamond marks**. As read on
2026-10-15, the table gives:

| Gem | Chip | Mark | Broam |
|---|---|---|---|
| Diamond | 0.2 | 1 | 4 |
| Garnet, Heliodor, Topaz | (check table) | | |
| Ruby, Smokestone, Zircon | 2 | (check) | (check) |
| Amethyst, Sapphire | (check) | 25 | 100 |
| Emerald | 10 | 50 | 200 |

Fill every cell from the table and put the finished table in the report. That is **ten** gems;
`_COSMERE_GEMS` has six, so add heliodor, topaz, zircon and amethyst. Keep chips as fractions of a
mark internally; to avoid float drift, store or compute in **diamond chips** (1 mark = 5 chips) and
convert for display.

## Approach
1. **One value table** in a small module (e.g. `systems/cosmere/spheres.py`): gems, denominations,
   `value_in_chips(gem, denom)`, and `wallet_total(wallet)`. `_sphere_value` and every total use it.
2. **Wallet format, versioned.** New shape:
   `wallet = {"version": 2, "spheres": {"<gem>": {"chip": n, "mark": n, "broam": n}}, "items": [...]}`.
   A legacy wallet (`spheres` is flat `{chip, mark, broam}`, no `version`) is read as **all
   diamond**. Convert on read in one helper; write only the new shape. Never lose a count during
   conversion, and test that the conversion is idempotent.
3. **Credit / debit by gem.** `_credit_cosmere_loot` adds to that entry's gem.
   `_debit_cosmere_loot` takes from that gem only (refuse if that gem's count is short, same
   message style as UI-03). Old ledger entries without a `gem` count as diamond.
4. **Totals.** Ledger rows and the party total show worth in diamond marks (e.g. "worth 12.4 mk").
   The per-gem counts stay visible.
5. **Player sheet.** The wallet section lists spheres per gem (only gems with a count), and
   the total in marks. The live repaint (`cosmere_loot` SSE) handles the new shape.
6. **Loot form.** The gem picker offers all ten gems; default diamond.

## Acceptance
- [ ] Tests (new `tests/test_cosmere_spheres.py` plus the loot tests): every table value; a legacy
      wallet reads as diamond and survives a credit/debit round trip; crediting an emerald broam
      and then deleting it restores the exact wallet; a debit of a gem the PC lacks is refused even
      if other gems would cover the value; totals are exact (no float drift) for mixed gems.
- [ ] A saved legacy PC document (fixture) loads, shows its old spheres as diamond on the sheet,
      and is written back in the new shape only after a real change (opening the sheet must not
      rewrite the file).
- [ ] `pytest -q tests/test_cosmere_loot.py tests/test_cosmere_loot_to_sheet.py tests/test_cosmere_spheres.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` (numbers). Full CI before merge.
- [ ] Browser (Claude does this if you can't): award an emerald broam and diamond chips, check the
      ledger total and the player sheet, delete the award, check the wallet is back.

## Out of scope / do not touch
- Stormlight infusion / spending Stormlight from spheres (gameplay, not money).
- Mistborn currency (boxings/clips; no gem values there).
- The UI-03 behaviour for spent awards (refuse, keep the line) stays as is.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

### Implementation

- Added `systems/cosmere/spheres.py` as the single source for the ten gems,
  denominations, exact values, wallet normalization, totals, and display formatting. Values stay
  as integer diamond chips until the final diamond-mark string, so mixed-gem totals have no
  floating-point drift.
- The completed value table is:

  | Gem | Chip | Mark | Broam |
  |---|---:|---:|---:|
  | Diamond | 0.2 mk | 1 mk | 4 mk |
  | Garnet | 1 mk | 5 mk | 20 mk |
  | Heliodor | 1 mk | 5 mk | 20 mk |
  | Topaz | 1 mk | 5 mk | 20 mk |
  | Ruby | 2 mk | 10 mk | 40 mk |
  | Smokestone | 2 mk | 10 mk | 40 mk |
  | Zircon | 2 mk | 10 mk | 40 mk |
  | Amethyst | 5 mk | 25 mk | 100 mk |
  | Sapphire | 5 mk | 25 mk | 100 mk |
  | Emerald | 10 mk | 50 mk | 200 mk |
- Wallet mutations now write version 2 with sphere counts grouped by gem. A legacy flat wallet is
  projected as Diamond in a detached copy, so opening its sheet does not rewrite the character.
  Its first sphere or item credit/debit migrates it while preserving every count and item.
  Note-only ledger entries do not migrate or save the wallet. Normalization is idempotent.
- Credit and reversal operate on the recorded gem and denomination. Equivalent wealth in another
  gem cannot fund a reversal. Old ledger entries without `gem` remain Diamond. Existing atomic
  validation and compensating wallet saves remain in place; add and delete rollback paths are
  covered.
- The ledger offers all ten gems with Diamond selected by default, shows each row and the running
  total in diamond marks, and gets all values from the server. The character sheet renders only
  nonempty gem groups, keeps its three stable wallet IDs, and replaces groups, goods, and the exact
  total from the existing `cosmere_loot` SSE event. The event carries the nested wallet and a
  server-calculated `wallet_total_marks`; JavaScript contains no second valuation table.
- The Stormlight sphere generator now draws from all ten gems and calculates its displayed worth
  through the same table. Mistborn currency and all PF2e/D&D mechanics are untouched.

### Validation

- `python -m pytest -q tests/test_cosmere_loot.py tests/test_cosmere_loot_to_sheet.py tests/test_cosmere_spheres.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
  -> **62 passed in 59.11s**.
- `python -m pytest -q tests/test_cosmere_generator.py tests/test_cosmere_generator_ui.py tests/test_cosmere_sheet_tabs.py`
  -> **33 passed in 14.52s**.
- `python tools/check_templates.py` -> **all 107 templates parse**.
- `git diff --check` -> exit 0, no output.
- Browser verification remains for Claude: award an Emerald broam and Diamond chips, verify the
  ledger and sheet counts/totals, then delete both awards and confirm the wallet returns exactly.
  Check the loot page and Gear tab at 1440px and 375px in both Stormlight and Mistborn skins.

## Review

**Claude, 2026-10-15: accepted. No required fixes.**

Read the diff: `systems/cosmere/spheres.py`, the loot add/delete/list routes, the sheet render and
template, the loot template, the generator change and the tests. Checked live on a local server on
this tree with a throwaway campaign. Did not rerun the test suite.

- **Value table vs. the rulebook.** I extracted the "Sphere Values in Diamond Marks" table from
  `systems/cosmere/content/starter-rules.json`:
  - Diamond 0.2 / 1 / 4;
  - Garnet, Heliodor, Topaz 1 / 5 / 20;
  - Ruby, Smokestone, Zircon 2 / 10 / 40;
  - Amethyst, Sapphire 5 / 25 / 100;
  - Emerald 10 / 50 / 200.

  `_VALUE_TIERS` is exactly that times 5, in integer diamond chips, for all ten gems.
- **Legacy wallet.** Teft's saved wallet was the old flat `{chip 0, mark 3, broam 0}` plus a Rope.
  The Gear tab showed "Diamond: Broam 0 / Mark 3 / Chip 0, Worth 3 mk". The file's modification
  time was unchanged after opening the sheet, so viewing does not rewrite it.
- **Awards, through the loot form.** I gave Teft an Emerald broam and then 7 diamond chips. The
  ledger rows read "1 emerald broam, Worth 200 mk" and "7 diamond chips, Worth 1.4 mk". The ledger
  total is 208.4 mk, which matches the two older entries (3 + 4 mk). The gem picker lists all ten
  gems and defaults to Diamond.
- **Live sheet update.** The Gear tab, open in another tab, repainted to "Diamond 3 marks 7 chips,
  Emerald 1 broam, Worth 204.4 mk" without a reload.
- **Saved format.** The file became `{version: 2, spheres: {diamond: {...}, emerald: {...}},
  items: [Rope]}`. A later state save (`/cosmere/pc/<id>/state`) kept the wallet unchanged.
- **Reversal.** Removing both awards in the UI restored the wallet exactly (diamond 3 marks, no
  emerald group, Rope) and the sheet repainted to "Worth 3 mk".
- **No cross-gem funding.** I awarded an Emerald mark, then set the wallet to no emerald and 100
  diamond broams. Deleting the award was refused with 409 "Wallet does not have enough emerald
  marks to reverse this award."
- **Layout:** loot page and Gear tab at 1440px and 375px in both skins. No sideways scroll, no
  element past its box, and two gem groups wrap cleanly on a phone.

Note (not required): under the Mistborn skin the wallet and loot page still show Roshar gems and
spheres. That matches the brief (Mistborn currency is out of scope), but a Mistborn campaign will
eventually want boxings and clips there. It belongs in the Cosmere screen batch or a Mistborn task.
