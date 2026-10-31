---
name: gatorhats-8869-slot1-threadtransfer-lite-v1
version: 1.0.0
description: Two native ImageGen edits in one Codex execution for GatorHats Stage 2 Slot 1.
---

# Slot 1 — ThreadTransfer Lite

Use exactly **two sequential native $imagegen image-edit calls in THIS Codex session**. No other image generation, scripts, browsing, or model calls.

**A — Macro.** View `design_reference` first. EDIT that exact artwork into one macro photograph of real low-profile machine embroidery on neutral woven twill: fine individual satin stitches on lettering, compact fill for wide strokes, realistic filament detail and slight stitch-foot compression. Preserve every glyph shape, line break, spacing, and pigment; no new decoration, puff, plastic, printed fill or rectangular patch. Copy the **unchanged native ImageGen PNG** from the isolated `$CODEX_HOME/generated_images` into `working/thread_macro.png`.

**B — Original cap.** View `person` (the guided original cap target) AND `working/thread_macro.png` before calling $imagegen again. EDIT **person**, not a newly invented hat. Use `design_reference` only for exact typography/pigments, the macro only for thread texture, `unaltered_stock_reference` for actual twill/light/geometry, and `slot1_edit_mask` to understand permitted pixels. Follow original cap crown curvature through tiny thread highlights and hairline stitch-contact shadows, never warp letters or stitch above the cap. Copy the **unchanged second native ImageGen PNG** to `output/final.png`.

If the image-edit tool cannot use the source images, fail. **No fallback** to text-to-image, paid API, Pillow rendering, generated font, screenshot, watermark, patch, multiple candidates or retries inside this Skill. Only the backend performs final protected-mask composite and fidelity QA. Do not modify the reference source files.
