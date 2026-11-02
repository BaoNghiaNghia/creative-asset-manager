# CAM Stage 2 Slots 2-3: One-pass Codex Direct Angle Edit V3

## Rollout scope
- Only **GatorHats Products, Stage 2, slots 2 (3/4) and 3 (true side)**.
- Slot 1 reference, slots 4-7, GatorHats Reviews and TikTokShop unaffected.
- No schema migration, no forced bulk regeneration. Old pending V2 jobs continue
  using their pinned `thread_material_pipeline=v2` payload. New queue/retry/
  regenerate jobs use `thread_material_pipeline=v3-direct-edit` and Codex.

## Native edit (one Codex CLI call per output)
1. A = official selected-color stock 3/4 or side photo, the actual EDIT TARGET
   (not a newly generated cap or a front/side composite). Stock SHA remains pinned.
2. B = saved native Slot 1 Embroidery Reference PNG, exact thread texture/color.
3. C = original Stage 1 design PNG for text/spelling/layout check.
4. Isolated `CodexImageGenRunner` uses built-in
   `output_contract=stage2_angle_direct_single`, calls native `$imagegen` once
   in IMAGE-EDIT mode. No Skill ZIP, no mask image, no second inpaint/refiner.
5. Native tool's PNG must match the **one and only** actual imagegen cache output
   by SHA-256. A fabricated Python PNG or another image cannot pass provenance.

## Raster QA after native edit
- Decode stock/candidate; require same aspect ratio. Native ImageGen can return
  a different pixel size: only the candidate yarn may be aligned/resampled.
- Pull allowed yarn colors from actual saved reference; identify plausible new
  stitched pigment contours within the physically relevant front-crown field.
- Composite **stitch-shaped pixels only** onto original stock. This is a
  protective output gate, NOT a rectangular inpaint mask sent to ImageGen.
- Reject broad colored rectangles, flat printing, wrong/absent front embroidery
  and mismatched provenance before saving Slot 2.
- True side faces left (toward visor); do not put a full slogan near the side
  logo. If no credible tiny front-edge embroidery is visible, save the original
  stock unchanged as an explicit `side_stock_fallback` in Output History.
- Factory branding and all other stock pixels outside the accepted stitch
  contours stay exactly unchanged; no reshot hat is published.

## Cost, retries, observability
- A new Slot 2 or Slot 3 job spends at most **one image-generation call per attempt**.
  No automatic second pass. Provider/transport failures can use existing bounded
  retries; visual QA failures are nonretryable (prevent token waste).
- Slot 3 safe fallback does not call ImageGen again; it records the rejected
  result's reason and `side_stock_fallback=true` in immutable Output History.
- Completed old versions are never overwritten; output history/source hash
  retains stock and reference IDs. No scene/UGC colorway changes.
- This is not a guarantee of perfect physical perspective/text recognition.
  Review 3 representative outputs for both angles before any larger rerun.

## Release / rollback
- Commit from main; run backend angle runner/QA/queue tests and frontend tests.
- Deploy using `cam-rebuild-backend.sh --no-migrate` then
  `deploy-cam-frontend.sh`, preserving one rollback release.
- Rollback backend/frontend to prior release restores V2 for new jobs; native
  output versions and source assets remain intact.
