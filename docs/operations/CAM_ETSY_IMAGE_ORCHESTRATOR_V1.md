# Etsy Picked → CAM Image Orchestrator V1 (guarded)

## Scope
The existing dedicated Image Agent transport supports an opt-in, pinned `orchestrator_v1` claim mode for new Etsy Picked extraction jobs. The normal CAM/Codex provider, Etsy Spy scanning, historic receipts and previously leased jobs are unchanged.

## Production switches
- `CAM_ETSY_EXTRACTION_WORKER_ENABLED=1` and a provisioned `CAM_ETSY_EXTRACTION_STORAGE_ROOT` are the existing transport/storage prerequisites.
- `CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED=0` is the **new default**. This is intentionally **off** on deploy; enabling it is an operator-controlled pilot decision after the currently outstanding old ImageGen job is resolved.
- New Windows relay sends `X-CAM-Image-Worker: orchestrator-v1` on Claim. If disabled, the response is `{task:null,pause_reason:"orchestrator_not_enabled"}`. Old relays are paused with `orchestrator_device_upgrade_required` when the feature is on. Previously leased old-mode jobs can still complete via Check/Result, with no mode mutation.
- Once opted in, CAM pins `execution_mode:"orchestrator_v1"` into the claimed job and response. Subsequent retries of the same device/lease are idempotent.

## Durable processing
1. Windows image agent checks its dedicated device pairing, local bridge v0.9.1 and absence of an incompatible older active journal. The preflight does not claim.
2. After one CAM Claim, it caches the original image immutably, rejects a pre-existing direct ImageGen pilot for that same CAM job ID, and enqueues one local orchestrator job.
3. Fast CPU handles suitable PNG/JPEG sources. A harder `auto` task may enter the guarded ChatGPT assisted queue, using the current Chrome tab. The relay renews its existing CAM lease every polling iteration while a user saves/imports the exact job PNG. No ChatGPT session scrape or auto-resend of unknown results.
4. The local QA gate checks the PNG SHA-256, actual alpha pixel data, 4500×5400 RGBA canvas and 300 DPI metadata. It never asserts text parity, print approval or commercial rights.
5. CAM accepts `needs_review` with `engine:"chatgpt_assisted"` **only** for the pinned v1 lease and only with the bounded `cam-image-orchestrator-qa-v1` evidence. Old/unauthorized clients may still upload only Fast CPU review PNGs. A failed/ambiguous image remains held for operator review rather than being silently marked complete.

## Rollback
Disable `CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED` for **new** claims, then restart the API. Already leased pinned-v1 jobs must be allowed to Check/Result/ACK using the original Windows journal until completed/cancelled; never delete or reassign their outputs. Legacy launchers continue to use the original paths. Do not start old and new Windows daemons concurrently. The CAM image agent remains separate from Etsy Scout credentials.

## Verification before enabling
- Run backend Etsy Spy image-agent regression tests, confirm daily cap, storage quota, rights and tenant isolation.
- Confirm Windows local bridge `0.9.1` reports `orchestrator_job_lookup`, paired device probe `orchestrator_enabled:true` and healthy storage.
- Verify there is no active legacy device journal and no duplicate direct ImageGen pilot for the target job.
- Run exactly one new Etsy Picked pilot through Fast or ChatGPT, check source hash, lease renewal, immutable PNG receipt, private preview, manual QA and unpick cancellation before increasing daily cap.

The opt-in flag must remain disabled until the pilot checks pass.
