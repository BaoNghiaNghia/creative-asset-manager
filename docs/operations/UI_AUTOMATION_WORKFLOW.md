# Automatic UI workflow

This is the project-wide default workflow for UI work in Creative Asset Manager.
It is intentionally independent of paid Cursor features and uses the existing
CodeLocal + Browser (Playwright) surface.

## Trigger

Treat a task as UI work when the user's request changes any visible or interactive
frontend behavior, including:

- layout, spacing, typography, colors, borders, shadows, icons, cards or grids;
- hover, selected, focus, disabled, loading, empty or error states;
- responsive behavior on desktop, tablet or mobile;
- menus, dialogs, panels, navigation, breadcrumbs, search controls or toolbars;
- media thumbnails, video controls, image presentation or overlays;
- visible role/permission behavior in the client;
- a screenshot-based redesign or a request to match a reference image.

For mixed frontend/backend tasks, apply this UI workflow to the UI portion and
the normal architecture/security workflow to the backend portion.

## Default autonomous sequence

When a UI request is clear enough to implement, do not stop to ask for routine
confirmation. Execute the following sequence automatically:

1. **Classify scope**
   - Identify the exact page/component/state affected.
   - Determine whether the request is visual-only, interaction-changing,
     authorization-sensitive, or desktop-native.

2. **Inspect before editing**
   - Inspect the current component, CSS, tests and nearby responsive rules.
   - If a screenshot is provided, treat the screenshot plus written requirements
     as the visual acceptance contract.
   - Identify behaviors that must remain intact: selection, drag/drop, double
     click, context menu, playback, search, share/review actions and keyboard
     accessibility where applicable.

3. **Implement the smallest scoped change**
   - Prefer CSS/layout changes for purely visual requests.
   - Reuse the existing visual language and icon family.
   - Avoid broad redesign outside the requested area.
   - Avoid adding dependencies unless the existing stack cannot reasonably solve
     the task.

4. **Verify during repair without looping the full gate**
   - During implementation, prefer `make ui-repair-check` for the affected viewport/state instead of rerunning the full gate.
   - The repair check always runs `git diff --check`, then uses a Vite dev server and targeted Browser/visual comparison. It intentionally skips the production build and full frontend test suite.
   - Add `CAM_UI_REPAIR_TESTS="..."` for a small relevant Vitest subset or `CAM_UI_REPAIR_TYPECHECK=1` when the current edit specifically needs those checks.
   - Automatic repair retries are bounded to two attempts per failed full-gate analysis by default. If they are exhausted, stop and inspect rather than increasing the loop.
   - Run the production build, complete frontend test suite and typecheck in the full UI gate only when the targeted state passes and the change is ready to finalize.
   - For authorization/security-sensitive UI, still run the relevant negative tests and integration checks required by `AGENTS.md`.

5. **Browser QA with Playwright**
   - Use one isolated browser session at a time.
   - Prefer the project runner `npm run ui:qa` because it supports deterministic viewport sizes and runs safely on the current root-owned VPS with Chromium sandboxing disabled only for this local QA process.
   - CodeLocal Browser can still be used when its runtime can launch safely.
   - Prefer local or explicitly designated staging endpoints. Remote hosts are blocked by the runner unless explicitly allowlisted.
   - Check the states relevant to the component: default, hover, selected, selected+hover, focus, long-title/overflow, loading/empty/error when applicable.
   - Check responsive viewports when the request can be affected by screen size.
   - Inspect console errors, page errors, failed requests, and HTTP 4xx/5xx responses.
   - Capture screenshots when they materially help comparison or handoff.

6. **Resource-aware cleanup**
   - Run viewport checks sequentially rather than opening many Chromium
     instances in parallel.
   - Close the Browser session after verification.
   - Do not keep large screenshot/video artifacts indefinitely; retain only
     what is useful for the current task or debugging.

7. **Handoff**
   - Report exactly what changed, tests/build results, Browser states/viewports
     checked, console/network issues, and any remaining risk.
   - Commit/push only when requested or when the project workflow explicitly
     calls for it.
   - Deploy Production only when the current user explicitly asks for a
     Production deploy.
   - Build/publish the Windows desktop app only through the authorized native
     Windows workflow when the current user asks for that release step.

