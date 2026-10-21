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

## P0 follow-up — tenant catalog coverage diagnostics (21 Oct 2026)

The **read-only** evening closing preflight now separately counts active catalog materials and
active storage locations for the authenticated tenant. The response includes
`catalog_coverage: { active_item_count, active_location_count, certified: false }`.
A tenant with zero active materials or zero active locations receives explicit
`inventory_items_missing_for_tenant` / `inventory_locations_missing_for_tenant`
blockers, localized in the Vietnamese staff view. Cross-tenant catalog entries
never count toward readiness. Even non-zero counts do **not** certify name,
variant, batch, unit conversion, or per-sheet location mapping.

In the independently restored 20 Oct production-source snapshot, the checked
row counts were 14 `inventory_items` and 0 `inventory_locations`; therefore
the current data is **not** ready to sign or carry forward. Staff/manager
mapping approval must precede actual writes. No catalog records are
auto-created, no physical counts are inferred and no Sheets values are changed.

A separate 21 Oct private read-only verifier recorded
`/root/catinat-db-rehearsal-20261020/migration-0173-proof.json`: the
isolated loopback PostgreSQL clone reports one `0173_catinat_branding_merge`
Alembic revision, 188 total tables (180 restored + 6 Catinat + 2 Branding),
unchanged source row counts, scoped Branding constraints and validated dump
checksum. This evidence **does not claim a fresh migration run**; it audits a
clone already found at revision 0173. Production migration was not run.

## Q01/Q14/Q44 — read-only catalog and warehouse mapping candidate audit (21 Oct)

- `GET /api/inventory/daily-sheet/catinat/catalog/mapping-preview?business_date=...` requires `inventory.read` and obtains the configured tenant workbook through the existing read-only evening readiness loader. The caller cannot submit a workbook, tenant, SKU or material mapping. Responses include the authoritative source hash and source cell identities for all three physical warehouse tabs.
- Exact Unicode NFC + trimmed/collapsed whitespace + case-insensitive **name candidates**, separate whole/loose unit checks, active items and exact warehouse names are evaluated within **one tenant**. No fuzzy/AI guesses, invented unit conversions, SKU equivalence, cross-tenant fallback or inferred missing warehouses.
- Records distinguish no item, duplicate item, missing unit, mismatched unit and missing/ambiguous warehouse. Even a perfect name/unit/location candidate is `uncertified` until a manager verifies canonical material identities and location assignments; this endpoint does **not** create catalog data or approve exceptions.
- The Vietnamese `Kiểm kê ca tối` page shows all three location states, total candidate/missing cells and per-cell review reasons. It cross-checks the same `source_hash` as readiness, count confirmations and closing preflight. If any fresh read differs it fails closed and clears the screen.
- `ready_to_sign=false`, `automatic_carry_forward_allowed=false`, `inventory_writes=0`, `sheets_writes=0`. Real signed shift closing remains disabled. No production database or Google Sheets mutation.
- Test: `test_catinat_catalog_mapping.py` covers tenant separation, duplicate candidates, missing locations, unit mismatch, stale date, untrusted source, duplicate cell and authenticated GET-only access. The Catinat UI test covers read-only route and Vietnamese review states.

**Still required before enabling ledger/carry-forward:** manager-approved persistent mapping with versioned source evidence; explicit physical warehouse identities; approved conversions and package/batch/expiry/waste checks; transactional transfer ledger posting; human close sign-off and conflict-proof Sheets writer protocol.

## Q44 — direct source-bound catalog mapping review requests (21 Oct)

- The evening count page's Catalog Mapping table now lets a staff reviewer select a specific Sheets warehouse + cell and **explicitly** send a reasoned `catalog_mapping` request to the existing append-only approval workflow, instead of manually composing JSON evidence.
- The UI refetches the tenant-bound catalog report before submission and **blocks** changed source fingerprints, row candidates or warehouse candidate identities. The request retains workbook source hash, sheet/cell, source material/form/unit and current candidate item/location ID arrays. **No canonical ID is auto-selected and `mapping_certified=false`**.
- The existing POST `/catinat/approvals` requires `inventory.review` or `inventory.control`. A separate tenant-admin/manager role plus `inventory.finalize` is required for decisions, and actors cannot approve their own requests. Ordinary viewers receive a permissions explanation; no implicit grants.
- A successful submission only records an **audit request**, with an idempotency key retained for network retries. Manager approval alone does not create an inventory location, certify a mapping, sign a shift or write Google Sheets.
- UI regression tests cover the exact evidence shape, uncertified values, source-linked row checks and the existing read-only/approval/transfer screens. Further manager-approved canonical mapping persistence and stock-ledger integration remain **STOP gates**.

## Q44 follow-up — versioned catalog certification evidence (0174, 21 Oct)

