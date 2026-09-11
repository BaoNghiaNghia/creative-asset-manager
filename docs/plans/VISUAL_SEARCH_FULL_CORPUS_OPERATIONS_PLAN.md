# Visual Search — Full-Corpus Search & Operations Plan

> **Repository:** `BaoNghiaNghia/creative-asset-manager`  
> **Document date:** 2026-09-11  
> **Status:** Planning document for later implementation. This document does **not** authorize production mutation, backfill execution, service restarts, Elasticsearch alias changes, or broad Visual Search rollout.  
> **Companion guide:** `docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md`

---

## 1. Why this plan exists

The current Visual Search implementation can accept an uploaded reference image and run a SigLIP/KNN search, but the current behavior can still return only a few results even when the tenant owns a much larger image corpus.

The main causes identified are:

1. connection-level source diversification is currently too aggressive (`max_per_source=2`), so an entire connected OneDrive/Google Drive/SharePoint source can be reduced to only a few results;
2. the frontend currently tends to pass the active source into Visual Search, so the default search can be source-scoped instead of tenant-global;
3. existing resource images are not necessarily all imported and visually indexed;
4. the current metrics are runtime/process-local and do not show durable corpus coverage;
5. the current query path does not expose enough attrition diagnostics to explain where candidates disappeared;
6. ANN retrieval breadth should be tuned separately from the number of returned results;
7. the current supported image MIME corpus is narrower than the possible resource corpus;
8. exact SHA dedupe is not sufficient to suppress resized/compressed/cropped near-duplicate derivatives.

The goal of this plan is to make Visual Search behave like a true full-resource discovery system rather than a narrow reverse-image lookup.

---

## 2. Target user behavior

The desired user-facing flow is:

```text
Upload/select reference image
        ↓
Optional crop
        ↓
Optional text refinement
        ↓
Search scope
  ● All resources
  ○ Current source
  ○ Current folder
        ↓
Visual embedding
        ↓
Tenant/viewer authorization filters
        ↓
Elasticsearch ANN/KNN over the full authorized visual corpus
        ↓
Exact duplicate suppression
        ↓
Optional near-duplicate diversification
        ↓
Paginated similar-image results
```

For users allowed tenant-global search, `All resources` should be the default.

Pure viewers must remain constrained by the source/folder permissions already enforced by CAM. Global Visual Search must never bypass tenant isolation or viewer folder scope.

---

## 3. P0 — Disable the connection-level `max_per_source=2` cap

### 3.1 Current problem

The current visual ranking policy includes a source cap equivalent to:

```text
VISUAL_SEARCH_RANKING_MAX_PER_SOURCE=2
```

The `source_id` used by Visual Search represents the connected external source, for example an entire OneDrive account or Google Drive connection, not one individual image or folder.

This means a corpus such as:

```text
OneDrive A
└── 30,000 searchable images
```

can effectively behave like:

```text
KNN finds many relevant hits
        ↓
source diversification
        ↓
maximum 2 hits from OneDrive A
```

This directly explains the symptom “upload image and only see a few similar results.”

### 3.2 Required change

For pure visual search:

```text
exact-content SHA dedupe = ON
connection-level source cap = OFF by default
```

Do not simply change the default from `2` to another arbitrary high value. Prefer an explicit disabled semantic such as `0` or `null`.

### 3.3 Acceptance criteria

```text
[ ] One connected source can contribute more than two results.
[ ] Exact SHA duplicates remain suppressed.
[ ] Pagination can return more results from the same connection.
[ ] Query diagnostics report how many candidates were removed by any active diversity rule.
```

---

## 4. P0 — Make `All resources` the default search scope

### 4.1 Current problem

The frontend Visual Search flow currently derives its scope from the active explorer provider/source. As a result, a user browsing OneDrive A can upload a reference image and unintentionally search only OneDrive A.

Desired default behavior is tenant-global across all resources the principal is authorized to access.

### 4.2 User-facing scope selector

Add to the Visual Search panel:

