# Inventory business audit — 21 October 2026

Status: **implemented and locally verified; final role/viewport evidence and visual baseline review pending; not deployed**.

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
| Frontend tests | **814 passed, 146 files** | Completed client test run. |
| Analyzer tests | **86 passed** | Completed analyzer regression run. |
| Frontend typecheck | **PASS** | Completed local check. |
| Frontend build | **PASS** | Verification build; final committed release artifact is still pending. |
| Real PostgreSQL concurrency scheduling | **NOT RUN** | Controlled SQLite committed-session regressions do not prove PostgreSQL lock scheduling. |
| Independent CI / PR checks | **NOT VERIFIED** | No CI result or PR completion is asserted by this audit. |

### Final browser evidence

The development-server run captured all 30 states with successful steps and zero overflow, unnamed controls, missing alt text, or small targets. Strict/full mode nevertheless returned exit 2 for an aborted `/api/v1/auth/identity` request per viewport; this is not recorded as a passing strict gate. The compiled release rerun is pending.

| Role | Phone | Tablet | Desktop |
| --- | --- | --- | --- |
| Staff | Functional states pass; compiled strict run pending | Functional states pass; compiled strict run pending | Functional states pass; compiled strict run pending |
| Manager | Functional states pass; compiled strict run pending | Functional states pass; compiled strict run pending | Functional states pass; compiled strict run pending |

Use `ui-qa-inventory-catinat-staff-plan.json` and `ui-qa-inventory-catinat-manager-plan.json` with their matching fixtures. Earlier screenshots or a permission-only empty state are not substitutes for the final candidate/count states.

The default UI Auto-Fix path exhausted its **two repair attempts**. Stale fixture/state mappings were corrected, and test-environment setup resolved the font-installation 403 failure. The accepted older visual baseline still blocks the final visual gate for the intended UI changes. The candidate-table grid now constrains its column width while preserving internal table scrolling. Development-server metrics show zero overflow at all three viewports; the final compiled rerun remains pending. **No visual baseline has been automatically accepted.** Any baseline proposal must follow explicit user review under `AGENTS.md` and the existing UI workflow.

## Production boundary and remaining uncertainty

No production deployment, candidate approval, database migration, stock posting, or Google Sheets write was performed by this audit. This patch adds **no dependency or migration** and changes **no secrets or credential configuration**.

Read-only public checks returned `/live: ok` and `/ready: ready`, with PostgreSQL and Elasticsearch available. `/version` advertised `1a442ba360ab849af7f3c6b6585563bfdb596131` / version `0.4.0`. The version handler can fall back to configured `BUILD_COMMIT`, so that value is not conclusive immutable-release provenance. Readiness validates DB connectivity (`SELECT 1`), not the current Alembic head. **Current live Alembic revision and unambiguous backend release provenance remain unverified.** No protected-path or credential workaround was attempted.

The candidate's existing release lineage includes earlier migrations through Catinat 0176. A patch without new migrations does not by itself establish live schema compatibility. Keep the new Q47 guard in force.

## Packaging and release gates

Follow [VPS deployment](VPS_DEPLOYMENT.md) after the remaining gates pass:

1. Commit the completed source, tests, fixtures, and current audit evidence as **S**; exclude test-generated dist and runtime links.
2. Rebuild frontend at **S** and complete final compiled staff/manager browser evidence.
3. Record those results in the audit documentation, then commit generated dist and documentation separately as **R**. Produce the visual baseline proposal at that final HEAD and obtain explicit review before accepting it. S and R are placeholders, not assigned commit hashes. Do not ship a verification build whose metadata points to the older base while frontend source has changed.
4. Recheck concurrent `origin/main`, preserve its work, obtain actual CI evidence, and use a clean release checkout at **R**.
5. Establish live backend schema compatibility before selecting migration behavior. The official backend `--no-migrate` option does not prove compatibility and still permits normal service/timer activation.
6. Preflight the exact official release command. For backend rollback readiness retain at least two releases; the current script's default retention is one. Verify live revision, health, and authenticated read-only production UI smoke after any eventual deployment.

The official scripts remain `scripts/deploy-cam-frontend.sh` and `scripts/cam-rebuild-backend.sh`. Standing authorization covers a normal reviewed release once its gates pass; it does not authorize baseline acceptance, unsafe migrations, or bypassing CodeLocal policy. Backend rollback does not downgrade the database. **Source/dist commits and deployment are forthcoming, not completed.**
