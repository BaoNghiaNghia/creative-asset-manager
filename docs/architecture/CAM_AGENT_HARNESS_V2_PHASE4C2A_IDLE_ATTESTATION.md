# CAM Agent Harness V2 — Phase 4C.2A: signed idle device report (stateless canary)

Date: 2026-11-11. **No schema change and no real multi-agent dispatch.**

## Motivation

The Phase 4B.2C job-bound heartbeat correctly requires a currently owned
`ProcessingJob`, so it goes silent when the Image Agent is idle. The Phase
4C.1 capacity preview intentionally reports zero verified *free* slots even
when no active job exists. A device's `last_seen_at` or a completed job
receipt cannot be used as proof of a free browser executor.

Phase 4C.2A adds **only** a short-lived, cryptographically authenticated
statement that a paired Image Agent sent an *idle self-report* while the CAM
database did not have an unresolved `etsy_design_extract` processing claim
under that specific device's owner ID. The browser's actual idle state,
available slots and provider readiness are **not independently verified**.

## Protocol: device

Default-OFF server gate (both required):
- `CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_ENABLED=1` (existing)
- `CAM_AGENT_HARNESS_IDLE_ATTESTATION_ENABLED=1` (new)

Signing key: reuse the independently provisioned, server-only
`CAM_AGENT_HARNESS_EXTERNAL_HEARTBEAT_SIGNING_KEY` (256-bit secret) but
use a distinct HMAC domain prefix, `cam-idle-attestation-v1:`.
No keys or agent bearer tokens are copied to Windows, output, source control,
or diagnostics.

`POST /api/v1/etsy-spy/image-agents/{agent_id}/harness-idle-attestation`

Device uses the existing paired, active Image Agent bearer role; Scout
credentials cannot call this route. JSON body is exact and bounded to
4 KiB: `{"protocol":"cam-image-idle-report-v1",
"session_id":"<random daemon UUID>","sequence":1,
"journal_empty":true,"device_mode":"orchestrator_v1"}`.
No remote URLs, commands, capacity counts, prompts or file payloads accepted.
Server checks global AI emergency stop, existing extraction/orchestrator flags,
dedicated agent authentication and absence of **any** unresolved
`status=processing` job with `claimed_by=etsy-image:{agent_id}`.
It never creates/claims/extends/completes/releases jobs or changes the
device row. If any old processing claim exists, it returns 409.

On acceptance, server returns a signed 30-second receipt scoped to its own
tenant, agent, client session UUID, strictly increasing *client-provided*
sequence, fixed mode/report, issuance/expiry seconds and random nonce. Sequence
and local journal flags are **self-reported**, not server-durable replay locks.
The server's HMAC signature verifies *who received the report* and the exact
message, but **not** the truth of device/browser state. Short expiry and the
lack of any authorization in the receipt prevent replay from granting slots.

`POST /api/v1/etsy-spy/image-agents/{agent_id}/harness-idle-verify`

The same paired credential may immediately resubmit its receipt for a
cross-request, server-side HMAC/tenant/TTL/status check. Response is
privacy-minimized: a boolean authenticated self-report, zero independent
browser verification, `verified_free_slots=0`, `dispatch_allowed=false`,
`reservation_created=false`, and no signature/nonce/secret/worker ID.

## Protocol: operator

`POST /api/v1/admin/agent-harness/{tenant_id}/external-executors/etsy-image/idle-attestation/verify`

Requires the existing Harness READ flag, both `ai_operations.read` and
`ai_provider.configure`, and tenant ownership. Takes a receipt object.
Performs HMAC + current active device/tenant/owner/processing-claim checks.
Does not reveal credential material, agent ID or command/payloads.

`GET /api/v1/admin/agent-harness/{tenant_id}/external-executors/etsy-image/capacity-preview`
still reports **zero verified free slots** unless a separate future
transactional reservation protocol proves otherwise. A signed idle report is
not an atomic slot reservation. The existing job-bound heartbeat and
historical job audit remain unchanged.

## Windows Image Extension

Under `D:\CAM Image Extension`:
- Existing `harness_heartbeat_enabled=true` is kept.
- New optional `harness_idle_attestation_enabled` is a strict boolean,
  default OFF. Cannot be enabled if job heartbeat is OFF.
- `node scripts/harness-heartbeat-opt-in.mjs --enable-idle` checks the
  paired read-only `probe` says `harness_idle_attestation_ready=true`
  and no active CAM journal before writing only the opt-in field.
- Restart **only** the CAM relay via the existing checked idle-only
  `scripts/restart-harness-relay-when-idle.ps1 -Apply`. Never restart
  Chrome, ChatGPT, the image bridge, or active job processing.
- While local journal is absent, the existing single-flight 15-second
  heartbeat loop now sends an idle report, with bounded backoff on failure
  and a 5-second HTTPS timeout. When a job is present, it sends the
  existing per-job lease heartbeat instead.
- A short-lived signed receipt is stored only under the existing private
  runtime directory `.runtime\extraction\cam\{agent_id}\harness-idle-receipt.json`.
  Redacted status contains no signature, credential or prompt.
- Explicit canary: `node agent/harness-idle-canary.mjs`.
  It independently requests + round-trip verifies one receipt using paired
  HTTPS endpoints, prints only booleans/TTL, never claims a job or calls AI.

## Rollback and limitations

Server: set the new idle flag to OFF in the existing secure systemd
environment and restart API; retain job heartbeat enabled and the signing
key. Client: `node scripts/harness-heartbeat-opt-in.mjs --disable-idle`
then only restart relay if no active job. The old job heartbeat contract is
untouched. No DB migration or downgrade.

**No autonomous agent job dispatch, new queue, real slot reservation,
provider access, policy promotion or automatic QA**. The idle observation
does not prove browser/ChatGPT provider readiness, and the 10/10 daily
Image Agent cap remains unchanged. Live job-bound signed heartbeat remains
unproven until a real authorized job claim occurs.

Independent off-host backup restore remains unverified under the earlier
operator waiver; this change is non-destructive.
