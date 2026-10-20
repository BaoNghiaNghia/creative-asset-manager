# Branding DNA queue health (2026-10-19)

## Scope
The CAM operator endpoint `GET /api/v1/branding-dna/shops/scan-readiness`
now provides per-shop `queue` and aggregated `queue_totals` counters. This
is **read-only** and scoped to the caller's active tenant; it requires
`ai_operations.read`. The reply is `Cache-Control: no-store`.

Counters: `staged`, `pending`, `leased`, `retry`, `blocked`,
`completed`, `cancelled`, `expired_leases`, and `due_retries`.
`expired_leases` counts leased rows with missing or elapsed lease timestamps.
`due_retries` counts retry rows whose retry time has arrived (or is absent)
and whose attempt limit has not been exhausted. Both are diagnostic counters:
**reading them does not requeue, reclaim, renew, or dispatch any job**.
The internal lease recovery routine also recognizes orphaned `leased` rows
with null `lease_until`, reclaiming them through the same bounded retry
or terminal block rules. It only operates for the supplied tenant.

The status breakdown is an aggregate; a `leased` row is **not proof** of a
live browser session, and an `expired_leases` count is not an instruction
to kill a process. The `staged_tasks` field remains the original strictly
staged count for compatibility.

**Windows Scout v1 contract regression (2026-10-20):** The existing
`apps/scout-manager-desktop/src-tauri/src/lib.rs` uses
`#[serde(deny_unknown_fields)]` for `CamPreviewResponse` and
`CamPreviewShop`. Adding `queue_totals`, per-shop `queue`, or
`review_decision` to `GET .../scout-agents/{agent_id}/dispatch-preview`
made live HTTP 200 replies fail JSON parsing in the Windows tool with
`CAM returned an invalid dispatch preview`. Keep this paired endpoint's
**exact version-1 response shape**; it contains only disabled dispatch
status, blocked-shop reasons and staged counts. The backend tests explicitly
assert the exact top-level and item keys to prevent this regression.

The operator-only `GET /api/v1/branding-dna/shops/scan-readiness`
provides extended queue counters. The Shop Library shows them and refreshes
while the page is visible approximately every 60 seconds.
`scan_enabled` remains false, `ready_count` stays zero, and neither API
can grant a marketplace browser job or establish source authorization.

## Live worker presence diagnostics

Read-only, tenant-isolated operator endpoint:
`GET /api/v1/branding-dna/workers/dispatch-health` (permission
`ai_operations.read`). Reports dedicated worker counts by online-idle,
online-paused, online-unhealthy, offline and disabled; paired Scout counts by
connected-waiting/offline. It includes the permanent
`scan_pipeline_not_deployed` reason until permitted execution adapters and
independent source authorization are actually in place. No IDs, tokens,
source URLs, jobs, lease payloads or evidence references are returned.
Agent presence is **not** marketplace authorization.

## Safety and continuation
- A shop's `access_mode`, self-declaration, or documentary review does not
  independently establish Amazon/Etsy access authorization.
- The internal lease selector now filters active, owned/authorized shops and
  rechecks those flags for lease callbacks. Additionally, it will not grant a
  new lease to an enrolled agent unless the worker is enabled, idle, and has
  a fresh heartbeat (within three minutes, not future-dated). Expired leases
  are still recovered before the presence decision, so a disconnected worker
  cannot block recovery of an abandoned lease. The internal claim path locks
  the worker row and checks for any existing leased task in that tenant:
  **at most one active task per worker**. Concurrent polls serialize on the
  same PostgreSQL worker row; a separate worker may claim another task.
  No claim bypasses the bounded retry delay. It must still stay disconnected
  from public worker lease endpoints pending independent marketplace policy
  review and an auditable, verified source adapter.
- An internal `complete_verified_task` primitive is available for later
  adapter integration. It requires a current fenced lease, a positive exact
  checkpoint, the independently verified listing snapshot state, and its
  immutable SHA-256 digest. Only the same lease can replay the terminal ACK;
  disabled workers and revoked shops cannot acknowledge completion. Its
  retained lease identity is an audit/ACK receipt, not an active lease. No
  public completion route, verified source adapter, or marketplace browser
  dispatcher is enabled.
- Before enabling dispatch, implement explicit reviewed authorization with
  source-specific limits and expiry/revocation, an independent source
  verification writer, the executable worker lease contract, reclaim/retry
  supervision, and concurrency/restart/security acceptance tests. Never enable
  it by only changing a UI label or setting `scan_enabled=true`.
- For troubleshooting, compare these queue counters over time with worker
  presence and queue events; preserve evidence and do not delete or reset
  outstanding tasks as a shortcut.

## Local verification
```bash
PYTHONPATH=.worktrees/branding-dna-dispatch/apps/api apps/api/.venv/bin/python -m pytest -q .worktrees/branding-dna-dispatch/apps/api/tests/modules/branding_dna
npm --prefix .worktrees/branding-dna-dispatch/apps/client run typecheck
npm --prefix .worktrees/branding-dna-dispatch/apps/client run build
node --test .worktrees/branding-dna-dispatch/apps/branding-dna-worker/tests/agent_cam.test.mjs
git -C .worktrees/branding-dna-dispatch diff --check
```

A green local test/build only validates this checkout; production still
requires merge, immutable release, authenticated smoke, monitoring, and
rollback verification. Deployment status must be reported separately.
