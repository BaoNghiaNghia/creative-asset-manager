---
name: gatorhats-8869-slot1-threadtransfer-lite-v1
version: 1.1.0
description: Two authentic native image edits in one Codex run; photographic low-profile satin fibers on the exact factory cap crown, without regenerated stock.
---

# Slot 1 — ThreadTransfer Lite / Photographic Fiber V1.1

Use exactly **TWO sequential native $imagegen IMAGE-EDIT calls** in this one Codex execution. The output is a real image edit of the supplied stock, not a newly generated hat. Never use text-to-image, API-key image calls, Python-painted yarn, reusable decals or an additional candidate.

**A — Stitched material master.** View `design_reference`. IMAGE-EDIT it into a true macro photograph of the same letters physically machine-embroidered on neutral twill. Preserve the original glyph silhouettes, text, spacing, line breaks, accent strokes and every independent color. Show **individual fine satin filaments crossing narrow strokes, directional fine tatami fill in broad strokes, understated natural thread sheen and tiny stitch-entry contact with visible weave**. Stitch loft must look low (well below puffy 3D foam), not like rope, molded rubber, a raised plastic plaque, a woven patch or a glossy embossed edge. Photograph fabric and threads, not a 3D typography render. Copy the **UNCHANGED native PNG** to `working/thread_macro.png`.

**B — Source-photo embroidery edit.** Inspect `person`, `working/thread_macro.png`, `unaltered_stock_reference`, `design_reference`, and `slot1_edit_mask` BEFORE the second $imagegen call. `person` is a **square-letterboxed close crop of the ACTUAL original cap front** with pigment-only placed lettering. This is the edit TARGET, not a new photo to recreate; `unaltered_stock_reference` shows the full factory cap for curve, light and camera consistency. EDIT `person` in place. Transfer **only the fine yarn/fiber material** from A to its existing colored strokes. Do NOT transplant A's background/twill, redraw typography, alter colors, or crop/scale/warp the square target.

Make the embroidery visually follow the authentic front crown curvature via gradual stitch-direction-dependent highlights and tiny foreshortening at the sides. Keep the glyph identity and line baselines from the original target. Stitch height is subtle; fine soft shadow is limited to discrete stitch feet, with a *broken 1–3 px hairline* of fabric compression, not a continuous dark contour. The original twill must stay optically visible immediately beside the fibers. **Avoid thick coarse diagonal ribs, swollen lettering, inflated satin tubes, deep bevels, harsh black outlines, printed fill or floating decals.** The goal is a genuine product photograph in natural soft light.

`slot1_edit_mask`: white = thread + narrow contact region, black = immutable photo pixels. Leave all black pixels, nearby fabric, seams, visor, side branding and the original white square padding exactly alone. The backend strictly restores outside the white mask and re-inserts the edited crop into the full cap photograph; do not attempt that reconstruction yourself.

Copy the **UNCHANGED second native PNG** to `output/final.png`. Exactly TWO genuine native images must exist, one per pass. If source-image editing is unavailable or you cannot preserve the actual stock/crop, fail clearly. No fallback, extra attempts, image creation by code, additional outputs, or invented hats.
