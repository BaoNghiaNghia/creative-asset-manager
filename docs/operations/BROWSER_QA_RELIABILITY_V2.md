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

Both jobs are independent of Production and need no session credentials.
Browser QA does not replace unit tests, authenticated Production smoke,
accessibility auditing or a real backend integration test.

### Useful commands

```bash
node --test apps/client/scripts/ui-qa-*.node-test.mjs apps/client/scripts/production-ui-plan-validation.node-test.mjs
node apps/client/scripts/ui-qa-coverage.mjs
node apps/client/scripts/ui-qa-coverage.mjs --strict   # expected to fail until baselines accepted
CAM_UI_QA_PROFILES=realistic-review-ugc CAM_UI_VISUAL_SKIP=1 bash scripts/cam-ui-profile-matrix.sh
CAM_UI_VISUAL_SKIP=1 bash scripts/cam-ui-profile-matrix.sh
```
