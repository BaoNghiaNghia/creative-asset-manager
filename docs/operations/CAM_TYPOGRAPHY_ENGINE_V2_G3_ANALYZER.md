# G3 Typography Visual Analyzer — implementation and safe activation

**Status:** Development-only, no production deployment or provider call performed. This is not the completed G3 gate.

## Purpose and scope

- Input: one reference explicitly uploaded/registered in the tenant's Typography Ideas Library, with verified source rights, source hash and immutable analyzer version.
- Output: a validated `typography-dna/v1` JSON structure containing **normalized typography region**, general font mood, relative layout graph, overlap rules and confidence. Exact original lettering, glyph outlines, cap shape, fabric and background are not included in saved DNA.
- Existing job infrastructure: migration `0189`, `TypographyAnalysisQueue` with tenant-isolated, versioned idempotency, up to 2 concurrent leases, heartbeat/fencing and a maximum of three bounded attempts. **Migration is not yet applied to production.**
- New development-only adapter: `typography_visual_analyzer.TypographyVisualAnalysisRunner`; **one explicit `tick` only**, not registered with CAM worker, systemd, API or schedule. The adapter can consume the existing `AiMetadataAnalysisInput` contract implemented by CAM's Gemini provider, but no model/credential is provisioned or called by this change.

## Gates and budget

1. `TYPOGRAPHY_V2_ENABLED` must be true and `TYPOGRAPHY_V2_CANARY_TENANT_IDS` must explicitly contain this tenant, verified from trusted server settings both before and after the provider call. Default is OFF.
2. The durable job must exist and match the active tenant, reference, SHA256 and analyzer version; source rights must be `owned`, `licensed` or `permitted_reference`. Archived or rejected references cannot be analyzed.
3. Managed Storage is opened by `tenant_id`, `asset_id` and private `remote_file_id`; **never** fetch arbitrary user-supplied URLs. Read at most 12 MiB, verify original content SHA256 and decode JPEG/PNG/WebP only, max 20 million pixels. Downsample to 1280px (maximum dimension), retaining transparency for PNG where applicable.
4. Make **at most one vision request per claimed attempt**. Default input/output timeouts: 30/90 seconds. Renew the 120-second lease every 35 seconds in short independent DB transactions. If the lease/rights/flag change, cancel result persistence.
5. Require strict typed output. AI cannot set trusted hash/version/tenant. Reject malformed relative graph/region and confidence below 0.65 as `needs_review`; retry provider/transient failures only within the existing max-three budget.
6. Save a versioned, tenant-scoped DNA record and mark complete **atomically** with a current lease token. Do not store the provider's raw response, image bytes, extracted text, credentials, private image URLs or unrestricted metadata in logs.
7. Source imagery is *not cropped before the vision call* in the initial single-pass cost-controlled pilot. The vision result identifies the typography-only normalized region. ROI-based local crop and optional 2nd-pass refinement must be evaluated on authorized pilot images and gated by cost/quality evidence rather than activated blindly.

## Development verification

- `python3 -m py_compile` both new modules and `git diff --check`.
- CI: `apps/api/tests/modules/realistic_review_ugc/test_typography_visual_analyzer.py`, existing contract/queue tests, PostgreSQL migration tests, security and browser matrix.
- Test with **synthetic generated image bytes and fake provider/storage only**. Verify disabled/tenant mismatch never bills, denied rights, hash mismatch, invalid graph/text injection, low confidence, transient retry, lease fencing and flag revocation.
- Before real pilot, secure eight licensed/owned images, capture per-image consent/provenance and 8 sanitized DNA results. Measure confidence, one-call/token usage, P50/P95 latency, cache rate, failed/review rate, and whether typography-only layout survived product/background distractions.

## Next steps (no auto activation)

1. Merge adapter only after required CI is green.
2. Add a permissioned **explicit** enqueue/status API, backed by migration `0189` and read/manage RBAC; keep routes flag-gated and absent from frontend until backend rollout.
3. Integrate CAM's existing AI credential/provider pool in an **opt-in pilot worker**, with a global budget, per-tenant concurrency, and no implicit scheduling.
4. Run eight rights-verified pilot references, manual visual region review, and a small real-image A/B versus un-cropped single-pass.
5. Resolve G0.1: backend `previous` symlink/restore path and VPS capacity. Validate PostgreSQL schema rollback before any production deployment.
6. G3 exit criteria remain **NOT MET** until the actual visual samples, cost/latency evidence, manual override and dedup QA exist. Then proceed to G4 Reference Matcher/Style Lab.
