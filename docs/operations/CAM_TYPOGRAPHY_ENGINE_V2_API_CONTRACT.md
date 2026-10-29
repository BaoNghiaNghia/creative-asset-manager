# CAM Typography V2 — G1.2 API Contract (pilot, default OFF)

**Scope:** metadata and durable reference-image library plus G3 *explicit queue request/status* contracts. These API routes **do not execute AI**, register a worker, start Typography Scout, layout composition, ImageGen, or change Stage 1 `concept-short-v1`.

## Activation and permission boundaries

Reference endpoints share prefix `/api/v1/realistic-review-ugc/typography` and are blocked (HTTP 404 `typography_v2_disabled`) unless **both** `TYPOGRAPHY_V2_ENABLED=true` and the active tenant ID appears in `TYPOGRAPHY_V2_CANARY_TENANT_IDS` (comma-separated). Defaults are **OFF** and empty; changing flags is a separate audited rollout after G0/G1–G10 gates pass. No feature activation was performed by this PR.

- `typography_reference.read`: list/detail/images/style DNA of workspace-visible or self-created private reference assets.
- `typography_reference.upload`: upload images to the active tenant; created items remain `candidate` and cannot be auto-approved.
- `typography_reference.manage`: edit rights, status, scope, privacy, labels, priority and archive, with tenant-safe ID verification.
- `typography_generate.run`, `typography_scout.manage`: reserved for later gates; **no generation/scout routes exist** in G1.2.

Existing Viewer and Operator roles never gain any of these new permissions automatically. Platform admin privileges do not override the feature-canary check.

## Endpoints

| Method | Path after prefix | Access | Notes |
|---|---|---|---|
| GET | `/status` | authenticated | HTTP 200 `{ "available": boolean }`; false when feature OFF, tenant not allowlisted or read permission absent; never reveals reference data |
| GET | `/references?status=&query=&source_type=&scope=&style_role=&my_uploads_only=&sort=&limit=&offset=` | read | Tenant/private filtered list and aggregate stats/capabilities; paginated, archived excluded by default |
| GET | `/references/{id}` | read | Tenant/private visibility 404 on inaccessible ID |
| GET | `/references/{id}/image` | read | Streams Managed Storage, `Cache-Control: private`, no public Drive URL |
| GET | `/references/{id}/dna` | read | Versioned raw AI DNA and guarded manual-review status/effective structure; tenant/private filtered |
| PATCH | `/references/{id}/dna/{dna_id}/review` | manage | Human accept / needs-review / reject and bounded layout, font-mood, overlap override; audit only, no AI call |
| GET | `/references/{id}/dna/{dna_id}/similar` | read | Read-only ROI dHash near-duplicate warnings, same-tenant & private visibility filter, bounded scan; no automatic rejection |
| GET | `/references/{id}/analysis` | read | Current analyzer version job status/cache only; tenant/private visibility enforced, never lease/token/provider metadata |
| POST | `/references/{id}/analyze` | manage | Explicit idempotent enqueue **only**; source rights + canary checked; no provider call or background start |
| POST | `/references/uploads` | upload | Multipart `files` repeated, up to 12 images; `rights`, `privacy`, optional attribution |
| PATCH | `/references/{id}` | manage | Partial JSON metadata; `approved` requires verified source rights |
| DELETE | `/references/{id}` | manage | Soft archive only (no image/storage deletion) |

### G3 explicit Analyze queue boundary (development only)

- `POST /references/{id}/analyze` requires **typography_reference.manage** and BOTH the server-side feature flag and tenant canary. There is **no request body** for a model, prompt or provider. CAM pins `g3-layout-font-overlap-v1` as the analyzer version.
- A request adds an idempotent tenant/version/hash job if the reference is active and rights are `owned`, `licensed` or `permitted_reference`. Possible `outcome` values are `created`, `existing`, `cache_hit`; missing/cross-tenant reference returns 404, unverified rights or inactive reference returns 409. An audit event is written only for a newly created job.
- `GET /references/{id}/analysis` requires **typography_reference.read** and obeys private/workspace visibility. It returns `analyzer_version`, `analysis_status`, `dna_available` and an optional public job view (`id`, `status`, `attempt_count`, `next_attempt_at`, `last_error_code`). **Lease token, worker owner, private remote file IDs, image URLs and credentials are never returned.**
- These routes **only enqueue and report state**; they neither call the AI provider nor start a background worker, and cannot be used on production until additive migration `0189` and G0.1 rollback/activation gates are satisfied. Typography V2 remains OFF by default.

### G3 manual Style DNA review

Human review is an explicit, manage-only step after Style DNA analysis. A reviewer may `accepted`, `needs_review`, or `rejected` a source/versioned result with a fixed reason code, optionally correcting normalized region, general font mood, structural layout graph and overlap. Arbitrary text and exact original lettering are **not** accepted. The original AI result is never overwritten; the existing `manual_override_json` field holds only the human decision/structural patch. Accepted `effective_dna` is released to future Style Lab only while the same tenant, source hash, analyzer version, verified rights and editorial approval remain valid. Revocation invalidates effective DNA immediately. No new migration or background worker is required for this API; all pilot flags remain OFF. See `CAM_TYPOGRAPHY_ENGINE_V2_G3_HUMAN_REVIEW.md`.