```text
Search scope

● All resources
○ Current source
○ Current folder
```

For `All resources`, the query should not automatically include `source_provider` or `external_source_id` filters.

For `Current source`, send the current provider/source filter.

For `Current folder`, send the existing folder/ancestor scope through the authorization-aware query path.

### 4.3 Authorization rule

Global scope does not mean unscoped search.

Every query must still enforce:

```text
tenant_id = active tenant
is_deleted = false
is_hidden = false
principal/viewer access filters
```

Pure viewers must never gain access to sources/folders they could not browse normally.

### 4.4 Acceptance criteria

```text
[ ] All Resources is the default for principals allowed tenant-global search.
[ ] Current Source remains available as an explicit filter.
[ ] Current Folder remains available as an explicit filter.
[ ] Viewer/folder authorization remains enforced end-to-end.
[ ] Load More reuses the committed scope rather than current draft UI state.
```

---

## 5. P0 — Treat full-corpus readiness as three coverage layers

“Full visual backfill” is not enough by itself. Searchability depends on three distinct layers.

### 5.1 Layer A — Discovery coverage

Question:

> How many image resources have actually been discovered from connected sources?

Source of truth:

```text
ExternalSource
    ↓
SourceAsset
```

Example:

```text
Discovered source images: 52,481
```

### 5.2 Layer B — Import coverage

Question:

> How many discovered images have become internal assets with the content identity required for visual indexing?

Required state:

```text
SourceAsset
    ↓
AssetSourceLink
    ↓
AssetModel + content_hash
```

Example:

```text
Discovered:       52,481
Imported:         49,810
Pending import:    1,921
Import failed:       750
```

### 5.3 Layer C — Visual-index coverage

Question:

> How many visually eligible imported images have a current compatible embedding/document in the visual Elasticsearch index?

Example:

```text
Visual eligible: 49,300
Indexed current: 47,950
Missing:          1,050
Stale:              300
```

### 5.4 Two separate coverage ratios

Display both:

```text
eligible visual coverage
= indexed current / visual eligible

whole-resource searchable coverage
= indexed current / discovered resource images
```

Example:

```text
Eligible visual coverage:      97.3%
Whole-resource searchable:     91.4%
```

The second number is the clearest answer to:

> “Visual Search can search what percentage of all my resource images?”

---

## 6. P0 — Durable full-corpus backfill and reconciliation

The current backfill tooling is useful, but production-grade full-corpus work must become a durable operation rather than one long synchronous command/request.

### 6.1 Required durable state

A backfill run should persist:

```text
run_id
status
scope
checkpoint_asset_id
scanned
eligible
queued
indexed/current
skipped_current
skipped_inflight
failed
started_at
updated_at
completed_at
```

Suggested states:

```text
pending
running
paused
completed
failed
cancelled
```

### 6.2 Reconciliation semantics

For every eligible visual asset:

```text
no visual-index job/document
    → enqueue visual_index_sync

pending/processing current job
    → leave alone

completed job + current ES document
    → skip current

failed/retry-exhausted job + missing current document
    → canonical retry/reconcile

stale document/content/schema mismatch
    → enqueue current projection
```

Do not consider “a historical job exists” sufficient reason to skip forever.

### 6.3 No direct DB status edits

Retry/reconciliation must use the normal processing job/repository/service paths. The UI must not repair jobs by directly editing job state in PostgreSQL.

### 6.4 Backfill controls

Admin/operator UI should support:

```text
Dry Run
Start Full Backfill
Pause
Resume
Cancel
Retry Failed
```

Backfill execution should remain idempotent and resumable.

---

## 7. P1 — Persistent coverage diagnostics

The existing runtime Visual Search metrics are useful for latency and recent behavior, but they are process-local and reset on service restart. They cannot answer persistent corpus questions.

Operations must separate:

```text
Runtime Metrics
+
Persistent Coverage
```

### 7.1 Runtime metrics

Track/display:

