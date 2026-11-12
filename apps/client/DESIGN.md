# CAM frontend design system — evondevKit adaptation (2026-11-12)

## Source, authorization, and boundaries

- UI/UX guidance: [evondevKit](https://github.com/evondev/evondevKit), MIT © 2026 Tuấn Trần, pinned to `5a892ceac93d1bb286391663eb01cbd011706886`.
- Install location for local **Codex CLI**: `<repo>/.agents/skills/ui-ux/`. Invoke `$ui-ux` in the repository checkout. Do not install the UX skill into the TikTok listing worker's production Codex HOME: that is a separate, restricted content-generation environment.
- This is a **design guidance skill**, not a React library or a runtime dependency. No automatic upstream upgrades; review before changing the pin.
- React 18 + TypeScript + Vite + existing CSS remain authoritative. Preserve existing component markup, accessible labels, handlers, tables, API routes and worker state. This pass is frontend-only.
- The original evondevKit skill license is included at `.agents/skills/ui-ux/LICENSE`.
- The skill's `audit.sh` was not run because the remote execution policy blocked it; manual audit of package.json, existing component inventory, CSS, app labels, responsive rules, and scrollbar rules performed instead.

## Visual direction

**Clean / Data-table-first / Light / Neutral.** Content is data-dense yet readable. Keep the existing blue brand, Be Vietnam Pro, and CAM navigation. No glass, gradient, shadows on regular cards, or colorful decorative badges. Status colors are reserved for success, warning, and errors; avoid pastel panels in every table cell.

**Desktop layout:** header → tab rail → overview counters → filtering controls → sticky-header table. Default is an operational workspace, not a collection of floating dashboard cards.

**Mobile layout:** preserve app navigation; overview counters become a horizontal swipeable strip; filters stack; product records remain cards with clear primary action; modal stays in one scrollable sheet. Respect safe-area inset and reachable controls.

## Tokens

Defined in `app/styles/cam-evo-tokens.css` with `--cam-evo-` prefix:

| Token group | Contract |
|---|---|
| Color | brand #265cbd; hover #1b4a9a; background #f6f8fb; surface #ffffff; ink #17243a; muted #64748b |
| Border | hairline #e8edf4; input #cdd7e4; focus #3674d4 |
| Spacing | 4 / 8 / 12 / 16 / 24 (px) plus 6 and 10 for compact controls |
| Radii | 6 controls, 10 cards, 14 dialogs |
| Typography | 12–13px body, 11–12px table, 10–11px labels, 16px section headings |
| States | neutral/warning/error/success with text and noncolor signal |

Reuse the current `.etsy-btn`, `.etsy-pill`, `.etsy-stat`, `.etsy-table-*`, `.etsy-tiktok-*` rather than implementing competing Button/Badge/Modal React components. This keeps current data handlers and behaviors intact.

## Phase 2 — GatorHats Products Stage 0–2 (2026-11-12)

This phase adapts the same pinned evondevKit `ui-ux` guidance (no upstream change) to GatorHats Products. GatorHats has its own workflow CSS/component library; do **not** replace it or introduce a second table, card, button or modal runtime system.

- Style entrypoint: `app/realistic-review-ugc/GatorHatsEvo.css`, imported **last** by `RealisticReviewUgcPage.tsx` after existing Stage CSS and `cam-evo-tokens.css`.
- All rules are scoped to `.rrugc-shell.rrugc-evo-products`. Reviews Stage 1–5 keeps its existing styling.
- Stage 0: more legible table labels, quiet sticky header, transparent source image handling, one-row KPI strip on mobile, retained internal table horizontal scroll. Zero-volume, Used and Suggested remain real filters with their existing handlers.
- Stage 1: compact creative brief/generation actions, readable keyword/status columns, image strip keeps original dimensions, drag-to-scroll, pick/approve controls and saved output URLs.
- Stage 2: streamlined 13-color and seven-slot layout hierarchy, status and actions. No changes to Stage 2 slot count, crop/placement, image generation, embroidery method or queue handling.
- Shared header/tabs are 4-stage Products navigation, retaining Stage 3 video link and ARIA/keyboard behavior. Never hide Stage 3 or remove its navigation to simplify Stage 0–2.
- Typography floor: 10–11px table and control labels at desktop; 11px+ content, 36px+ primary touch controls on mobile. Status badges remain semantic; active filters use one consistent brand-blue outline.
- Quality gate: build, focused Stage 0–2 regression tests, Chrome responsive fixture at 390/768/1440 and no page-wide overflow. Avoid browser reload or forced modal closure during any live data refresh.
- Audit: manual component/CSS inventory completed; upstream `audit.sh` was blocked by remote execution policy. No new UI runtime dependency.

## Phase 3 — GatorHats Reviews Stage 1–5 (2026-11-12)

Continue with the pinned evondevKit `ui-ux` skill as design guidance, **not** as an additional React dependency. Reviews has distinct operational screens for Cap Stack Scout (1), embroidery colors (2), Pinterest source references (3), image-generation queue (4) and synthetic UGC concept review (5).

- Style entrypoint: `app/realistic-review-ugc/GatorHatsReviewsEvo.css`, imported after the existing GatorHats Products stylesheet. Reviews is the only workflow receiving `.rrugc-evo-reviews` root scope. Switching workflows removes the previous CSS scope without remounting the page.
- Shared UI: same blue CAM tokens, compact management actions and a **five-stage** focus-visible tab rail with keyboard navigation and preserved URLs. Do not convert tabs into independent route reloads.
- Stage 1 Scout: single overview and toolbar; lean keyword/search controls; readable photo labels, unchanged masonry ratios and feedback controls. Neutral card treatment, retain semantic approved/rejected/AI differences.
- Stage 2 Embroidery: legible table, source metadata, true 13-color output strip, unchanged ordering/preview/retry semantics; horizontal table scroll remains internal.
- Stage 3 Pinterest references: flat header/KPIs, one search-and-sort toolbar, clearer source/references/status rows, unchanged folder navigation/feedback/gallery.
- Stage 4 Image Generation: quieter queue status badges, compact search/actions, readable source and output columns, unchanged selection, regeneration, logs and quota pause.
- Stage 5 UGC Review: shorter toolbar with visible count/filter actions and two-column narrow gallery, photo previews and sample-origin data remain unchanged; destructive confirmation stays explicit.
- Responsiveness: no page-level horizontal overflow at 320, 390, 768, 1024 and 1440 px. Wide tables own horizontal scroll; KPI rows are swipeable on phones. Do not reset modal state or modify worker polling.
- Quality gate: workflow scope test, existing feature regression tests, Vite/TypeScript build, fixture UI QA across Stage 1–5 and five viewports, read-only production smoke. No API/database/agent changes.
- Audit: manual DESIGN/stack/components/CSS inventory. Vendored upstream `audit.sh` remains blocked by the remote execution policy. Design uses existing controls and CSS tokens.

## Phase 4 — Asset Explorer, Job Queue, Inventory, Access Management (2026-11-12)

Continue the pinned evondevKit `ui-ux` visual roles using a **single route-scoped adapter** `styles/cam-console-evo.css`, imported last by `app/main.tsx` after the existing app styles. Tokens come from `app/styles/cam-evo-tokens.css`. This is a visual styling pass, not a component migration or new runtime library.

- **Asset Explorer**: `.cam-evo-explorer` root class in the existing `App`, no changes to source selection, grid/list mode, hover overlays, file-type previews, drag/drop, upload queue, keyboard shortcuts, asset details, or Drive auth.
- **Job Queue**: `.job-queue-shell` root; five existing status tabs, search/provider/rows controls, KPI strip, readable internal-scroll table and generation-result dialog. All queue data and refresh actions unchanged.
- **Inventory**: `.inventory-workspace` root; compact daily summary, three process cards, candidate review, Catinat exception and shift-readiness disclosure panels. Do not imply unverified inventory was reconciled; retain caution/error semantics and action approvals.
- **Access Management**: `.access-shell` root; Members/Roles/My Access tab rail, filters, tenant access table, compact manage dialog and explicit destructive confirmations. Permission enforcement remains server-authoritative; never conceal access controls.
- **Modal safety**: existing mounted React modal state, keyboard close, overflow locking and focus behavior must not be overwritten. This phase changes CSS only, plus explorer root class and CSS imports. No forced reload, poll timer, API, DB or worker change.
- **Responsive**: page-wide overflow prohibited at 320, 390, 768, 1024, 1440px. Long queue/admin tables scroll *inside* their wrappers; phone KPI rows swipe independently; controls remain keyboard-focusable.
- **Validation**: narrow-scope CSS contract regression tests, existing 4-domain functional tests, production Vite build, read-only Chrome fixture QA and production smoke, current + rollback frontend releases.

## Phase 5 — AI Operations, Branding DNA, TikTokShop, Review Board (2026-11-12)

Continue the pinned evondevKit `ui-ux` design guidance, **not** a new React/runtime framework. All four operational screens share a neutral CAM blue/white hierarchy while retaining independent workflows.

- Stylesheet: `styles/cam-platform-evo.css`, imported after the Phase 4 adapter in `app/main.tsx`. Reuse existing `cam-evo-tokens.css`. All selectors are route-scoped to `.cam-evo-ai-operations`, `.branding-dna-page`, `.rrugc-shell.rrugc-tiktokshop-workspace`, and `.review-board-shell`. No global reset or GatorHats/Etsy mutation.
- **AI Operations**: add one root CSS scope to `AiOperationsShell`; maintain all existing tabs, drag-to-scroll, dashboard filters, refresh cadence, provider status and worker pause/resume controls. Compact tab rail, summary, table and chart cards; no query or metrics changes.
- **Branding DNA**: compact six-stage tab rail, shop registry and worker panels with transparent readiness indicators. Scanner remains *not connected* and evidence remains *unverified* where the API says so. Do not promote prototypes to live operations.
- **TikTokShop**: solely Stage 3, 4, 5 isolated by the TikTokShop source root, compact management/actions, readable source and output tables, internal-scroll data, preview/gallery cards and settings. Preserve source-root API isolation, image quality QA and internal draft status. Never depict synthetic drafts as real consumer reviews.
- **Review Board**: compact stats, filter/search, shared and RRUGC modes, maintain three-pane scroll geometry, comments/annotations and full approve/reject UI. Do not auto-close selected issue or reset inspector on refresh.
- Preserve existing modal lifecycles, tab selection, data mutation feedback, keyboard focus and asynchronous retry behavior. No API, database, auth, worker or polling changes. Responsive target 320, 390, 768, 1024 and 1440 px, with tables scrolling inside their own wrappers.
- Quality: production Vite build + focused feature regression, CSS isolation contract test, Chrome fixture QA including mode switch/tab navigation. Release frontend-only from merged `main`, preserve current + previous and run read-only production smoke.

## Phase 6 — Cross-workspace feedback, skeletons, modal rhythm and render efficiency (2026-11-12)

Continue the pinned evondevKit `ui-ux` direction. This remains a local adaptation of the UI design skill (not a new runtime library), with original business rules and user interaction semantics authoritative.

- New reusable components `app/components/WorkspaceFeedbackState.tsx`: semantic *loading / empty / error* states, accessible labels, optional manual retry and bounded, non-interactive list-row skeletons. Avoid full-page loaders when a view has previously loaded data.
- Adopt in **Job Queue** table and both **Review Board** modes; also normalize existing AI Operations dashboard states, Branding DNA empty/error indicators and TikTokShop errors with phase-6 CSS without replacing the original handlers. Error messages must never be mislabeled as an empty result.
- `styles/cam-feedback-evo.css` imported last, after phase-5 adapter. Reuse existing `--cam-evo-` tokens. Status styling has text and an optional button, not color alone. Skeletons are disabled when reduced motion is preferred.
- **Job Queue request safety**: in-flight responses from prior filters/pages and unmounted screens are ignored using a monotonic version number. Keep the current table during manual Refresh, show a small updating status, and preserve the selected result modal. No network polling is added or removed.
- **Job Queue render performance**: a singleton duration clock is consumed only by running-duration cells, not the page. The clock starts with the first subscribed running cell and stops with the last; all non-processing row cells remain static until data updates. This avoids a full table re-render every second.
- Keep all existing modal portals/backdrops, Escape-to-close, scroll lock, focus return and forms. The adapter alters surface, typography and focus outlines only. A global toast remains a lightweight action confirmation with existing 5-second duration/3-visible limit.
- Responsive/reliability gate: no document overflow at 320/390/768/1024/1440px; preserve internal table scrolling, image previews and selection; verify error/empty/loading, stale request suppression, duration timer cleanup, route navigation and production build. Frontend-only rollout from merged `main`, retaining current and rollback releases.
- The new shared state component is incremental: other pages can opt in in future releases. No broad markup refactor, new dependency, API/DB/worker change or automatic modal closure.

## QA and deployment

- Verify no overlap or horizontal document overflow at 375, 768, 1024, 1440 and 1920px; tables are allowed their *own* horizontal overflow at desktop.
- Test keyboard focus and buttons, tabs, sorting, batch selection, Picked Design PNG and TikTok SEO modal.
- Run `npm --prefix apps/client test -- app/etsy-spy/EtsySpyPage.test.tsx app/etsy-spy/TikTokListingCell.test.tsx` and `npm --prefix apps/client run build` before release.
- Deploy **frontend only** from the reviewed `main` commit using `scripts/deploy-cam-frontend.sh`, keep current + previous and validate public build info.
- Stage 0–2 and the rest of CAM should adopt these token *roles* in separate tested releases, not through a global CSS reset.
