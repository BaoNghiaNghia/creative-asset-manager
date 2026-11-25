# Muse runner integration: CAM Muse Bridge 1.4.15

Status: CAM backend deployed. Runner-side integration is not confirmed until genuine worker events arrive. No new automation or worker account is required.

Apply to the existing Muse-01 and Muse-02 tasks, in place. Keep the existing scheduler, authenticated worker transport, role/standby policy, current story and hero rules, and verified output handling. Never create synthetic renderer proof or regenerate completed ratios.

## Install
Copy muse_observability_outbox.py into each existing runner's persistent runtime directory. Use separate private outbox directories for muse-01 and muse-02, not a temporary folder and not a shared directory. Never put bearer tokens, command/video lease values, images, prompts or raw provider responses in the outbox.

The transport callback must use the existing account-specific authenticated full-response HTTP helper:
POST /api/v1/muse-bridge/automation/events
JSON body: {"events":[...]}
Return (integer HTTP status, decoded JSON object, response headers). Use a bounded request timeout. Do not expose credentials in log or exception messages.

Read GET /api/v1/muse-bridge/workers/config at each normal configuration refresh. Require observability.enabled and version CAM_MUSE_OBSERVABILITY_V2. If disabled, leave the core runner behavior unchanged.

Example:
    from muse_observability_outbox import Outbox
    out = Outbox(existing_private_persistent_worker_dir / "telemetry", send_events)
    out.emit("RUN_STARTED", state="polling")
    out.heartbeat("polling")
    out.flush()

Wrap local telemetry failures in a nonfatal logging boundary so they cannot prevent the existing command claim/complete or media delivery. Keep the existing full-response/lease validations. Outbox replay never grants authorization.

## Original artwork source (CAM Bridge 1.4.18+)
Read artworkSourcePolicy in workers/config and artworkSource in each job/command. After a legitimate claim, GET the authenticated job artwork path as binary PNG. Check X-CAM-Artwork-Variant=original and compute SHA256 of the entire downloaded bytes. It must equal artworkSource.expectedSha256 and X-CAM-Artwork-SHA256. Use the original unmodified bytes for garment print/hero creation. Never use thumbnails, previews, Etsy product photos, browser screenshots or resized/re-encoded artwork as a substitute. Existing approved heroes may be reused only when bound to the same artwork SHA and current workflow. The isolated transparent design is not a video start frame; generate/render the approved wearer/hero. Missing originals or mismatching bytes are ARTWORK_UNAVAILABLE; do not generate or fall back to a thumbnail. No new claim fields, extra renderer call or extra task is required.

## CAM-authoritative dispatch (CAM Bridge 1.4.19+)
For new work, read workflowController from workers/config, then GET /commands?limit=20. Consume executionContract.nextAction and its commandClaimHeaders. Do not join discovery to /reviews or a local review cache. The /reviews collection contains pending review tasks, not the full historical QA ledger. Read /jobs/{listingId}/artwork-qa only if a current QA record needs inspection. A CAM-selected create_video uses the existing SHA-matched QA record and still requires actual command and job claims. Artwork review compares the exact Original PNG to the reference design and placement; the original listing garment need not be C1717. Use a genuine adult C1717 wearer in the generated hero and output video. Preserve all genuine holds, provider refusals, renderer availability and cooldowns. Only follow the exact live videoJobClaim body for this job; fresh Story Lite does not require legacy geometry/two-take proof. Keep prior pinned legacy contracts intact. One renderer operation, then upload and complete the required aspect as soon as CAM verifies it; never wait for a deferred ratio. Preserve existing scheduler and real telemetry.

## Receipt acknowledgment recovery (CAM Bridge 1.4.20+)
A lost HTTP response after a genuine successful video completion is not a renderer failure. Keep the exact success completion request securely until CAM acknowledges it. On timeout, replay that exact request to the same command /complete endpoint with the same original command lease. For up to 24 hours CAM can acknowledge a matching already committed receipt without a new claim, without modifying state, and without generating another video. If CAM rejects the retry, inspect the existing video-status receipt and ordinary lease recovery; never generate an already verified ratio or transfer a lease. A re-review command authorized by a CAM operator follows the same normal artwork review requirements. Never reuse the archived failed review as a new PASS.

## Runner checkpoints
- Start the current run: RUN_STARTED, WORKER_HEARTBEAT.
- Normal polling: POLL_SUCCESS or QUEUE_EMPTY when actually observed.
- Actual successful claims: COMMAND_CLAIMED, JOB_CLAIMED with the real command/listing identifiers.
- Actual verified source: HERO_READY.
- Before a real renderer call: RENDER_STARTED.
- Actual renderer failure: RENDER_FAILED with real provider status and applicable error enum, e.g. PROVIDER_UNAVAILABLE for 502/503/504, PROVIDER_RATE_LIMIT or VIDEO_DAILY_BUDGET_EXHAUSTED for a confirmed 429. Record actual Retry-After only when present.
- Cooldown transitions: COOLDOWN_STARTED / COOLDOWN_ENDED.
- Successful upload and actual CAM acknowledgment: VIDEO_UPLOADED / CAM_ACK_RECEIVED.
- Actual end: RUN_FINISHED. Unexpected error: WORKER_ERROR.
- During actual runner activity, idle/cooldown checkpoints and render polling, call heartbeat with the current real state every 60 seconds when possible. For blocking renderer calls, integrate a scoped heartbeat into the existing runner lifetime if supported; do not launch a separate process that can claim the worker is alive while the runner is dead.
- At each checkpoint use flush with bounded timeout; retry on later checkpoints. Existing scheduling remains unchanged. Reopen the same outbox on restart.

Telemetry does not renew command/video leases. Continue existing command heartbeats only while a legitimately claimed lease is held. Preserve quota policy and worker eligibility: Muse-02 cannot take production video work until CAM's independent renderer verification gate passes.

## Acceptance checks
1. Read /automation/diagnostics using existing authenticated access.
2. Run the existing tasks normally, without a forced generation or duplicate claim. Confirm each worker's genuine RUN_STARTED and WORKER_HEARTBEAT events in /automation/events, and heartbeat age <180 seconds.
3. For actual work, confirm event order and matching real commandId/listingId.
4. Simulate a telemetry-only network failure using offline tests, not a renderer outage. Confirm original event IDs survive restart, duplicate retry is safe, rejected IDs are quarantined, and only accepted/duplicate IDs are removed.
5. Do not mark the worker executable solely because telemetry arrived. The CAM upload receipt and independent renderer verification gates remain separate.

Tests on CAM: python3 -m unittest discover -s ops -p test_muse_observability_outbox.py -v