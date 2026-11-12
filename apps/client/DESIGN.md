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

## QA and deployment

- Verify no overlap or horizontal document overflow at 375, 768, 1024, 1440 and 1920px; tables are allowed their *own* horizontal overflow at desktop.
- Test keyboard focus and buttons, tabs, sorting, batch selection, Picked Design PNG and TikTok SEO modal.
- Run `npm --prefix apps/client test -- app/etsy-spy/EtsySpyPage.test.tsx app/etsy-spy/TikTokListingCell.test.tsx` and `npm --prefix apps/client run build` before release.
- Deploy **frontend only** from the reviewed `main` commit using `scripts/deploy-cam-frontend.sh`, keep current + previous and validate public build info.
- Stage 0–2 and the rest of CAM should adopt these token *roles* in separate tested releases, not through a global CSS reset.