```text
query requests by kind/outcome
success rate
empty-result rate
unavailable/error rate
upload p50/p95
by-asset p50/p95
crop p50/p95
hybrid p50/p95
encode-image p50/p95
encode-text p50/p95
KNN p50/p95
rank p50/p95
hydrate p50/p95
visual-index source-read/encode/ES-upsert p50/p95
```

### 7.2 Persistent coverage

Track/display:

```text
discovered_images
imported_images
visual_eligible
visual_indexed_current
visual_index_missing
visual_index_stale
visual_jobs_pending
visual_jobs_processing
visual_jobs_failed
unsupported_images
```

Persisted coverage must survive API/worker restarts.

---

## 8. P1 — Query attrition diagnostics

When a query returns only a few results, operators need to know exactly where candidates disappeared.

For each bounded recent query, record aggregate counts only:

```text
KNN hits received
exact duplicate removals
source/diversity removals
stale/hydration removals
authorization/scope removals if applicable
final returned count
```

Example:

```text
Raw KNN candidates       100
Exact duplicates removed  14
Source-cap removed          0
Hydration removed           2
Returned                   40
```

This makes failures immediately explainable:

```text
Raw 100 → After source diversity 2
```

means ranking/diversity is suppressing results.

```text
Raw 4 → Returned 4
```

means the likely issue is corpus coverage or retrieval recall, not frontend rendering.

Do not store query images, raw embeddings, signed URLs, authorization headers, or cross-tenant asset details in query diagnostics.

---

## 9. P1 — Separate retrieval K from ANN `num_candidates`

The number of results retained and the breadth of ANN exploration are different controls.

Recommended initial benchmark candidate:

```text
VISUAL_SEARCH_RETRIEVAL_K=200
VISUAL_SEARCH_NUM_CANDIDATES=500
VISUAL_SEARCH_PAGE_SIZE=40
```

Conceptually:

```text
large visual corpus
      ↓
ANN explores ~500 candidate neighborhood
      ↓
returns top 200 retrieval candidates
      ↓
dedupe/ranking/hydration
      ↓
UI page of 40
```

For larger corpora, benchmark `num_candidates` values such as:

```text
500
750
1000
```

Do not increase these blindly. Measure relevance together with KNN p95, total query p95, CPU, and Elasticsearch memory.

The Operations UI should show the active retrieval configuration read-only.

---

## 10. P2 — Expand supported image formats

The existing supported visual corpus is centered on:

```text
JPEG
PNG
WebP
AVIF
HEIC
HEIF
```

### 10.1 First expansion group

Add safe rasterization for:

```text
BMP
GIF
TIFF
```

Policy examples:

```text
animated GIF → representative/first frame for V1
multi-page TIFF → first page for V1
BMP → decode and normalize RGB
```

### 10.2 Separate later group

Do not mix the following into the same small task unless decoder isolation already exists:

```text
SVG
PSD
DNG / RAW
```

These require different parser/rasterizer/resource-safety boundaries.

### 10.3 UI evidence

Show unsupported resource breakdown:

```text
GIF
TIFF
BMP
PSD
DNG
other
```

so operators can see exactly how unsupported formats affect whole-resource coverage.

---

## 11. P2 — Near-duplicate clustering

Exact SHA dedupe only catches byte-identical content.

It does not group derivative copies such as:

```text
photo.jpg
photo_resized.jpg
photo_compressed.webp
photo_crop.jpg
```

These can dominate visual similarity results even though they are effectively the same creative.

Do not use connection-level source suppression to solve this.

Prefer a near-duplicate identity such as:

```text
visual_cluster_id
```

or a dedicated perceptual-hash/visual-cluster stage.

Ranking can then prefer:

```text
best hit from cluster A
best hit from cluster B
best hit from cluster C
```

and expose additional copies as expandable members if useful.

Potential user-facing option:

```text
☑ Diversify similar copies
```

---

## 12. Visual Search Operations UI

Add a dedicated Visual Search section under the existing operations/admin experience.

Suggested navigation:

