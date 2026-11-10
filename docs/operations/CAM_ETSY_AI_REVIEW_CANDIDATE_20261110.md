# Etsy Picked AI-review candidate V1 — 2026-11-10

## Purpose and boundary

The single real pilot, listing `4457227360` / job `4e0f2418-792d-499c-b0d1-f4b9266000ca`, completed Fast Extraction with `needs_ai` / `textured_or_nonuniform_background`. It must **not** be reclaimed or silently changed to `needs_review`. The original SHA256-bound job result is immutable.

The Windows machine has produced a transparent 4500×5400 PNG **draft** by manually targeted, local three-colour layer separation (not a machine-learned image model). Its original artwork region is about 941×1133 pixels; the canvas enlarges it approximately 3.81 times. Text fidelity, edge quality, the children's-book-inspired characters and usage rights remain **unverified**. No print-ready or commercial-use declaration follows from transparency or canvas size.

## Offline Windows evidence

- Source JPEG is the immutable file already cached by Windows Image Agent under the original job ID, verified against SHA256 `6371541496e68468d1dfdc0db4afac780f399bcc9d6d12aaf9a7a9ed3c723a5d`.
- First experiment `layered-preview-v1.png` was rejected during visual QA because the green illustration was white and the bottom lettering was clipped. It remains only as local historical QA evidence.
- Corrected draft `D:\\CAM Image Extension\\.runtime\\extraction\\ai-review\\4e0f2418-792d-499c-b0d1-f4b9266000ca\\layered-preview-v2.png` is a separate file, not the original receipt asset. Manifest `candidate-v2.json` records SHA256 `3707a7a8e4991bdd43302765622bfd9028bd3e8536fa3aff89a291a28d406ff4` and explicit `print_ready=false`, `rights_verified=false`, `ocr_verified=false`, `not_an_ai_model=true`.

## Private CAM handoff

1. Windows `node agent/upload-ai-candidate.mjs` loads the **existing paired device token locally** and validates the fixed source/job/tenant/PNG SHA256 before uploading. It never emits bearer credentials, touches the original journal, or claims another job.
2. The tenant-bound, paired-device-only `POST /api/v1/etsy-spy/image-agents/{agent_id}/review-candidate` requires the original completed `needs_ai` job, same claiming agent, matching source SHA256, and an RGBA 4500×5400 PNG with reviewed-only QA flags. The stored review-candidate digest is immutable; identical replays are idempotent. Candidate storage is private, quota-controlled and atomically written. It is separate from the original receipt.
3. A logged-in CAM user with `etsy_spy.read` in the same tenant, and with the listing still Picked, can open `GET /api/v1/etsy-spy/image-agents/candidates/{job_id}`. Other tenants, Unpicked products and unfinished jobs cannot access it. Responses are no-store, inline PNG only.
4. The Picked table continues to show `Needs AI` and additionally offers `Review draft PNG` and a warning. The uploaded local draft does not qualify as finished AI art or print-ready output.

## Safety & rollout

- Existing Picked data, Scout Queue V3, SMS, daily Image Agent cap (1/day), and the original `needs_ai` result are unchanged.
- Tests cover original receipt immutability, idempotent upload, tenant isolation, role checks, Unpick revocation, no fresh claims, PNG SHA256, and invalid rights claims.
- Deploy backend before the frontend so the review link never points at an unimplemented endpoint. Verify API health, then upload exactly the local draft and check the database. If upload fails, the local candidate remains on D: and may be replayed; never re-claim the original job.
- Rollback is the previous CAM API/client release. The candidate file and source remain retained; do not delete historical evidence or roll back database storage destructively.
- Future optional ImageGen/MausBot work should create a **different versioned candidate** only under an explicit rights/QA workflow, not overwrite this immutable candidate or trigger unbounded paid calls.
