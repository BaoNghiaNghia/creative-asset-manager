# Inventory business audit — 21 October 2026

Status: **implemented; compiled staff/manager browser verification passed; visual baseline review and production compatibility gates remain open; not deployed**.

This bounded audit continues the approved [Catinat Q01–Q47 implementation](INVENTORY_CATINAT_Q47_IMPLEMENTATION.md). It repairs retry, review, and stale-state behavior. Official shift signing, stock-ledger posting, and Catinat Google Sheets carry-forward remain disabled until their separate business and concurrency gates are satisfied.

## Implemented behavior

- **Conflicting evidence requires review.** Stale source/readback evidence is no longer classified as a retryable technical failure. Scheduler recovery cannot turn old stale-evidence jobs back into retries or mark them completed from an older verified snapshot. Active worker leases are preserved. Genuine network/rate failures and recovery after a timeout following an accepted write retain their existing retry behavior.
- **Legacy Catinat writeback is blocked.** Both automatic carry-forward and manual apply reject the legacy writer with `catinat_signed_close_required` when exact Q47 warehouse titles are present in tenant source configuration or observed source/target metadata, including partial layouts. The authoritative identities remain `KHO PHA CHẾ` L/M, `PHÒNG PHA CHẾ` H/J, and `KHO PHÒNG RANG` J/L. Registry totals do not identify physical warehouses. A stored signed row alone cannot unlock this writer. Non-Q47 legacy workbooks remain compatible.
- **Bulk material review is explicit and atomic.** The new bulk route requires a manager/admin role plus Inventory finalize permission. Tenant and actor come from the authenticated principal. Complete selected IDs, count, exact stored evidence hash, unique active catalog match, and explicit compatible unit evidence are checked before any group is approved. Identical retries return the existing result; changed actors, selections, or evidence conflict. This approves internal catalog associations; it does not certify a physical close, create conversion factors, or post stock.
- **Candidate audit data survives competing actions.** Equivalent-group and single-item approvals acquire candidate/catalog locks in a consistent order. Approve/reject/ignore reject stale non-pending candidates; queue updates refresh locked ORM state before merging pending context. Evidence refresh binds its provider read to the original tenant/source/sheet/row/name/status/context, then locks and revalidates every pending row before writeback. An intervening approval or source/context change fails without erasing `approved_by` or `bulk_approval`. The missing `select` import used by reject/ignore was also corrected.
- **UI review and request lifecycle are safer.** Bulk capability follows server authorization. Source/date/request changes invalidate stale review selections and dependent confirmations; late responses cannot silently restore obsolete state. Staff and manager fixtures exercise the actual candidate and physical-count flows.

The implementation is concentrated in Inventory `daily/carry_forward.py`, `daily/scheduler.py`, `daily_sheet/service.py` and routers, `materials.py`, `material_evidence.py`, the grouped-candidate reader, and their API/UI tests.

## Recorded verification

These are completed local checks, not a claim of CI success or a final release SHA.

| Check | Recorded result | Evidence / scope |
| --- | --- | --- |
| Inventory API regression suite | **820 passed** | Finished CodeLocal terminal record `1792592299336593568`, exit `0`, duration `138843 ms`; pytest cache contains 820 node IDs and `lastfailed={}`. |
| Frontend tests | **814 passed, 146 files** | Includes request/evidence regressions. The final grid-width and button-class presentation changes were subsequently verified by the build and compiled browser runs. |
| Analyzer tests | **86 passed** | Completed analyzer regression run. |
| Frontend typecheck | **PASS** | Completed local check. |
| Frontend build | **PASS** | Built at source `955af39383ba80ce0680f428c786cb1191aef8b9`; `build_utc_timestamp=2026-10-21T14:35:32.689Z`, Vite 8.43 s. Existing large-chunk advisory only. |
| Real PostgreSQL concurrency scheduling | **NOT RUN** | Controlled SQLite committed-session regressions do not prove PostgreSQL lock scheduling. |
| Independent CI / PR checks | **NOT VERIFIED** | No CI result or PR completion is asserted by this audit. |

### Final browser evidence

Both role plans passed in strict/full mode against the compiled preview at `http://127.0.0.1:4183/inventory`, with build metadata bound to source `955af39383ba80ce0680f428c786cb1191aef8b9`. Staff exercises six states per viewport; manager exercises four.

