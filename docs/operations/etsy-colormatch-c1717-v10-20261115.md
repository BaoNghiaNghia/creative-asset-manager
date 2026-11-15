# ColorMatch V10 — role-aware garment-color recommendations

## Scope
- Runs on existing Picked design PNG (192px alpha thumbnail, optional SHA-bound medium analysis up to 768px), never generates or changes image pixels.
- Chooses among configured C1717 colors with known HEX. Other SMS/supplier and legacy swatches remain in the manual group.
- This is NOT print certification; DTG/DTF garment underbases, inventory and final color require sample review.

## Algorithm
1. Bounded deterministic samples of visible alpha ink families and local edge saliency, retaining minority pigments.
2. Probable text ROIs from morphology (not OCR). Medium ROI sampling refines text brightness/alpha. Text thresholds are not forced on every illustration pigment.
3. Alpha-composited contrast plus salience-weighted illustration visibility, perceptual color difference and harmony.
4. CIEDE2000 using D65 CIELAB; muted-garment preference is a small heuristic, not exact Google Material HCT.
5. Score weights: 40% text readability, 25% illustration visibility, 25% color harmony, 10% print-review factors. Static sales prior below 1 point.
6. Rank at most ten choices. High-confidence choices remain separate from at most five conditional preview-only colors. Some designs can still have zero good options.
7. Store compact rejection reasons and SHA-bound normalized ROIs. No OCR, external AI API, or new artwork files.

## UX
- Keep two palette sections: best colors and the remaining choices.
- If all suggestions are conditional, label top section as preview candidates.
- Conditional swatches use a subtle dashed border and one concise review note.
- Shared color across multiple positions intersects high-confidence colors only.
- Never label preview-only candidates as safe to print.

## Release / backfill
1. Deploy backend V10 before frontend using a single committed release SHA, retaining current + one rollback.
2. Verify backend health and frontend build info.
3. Production dry-run for creative-assets-prod; apply tenant-scoped idempotent SHA-bound metadata backfill with page size 40 and at least 0.5 second delay if authorized.
4. Final dry-run counts only validated V10 as fresh; V9 remains API-readable until refreshed.
5. Backfill changes only the color_match JSON on completed designs, not the original artwork or Drive files.

## Guarded preview calibration (V10 policy revision)
- Keep the high-confidence readability gate unchanged.
- Reduce only the conditional preview score floor from 38 to 34 and the
  preview relative-rank floor from max(40, 78% best score) to
  max(34, 78% best score). Preserve the minimum 1.8:1 ink contrast,
  23 illustration visibility points, and severe-visibility rejection.
- Preview colors are still explicitly review-only. Very low-opacity
  ink that cannot clear the contrast floor remains without suggestions.
- Set analysis.review_policy to guarded-preview-floor-v2.
- Only V10 designs with empty existing suggestions and missing this
  policy marker are stale for backfill. V10 designs with existing valid
  suggestions are intentionally preserved. Empty designs that still have
  zero acceptable previews are processed once, not indefinitely.
- PNG files, supplier palette and frontend's two-group layout stay unchanged.

## Known limitations
- Region classification is geometry-only, so script/serif text and small punctuation may be misclassified.
- CIEDE2000 measures color difference, not universal aesthetic quality.
- Recalibrate against actual production examples and operator picks before modifying hard-gates globally.
