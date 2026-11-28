# CAM_SMS_SYNC_V2

## Scope and authority

Idea-only handoff for Tiktok_McGowanshopdesigns, C1717. CAM produces a
review candidate, not an automatic SMS write or marketplace listing.
Existing marketplace preflight blockers remain unchanged; the new
`sms_sync_v2.ready_for_idea_review` is separate. A visible storage-verified
9:16 UGC receipt is sufficient with the exact current managed Native Master,
source metadata, reference photo and SEO draft; no visual video QA,
template, prices, SKU, final listing images or 13-role completion gate.
The unverified USA shipping/customs placeholder is not exported.

The root is the already approved CAM ImageGen Designs Viewer root. Reference
images are the product photo and the matching design thumbnail only, not the
original print file. Verify both links actually return the expected image
before each confirmed SMS operation. CAM transfers only Drive view URLs for
video; SMS can independently fetch media. No claim is made that SMS internally
stores a pure link or that video playback is proven.

## Operator workflow

1. Read GET /api/v1/etsy-spy/listings/{listing_key}/listing-idea-preflight.
   Review `sms_sync_v2` and `media_integrity`. Do not send a blocked candidate.
   A missing pack/slot or preview size is a repair advisory, not a video QA gate.
2. Use the exact dedupe key (Etsy ID + destination shop). Check live SMS
   Listing Ideas, both open and discarded, and the exact existing target.
   Verify the Etsy ID field; title search alone is insufficient. Never create
   a second idea if its identity is unresolved. A prior report is not a
   substitute for a fresh live duplicate check.
3. For the candidate's current vertical video SHA and file ID, call the
   existing authorized SMS video-share prepare API (CAM_SMS_VIDEO_SHARE_V1).
   Require state=viewer_link_verified. Use its prepared /view URL in both
   targets[].video_urls (exactly one) and the 9:16 idea field. Retain the
   horizontal link only in Fields when present. Do not send bytes, files,
   template, price or publish actions. Re-read preflight if source changes.
4. Present the exact create/update payload to the user and obtain SMS's
   explicit confirmation. For an existing target use its target_id, keep
   unchanged metadata and do not append another video when videos=1. If its
   video identity cannot be verified or differs, stop and resolve with SMS;
   do not blindly replace or clear an existing video.
5. Read the resulting idea via SMS MCP. Require correct Etsy ID and shop,
   two reference images, one Target Video, correct vertical Field, and no
   listing made/live. If an SMS call returned success but videos=0, it is
   incomplete. Fields alone do not satisfy Target Videos. Do not blindly retry.
6. Authorized CAM manage caller may POST
   /api/v1/etsy-spy/listings/{listing_key}/sms-sync-readback using this
   exact JSON shape (values below are illustrative):
   ```json
   {
     "confirm": true,
     "candidate_id": "<current preflight candidate SHA256>",
     "idea_code": "I123",
     "idea_id": "<SMS Idea UUID>",
     "target_id": "<SMS target UUID>",
     "shop": "Tiktok_McGowanshopdesigns",
     "etsy_id": "<Etsy ID>",
     "reference_images": 2,
     "target_videos": 1,
     "listings_made": 0,
     "listings_live": 0,
     "vertical_field_url": "<same prepared Drive view URL>"
   }
   ```
   The receipt is explicitly an operator-reported MCP count/Field observation,
   not a CAM fetch or verification of target URL identity. Same reports are
   idempotent; conflicting idea/target identities and stale candidates fail.
   Subsequent changes to candidate data mark the report stale. Normal draft
   saves cannot forge or erase the server-owned receipt.

## Audit and repair

GET /api/v1/etsy-spy/listing-media-audit?limit=20 returns bounded
tenant-scoped pages. Follow next_after until null. The response includes
per-pack saved slots, missing main, preview-only slots, manifest readiness,
and a design SHA classification. Distinct currently mapped design variants
are not automatically stale. If the latest extraction is unresolved,
keep existing outputs and investigate. A pack that is not in the current
managed design set must be checked against the exact latest intended
placement before replacement. Old manifests may have 10 roles; without
persisted role count or slots 11–13, missing V13 roles are advisory only.

Use existing Image Agent slot coverage, queue refill and regeneration
mechanisms after operator review. Do not delete/replace completed videos or
images merely because a different variant exists, or lower resolution/QA
rules to label a preview final. V2 does not automatically regenerate media.

No new database/migration, cron, SMS credentials, duplicate uploads, or
marketplace write path. Production rollout requires regression tests and
health checks; rollback keeps the existing draft and audit history.
