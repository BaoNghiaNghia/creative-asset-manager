# Muse-02 shared Drive canary V1

Scope: one immutable operator-approved MP4 from Muse-02 generation 92d937d0-289b-48e0-bee8-49be1151ca3f. Both production workers already use CAM-managed storage through the existing listing video route. No new Google OAuth connection, credential copying, sharing changes, DB migration, provider rendering or production permission change.

## Runner handoff

Refresh GET /api/v1/muse-bridge/workers/config with the existing Muse-02 credential. Only Muse-02 receives rendererCanaryUpload. POST the original binary MP4 to its uploadPath with Content-Type: video/mp4, X-CAM-Canary-Run-Id and X-CAM-Canary-Sha256 exactly as advertised. Do not send JSON, a Drive URL or a listing claim. The approved sample size is 2735467 bytes. Use a bounded timeout and the existing full-response helper. A retry of the same bytes is idempotent via the existing Drive asset identity; never generate another canary merely to retry transfer.

Destination: CAM ImageGen Designs (1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl) / muse_02_renderer_canary. The API checks the exact folder, tenant and worker metadata, managed OAuth owner, restricted sharing, source hash, MP4 headers and saved Drive SHA/size readback. Any other worker, sample, root, parent, public file or invalid media is rejected. No arbitrary destination ID is accepted. Native bytes are preserved. Existing listing/video upload and quota behavior stay unchanged.

A successful response proves canary bytes and storage, not independent provider provenance, real-motion visual QA or a production listing receipt. Keep Muse-02 production readiness false until independent acceptance. Never use this endpoint to obtain a listing lease, and never treat the transfer as a render or permission to exceed provider limits.

## Tests and rollback

Run the existing Muse module tests, including test_renderer_canary.py. Fixtures are not provider evidence. After normal reviewed backend deployment, verify live route registration and unauthorized/wrong-worker rejection without sending an MP4. Genuine end-to-end verification requires the original Muse-02 bytes. No migrations or dependencies. Rollback using the standard immutable backend release rollback; the private canary asset, if uploaded, remains isolated and is not deleted.
