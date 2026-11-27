# Muse Image Lite — 2026-11-27

Independent image-only pilot using the existing processing_jobs table (job_type cam_muse_image_lite). No migration, VM, new database, or changes to the video router, worker, credential, cron, or video cooldown. Production rollout must preserve active video work; do not restart video workers.

## Scope and guards

One C1717 front-print listing / Role 01 pilot per tenant; never reuse an existing gallery or deletion receipt. Verify original Native Master SHA, Skill V13, palette, print-box authority and 150 mm placement lock. Require independent operator evidence of the dedicated image renderer/resource, plan and placement, and a distinct image-only worker credential. A heartbeat is not proof that provider capacity is independent. Respect all provider quotas and cooldowns; do not borrow or circumvent a video budget. No full-pack scheduling or bulk auto admission is enabled in this pilot.

Use a separate image worker identity muse-image-01, with a SHA256 verifier configured as CAM_MUSE_IMAGE_LITE_WORKER_TOKEN_SHA256. Provision the secret privately on the image runner. Do not reuse video credentials. Existing Image Agent authentication is used only for Extension reservation and status. Do not log secrets or store the raw lease token.

## Transport

All paths below are relative to /api/v1/muse-bridge/image-lite. Existing video /commands and /workers/cooldown are not used.
- GET /work: image-only discovery and gating.
- POST /operator/prepare: independently verified plan, runner/resource identity and evidence. Preparation is not authorization to render.
- GET /reservations and POST /commands/{cid}/legacy-fence: paired Image Agent durable exclusive listing reservation. No ACK if an existing pack or unresolved send prevents exclusivity. Requires installed Bridge changes and fresh fence.
- POST /commands/{cid}/runner-ready: report separately verified runner, workflow/resource identity and videoJobsEnabled=false. Fresh heartbeat required.
- POST /operator/commands/{cid}/enable: operator explicitly enables pilot after prerequisites are verified. /stop revokes new-send permission, /cancel only cancels pending unsent work.
- POST /commands/{cid}/claim: one owner, with X-CAM-Skill-Sha256.
- POST /commands/{cid}/artwork: epoch and lease; exact original PNG with SHA headers.
- POST /commands/{cid}/start: epoch, lease and attemptId; one-shot send permission. Never replay unknown sends or uncertain uploads.
- Image-specific heartbeat and cooldown. A 429 pauses this image flow, not video. No retry of an uncertain sent attempt without reconciliation.
- Independent operator QA and GET /commands/{cid}/qa-status. PUT /commands/{cid}/image: raw PNG plus epoch/lease/image SHA/QA receipt headers. Actual Drive readback, atomic gallery receipt. 1600px is preview-only; publication requires native >=2000px. This is role-level, not full-pack publication.
- GET /commands/{cid}/status: reconcile state and receipts.

## Verification and activation

50 regression tests passed, one PostgreSQL-only case skipped in the default suite and passed separately against disposable PostgreSQL. Extension reservation tests 3/3 passed; source syntax check passed. No production render attempted by these tests.

Deploy API only when safe (no migration, no video worker restart). Verify old video contracts, existing Extension coverage, and new /work live. Load updated Extension Bridge at a safe idle boundary; confirm live reservation handshake. Provision distinct image credential and separate Muse image runner; verify actual resource independence, installed Skill hash, operator evidence, and source bytes. Run one real Role 01 canary, independently review and verify saved receipt before any broader automation. If any gate fails, retain PREPARED_DISABLED. Never fake a readiness or QA receipt.
