# CAM_SMS_VIDEO_SHARE_V1

## Operator handoff
The CAM-managed Drive root follows CAM_IMAGEGEN_LINK_VIEWER_V1: Restricted or the approved non-discoverable Viewer root. No grants occur when Muse creates a video or when preflight is read. Before a confirmed SMS create/update, an authorized CAM operator with etsy_spy.manage calls:

POST /api/v1/etsy-spy/listings/{listing_key}/sms-video-share
Content-Type: application/json

Body (example shape, not live values):
```json
{"action":"prepare","platform":"TiktokShop","aspect":"9:16","expected_file_id":"exampleFile123","expected_sha256":"<current video SHA256>","confirm":true}
```

Obtain the current SHA256 and file ID from that listing's current storage-verified CAM preflight table_video link. Never accept an arbitrary Drive URL or a manual field as proof. TikTok supports only 9:16; Etsy supports either ratio. Use one request per file. The route derives tenant from the authenticated principal and revalidates the exact receipt under a row lock. It persists an audit intent before the external mutation, records completion/failure in the existing auth audit table, and uses the existing CAM-managed OAuth connection. No schema migration or new credential is required.

Only state=viewer_link_verified is a successful prepare result. The TikTok preflight exposes sms_target_video_plan with one current, storage-verified 9:16 candidate; candidates are not write receipts. Use the prepared canonical /view URL in targets[].video_urls on the confirmed SMS MCP create_listing_idea or update_listing_idea. For TikTok, send exactly one vertical video, never the horizontal video. Keep the idea-level Fields as additional references (both aspects when available), not as a substitute for Target Videos. CAM sends URLs only, not MP4 bytes or files. SMS may fetch/download the linked video on its side; its internal storage semantics are not guaranteed by this contract.

Check for an existing idea and exact shop target before creation. For an existing target use target_id, preserve other fields, do not create a duplicate. After mutation, call listing_ideas with the idea ID and verify the intended target has videos=1, the reference images and metadata remain, and no listing has been published. If target already contains a video, verify its identity where available before retrying or replacing; do not blindly append or clear it. If the response has download warnings, stop and inspect the link and target. Fields-only or a successful MCP response without target readback is not completion. The preflight and prepare API do not call SMS themselves or publish; the authorized MCP caller must follow this contract and its explicit confirmation requirement.

## Safety and recovery
If the approved root already provides inherited Viewer, do not change individual file permissions. Otherwise grant only anyone/reader, non-discoverable, on the selected MP4. Root, folders, master artwork, and other assets are not modified by prepare. Anyone possessing the video link can download it. Check exact file/parent/tenant/asset/hash and an anonymous HEAD of the canonical Drive /view HTML page. CAM does not request the MP4, follow redirects, or import media. SMS may fetch the supplied link after handoff. A permission readback and reachable page do not establish inline playback inside SMS.

On failed probe, attempt to revoke only the newly created permission; leave existing grants untouched. On GRANT_REVIEW_REQUIRED, ROLLBACK_REVIEW_REQUIRED, or AUDIT_REVIEW_REQUIRED, stop handoff and have the authorized owner review the actual permission state. Do not blindly retry. The current receipt can be revoked using the same request with action=revoke; only CAM-created grants are eligible. If a receipt has been replaced, historical revocation requires the authorized owner's review. Revoke only when SMS no longer needs the link.

Release rollback removes this route but does not revoke existing Drive permissions. No MP4 copy or separate database is created.

## Verification
Isolated tests must pass before deploy: SMS share adapter and failure tests, route RBAC and audit tests, existing listing idea preflight, Drive and design preview tests. Live Drive permission, link accessibility and SMS link rendering require an authorized single-listing canary; mock test success is not proof of live viewing.
