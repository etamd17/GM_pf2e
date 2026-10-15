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

## Review