## Verification matrix

Use the smallest matrix that covers the changed behavior.

| UI type | Required Browser checks |
| --- | --- |
| Card/grid | default, hover, selected, selected+hover, long title |
| Button/menu | default, hover, focus, open/close, disabled if applicable |
| Dialog/panel | open, close, focus, overflow, narrow viewport |
| Media card | thumbnail, play affordance, hover, selected, playback entry |
| Responsive layout | desktop + affected tablet/mobile widths |
| Search/filter UI | input, results, empty/error, keyboard focus |
| Permission-visible UI | allowed role + denied/hidden role path |

Recommended viewport targets when relevant:

- 1440 x 900 desktop;
- 1280 x 800 compact desktop;
- 1024 x 768 tablet landscape;
- 768 x 1024 tablet portrait;
- approximately 390 x 844 mobile.

For cross-workspace responsive layout, use the canonical CSS matrix from
`apps/client/styles/ui-foundation.css`: wide desktop `>=1600`, standard
desktop `1280–1599`, compact desktop `1025–1279`, tablet `681–1024`,
phone `<=680`, and small phone `<=420`. A feature may add a narrower local
breakpoint when its component genuinely needs it, but do not introduce a new
page-level breakpoint ladder when one of these ranges already covers the
behavior.

Do not run every viewport for every change. Run the ones whose layout could
actually be affected.

## Escalation rules

Use a stronger verification path when the UI task changes:

- authentication, RBAC or tenant visibility;
- public review/share behavior;
- upload/delete/destructive actions;
- storage/provider flows;
- migrations or API contracts;
- desktop native drag/drop, file system or updater behavior.

Those tasks are not "CSS-only" even when the user describes them visually.

## Completion criteria

A normal UI task is ready only when:

- the requested visible behavior is implemented;
- preserved interactions still work;
- relevant tests/typecheck/build pass;
- Browser QA passes for the affected states;
- no new relevant console/network errors are observed;
- Production has not been mutated unless the current user explicitly requested
  deployment.

## Implementation phases

The automatic UI workflow is implemented in these concrete phases:

1. **Browser runtime** — install/check the Chrome channel with `make ui-browser-install`. This is a one-time VPS prerequisite.
2. **Code gate** — `make ui-check` or `bash scripts/cam-ui-gate.sh` runs diff-check, typecheck, frontend tests, and production build whenever frontend/UI files changed.
3. **Authenticated local staging** — after the build, the gate starts a loopback-only Vite preview and runs the real frontend against deterministic fixture-backed profiles. `explorer-viewer` remains the fallback, while `review-board`, `realistic-review-ugc`, `ai-operations`, `inventory`, `access-management`, `video-generation`, `job-queue`, and `public-review` select their own route, fixture, interaction plan, and visual baseline through `apps/client/scripts/ui-qa-profiles.mjs`. When no explicit `CAM_UI_QA_PROFILE` is supplied, the gate uses the smart profile matrix to select every workspace affected by the changed files; cross-workspace foundation/navigation changes expand to all profiles. Playwright intercepts only `/api/**` inside that browser context and fails closed with synthetic HTTP 599 on any API route the selected fixture does not explicitly handle. No OAuth login, Production session cookie, Production database, or Production cloud source is used.
4. **Visual QA runner** — `npm run ui:qa -- --url <local-or-staging-url>` captures deterministic viewport screenshots and records console/page/network issues. Add `--fixture <json>` to provide a safe authenticated scenario.
5. **Interactive-state plans** — each supported profile owns a deterministic plan: Asset Explorer covers default/hover/selection/search/focus; Review Board covers workspace, issue hover, reviewer focus and date filters; Realistic Review UGC covers campaign/candidate surfaces and candidate filtering; AI Operations covers overview KPI/filter states; Inventory covers material registry/search/candidate review surfaces; Access Management covers member rows/search and the roles tab; Video Generation covers the generation card, prompt focus, and reference selection; Job Queue covers rows, search, status tabs, and generation-result modal; Public Review covers shared cards, search focus, and the media review viewer. `docs/operations/ui-qa-plan.example.json` remains the generic template for future profiles.
6. **Visual regression** — the authenticated staging run compares every captured state against the tracked PNG baseline directory assigned to the selected profile under `apps/client/visual-baselines/<profile>`. Pixel differences above the configured tolerance or any screenshot dimension change fail strict QA and write a diff image into the current `.ui-qa/<run-id>/diffs/` folder. Baselines are never rewritten during the normal gate; intentional changes go through governed proposal + explicit acceptance.
7. **Automatic diff triage** — on a visual failure the runner clusters changed pixels into bounded regions, maps those regions to the current DOM, records the interaction that produced the state, ranks likely affected selectors, cross-checks those selectors against changed frontend source files, and writes both `visual-analysis.json` and `visual-analysis.md`. It also creates annotated screenshots in `.ui-qa/<run-id>/diagnostics/`.
8. **Adaptive repair loop** — `make ui-repair-check` reads the latest failed **full** visual analysis, selects at most four failing issues, replays only the necessary interaction prefix, captures only the requested failing states and viewports, and runs against the Vite dev server without a production build. The default repair budget is two attempts per failed full-gate run. A successful targeted repair authorizes one final full gate; a failed targeted repair does not.
9. **UI Auto-Fix Orchestrator** — `CAM_UI_TASK="..." make ui-autofix` is the normal agent entry point after an implementation edit. It classifies the task and changed files, writes `.ui-qa/autofix-plan.json`, selects the matching fixture-backed profile for Asset Explorer, Review Board, Realistic Review UGC, AI Operations, Inventory, Access Management, Video Generation, Job Queue, or Public Review, executes bounded state/viewport repair verification, and then permits exactly one final full UI gate for that session. Tests that passed in the immediately preceding targeted verification are not rerun again inside that same final gate; typecheck, analyzer checks, production build and authenticated Browser smoke still run.
10. **Smart profile matrix** — `make ui-profile-matrix` reuses one local preview server and runs the deterministic Browser/visual QA profiles selected by `apps/client/scripts/ui-qa-profile-selection.mjs`. Shared foundation, global responsive, route shell, workspace navigation, shared header, font, or QA-runtime changes select the complete profile set. Feature-local changes select only the dependent workspaces; for example AI Operations shared feature changes cover both AI Operations and Job Queue, Inventory changes also cover its embedded AI Operations view, and `RichAnnotation` changes cover both Public Review and Review Board. The normal final gate uses this matrix automatically unless an explicit `CAM_UI_QA_PROFILE` is already set by a targeted Auto-Fix flow.
11. **Smart Test Selection** — `apps/client/scripts/ui-smart-tests.mjs` builds a lightweight local import/reverse-dependency graph for frontend code and tests. Localized component/hook changes run only linked Vitest files, visual/automation-only changes can skip Vitest, and shared/security-sensitive/uncertain changes fall back to the full suite. `scripts/cam-ui-run-smart-tests.sh` is used by both repair checks and the final UI gate.
12. **Baseline governance** — intentional visual changes never overwrite tracked baselines directly. `CAM_UI_TASK="..." make ui-visual-propose` runs the current UI against the tracked baselines and writes an isolated proposal under `.ui-qa/baseline-proposals/<id>/` containing candidate screenshots, hashes, source HEAD/workspace fingerprint, changed frontend source files, Browser/runtime evidence, `proposal.json`, and `proposal.md`. Tracked baselines remain untouched. Acceptance requires a separate current-user confirmation and an explicit `CAM_UI_BASELINE_ACCEPT=1`, proposal ID, and acceptance reason. Before applying, the system verifies that source HEAD, working-tree fingerprint, candidate hashes, and tracked baseline hashes still match the proposal. After applying, it reruns full dev-server visual QA; any failure restores the previous baselines automatically.
13. **Resource control** — targeted repair runs and full viewport checks are sequential, only one Chrome process is used at a time, local servers are stopped automatically, and old `.ui-qa` runs are pruned.
14. **Production UI Smoke** — after an explicitly authorized Production deployment, `make production-ui-smoke` can verify the live HTTPS site without mutating Production. The Browser blocks every HTTP method except GET, HEAD, and OPTIONS, runs desktop/tablet/mobile sequentially, captures screenshots plus console/page/network evidence, checks `/build-info.json` provenance against the expected deploy commit, and verifies Asset Explorer, Review Board, Realistic Review UGC, Privacy, and Terms. Authenticated private routes require a Playwright storage-state file kept outside the repository with mode `600` or stricter. `CAM_PRODUCTION_UI_PUBLIC_ONLY=1` intentionally runs only the public legal routes and is a partial smoke, not a replacement for authenticated verification.
15. **Production handoff** — Production deployment remains a separate explicit user-authorized step. The smoke workflow never grants deployment authorization and never performs create/update/delete actions.