### G3 near-duplicate screening (advisory only)

After a successful single-pass visual analysis, CAM computes a local versioned 64-bit dHash on the AI-identified typography-only ROI and saves it in the pre-existing `perceptual_hash` DNA column. `GET /references/{id}/dna/{dna_id}/similar` returns only visible same-tenant reference IDs and Hamming distances; source hashes and private image URLs never appear. Defaults: distance 6, limit 10, scan cap 500; truncated scans are clearly marked. The hint can trigger the human reviewer reason `near_duplicate` but **never** automatically rejects a source. No model re-run or extra migration; see `CAM_TYPOGRAPHY_ENGINE_V2_G3_SIMILARITY.md`.

### G2 library filters and response

The list accepts `status=candidate|approved|rejected|archived`, literal keyword/tag/source `query`, `source_type=upload|scout|internal`, `scope=workspace|campaign|keyword|skill`, and `style_role=layout|font_mood|overlap`. `my_uploads_only=true` filters by the current actor. `sort=newest|oldest|priority` uses a deterministic ID tie-breaker; `limit` is 1–100 and `offset` is nonnegative. Unknown filter values return HTTP 422.

Responses include `items`, `total`, `limit`, `offset`, visibility-filtered `stats` (`total`, `approved`, `needs_review`, `analyzing`), and `capabilities` (`read`, `upload`, `manage`). Never derive permission status from UI-only roles: server is authoritative. `style_role` filters the approved Stage 1 transfer components, **not** an analyzed style family; family matching awaits G3 analyzer.

### Upload

Request is multipart with one to twelve `files` and optional `rights=unknown|owned|licensed|permitted_reference|blocked`, `privacy=private|workspace`, `source_attribution` (max 400 chars). All images are checked as actual decoded **PNG/JPEG/WEBP** (not MIME claims), bounded at 20,000,000 bytes each and existing decoder limits. Each file returns its own `created|duplicate|error` result; the response includes `created`, `duplicates`, `failed`, `total`. File import is not atomic as a batch; a failed item doesn't roll back preceding successful files.

A duplicate hash in the same tenant reuses the storage record and **does not** automatically change original ownership, rights, approval or tags. Private references created by another owner are not returned by dedup operations. Existing generic RRUGC list/detail/image and generic RRUGC upload cannot re-expose **typography-only** assets through the legacy namespace.

**Example result shape:**

```json
{
  "items": [
    {"index": 0, "filename": "idea.png", "status": "created", "reference": {"id": "...", "editorial_status": "candidate", "source_rights_status": "unknown"}, "stored_new_asset": true},
    {"index": 1, "filename": "duplicate.png", "status": "duplicate", "reference": {"id": "..."}, "stored_new_asset": false},
    {"index": 2, "filename": "invalid.txt", "status": "error", "code": "typography_format_unsupported"}
  ],
  "total": 3,
  "created": 1,
  "duplicates": 1,
  "failed": 1
}
```

### Editing and approvals

Editable fields: `editorial_status`, `source_rights_status`, `privacy`, `scope`, `campaign_id`, `keyword_id`, `skill_name`, `preference`, `priority`, `tags`, `reference_roles` (`layout|font_mood|overlap`), `source_attribution`, `notes`. Scope can be `workspace|campaign|keyword|skill`. The API checks tenant ownership of linked Stage 0 keyword IDs and campaign IDs. Switching scope clears unrelated identifiers unless explicitly supplied. Editing reference metadata and archive actions create tenant-scoped audit entries without storing full image bytes or private URLs.

References with `unknown` or `blocked` rights **cannot** be `approved`, even by an administrator. `DELETE` records `archived` and never destroys stored bytes or other consumers' references.

## Validation evidence and remaining gates

- G1.1 `#40`: additive schema/repository merged `b5b74814635b70997791c7847c3cdc4bf9c9115f` after all 17 GitHub checks.
- G1.2: PR #41 merged `b8e558cd48c80dd6a50fc8b986eb40a6c8ae71b2`, all 17 checks green. `TYPOGRAPHY_V2_ENABLED` remains OFF by default; no production rollout.
- G0.1: production rollback symlinks inaccessible to authorized workspace; disk and swap capacity review remains mandatory. This API is not activated in production.
- G2 (branch `feat/typography-v2-ideas-ui-20261028`): library/subnav built and locally tested. The web UI selects up to 20 files, sends at most **3 concurrent multipart requests, 2 files per request**, reports per-file status/error/duplicate, and supports manual retries. This does not change the backend 12-file API maximum. Browser QA/UX-01..UX-16 are separate acceptance gates; actual production activation still blocked by G0.1.
