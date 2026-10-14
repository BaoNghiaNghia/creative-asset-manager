# Browser QA Reliability — incremental rollout

This rollout extends the existing fixture-first UI process. It does **not** automatically
accept visual baselines, log into production, write production data, or deploy production.

## Phase 1 — reliability and truthful evidence

- `apps/client/scripts/ui-qa.mjs` captures step/navigation failures as
  `results[].issues.stepFailures`, preserves a `*-failure.png` screenshot when
  a page is renderable, and writes `report.json` for these failures. Later
  dependent states are not executed after a failed interaction.
- `apps/client/scripts/ui-qa-assertions.mjs` supports `visible`,
  `hidden`, `text-includes`, `count`, `attribute`, `checked`,
  `no-horizontal-overflow`, and `icon-visible`. Interaction plans can use:
  `"assertions": [{"type":"text-includes","selector":"#status","value":"Completed"}]`.
- The Production smoke plan now targets the correct Stage 5 panel and tests
  read-only navigation in Stages 1, 2, 4 and 5 whenever the caller supplies
  valid authenticated storage state. `production-ui-plan-validation.mjs`
  catches cross-stage selector mistakes before opening Production.

**Production auth requires operator action.** An account with the minimum
appropriate read permissions must be prepared and its Playwright storage-state
kept **outside the repository**, with file mode 600. Use:

```bash
CAM_PRODUCTION_UI_STORAGE_STATE=/etc/creative-asset-manager/production-ui-storage-state.json \
CAM_PRODUCTION_UI_MODE=strict make production-ui-smoke
```

The default `auto` mode will **explicitly report degraded public-only
coverage** if no authorized state is present. It must not be described as
a full authenticated production QA pass. All non-read requests are blocked.

## Phase 2 — icon inventory and governed visual coverage

- `report.json` records a bounded `iconAudit` for every captured state:
  number of visible buttons, buttons with glyphs, potential text-only
  buttons and buttons lacking an accessible label. The new `qualityAudit`
  also records viewport overflow, small touch targets, missing image alt,
  navigation timing, resource count and transferred KB. These are **review
  inventories**, not release-blocking accessibility or performance thresholds
  until the project approves explicit budgets. Browser QA does not claim
  axe/WCAG conformance. Not every button needs an SVG.
- `requireIcons` contract in selected plans checks that glyphs are rendered
  and at least 8x8px, including style visibility.
- Realistic Review UGC mobile now enforces `mobileAssertions` across all
  eight states: document viewport overflow must be 0px; the Stage 3 reference
  arrows/voting buttons and Stage 4 selection controls must have a minimum
  24px visual hitbox. The QA report includes bounded `smallTargetExamples`
  and `overflowingElements` only when necessary for diagnosis.
  The responsive stage tabs intentionally scroll *within their own bar*,
  rather than expanding the full page; compact source thumbnails are slightly
  larger so the vote icons remain accessible without covering adjacent cards.
- `node apps/client/scripts/ui-qa-coverage.mjs` compares each interaction
  plan with the last accepted visual baseline manifest.
  `--strict` fails if states/viewports are uncovered, but normal inventory
  does not mutate baselines.
- The UGC plan has four added Stage 1/2/4/5 states requiring an approved
  baseline proposal before strict visual comparison can be considered current.
  Request user acceptance and run the governed visual proposal/accept process;
  do **not** synthesize or overwrite the canonical PNGs.

## Phase 3 — required fixture Browser QA in CI

CI runs the nine registered profiles against a local build with the locked
Playwright Chromium browser, three viewport sizes and fail-closed API fixtures.
It uploads evidence after success/failure. The separate visual coverage audit
runs as an informational report until baseline proposals are accepted.

The `ui-cross-browser.yml` workflow runs fixture-backed **Firefox and WebKit**
at the mobile viewport on a weekly schedule and via manual dispatch. Firefox
was verified locally against Explorer, UGC and Public Review. WebKit runs
in GitHub Actions; a committed workflow is not proof that the remote run passed.
WebKit on Linux is a Safari-family rendering-engine check, **not** proof of
full iOS Safari or touch-device compatibility.

Each Chromium/Firefox/WebKit CI run now emits an HTML + JSON visual review packet
for the most recent UGC QA run, alongside its screenshots in the Browser QA
artifact. The packet compares approved screenshots with fixture captures and
marks the four currently unapproved Stage 1/2/4/5 states. It never edits
tracked baselines or approves changes. Create one locally with:
`node apps/client/scripts/ui-qa-visual-review.mjs --profile realistic-review-ugc --latest`.
This report is distinct from a governed proposal; acceptance still requires
explicit approval and the normal baseline governance checks.

### Phase 4 — calibrated quality gate and complete matrix evidence