### Standard commands

```bash
# One-time VPS setup
make ui-browser-install

# Default UI verification entry point after implementing the request.
# It plans scope/state/viewport, runs Smart Tests + targeted visual QA,
# then runs one final full gate only after targeted verification passes.
CAM_UI_TASK="Fix Asset Explorer card hover on mobile" make ui-autofix

# Inspect the inferred plan without running Browser/tests.
CAM_UI_TASK="Fix Asset Explorer card hover on mobile" \
CAM_UI_AUTOFIX_PLAN_ONLY=1 \
make ui-autofix

# Lower-level targeted repair check for a known state.
CAM_UI_REPAIR_VIEWPORTS=mobile \
CAM_UI_REPAIR_STATES=selected-hover \
make ui-repair-check

# Inspect/run Smart Test Selection directly.
make ui-smart-tests

# Full UI gate. Usually invoked by Auto-Fix once at finalization.
make ui-check

# Run the default authenticated Asset Explorer profile against the current dist build.
make ui-staging-qa

# Run the smart impacted-profile matrix against one shared local preview.
make ui-profile-matrix

# Force a bounded manual matrix when investigating shared UI.
CAM_UI_QA_PROFILES=explorer-viewer,review-board,public-review make ui-profile-matrix

# Select another deterministic workspace profile.
CAM_UI_QA_PROFILE=review-board make ui-staging-qa
CAM_UI_QA_PROFILE=realistic-review-ugc make ui-staging-qa
CAM_UI_QA_PROFILE=ai-operations make ui-staging-qa
CAM_UI_QA_PROFILE=inventory make ui-staging-qa
CAM_UI_QA_PROFILE=access-management make ui-staging-qa
CAM_UI_QA_PROFILE=video-generation make ui-staging-qa
CAM_UI_QA_PROFILE=job-queue make ui-staging-qa
CAM_UI_QA_PROFILE=public-review make ui-staging-qa

# Create a review proposal for an intentional visual change. This does NOT mutate baselines.
# CAM_UI_QA_PROFILE selects the matching workspace baseline.
CAM_UI_QA_PROFILE=review-board \
CAM_UI_TASK="Adjust Review Board tablet layout" \
make ui-visual-propose

# Only after the current user explicitly confirms the new visual result is intentional:
CAM_UI_BASELINE_PROPOSAL=<proposal-id> \
CAM_UI_BASELINE_ACCEPT=1 \
CAM_UI_BASELINE_ACCEPT_REASON="Matches the user-approved card redesign" \
make ui-visual-accept

# Compatibility alias: now creates a proposal instead of writing baselines directly.
CAM_UI_TASK="Increase Asset Explorer card title size" make ui-visual-update

# Read-only Production smoke after an explicitly authorized deploy.
# Authenticated route coverage requires a root/operator-owned Playwright storage state outside the repo.
CAM_PRODUCTION_UI_STORAGE_STATE=/etc/creative-asset-manager/production-ui-storage-state.json \
CAM_PRODUCTION_EXPECTED_COMMIT=<deployed-commit> \
make production-ui-smoke

# Deliberately partial public-only smoke when no authenticated state is available.
CAM_PRODUCTION_UI_PUBLIC_ONLY=1 \
CAM_PRODUCTION_EXPECTED_COMMIT=<deployed-commit> \
make production-ui-smoke

# Force the gate even when automatic diff detection sees no frontend change.
CAM_UI_FORCE=1 make ui-check

# Use a specific local/staging URL instead of the built-in loopback preview.
CAM_UI_QA_URL=http://127.0.0.1:4173 \
CAM_UI_VIEWPORTS=desktop,tabletPortrait,mobile \
make ui-check

# Explicitly skip Browser QA for an exceptional code-only diagnostic run.
CAM_UI_QA_SKIP=1 make ui-check

# Browser QA only, with a deterministic authenticated viewer fixture.
cd apps/client
npm run ui:qa -- \
  --url http://127.0.0.1:4173 \
  --fixture scripts/fixtures/explorer-viewer.json \
  --viewports desktop,tabletPortrait,mobile \
  --plan ../../docs/operations/ui-qa-explorer-viewer-plan.json \
  --baseline-dir visual-baselines/explorer-viewer \
  --strict
```

