# CAM GatorHats Products Stage 2: native fine-thread macro reuse

Date: 2026-11-02.
Scope: **Products Stage 2 Slots 1–7**. Reviews Stage 2 is a different workflow and is not migrated.

## Source of truth

Slot 1 runs two native ImageGen calls, generating a photographic satin/tatami thread macro (A) from the selected Stage 1 design and then inpainting it onto the unprinted official factory front stock (B). The verified untouched PNG bytes of Pass A are now saved as a tenant-scoped managed asset and pinned to Slot 1's completed job, separately from the finished Ecommerce photo. New jobs reject missing/unchanged/corrupt native material. This is *not* a PIL-generated stitch image.

The slot 1 model's historically unused `front_preview_*` columns hold this native material only when `skill_name=cam-slot1-thread-reference-inpaint-v2`; historical pre-V2 rows are never treated as material. The source-revision and design checksum are checked before reuse.

## Downstream contracts

| Slot | Rendering | Native ImageGen calls |
|---|---|---:|
| 1 Front | Native macro, then original-stock inpaint and thread-level QA | 2 |
| 2 Real factory 3/4 | Edit native 3/4 stock using pinned source design and native macro; protected stock-region composite | 1 |
| 3 Real factory side 90° | Edit only the visible front edge; preserve GatorHats lateral factory branding and rest of source | 1 |
| 4 Back | Reuse exact verified unembroidered factory rear photograph; never invent rear embroidery | 0 |
| 5 Multi-hat | Existing assigned Skill composes three separate scenes; one native inpaint pass per finished scene adds native satin material | 2 per scene |
| 6 UGC | Existing assigned Skill composes three distinct authorized lifestyle scenes; one native inpaint pass per finished scene | 2 per scene |
| 7 12 Natural colors | Use the same Slot 1 macro with each original verified stock color; mask-guided native image edit plus fine-thread pixel-level QA | 1 per color |

Only Slots 5–6 keep configurable Skills for *scene composition*, not for embroidery inpaint. Slots 1–4 and 7 run internal contracts; no ZIP requirement. ImageGen is only invoked when the relevant source/color cap exists. Do not run an unnecessary rear-cap generation.

All new jobs must pin tenant, design revision, original cap color/stock hash, and the source V2 front/macro asset. Existing jobs remain on historical contracts and output versions are retained. If a design already has a completed old front photo but lacks its V2 macro, explicit Slot 1 Regenerate is required before the new downstream inpaint; no automatic expensive replay.

## Preserving photos

- Slots 2–3 use original official photos as the edit target, not a recreated cap. Reject no-op/non-native output, and composite model edits only within a tightly bounded visible crown ROI. Original pixels outside that window remain unchanged.
- Slot 4 returns original rear stock bytes.
- Scenes 5–6 retain existing approved scout composition/people/stock as the edit target for Pass B. Reject native edits that regenerate too much of the scene (>22% of pixels differ substantially) or do nothing; no altered cap count, face, background or colors in prompts.
- Slot 7 uses the Slot 1 placement/pigment guide and fine-thread QA to clip edited strands to allowed stitch and needle-contact pixels. No retinting thread hues to match a different cap color. Native macro reference remains exact PNG, never JPEG reencoded.
- Missing material, invalid stock or worker errors fail closed and publish no fake output.

## Verification and release

Backend: Ecommerce Queue/Run All, version pinning, native material persistence, 3/4 and side clipped overlays, original rear stock, scene inpaint QA, twelve-color worker and legacy Reviews tests. Frontend: 7-slot controls, 5 internal/no ZIP and 2 scene Skills, no redundant Slot 7 Skill selection. Standard production backend/frontend release only; no DB migration or bulk retry. Native image generation's *visual* quality is not established by mock-based tests; inspect real output examples before claiming parity to reference photographs. Keep normal previous immutable releases and existing completed outputs.
