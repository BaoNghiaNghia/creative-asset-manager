---
name: worker-hat-v1
description: Reference-conditioned photorealistic product-on-person editing for RRUGC generation. Preserve the person and scene while applying the supplied product references faithfully.
---

# Reference-conditioned product edit

Create one photorealistic edit from the role-labeled files in the current workspace.

## Input semantics

- `person.*` is the edit target. Preserve the person's identity, face, body, pose, expression, camera perspective, framing, lighting, background, and scene unless the job instruction explicitly requires a compatible adjustment.
- Files named from `product-*` or another product role are product references. Use them for exact product shape, construction, proportions, material, color, trim, logo, embroidery, artwork, text, and placement.
- Multiple product views describe the same physical product. Reconcile them consistently rather than inventing a different design.

## Editing rules

- Change only what is required to place the referenced product naturally on the person.
- Preserve product identity and visible design details as closely as the references allow.
- Do not redesign, simplify, restyle, recolor, mirror, substitute, or hallucinate logos, embroidery, artwork, or text.
- Match scale, perspective, occlusion, shadows, fabric/material behavior, and contact with the body realistically.
- Preserve photographic realism. Avoid illustration, CGI, beauty retouching, artificial skin, or editorial stylization unless explicitly requested.
- Do not add unrelated objects, text, branding, people, or scene changes.
- If a product detail is not visible in the references, do not invent a precise detail that contradicts them.

## Output contract

Use the available image-generation capability to produce exactly one final raster image.

- Save the only final deliverable to `output/final.png`.
- The file must be a valid PNG.
- Do not use any API-key-backed fallback.
- Verify `output/final.png` exists and decodes successfully before finishing.