| Role | Phone (390 × 844) | Tablet (768 × 1024) | Desktop (1440 × 900) |
| --- | --- | --- | --- |
| Staff | **PASS — 6 states** | **PASS — 6 states** | **PASS — 6 states** |
| Manager | **PASS — 4 states** | **PASS — 4 states** | **PASS — 4 states** |

All **30 states** have zero viewport overflow, unnamed controls, missing alt text, and small targets. All console/page errors, failed requests, ignored failed requests, bad HTTP responses, and failed steps are empty. Both commands exited `0`.

Evidence, relative to the repository:

- Staff: `apps/client/.ui-qa/inventory-catinat-staff-release/2026-10-21T14-36-21-280Z/report.json`.
- Manager: `apps/client/.ui-qa/inventory-catinat-manager-release/2026-10-21T14-37-18-412Z/report.json`.
- Plans: `ui-qa-inventory-catinat-staff-plan.json` and `ui-qa-inventory-catinat-manager-plan.json`, with matching client fixtures.

These are synthetic role fixtures. The staff scenario stops before saving a count; the manager scenario uses a fixture approval response. Exact submit payload and server authorization behavior are covered by the UI/API regressions. No real physical count, approval, stock, or Sheets write is implied. Native iOS/Android/iPad checks were not run.

The default UI Auto-Fix path exhausted its **two repair attempts**. Stale fixture/state mappings were corrected, and normal local dependency installation resolved the earlier font 403 environment failure. The accepted baseline contains older Inventory state names, so the final visual gate still requires an explicit baseline proposal and user review under `AGENTS.md`. **No visual baseline has been automatically accepted.**

Candidate-table overflow was repaired by constraining the parent grid column and retaining horizontal scrolling inside the table. The compiled three-viewport runs confirm zero viewport overflow. An earlier development-server strict run returned exit 2 for an identity request abort; that run was not claimed as a passing strict gate, and the final compiled reports contain no failed or ignored requests.

## Production boundary and remaining uncertainty

No production deployment, candidate approval, database migration, stock posting, or Google Sheets write was performed by this audit. This patch adds **no dependency or migration** and changes **no secrets or credential configuration**.

Read-only public checks returned `/live: ok` and `/ready: ready`, with PostgreSQL and Elasticsearch available. `/version` advertised `1a442ba360ab849af7f3c6b6585563bfdb596131` / version `0.4.0`. The version handler can fall back to configured `BUILD_COMMIT`, so that value is not conclusive immutable-release provenance. Readiness validates DB connectivity (`SELECT 1`), not the current Alembic head. **Current live Alembic revision and unambiguous backend release provenance remain unverified.** No protected-path or credential workaround was attempted.

The candidate's existing release lineage includes earlier migrations through Catinat 0176. A patch without new migrations does not by itself establish live schema compatibility. Keep the new Q47 guard in force.

## Packaging and release gates

Source commit **S** is `955af39383ba80ce0680f428c786cb1191aef8b9`. The frontend was rebuilt at that exact commit. The accompanying artifact commit packages generated `apps/client/dist` and this final verification record; no frontend source changes were made after S. Runtime dependencies and the temporary backend virtualenv link are excluded from Git.

Follow [VPS deployment](VPS_DEPLOYMENT.md) after the remaining gates pass:

1. Create the governed Inventory visual baseline proposal at the final clean HEAD, present its candidates, and obtain explicit current-user approval before accepting it or rerunning the final visual gate.
2. Recheck concurrent `origin/main`, preserve its work, publish the review branch, and obtain actual CI evidence. This note does not assert a PR or CI result.
3. Establish live backend schema compatibility and unambiguous release provenance before selecting migration behavior. The official backend `--no-migrate` option does not prove compatibility and still permits normal service/timer activation.
4. Preflight the exact official release command from a clean checkout. For backend rollback readiness retain at least two releases; the script's default retention is one. Verify live revision, health, and authenticated read-only production UI smoke after any eventual deployment.

The official scripts remain `scripts/deploy-cam-frontend.sh` and `scripts/cam-rebuild-backend.sh`. Standing authorization covers a normal reviewed release once its gates pass; it does not authorize baseline acceptance, unsafe migrations, or bypassing CodeLocal policy. Backend rollback does not downgrade the database. **No deployment has been performed.**
