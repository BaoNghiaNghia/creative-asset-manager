# CAM Multi-Device Integration Standard (v1.1)

**Status: mandatory for all current and future CAM web UI work. Effective 2026-10-11.**

This document is the repository-canonical version of the CAM Multi-Device Integration Kit (2026). It covers integration architecture, design rules, media, route contracts, quality gates, staging and rollback. Detailed execution checks are in [CAM_MULTI_DEVICE_QA.md](CAM_MULTI_DEVICE_QA.md). Follow [UI_AUTOMATION_WORKFLOW.md](UI_AUTOMATION_WORKFLOW.md) and the root AGENTS.md as well.

## 1. Objective and boundary

Every **new UI feature** and every modification to existing CAM UI must offer a functional responsive design for phones, iPads/tablets and desktops, including High-DPI screens, touch interactions, landscape, small browsers and foldable devices. **Do this by default even when the individual user request does not repeat "mobile".**

This requirement applies to Asset Explorer, Shared/Public Review, Visual Search, media viewers, AI Operations, Realistic Review UGC and its Stage 0–5 screens, Inventory, Review Board, Job Queue, Access Management, Video Generation, Settings and newly added workspace routes.

Never silently remove a desktop feature on mobile. When a dense desktop workflow does not fit on a phone, provide a touch-usable alternative (details drawer, drill-down, card view, local table scroll, filter sheet, picker, overflow menu). Preserve the same data, role/tenant permissions, search/selection state, and business behavior.

This is an incremental React/Vite/CSS integration, not a rewrite. Changes to production API, RBAC, database, image processing or deployment are outside a purely responsive task and must follow their own security reviews. Existing production security boundaries always apply.

## 2. Verified CAM implementation (snapshot 2026-10-11)

- Frontend: React 18, TypeScript, Vite, Be Vietnam Pro; client project at apps/client.
- Shared styles: apps/client/styles/ui-foundation.css, responsive-platform.css, mobile-workspace.css.
- Mobile navigation: apps/client/app/components/ResponsiveWorkspaceNav.tsx; permission-filtered destinations must stay permission-filtered.
- Asset grid: apps/client/app/components/AssetGrid.tsx already uses lazy images, IntersectionObserver, skeletons and limited thumbnail concurrency; preserve it.
- AI Operations: apps/client/app/ai-operations; RRUGC: apps/client/app/realistic-review-ugc with tablet-mobile-density.css and ui-overhaul.css.
- UI test infrastructure: apps/client/scripts/ui-qa.mjs (Playwright), npm scripts for typecheck, test, build and ui:qa; project make targets ui-autofix, ui-repair-check, ui-profile-matrix and ui-check.
- Current responsive breakpoints exist across several files. Their overlap is **technical debt for audit**, not proof of a bug. Never replace all media queries globally.

Verify live paths, imports, stage numbering and deployed commit against the current revision before editing. Treat proposed tokens, CDN variants and release KPIs below as targets, **not evidence that they already exist**.

## 3. Responsive shell and breakpoints

Always base layout on **CSS viewport width/container width**, never the phone marketing model or physical panel resolution. A 1320px-wide physical panel with DPR 3 may have only 440 CSS px of layout width. DPR changes image quality strategy, not whether desktop navigation should display.

Keep the established app-shell breakpoint ladder until a reviewed migration:
 
| CSS viewport width | CAM shell intent |
| --- | --- |
| up to 420px | Small phone; narrow titles, compact toolbars and touch actions |
| 421–680px | Phone and some folded/unfolded devices; bottom navigation |
| 681–1024px | Tablet / flexible-width layout, responsive top workspace navigation |
| 1025–1279px | Compact desktop; preserve functional workspace |
| 1280–1599px | Standard desktop |
| 1600px+ | Wide desktop, bounded content and/or dense workspace |

Component-level breakpoints and container queries are permitted where contents actually stop fitting, but each extra cutoff needs evidence and QA at widths one pixel either side. Existing RRUGC CSS also uses 760/761/1180 and other styles use 650/900; inspect selector cascade and import order first.