- Additive migration `0174_catinat_catalog_cert` adds `inventory_catinat_catalog_certifications`. Unique approval request per tenant and unique version per `tenant/workbook/warehouse tab/cell`; foreign keys bind each certification to the *same tenant's* approved request, active catalog item and active location. Records are append-only evidence with required version, source hash, original material/form/unit, reviewer and manager identities and reason. DB enforces `status='evidence_only'`.
- Manager-only `POST /api/inventory/daily-sheet/catinat/catalog-certifications` requires **both** `inventory.finalize` and an approved manager/admin role. A manager may certify only a previously **approved** catalog-mapping request that they personally decided, never the requesting employee's own proposal. It re-fetches configured tenant source and candidate report, verifies original source hash + workbook ID, exact matched row/location, proposal digest, approved item/unit, active location, and zero ambiguity before writing one immutable evidence record. Conflicting replay is blocked.
- `GET /api/inventory/daily-sheet/catinat/catalog-certifications?business_date=...` requires `inventory.read` and returns version history. The Vietnamese manager-review UI allows a separate explicit certification note after approval for unambiguous exact candidates only.
- A certification **does not activate mappings**, fill missing catalog entries, invent locations, authorize transformations, write a Sheets cell, post stock/transfer ledger, carry stock forward or sign any shift. No service reads these records as closing authority. `mapping_activated=false`, `ready_to_sign=false`, `inventory_writes=0`, `sheets_writes=0`. Unknown, duplicate, mismatched-unit or missing-location candidates stay blocked. Manager-approved non-exact mapping proposals require a separate future canonical mapping/exception workflow.
- Migrations must be tested on a *fresh isolated PostgreSQL clone* of current production before synchronized promotion; do not mutate the previous 0173 rehearsal/proof DB and do not treat SQLite unit tests alone as release authorization.

## Q48 — read-only three-area manager evidence coverage (21 Oct)

- New inventory.read-protected GET `/api/inventory/daily-sheet/catinat/catalog-certifications/coverage?business_date=YYYY-MM-DD` joins the tenant's current authoritative sheet hash, exact mapping candidates and the existing immutable `0174` certification ledger **in read-only mode**.
- Coverage reports each of the three fixed physical areas (`KHO PHA CHẾ`, `PHÒNG PHA CHẾ`, `KHO PHÒNG RANG`), their exact-name warehouse state, source cell count, manager evidence count and unresolved cell count. The UI exposes this progress from the evening count page with a link to the **existing** Inventory `Nguyên vật liệu → Danh mục kho` editor. The dashboard never creates warehouses or guesses mapping.
- A certification is counted only when its **tenant, workbook, business date, source fingerprint, sheet and cell** match the current read-only preview and its item/location identity, name, form and unit still agree with the one unambiguous exact candidate. The highest stored version for a cell counts only once. Stale evidence, ambiguous locations, missing materials, duplicate sheet cells, wrong units and missing areas cannot pass by accident.
- This is **coverage of human-certified evidence, not active stock mapping**. The response hard-sets `mapping_activated=false`, `ready_to_sign=false`, `automatic_carry_forward_allowed=false`, `inventory_writes=0` and `sheets_writes=0`. The official closing preflight remains fail-closed and unchanged.
- Only the existing protected warehouse-registration editor may create canonical locations **after physical manager verification**. There is no automatic seeding of the three warehouses. Requires real manager-reviewed locations/materials before a Catinat operator can produce signed certification evidence.

## Q31 follow-up — read-only package conversion evidence (21 Oct 2026)

- New `GET /api/inventory/daily-sheet/catinat/catalog-certifications/conversion-review?business_date=...` requires `inventory.read`; it uses the server-bound tenant workbook, validates the same source hash, enumerates all three known stock areas and never accepts client-supplied item/location conversion parameters.
- Per source cell, this review distinguishes loose stock whose unit matches the catalog from whole-package stock that **requires an independently manager-approved package conversion record**. The legacy `inventory_items.conversion_factor` value alone is explicitly **not** authorization.
- For whole packages with a distinct loose/base unit, only an existing `InventoryMaterialPackageConversionModel` record with matching normalized whole-unit package key, explicit nonempty `approved_by`, matching canonical target unit, positive finite factor, and agreement with the catalog's factor is marked `approved_conversion_evidence_found`. Missing/unapproved/conﬂicting evidence is shown as a problem, never silently converted.
- Results are informative evidence only: `conversion_applied=false`, `ready_to_sign=false`, `automatic_carry_forward_allowed=false`, `inventory_writes=0`, `sheets_writes=0`. The Catinat evening page cross-checks the conversion report hash against its existing fresh source-bound count, mapping and manager-certification reports.
- Existing `package_conversion` manager approval requests are **not** equivalent to durable approved conversion factors; the **separate reviewed conversion writer, physical batch/expiry/waste ledger, transfer stock posting, and concurrent Sheets write-protection** remain STOP gates before actual closing/carry-forward. No 0175 migration or automatic factor seeding is introduced by this change.

