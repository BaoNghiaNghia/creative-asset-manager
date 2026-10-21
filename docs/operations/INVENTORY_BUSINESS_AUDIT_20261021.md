# Inventory business audit — 21 October 2026

Status: **implemented; compiled staff/manager browser verification passed; visual baseline accepted with explicit user authorization and a passing strict acceptance gate; production compatibility and release gates remain open; not deployed**.

This bounded audit continues the approved [Catinat Q01–Q47 implementation](INVENTORY_CATINAT_Q47_IMPLEMENTATION.md). It repairs retry, review, and stale-state behavior. Official shift signing, stock-ledger posting, and Catinat Google Sheets carry-forward remain disabled until their separate business and concurrency gates are satisfied.

## Implemented behavior

- **Conflicting evidence requires review.** Stale source/readback evidence is no longer classified as a retryable technical failure. Scheduler recovery cannot turn old stale-evidence jobs back into retries or mark them completed from an older verified snapshot. Active worker leases are preserved. Genuine network/rate failures and recovery after a timeout following an accepted write retain their existing retry behavior.
- **Legacy Catinat writeback is blocked.** Both automatic carry-forward and manual apply reject the legacy writer with `catinat_signed_close_required` when exact Q47 warehouse titles are present in tenant source configuration or observed source/target metadata, including partial layouts. The authoritative identities remain `KHO PHA CHẾ` L/M, `PHÒNG PHA CHẾ` H/J, and `KHO PHÒNG RANG` J/L. Registry totals do not identify physical warehouses. A stored signed row alone cannot unlock this writer. Non-Q47 legacy workbooks remain compatible.
- **Bulk material review is explicit and atomic.** The new bulk route requires a manager/admin role plus Inventory finalize permission. Tenant and actor come from the authenticated principal. Complete selected IDs, count, exact stored evidence hash, unique active catalog match, and explicit compatible unit evidence are checked before any group is approved. Identical retries return the existing result; changed actors, selections, or evidence conflict. This approves internal catalog associations; it does not certify a physical close, create conversion factors, or post stock.
- **Candidate audit data survives competing actions.** Equivalent-group and single-item approvals acquire candidate/catalog locks in a consistent order. Approve/reject/ignore reject stale non-pending candidates; queue updates refresh locked ORM state before merging pending context. Evidence refresh binds its provider read to the original tenant/source/sheet/row/name/status/context, then locks and revalidates every pending row before writeback. An intervening approval or source/context change fails without erasing `approved_by` or `bulk_approval`. The missing `select` import used by reject/ignore was also corrected.
- **UI review and request lifecycle are safer.** Bulk capability follows server authorization. Source/date/request changes invalidate stale review selections and dependent confirmations; late responses cannot silently restore obsolete state. V4 status uses current lifecycle/stage/invariant evidence; absent lifecycle or reconciliation data cannot imply completion or zero exceptions. Optional catalog diagnostics do not hide an otherwise valid physical-count roster, while required source/readiness/preflight failures still block. Historical sandbox confirmations list the actual selected eligible dates and omissions. Staff and manager fixtures exercise the candidate and physical-count flows.

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

### Recorded compiled browser evidence — source S / artifact R

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

The default UI Auto-Fix path exhausted its **two repair attempts**. Stale fixture/state mappings were corrected, and normal local dependency installation resolved the earlier font 403 environment failure. The current Overview names were then proposed through the governed workflow. Following the user's explicit authorization, the normal acceptance workflow passed; the separate 15-state visual result is recorded below. The earlier 30 compiled strict/full states remain their original, separate evidence.

Candidate-table overflow was repaired by constraining the parent grid column and retaining horizontal scrolling inside the table. The compiled three-viewport runs confirm zero viewport overflow. An earlier development-server strict run returned exit 2 for an identity request abort; that run was not claimed as a passing strict gate, and the final compiled reports contain no failed or ignored requests.

## Production boundary and remaining uncertainty

No production deployment, candidate approval, database migration, stock posting, or Google Sheets write was performed by this audit. This patch adds **no dependency or migration** and changes **no secrets or credential configuration**.

Read-only public checks returned `/live: ok` and `/ready: ready`, with PostgreSQL and Elasticsearch available. `/version` advertised `1a442ba360ab849af7f3c6b6585563bfdb596131` / version `0.4.0`. The version handler can fall back to configured `BUILD_COMMIT`, so that value is not conclusive immutable-release provenance. Readiness validates DB connectivity (`SELECT 1`), not the current Alembic head. **Current live Alembic revision and unambiguous backend release provenance remain unverified.** No protected-path or credential workaround was attempted.

