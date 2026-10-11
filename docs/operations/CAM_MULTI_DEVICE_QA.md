# CAM Multi-Device QA and Future-Feature Acceptance

**Mandatory companion to [CAM_MULTI_DEVICE_STANDARD.md](CAM_MULTI_DEVICE_STANDARD.md).**
Applies to newly added and meaningfully edited UI features, even if their request does not mention mobile. Use existing CAM Playwright runner and its authenticated synthetic fixture profiles, never a logged-in production session.

## A. Canonical QA selection

The current UI runner includes: desktop 1440x900, compact 1280x800, tabletLandscape 1024x768, tabletPortrait 768x1024, mobile 390x844 and mobileSmall 320x700. These are the **existing working names** from apps/client/scripts/ui-qa.mjs.

Do **not** assume other names like largePhone/fold exist in the runner. To expand the matrix, deliberately add/configure viewport names with tests first, retaining existing names and baselines. Do not add a new QA runner that bypasses fixture, screenshot governance or the current timeout/security rules.

### Additional proposed devices for progressive coverage

Machine-readable planning list: [cam-multidevice/viewport-matrix.csv](cam-multidevice/viewport-matrix.csv). The CSV is a proposed QA input, not an assertion that all names are already registered in the existing runner.

| CSS width x height | DPR simulation | Device class / scenario | Required when |
| --- | --- | --- | --- |
| 320x700 | 2 | narrow phone | Shared shell, complex mobile UI |
| 360x780 | 3 | small Android | New mobile navigation/toolbars |
| 390x844 | 3 | reference phone | Every UI feature |
| 402x874 | 3 | compact high-DPI phone | New dense touch controls |
| 412x915 | 2.625 | Android medium | Width-specific failures |
| 440x956 | 3 | large high-DPI phone | New UI; image/viewer changes |
| 480x900 | 2 | wide mobile | Foldable and unusual widths |
| 600x900 | 2 | small unfolded/fold/tablet | Shared layout migrations |
| 680x900 | 2 | shell boundary | Navigation changes |
| 681x900 | 2 | shell boundary | Navigation changes |
| 768x1024 | 2 | iPad portrait | Every UI feature |
| 834x1194 | 2 | tablet mid-width | Tablet-dense features |
| 1024x768 | 2 | iPad/tablet landscape | Every UI feature |
| 1032x1376 | 2 | large tablet portrait | Large modal/split panels |
| 1280x800 | 1 | compact desktop | Broad and dense UI work |
| 1440x900 | 1 | desktop reference | Every UI feature |
| 1600x900 | 1 | wide desktop boundary | Shared shell change |
| 1920x1080 | 1 | full HD desktop | Major milestone |

Values are deliberately CSS emulation targets, **not assertions of an exact hardware model**. For browser QA, DPR 4 can be separately simulated on at least one phone; a DPR change should not activate desktop layout.

### Breakpoint edge audit

For shared navigation/CSS changes, consider width at breakpoint-1, breakpoint, breakpoint+1 for the currently implemented CSS edges: 420, 650, 680, 760, 900, 1024, 1180, 1279 and 1600. Do not run every edge for every minor task; choose causal coverage.

## B. Three tiers

**Every new feature and substantial UI change:** phone 390x844, tablet 768x1024 (and 1024x768 for orientation-sensitive layouts), desktop 1440x900. Add 320x700 and 440x956 when modals/grid/toolbars/media affected. Run relevant authenticated fixture profile and interaction states.

**Cross-workspace/global change:** run full affected profile matrix and shell boundary widths; use test selection and one sequential Browser session. Preserve visual-baseline review.

**Before major release:** desktop Firefox/Chromium plus mobile WebKit emulation when runtime supports it. Validate on actual Safari iPhone + Android Chrome + iPad Safari when available, including touch, soft keyboard, safe-area and back gesture; disclose NOT_RUN otherwise.

## C. Pass/fail assertions

