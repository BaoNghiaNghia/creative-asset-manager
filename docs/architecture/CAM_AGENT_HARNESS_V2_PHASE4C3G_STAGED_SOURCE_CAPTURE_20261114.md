# CAM Agent Harness V2 — Phase 4C.3G: stage network I/O outside PostgreSQL locks

**Date:** 2026-11-14. **Status:** default-OFF, unconnected code-only candidate, pending hosted CI. **NO production migration or dispatch.**

## Verified predecessor and precise scope

The preceding Phase 4C.3F created a bounded public-IP-pinned Etsy source downloader, an inert signed independent source byte manifest, and descriptor-pinned immutable PNG review reader. The previous `capture_server_source_for_existing_slot(session, ...)` incorrectly held `ProcessingJob`, paired Image Agent and device-slot row locks while it executed bounded HTTPS. Although no production route ever called it, that interface would cause agent throughput and lease contention once connected.

Phase 4C.3G **retires the old single-session entrypoint**: it returns `staged_capture_required` (or normal disabled/emergency-stop states) and does not execute HTTP, mutate SQL, or touch files. All staged capture is now isolated in `fenced_source_staging.py`, which only accepts a **server-owned Session factory** and defaults OFF.

## Three-stage transaction boundary

1. **Plan:** enter a fresh PostgreSQL Session, apply `verify_image_output_fence` under job/device/slot locks, confirm the server-owned allowlisted Etsy CDN URL and URL SHA256, and detect any existing signed capture. Return the immutable tuple `tenant_id, agent_id, job_id, attempt_count, claimed_at, fencing_token, source_url, source_url_sha256`. **Rollback and close the Session before network work**; no claim, slot, receipt, or queue changes.
2. **Fetch + stage (NO Session/SQL lock open):** acquire a nonblocking per-process bounded semaphore (2 fetches max in one process). Resolve public DNS, pin the selected public IP for certificate-checked TLS using the Etsy hostname/SNI, disallow HTTP redirects, content encoding, proxies and malformed body types. Bound image bytes and decoded pixels, recheck the decoded source, save immutable SHA256-named bytes via exclusive temp+fsync+atomic hard link. Any capacity, HTTP, image or disk problem fails closed; a post-fsync process crash may leave an **orphan** blob only, never a database proof. The in-process semaphore is **not** a distributed fleet-wide capacity admission and does not replace future dispatcher controls.
3. **Finalize:** open a **new** PostgreSQL Session, reacquire the authoritative job/device/slot locks, require exact fencing token and plan claim epoch, ensure source URL + URL SHA256 are unchanged and that no different manifest was committed while fetching. Re-read content using the no-follow, hash-pinned file descriptor; create the signed server-only HMAC manifest and update the ProcessingJob payload. **Commit only on `captured` or verified idempotent `already_captured`**; otherwise rollback and preserve any staged orphan for audited reconciliation. No external HTTP happens inside this second SQL transaction.

In-flight cancellation, worker change, claim increment, URL mutation, source-lease expiry or slot expiry must fail closed. An old signed manifest cannot be overwritten for a newer claim epoch. Captures DO NOT assert that a browser ran, that a model produced artwork, or that print/commercial rights were cleared.

## Evidence and tests

- Disposable CI PostgreSQL 12.22/16.4: a second connection obtains a `SELECT ... FOR UPDATE NOWAIT` on the same ProcessingJob **during the fake source fetch**, proving that the download is outside the first DB transaction. Cancellation and source URL mutation midway reject finalization, even if a content-hashed file exists. A second claim epoch mid-fetch rejects the old fence.
- Simulated process crash after fsynced immutable blob but before final SQL commit leaves no manifest; a later valid retry reuses the exact same digest. Invalid fake image and capacity saturation cannot sign anything. Existing provenance, symlink, token, receipt and viewer tests still run.
- All source network calls in tests are stubbed; no actual Etsy, ImageGen, browser or paid provider calls. A hosted **synthetic** DB migration/restore exercise does not prove recovery of actual production data.
- CI must be green on the exact PR head before review. Even a green CI **does not authorize production** migration `0209/0210` or activating any of the functions.

## Remaining blockers

1. Independently restore a **real production backup off-host**, with checksums, complete schema/table-row evidence and rollback plan. No full restore may run on the production VPS.
2. Reviewed globally durable job/device admission, network egress/rate-budget controls across processes, DNS resolver timeout, cancellation, telemetry and operator reconciliation. The current semaphore is only process-local and the stage is not integrated into the scheduler.
3. Independently verifiable browser/provider execution and exact source-to-PNG lineage. Even a valid server HMAC of source bytes always has `may_accept_output=false`.
4. Authenticated, no-dual-writer `/result` and media viewer cutover, rights/review policy, manual stale-receipt UX and one-job canary. Current production `<job_id>.png` path is untouched.
5. Keep feature flags, migration execution, dispatch, auto-learning, Windows agent restarts and provider/API calls **OFF**. Retain only current + rollback release when a separately approved exact main commit is deployed.