Production smoke artifacts are written under `apps/client/.ui-qa/production-smoke/<run-id>/` and are gitignored. They contain screenshots and `report.json`/`report.md`, but never the authenticated storage-state file. To limit disk usage and Production screenshot retention, the wrapper keeps the newest five runs by default; `CAM_PRODUCTION_UI_KEEP_RUNS` may be set from 1 to 20. The Production runner refuses HTTP/loopback targets, rejects storage state located inside the repository, and treats any attempted non-read request as a smoke failure.

The Browser runner writes screenshots plus `report.json` under `apps/client/.ui-qa/<run-id>/`. When baseline comparison is enabled it also writes `visual-analysis.json` and `visual-analysis.md`; failures include annotated screenshots in `diagnostics/` and raw visual diffs in `diffs/`. Direct `--update-baselines`/`CAM_UI_VISUAL_UPDATE=1` writes are blocked in normal workflows; baseline changes must pass through the proposal/acceptance governance flow above. Reports are tagged with `mode: "full"` or `mode: "repair"`, so the repair workflow does not recursively treat its own targeted failures as a new full-gate session. The repair runner supports `--states` targeting while still replaying prerequisite steps from the plan, which preserves dependent states such as selected+hover without capturing every earlier screenshot. Repair attempt counters live under `.ui-qa/repair-sessions/`; the default limit is two. Auto-Fix session guards live under `.ui-qa/autofix-sessions/` and prevent repeated final full-gate execution for the same task/base commit. Reset an Auto-Fix session only after a diagnosed, causally scoped repair using `CAM_UI_AUTOFIX_RESET=1`. Smart Test Selection is enabled by default; set `CAM_UI_SMART_TESTS=0` only when you deliberately need the full suite, or use `CAM_UI_TESTS` / `CAM_UI_REPAIR_TESTS` to name an explicit test subset. The analysis reports the viewport/state, triggering selector actions, changed-pixel regions, likely DOM elements and likely changed source files so an agent can localize the regression before editing. The directory is gitignored and old runs are pruned automatically. Visual comparison uses `pixelmatch`/`pngjs`; the default per-pixel threshold is `0.1` and the maximum accepted changed-pixel ratio is `0.001` (0.1%). A dimension mismatch always fails strict QA. Region clustering defaults to 12 px tiles, a two-tile join radius and at most eight reported regions; these can be tuned with `CAM_UI_VISUAL_REGION_TILE_SIZE`, `CAM_UI_VISUAL_REGION_JOIN_RADIUS` and `CAM_UI_VISUAL_MAX_REGIONS` for a justified diagnostic case. Override pixel thresholds only with `CAM_UI_VISUAL_PIXEL_THRESHOLD` or `CAM_UI_VISUAL_MAX_DIFF_RATIO`. Set `CAM_UI_VISUAL_SKIP=1` only for an exceptional browser diagnostic where baseline comparison is intentionally not relevant. The default fixture contains synthetic `.example.test` identity data and synthetic asset metadata only; it must never be replaced with copied Production session cookies, OAuth tokens, or Production user data.
