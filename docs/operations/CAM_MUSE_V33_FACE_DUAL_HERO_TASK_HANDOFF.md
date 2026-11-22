# Existing Muse cam-auto-video task — V3.3 face-on, dual-hero upgrade

**This is a handoff to update the EXISTING external Muse scheduled task.**
Do NOT create a second task. Keep its **2-minute cadence**, the existing
V3.1 nonpilot work, and immutable previously verified video files.

**Target CAM Muse Bridge:** 1.3.5 source; verify LIVE production /health
and authenticated /video-work before trying V3.3. Do not assume it has
been deployed. V3.3 output remains exactly 12 seconds.

## Claim capability / pilot

Read `storyV33SpecAvailable`, `storyV33SourceContract`,
`storyV33OutputContract`, `storyV33RequiresTwoOriginalHeroImages`,
and each job's `storyEnabled`. Only pilot allowlisted jobs with
`storyEnabled=true`, no completed videos, and valid artwork are eligible.
Never expand beyond the two pilot listings until four video QA reports pass.

Claim a NEW eligible job using:
```json
{
  "worker": "muse",
  "storyVersion": "CAM_STORY_REAL_LIFE_V3_3",
  "storyOutputContract": "CAM_STORY_12S_TWO_TAKE_V1",
  "storySourceContract": "CAM_STORY_DUAL_HERO_FACE_V1"
}
```

## Generate TWO genuinely independent source JPEG/PNG images

For each listing generate EXACTLY TWO original *source hero* images:
one native portrait **9:16**, one native landscape **16:9**.

1. Read `videoSpec.storySpecV33.aspectPlans["9:16"]`. Use its unique
   `prompt`, `heroRequestId`, `heroSeed`, `storyId`, and camera plan
   for a **new image generation**. Do NOT start from any image/video from
   the other ratio. No cropping, outpainting, reformatting, stretching,
   upscaling, or extending portrait into landscape.
2. Read `videoSpec.storySpecV33.aspectPlans["16:9"]`. Issue a SECOND
   independent image generation request using its distinct ID and seed.
   Use its DIFFERENT composition, camera/action plan, and native wide aspect.
   The human actor or environment may differ. The approved C1717 garment,
   original artwork, print colors and physical location MUST match.
3. Both hero images must show a full natural adult face and entire front
   print, not a neck-down/torso-only shot. Show jeans or khakis for at
   least two seconds of final video. Pick a model with natural, proportionate,
   average or moderately athletic build; never retouch the body.
4. The first flat-ink pixel remains 150mm below the lower collar rib.
   Preserve exact approved Native Master SHA/text and approved shirt color.
   NEVER generate new text/artwork or turn a T-shirt into a sweatshirt.
5. If independent hero generation, face visibility or source consistency
   is not possible, report **BLOCKED** rather than pretending images differ.

Upload the ACTUAL source hero file bytes (not just hashes or links) to:
- `POST /api/v1/muse-bridge/jobs/{listingId}/hero/vertical`
- `POST /api/v1/muse-bridge/jobs/{listingId}/hero/horizontal`

Required image request:
- Body: valid original native-ratio JPEG or PNG bytes (max 12 MiB).
- `Content-Type: image/jpeg` or `image/png`.
- `X-Muse-Lease: <claim lease>`
- `X-CAM-Story-Source-Contract: CAM_STORY_DUAL_HERO_FACE_V1`
- `X-CAM-Hero-Request-Id: <aspectPlans[ratio].heroRequestId>`
- `X-CAM-Hero-Seed: <aspectPlans[ratio].heroSeed>`

Bridge checks actual bytes/dimensions, pixel hash and near-duplicate
fingerprint, saves each hero to private CAM Drive with provenance, and
rejects a same/near-identical source reused across ratios. Keep its returned
`imageSha256` receipt. **This is not automatic facial QA approval.**

## Render each ratio from its own hero

Use ONLY that ratio's uploaded hero source image to generate two real
moving takes. Native render source maximum is 10s per take; keep 6
seconds of actual human motion from take A and 6 from take B. One motivated
cut at precisely second 6; no loops, reverse, frame holds, slowing, photo
panning or frozen image extension. Same actor, jeans/khakis, exact shirt
and location remain coherent WITHIN each video. Across the two ratios,
the photos, seeds, compositions and action plans MUST be different.

- Final: two 12-second H.264 MP4s, native 9:16 and native 16:9.
- Face visibly recognizable **at least 8 seconds of each 12-second MP4**;
  keep face and full print simultaneously in view for the main action.
- No chin-down fallback, camera-only sway, unnatural bobbing or
  idle model posing. A meaningful everyday story must finish naturally.
- Artwork Native Master exact; no embroidery, text warping or print drift.
- Do not reuse the portrait video in landscape, nor vice versa.

Upload MP4 using the existing direct CAM endpoint per missing ratio:
- `X-Muse-Lease: <claim lease>`
- `X-CAM-Motion-Version: CAM_STORY_REAL_LIFE_V3_3`
- `X-CAM-Story-Output-Contract: CAM_STORY_12S_TWO_TAKE_V1`
- `X-CAM-Story-Source-Contract: CAM_STORY_DUAL_HERO_FACE_V1`
- `X-CAM-Hero-Sha256: <verified CAM source-hero imageSha256>`
- `X-CAM-Hero-Request-Id: <matching ratio heroRequestId>`
- `Content-Type: video/mp4`

The upload is rejected before Drive if the source hero for the ratio
is missing or not the exact verified original. Do not overwrite any
previously saved/verified aspect, even if the new style looks better.

## Two-stage independent QA and results

For each of four pilot MP4s, independently inspect face-visible seconds,
meaningful actual body motion, wardrobe/garment SKU, Native Master print
and 150mm placement, and edit continuity around second 6. Compare BOTH
hero source images for genuine independent composition and absence of
cross-ratio conversion. Source pixel checks alone cannot prove truly
independent AI generation or facial visibility; visual QA MUST confirm.

Report for each listing/aspect:
`heroRequestId`, `heroSeed`, native image dimensions, original source
image SHA-256, final MP4 SHA-256, duration, face-visibility QA,
motion/story QA, print QA, and distinct-hero QA. Do not mark PASS merely
because files were successfully saved to Drive.

Keep V3.1 running for everything else, the existing scheduled cadence
unchanged, and all completed videos untouched. If Bridge production
still reports 1.1.0 or the new hero endpoints are absent, report BLOCKED
and continue safe legacy V3.1 work.
