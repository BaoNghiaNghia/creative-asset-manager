# CAM Typography Engine V2 — G3 pilot acceptance and evidence

**Status: NOT STARTED.** This document is a checklist only. No private references, source images, tenant IDs, provider credentials, billed requests or user keywords have been copied to the repo. All rollout flags remain OFF.

## 0. Hard preflight: no provider execution until completed

The authorized operator must record evidence privately outside Git for **each** item:

- [ ] Backend `current` and a genuinely usable `previous` release were verified: correct code, Python environment, matching schema compatibility and tested rollback procedure. A symlink to the same release does **not** count.
- [ ] PostgreSQL backup/restore verified and additive migrations `0188` and `0189` applied in safe order in an authorized test environment. No destructive migration, and rollback plan covers queued job/DNA data.
- [ ] VPS disk/CPU/RAM/swap capacity reviewed; resolve the latest 89% disk / fully occupied 2 GiB swap situation before enabling real calls.
- [ ] `concept-short-v1`, live Stage 1 jobs, Stage 2 and Windows Scout are unchanged. No other worker is auto-enqueued; default V2, pilot and Scout flags all OFF.
- [ ] Pilot tenant is explicitly allowlisted with per-actor permission `typography_reference.manage` for enqueue and `typography_reference.read` for status, and at most 2 concurrent analysis leases.
- [ ] A specific AI provider/credential pool, per-image cost cap, daily request cap and emergency kill-switch have been approved. Do **not** include secret values in evidence.
- [ ] The observer can retrieve tenant-scoped sanitized logs (job status, duration, retry count, provider calls, token usage/cost) without viewing private bytes or raw AI output.
- [ ] A reviewer has independently verified legal permission for all eight source images and recorded attribution/consent in approved private storage.
- [ ] Stage 1 / Typography Ideas authenticated screenshots have been reviewed at 1440px, 768px and 390px; source asset privacy is protected.

**Any unchecked item means DO NOT set `TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=true`, and do not invoke `--execute`.** Passing CI does not replace this manual release gate.

## 1. Pilot scope

Use **exactly eight** distinct, rights-verified typography references from the CAM-managed image library. Plan a mix of straight/arched/curved letter arrangements, one/two relative text layers, and realistic cases where background or product fabric could confuse the region detector. Each can be queued only once per `image_hash + analyzer_version` and tenant.

| Slot | Rights verified | Source hash matched | DNA generated | Region typography-only | Font/layout/overlap plausible | No copied lettering | Needs review | Reviewer |
|---|---|---|---|---|---|---|---|---|
| R01 | pending | — | — | — | — | — | — | pending |
| R02 | pending | — | — | — | — | — | — | pending |
| R03 | pending | — | — | — | — | — | — | pending |
| R04 | pending | — | — | — | — | — | — | pending |
| R05 | pending | — | — | — | — | — | — | pending |
| R06 | pending | — | — | — | — | — | — | pending |
| R07 | pending | — | — | — | — | — | — | pending |
| R08 | pending | — | — | — | — | — | — | pending |

Use pseudonymous slot IDs here; real reference IDs, source photos, hashes, legal documents and private URLs stay in access-controlled storage.

## 2. Per-image QA evidence to collect

1. **Pre-model:** internal source SHA256 equals Managed Storage bytes, tenant/rights/approval policy verified, image dimensions/format/size within allowed bounds, exact job version identified.
2. **Model result:** valid `typography-dna/v1` schema, normalized image region not exceeding boundaries, confidence at least 0.65 or explicit `needs_review`, bounded layout nodes/edges, clear hierarchy and no source phrase recorded.
3. **Typography-only:** reviewer marks whether region excludes cap visor/body, photo background, face, fabric texture, logos or unrelated text; mark `needs_review` instead of silently accepting uncertain detections.
4. **Structural fidelity:** path type (straight/arch/curve), relative font mood and overlap constraints correspond to the reference sufficiently to transfer to *different* keywords without copying original outlines. No claim of trademark clearance.
5. **Lease/cache/privacy:** a second request with the same source hash/analyzer version must be a cache hit or existing job; revoked rights or stale tokens cannot commit; private assets remain invisible to non-owning non-managers.
6. **Budget:** record provider, model, input/output tokens, billed USD amount (when known), bytes before/after downsampling, single-call count, elapsed milliseconds, attempt number and final status. Never invent a missing metric; mark `unknown`.
7. **Review:** explicitly sign off each slot as `accepted`, `needs_review`, or `rejected` with sanitized reason code; archive detailed evidence privately.

## 3. Quality and cost comparison

Measure two approaches on the *same eight* references, without automatically billing a second request:

- **A — single pass, low cost (default):** provider receives only size-limited image and outputs the normalized typography crop region + Style DNA.
- **B — ROI crop/refine (conditional):** only if reviewer demonstrates A incorrectly includes product/background or loses thin strokes. Crop locally from the verified source; inspect quality and expected incremental cost before authorizing any second AI call.

Report counts of accepted/review/failed, region precision by human review, P50/P95 latency, cache hit ratio, attempts per image, provider usage and actual billed total. G3 cannot be declared complete using synthetic-only samples; do not claim a cost or pass rate without real evidence.

## 4. Exit criteria and next action

- All eight items have verifiable source rights and a human-reviewed DNA result or explicit reason for non-completion.
- No source text copied; all accepted DNA correctly isolates typography and uses generalizable relative layout/font/overlap attributes.
- Failed/low-confidence samples are flagged for human review; retries are budgeted and terminate.
- API status/permission behavior, worker lease fencing, private image access, cache behavior and rollback are supported by CI + pilot evidence.
- Pilot latency, token use, costs and error/review counts have been recorded, not estimated.
- Manual override, perceptual near-duplicate screening and G2 authenticated UI acceptance are reviewed before progressing to G4.

**Next only after G3 is accepted:** start G4 reference matcher and Style Lab dynamic composer in Balanced mode; preserve the exact keyword and retain Stage 1 `concept-short-v1` fallback.
