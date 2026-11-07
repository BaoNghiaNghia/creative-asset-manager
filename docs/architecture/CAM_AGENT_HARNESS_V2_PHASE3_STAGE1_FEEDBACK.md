# CAM Agent Harness V2 — Phase 3A Stage 1 Feedback Candidate Evidence

Date: 2026-11-07 | Review and rollout: backend-only

## Goal and existing sources

Safely materialize operator-controlled, candidate-only Experience snapshots from existing Stage 1 Pick(v) / Reject(x) outputs. CAM already stores server-mediated feedback in rrugc_image_output_versions.font_info_json.creative_feedback with an authenticated actor and timestamp; no new vote table, provider, observer job, or change to the existing Stage 1 workflow is needed.

## Source validation

- The output version must be stage1, current, tenant-owned, with a matching tenant-owned parent rrugc_keyword_image_job and persisted remote asset.
- Output technical_qa_passed must be true and content_sha256 must be a valid 64-char lowercase SHA-256. The review must be a currently persisted picked or rejected enum with a nonempty recorded actor and timezone-aware timestamp.
- Rejected reasons must be a fixed approved reason other than unspecified; picked patterns must be a recognized 1/4/4 concept mode and variant. No free-text title, prompts, image pixels, external URLs, font name or arbitrary user instructions enter the Experience Store.
- Keywords are classified by the already-established semantic_cohort function in keyword_cohort_learning. Queries are limited to last 180 days and at most 800 recent current votes in that tenant. Per signal: max 4 outputs per keyword, max 40 total, deduped by asset SHA-256 and output ID.
- A candidate requires at least 10 distinct output versions and at least 3 distinct exact keyword phrases. Only up to 5 rule snapshots per request. A human vote is preference evidence, NOT a QA or statistical improvement outcome.

## API contract

POST /api/v1/admin/agent-harness/{tenant_id}/feedback/stage1/candidates

Body fields: cohort (music|western|pets|healthcare|coffee|family|outdoors), agent_version (v1 etc.), dry_run (true default). Extra fields including source IDs and auto_promote are rejected.

- Authenticated active tenant scope and BOTH ai_operations.read + ai_provider.configure permissions are mandatory.
- AGENT_HARNESS_READ_API_ENABLED controls all previews. A persistence run (dry_run=false) additionally requires AGENT_HARNESS_MANAGEMENT_API_ENABLED and AGENT_HARNESS_FEEDBACK_SYNC_ENABLED, both default false. The existing AI_EMERGENCY_STOP_ENABLED rejects all requests.
- For a write, the specified tenant must have a **registered disabled** gatorhats.feedback-intelligence agent version with feedback_analyze capability and feedback.read-only tool grant. It is registered separately via the existing audited Agent Catalog API. No automatic privileged seed.
- Snapshots have source_event_ids as persisted output identifiers, evidence_refs as artwork SHA-256 digests, confidence fixed at 0.25, NO verification_refs, status candidate, and evaluation flags independent_qa_passed=false/holdout_verified=false/promotion_allowed=false.
- Created candidates and sanitized actor audit records commit atomically using existing cam_agent_experiences and auth_audit_events; no migration. Exact retries are idempotent. A changed group of current votes forms a new immutable candidate; previous candidates remain inert. Undo never becomes a new candidate.
- API response exposes only fixed rule keys, aggregate counts and candidate/holdout flags; never raw keyword text, user notes, prompts or file identifiers.

## Important learning boundary

This release does NOT add a background hook to Pick/Reject, does NOT automatically promote evidence, change Creative Brief or generation prompts, update model weights, run Codex, queue jobs or modify any existing output. Retained snapshots are not directly reusable if the underlying votes are later changed; before a future promotion mechanism can exist, re-check all current votes, source/asset lineage, independent QA, outcome improvement and an isolated holdout.

## Deployment and rollback

1. Run focused tests (tenant, permission, QA, dedup, idempotence, undo, atomic audit) and existing Agent Harness, Alembic 0203, Etsy migration, startup and authorization regressions.
2. Compare GitHub CI PostgreSQL 12/16 and durable pipeline with parent main. Do not hide unrelated historical UI/fixture failures.
3. Merge to main and deploy via pinned cam-rebuild-backend.sh --no-migrate. Verify /version /ready workers and current+previous.
4. Keep flags disabled in production by default. Enable read-only preview as a separate admin canary; only enable sync after validating that the tenant explicitly registered the dormant feedback agent. Never activate automatic execution.
5. Rollback by disabling flags or selecting previous backend; no database downgrade needed.

## Phase 3B

Add authenticated feedback-event outbox and throttled/leased periodic incremental capture, stale candidate invalidation, independent QA holdout and manual promotion, with no impact on vote latency or running image jobs.
