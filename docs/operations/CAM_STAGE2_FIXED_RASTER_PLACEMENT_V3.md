# CAM Products Stage 2 — Fixed Raster Placement V3

**Scope:** Built-in Ecommerce Slot 1 (front), Slot 2 (official 3/4),
Slot 3 (official right-side 90°). Historical outputs and queued jobs keep
their original version. No database migration or bulk regeneration.

## One authoritative front embroidery area

Slot 1 uses the *already saved* FRONT embroidery rectangle in CAM
(`front_placement`). It retains each design's saved `design_size` and fits
the visible photographic yarn with `contain` at up to **90% of the guide width,
76% of its height** for 100% zoom, preserving aspect ratio and safe margins.
The old independent 38% whole-image width cap is eliminated for new jobs.
The new contract version is `front-guide-v2` (stored inside the existing
`embroidery_scale_front_v1` snapshot key for wire backward compatibility).
Old in-flight `front-locked-v1` contract data uses its prior sizing rules.

After local compositing Slot 1, immutable Output History stores its ACTUAL
normalized yarn footprint in `embroidery_placement_v1.front_box` with source
revision and the saved Embroidery Reference remote ID. Retry Placement reuses
that photograph without calling native ImageGen.

## Two image inputs, zero new image generations for Slots 2/3

A newly queued / explicitly Retr(ied)/Regenerat(ed) Slot 2 or 3 pins
`thread_material_pipeline=v4-raster-placement` and the actual completed
Slot 1 footprint. That job uses only:

- The unmodified manufacturer photo for its selected color and angle.
- The exact saved photographic `Embroidery Reference` from Slot 1.

It does NOT fetch the flat Stage 1 design artwork, the front photo, a
third image, a Skill archive or a generated angle photograph. Its
`provider_key/provider_scope` are null: no Codex lease, CLI, Image API,
ImageGen call or second cleanup inpaint.

The established `_layer_from_reference` operation separates satin yarn
from backing fabric. The verified premultiplied yarn pixels are projected
onto one hard-coded stock-specific quadrilateral with
`cv2.getPerspectiveTransform` and `cv2.warpPerspective`:

- **3/4:** full yarn, an apparent width of ~82% of the actual front footprint
  (capped by the verified factory front crown).
- **True side 90°:** only the rightmost ~18% of the embroidery source, in a
  narrow front-edge sliver of ~13% of frontal width. An entire readable slogan
  cannot physically be visible on a true side panel. The original GatorHats
  side wordmark stays unchanged.

Pixel composition is strictly confined to the angled target. The official
stock, fabric folds, visor, seams, logo and photo outside the stitch-shaped
alpha remain bit-identical. QA rejects missing yarn, severe undersizing,
mismatched saved references or reference/stock dimensions. It never paints
a rectangular backing patch.

## History and rollout

- Legacy `v2` local-angle and `v3-direct-edit` Codex jobs keep their
  pinned behavior, including existing in-flight work.
- **New or explicitly retried** Slot 2 and Slot 3 use deterministic v4.
- Existing Slot 1 references are reused; optionally run **Retry Placement**
  first to update an older small/offset front footprint.
- Rerun one selected keyword/color in Slot 1 → 2 → 3 to inspect stitch
  fidelity; do not regenerate all keywords or discard old output versions.
- Run SQL-backed queue / worker tests (prove no Codex lease and no flat PNG),
  pixel-protection QA, UI tests and production health checks before rollout.
