# GatorHats Products / Reviews workflow split — 2026-10-11

**UI amendment:** The newer [sidebar submenu design](RRUGC_SIDEBAR_SUBMENU_20261011.md) replaces the in-page two-group selector described below. Products/Reviews are now nested under the global Realistic Review UGC menu; only the three Stage tabs remain inside the page.

This change is frontend-only and follows [Mobile UI refresh](MOBILE_UI_REFRESH_20261011.md) and [Automatic UI workflow](UI_AUTOMATION_WORKFLOW.md).

## Two-tier navigation

| Group | Allowed stages | Default | Label |
| --- | --- | --- | --- |
| GatorHats Products | Stage 0 Analysis Keyword, Stage 1 Keyword Image Generation, Stage 2 Embroidery → 13 Colors | Stage 0 | Product artwork |
| GatorHats Reviews | Stage 3 Pinterest References, Stage 4 Image Generation, Stage 5 UGC Review | Stage 3 | Realistic reviews |

Both groups are visible for users who already have `realistic_review_ugc.read`; existing role enforcement at the route is unchanged. Selecting a group shows only its three stage tabs. The last visited stage for each group is kept during the current page session. Each stage panel retains its original ID, backend jobs, live polling, forms, and data ownership. The global Manage skills and Settings actions are available in both groups.

## Responsive and accessibility acceptance

- Two workflow buttons, consistent vector icons and visible selection, then 3 stage buttons; at phone widths the group buttons remain readable and stage buttons horizontally scroll **inside the tab rail**.
- Keyboard: left/right arrows and Home/End work independently in the workflow group and stage tablists; buttons expose `aria-selected`, `aria-controls`, correct IDs, and visible focus rings.
- Re-clicking an already-selected Stage 3 or Stage 4 does not restart a skeleton without a request — fixed as part of the cross-workflow QA.
- No backend/database/Drive/Skill changes, no output deletion, and no changes to Stage 0–5 processing.

## Verification gates

1. Static/typecheck + dedicated nested-tab navigation tests.
2. Browser QA with source-plan reference fixture on desktop 1440 × 900, tablet portrait 768 × 1024, phone 390 × 844 and small phone 320 × 700. Browser plan explicitly switches to Reviews for Stages 3–5 and Products for Stages 0–2.
3. Full frontend tests and production build with committed `dist/build-info.json` provenance.
4. Independent baseline review; intentional navigation changes do not authorize automatic acceptance of old visual baselines.
5. Explicit frontend-only production deploy using `scripts/deploy-cam-frontend.sh` with read-only post-deploy smoke, then verify live build metadata and relevant services. Do not run backend rebuild for a frontend-only change.

## Outstanding real-device checks

Authenticated real-device Safari/Android checks and real-world dense Stage 0–5 modal data require an operator's signed-in testing device and current production data. Fixture Browser QA and jsdom tests do not demonstrate these. Do not report authenticated production smoke unless a protected storage-state file is actually available to the runner.
