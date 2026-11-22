# CAM Muse — Story-Driven UGC V3.3, 12-second final video

**Current requirement (2026-11-22): 12-second final MP4, NOT 14 seconds.**
This supersedes the original 14-second V3.3 proposal. The existing Muse
renderer has a reported maximum of 10 native seconds *per take*. Never
pretend the tool can create a native 12-second take without proving it.

## Status and boundaries

- Story V3.3 source and CAM Muse Bridge contract are implemented. The current
  production version and external Muse scheduled task must be verified
  separately before making claims of deployment or active use.
- `CAM_MUSE_STORY_V33_ENABLED` remains **default OFF**. Enable ONLY after the
  existing external `cam-auto-video` worker can read the V3.3 bridge fields,
  create real 12-second stories within its source-render limit, and pass an
  independent canary. The flag is not a substitute for renderer capability.
- Preserve V3.1 for existing unclaimed/in-flight jobs until an approved
  cutover; never change completed or partially uploaded video receipts.
  V3.2 failed pilot remains frozen. V4 print-tracking PoC is independent.
- No DB migration, extra DB, extra browser worker/profile, new AI API, or
  changes to approved Native Master artwork are required for the prompt spec.

## Twelve-second three-act structure

- **0-3s context:** a meaningful real-life situation, body already moving.
- **3-9s action:** real steps, rising from an approved chair, leaving through a
  real door, or following a real bend — not head swivels or camera motion.
- **9-12s payoff:** complete the activity with unforced, physically coherent
  movement. Never freeze a frame to extend a shot.

There are 13 theme families with two script options each (26 stories):
outdoors, ride, holiday, gaming, sports, birthday, pets/wildlife artwork,
coffee, family, romance, retro, humor, everyday. Title cues identify a safe
theme only: title text is NEVER new printed artwork or the garment SKU.
The same deterministic story/person/wardrobe/artwork is used for 9:16 and 16:9;
camera framing may be adapted but a camera pan cannot replace human action.

### Genuine 12-second assembly using a <=10s native renderer

**Preferred method:** render two genuine moving source takes, each at most
10 seconds. Retain six usable real-time seconds from each and join 6+6 to a
12-second final MP4, with **one motivated cut** at the 6-second mark.
The action begins before the cut and continues meaningfully after it.
The two shots must have coherent person, approved shirt/print, pants, light
and location. If a genuine single native 12-second take is eventually proven
available, it may replace the two-take method without changing the target.
Do **not** use reversed footage, looping, frame duplication, frozen holds,
slow-motion padding, a camera-only transition, or a static portrait.
If two matching active source shots are not available, report BLOCKED.

The exported MP4 is accepted by the Bridge only if `ffprobe` reports
11.5-12.5 seconds, valid aspect ratio and H.264. Duration checking does not
verify meaningful motion or source integrity; a *separate visual QA* is required.
For final assembly, use an actual encoded MP4, never a mock receipt.

### Approved person, garment and wardrobe

Current CAM Muse jobs use the authoritative **Comfort Colors C1717**:
short-sleeve garment-dyed heavyweight cotton crewneck with relaxed/boxy fit.
Do not change it into a long-sleeve sweatshirt or hoodie even if the listing
title contains those words. Additional G500/G180/G185 future profiles require
trusted CAM SKU metadata, not guessing from the title.

For new V3.3 work use an approved source adult model with a natural,
proportionate average/medium or moderately athletic build. Select a reference
with **denim jeans** or cotton-twill **khaki/chino trousers** already visible.
Do not accept shorts, joggers, leggings, sweatpants, skirts, wardrobe swaps,
or inconsistent model/pants between aspect ratios. Never digitally slim,
reshape or retouch an actual person to meet a casting preference.
For QA, show the waist and upper thigh enough to identify trousers for two
continuous seconds in each ratio while keeping front artwork visible.

The exact approved Native Master SHA, original text and colors are mandatory.
Flat-ink print only; first print pixel is **150mm below the lower collar rib**
along fabric. No regenerated artwork, text, embroidery, perspective warping,
hand occlusion or inconsistent print position. Scene/action may use only objects
already present in an approved start frame, otherwise choose another or BLOCK.

### Bridge and external worker handoff

Use existing authenticated CAM Muse Bridge; do not create a duplicate task.
`GET /api/v1/muse-bridge/video-work` advertises the story spec and capabilities
even when the workflow is disabled. The bridge v1.3.3 /health returns:
`storyV33SpecAvailable=true`, `storyV33TargetDurationSeconds=12`,
`storyV33MaxNativeTakeSeconds=10`,
`storyV33NativeRendererVerified=false` and
`storyV33TwoTakeAssemblyVerified=false` (until independently verified).

The worker must read `storySpecV33`, `storyEnabled`, `missingAspectRatios`,
and `savedAspectRatios` for each eligible command. Only when the deployed
bridge advertises V3.3, the worker has proven the two-take assembly contract,
and the operator has reviewed the flag cutover, claim a NEW eligible listing:

```json
{"worker":"muse","storyVersion":"CAM_STORY_REAL_LIFE_V3_3"}
```

This pins a **12-second** V3.3 contract in existing `output_receipts` and
cannot silently change an older 14-second pin or a partially saved V3.1 job.
Upload each missing MP4 using `X-Muse-Lease` and
`X-CAM-Motion-Version: CAM_STORY_REAL_LIFE_V3_3`. Reject 10s or 14s final
uploads (422), mismatched version and invalid lease before Drive storage.
Never upload a different MP4 over an already verified ratio. Completing
storage still leaves independent garment/print/motion QA pending.

### Canary, release and rollback

- 2 new listings x 2 aspect ratios = **4 final 12-second videos**, no
  previously completed or V3.2 failed listings.
- Inspect sampled frames and shot transition for purposeful story action,
  matching approved model/jeans or khakis/C1717, accurate Native Master,
  150mm print anchor, and matching two aspect ratios. Reject the entire
  video when the only motion is gaze, sway or camera movement.
- Test fresh V3.3 claim, V3.1 legacy claim protection, old 14s pin refusal,
  one-ratio saved retry, 10s final rejection, correct 12s final acceptance,
  lease expiry and no overwritten verified Drive uploads.
- Run CI and reviewed backend release health gates. Do not bypass historical
  SQLite migration failures, UI fixture failures, privilege controls or dirty
  checkout guards to force production release.
- If canary fails, leave or reset the feature flag OFF for new claims.
  Already pinned 12s V3.3 work must not be silently converted back to V3.1.
