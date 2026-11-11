# CAM Agent Harness V2 — Phase 4A route-preflight hardening

Date: 2026-11-11. Scope: read-only advisory routing only, no job dispatch or
provider call beyond the existing Codex login-status probe.

## Problem

The previous route preview probed Codex profile login even for a Stage 2/4
request that the stage does not support. It also selected an executor solely
from a positive slot count plus capability names, without requiring a
positively verified probe status or internally coherent count snapshot.
An unverified Browser Extension heartbeat or stale/malformed probe response
must never become a routing recommendation.

## Implementation

- Reuse the existing, tenant- and RBAC-scoped agent version and stage
  capability contracts. Check stage capability support before CLI login probes.
- Do not invoke Codex probing for browser-only agent definitions, emergency
  stop, unpermitted capabilities/tools, or unsupported stage combinations.
- Require status exactly `observed_capacity` with integer non-negative
  configured/busy/available counts, available > 0, and
  configured = busy + available before recommending an executor.
- Preserve preferred-first and explicitly declared fallback order. Add a
  `candidate_priority` field (`preferred`, `fallback` or null) to explain
  the advisory choice without exposing credentials, profile paths, prompts,
  keyword identities or worker lock IDs.
- Browser Extension remains unverified by the actual production probe and
  cannot be recommended; a synthetic verified browser snapshot is tested
  solely to validate abstract fallback ordering.
- The result continues to return `dispatch_allowed: false`,
  `execution_enabled: false` and `slot_counts_are_advisory: true`.
  No queue lease is acquired, no paid AI calls, no new worker spawned.

## Validation and operations

Regression tests cover unsupported-stage probe skipping, browser-only
definitions, unverified positive slot counts, contradictory counters,
priority of eligible preferred/fallback executor, and continued dispatch
prohibition. Verify with the existing API unit CI plus PostgreSQL 12/16
matrix. No schema migration, new dependency, secret or feature flag is
required. The route preview still requires the existing
`AGENT_HARNESS_READ_API_ENABLED` flag and both read/configure permissions.

Deploy backend from merged `main` using the reviewed
`cam-rebuild-backend.sh --no-migrate --keep-releases 2` workflow.
Confirm current and previous commit, API ready/live/version and all worker
health checks. On regression use code-only rollback to the retained previous
release; never downgrade 0207 or delete retained QA artwork.
The independently restorable production backup remains unverified under the
owner's 2026-11-11 one-time off-host rehearsal exception; this change does
not claim any backup recovery assurance.

## Subsequent gate

Actual multi-agent dispatch needs authenticated executor heartbeat and
lease-bound capacity reservation tied to ProcessingJob, scoped tool
authorization, bounded costs and retry policy, independent artifact QA and
trusted provider cost receipts. Do not infer objective quality from Pick/Reject
or enable automatic agent policy promotion from this advisory preview.
