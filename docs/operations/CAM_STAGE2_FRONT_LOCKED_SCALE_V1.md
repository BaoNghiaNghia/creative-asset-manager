# CAM Stage 2: Front-Locked Embroidery Scale V1

## Problem
One Slot 1 embroidery design had different *apparent physical sizes* across
front, three-quarter and true side: the 3/4 edit could enlarge lettering or
shift it toward seams; the side view physically reveals only a front-edge
sliver. The material reference itself is NOT a size reference.

## Scope
- New **Products Stage 2** Slot 1 ThreadTransfer placement, Slot 2 3/4,
  Slot 3 genuine right side 90 degrees. Reviews, TikTokShop, UGC, Multi-hat,
  Slot 4 back and Slot 7 colorways remain unchanged.
- No migrations or automatic bulk regeneration, no extra ImageGen calls.
- Previously queued attempts without `embroidery_scale_front_v1` /
  `embroidery_placement_v1` retain their original placement.
- Existing output versions remain immutable; explicit Slot 1 Retry Placement
  recalibrates with the *saved* Embroidery Reference, without new ImageGen.

## Algorithm
1. Fresh Slot 1 jobs pin `embroidery_scale_front_v1` in their processing
   payload using the calibrated front guide, design-specific size percentage,
   and bounded real stock safe zone.
2. Slot 1 raster-composites *only isolated photographic yarn* within that
   scale and front crown center; it retains the original factory photo.
3. The **actual output thread bounding box** and the exact saved Embroidery
   Reference remote ID are recorded in immutable Output History as
   `embroidery_placement_v1.front_box` (normalized photo coordinates).
4. When new Slot 2/3 jobs queue, they pin the latest **completed Slot 1**
   history's actual master box, not the generated reference's background
   dimensions or current global UI zoom. Historical versions with a
   `slot1_embroidery_pipeline.transfer_box` are upgraded at queue time;
   imported history lacking a verified box uses a bounded conservative
   fallback with `legacy_fallback=true`.
5. Both angle jobs continue to send exactly two images: original official
   stock as the Codex CLI image-edit target plus the saved photographic
   Embroidery Reference as the only reference. The prompt includes the
   projected location and expected apparent size; no extra mask or pass.
6. Native yarn QA extracts accepted stitch pigment and projects a bounded
   *uniformly scaled stitch-shaped layer* to the master-derived stock
   position, not a rectangular patch. Every stock pixel outside the yarn
   remains unchanged; no cap redraw, logo replacement or artificial angle.
7. 3/4 apparent width ≈ 78% of the front master *photo-normalized* width.
   At true 90-degree side, the frontal design cannot be fully visible:
   only ≈ 13% of front master width is permitted in the front-edge strip.
   Side failure falls back to original factory stock, never invents a slogan.

## Guardrails
- Center calibration is bounded within front cap crown. No seam overlap.
- Extreme native rescale, insufficient stitch detail, broad colored patch,
  unexpected frame ratio or unpinned reference is rejected before saving.
- Retry/Regenerate re-pins from the latest completed Slot 1 reference/output.
  In-flight jobs retain their original snapshot and can be reviewed separately.
- Showing most of the slogan on a purported true side 90-degree stock is
  impossible without a different verified 65–75-degree factory stock photo.
  Do not invent or turn the original hat using ImageGen.

## Validation
Tests cover successful front yarn transfer, consistent projected 3/4
geometry, true side width, stock pixel protection, oversized off-center
Codex output normalization, reference/revision pinning, queue history
recovery and UI readiness.
