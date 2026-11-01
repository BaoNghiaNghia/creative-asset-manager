---
name: tiktokshop-apparel-ugc-v1
version: 1.0.0
description: Internal TikTokShop product-aware Image Edit for embroidered hoodies, sweatshirts, tees, shirts and sweaters. Uses the exact source artwork and one to three approved lifestyle references; NEVER use for GatorHats caps or fake buyer reviews.
---

# TikTokShop · Apparel UGC Image Edit (V1)

## Invocation and scope

This Skill is for CAM TikTokShop Scout & UGC Stage 4, and only for
source-plan records from the server-owned TikTokShop Drive root.
Use the existing Codex `image_studio` workflow. This is an internal
creative-image **draft**, not a photograph of a verified customer.

Inputs:
1. **Authoritative artwork/source image**, supplied as the primary image.
2. **1–3 selected approved reference photos**, supplied as secondary images.
3. Stage 3 product context/brief including garment type, artwork placement,
   materials, text and permitted colors when known.

Output: one coherent photorealistic apparel lifestyle draft per job, in the
same image delivery contract as CAM Stage 4. Do not write any third-party
design, testimonial, rating, reviewer name, purchase record, watermark or text
not explicitly present in the input artwork.

## Source authority and product preservation

- User-approved embroidery artwork/lettering is always the authority for
  **exact words, spacing, glyph shapes, alignment, arrangement and colors**.
- A source product photograph (when present) is authoritative for the
  garment's actual material, shape, base color, fit, seams, collars and cuffs.
- A design-only PNG is NOT proof of the original product's fabric/color/cut.
  If no product photo exists, create a **concept mockup** on a restrained
  garment of the declared category, and do not claim exact product fidelity.
- Lifestyle reference photographs are **composition and lighting cues only**.
  Do not borrow their apparel logos, prints, text, faces or branded details.
- Never interpret clothing as a cap, crown, brim, visor, 8869 stock or
  "trucker" product. Never add GatorHats branding or side embroidery.

## Compose one believable UGC frame

Prefer natural indoor/window or cloudy-day light, smartphone crop, realistic
portrait perspective, spontaneous posture, true cloth texture and depth.
Use garment-specific cropping (torso only is fine), natural folds at shoulders,
armpits, hem and elbow. Embroidery must adhere to cloth curvature, thread
relief and stitch direction, never look like flat print, foam or a floating
sticker. It must not cross seams or look pasted over folds.

Do not alter anatomy to fit a design. Hands and arms must be plausible and
interact consistently with garment drape and shadows. Preserve original
artwork content without hallucinating words. Where exact embroidered text
cannot remain legible at the target size, reduce creative complexity rather
than manufacturing characters or pretending the text is accurate.

Use one scene per generated image. Avoid collage, duplicated products,
impossible shirt sleeves, excessive airbrushing, synthetic skin, fabricated
brand labels, visually unrelated props and misleading promotional badges.
Never add a claim that a real customer bought, wore, endorsed or reviewed it.

## Output QA gate

Compare the rendered output against the supplied source artwork and garment
context before accepting it:

1. **Design identity:** exact text/shape/color preserved; no missing letters,
   extra icons, invented artwork or third-party designs from reference scenes.
2. **Garment fit:** correct garment class, collar/sleeves/cuffs, seam position,
   natural fold deformation and visible placement area.
3. **Physical stitching:** thread detail integrated in cloth, plausible
   scale, natural curvature, no floating patches, printing or rubber sheen.
4. **Photographic realism:** coherent body/hand geometry and lighting,
   background continuity, no AI skin artifacts or warped accessories.
5. **Draft integrity:** no fake reviewer name, star rating, purchase statement,
   endorsement, review card or implication of a verified customer photo.

If any gate fails, revise only the failing region if safe, or return failure
and preserve the source/previous output. Never upload an unverified draft to
a customer-facing review catalog; Stage 5 is internal QA unless genuine
customer rights and data are supplied independently.

## Runtime efficiency

Read only input artwork, metadata for the current product folder and the
selected references. Reuse source snapshots, approved style guidance and
existing worker/Codex account pool. Do not scout new Pinterest references,
browse other Drive roots or call extra image models in this Skill.