Layout requirements: fluid grids with minmax and min-width:0, adaptive rather than fixed panel widths, wrapped toolbars, bounded local table overflow instead of page overflow, sticky actions not covering text, 100dvh or safe fallback for full-height drawers, iOS safe-area insets and virtual keyboard handling. Test portrait AND landscape. Use prefers-reduced-motion.

## 4. Shared design system

Reuse design tokens already defined in apps/client/styles/ui-foundation.css. Introduce shared tokens before adding repeated custom values. Suggested spacing rhythm is 4/8/12/16/24/32 CSS px with adaptive gutters about 12 mobile, 16 tablet, 24 desktop. Font sizes are **CSS px**: general body 14–16px, actionable labels normally at least 12px; avoid unreadable 7–10px UGC text. Avoid shrinking all content simply to fit a table.

Target at least 44x44 CSS px effective touch hit areas for important controls, while correctly distinguishing this CAM target from WCAG 2.2 AA target-size minimum (24x24 or spacing/exception). Icon-only controls require accessible names and visible keyboard focus. Icons must be SVG from the existing icon language, not mismatched emoji navigation art. Preserve hover/selected/selected+hover/focus states with no layout shifts.

New components MUST describe and implement loading, error, empty, disabled, selected and long-text behavior at relevant widths. Essential actions cannot be hover-only, and drag operations require a usable non-drag alternative on touch. Toast, confirmation and destructive actions retain existing CAM interaction conventions.

## 5. Media / High-DPI

- For thumbnails, prefer an appropriately sized CDN/worker variant and srcset/sizes where supported; select dimensions from displayed CSS width and devicePixelRatio (DPR), with rational quality/cost caps.
- Do not load full original images for an entire grid merely because phones have DPR 3 or 4. Original-quality is for explicit HQ viewing or zoom.
- Preserve signed URL and tenant authorization behavior, lazyload/skeleton, cancellation and thumbnail concurrency. Do not expose tokens in screenshot evidence or logs.
- For modal/masonry, show stable aspect ratios and predictable placeholders; no blank layout jumps on image load or error. Modal viewers must offer HQ swapping without jarring spinner or position changes.
- For video, preserve thumbnails, controls and playback semantics, bandwidth-conscious loading, and non-autoplay defaults where appropriate.
- Report transfer bytes and media errors if a change touches image loaders or CDN variants.

## 6. Screen-by-screen functional contracts

| Workspace | Mobile and tablet adaptation | Must preserve |
| --- | --- | --- |
| App navigation | Phone bottom tabs + More, tablet nav, safe-area; no invisible destinations | Role-filtered links, active route |
| Asset Explorer / Shared | Fluid asset grid; compact filters; image viewer full-height; local list overflow | Selection, copy/move, drag or touch picker, share, search, video |
| Visual Search | Responsive crop/editor, recent visual searches always discoverable, preview size adapted to DPR | Crop accuracy, query and result history |
| AI Operations | Scrollable tabs; KPIs reflow; responsive job list and dialogs | Queue state, logs, retry/version outputs, permission |
| RRUGC Stage 0–5 | Source/references/outputs in adapted cards or local tables; swipeable masonry and tabs | Scouting, stage progression, grouped embroidery, skill results, job states |
| Inventory / Dashboard | KPI cards reflow, long data tables within local scroll, clear filters | Sorting, searching, edit/approve permissions |
| Review Board / Public Review | Stacked detail panels, touch annotation alternative and full-viewer modal | Guest/session and tenant security, review state |
| Job Queue / Access Management | Responsive filters, action overflow, explicit confirmations | Retry/management actions, RBAC/tenant checks |
| Video Generation / Settings | Responsive editor, form validation, image/video previews | Upload flow, generation state, credentials protection |
| All new routes | Must register mobile/tablet/desktop layout and test fixture/profile | Equivalent capabilities at each width |

Do not change stage labels/numbers solely from this document: current repository behavior and user-approved workflow remain authoritative.

## 7. Modal, drawer, table and navigation contracts

