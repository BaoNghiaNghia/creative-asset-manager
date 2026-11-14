# CAM Etsy Spy — ColorMatch V1

**Date:** 2026-11-14
**Scope:** Picked → Design modal, only. Existing PNG/Google Drive workflow unchanged.

## Data flow and performance

New mapped Design uploads already decode the PNG and create one <=192px
alpha-preserving Drive thumbnail. The pre-existing thumbnail generation runs in
`asyncio.to_thread` with a per-process concurrency cap of ONE. A bounded
ColorMatch evaluation (maximum ~2,000 sampled opaque pixels × 22 fixed garment
colors) is performed on the **small thumbnail** in that same worker thread.
No full-resolution design is decoded twice; the original master and Drive
upload bytes are unchanged. The extra computation is isolated from the API
event loop and never runs in Picked listing/render, modal open, switching
position, selecting colors, browser frontend, or image-serving requests.

Analysis is advisory; processing exceptions are swallowed and leave the
existing palette behavior intact. Metadata is stored in the existing
ProcessingJob `payload_json.design_assets[].color_match` (no database
migration, preview images, external API, or provider fee):

```json
{
  "version": "colormatch-pixel-v1",
  "output_sha256": "<exact uploaded artwork SHA256>",
  "recommended": ["ivory", "pepper", "bay", "blue-jean"],
  "method": "thumbnail_pixel_contrast"
}
```

`scores` may exist in stored raw metadata but are not projected to clients
to keep Picked payloads small. Projection verifies the algorithm version, exact
asset hash, unique palette IDs and max 4 items before inclusion. The palette
module has **no Pillow dependency** and loads cheaply in the listing path.
Changing artwork SHA or algorithm version invalidates old recommendations.

## Ranking rationale

Sample only visible alpha pixels, evaluate sRGB linear-light luminance contrast
plus OKLab perceptual separation, penalize 20th-percentile weak details and
low-contrast ink fraction. Prefer a handful of *different* garment colors
rather than near duplicates. These are not formal WCAG scores nor calibrated
dyed-fabric measurements. The algorithm does not guarantee print suitability;
human review remains mandatory.

UI: for cached metadata, auto-display the first suggested background, show
top four colors + checkerboard first, and expose all 22 garment plus 4
legacy preview swatches behind `Xem tất cả màu áo`. A manual choice is
remembered per immutable design SHA in browser session storage; switching
design/position restores its own choice. PNG bytes/URLs never change.
For any missing/stale metadata, the original 27-swatch UI remains fully
accessible, with checkerboard default.

## Scope boundaries and rollout

- **New mapped assets:** recommendations appear automatically after their
  upload succeeds; no new network calls are required when viewing them.
- **Older Design assets:** the UI retains the legacy palette until a separate
  low-priority, rate-limited backfill is explicitly run. The guarded script
  `scripts/cam-etsy-colormatch-backfill.py` defaults to DRY RUN, requires
  `--apply` plus an exact tenant, handles at most 30 assets per invocation,
  waits at least one second between Drive *thumbnail* reads, and rechecks
  the immutable asset SHA under a row lock before updating JSON metadata.
  For a small canary: `nice -n 15 python scripts/cam-etsy-colormatch-backfill.py
  --tenant-id creative-assets-prod --limit 10 --scan-limit 500 --apply`.
  NEVER backfill all items synchronously in a listing request.
- **One shirt/multiple placements:** each artwork is ranked independently.
  A future product-wide `best on every placement` selection will require
  a separate score aggregation before presenting one garment recommendation.
- **AI suggestions:** not used in V1. ImageGen prompts stay unchanged so a
  native-master-only image result does not risk being replaced by JSON; a
  future AI style hint can be advisory and never bypass actual contrast QA.
- **Release:** run frontend TypeScript, Design gallery tests, backend
  ColorMatch and Drive tests and full CI before production. Monitor Picked
  p95 latency, modal open time, Drive upload failures, memory and CPU; rollback
  by deploying previous current/rollback, with no schema migration required.

No permissions, print_ready, rights_status, agent/browser execution flags,
claim queue, or production Harness migrations 0209/0210 are changed.