## Q32/Q40 — shelf-life, waste and transfer safety triage (21 Oct)

- New authenticated, tenant-bound **read-only** GET `/api/inventory/daily-sheet/catinat/shift/safety-triage?business_date=...` combines the authoritative configured workbook's evening source fingerprint with report-row date-only preparation hints and the tenant's transfer handoff/receipt table (all transfers through the selected business date).
- For each physical stock cell, the absence of durable batch identity, exact verified opened/prepared time, product-specific rule and independently verified disposal means expiry and waste remain **uncertified**; a zero count or date-only text does NOT prove disposal or 96-hour shelf-life safety. A 96-hour deadline must be computed from an exact time (and the shorter product safety deadline if applicable) in a future separately approved batch workflow. This triage deliberately does not attempt to infer that timestamp.
- Transfer summary reports dispatched, received, remaining-in-transit, pending receipt and the **separate** `ledger_posted` state; a fully received handoff does NOT mean its ledger has been posted. It rejects inconsistent receipt status/quantity, prevents cross-tenant reads, and does NOT credit the destination stock.
- The `Kiểm kê ca tối` dashboard reads triage along with count confirmations, manager mapping evidence, conversion audit and closing preflight. It rejects mismatched source fingerprints and shows Vietnamese per-cell batch/waste concerns, date-only preparation hints and historical outstanding transfers. The interface has no mutation controls for this audit.
- The report explicitly returns `batch_expiration_and_waste_certified=false`, `transfer_ledger_reconciled=false`, `ready_to_sign=false`, `automatic_carry_forward_allowed=false`, `inventory_writes=0` and `sheets_writes=0`. **No new migrations and no transfer posting, expiry certification, disposal accounting, Google Sheets writes or signing** were implemented. Subsequent work must add actual operator and manager-approved batch/disposal evidence and atomic transfer-ledger posting before relaxing any close gate.

## Q40/Q45 — staff batch timestamp evidence and manager waste proposals (21 Oct 2026)

- Additive Alembic **0175_catinat_batch_evidence** extends the append-only approvals kind check with `waste_disposal` and creates tenant-scoped `inventory_catinat_batch_evidence` (unique idempotency key, day, configured workbook, source hash, physical sheet+cell, material/form/unit, lot reference, prepared/opened timestamp, optional label-expiration timestamp, staff attestor, note, immutable payload SHA-256). The row is **staff_attested_unverified**, *not* a verified safe-to-use batch. Downgrade fails closed once evidence or disposal requests exist.
- Staff with Inventory review/control/finalize permission **and a verified staff role** may POST `/daily-sheet/catinat/batch-evidence`. It **always rereads the configured tenant workbook** and verifies the day, source hash and unique physical cell. Inputs require a real manually-entered lot reference, exact offset-aware timestamp (Vietnam frontend captures `UTC+07:00` explicitly), non-empty note and idempotency key; stale Sheets, unknown/duplicate cells, missing unit and future timestamps fail closed. `GET /daily-sheet/catinat/batch-evidence` requires `inventory.read` and marks old-source evidence as stale.
- The staff UI under `Kiểm kê ca tối` captures one named lot per actual source cell, the exact Vietnamese preparation/opening time and optionally an expiry **actually read from packaging**. It explicitly warns that 96 hours is a *maximum preparation interval only* and that other shorter product/batch policies remain unverified. Stored evidence cannot change existing counts or retroactively make a lot safe.
- Staff may explicitly POST `/daily-sheet/catinat/batch-evidence/{batch_id}/request-disposal` with the specific manager-review quantity and reason. The service validates the tenant, recorded batch, current workbook/source hash, item/form/unit and positive proposed quantity, then creates an immutable `waste_disposal` **proposal** in the existing manager approval queue. Calling the generic approvals POST with `waste_disposal` directly is blocked to prevent forged source-less requests. The existing manager-only decision endpoint prohibits self approval. A manager decision **does not certify physical disposal or post a waste transaction**.
- API and UI responses explicitly include `expiry_certified=false`, `disposal_performed=false`, `ready_to_sign=false`, `inventory_writes=0`, `sheets_writes=0`. Official close, stock ledger and Google Sheets carry-forward remain disabled.
- Before any actual waste deduction: independently verified lot identity and product shelf-life policy, physical destruction/return proof, manager sign-off bound to exact quantity/source, atomic ledger posting with idempotence and concurrency protection, and a conflict-safe Google Sheets writer must all be implemented and tested. The active production database is **not migrated** by this PR review.

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