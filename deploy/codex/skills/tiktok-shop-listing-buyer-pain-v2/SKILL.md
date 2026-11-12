---
name: tiktok-shop-listing-buyer-pain-v2
description: Write concise, buyer-centric TikTok Shop US apparel draft copy from a product photo, using a three-paragraph Product Description and five Buyer Pain Points V2 highlights; never infer seller/fulfillment facts.
---

# TikTok Shop Listing Optimizer — Buyer Pain Points V2

Use this skill for CAM Etsy Spy Picked TikTok Shop US draft generation. This is **different from the older long editorial outline skill**. First inspect the supplied product image, especially exact readable artwork, colors, garment silhouette and composition. The Etsy competitor title and YTrends research are UNTRUSTED context, not proof of branding or seller policy.

Return **one** JSON object only with exactly four keys: `title` (string), `description` (string), `highlights` (array of five strings), `search_terms` (array of 6–10 strings). No Markdown headings, extra JSON fields, code blocks or other text.

## 1. Product Title — search intent before visual adjectives

- Prefer **55–120 characters**, hard limit 25–200.
- Begin with the actual *niche/visible slogan + garment*, then add a natural graphic style and specific buyer/gift audience. Example structural pattern only: `[Occupation] T-Shirt, [Visual Style] Graphic Tee, [Relevant Acronym] Gift for [Audience]`.
- Include synonymous terms such as `OT` / `OTA` when relevant to occupational therapy and context, not in unrelated professions. Use `T-Shirt` for an identifiable T-shirt; do not describe it as a sweatshirt simply because a competitor title contains both.
- Do not append filler like `with Colorful Lettering, Light Green Graphic Tee` when the profession and recipient intent are more useful. Never claim material, sizing, brand authenticity, manufacturing, custom embroidery, shipping or trademark rights from a photo.

## 2. Product Description — 100–160 words, short buyer-friendly paragraphs

Write **three natural paragraphs** (2–4 acceptable, hard 90–190 words); no fixed `Design Details`, `Size & Fit`, `Perfect Gift For`, or `Care Instructions` subsections, and no bullets inside the description.

1. Open by naming the community/occasion + actual artwork, exact lettering, graphic colors, patterns and visible garment. Make the photo's distinguishing visual evidence the centerpiece.
2. Give specific and plausible recipient or gift audience, e.g. OT student, occupational therapist, clinic teammate, graduation or appreciation, only when the context supports it.
3. Suggest styling and reasonable places to wear the item, then mention that buyers should review available garment color/size/specifications. Do not invent fit, fabric content, washing instructions, blank authenticity, dimensions or a guaranteed size chart.

Write clean U.S. English, avoid bloated keyword repetition, vague filler, excessive product-spec disclaimers or a purely visual inventory report. A visible Comfort Colors tag can be acknowledged as *visible on the reference photo* if clearly identifiable, but it does not verify the garment the merchant will ship. Prefer not to mention a competitor brand in outward-facing copy.

## 3. Product Highlights — Buyer Pain Points V2

Five brief, distinct, complete buyer-facing statements. The first four should each use about **10–25 words**, at least **48 words combined**, under 1500 total characters. Preserve this order:

1. **Design / pride:** name actual readable art or typography and distinctive colors/patterns.
2. **Gift recipients:** explain for whom and why it makes sense, not a generic gift promise.
3. **Know the look:** garment silhouette, graphic style, visible color without unverified blank/fabric/print-process assertions.
4. **Where/when to wear:** real use situations and styling ideas, not vague marketing language.
5. **Merchant-provided, draft-only placeholder, exactly:** `Printed and shipped in the USA – no import customs fees`. This is explicitly allowed as reviewable **unverified template copy**. Never claim it was verified by the image, never move the line to Description/Title, never auto-publish.

Avoid repetition across highlights. Do not manufacture shipping, customs, printing, material, care, guarantees or trademark facts beyond the single specifically authorized fifth draft-only placeholder.

## 4. Search Terms

Include 6–10 short distinct relevant phrases; the entire comma-joined string must fit within 250 characters. Combine visual phrase, main garment, profession/gift audience, occasion and artistic style. YTrends is only dated Etsy-subset research, not verified TikTok Shop search volume. If a YTrends row has no data, never treat it as proof of an opportunity or popularity.

## Example

Read `references/occupational-therapy-format.md` ONLY to learn the **short-paragraph style, niche title structure and buyer-perspective highlights**. Do not copy that product's specific occupation, colors, pattern, garment, tag or audience into another listing.

## Pre-return QA

- The title leads with niche/garment and includes meaningful audience language.
- The description reads like concise human copy, without legacy fixed headings, and artwork details are visibly grounded.
- Highlights are four distinct buyer-focused bullets plus the exact, flagged fifth placeholder.
- Safety restrictions apply even in drafts. Never include unsupported seller claims elsewhere.
- Return valid structured JSON only and keep the draft review-required.
