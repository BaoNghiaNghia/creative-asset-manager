# CAM Muse Story-Driven UGC V3.3 — 14 seconds

**Status:** Implemented as a backward-compatible API preview and opt-in claim
contract. **NOT automatically activated on production or in the external Muse
scheduler.** Native Muse worker capability and independent media QA are
separate required rollout gates. No new DB, models, browser profile or secrets.

## User problem and new approach
The user reviewed six recent 12-second videos: Vintage Tiger (9:16/16:9),
Tonight We Ride (9:16/16:9), Adventure Hiking (9:16/16:9).
All showed too little purposeful motion (mostly looking around / swaying).
Four samples showed a long-sleeve sweatshirt rather than the required
Comfort Colors C1717 short-sleeve T-shirt; do not treat a saved Drive receipt
as proof of correct garment, action or print.

V3.3 turns 14 seconds into a small, believable life story:
- 0–3 s: start an understandable everyday situation while already moving.
- 3–10 s: perform a concrete environmental action: walk, enter a real door,
  rise from an existing chair, turn a real corner, step down a real path.
- 10–14 s: complete the action and settle naturally without frozen padding.

This is **one continuous event**, not 14 seconds of oscillating or staring.
Three camera styles are selected deterministically per listing/artwork hash,
but every style requires actual PERSON motion, not camera-only movement.

The bounded theme classifier selects one of two story variants for:
outdoors, ride, holiday, gaming, sports, birthday, pets/wildlife-print,
coffee, family, romance, retro, humor, and everyday fallback.
Two aspect ratios reuse exactly the same story/camera action, just adapted
framing. Marketplace title is used as a *theme* only and is never copied into
the render prompt or treated as authoritative printed text.

### Garment and approved art
For the current CAM Muse job contract, approved garment is always **C1717**
short-sleeve, relaxed garment-dyed T-shirt regardless of listing titles saying
`sweatshirt`, `hoodie`, etc. The module has profiles for G500/G180/G185,
but they must be selected from authoritative CAM SKU metadata in a future
separate implementation, never guessed from a title.

### Model casting and trousers — CAM_STORY_WARDROBE_CASTING_V1

For all NEW V3.3 story videos, use an adult model with natural,
proportionate average/medium to moderately athletic build, without exaggerated
bulky body proportions. This is a **casting/start-frame selection preference**:
NEVER reshape, slim, retouch or alter the body of a real person in an existing
approved reference.

The only acceptable bottoms are **denim jeans** (blue/black or neutral
washed denim) OR **cotton-twill khaki/chino trousers** (beige/tan/olive),
in a believable straight or relaxed fit. Do NOT select shorts, sweatpants,
joggers, leggings, skirts or formal trousers. Trousers and model build must
already match an APPROVED starting frame; select another approved reference
or BLOCK, rather than generating a wardrobe swap or making a person's body
appear different. Apply exactly the same person, trousers, shade and fit to
both 9:16 and 16:9. At least two continuous seconds per aspect must visibly
show waistband/upper-thigh area sufficient to identify the bottoms, while
keeping the entire front print readable.

Run garment/wardrobe/model-consistency QA on both ratios before video approval.
A valid SHA/Google Drive receipt does not prove pants or proportions are correct.
Versioned policy is emitted in `videoSpec.storySpecV33.wardrobeCastingLock`
for compatible Muse workers; V3.1 jobs and completed variants are unchanged.

Retain original Native Master SHA, lettering, color, scale, flat ink and
top-of-ink 150 mm below lower collar rib along actual fabric. Do not create
new pets, actors, motorcycles, coffees, doors or scenery absent the approved
start frame. If the selected story requires a path/door/chair not visible in
that frame, select another approved start frame or mark BLOCKED; never
invent objects or force an implausible pose.

### Illustrative deterministic themes
- **Vintage Tiger:** normal park walk; real forward steps, a path bend,
  then an unforced glance at actual surroundings. Do not generate a tiger.