A responsive modal must have focus containment, Escape/close behavior, focus restoration, scroll-lock without causing underlying grid width shift, logical safe-area padding, usable soft-keyboard behavior and content scrolling. A destructive confirmation must not execute due to inadvertent gesture. Masonry grids use progressive rendering and scroll stability. Data tables may scroll horizontally **within a labeled local wrapper** when necessary, but page-level overflow is a failure for ordinary routes. Pin central selectors and preserve drag/keyboard navigation semantics.

## 8. Requirement for EVERY future UI task

### Definition of Ready

Before implementing, record: affected route/component; permissions/user roles; intended phone/tablet/desktop layout; narrow/landscape cases; image/DPR concerns; interaction states; relevant Browser fixture and acceptance assertions. For cross-workspace edits, identify all dependent route profiles.

### Definition of Done

A new feature or a meaningful UI modification **must**:
1. Be functionally usable in a phone viewport, a tablet viewport, and a desktop viewport; do not use "works on 1440px" as responsive proof.
2. Pass the applicable QA states: default, hover, selected, focus, long text, loading/error, modal open/close, navigation, touch and resize/orientation.
3. Have captured test evidence for at least one representative viewport in all three classes. Critical/shared controls require 320, 390, 440, 768, 1024 and 1440 CSS px, plus affected breakpoint edges, where feasible.
4. Preserve business actions and authorization. No body overflow or controls obscured by safe-area/keyboard. No critical console/page/network failures.
5. Pass relevant source checks and the existing visual Browser gates, or explicitly report the blocked tests as NOT_RUN rather than inventing PASS.
6. Disclose device emulation limits. Major releases also require real iOS Safari, Android Chrome and tablet Safari coverage when devices are available.
7. Attach responsive evidence and open risks to the final task report. If a requirement cannot fit or is intentionally unsupported, obtain an explicit approved exception; never silently omit mobile functionality.

This contract is durable: **agents must apply it to feature requests that do not mention devices.** Agent-specific rules in .cursor/rules/cam-ui-design.mdc must link here.

## 9. Integration order and rollout

Phase 0: inventory current CSS cascade, source route map, record baseline screenshots and responsive failures. Do not overwrite working-tree changes or dist builds.

Phase 1: establish shared tokens and safe-area/overlay conventions with minimal scope. Test all shared navigation profiles.

Phase 2: adapt Asset Explorer, Shared, visual search, media viewer and modal components without changing APIs.

Phase 3: adapt AI Operations, RRUGC stages, job queue, logs and image sliders; separate image/display improvements from workflow changes.

Phase 4: adapt remaining workspaces and native desktop window-resize behavior where applicable.

Phase 5: run profile-aware UI tests, full cross-workspace Browser matrix, real-device milestone checks, staging verification, documented canary and rollback. Production deploy is a separate explicit user-authorized action.

For any slice: audit > scoped edit > typecheck/tests > targeted Browser QA > final UI gate > Git review > staging review > authorized release. Record current commit/build-info, compare before/after screenshots, and keep rollback to the prior known-good release. Never silently update visual baselines to make regressions pass.

## 10. Source of truth and references

- AGENTS.md: universal AI-coding-agent requirements and production security boundary.
- docs/operations/UI_AUTOMATION_WORKFLOW.md: actual CAM runner, fixture, profile selection and baseline governance.
- docs/operations/CAM_MULTI_DEVICE_QA.md: multi-device QA matrix and future-feature checklist.
- .cursor/rules/cam-ui-design.mdc: Cursor-specific handoff and screenshot workflow.
- apps/client/styles/ui-foundation.css: actual current shell breakpoint/token source.
- apps/client/scripts/ui-qa.mjs: actual QA engine; do not replace with a generic starter runner.

References: MDN viewport and responsive images; Playwright emulation; W3C WCAG 2.2. See the current technology versions in apps/client/package.json.

**Important:** This document sets a development and acceptance policy; merging docs does NOT mean responsive changes are already deployed or all pages are QA-certified.
