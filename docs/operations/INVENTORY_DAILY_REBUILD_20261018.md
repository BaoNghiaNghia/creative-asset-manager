# Inventory Daily — phased stabilization using real operator workbook

Status: P0 source-only implementation on feature branch, **NOT deployed to production**.
Reference: user-supplied "KIỂM KÊ HÀNG NGÀY.xlsx", SHA-256
`5b22ccb56295b4e2035c23b439281f83f94aeddd73685d2c8cad8b17735ed0b8`,
dated 2026-10-18. Store only minimal source-grounded cell evidence in the repository:
`apps/api/tests/fixtures/inventory_catinat_2026_10_18_golden.json`. The
original workbook and credentials must not enter git.

## Observed workbook contracts

- `KHO PHA CHẾ`: sealed stock / warehouse inventory, two dimensions
  "Nguyên" and "Lẻ", with Xuất/Nhập/Tồn fields. No automatic merging with
  counter stock just because normalized item names match.
- `PHÒNG PHA CHẾ`: counter stock, partial packages plus quantity dimension
  "gr", incoming stock, closing stock and damaged/waste field. One real
  `1/2 chai` cell became Excel serial `46054` with `d/m` number format.
  It **cannot** be safely restored from numeric serial alone.
- `KHO PHÒNG RANG`: opening, inbound, outbound, closing and damaged fields;
  the pizza hải sản line says opening=6, closing=6, waste="1( gãy)".
  Flag for review; do not infer movement or modify stock.
- `Báo cáo sử dụng NVL trong ca`: separate shift ledger, not a fourth
  warehouse. It contains fresh fruit, toppings, sauces and brewed tea.
  Example: "Vải trái" 10 opening, "17+4" used, 14 inbound, 5 reported closing;
  a numeric reconciliation might suggest 3, but the expression and period
  must be verified by the operator before a correction is authorized.
- Two blank tabs are not stock sources.

Quantities can be written in Vietnamese decimal comma, explicit `gr`
alias, fractions of containers, dated multiple lots, numeric counts,
operator notes and `/`. **The meaning of `/` has NOT been confirmed
by the user**. Neither `/` nor blank may become zero automatically.
Numbers and units must be interpreted by sheet, warehouse, stock state,
business date and batch. Normalizing names is insufficient.

## P0 protection implemented in source

1. Strict V1 decimal parser: `1,234.50` remains 1234.50; `1,5` now
   fails closed instead of silently becoming 15. No guessed locale.
2. V1 variance reconciliation requires the exact warehouse/SKU set in both
   snapshots. If a row vanishes, refuse to construct a write plan; never
   synthesize `Decimal(0)` for an absent row.
3. V2 quantity parser recognizes explicit `gr` as grams, without making
   container-to-gram conversions.
4. Read-only quantity observation classifier records evidence; ambiguous
   slash, fractional packages, formulas, dated batches, shifted cell
   formatting and expressions are review-required, not writable values.
5. Golden tests directly cover **minimal actual cell values** from all four
   business tabs. No live workbook writes or Google API calls.

## P0 production rollout block

No turning on V4 auto apply, Morning Reset to Shared Sheet or restoring any
historical date until these independent gates are actually proven:

- Identify live tenant/config/apply mode using authenticated read-only
  diagnostics; a passing `/ready` is not evidence of correct Inventory data.
- Verify Google Drive/Sheets permissions with the read-only preflight,
  then perform real disposable copy -> marker write -> readback -> dispose
  without touching the original.
- Verify production backup source and isolated restore if migrations are
  proposed, then deploy only after CI and rollback plan.
- Verify affected source workbook revision/hash immediately before any
  potential write.
- Shadow and compare every populated row against operator records for at
  least one full operating cycle; unresolved ambiguity prevents promotion.
- Review source-specific locations, aliases, approved container conversion
  factors and slash semantics with the human operator.

## Proposed V2 design (not yet implemented)

1. Immutable evidence: spreadsheet ID, sheet, exact A1 cell, entered/formatted
   value, number format, date/shift, hash, formula, merged/protected metadata.
2. Canonical inventory identity: tenant + material + physical location +
   sealed/open + unit + business day + batch where applicable.
3. Per-location movement ledger with immutable events: receiving, usage,
   waste, transfer-out/in and physical count; never combine units without a
   human-approved conversion factor.
4. Deterministic reconciliation: only evaluate
   `closing_expected = opening + receiving - outgoing - usage - waste`
   when all operands, units, time period and transfers are verified;
   otherwise mark `MISSING_DATA`, `REVIEW_REQUIRED` or `UNIT_MISMATCH`.
5. Closing is an independently confirmed **inventory snapshot**, never merely
   "Gemini job finished". Opening references verified prior Closing, with
   explicit unresolved carry-forward status when not yet confirmed.
6. Gemini runs as advisory/explanation, NEVER as sole authority for a physical
   adjustment. Historical replay is immutable-copy-only; no retroactive
   write or clear in the current Shared Sheet.
7. Any eventual live write uses Preview -> human review -> exact revision
   compare-and-set -> mutation -> independent readback and idempotency.

## Test / operational evidence

Run `cd apps/api && .venv/bin/python -m pytest -q
tests/modules/inventory/test_inventory_catinat_golden.py
tests/modules/inventory/test_daily_sheet_parser.py
tests/modules/inventory/test_daily_sheet_v2.py`.
The full Inventory test folder must pass as an additional release gate.

This phase intentionally **does not** modify production settings, the
scheduler's live slot state or any historical morning reset. Subsequent phases
must explicitly replace the lifecycle's Gemini dependency with verified
deterministic Closing/Opening after shadow testing.