```text
AI Operations
├── Image processing
├── Video processing
├── ...
└── Visual Search
```

### 12.1 Coverage overview

Top-level cards:

```text
Resource Images
Imported
Visual Eligible
Searchable
Missing Index
Stale
Failed
Unsupported
```

Primary KPI:

```text
Whole-resource searchable
██████████████████░░ 91.4%
47,950 / 52,481 images
```

Also show:

```text
Eligible visual coverage
47,950 / 49,300 = 97.3%
```

### 12.2 Source coverage table

Columns:

```text
Source
Provider
Resource images
Imported
Eligible
Indexed current
Missing
Stale
Failed
Unsupported
Coverage
Last sync
```

Example:

| Source | Resource images | Imported | Indexed | Coverage | Failed |
|---|---:|---:|---:|---:|---:|
| OneDrive — account A | 31,420 | 30,980 | 30,201 | 97.5% | 114 |
| Google Drive — Main | 17,312 | 16,901 | 16,610 | 98.3% | 89 |
| SharePoint — Design | 3,749 | 1,929 | 1,139 | 59.0% | 97 |

### 12.3 Backfill panel

Example:

```text
VISUAL INDEX BACKFILL

Status: Running

Scanned        28,100 / 49,300
Queued          3,821
Indexed         3,410
Pending           294
Failed            117
Skipped current 24,279

Rate            2.8 img/s
Elapsed         36m 18s
Estimated left  2h 06m
Checkpoint      <bounded checkpoint>

[ Pause ] [ Cancel ]
```

Idle state:

```text
[ Dry Run ] [ Start Full Backfill ] [ Retry Failed ]
```

### 12.4 Runtime/search health panel

Display aggregate operational evidence:

```text
Queries
Success
Empty-result rate
Unavailable/error rate
Upload p50/p95
By-asset p50/p95
Crop p50/p95
Hybrid p50/p95
Encoder p50/p95
KNN p50/p95
Hydration p50/p95
Total p50/p95
```

### 12.5 Retrieval configuration panel

Read-only snapshot:

```text
Visual index alias/schema
Encoder name/revision
Retrieval K
num_candidates
Page size
Image weight
Text weight
Connection source cap: OFF
Supported formats
```

### 12.6 Index-job table

Columns:

```text
Status
Asset/source
Provider
Operation
Attempt
Age/duration
Error code
Updated
Actions
```

Actions should use canonical processing APIs only:

```text
Retry failed job
Retry failed visual-index group
```

---

## 13. User Visual Search UI changes

The normal end-user Visual Search panel should communicate its real search scope and corpus size.

Example:

```text
┌────────────────────────────────────────────┐
│ Find similar images                     × │
│                                            │
│       [ reference image ]                  │
│                                            │
│ Search scope                               │
│ ● All resources                            │
│ ○ Current source                           │
│ ○ Current folder                           │
│                                            │
│ Searching 47,950 indexed images            │
│ across OneDrive, Google Drive, SharePoint  │
│                                            │
│ Refine: [ same design but blue       ]     │
└────────────────────────────────────────────┘
```

The count is informational and should be based on the same authorization-aware coverage rules used by the query.

Do not show a tenant-global number to a viewer who is authorized for only a subset of sources/folders.

---

## 14. Recommended admin APIs

Exact naming should follow the current AI Operations conventions at implementation time.

Suggested contract:

```text
GET  /api/v1/admin/visual-search/coverage
GET  /api/v1/admin/visual-search/coverage/sources
GET  /api/v1/admin/visual-search/backfills
POST /api/v1/admin/visual-search/backfills/dry-run
POST /api/v1/admin/visual-search/backfills
POST /api/v1/admin/visual-search/backfills/{id}/pause
POST /api/v1/admin/visual-search/backfills/{id}/resume
POST /api/v1/admin/visual-search/backfills/{id}/cancel
POST /api/v1/admin/visual-search/jobs/retry-failed
GET  /api/v1/admin/visual-search/diagnostics
GET  /api/v1/admin/visual-search/recent-queries
```

