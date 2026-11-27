# CAM_SMS_VIDEO_SHARE_V1

## Operator handoff
The CAM-managed Drive root remains Restricted. No grants occur when Muse creates a video or when preflight is read. Before a confirmed SMS create/update, an authorized CAM operator with etsy_spy.manage calls:

POST /api/v1/etsy-spy/listings/{listing_key}/sms-video-share
Content-Type: application/json

Body (example shape, not live values):
```json
{"action":"prepare","platform":"TiktokShop","aspect":"9:16","expected_file_id":"exampleFile123","expected_sha256":"<current video SHA256>","confirm":true}
```

Obtain the current SHA256 and file ID from that listing's current storage-verified CAM preflight table_video link. Never accept an arbitrary Drive URL or a manual field as proof. TikTok supports only 9:16; Etsy supports either ratio. Use one request per file. The route derives tenant from the authenticated principal and revalidates the exact receipt under a row lock. It persists an audit intent before the external mutation, records completion/failure in the existing auth audit table, and uses the existing CAM-managed OAuth connection. No schema migration or new credential is required.

Only state=download_verified is a successful prepare result. Use the returned video_urls for the confirmed SMS MCP create_listing_idea or update_listing_idea. Preserve other target fields and check SMS import status; a link count alone does not prove ingestion. This API does not call SMS, publish a listing, or modify queue state. External MCP callers must explicitly invoke the prepare step; existing read-only callers are not automatically migrated.

## Safety and recovery
Grant only anyone/reader, non-discoverable, on the selected MP4. Root, folders, master artwork, and other assets are not modified. Anyone possessing the video link can download it. Check exact file/parent/tenant/asset/hash and a bounded anonymous MP4 header probe. A header probe does not establish full SMS ingest.

On failed probe, attempt to revoke only the newly created permission; leave existing grants untouched. On GRANT_REVIEW_REQUIRED, ROLLBACK_REVIEW_REQUIRED, or AUDIT_REVIEW_REQUIRED, stop handoff and have the authorized owner review the actual permission state. Do not blindly retry. The current receipt can be revoked using the same request with action=revoke; only CAM-created grants are eligible. If a receipt has been replaced, historical revocation requires the authorized owner's review. Revoke only when SMS no longer needs the link.

Release rollback removes this route but does not revoke existing Drive permissions. No MP4 copy or separate database is created.

## Verification
Isolated tests must pass before deploy: SMS share adapter and failure tests, route RBAC and audit tests, existing listing idea preflight, Drive and design preview tests. Live Drive permissions and SMS import require an authorized single-listing canary; mock test success is not proof of live ingestion.
