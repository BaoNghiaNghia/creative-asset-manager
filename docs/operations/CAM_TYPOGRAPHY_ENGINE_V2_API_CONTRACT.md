# CAM Typography V2 — G1.2 API Contract (pilot, default OFF)

**Scope:** metadata and durable reference-image library only. This release **does not** start Typography Scout, analysis, layout composition, ImageGen, Stage 1 jobs, or modify `concept-short-v1`.

## Activation and permission boundaries

All endpoints share prefix `/api/v1/realistic-review-ugc/typography` and are blocked (HTTP 404 `typography_v2_disabled`) unless **both** `TYPOGRAPHY_V2_ENABLED=true` and the active tenant ID appears in `TYPOGRAPHY_V2_CANARY_TENANT_IDS` (comma-separated). Defaults are **OFF** and empty; changing flags is a separate audited rollout after G0/G1–G10 gates pass. No feature activation was performed by this PR.

- `typography_reference.read`: list/detail/images/style DNA of workspace-visible or self-created private reference assets.
- `typography_reference.upload`: upload images to the active tenant; created items remain `candidate` and cannot be auto-approved.
- `typography_reference.manage`: edit rights, status, scope, privacy, labels, priority and archive, with tenant-safe ID verification.
- `typography_generate.run`, `typography_scout.manage`: reserved for later gates; **no generation/scout routes exist** in G1.2.

Existing Viewer and Operator roles never gain any of these new permissions automatically. Platform admin privileges do not override the feature-canary check.

## Endpoints

| Method | Path after prefix | Access | Notes |
|---|---|---|---|
| GET | `/references?status=&query=&source_type=&scope=&style_role=&my_uploads_only=&sort=&limit=&offset=` | read | Tenant/private filtered list and aggregate stats/capabilities; paginated, archived excluded by default |
| GET | `/references/{id}` | read | Tenant/private visibility 404 on inaccessible ID |
| GET | `/references/{id}/image` | read | Streams Managed Storage, `Cache-Control: private`, no public Drive URL |
| GET | `/references/{id}/dna` | read | Existing analyzer versions; empty before G3 |
| POST | `/references/uploads` | upload | Multipart `files` repeated, up to 12 images; `rights`, `privacy`, optional attribution |
| PATCH | `/references/{id}` | manage | Partial JSON metadata; `approved` requires verified source rights |
| DELETE | `/references/{id}` | manage | Soft archive only (no image/storage deletion) |

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
