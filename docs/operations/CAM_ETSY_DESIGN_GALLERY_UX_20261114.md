# Etsy Spy Picked — Design placement gallery UX (2026-11-14)

## UI / data contract

- Scope: `apps/client/app/etsy-spy/DesignCell.tsx`, `EtsySpyPage.css`. Existing `Listing.designs: DesignAsset[]` already exposes source-defined `position` and `variant`; no API, database or processing change.
- The Design cell remains **image-only**, with the existing bottom-left on-hover original preview and click-to-open gallery. No duplicate QA/status text is added to the table.
- The dialog groups designs into print locations: Front, Back, Left/Right sleeve, chest/pocket and other supplied positions. Groups appear only when backed by real assets; unknown position names are displayed as data rather than invented. Each group has a grounded count, and within it the sidebar displays only that location's actual variants.
- The selected original PNG stays on a transparent checkerboard stage; background swatches are **preview-only**. Original art, layout, colors, alpha and existing thumbnail/original URLs are never altered. No print-ready or rights-clearance claim.
- Previous/next and keyboard arrows iterate through all artwork in source order, automatically switching the active location. Clicking a location selects its first design; clicking a variant switches the preview. The existing Escape-to-close, focus trap, focus restoration, portal scroll lock and background reset on reopen remain.
- Desktop: large contained image stage plus ~308px inspector. Tablet: stage and ~268px inspector. Phone: full-height modal with stage above a vertically scrollable location/variant/color inspector; location/variant rows scroll horizontally and remain touch usable, with safe-area padding. Long listing titles are truncated visually only, full text retained in title attribute.

## Verification

- `npm test -- app/etsy-spy/DesignCell.accessibility.test.tsx app/etsy-spy/EtsySpyPage.test.tsx`: **52/52 PASS** using the existing shared node_modules cache.
- Scoped strict TypeScript compile for DesignCell: **PASS**.
- Full `npm run typecheck`: **NOT CLEAN** in the shared test-dependency environment due to pre-existing Public Review Tiptap mismatch and an omitted Stage 2 JSON asset in the sparse checkout; neither diagnostic points into the affected UI files. Run locked CI before any release.
- Isolated Chrome `--no-sandbox` synthetic fixture captures and automated geometry/interaction assertions: PASS at **320x700, 390x844, 768x1024, 844x390, 1440x900**. Each verified modal bounds, no document horizontal overflow, stage >=170px height, position/variant counts, selecting Back, background switch and Escape. Local QA screenshots are at `apps/client/.ui-qa/design-gallery-20261114/` in the development worktree (not user artwork).
- Real iOS Safari / Android Chrome / iPad Safari: **NOT RUN**. No production data or credentials used by fixture.
- Dist artifacts must be rebuilt only in an approved clean release; do not commit unrelated `dist` churn or modify the pending Etsy adaptive UX checkout.

## Follow-up — Picked Design thumbnail intrinsic width (2026-11-14)

- The Picked > Design cell uses the same visual image height as Product/Etsy: 68 CSS px on desktop/tablet, 76 px on mobile. Image width is automatic and no square HTML width/height attributes are set, preserving wide, square and sleeve aspect ratios.
- Buttons use content width with 44 px minimum touch target and no square crop. On desktop the Design rail is bounded within the existing 242 px table column (224 px content width); multiple or wide designs scroll **inside the cell**, not the entire table. On mobile the rail fills the card.
- Reset the legacy 1010 px products-table minimum width only in the mobile-card breakpoint (<=680 px). No changes to PNG, source URLs, lazy image IO, hover previews, gallery, API or processing.
- Regression: 53/53 targeted Etsy Spy React tests PASS; full TypeScript typecheck PASS. Firefox fixture PASS at 1440x900, 768x1024, 390x844 and 320x700, with portrait (1:4), square (1:1) and wide (3:1) artwork. Image/button heights align with the Product thumbnail, ratios are preserved, and gallery/Escape and overflow checks pass.
- Production rollout requires committed dist provenance, CI green, one rollback retained, and read-only public post-deploy smoke. Authenticated Picked coverage is a separate check.

## Rollback / release

Pure frontend change. No migration/flag/credential/API cost. Revert the scoped component, CSS and test changes if unexpected browser regressions occur. Release from reviewed, green `main` with current + rollback retained; verify modal on a real Picked multi-placement listing and preserve existing table/modal refresh behavior.
