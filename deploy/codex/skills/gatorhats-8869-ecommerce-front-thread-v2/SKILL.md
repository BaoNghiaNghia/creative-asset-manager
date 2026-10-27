---
name: gatorhats-8869-ecommerce-front-thread-v2
description: Edit a GatorHats 8869 Stage 2 Products Slot 1 front cap photograph, faithfully applying the exact selected Stage 1 embroidery artwork as low-profile photographic machine stitching; no new lettering or regenerated cap.
---

# CAM Products Stage 2 / Slot 1 — Thread Fidelity v2

This is an **image edit**, never a new slogan or new product photo.

Use the job's role-labelled images:
- **Edit target (person)**: official stock cap with a deterministic, source-faithful low-profile stitch underlay. This is the only canvas.
- **design_reference**: canonical Stage 1 embroidery artwork. Exact visible glyph outlines, line breaks, punctuation, colors, tracking and arrangement must remain unchanged.
- **unaltered_stock_reference**: immutable hat fabric, seams, bill, lighting, side wordmark and camera framing.
- **slot1_edit_mask**: white thread/contact pixels alone may be refined; black is protected. Never interpret the mask as artwork.

Invoke the available $imagegen tool **in edit mode on the supplied edit target**, using the other images for reference. Generate one finished PNG at `output/final.png`. Do not generate a text-to-image replacement, generate variants, or produce typography from a transcribed prompt. The runtime owns the hard edit mask and restores all stock pixels outside it.

## Pass 2: Photographic thread material only

Pass 1 has already applied the exact design deterministically. Add the physical appearance of **fine, individual machine-embroidered yarns** within those existing strokes: directional satin across thin strokes; tiny angled fill stitches on broad strokes; subtle alternation of filament light, low thread elevation, small needle entry points and soft contact with real cap weave. Keep stitch ends slightly organic and edge detail fine and naturally irregular without growing the glyph shape.

Do not alter the typography, word choice, wording, letter proportions, number of lines, relative positions, scale, thread pigment or embroidery placement. The design is a source file, **not an invitation to redesign**. "WE THE PEOPLE" is an example of *stitch quality*, not target copy.

Never make the raised surface resemble foam, embossing, silicone, rubber, molded plastic, appliqué, thick diagonal ribs, print, digital bevel, transfer decal or a sewn-on cloth patch. Do not repaint a single cap pixel outside the white mask. Never borrow background, colors or wording from quality references.

Before writing final.png, verify the real cap/photo is not regenerated and no letters are changed. If $imagegen cannot edit the supplied target, fail clearly; never silently substitute an invented render. Do not use any API-key-backed fallback. Save exactly one PNG. The caller performs pixel and fidelity QA.
