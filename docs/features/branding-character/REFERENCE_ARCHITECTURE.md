# Reference architecture: creative-ai-hub

Reference: [tranquocvu7400-dotcom/creative-ai-hub](https://github.com/tranquocvu7400-dotcom/creative-ai-hub).

The public repository's October 2026 state consists of conceptual documentation for a Multi-Shop AI Visual Production Hub. It is **not** a prebuilt storefront crawler or an implementation to copy directly.

## Six profile layers

1. **Shop Profile:** visual identity, vibe, palette, audience clues; record evidence vs. inference.
2. **Category Profile:** distinct design and image patterns for hats, apparel, tote bags, crochet etc.
3. **Listing Visual Profile:** gallery order and roles such as Hero, Detail, Lifestyle, UGC, Variation and Info.
4. **Image Style Profile:** lighting, camera view, background, props, emotion and realism.
5. **Platform Profile:** separate observed presentation patterns from official Amazon/Etsy/Shopify requirements.
6. **Seasonal Campaign Profile (CAM extension):** holiday, event and campaign exceptions without rewriting the evergreen shop DNA.

Each statement requires evidence asset/listing references, frequency numerator/denominator, confidence and exception notes. Preserve data provenance: `observation` vs `inference`.

## Shared workflows, per-shop DNA

`Workflow + Approved Shop DNA/version + Category + Listing Role + Product Accuracy + Platform Rules + References -> Assets`

Do not train on anonymous title-only content and treat it as verified image evidence. Avoid leakage of credentials, cookies, private analytics or artwork between shops/tenants.

## Source boundaries

- Existing CAM `integrations/agent-reach` is keyword/source discovery, not a listing-complete Playwright scanner.
- Existing Scout workflow on Windows can inform browser management, but shop scanning requires its own supported connectors and access policies.
- No official platform authorization can be inferred from merely being able to browse a public listing.
