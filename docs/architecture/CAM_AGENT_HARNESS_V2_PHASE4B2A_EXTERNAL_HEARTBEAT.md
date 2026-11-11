# CAM Agent Harness V2 — Phase 4B.2A authenticated Image Agent heartbeat

Date: 2026-11-11. Backend adapter and proof only. **Actual multi-agent dispatch,
queue claims, worker leases, automatic QA, learning and promotion stay disabled.**

## Protocol

The paired Windows Image Agent already authenticates with a dedicated bearer
credential and owns an existing `etsy_design_extract` ProcessingJob lease.
This new control-plane heartbeat reuses that credential and existing per-attempt
HMAC lease token. It never replaces the historical `check-job` / `result`
contract, whose same-device recovery semantics remain unchanged.

- `POST /api/v1/etsy-spy/image-agents/{agent_id}/harness-heartbeat`
- JSON: `{"protocol":"cam-agent-external-hb-v1","job_id":"<UUID>",
  "lease_token":"<64 lowercase hex>","attempt_count":1}`
- Exact validated JSON only, 4 KiB max, image-agent-only scoped bearer.
  Reject unknown/cross-tenant/unpicked jobs, wrong attempt/owner, stale lease,
  cancellation, terminal states and near-expired lease.
- Server holds the authoritative `ProcessingJob` row lock; on accepted
  heartbeat it replaces one bounded field
  `_cam_agent_external_heartbeat_v1` in `payload_json` and updates
  agent last-seen + job updated-at. It does **not** claim/extend/release a
  processing lease, alter attempt count, reserve an executor or create a job.
- Evidence includes tenant/agent/job/attempt/original claim timestamp,
  short-lived 45s-or-less expiry, fresh random nonce, and HMAC-SHA256
  signature with a **separate 256-bit server-only signing key**.
  The device credential hash is never used as the attestation signing key.
  The response exposes only an abbreviated receipt ID, attempt/expiry,
  and explicit `lease_renewed=false`, `dispatch_allowed=false`.
- Admin projection: `GET /api/v1/admin/agent-harness/{tenant}/jobs/{job_id}/external-heartbeat`
  requires existing read feature flag, both `ai_operations.read` and
  `ai_provider.configure` RBAC, and tenant ownership. It verifies HMAC,
  attempt/claim/lease binding, time window, device role and active status;
  never returns nonce, signature, worker ID, credentials or job payload.

## Activation and key lifecycle

Explicit opt-in; BOTH are required in the backend service environment:

- `CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED=1`
- `CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY` = a new,
  randomly generated **64 lowercase hex-character** server-only secret,
  provisioned outside source control and never copied to the extension.

With the flag off, the device route returns 404 and cannot write evidence.
Without a strong key, the device route fails closed with 503. The admin
projection never infers an authenticated heartbeat from mere job.updated_at
or agent.last_seen_at. Signatures are short-lived (max 45s); key rotation
invalidates existing proof, which is acceptable for a transient heartbeat.

Do not turn on the opt-in flag until the Windows Image Extension client
has been updated to send bounded periodic heartbeats without breaking
`check-job`. The existing extension is intentionally unmodified in this
server-side milestone due to concurrent extension development.

## Safety and release

No new migration, queue, managed worker or external provider call. Metadata
writes occur only after both independent credential and lease checks.
Signature alone is not execution authorization; ownership and attempt
must be checked at every accepted result, and the legacy result endpoint
continues to fence by actual ProcessingJob status and lease expiry.

Regression tests must cover disabled/no-key, role/tenant/owner/attempt,
expired/near-expired/cancelled/unpicked jobs, signature tampering,
replay after attempt change, short-lived expiry, revocation, and RBAC.
Use reviewed backend release `--no-migrate --keep-releases 2` after full CI.
Off-host production-backup restore remains **unverified** from prior waiver;
this release does not make destructive changes or claim recovery assurance.

Next Phase 4B.2B: negotiate heartbeat support in Windows Image Extension,
wire a disabled-by-default heartbeat loop to existing one-job journal,
expose operator diagnostics and integrate fail-closed stale-ack fencing
before enabling controlled executor admission.
