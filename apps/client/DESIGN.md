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

## QA and deployment

- Verify no overlap or horizontal document overflow at 375, 768, 1024, 1440 and 1920px; tables are allowed their *own* horizontal overflow at desktop.
- Test keyboard focus and buttons, tabs, sorting, batch selection, Picked Design PNG and TikTok SEO modal.
- Run `npm --prefix apps/client test -- app/etsy-spy/EtsySpyPage.test.tsx app/etsy-spy/TikTokListingCell.test.tsx` and `npm --prefix apps/client run build` before release.
- Deploy **frontend only** from the reviewed `main` commit using `scripts/deploy-cam-frontend.sh`, keep current + previous and validate public build info.
- Stage 0–2 and the rest of CAM should adopt these token *roles* in separate tested releases, not through a global CSS reset.
