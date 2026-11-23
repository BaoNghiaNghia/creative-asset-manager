# CAM Muse Bridge — low-latency queue rollout (P0/P1)

Status: SOURCE VALIDATION ONLY until dependency-complete HTTP/DB checks and a
real Muse worker canary pass. A green syntax gate is not a production rollout.

Scope: Muse worker discovery only. No video regeneration, lease rule override,
new DB, new scheduled ChatGPT automation, or changes to V3.1/V3.2/V4.

## Implemented
- Lease-aware tenant-filtered SQL discovery in `command_queue.py`. Active
  30-minute leases and claimed jobs without a valid expiry no longer occupy
  the first 100 rows; a stable command-ID tie-break is applied.
- `GET /commands` and `GET /video-work` use the shared eligible query.
- `GET /commands/wait?waitSeconds=20&limit=1` is a bounded read-only long poll.
  It uses fresh short DB sessions every 2 seconds and returns immediately
  when eligible work is discovered. The ordinary `/commands` API is retained.
- `/video-work` advertises a 20-second polling fallback and long-poll URI.
  The worker must be updated separately to call it; a Muse scheduled task
  running every five minutes will not become continuous on its own.
- The existing atomic claim API and 30-minute renewable leases remain the
  source of truth. The long-poll response MUST NOT be treated as a claim.

## Verification completed and still required

- CodeLocal system Python: `python3 -m py_compile`, targeted `git diff --check`,
  and four dependency-free AST/control-flow tests all PASS.
- The repository's light staging gate passes with base `HEAD`, but skips pytest
  because `apps/api/.venv` is absent.
- Four lease/tenant/action SQLAlchemy tests passed in a separate SQLite
  compatibility harness, not on the production host; these do not replace
  execution of `apps/api/tests/test_muse_command_dispatch.py`.
- HTTP tests in `test_dispatch_http.py` still require the project's API venv.
- Full integration runs Docker PostgreSQL+Elasticsearch containers and has
  not been run on the disk-constrained production host.
- The pre-existing 150mm flat-ink artwork placement changes in the same
  router must be reviewed as a separate behavior contract, even if shipped
  with this patch. Never override any completed video.

## Worker integration
Muse-side bounded loop (only with authorized and tested Muse runtime):
1. GET /commands/wait?waitSeconds=20&limit=1 with Muse-scoped bearer.
2. POST /commands/{id}/claim and handle 409 by looping; never use unclaimed
   instructions as permission to start media generation.
3. For create_video: check exact approved Native Master SHA, review,
   `missingAspectRatios`, and CAM-verified Google Drive receipts.
4. Generate only missing variants; send command heartbeat during legitimate
   work and upload each MP4 with the current lease. Never overwrite any
   stored ratio. Do not regenerate previously completed videos.
5. Complete the command only after CAM checks the existing receipt contract.
6. On quota/backpressure, use a bounded retry/backoff without opening more
   browser profiles; do not synthesize progress heartbeats.
7. Immediately loop to the next claim after completion (rather than waiting
   until the next five-minute scheduled trigger).

The API does NOT launch a persistent Muse worker, configure external Muse
schedules or provide a Muse generation credential. These are separate rollout
steps; code here cannot guarantee production pickup latency.

## Canary and rollback
- Verify package dependencies, SQL query filtering, authorization failure
  paths and the focused test `test_muse_command_dispatch.py`.
- Start with a read-only polling canary, confirm immediate results for pending
  jobs, active/expired lease behavior, and tenant isolation.
- In the Muse runtime, test one new QA-approved listing (not a previously
  completed listing); measure queued-to-claimed P50/P95 and no duplicates.
- If errors increase, revert the worker to GET /commands and existing five-
  minute schedule. Existing claim/heartbeat/receipts are unchanged.
- Never deploy changes until staging tests and live health gates pass.
- No new migration/secret/dependency. Index improvements can be considered
  separately after PostgreSQL EXPLAIN evidence.

## Required preservation
- V3.1 unrelated listing baseline continues.
- V3.2 failed pilot is not automatically retried or expanded.
- V4 tracking + print compositing PoC remains separately gated 4/4.
- Flat-ink print position: 150 mm below the lower collar rib on fabric;
  retain SHA fidelity, print QA and motion QA.
