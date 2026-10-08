---
name: gatorhats-8869-scale-image
version: 1.0.0
description: Strictly copy one approved embroidery artwork onto one official Valucap 8869 stock cap colorway. Use for a single-color output in the Stage 2 13-color pipeline; do not create a ten-image ecommerce set.
---

# GatorHats 8869 Scale Image — single-color embroidery reproduction

This Skill is the specialized **COLOR_SCALE_HANDOFF** from GatorHats 8869 Image Studio. Run once per colorway. The caller owns batch state, all 13 colors, retries, source revisions and naming; this Skill creates **exactly one finished product image** per invocation.

## Input contract

- **Edit target** (`person` file from the runner) is the actual, authorized front-facing **stock photo** of one specific Valucap 8869 colorway. Preserve its cap shape, camera framing, natural fabric texture, crown/bill/button/eyelet colors, proportions, seams, bill curve, shadow and perspective.
- **Role-labeled `design_reference`** is the one embroidery image to transfer, **not** a scene reference and **not** a replacement hat photo.
- The user's prompt states the official target colorway. Do not infer or substitute another colorway, even when the artwork has conflicting colored background or another hat in frame.
- The exact embroidery image is the **identity authority**: lettering, spacing, design geometry, outline, stitch direction and relative thread colors.

## Execution

1. Inspect the edit-target stock photo and current embroidery reference independently.
2. Identify only the embroidery art. Discard the reference's background, mockup surface and product color; do not copy another cap from the reference.
3. Apply the same embroidery **only at front center**, at realistic scale, aligned to the front crown and seam geometry.
4. Keep text character-for-character. No replacement letters, made-up words, extra text, icons or side personalization.
5. Preserve the original embroidery palette as faithfully as the stock crown contrast permits. Do not automatically recolor the design to match the bill or each cap color. Preserve meaning over decoration.
6. Render one crisp, photographically convincing product-front image; retain fabric and embroidery stitch realism, visible cap structure and natural lighting. No collage, model, props, additional hats, background scene change or art style change.
7. Before finalizing, visually check artwork identity, correct letters, embroidered texture, placement, 8869 crown/bill geometry and the exact named cap color.
8. Save **one** final PNG to `output/final.png`. Do not generate a set or overwrite a different job's output.

If you cannot preserve a legible design or the requested stock color, favor a faithful result and do not hallucinate missing letters. The caller validates dimensions, stores output and handles retries.

Use the provided `$imagegen` capability for image editing. Never substitute a key-backed image API fallback. Do not run additional Python/PIL validation commands; the caller validates output.