The candidate's existing release lineage includes earlier migrations through Catinat 0176. A patch without new migrations does not by itself establish live schema compatibility. Keep the new Q47 guard in force.

## Packaging and release gates

Historical tested source commit **S** is `955af39383ba80ce0680f428c786cb1191aef8b9`. Historical artifact commit **R** is `d06995e9f435904e2cdec9ed1189c3b34164dad9`, built at that exact source. No frontend runtime implementation changes were made after S. Runtime dependencies and the temporary backend virtualenv link are excluded from Git; the link was removed after testing. Later documentation, disclosure-plan correction, and accepted baseline additions do not alter this compiled implementation. Acceptance commit **A** is `3a2dc90091bca52e313c319c20bdbd3e5b406081`, with parents `b1a9bc23cc3d5dfcf340e0d6cbc8b20690c3ace8` and R. It records the reviewed merge and accepted visual evidence. For each delivery artifact, use its actual build-info.json and Git source/artifact lineage to establish provenance; S and R above remain historical verification references. The 820 API / 814 frontend / 86 analyzer results remain the earlier recorded checks; acceptance did not rerun those complete suites.

Follow [VPS deployment](VPS_DEPLOYMENT.md) after the remaining gates pass:

1. Preserve acceptance commit A and its reviewed lineage. Verify the exact delivery source commit against build-info.json and artifact history during the normal clean-checkout packaging checks; the S/R references above describe the recorded historical verification.
2. Restore authenticated GitHub write access for the CodeLocal host, recheck concurrent `origin/main`, preserve its work, publish the review branch, and obtain actual CI evidence. The normal push failed with exit 128 because no noninteractive HTTPS username/credential was available; no PR or CI result is asserted.
3. Establish live backend schema compatibility and unambiguous release provenance before selecting migration behavior. The official backend `--no-migrate` option does not prove compatibility and still permits normal service/timer activation.
4. Preflight the exact official release command from a clean checkout. For backend rollback readiness retain at least two releases; the script's default retention is one. Verify live revision, health, and authenticated read-only production UI smoke after any eventual deployment.

The official scripts remain `scripts/deploy-cam-frontend.sh` and `scripts/cam-rebuild-backend.sh`. Standing authorization covers a normal reviewed release once its gates pass. Baseline acceptance was separately and explicitly authorized by the user for the proposal below. Unsafe migrations and CodeLocal policy bypasses remain outside that authorization. Backend rollback does not downgrade the database. **No deployment has been performed.**

## Accepted visual baseline and handoff

The self-contained [Inventory review packet](reviews/inventory-review-20261021.html) embeds all 15 accepted baseline PNGs and six representative compiled staff/manager PNGs with their exact hashes. Its SHA-256 is `fdc00bba5fd1fc6af9b1e2b77ccbd0669157b9b57aa827866d98ffc185917af4` (5,197,334 bytes). It records the completed acceptance and uses synthetic QA data. It is a review artifact in this Git-backed project; opening it performs no UI publishing or production action. The original proposal metadata remains embedded as a historical snapshot, alongside the acceptance record.

- Proposal: `20261021T150736Z-b1a9bc23-inventory`, **accepted** following explicit user authorization. The original proposal snapshot retains `review-required` as historical metadata; its 15 candidate PNGs became the accepted baselines without byte changes.
- Review worktree: `/srv/creative-asset-manager-source/.inventory-business-audit-visual-review-20261021`.
- Proposal directory: `apps/client/.ui-qa/baseline-proposals/20261021T150736Z-b1a9bc23-inventory` inside that worktree.
- Historical proposal source HEAD: `b1a9bc23cc3d5dfcf340e0d6cbc8b20690c3ace8` (the pre-audit base). The genuine reviewed merge of R was pending at proposal creation and was committed after acceptance as A `3a2dc90091bca52e313c319c20bdbd3e5b406081`.
- Historical proposal workspace fingerprint: `9927fce889397633d8468a5372aadfbd216d06d306b35ac2d3df64da26572fe3` (also recorded by acceptance).
- Frontend runtime implementation and dist were checked against R with no difference. The accepted additions concern the disclosed Inventory visual manifest and baseline images; they do not add runtime behavior.

### Explicit authorization and completed acceptance

