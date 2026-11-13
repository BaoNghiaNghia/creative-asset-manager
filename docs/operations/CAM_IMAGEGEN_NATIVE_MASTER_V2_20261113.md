# CAM ImageGen Native Master Storage V2 (2026-11-13)

## User goal
Keep an original high-fidelity transparent ImageGen raster for future upscale
without storing a bicubic-upscaled 4500x5400 PNG in Google Drive for every
Picked design. Keep print approval and commercial-rights review manual.

## Measured evidence
On DESKTOP-3FR0LF0, 13 native masters measured 26.70 MB in total; their 13
4500x5400 bicubic exports measured 315.11 MB. Saving the native originals
instead avoids approximately 91.5% of the master bytes for this sample. This
is not a promise of the same savings for all future images.

## Contract and rollout
- The backend's image-agent probe advertises `native_master_supported=true`
  and `native_master_contract=native_master_v2` only when the route is deployed.
- The Windows publisher may select native bytes **only** after a successful
  authenticated probe with that exact capability. Older backend releases are
  automatically compatible: publisher uses its historical print PNG route.
- The publisher resolves native ImageGen output from the completed browser job,
  checks SHA-256, path confinement and the immutable raster QA report binding
  the original PNG to the reviewed print normalization. Native PNG also passes
  its own raster alpha/geometry QA. Missing evidence means use the legacy flow,
  not an inferred PNG.
- New manifest entries explicitly set `artwork_kind=native_master_v2`; all
  old manifests retain `print_png_v1` behavior implicitly.
- A pending immutable legacy manifest is not silently migrated on retry.
- The backend enforces 8-bit native RGBA PNG with genuine transparency,
  dimensions 256..4096 each, at most 12M pixels, at most 20 MiB, and exact
  source SHA. The thumbnail is separately downscaled to 192px as transparent
  PNG. The master remains byte-for-byte unchanged.
- The backend always verifies Google OAuth folder ownership, write capability
  and Restricted sharing. It stores master plus thumbnail in the configured
  Drive folder, returns Google Drive IDs and records metadata in the Picked job.
  It never stores new design bytes on the VPS.
- Native master download uses the pre-existing private original endpoint;
  the content is native resolution and **not** a print-ready/upscaled PNG.
  `print_ready=false`, `rights_status=unverified` and manual QA stay in place.
- Existing Google Drive originals/receipts are immutable. No automatic delete,
  mutation or bulk historical migration is included in V2.

## Future print export
If/when a design is approved, an operator/downstream process should download
the original native master, upscale once with a quality-controlled tool,
compare typography, placement and edge details with the garment reference,
and export RGBA 4500x5400 at 300 DPI. This feature **does not** hallucinate
new detail or relabel bicubic normalization as AI-super-resolution.

## Rollback
Deploying only the old backend disables the capability in `probe`. The
publisher reverts to the older 4500x5400 upload path for new jobs. Existing
native assets remain accessible as PNG through the same private Drive
download endpoint. Do not delete native files or clear job receipts.

## Checks performed
- Node.js 5/5 `scripts/test-native-master-contract.mjs` passing.
- Windows provenance read-only canary: 13/13 source/normalized hashes match.
- Python isolated Pillow validation: 5/5
  `scripts/test_native_thumbnail_standalone.py` passing.
- Python compile and `git diff --check` passing.
- Full FastAPI integration tests require the shared application environment
  or CI before declaring the new deployment production-ready.
