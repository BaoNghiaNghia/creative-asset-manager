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

4. **Verify code**
   - Run targeted tests for the changed component or feature.
   - Run TypeScript/typecheck when frontend types or component contracts changed.
   - Run the production frontend build for user-visible UI changes.
   - Run `git diff --check`.
   - For authorization/security-sensitive UI, run the relevant negative tests
     and integration checks required by `AGENTS.md`.

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
3. **Visual QA runner** — `npm run ui:qa -- --url <local-or-staging-url>` captures deterministic viewport screenshots and records console/page/network issues.
4. **Interactive-state plan** — pass `--plan docs/operations/ui-qa-plan.example.json` as a template for default/hover/selected/selected+hover/focus checks; agents should tailor selectors to the component being changed.
5. **Resource control** — viewport checks run sequentially in one Chrome process and old `.ui-qa` runs are automatically pruned (default: keep 5).
6. **Production handoff** — production deployment remains a separate explicit user-authorized step.

### Standard commands

```bash
# One-time VPS setup
make ui-browser-install

# Code-only validation for changed UI
make ui-check

# Force the gate even when automatic diff detection sees no frontend change
CAM_UI_FORCE=1 make ui-check

# Code gate plus Browser QA
CAM_UI_QA_URL=http://127.0.0.1:4173 \
CAM_UI_VIEWPORTS=desktop,tabletPortrait,mobile \
make ui-check

# Browser QA only, using an interaction plan
cd apps/client
npm run ui:qa -- \
  --url http://127.0.0.1:4173 \
  --viewports desktop,tabletPortrait,mobile \
  --plan ../../docs/operations/ui-qa-plan.example.json
```

The Browser runner writes screenshots plus `report.json` under `apps/client/.ui-qa/<run-id>/`. The directory is gitignored and old runs are pruned automatically.