The user explicitly authorized automatic Browser QA acceptance: **“Xác nhận tự động duyệt cho browser qa”**. The normal `ui-visual-accept` workflow exited **0**. Acceptance was recorded at **2026-10-21 15:23:18 UTC / 22:23:18 Vietnam time** (`2026-10-21T15:23:18.683020+00:00`), followed by the report run `2026-10-21T15-23-21-413Z`.

- Exact original record: [inventory-baseline-acceptance-20261021.json](reviews/inventory-baseline-acceptance-20261021.json), **1,277 bytes**, SHA-256 `f0e5ab4d73c584c3e37174a4783307316586575773eb92fd42efb31cf41aab8a`. The original trailing newline is preserved.
- Durable raw report: [inventory-browser-qa-acceptance-20261021.json](reviews/inventory-browser-qa-acceptance-20261021.json), **105,921 bytes**, SHA-256 `2d7a9f8d11d68b314f6787c9e9b59db7f982127f262d5aedb7f970a7bae6ed1c`. Original location: `apps/client/.ui-qa/2026-10-21T15-23-21-413Z/report.json` in the review worktree.
- Result: **15/15 visual matches** in **strict / baseline-accept** mode across five Overview states and three viewports, under unchanged comparator tolerances `pixelThreshold=0.1`, `maxDiffRatio=0.001`. Match is the comparator result; it does not assert that all rerun captures are byte-identical.
- Console/page errors, failed steps, bad HTTP responses, and unclassified request failures: **0**. All 15 states also have zero viewport overflow, unnamed controls, missing alt text, and small targets.
- Classified request aborts: **3**, one per viewport, all `GET /api/v1/auth/identity` with `net::ERR_ABORTED`, permitted by the existing baseline-accept classifier. This is separate from the earlier **30 compiled strict/full states**, whose failed and ignored request arrays are all empty.
- All **15 accepted baseline PNGs retain the exact reviewed hashes**. All **12 pre-existing legacy PNGs remain unchanged**. This acceptance covers five Overview states; it makes no new coverage claim for the four preserved legacy states.

At proposal creation, the clean committed R checkout could not create a proposal because governance requires an actual pending frontend change. The separate review checkout therefore held the genuine reviewed R change against its original base. This preserved R's history without dummy edits or governance changes. After authorized acceptance, that merge was committed as A; the historical base HEAD is not a claim about the deployed implementation.

The manifest extension preserves its original three viewports, four legacy states, all existing PNGs, pixel thresholds, schema, and prior governance metadata. It appends only the five names actually emitted by the current Overview plan: `overview-ready`, `status-hover`, `status-refresh-focus`, `status-refresh`, and `human-attestation-collapsed`. The existing additive-extension validator returned true. The profile repair was accepted through the normal authorized workflow, adding exactly the 15 reviewed PNGs. Its comparison covers the five captured states at three viewports and makes no fresh certification of the retained legacy states.

Final image inspection caught a naming/evidence error in the earlier proposal `20261021T144957Z-b1a9bc23-inventory`: its final step opened a native disclosure and captured it under the name `human-attestation-collapsed`. That proposal is retained only as historical evidence and must not be accepted. The corrected plan requires a closed disclosure before clicking, requires it to be open before pressing Enter, then waits for it to be closed before capture. The existing targeted planner suite passed **14/14**; this is a repeated relevant subset of the analyzer evidence, not 14 additional independent checks. The normal proposal workflow was rerun without resetting Auto-Fix or changing governance, and produced the current 15-image proposal with zero recorded runtime issues. Its fingerprint/candidate hashes passed the existing read-only validator.

All 15 final candidate hashes were verified; 12 images are byte-identical to the earlier run and all three corrected disclosure images were inspected at original resolution. They show the closed panel and keyboard focus, with no new severe clipping or readability issue. Fixed navigation obscures parts of some full-page captures, especially on mobile and the scrolled tablet disclosure state. The affected content appears in other states; these static captures do not prove a persistent scrolling defect. This capture limitation is disclosed in the review packet and was retained in the evidence accepted after explicit user authorization.

The API/client/analyzer results, 30 compiled strict/full states, completed 15-state baseline acceptance, historical proposal metadata, and remaining release blockers are recorded for handoff. The existing CodeLocal connection is working; GitHub push needs a usable noninteractive HTTPS write credential for the host account running CodeLocal. No credential-store inspection, remote change, access-mode change, or authentication workaround was attempted. Current live Alembic revision/provenance and actual CI remain outstanding. All audit-owned preview servers, including the acceptance preview, were stopped. **Baseline acceptance is complete; no production deployment has occurred.**
