# Mobile UI refresh — 2026-10-11

## Scope and design

This round reworks the **shared mobile experience** without changing business logic, permissions or desktop/tablet workflows.

- All signed-in workspace routes share one phone-specific bottom rail. It has a **visible icon and short text label** per destination, up to four primary destinations, with an accessible **More** sheet for remaining permission-allowed destinations.
- The More sheet uses backdrop and Escape dismissal, a focus loop, scroll lock while open, active-route indication and 44px close targets. Navigation uses the same permission gates as the existing desktop sidebar; no protected routes are exposed by the new rail.
- On phones, form controls receive a readable font floor to prevent iOS input autozoom, operational tables scroll **inside their panels** rather than pushing the entire document, and horizontal stage/section tabs remain accessible by touch.
- Shared page titles, actions, bottom-floating controls, campaign editor, inventory drawer and Asset Explorer sidebar account for device safe-area and the fixed bottom rail.
- RRUGC small-text density is lifted in Stage tabs, source tables, buttons and action areas without changing the output/Stage workflow. Original-source images, results, modals, job queues and backend requests are not changed.
- Desktop (1025px and above) and tablet navigation (681–1024px) retain their previous structure.

## Tested routes

| Fixture-backed route | Phone 390 x 844 | Small phone 320 x 700 |
| --- | --- | --- |
| Asset Explorer | PASS | PASS |
| Review Board | PASS | PASS |
| Realistic Review UGC | PASS | PASS |
| AI Operations | PASS | PASS |
| Inventory | PASS | PASS |
| Access Management | PASS | PASS |
| Video Generation | PASS | PASS |
| Job Queue | PASS | PASS |
| Public Review / Shared | PASS | PASS |

Validation: `make ui-qa-headless` with explicit nine-profile list and `CAM_UI_VIEWPORTS=mobile,mobileSmall`. The matrix is functional/DOM/quality QA on a local fixture preview, not authenticated production. Visual baseline comparisons are **skipped** by the functional gate: old screenshot baselines should only be revised after reviewing actual design differences. Public Review has no authenticated workspace bottom rail and keeps its existing viewer layout.

Automated navigation tests cover route/permission mapping, More grouping, selected tab state, Escape and backdrop dismissal. New JS/CSS is fully excluded from the desktop viewport where appropriate.

## Follow-up acceptance

Before a production publish:
1. Review screenshots for all changed mobile baselines, approve only valid intended differences.
2. Smoke-test a real account on iOS Safari and Android Chrome; Playwright fixture tests cannot validate OS keyboard behavior, touch gestures in third-party browser engines or live authorization.
3. Test representative Stage 0–5 modals with real large result sets (masonry, skeleton, lazy loading and drag scrolling) in authenticated staging; fixture screenshots do not cover every data scale.
4. Deploy frontend **only on an explicit production-deployment request** through the reviewed frontend deploy script, with build provenance and post-deploy production smoke.

No API, database, worker, storage, image-generation skill, or production authorization changes are included here.
