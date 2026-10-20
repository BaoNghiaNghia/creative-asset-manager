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

## Q44 exception approvals (new in this iteration)

- Migration `0170_catinat_approvals`: tenant-scoped `inventory_catinat_approval_requests` and append-only `inventory_catinat_approval_events` with per-tenant idempotency key, proposal hash, immutable requesting actor, decision actor/time and decision reason.
- `daily/catinat_approvals.py` safely queues five categories only: discrepancy, catalog mapping, package conversion, post-close adjustment and pending-transfer exception. Self-approval, repeated conflicting decisions, cross-tenant access, duplicate key with changed payload and payload over 8KB are rejected. **Approvals DO NOT perform Sheets writes, adjust stock, override food safety or change signed closing snapshots.** The downstream evidence-bound application workflow remains pending.
- `daily/catinat_approval_router.py` adds authenticated `/api/inventory/daily-sheet/catinat/approvals` list/read/create/decision endpoints. Create requires `inventory.review` or `inventory.control`; list/read require `inventory.read`; decision requires **both** `inventory.finalize` permission and a tenant-admin/approved-manager role (or platform admin). Actor is taken exclusively from authenticated `principal.user_id`. Account-specific staff/shift-leader role grants must be audited during rollout.
- `InventoryCatinatApprovals.tsx` and Inventory navigation add a Vietnamese exception work queue for staff and managers. Generic JSON evidence is an interim operator UI, not final row-aware physical-count confirmation.
- Tests in `test_catinat_approvals.py` cover database isolation, audit events, idempotency, manager role and cross-tenant protections. **No production migration or deployment has been run.**

## Q02/Q03-Q05/Q27/Q34 evening physical-readiness screen (new)

- `daily_sheet/catinat_shift_readiness.py`: deterministic read-only inspection of the exact **three separate physical closing layouts** in the live workbook: `KHO PHA CHẾ L/M`, `PHÒNG PHA CHẾ H/J`, `KHO PHÒNG RANG J/L`. Read-only report checks preparation batches missing verified time for strict 96-hour expiry.
- Verifies the business date in all four sheets, warehouse closing headers, bounded tab size, missing/invalid physical counts, missing stock unit, unidentified quantity rows and unverified prepared-batch timestamps. Numeric 0 and `/` are accepted as quantity 0 **but never as staff confirmation**.
- `GET /api/inventory/daily-sheet/catinat/shift/readiness?business_date=...` requires `inventory.read`; reads only the tenant-configured workbook and returns `source_hash`, `items` and `blockers`, with `staff_confirmation_available=false`, `automatic_carry_forward_allowed=false`, `sheets_writes=0`.
- `InventoryCatinatShiftReadiness.tsx` adds Vietnamese **Kiểm kê ca** screen listing each corresponding Google cell, missing count and blockers; it is deliberately non-mutating.
- This screen **does not yet establish completeness against a manager-approved catalog, prove pending transfers are reconciled, register actual discarded expired batches, or sign the immutable shift snapshot**. Do not allow official closing/carry-forward based on this read-only roster, and do not mistake the hash for atomic Sheets locking.
- Dedicated tests `test_catinat_shift_readiness.py` and `InventoryCatinatShiftReadiness.test.tsx` cover missing/slash/zero cells, all three layout mappings, dates, changed sources, authorization, and the read-only UI/API contract.

## Q02/Q16/Q27/Q34/Q44 — per-cell employee confirmations (new)

- Migration `0171_catinat_counts` adds **append-only** `inventory_catinat_count_confirmations`, unique per tenant/day/evening/sheet/cell/source-hash. Each record retains authenticated staff identity, original raw count, parsed quantity, unit, product label, source workbook, and confirmation time.
- `daily/catinat_count_confirmations.py` binds a user's explicit cell selection to the **server-read, tenant-configured Google Sheets** roster and matching source hash. The user cannot submit the quantity, catalog identity, or actor as trusted evidence. Blank/ambiguous fields are blocked; slash and numeric zero require the same explicit confirmation as other numbers.
- Duplicate clicks with the same actor/source are idempotent. Another actor cannot silently claim the same cell version. A newer Sheets source hash invalidates old confirmations for that current state without mutating old audit rows.
- `daily/catinat_count_router.py` exposes authenticated GET/POST `/api/inventory/daily-sheet/catinat/shift/count-confirmations`. POST requires reviewer/control/finalize permission **and** an employee, shift-leader or manager role. Actual actor identity is taken from the login session. Sheets errors and stale revisions fail closed.
- `InventoryCatinatShiftReadiness.tsx` now allows staff to select only valid, unconfirmed cells, explicitly attest personal physical counting, and save up to 50 cells per submission. Refresh compares hashes from two independent reads and refuses mixed revisions.
- **Important:** these events confirm individual physical cells, **not** the official shift close. The signed closing path still needs complete approved material catalog, verified batch expiry/waste/discrepancy, transfer reconciliation, authenticated authorization and a safe write/lock protocol. Do not enable carry-forward from per-cell confirmations alone.

## Q32 durable in-transit handoff / receipt tracking (new)

- Migration `0172_catinat_transfers` adds `inventory_catinat_transfers` (tenant-scoped unique handoff reference, approved item and both active locations, unit, dispatch/receipt quantities and status), plus append-only `inventory_catinat_transfer_events` with receipt event idempotency.
- `daily/catinat_transfers.py` records **dispatch -> pending_receipt -> partial_receipt -> received**, each receipt explicitly confirmed by an authenticated receiving staff member. Both dispatch/receipt duplicate calls with the same idempotency keys are safe; conflicting replays, over-receipt, invalid units, inactive catalog/locations and cross-tenant access are rejected. Transfer status remains pending until all quantity is acknowledged. Stale business dates are preserved in records, receipt time is captured separately.
- `daily/catinat_transfer_router.py` exposes tenant-aware GET/list, POST dispatch and POST /{id}/receive. `InventoryCatinatTransfers.tsx` provides a Vietnamese handoff list, pending-quantity display, form for an approved material and locations and explicit receiver checkboxes. **This is an event evidence tracker only**: `mode=handoff_tracking_only`, `ledger_posted=false`, and `sheets_writes=0`. It DOES NOT subtract source or credit destination authoritative stock yet. Reconcile these audit records with actual inventory transactions before enabling ledger posting, otherwise the legacy `_transfer_expected` double-post path remains dangerous.

## Closing preflight across current signed counts, transfers and exceptions (new)

- `daily/catinat_close_preflight.py` aggregates the currently valid source-hash-matched **staff physical count attestations**, expiring data/blank cells in four Sheets, all daily manager approval requests, outstanding in-transit transfers, unposted transfer ledger events and existing immutable shift snapshots. Existing signed snapshots are never modified if Sheets later changes. An approved manager exception is not automatically treated as a reconciled stock transaction.
- GET `/api/inventory/daily-sheet/catinat/shift/closing-preflight?business_date=...` requires `inventory.read`; the `Kiểm kê ca` screen cross-checks 3 fresh read hashes and displays remaining blockers. All returned documents have `ready_to_sign=false` until the missing policy gates below are built and demonstrated. No POST close or auto carry-forward has been enabled.
- QA: `test_catinat_transfers.py` and `test_catinat_close_preflight.py` exercise tenant isolation, pending/partial/received state, replay/over-receipt, manager approval evidence, stale source hash, and no Sheets write paths.

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
