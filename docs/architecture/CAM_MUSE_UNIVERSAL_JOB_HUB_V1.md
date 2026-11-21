# CAM Universal Job Hub V1 — Architecture Proposal

Status: DESIGN ONLY, NOT DEPLOYED (2026-11-21).
Goal: many unrelated job types, one durable queue, Muse execution and result
delivery into ChatGPT without OpenAI API calls for background processing.

## Verified as-is
Production CAM Muse Bridge is on ec3041bb7. cam_muse_commands from migration
0214 supports only create_video and review_artwork, requires an Etsy listing
key and artwork_sha256, and each command points to cam_muse_jobs. A standalone
Pinterest research job does not fit. Do not keep adding hardcoded action check
constraints / routes for each new task type. Uncommitted Pinterest-specific
experiments are not deployed.

## Proposed system

ChatGPT / CAM UI / external clients
   -> private MCP tools or operator API
   -> CAM UNIVERSAL JOB HUB (Postgres + atomic outbox/event log)
   -> capability dispatcher
       -> Muse custom connector: web research, image/video/creative workflows
       -> CodeLocal: scoped code changes, testing, local file operations
       -> deterministic CAM worker: syncing, checksums, downloads, formatting
   -> validated result artifacts (Google Drive/R2 pointers)
   -> CAM job inbox + notifications
       -> private ChatGPT MCP UI live card (no model call per UI refresh)
       -> optional MCP Events to subscribed eligible ChatGPT Work chat
       -> Muse/Telegram notifications on close

### Generic job envelope
{
  "job_type": "research.pinterest_poses",
  "schema_version": 1,
  "request_id": "chatgpt-20261121-pinterest-cc-20",
  "payload": {
    "product": "Comfort Colors C1717",
    "count": 20,
    "source": "pinterest",
    "criteria": ["natural arms", "visible front print",
                 "varied framing and genders", "no hair touching"]
  },
  "constraints": {
    "allowed_executors": ["muse"],
    "openai_api_allowed": false,
    "distinct_pin_ids_required": true,
    "external_reference_only": true
  }
}

### Relational tables, all tenant scoped
- cam_jobs: job_id UUID, tenant_id, type, schema_version, unique request_id,
  parent_id nullable, related_entity_id nullable, input JSONB, constraints JSONB,
  state, priority, result_summary JSONB, created_at, finished_at.
- cam_job_runs: run_id, job_id, executor, attempt, lease_digest,
  lease_expires_at, last_heartbeat, status, failure_code.
- cam_job_events: event_id, job_id, run_id, monotonically increasing seq,
  type, payload JSONB, created_at; append-only and durable.
- cam_job_artifacts: artifact_id, job_id, kind, provider, storage object key,
  preview/public URL if allowed, MIME, bytes, sha256, attribution, metadata.
- cam_job_subscriptions: tenant/user, event name, approved destination ref,
  filtered jobs, expiry and last delivered event_id.

Job kinds are registered in a versioned type registry (input schema,
output schema, worker capability, safeguards, budget, result renderer).
A new type such as research.pinterest_poses, report.market_trends,
image.artwork_qa, video.ugc_generate or code.bugfix does NOT require schema
DDL changes or a brand-new endpoint. Never allow arbitrary shell execution.

### Stable REST APIs
Operator/MCP plugin: POST /jobs, GET /jobs/{id}, GET /jobs/{id}/result,
GET /jobs, POST /jobs/{id}/cancel, GET /inbox.
Muse and other workers (scoped): GET /worker/jobs?capability=..., POST
/worker/jobs/{id}/claim, /heartbeat, /event, /result, /complete and /fail.
Only the CAM server decides final state after it validates all artifacts,
source fidelity, job-type output schema, policy, and approval gates.

Job state: queued -> claimed -> running -> result_submitted -> validating
-> succeeded, with waiting_approval, failed_retryable, failed_final and
cancelled branches. PostgreSQL SKIP LOCKED + 30-minute leases, heartbeat,
bounded 5 retries, exponential backoff, idempotency keys. At-least-once
dispatch with deduped effects; do not claim exactly-once processing.

### Returning results to ChatGPT
Primary zero-background-OpenAI-call display: private ChatGPT MCP plugin
opens a live Job Inbox widget directly inside ChatGPT. Its iframe is
CSP-limited to the CAM domain; while mounted it may refresh directly
from the CAM API, without a ChatGPT model call per refresh. This is an
updating card, NOT a brand-new assistant message. Existing chat model
turns still consume the user's ChatGPT plan allowance.

Optional full conversational push: MCP Events (events/list,
events/subscribe, events/unsubscribe, signed Standard Webhooks delivery,
job.completed and job.failed) in an eligible subscribed ChatGPT WORK
chat. This triggers model processing and may consume ChatGPT usage.
It is NOT guaranteed in the existing ordinary project conversation.
For strictly no ChatGPT background use, notify via Muse/Telegram while
retaining results in the live Job Inbox widget / CAM.

### Security
Separate least-privilege user OAuth/MCP access, CAM operator token and
Muse worker token. No raw credential in app UI, result manifests or logs.
Tenant isolation everywhere; validate task type and JSON schema; pin
authorized artifact storage keys; no unbounded filesystem, browser, SSH
or terminal access. External Pinterest links are reference material only,
with provenance and copyright restrictions. No extra database instance,
disk backups, PNG duplication or OpenAI API calls for job execution.

### Rollout without disrupting legacy CAM
1. New additive 0216 migration (generic jobs/events/runs/artifacts), NOT
   a mutation of 0214 and NOT any Pinterest-only 0215 experiment.
2. Generic APIs, integration tests for empty input, idempotency, token
   isolation, queue races, lease expiry, retries, user cancellation,
   failure receipts and result schema.
3. Add Muse generic worker adapter while retaining current Muse video
   connector and existing CAM source/QA gates.
4. Canary the requested 20 Pinterest pose references; validate 20 distinct
   real pins, pinimg URLs, descriptions, and provenance.
5. Private ChatGPT MCP tools (submit_job, get_job, list_jobs, cancel_job)
   and live Job Inbox widget; verify no model calls during refresh.
6. Optional ChatGPT Work MCP Events when enabled and user accepts the
   extra model usage; delivery never changes job status.
7. Migrate existing garment QA/video tasks via adapters, one by one.

### Acceptance tests
- A non-Etsy research job can be submitted with NO listingId/artworkSHA.
- Muse claims it once and returns 20 verified distinct Pinterest pins.
- Results persist across reboot and chat closure, with signed URL policies.
- In-chat widget shows finished result without re-invoking the model.
- OpenAI API outbound call count remains zero for background job execution.
- Invalid origins, duplicate events, stale leases, unapproved high-risk
  actions and cross-tenant data access all fail closed.
- Existing 353 Picked/video queue and current production are unaffected.
