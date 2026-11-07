# CAM Agent Harness V2 — Phase 2C.1: audited tenant catalog

## Boundary

Metadata-only Agent registration. No job, worker, model, browser, credential, code change or Experience promotion is executed.
Preserve existing Stage 0–5 and Scout queues. Reuse existing cam_agent_definitions and auth_audit_events; no migration.

## API

- GET /api/v1/admin/agent-harness/{tenant_id}/overview requires AGENT_HARNESS_READ_API_ENABLED and ai_operations.read.
- POST /api/v1/admin/agent-harness/{tenant_id}/agents requires BOTH AGENT_HARNESS_READ_API_ENABLED and AGENT_HARNESS_MANAGEMENT_API_ENABLED, plus ai_provider.configure.
- Both endpoints require authenticated principal and exact tenant scope (platform admins follow existing CAM scope policy).
- POST body is strictly typed: agent_id, version, capabilities, allowed_tools, preferred_executor, fallback_executors, max_concurrent, max_retries, reason.
- Executors are metadata identifiers codex and browser. These are not live executor connections.
- Capability/tool lists have conservative server-owned allowlists; unknown tools/commands, enabled=true, URLs, credentials and arbitrary fields are rejected.
- New record returns 201; exact retry returns 200, conflicting same-version policy 409; invalid input 422; unauthorized 401/403; flag disabled 404.
- Persisted AgentDefinitionModel.enabled ALWAYS false. Neither flag turns on existing workers or Experience promotion.
- New registration and AuthAuditEventModel are committed in the SAME DB transaction with user ID, tenant, version and reason; audit failure rolls back registration.

## Deployment gates

1. Unit/HTTP negative tests, startup/configuration/migration/auth regression and GitHub CI comparison against existing baseline.
2. Deploy only from main pinned release via cam-rebuild-backend.sh --commit SHA --no-migrate.
3. Verify /version, /ready, health of all backend workers and current + previous retention.
4. Separate operator opt-in: enable READ first, then MANAGEMENT under protected production env; verify authenticated tenant-only access. Execution stays off.
5. Rollback by turning MANAGEMENT flag false or switching to prior backend release. No DB downgrade needed.

## Deferred

Source-event authentic Experience promotion; Codex CLI/browser-extension adapter health/permissions; bounded runtime queue routing and canary.
