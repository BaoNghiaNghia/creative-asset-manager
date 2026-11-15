# ColorMatch V8: 10 automatic C1717 preview colors per design

## Product requirement
- Remove the "Phương pháp in dự kiến" and "Lưu màu đã chọn /
  Không phù hợp" blocks from the Picked Design modal.
- Each real, non-empty, SHA-verified Design PNG receives **exactly ten**
  distinct configured SMS C1717 garment color suggestions.
- Automatically select the highest-ranked garment background in the modal
  without requiring user actions. Keep optional click-to-preview swatches,
  transparent checkerboard and the full 72-color picker.
- Historical explicit feedback endpoints/data remain intact, but the modal
  performs neither feedback GETs nor POSTs or passive tracking.

## Risk-aware recommendation strategy
- Rank ink/readability-safe candidates first; use per-artwork pixel contrast
  and the bounded SMS sales-speed prior to break ties.
- If fewer than ten distinct verified-safe garment colors exist, fill the
  remaining slots with highest measured readability margins, **marked**
  as review candidates. They are not asserted printable, legible or safe.
- V8 records `recommended` (exactly 10), `eligible_colors` (verified
  safe, possibly empty), `unsafe_colors` (hard-gate failures), and
  `review_colors` (top-ten suggestions not in eligible set).
- In the UI, review slots have a dashed outline and warning tooltips;
  selecting one displays a contrast/print-proof caution. These warnings
  are retained although the optional print-method dropdown is gone.
- Product-wide intersections continue to use ONLY `eligible_colors`
  for each distinct actual print position. Never treat a review-only color
  as safe across several placements.
- Truly empty transparent or corrupt PNGs do not receive invented color
  rankings. Sparse tiny designs are analyzed using actual thumbnail ink
  pixels rather than dropped by the sampled-pixel threshold.

## Production and rollback
- Algorithm `colormatch-sms-c1717-v8` in both backend and frontend.
- V7 records remain readable until SHA-guarded V8 backfill.
- No database migrations, new images, prompts or regeneration.
- Retain current and one rollback release. Use official backend/frontend
  deployment scripts, same commit hash, then tenant-only idempotent
  ColorMatch backfill.
- Backfill updates `color_match` JSON only, never PNG master or Google Drive
  original. Final dry-run must report 0 Drive/local targets and 0 errors.
- Report `updated_zero_safe` and `updated_with_review_colors` from
  `eligible_colors` and `review_colors` (not from ten UI slots).
- Usability check: Design modal opens with 10 C1717 colors; no print-method
  selector, no save/reject buttons and no feedback network requests.

## Important limitation
The ten colors are ranked *preview suggestions*, not ten validated production
print colors. Designs mixing black and white text may have zero hard-safe
options. These still show ten honest, marked review candidates and require
artwork changes or a real print proof before production.
