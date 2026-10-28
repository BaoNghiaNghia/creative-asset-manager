---
name: gatorhats-8869-ecommerce-front-thread-v4
version: 4.0.0
description: Edit a GatorHats 8869 Stage 2 Products Slot 1 front cap photograph, faithfully applying the exact selected Stage 1 embroidery artwork as low-profile photographic machine stitching; no new lettering or regenerated cap.
---

# CAM Products Stage 2 / Slot 1 — Prompt-first Crown Contact v4

This is an **image edit**, never a new slogan or new product photo.

Use the job's role-labelled images:
- **Edit target (person)**: square-letterboxed stock with a pigment-only positioning guide. This is the only edit canvas; it contains no fake yarn texture.
- **design_reference**: canonical Stage 1 embroidery artwork. Exact visible glyph outlines, line breaks, punctuation, colors, tracking and arrangement must remain unchanged.
- **unaltered_stock_reference**: immutable hat fabric, seams, bill, lighting, side wordmark and camera framing.
- **slot1_edit_mask**: white is the calibrated thread and hairline fabric-contact band; black is strictly protected. Never interpret the mask as artwork.

Invoke the available $imagegen tool **in edit mode on the supplied edit target**, using the other images for reference. Generate one finished PNG at `output/final.png`. Do not generate a text-to-image replacement, generate variants, or produce typography from a transcribed prompt. The runtime keeps the true glyph shape unchanged and permits only tightly bounded lighting/contact changes in a 2-pixel band immediately outside the thread; all other stock pixels are restored exactly. The mask is a reference image, not a native imagegen API parameter.

## Generate photographic thread material

The target shows pigment and placement only; create the true physical appearance of **fine, individual machine-embroidered yarns** within those existing strokes: directional satin across thin strokes; tiny angled fill stitches on broad strokes; subtle alternation of filament light, low thread elevation, small needle entry points and soft contact with real cap weave. Keep stitch ends slightly organic and edge detail fine and naturally irregular without growing the glyph shape.

## Surface-conforming embroidery: prompt-first realism

**Priority after identity preservation:** Make the machine stitching read as thread sewn *into the actual curved front crown*, not a flat 2D graphic, sticker, printed ink or material laid on top. The stock hat's center is closer to the camera and its sides curve gently away. The micro-direction, reflected highlights and tiny shadow sides of the threads must respond consistently to this existing crown curvature and the original photo's lighting. At the left and right sides, use restrained surface-aware thread foreshortening and light falloff; keep the center nearly frontal. This is a **subtle optical/material effect inside the unchanged lettering**, NOT a curved text baseline, arched words, transformed logo or geometric warp. Preserve exact glyph outlines, stroke widths, line breaks, spelling, color boundaries and position.

**Thread-to-fabric contact:** Stitches should look slightly tensioned into the twill at their entry points. Within the *permitted thread/contact pixels only*, render minute dark contact occlusion **at the foot of each stitch** with adjacent fine thread highlights and small needle entry irregularities. The immediate inner edge of a stitch may be gently shaded to imply mild fabric compression, but the shading must be hairline-soft and nearly imperceptible at normal product-photo size. Vary this according to the existing stock light and the cap weave rather than drawing a uniform outline. Script satin and broad fill should retain distinct, convincing stitch directions.

**Narrow fabric-contact permission (v4 only):** The worker explicitly permits a tiny, optically convincing indentation immediately beside the actual sewn yarn (within roughly 2 source-photo pixels), while safeguarding the entire remainder of the hat. At each stitch foot, show extremely subtle shadowing from thread tension into the original twill: varied, broken, soft and consistent with the original light and crown curvature. Preserve the natural weave texture. **No uniform outline, external bevel, broad darkness, embroidery border, double lettering, new seam or visibly depressed channel.** The worker bounds this shading and rejects broad halos.

**Accept only** believable, low-profile thread physically coupled to the crown: soft material relief, fine natural filament variation, subtle curvature-dependent highlights and precise contact at stitch feet. **Reject** a flat printed fill, a floating appliqué, thick uniform ribs, shiny rubber/plastic, 3D puff, embossed/grooved outlines, lifted lettering, strong black contours, or any alteration to the original cap, design geometry or area outside the mask. If realistic integration cannot be achieved within these constraints, fail rather than invent a new cap or damage the source.

Do not alter the typography, word choice, wording, letter proportions, number of lines, relative positions, scale, thread pigment or embroidery placement. The design is a source file, **not an invitation to redesign**. "WE THE PEOPLE" is an example of *stitch quality*, not target copy.

Never make the raised surface resemble foam, embossing, silicone, rubber, molded plastic, appliqué, thick diagonal ribs, print, digital bevel, transfer decal or a sewn-on cloth patch. Do not repaint any cap pixel outside the white mask; do not change hat geometry, panel seams, brim or side branding. Never borrow background, colors or wording from quality references.

Before writing final.png, verify the real cap/photo is not regenerated and no letters are changed. If $imagegen cannot edit the supplied target, fail clearly; never silently substitute an invented render. Do not use any API-key-backed fallback. Save exactly one PNG. The caller performs pixel and fidelity QA.
