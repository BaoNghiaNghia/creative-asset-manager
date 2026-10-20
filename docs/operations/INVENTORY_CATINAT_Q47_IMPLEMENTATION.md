# Catinat Inventory Daily Q01–Q47 — Implementation & release gates

Status: **partial implementation / NOT authorized for production write or Morning Reset**.
Business policy: user-approved Q01–Q47 (with Q30 covered by Q25, not separately confirmed).
Business timezone: `Asia/Ho_Chi_Minh`. Production workbook remains staff-operated Google Sheets.

## Current code in this branch

- `apps/api/app/modules/inventory/daily/shelf_life.py`: time-zone-aware, exact 96h prepared expiration (Q40), shortest applicable safety deadline, opened-package unknown time quarantine (Q37/Q45). Pure read-only policy.
- `apps/api/app/modules/inventory/daily/catinat_policy.py`: validated count parsing, approved whole-to-loose package opening (Q31), pending/confirmed/short transfer accounting (Q32), same-batch container moves only (Q36), explicit staff physical counts, manager separation and expiration/discrepancy gates (Q02/Q16/Q19/Q27/Q39/Q44). Pure policy; does **not** itself persist confirmation/approval or constitute evidence of RBAC.
- `apps/api/app/modules/inventory/daily_sheet/catinat_semantics.py`: Q18 contextual `8+4` AM/PM usage, seeded-vs-seedless lemon, Q11 container quantities and unverified batch dates.
- `apps/api/app/modules/inventory/daily_sheet/catinat_closing.py`: deterministic **computed-unverified** missing-closing suggestions, strict sheet layout/date guard, read-only, defaults to block missing movement evidence; no physical close approval.
- `apps/api/app/modules/inventory/daily_sheet/catinat_preview.py` and `router.py`: tenant-bound `GET /api/inventory/daily-sheet/catinat/closing-preview?business_date=YYYY-MM-DD` requiring `inventory.read`. Reads only configured workbook and bounded rows. Returns `mode=read_only_unverified` / `auto_fill_allowed=false` and suggestions/blocked, never writes or signs.
- New tests `test_catinat_policy.py`, `test_catinat_shelf_life.py`, `test_catinat_semantics.py`, `test_catinat_closing.py`, `test_catinat_preview.py`.

## Additional P2 foundation (not yet exposed to employees)

- `database/migrations/versions/0169_inventory_catinat_signed_shifts.py` and `InventoryCatinatSignedShiftModel`: one authoritative signed snapshot per tenant + business date + AM/PM, snapshot/source digests, confirmed actor and typed physical counts.
- `daily/catinat_signed_shift.py`: authenticated-actor **caller contract**, mandatory server-derived fresh sheet hash callback, atomic database transaction and replay validation; conflicting second close is refused. Pending transfers and unresolved discrepancies are always blocked until a **separate trusted approval workflow** is implemented. Does not write Sheets or perform carry-forward.
- `test_catinat_signed_shift.py`: SQLite persistence/idempotency/source-mismatch tests. **This is not yet a usable employee close endpoint**; caller RBAC, manager approvals, source-to-cell mappings and UI still have to be integrated.

## Hard requirements NOT yet implemented or live-verified

1. **P0/R0**: Live catalog/warehouse mapping audit, missing items/location recovery, production `apply_mode`/reset paths, job queue and persistent retries, backend migration health. Without proof, keep automatic resets/carry-forward disabled for Catinat.
2. **P1/R1**: Convert parsing into authoritative structured, versioned catalog observations. Resolve material variants, conversions, and box identities only with approved evidence. Historical date-only batches have **unknown hours**, not a verified 96h expiration.
3. **P2/R2**: Database migrations/models and authenticated write API for employee count confirmations, separate AM handoff / PM closing, immutable snapshots indexed uniquely by tenant + business date + shift, signed actors and source hashes. Integration must not accept arbitrary `confirmed_by` or `approved_by` from a client payload. Role mapping must enforce 3 roles and reject self-approval on server. Actual waste, loss, expiry and catalog reviews need durable human actions.
4. **P3/R3**: Durable transfer dispatch/receipt events. Legacy `InventoryDocumentCommitter._transfer_expected` currently posts **both** `transfer_out` and `transfer_in` for an approved transfer document. This must not be used as evidence of confirmed Catinat receipt. A migration + receiver-confirm workflow is required before enabling transfer-dependent writes. Persistent box/batch IDs and opening events are also pending.
5. **P3/R4**: Idempotent signed closing to next-day carry-forward, verified previous-day snapshot, conflict-proof Sheets concurrent edit coordination, versioned per-cell source hashes, safe preflight/partial-write readback/recovery. Re-reading just before writing is NOT atomic compare-and-swap; if no suitable exclusive edit window/coordination exists, fail closed.
6. **P4/R5**: Read-only shadow comparison against real shift data, controlled test-copy writeback, Gemini retry/failure-injection tests, UI multi-device QA, rollback rehearsal, explicit approval for deploy. No production code deployment or Google Sheets mutation from this branch without gates.

## Required QA

- Parser: `/` standalone zero but unconfirmed; empty missing; decimal locale ambiguities; AM/PM vs named lemon variants vs containers; exact batch time known vs unknown; erroneous unit positions suggestion only.
- Accounting: whole-to-loose conservation, no missing factor, no double count when transfer pending, no inventory drift when moving boxes, shortest expiration deadline, all losses/waste explicit.
- Signatures: AM vs PM immutable, one snapshot per day+shift, double click/retry idempotent, zero must still be explicitly confirmed, manager self-approval refused, no correction by mutation after closing.
- Google: staff concurrent edit, disconnection, timeout after accepted write, manual edit after close, overnight previous-day unclosed -> next day provisional, never overwrite staff edits.
- Gemini: 429/unavailable queued with controlled retry; unresolved identity or number blocks rather than AI guessed write.

**Release stop criteria:** any missing actor identity, unsigned physical count, missing timestamp/approved unit conversion, unconfirmed incoming transfer, unresolved expired stock, legacy auto-write path bypassing authorization, or unverified production Sheets write/readback. No implicit approval and no silent fill of blank cells.
