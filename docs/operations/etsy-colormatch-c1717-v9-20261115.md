# Etsy Spy ColorMatch V9 - quality-first garment previews

## Product contract
- Design modal displays two groups only: **Màu phù hợp nhất** (0-10 recommended garments) and **Các màu còn lại** (all other 72 SMS C1717 colors plus 4 legacy preview colors, without duplicates).
- A separate transparent checkerboard remains available.
- Every remaining swatch can be previewed manually except the 3 SMS colors with unknown HEX (disabled, with title text). Supplier-only colors stay visible but are not automatically recommended.
- Product-wide shared colors use one optional checkbox, not a third palette.
- No mandatory review overlay, orange warning-ring clutter, forced 10-count, per-design image duplication or extra Picked-list fetch.

## Algorithm and versioning
- **V9 `colormatch-sms-c1717-v9`**, `design_region_alpha_quality_first` and `region_ink_alpha_composite_v3`.
- Uses verified in-memory thumbnail/medium-resolution alpha PNG; never modifies native master.
- Balances samples across ink families so minority letters, dark/light details and low-alpha regions do not disappear behind a dominant illustration. Readability hard-gate on detected ink regions protects thin text.
- Scores lower-tail contrast and weakest meaningful ink family plus an artwork-wide contrast estimate. Demand popularity acts only as a near-tiebreaker. No forced diversity or unsafe filler.
- Ranks up to 10 candidates that pass both readability and relative/absolute contrast floor. Can return 0 when no garment is acceptable, in which case all are manual previews.
- Candidate list represents **visual recommendations**, not certified print suitability, inventory or a substitute for print proof. Historical V8 remains API readable (review-only colors excluded by frontend); V9 SHA/version refresh is required for the latest ranking.

## Release and data integrity
- Deploy backend/API first then frontend using the **same committed release SHA**.
- Preserve current and one rollback release. No migrations required.
- After backend V9 is live, run tenant-scoped idempotent ColorMatch backfill for `creative-assets-prod` only. Dry-run first, then `--apply` if healthy; final dry-run should report zero remaining writable targets or clearly report unavailable remote assets.
- Backfill updates solely the `color_match` JSON of completed picked designs under SHA and storage identity guards; never overwrites originals, Drive files or print artwork.
- Maintain production load guard with bounded page size, 0.5s+ delay, and stop on repeated source errors.
