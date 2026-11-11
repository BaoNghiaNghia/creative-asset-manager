# CAM Agent Harness V2 — Phase 4C.1 historical heartbeat audit + safe capacity preview

Date: 2026-11-11. Backend-only metadata read paths. **No new queue,
worker, reservation, migration, provider call, default-on dispatch or AI
policy change.**

## Why

Production Phase 4B.2C enabled an HMAC-signed paired Windows Image Agent
heartbeat, but the daily Image Agent quota was already 10/10. At release
there were **zero** processing `etsy_design_extract` jobs and **zero**
signed heartbeat rows. A genuine end-to-end canary is not yet evidenced.

A normal Image Agent `/result` sets the existing `ProcessingJob` status
to `completed` and clears `lease_expires_at`, so the previous live-only
admin proof endpoint could no longer verify a short-lived heartbeat after
the job finished. This phase makes signed job evidence **retrospectively
auditable**, while firmly distinguishing it from live capacity.

## Read-only changes

### Historical signature proof

Existing admin endpoint:

`GET /api/v1/admin/agent-harness/{tenant_id}/jobs/{job_id}/external-heartbeat`

Adds `historical_attestation` without changing the live
`worker_heartbeat_authenticated` interpretation. Historical proof is
valid only for a `completed` `etsy_design_extract` job with an actual
accepted result receipt, original claim/attempt still matching the signed
record, device registration still mapped to the tenant and job owner,
and a valid server HMAC. The issued time must fall between claimed_at
and completed_at, and its 5–45-second original lifetime must be valid.
The **present** job lease is not necessary: completion intentionally
removed it. A revoked device does not erase a previously valid signed
event, but it can never supply current capacity.

A historical signature is evidence only that the paired credential
reported a specific *processing attempt* before completion. It does
**not** prove provider success, image correctness, manual QA, device
availability now, print rights or executor slot ownership. This endpoint
never returns the signature, nonce, tenant secrets, agent credential,
worker identity, prompts or source URL.

### Executor capacity/evidence preview

New admin endpoint:

`GET /api/v1/admin/agent-harness/{tenant_id}/external-executors/etsy-image/capacity-preview`

Requires the existing Agent Harness READ feature flag, both
`ai_operations.read` + `ai_provider.configure` permissions and matching
tenant scope. Queries **only the existing CAM database**:

- Up to 20 paired Image Agent records, not all Scout agents.
- Bounded `processing` jobs (up to 160), and grouped `pending/retry`
  counts for `etsy_design_extract`.
- Valid active lease and HMAC attestation for each job; detection of
  stale leases, revoked agents and more than one processing claim per
  paired device.
- Only the 32 most recent completed jobs for retrospective HMAC
  evidence verification. The response labels this as a **bounded sample**
  and exposes at most five verified job IDs for authenticated operator
  follow-up, not an all-time count.

The endpoint is **observational**, never schedules/claims/renews a job.
An idle device has **zero verified free slots**: a job-bound heartbeat
cannot prove it is currently online or has a free browser session.
Even a current signed heartbeat represents a busy executor with
`verified_free_slots=0`, not a reservable available executor.
All responses explicitly set `dispatch_allowed=false`,
`execution_enabled=false`, `reservation_created=false` and
`automatic_promotion_enabled=false`.

Oversized device or job sets fail closed with an explicit limit state;
never silently truncate capacity and report success. Missing/revoked
devices, unrecognized owner IDs, unsigned/stale/expired receipts,
conflicting claims and global emergency stop never provide capacity.

## Deployment and rollback

1. Run focused tests and repository CI (API + PostgreSQL 12/16 + Browser
   QA). Merge into `main` after all jobs PASS.
2. Deploy backend-only using
   `scripts/cam-rebuild-backend.sh --no-migrate --keep-releases 2`.
3. Verify 9 CAM systemd services, readiness endpoints, Alembic head
   `0207_cam_agent_trial_artwork_snapshots`, and distinct prior release.
4. Existing server-only HMAC key remains in the root-owned out-of-Git
   service env. Do not rotate/disclose it for this phase. Windows
   extension is not restarted.
5. Roll back code to the retained previous release if a regression
   occurs; **never downgrade 0207**.

Real Phase 4B.2C heartbeat attestation is still dependent on the next
legitimate job claim within the existing cap, not a fabricated job.
A new **independently verified idle-executor handshake**, token-fenced
slot reservation and controlled dispatch will be a distinct Phase 4C.2
behind a disabled-by-default execution flag.

Production off-host backup recovery remains unverified under the earlier
operator exception; do not infer restore assurance from clean CI or
read-only status checks.
