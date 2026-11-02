# CAM Stage 1 Cap-First Embroidery V5

## Diagnosis (2026-11-02)

The user's "THIS IS MY COWBOY HAT" baseline is compact two-tier heritage satin embroidery (warm brass + rust with a controlled teal flourish). The 2026-11-02 Stage 1 batch recycled an old Creative Brief from 2026-10-31 20:08. The old Explore direction literally specified a diagonal single-line banner, and previous ImageGen prompt used only generic "2048x1024 safe canvas" with no physical cap geometry. Native PNG technical QA verified alpha, not fit. There were 18 historical output versions across two 9-image attempts; do not treat repeated filenames as duplicates in one generation.

The connected Valucap 8869 front placement calibration for this product tenant yields an ~2:1 physical design-field aspect on the stock photo. Decorator reference mentions a nominal maximum of about 4.5 in x 2.25 in; actual production fit and digitizing remain dependent on the saved tenant calibration and stitch-out. The output canvas being 2:1 does not prove the artwork alpha footprint fits.

The user-supplied "Redesign Pet" ZIP bundles a *different* skill for custom pet sweatshirt embroidery. Its emotion-first and one-meaningful-twist principles generalize, but its outline-only large pet-name letters do NOT define the GatorHats Stage 1 stitch material, so do not install or reuse that skill for the cap.

## Implementation

- New only: `cap-fit-embroidered-v5` profile in `creative_v3_generation.py`.
- Preserve `embroidery-first-lite-v4`, earlier pinned V3/V2 and legacy six-slot prompt behavior exactly. Do not touch or regenerate existing Output History, saved briefs, or queued jobs.
- Separate material contract `true-satin-embroidered-lettering-v2` from semantic Brief, with descriptive Western look and tactile satin, no fake plastic/puff.
- Nine slot-specific art blueprints maintain 1 / 4 / 4, old keyword-specific directions and creative hooks while making cap-front 2:1 geometry higher priority than stored banner/ticket/diagonal layout instructions.
- An optional two-line wrap suggestion preserves exact literal phrase order; the downstream ImageGen must still preserve exact characters, not guaranteed by text QA.
- Native PNG checks retain existing transparency/uniqueness gates. Append non-blocking bbox/aspect warnings for new V5; NO automatic QA ImageGen retry from geometric warnings. Store per-slot flags with immutable output metadata, show "review required" in full-image viewer. Pixel QA cannot verify spelling or real-world thread density.

## Deployment/verification

Run source tests, frontend typecheck and production health gates; no schema migration or new dependencies. Do not automatically queue or regenerate keyword batches. Compare a future manually generated batch on a real front-cap preview before promoting design quality; auto screenshot/alpha check alone is not production proof.
