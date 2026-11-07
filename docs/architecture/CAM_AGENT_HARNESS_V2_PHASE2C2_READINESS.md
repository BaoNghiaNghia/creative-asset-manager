# CAM Agent Harness V2 — Phase 2C.2 Executor Readiness (2026-11-07)

## Goal

Provide real, read-only preflight signals for the current RRUGC Codex CLI account pool, and explicitly expose Browser Extension as unverified. Do NOT create a second queue, do not launch generation, and do not mark dormant agent definitions as enabled.

## Existing source integration

- Reuse RrugcCodexAccountConfigModel tenant profile configuration, codex_account_manager.auth_status(), and active_slots() FLOCK inspection. Only registered, enabled, stage-eligible RRUGC accounts are probed; no ensure_accounts() calls, login mutation, credential exposure, or new storage.
- Browser Extension readiness remains BLOCKED regardless of BROWSER_EXTENSION_READY until an authenticated heartbeat, authorized provider output, durable CAM-to-Windows acknowledgment, and verified result lineage exist. The prototype is NOT a production fallback.
- The probe does NOT reserve a slot, does NOT call an AI model, and does NOT guarantee future capacity. The upper bound is min(agent.max_concurrent, observed free Codex profile slots). The existing account pool remains the authoritative lease authority.
- The supported RRUGC stages are stage1 (generate_text, image_edit), stage2 (image_edit), stage4 (image_edit). Code review and Scout discovery are not automatically mapped to RRUGC worker capabilities.

## API

POST /api/v1/admin/agent-harness/{tenant_id}/agents/{agent_id}/{version}/route-preview

- Requires feature flag AGENT_HARNESS_READ_API_ENABLED=true plus BOTH ai_operations.read and ai_provider.configure, with authenticated tenant scope.
- Strict JSON body only: stage (stage1|stage2|stage4), required_capabilities (generate_text/image_edit), and required_tools (existing limited tool allowlist). Unknown fields/commands and duplicate or unsupported capabilities rejected.
- Result always has dispatch_allowed=false and execution_enabled=false. The candidate_executor and advisory_agent_slot_limit are diagnostic hints only, never job ownership or permission to execute.
- Agent version must exist in the request tenant. Tool/capability mismatch and emergency stop are evaluated BEFORE invoking the CLI login-status probe; no cross-tenant health probes.
- Current Agent Catalog registrations are always persisted with enabled=false. No API in this phase can turn them on. AGENT_HARNESS_EXECUTION_ENABLED stays false.
- No migration, new environment variables, user credential access, provider charges, background jobs, or modifications to GatorHats/Scout queue.

## Validation

- Unit tests for login connected/auth_required/CLI missing, account pool active locks, tenant isolation, slot limits, emergency stop, permission denials, malformed requests and Browser Extension fail-closed state.
- API smoke, configuration, Alembic 0203/Etsy Spy, startup and existing Agent Harness tests.
- GitHub PostgreSQL 12/16 CI and durable pipeline compared to prior main; unrelated existing test failures documented, not suppressed.

## Production rollout

Deploy from main pinned commit using backend release script --no-migrate and keep previous release. Verify /version and /ready, all workers, unauthenticated 401 and rollback. Feature API remains off by default; only a separately authorized canary can enable the READ flag. Never enable AGENT_HARNESS_EXECUTION_ENABLED from this release.

## Next phase

Trusted worker-specific heartbeat and capacity reservations tied to ProcessingJob leases; browser extension real provider acceptance and QA; verified Pick/Reject evidence ingestion into candidate-only Experience Store. No automatic self-improvement promotion without independent evidence.