Every route must be tenant-bound and permission-gated.

---

## 15. Coverage response model

Example:

```json
{
  "generated_at": "...",
  "totals": {
    "discovered_images": 52481,
    "imported_images": 49810,
    "visual_eligible": 49300,
    "visual_indexed_current": 47950,
    "visual_index_missing": 1050,
    "visual_index_stale": 300,
    "visual_jobs_pending": 294,
    "visual_jobs_failed": 117,
    "unsupported_images": 3181
  },
  "ratios": {
    "import_coverage": 0.949,
    "eligible_visual_coverage": 0.973,
    "whole_resource_searchable": 0.914
  }
}
```

Do not consider an asset “indexed current” just because an old completed processing job exists.

---

## 16. Coverage consistency rules

### Discovered

Count live image `SourceAsset` rows in scope, excluding deleted/retired items.

### Imported

Require a live source-to-internal-asset relationship and the content identity required for visual indexing.

### Visual eligible

Require:

```text
supported visual MIME
live source
internal asset/content hash
not deleted/hidden according to visual corpus rules
```

### Indexed current

A current visual document must match:

```text
tenant_id
asset_id
content_sha256
embedding_schema_version
encoder descriptor compatibility
not deleted/hidden
```

A stale vector/document must not count as searchable-current.

### Failed

Count latest canonical visual-index work that is terminally failed/retry-exhausted and still lacks a current compatible document.

---

## 17. Revised priority roadmap

| Priority | Task | Purpose | Required UI evidence |
|---|---|---|---|
| **P0** | Disable connection-level source cap | Stop collapsing one connected source to only a few results | Query attrition shows source-cap removals |
| **P0** | Default to All Resources | Search all resources the principal is authorized to access | Scope selector + active scope summary |
| **P0** | Discovery/import/index coverage accounting | Know how much of the real corpus is searchable | Coverage funnel + source table |
| **P0** | Durable full backfill + failed/stale reconciliation | Drive searchable coverage toward completion | Progress, checkpoint, pause/resume, retry failed |
| **P1** | Persistent coverage diagnostics | Know precisely where corpus loss occurs | Visual Search Operations dashboard |
| **P1** | Query attrition diagnostics | Explain why a query returns few results | Raw → dedupe → hydrate → returned counts |
| **P1** | Separate K / num_candidates; initial 200 / 500 | Improve ANN recall on a large corpus | Retrieval config + latency |
| **P2** | GIF/TIFF/BMP support | Expand searchable corpus | Unsupported-format breakdown |
| **P2** | Near-duplicate clustering | Avoid repeated derivative copies | Cluster/diversity indicator |

Recommended execution order:

```text
VS-13A  Remove source cap + global-scope query contract
   ↓
VS-13B  Persistent coverage service + coverage APIs
   ↓
VS-13C  Durable full-corpus backfill/reconciliation
   ↓
VS-13D  Visual Search Operations UI
   ↓
VS-13E  Query attrition diagnostics
   ↓
VS-13F  Retrieval K / num_candidates tuning
   ↓
VS-13G  Additional image formats
   ↓
VS-13H  Near-duplicate clustering
```

Do not benchmark relevance against a partially indexed corpus without recording coverage alongside the benchmark.

---

## 18. Codex task boundaries for later execution

### VS-13A — Full-resource query semantics

Goal:

```text
All Resources is default.
Connection-level cap is disabled.
Authorization remains strict.
```

Acceptance:

```text
[ ] One source may return >2 results.
[ ] Exact SHA duplicates remain suppressed.
[ ] Owner/admin global query omits source filters.
[ ] Viewer scope remains constrained.
[ ] Load More preserves selected scope.
```

### VS-13B — Persistent coverage accounting

Goal:

```text
Compute discovered/imported/eligible/indexed/missing/stale/pending/failed/unsupported
for tenant and per source.
```

Acceptance:

