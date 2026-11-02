# CAM ThreadTransfer V2 - Products Stage 2 Slots 2-7

## Objective
Extend the already-deployed Slot 1 saved Embroidery Reference into the remaining
Products Stage 2 outputs. Do not alter Reviews workflows or previously
completed output versions. All processing jobs retain current IDs, storage,
output-version audit, retry and sequential Run All contracts.

## Slots
- Slot 2 (3/4): exact manufacturer 3/4 stock + pinned Slot 1 photographic
  reference, deterministic perspective projection and tiny front-crown mask.
  Zero new Codex/ImageGen calls. No reshooting or resizing factory photo.
- Slot 3 (90-degree side): use true official side stock and the last visible
  ~18 percent of source front yarn projected only into narrow front-edge strip.
  Never paste a complete slogan onto the lateral side. Zero model calls.
- Slot 4 (back): exact manufacturer back image byte-preserving, as previously
  implemented; now queue under a local provider-neutral job.
- Slot 5 (Multi-hat): keep three individual photos and approved Cap Stack Scout
  references plus official stock palette. One Skill-based scene-generation
  dispatch per shot; preserve the native scene. New jobs do not run a second
  ImageGen thread-inpaint dispatch. The selected saved yarn macro is supplied
  directly as a reference to the single scene generation.
- Slot 6 (UGC): same one-dispatch scene path, retaining independent
  per-shot approved Scout reference, permissions and feedback. The photo must
  still be rendered once if a new lifestyle composition is needed.
- Slot 7 (12 Natural-series colorways): exact official front stock for each
  Natural color; locally project the single saved Slot 1 yarn reference.
  Zero Codex/ImageGen calls per color. Reviews' legacy 13-color pipeline
  continues independently.

## Provenance, QA and compatibility
- The source revision, stock photo hash, reference remote asset and source
  design SHA remain pinned. V2 mode is fixed in the processing payload.
- All original stock pixels outside the narrow mask remain byte-identical.
  An unsegmentable/flat reference fails without publishing a fake output.
- Input file, target dimensions and segmentation are bounded.
- V1 jobs already queued retain their pinned V1 payload, including the
  historical second inpaint for old scene jobs. Explicit Retry/Regenerate
  creates a new V2 payload; old completed outputs stay in immutable history.
- QA is deterministic for factory stock, but **one-pass scene generation is
  not a deterministic per-cap thread overlay**: Multi-hat/UGC cap location and
  occlusions are unknown without separately approved per-cap segmentation.
  Do not claim pixel-perfect embroidery in scenes. Review 3 representative
  sample outputs before any bulk rerun. No automatic bulk backfill here.
- Metadata records threadtransfer-v2-angle, scene-single-pass-v2 or
  threadtransfer-v2-colorway for new output versions.

## Release
- Commit/build/test on main. Deploy frontend and backend together using
  official immutable release scripts and schema compatibility check.
- Preserve one rollback backend/frontend release and production data.
- No schema migration, provider secret updates or new external dependencies.
