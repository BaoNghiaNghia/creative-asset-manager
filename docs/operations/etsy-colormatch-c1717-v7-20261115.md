# CAM Etsy Spy Picked — ColorMatch V7 rollout and safeguards

## Scope / design
- P0: Detect significant dark/light and probable glyph-shaped ink with alpha >=
  32/255, not the V6 fixed 155 cutoff. Compare semitransparent ink composited
  over each real C1717 SMS swatch. Hard reject poor readability before ranking.
- P0: Persist bounded, normalized (0..1000) probable-text and critical-ink
  regions, and faded-ink flag per immutable artwork SHA. Spatial masks are
  morphology proxies; **not OCR**, no guarantee of correct text reading.
- P1: On ingest, build two IN-MEMORY separate previews from verified original:
  normal 192px published thumbnail, 512px internal analysis. Full master PNG
  bytes remain unchanged and private. Crop probable small-text ROIs on medium
  pixels (bounded six); preserve only region descriptors, not crop files.
- P1: Legacy Picked Drive originals are fetched *only* if source size <=8MiB
  and authenticated read matches master SHA; larger/missing/failed sources
  fall back to the existing SHA-verified 192px thumbnail. Backfill flags
  medium vs thumbnail fallbacks; no new Drive files, no master upload.
- P1: Only known distinct positions share garment recommendations: intersect
  eligible (hard-safe, minimum-quality) C1717 colors across one active variant
  per real print position. Unknown placements do not count. No force-fitting
  colors where the intersection is empty.
- P1: Users may choose unknown/DTG/DTF/screen print method for advisory
  warnings; no unsupported claim of real print safety or stock.
- P2: Explicit user-selected/rejected color events stored in additive,
  tenant-scoped Alembic table with SHA and small bounded payload. No passive
  browsing telemetry, no photos, no personally identifying data. Explicit
  rejected colors disappear from the visible recommendations for that design.
  Aggregate historical feedback does not auto-train scoring thresholds without
  human holdout review; preserve signals for later calibration.

## Compatibility / no-regeneration
Algorithm `colormatch-sms-c1717-v7`. V1-V6 remain SHA-validated and display
until V7 backfill. New design analysis stays off hot Picked list requests.
Backfill changes ONLY job JSON `color_match`, checks tenant, Picked, completed,
SHA, provider identity and locks. No new job queued and no print PNG altered.
Original artwork remains copyright/rights-unverified and print-review-required.

## Verification
- Synthetic faded black type alpha 85/115/145/154/155/200/255: dark navy
  rejected; white retained; no fixed-opacity cliff.
- Synthetic dense text, multi-position garment intersections, invalid SHA,
  distinct alternatives, malformed-color-feedback/tenant/asset authorization.
- Frontend TypeScript, targeted Vitest and all client test suites.
- Alembic single head before deployment; ensure fresh 0209 DDL is run in
  default `cam-rebuild-backend.sh` migration path (DO NOT use --no-migrate).

## Production release (same commit for backend + frontend)
```bash
bash scripts/cam-rebuild-backend.sh --commit SOURCE_COMMIT --keep-releases 2
CAM_FRONTEND_RELEASE_KEEP=2 bash scripts/deploy-cam-frontend.sh --commit SOURCE_COMMIT
```
A failed migration or failed health-check must block release. Retain previous
backend/frontend revision and use official rollback if necessary.

## Backfill all eligible Picked designs (tenant-isolated)
```bash
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100 --delay-seconds 0.5 --apply
bash scripts/cam-etsy-colormatch-v2-backfill.sh --tenant-id creative-assets-prod --page-size 100
```
If host RAM is marginal, use --no-highres for a safe thumbnail-only fallback,
and clearly report how many analyses did not use 512px. Avoid parallel backfill.
Expect 0 remaining targets, 0 skipped, 0 errors and all target SHAs valid on
final read-only pass. Design count may rise during operation as new Picked
designs finish; re-run the idempotent action if required.

## Drift and print caveats
SMS C1717's 72 supplier colors, 56 setup and 54 known-HEX setup colors
derive from the authenticated 2026-11-14 snapshot. Screen HEX is approximate.
SMS sales-speed prior is a fixed 60-day snapshot; not real-time seller-wide
best sellers. Real DTG/DTF/screen print proofs still required.