- **Tonight We Ride:** walk through an existing doorway/driveway or along
  a real evening sidewalk. Do not generate a motorbike or sweatshirt.
- **Adventure Hiking:** arrive at a real trail viewpoint after forward
  steps, stop with realistic foot/body motion, then continue. Do not
  substitute pan/zoom or a stationary subject.
- **Holiday / Christmas:** enter a real room and prepare for a gathering.
  No gifts/people/decorations are hallucinated.
- **Gaming:** stand up from an already visible desk/chair and leave for
  a break; no newly invented controller/monitor.

## API contract and rollout safety

`GET /api/v1/muse-bridge/video-work` exposes `storySpecV33`,
`storyEnabled`, `targetDurationSeconds` and `storyCapabilityRequired`.
`GET /jobs` and `POST /jobs/{listing_id}/claim` expose
`job.videoSpec.storySpecV33` for never-rendered listings.

**Default OFF:** `CAM_MUSE_STORY_V33_ENABLED=false`. With flag OFF,
legacy V3.1 remains the actual 12-second render specification; V3.3 preview
can be used to update and test the Muse external worker. Enabling flag without
a capable Muse worker would deliberately refuse first claims rather than
silently publish another low-quality legacy video. Never enable until a
sandboxed worker can create an actual native 14-second clip with quality QA.

When enabled, a compatible Muse worker must:
1. Authenticate with existing scoped Muse bearer and read the job videoSpec;
   fetch and verify the approved Native Master.
2. Verify the selected story's required scene and approved C1717 reference.
   Do NOT render unless one actual start frame physically supports the story.
3. `POST /jobs/{listingId}/claim` with JSON
   `{"worker":"muse","storyVersion":"CAM_STORY_REAL_LIFE_V3_3"}`.
   CAM atomically pins V3.3 in existing `output_receipts`, preserving
   any prior visual QA and verifying artwork SHA / C1717.
4. Render **one native 14-second continuous take**, not 12 + loop/stretch.
   Sample acceptance of MP4 duration is 13.5–14.5 seconds. If native 14s
   cannot be generated, report BLOCKED rather than spoofing the duration.
5. Upload ONLY missing 9:16 or 16:9 file via existing direct MP4 endpoints
   using `X-Muse-Lease` and
   `X-CAM-Motion-Version: CAM_STORY_REAL_LIFE_V3_3`.
   Upload rejects mismatched version and out-of-range duration **before**
   Drive storage. Each ratio is kept immutable once saved.
6. Complete normal command receipt flow. Independent garment, story/action,
   native-art, print placement, human motion, and event consistency QA is
   still required per ratio. `storageVerified=true` and `camLinked=true`
   are NOT content QA. Do not mark APPROVED merely due to two MP4s.

Claims of older unfinished/partially saved or actively leased V3.1 jobs retain
legacy behavior. Previously completed videos are never recreated or overwritten.
V3.2 failed motion pilot remains frozen. V4 plain-shirt tracking/compositing PoC
is a separate controlled experiment.

## Acceptance and rollback
- Canary: 2 new listings x 2 aspect ratios = 4 native 14-second videos.
- Each must visually pass independent Garment SKU, motivated Motion/Story,
  Artwork/Print, and environment QA; no head-only sway, recycled frames,
  uncontrolled garment morph or print occlusion.
- Verify one complete and one partially saved legacy job remain unchanged,
  old worker without storyVersion receives 409 on a new claim when enabled,
  and 14s pin is preserved across lease expiry/retry.
- Fail-closed canary: disable the opt-in flag if anything violates its gates.
  Already pinned V3.3 jobs remain pinned and must be finished with the
  compatible worker or handled by the existing failure/lease policy;
  disabling the feature does not rewrite them into V3.1.
- Do not change a saved Drive MP4 or existing CAM job's artwork SHA.
- No migration, new database, destructive cleanup or production secret edits.
- Before production release: targeted tests + CI/staging gates, available
  native Muse capability, no unrelated CI regressions, and verified live
  deployment health. The new API cannot itself schedule/control the external
  Muse rendering tool.
