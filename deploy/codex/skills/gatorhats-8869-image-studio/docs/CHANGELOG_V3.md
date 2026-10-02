# V3 Optimization Summary

## Critical fixes retained from V2

- 39 stock images are directly accessible under `assets/stock/`; no nested stock ZIP.
- Exactly 13 physically present colorways are official.
- `Natural/Realtree All Purpose` is included.
- Non-present White/* and Khaki/Forest Green combinations are not official.
- Front artwork no longer automatically matches bill color.
- No runtime-specific `referenced_image_paths` instruction.
- 7 approved style references; redundant dog-theme reference removed.
- `mauchi.txt` remains the single thread inventory.

## New V3 improvements

- Added explicit `COLOR_SCALE_HANDOFF` to prevent conflict with Scale Image 8869.
- Reworked source authority into PRODUCT/CONTENT vs STYLE layers.
- Added lightweight job-state model and new-artwork reset rule.
- Added task-specific reference loading matrix to reduce context and leakage.
- Added strict delta-edit lock for targeted revisions.
- Added `QA_POLICY.md` with BLOCKER vs ACCEPTABLE VARIATION.
- Added maximum 2 recovery attempts per image to prevent regenerate loops.
- Reframed ecommerce white as a visual generation target; pixel-perfect white is post-processing when required.
- Simplified prose that merely restated general image-generation knowledge while retaining business-critical constraints.
- Restricted plugin/product policy to ChatGPT because this package is designed for direct ChatGPT use.