For each affected route:
- No blocking pageexception or new relevant 4xx/5xx/console errors.
- Document/body scroll width does not exceed CSS viewport width by more than 1px unless the route explicitly and intentionally implements an isolated horizontal canvas. Local scrollers are allowed.
- Visible primary actions have adequate touch hit area, are not covered by sticky tabs or phone notch/home indicator, and are not hover-only.
- Overlay open/close does not shift the underlying page; focus trap, Escape/close and focus return work.
- Table content remains discoverable; filters/actions remain available as cards, scrollable data or detail views.
- Image skeleton persists while loading, settles on error, visible images are not blurred by undersized variants; original-quality image viewer works at DPR 3.
- Search, filter, sort, selection, pagination and keyboard shortcuts preserve state over resize/rotation.
- Allowed role access remains functional; denied role remains unable to perform restricted mutation (also tested server-side).
- Icons have accessible names, text contrast/focus ring are visible, and UI supports reduced motion.
- No critical regression in existing stage actions (skill selection, retry, versions, approval, output images).

FAIL on significant missing actions, broken navigation, auth regression, crash, no-access modal, globally broken overflow, lost data or test gate failure. Warnings like minor harmless antialiasing must be distinguished from fails; never silently accept visual baseline changes. **NOT_RUN is not PASS.**

## D. Screenshots and evidence

For every new UI capability record the profile/route, build/source commit, browser, dimensions and DPR, state name, screenshot or replay link, console/network summary, PASS/FAIL/NOT_RUN, follow-up issue and reviewer. Useful states: default, hover, selected, selected+hover, keyboard focus, menu open, filter applied, modal open, image viewer loaded, error/empty/skeleton, keyboard raised and landscape.

Do not store live auth cookies, secrets, signed URL query strings, personally identifiable production data or destructive action traces in Git. Prefer local synthetic fixtures.

## E. Execution commands (existing, do not invent scripts)

From repository root, for a normally scoped UI feature:

~~~bash
CAM_UI_TASK="Add responsive filtering to the new feature" make ui-autofix
~~~

For shared layouts, after local tests and correctly scoped changes:

~~~bash
make ui-profile-matrix
make ui-check
~~~

To inspect currently configured fixture-backed QA and viewport names:

~~~bash
cd apps/client
npm run ui:qa -- --url http://127.0.0.1:4173 --viewports desktop,tabletPortrait,mobile
~~~

The explicit UI QA command requires an actually running safe local/staging server plus suitable fixture/plan for authenticated routes; do not treat the illustrative command as self-sufficient. The project make workflows already manage preview/fixtures and are preferred. Browser QA timeouts, screenshots, visual diffs, baseline proposal and explicit acceptance remain controlled by docs/operations/UI_AUTOMATION_WORKFLOW.md.

## F. Future-feature PR checklist (copy into task completion)

- [ ] Current route/roles and affected state(s) listed
- [ ] Mobile functional layout at 390px verified, with 320px if needed
- [ ] Tablet 768px verified; landscape when applicable
- [ ] Desktop 1440px verified with workflow unchanged
- [ ] DPR 3 image quality checked for media components
- [ ] Touch targets, non-hover actions and safe-area covered
- [ ] Keyboard access, focus restore, Escape/close covered
- [ ] Existing state, authorization, selection and retry behavior retained
- [ ] Relevant typecheck/tests/build passed
- [ ] Fixture-based Browser screenshots/issues recorded
- [ ] Risks and real-device NOT_RUN checks disclosed
- [ ] Approval and deployment boundary respected

The checklist cannot be declared complete based only on source-level tests. **For every future UI feature** the owner must describe the three viewport outcomes in the handoff.

## G. Operational rollout

Capture pre-change baseline, keep the current production release untouched during docs/UI work, test individual feature slices, then staging canary with read-only smoke and rollback to the previous known-good package if accepted. Do not delete unrelated dist files or bypass the Git/CI state. Production deploy, destructive maintenance and visual-baseline acceptance each need their separate required user authorization.
