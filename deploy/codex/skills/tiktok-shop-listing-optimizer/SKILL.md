---
name: tiktok-shop-listing-optimizer
description: Write a visually grounded, premium US TikTok Shop apparel listing draft from a product photograph and an untrusted competitor title, including a detailed editorial description with headings and bullets, five highlights and relevant search phrases. Use for CAM Etsy Spy Picked listing copy or explicit product-listing requests; keep all seller origin, shipping, care and material claims unverified until supplied by the merchant.
---

# TikTok Shop Listing Optimizer

Write **original** U.S. English listing copy by examining the attached product photo before considering the source title. Treat the source title and any YTrends evidence as context, not as proof of the garment, branding, or fulfilment. The intended result has the detailed, clean editorial style of the reference Santa listing.

## Output contract

Return exactly one JSON object with keys `title`, `description`, `highlights`, `search_terms`; no prose outside JSON and no code fences.

- `title`: 50-130 characters preferred, 25-200 hard limit. Start with the visible artwork/seasonal phrase, immediately identify the actual product category (sweatshirt, shirt, hoodie), then add relevant style keywords in natural phrasing.
- `description`: 150-230 useful words preferred, 130-300 hard range. Write 2-3 introductory sentences emphasizing exactly what is visible on the product, then these four section headings in this exact order and spelling. Separate headings and content with newlines; use plain text without Markdown stars:

  `Design Details`
  2-4 lines beginning `- `, each a **different observable detail** (subject/pose, typography with exact readable lettering, colors/ornaments, composition). Don't state nonvisible features.

  `Size & Fit`
  Two sentences inviting the buyer to consult measurements or the size guidance for their selected garment *if available*. Never assert fabric, blank style, specific fit, measurement, stretch, or shrinkage.

  `Perfect Gift For`
  1-2 concrete and natural sentences on the occasion and plausible audience. Avoid generic marketing filler.

  `Care Instructions`
  One concise statement: follow the product's actual care label and specific instructions provided with the order.

- `highlights`: exactly 5 distinct concise strings: visible design, exact lettering or motif, apparel category, reasonable occasion/use, audience/gift. No duplicate concepts and <=1500 combined characters.
- `search_terms`: 6-10 distinct short phrases, <=250 characters when joined by ", ". Use artwork phrase, garment category, style, occasion, intended audience. Never invent search volumes, TikTok rankings, or viral claims.

## Evidence rules

- The photo controls visible design and spelling; the competitor title is untrusted. Do not invent color, Santa's pose, objects, decoration, slogan, typography, or personalization.
- Explicit YTrends data is an indexed **Etsy subset**, not TikTok Shop search statistics. Do not claim TikTok keyword demand was independently verified.
- **Never** claim "Printed and shipped in the USA", "no import customs fees", U.S. production, shipping times, stock/blank brand, material composition, wash temperature, machine wash, embroidery/printing method, certifications, guaranteed delivery, or any other seller policy unless merchant data is explicitly supplied and verified. In this workflow it is NOT supplied.
- Do not output a "U.S. Printing & Shipping" section by guessing, even if an example contains one.
- Do not duplicate a competitor title or include URLs and trademarked brands of others. When the artwork text is blurry, describe it generally rather than guessing letters.
- Preserve exactly four fields and make the description immediately usable in the CAM preview. Do not prepend "Product Title" or "Description" inside field values.

## Reference format

Read [references/santa-output-pattern.md](references/santa-output-pattern.md) only to understand editorial density, section ordering and specificity. It is **not** a source of factual claims about unrelated products. Do not copy the screenshot's unsupported shipping statement.