```text
[ ] Coverage survives API restart.
[ ] Indexed-current validates content/schema/encoder compatibility.
[ ] Per-source totals reconcile with tenant totals.
[ ] No cross-tenant leakage.
```

### VS-13C — Durable full-corpus backfill

Goal:

```text
Backfill/reconcile all eligible assets safely and resumably.
```

Acceptance:

```text
[ ] Checkpoint persisted.
[ ] Pause/resume supported.
[ ] Failed jobs can be canonically retried.
[ ] In-flight jobs are not duplicated.
[ ] Stale/missing documents are repaired.
[ ] Completed/current documents are not re-encoded unnecessarily.
```

### VS-13D — Operations UI

Goal:

```text
Expose coverage, source breakdown, backfill, failures, runtime health, and retrieval config.
```

Acceptance:

```text
[ ] Coverage funnel visible.
[ ] Whole-resource-searchable KPI visible.
[ ] Source table visible.
[ ] Backfill controls are permission-gated.
[ ] Failed index jobs use canonical retry APIs.
[ ] No secrets/query images/vectors are exposed.
```

### VS-13E — Query attrition diagnostics

Goal:

```text
Make low-result queries explainable.
```

Acceptance:

```text
[ ] Raw KNN hit count.
[ ] Exact duplicate removals.
[ ] Diversity/source-cap removals.
[ ] Hydration removals.
[ ] Returned count.
[ ] Scope/corpus estimate.
[ ] Latency stages.
```

### VS-13F — Retrieval tuning

Initial candidate:

```text
K=200
num_candidates=500
page_size=40
```

Compare Recall@10 / Precision@10 / NDCG@10 and p95 latency before increasing retrieval breadth.

### VS-13G — Format expansion

Add safe BMP/GIF/TIFF rasterization first.

Keep PSD/RAW/SVG separate unless decoder/runtime isolation is already proven.

### VS-13H — Near-duplicate clustering

Diversify derivative copies without suppressing an entire connected source.

This is post-baseline quality work and should not block the first full-corpus release.

---

## 19. Definition of Done — full-corpus Visual Search baseline

```text
[ ] All Resources is the default user scope for principals allowed global tenant search.
[ ] Viewer/folder restrictions remain enforced end-to-end.
[ ] One connected source can contribute more than two results.
[ ] Exact-content duplicates remain suppressed.
[ ] Coverage API reports discovered/imported/eligible/indexed/missing/stale/pending/failed/unsupported.
[ ] Whole-resource searchable percentage is visible.
[ ] Coverage is available per connected source.
[ ] Durable full backfill supports checkpoint + resume.
[ ] Failed/stale visual projections can be reconciled without direct DB edits.
[ ] Operations UI exposes backfill progress and visual-index failures.
[ ] User Visual Search panel shows active scope and approximate searchable corpus size.
[ ] Query diagnostics expose raw-candidate-to-returned-result attrition.
[ ] K and num_candidates are independent configuration values.
[ ] Retrieval tuning is benchmarked against a corpus whose coverage is recorded.
[ ] Runtime metrics and persistent coverage are clearly separated.
[ ] No query images, vectors, auth tokens, signed URLs, or cross-tenant details are logged/exposed.
```

---

## 20. When this work is resumed

Execute in this order:

```text
1. Fetch latest main and reconcile drift.
2. Audit production visual-index coverage read-only.
3. Execute VS-13A only.
4. Review source/query behavior.
5. Execute VS-13B only.
6. Review actual discovered/imported/indexed coverage numbers.
7. Execute VS-13C durable reconciliation/backfill.
8. Build VS-13D Operations UI.
9. Add VS-13E attrition diagnostics.
10. Only then benchmark VS-13F retrieval tuning.
11. Format expansion and near-duplicate clustering remain P2.
```

Core operating principle:

```text
A query can only be as good as the corpus it is allowed to search.

Before blaming the encoder or ANN relevance:
prove the asset was discovered,
prove it was imported,
prove it was eligible,
prove a current embedding exists,
prove the query scope included it,
then evaluate ranking quality.
```
