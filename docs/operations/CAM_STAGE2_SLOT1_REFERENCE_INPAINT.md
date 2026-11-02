# GatorHats Products Stage 2 Slot 1 — Reference-First Thread Inpaint

Status: Built-in workflow; no custom Skill ZIP for new Slot 1 jobs.
Last reviewed: 2026-11-02.

## Source of truth and outputs

Input A: immutable Stage 1 source artwork (original lettering, line breaks and separate pigment colors).
Input B: verified original Valucap 8869 front stock photograph of the selected hat color.
Input C: pinned front embroidery placement.

- Pass A: one native ImageGen image EDIT of source artwork into a photographic, low-relief macro of individual satin/tatami fibers on neutral twill; verify byte-exact native output and fail when the design is simply returned unchanged.
- Pass B: one separate native ImageGen image EDIT of the unprinted factory crown crop. Transfer the thread material from Pass A; use the separate deterministic placement/pigment guide and protected-area mask strictly as references. Do not transfer the reference photo's twill or lighting onto the actual hat.
- Final: geometry/color/fiber QA followed by a mask-bounded composite over the untouched original full-size stock photo. Outside the glyphs and narrow stitch-contact zone, preserve original pixels bit-for-bit.
- Persist only the finished Ecommerce output and its immutable version. Do not persist a redundant flat-painted full-hat preview for new Slot 1 jobs. Keep source/stock hashes and the two native pass SHA-256 values in version metadata. Intermediate native files are isolated and deleted after execution.

## Boundaries

- New jobs and explicit Retry/Regenerate select `cam-slot1-thread-reference-inpaint-v2` internally and require no uploaded Skill; the UI must not block on a Skill assignment.
- Previously queued jobs pinned to older Skill/contract versions are **not** rewritten or deleted during this change. Old outputs and other slots (Ecommerce 2–4, Multi-hat, UGC, Colors) remain untouched.
- Abort without publishing on missing native ImageGen, invalid PNG/native provenance, unchanged macro, source glyph/color drift, missing directionally coherent strands, excessive puff/emboss/halo or changed protected stock.
- The provider's edit mask is a visual alignment reference, not a guaranteed native inpaint API mask; deterministic final clipping is the pixel-preservation safety boundary.
- Native image edits are two calls; do not add custom ZIP skills, extra generation passes, Python-made fake fibers, an API-key-backed fallback, or duplicate reference images.

## Verification and production

Targeted backend coverage: `test_threadtransfer_lite.py`, `test_slot1_skill_repin.py`, `test_slot1_embroidery.py`, `test_ecommerce_worker.py`.
Targeted frontend coverage: `ProductsPhotoWorkflow.run-readiness.test.tsx`, `ProductsPhotoWorkflow.test.tsx`, `ProductsPhotoWorkflow.bundled.test.tsx`.

No database migration, new dependency, secret change or bulk replay. Standard frontend/backend release scripts only, with live version and health verification. A unit-test pass does not imply that a real provider edit visually matches the photographic reference: inspect real outputs separately.
Rollback both affected components to the previously verified immutable releases; preserve images, versions and processing history.