- QA plans may declare explicit viewport-specific `qualityBudgets` as upper
  bounds for `maxViewportOverflowPx`, `maxUnnamedControls`, `maxMissingAlt`,
  `maxSmallTargets`, `maxResourceCount`, `maxResourceTransferKb`, and
  `maxDomContentLoadedMs`. Per-state overrides are supported.
- **All nine workspaces now enforce the measured zero-regression budgets**
  for unnamed visible controls and visible images without `alt` across desktop,
  tablet, and mobile. UGC mobile additionally enforces zero document overflow
  and zero controls below 24px; its existing per-element assertions remain.
  These budgets were derived from the fixture QA matrix rather than
  arbitrarily enabled globally.
- Other performance/quality counters remain advisory until representative
  fixtures and approved limits are available. The matrix has surfaced viewport
  overflow candidates in Review Board and Access Management which require
  separate design verification and fixes; they are not silently treated
  as passing a zero-overflow budget.
- Failures produce the same bounded screenshot, state-specific diagnostic
  and report evidence as an interaction failure. The gate cannot make a
  failing state PASS by omitting a quality metric.
- The profile matrix writes `apps/client/.ui-qa/matrix-summary.json`,
  including pass/fail, viewport and capture completeness, max observed
  quality counters per profile, and how many visual states await approval.
  A missing, stale, incomplete, or failed profile cannot be reported as PASS.
  CI uploads this JSON alongside screenshots and the HTML baseline review.

Both jobs are independent of Production and need no session credentials.
Browser QA does not replace unit tests, authenticated Production smoke,
WCAG accessibility auditing or a real backend integration test.

## October 14, 2026 — RBAC preflight and baseline-review integrity

Fixture-backed QA now validates route-specific read permissions **before**
launching a browser. It checks simulated identity data against the route
contract without elevating it. A mismatch fails quickly with a specific
`fixture-rbac-mismatch` reason; matrix reporting distinguishes `BLOCKED`
from an application assertion failure and does not call a blocked workspace
PASS. Companion unit tests guard against drift in the protected route mapping.

Inventory fixture access and readiness coverage were reconciled on
2026-10-14: the synthetic operator identity now has the route-required
`inventory.read` permission, while no production role or grant changed.
`GET /api/inventory/materials/readiness` has a bounded fixture response.
Inventory functional Browser QA passes in desktop, tablet portrait and mobile;
the real mobile layout also reserves scroll clearance above the fixed rail.
A full nine-workspace functional matrix then passed on all three viewport sizes
(9/9 profiles, zero interaction/network issues), including a separate clean
Research Inbox run. This result remains fixture-backed; visual comparisons
are still skipped pending approvals and authenticated production QA is separate.
The preflight remains fail-closed for future fixture RBAC regressions.

Further QA hardening (2026-10-14): the Research Inbox sub-run now inherits
the matrix's `CAM_UI_QA_KEEP` limit, so it cannot silently delete earlier
workspace screenshots needed for baseline review. AI Operations was separately
verified across all six declared viewports (1440, 1280, 1024 landscape,
768 portrait, 390 and 320px), 15 states each, with no issues. Its 90-image
review packet is `review-only`, not `review-blocked`; RRUGC also has a
90-image review packet. Both retain unapproved states without accepting any
new baseline. Public Review now renders a readable fallback when a selected
file lacks a usable image URL rather than rendering an empty image source.

The visual review CLI now requires the **correct QA plan state list** when
resolving `--latest`; this prevents a Research Inbox run on the same RRUGC
URL from replacing the primary Stage 0–5 review packet. A review packet
assesses all expected states, viewports, screenshots, quality audits and
functional issues. Incomplete or failed runs are marked `review-blocked`;
clean runs are `review-only`. Neither mode modifies accepted PNGs.

Last validated RRUGC run:
`apps/client/.ui-qa/2026-10-14T13-20-32-787Z/`.
Its read-only review packet contains 90 captures covering 30 states across
desktop, tablet portrait and mobile, with 26 RRUGC states awaiting review.
AI Operations has one additional baseline state awaiting review (27 total).
Do not claim visual regression PASS while these states lack approval.

Production login remains a separate dependency: the VPS needs a legitimate
least-privilege authenticated Playwright storage state outside Git, with mode
600. Never synthesize or copy live credentials into fixtures or reports.
The public production smoke is only a limited route/health check.

### Useful commands

```bash
node --test apps/client/scripts/ui-qa-*.node-test.mjs apps/client/scripts/production-ui-plan-validation.node-test.mjs
node apps/client/scripts/ui-qa-coverage.mjs
node apps/client/scripts/ui-qa-coverage.mjs --strict   # expected to fail until baselines accepted
CAM_UI_QA_PROFILES=realistic-review-ugc CAM_UI_VISUAL_SKIP=1 bash scripts/cam-ui-profile-matrix.sh
CAM_UI_VISUAL_SKIP=1 bash scripts/cam-ui-profile-matrix.sh
```
