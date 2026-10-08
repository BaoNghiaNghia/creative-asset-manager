# RRUGC Stage 0–5 hardening — 2026-10-08

## Scope and invariants
Six independent visible stages are unchanged. Tenant/user authorization remains server-side. Old generated outputs, keyword data, source revisions and reference labels are never deleted by these patches. No schema migration or new runtime dependency is required.

## Implemented
- Stage 1: bundled keyword-only gatorhats-keyword-embroidery skill, keyword_artwork workflow accepts zero source images. Queue rejects skills requiring source references. Existing pinned jobs retain their original skill/outputs; they do not silently migrate.
- Stage 2: read-only readiness checks flags, 13 stock photos, installed scale skill and executable Codex CLI. Does not make a real paid-provider call or guarantee auth/quota.
- Stage 4: generation submissions are sequential 3-reference batches; each created job appears immediately in UI, and user cancellation stops subsequent submissions (with best-effort cancellation for an in-flight creation). Cancellation supports explicit job IDs and checks tenant/user/group ownership. Legacy caller without IDs cancels only recent jobs. Expired jobs do not veto fresh ones.
- Stage 5: Analyze all queries only missing/failed/stale analyses in 500-item pages, and frontend queues subsequent pages. Gallery still has an independent 5,000-image limit pending further pagination. Current tests: 308 RRUGC backend, 96 Scout, 544 frontend; multi-viewport fixture QA passed with zero recorded errors. Baseline pixel-regression approval is still separate.
- Stage 5: conservative review-ready thresholds and visible AI-generated concept disclosure.

## Release and verification
- Use guarded backend/frontend release scripts with provenance checks; verify /live, /ready and public UI smoke. An authenticated Stage 1–5 production flow requires a separate real-run QA pass.
- No migration. No new secrets. Bundled Skill must be synced to Codex home by normal deployment.
- Roll back via source commit revert. Existing job state and outputs must remain intact.
- Verify real Stage 1 keyword rendering and 13 Stage 2 color output images with human embroidery comparison before claiming production-quality fidelity.

## Outstanding follow-ups
1. Stage 0 separate keyword-Pin source history and reconcile short-word acceptance before rewriting stored phrases; two-word minimum is retained.
2. Stage 3 benchmark local pre-Gemini classifier against known approved/rejected images and monitor false negatives; deferred Gemini protection remains.
3. Stage 2 historical revision UI and visual artwork QA, retry without overwrite.
4. Stage 5 DB-paginate review gallery beyond 5,000 images and adaptive idle polling.
5. Verify Windows Scout agent versions, throughput and backlog through authenticated production diagnostics.
