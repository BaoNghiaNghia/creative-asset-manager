# Creative Asset Manager — Pinterest-Style Visual Search Implementation Guide

> **Document type:** Master architecture + implementation plan + Codex taskbook  
> **Repository:** `BaoNghiaNghia/creative-asset-manager`  
> **Baseline inspected:** `main` @ `f393dfc94c1fea3302b1cad6cd09cd4fd7aafd87`  
> **Status:** DEVELOPMENT PLAN — this document does **not** authorize production changes or deployment.

---

## 0. How to use this document

This is the source-of-truth development guide for implementing Pinterest-style visual discovery in Creative Asset Manager.

Execute one bounded task/phase at a time:

```text
VS-00 Audit + ADR
  ↓
VS-01 Foundation
  ↓
VS-02 Encoder
  ↓
VS-03 Elasticsearch vector index
  ↓
VS-04 Asset indexing lifecycle
  ↓
VS-05 Find Similar
  ↓
VS-06 Upload image search
  ↓
VS-07 Crop search
  ↓
VS-08 Frontend discovery UX
  ↓
VS-09 Hybrid text + reranking + diversity
  ↓
VS-10 Backfill
  ↓
VS-11 Observability
  ↓
VS-12 Quality/performance/canary
```

At the start of every Codex session:

1. Fetch latest `main`.
2. Read root `AGENTS.md`.
3. Read this document.
4. Read `docs/plans/VISUAL_SEARCH_IMPLEMENTATION.md` if VS-00 has created it.
5. Inspect every current source file that will be touched.
6. Current source wins over assumptions in this guide.
7. Implement only the requested phase.

Do not instruct Codex to implement the full plan in one run.

---

# 1. Executive objective

Implement a Pinterest-style visual discovery system inside CAM.

The desired product is not merely reverse-image lookup. Users must be able to:

- select an existing CAM asset and click **Find Similar**;
- upload a reference image and find related CAM assets;
- crop/select a region and search based on that region;
- refine an image query with text such as `same garment but outdoor`, `similar but pink`, `hospital`, or `embroidery`;
- use refinement chips;
- continue discovery from any result;
- receive useful diversity instead of only near-duplicates.

Long-term visual dimensions:

```text
overall image
garment / product
embroidery
person / pose
background / environment
composition
color / photographic style
related creative concept
```

---

# 2. What “Pinterest-like” means

A basic reverse-image system is:

```text
image → embedding → nearest-neighbor search → similar images
```

The CAM target is:

```text
                   QUERY IMAGE
                       │
             full image or crop
                       │
             ┌─────────┴─────────┐
             │                   │
       visual embedding      text refinement
             │                   │
             └─────────┬─────────┘
                       │
                tenant filters
                       │
                       ▼
             candidate retrieval
             Elasticsearch ANN/KNN
                  top 200–500
                       │
                       ▼
                   reranking
          ┌────────────┼─────────────┐
          │            │             │
       visual       semantic       metadata
          │            │             │
          └────────────┼─────────────┘
                       │
                 diversification
                       │
                       ▼
                discovery results
                       │
                 refinement chips
                       │
                       ▼
              Find more like this
                       │
                      ...
```

The product should feel like visual exploration, not a one-shot utility.

---

# 3. Existing-system constraints

Visual search must fit CAM's existing architecture.

Known properties:

- React + TypeScript + Vite frontend;
- FastAPI API;
- PostgreSQL authoritative business database;
- Elasticsearch already used for search;
- image/video worker architecture;
- multi-tenant authorization;
- native systemd production deployment with immutable releases.

The current VPS has limited headroom. The last audit showed approximately:

```text
CPU       3 vCPU
RAM       5.8 GiB
Available ~2.1 GiB
Swap      0
Disk      30 GiB total / ~15 GiB free
Storage   local ext4
```

Initial operational rules:

```text
visual encoder concurrency = 1
backfill concurrency       = 1
no new vector database
no GPU requirement
no model inside FastAPI request process
no unbounded indexing jobs
```

Do not run a large visual backfill simultaneously with Dola/Chromium or other heavy production workloads without resource measurements.

---

# 4. Non-negotiable architecture principles

## 4.1 PostgreSQL remains authoritative

PostgreSQL owns:

- asset identity;
- tenant ownership;
- source relationships;
- authoritative asset lifecycle;
- permissions/business metadata.

Elasticsearch owns derived searchable representations.

## 4.2 Elasticsearch is the V1 vector engine

Do not introduce Qdrant, Milvus, Weaviate, Pinecone, or another vector DB unless measurements later justify it.

VS-00 must verify the exact deployed Elasticsearch version and vector capabilities before VS-03.

## 4.3 Do not host a heavy vision model in FastAPI

Forbidden:

```text
HTTP request
  ↓
CAM API
  ↓
load CLIP/SigLIP
  ↓
encode
```

Embedding generation belongs behind an isolated encoder/worker abstraction.

## 4.4 Tenant filter must participate in candidate retrieval

Never:

```text
KNN across all tenants
  ↓
top 500
  ↓
filter tenant
```

Tenant constraints must apply during candidate retrieval using the capability supported by the deployed Elasticsearch version.

Any cross-tenant result is a release blocker.

## 4.5 Search V3 must remain usable

If visual search is disabled, partially indexed, or the encoder is down, existing Search V3 must continue operating.

## 4.6 Embeddings are versioned derived data

Record:

```text
encoder_name
encoder_version
embedding_schema_version
dimension
preprocess_version
generated_at
```

Changing model, dimensions, normalization, preprocessing, or distance semantics requires a new versioned field/index.

## 4.7 Do not fake similarity percentages

Do not show `92% similar` by multiplying raw cosine similarity by 100. Until calibrated, use result order/qualitative explanation only.

---

# 5. Scope

## V1 required

1. global image embedding;
2. exact content fingerprint;
3. optional pHash near-duplicate fingerprint;
4. Elasticsearch vector indexing;
5. existing-asset Find Similar;
6. upload-reference search;
7. crop/region search;
8. optional text refinement;
9. current CAM metadata filters;
10. tenant isolation;
11. reranking;
12. duplicate suppression + basic diversification;
13. refinement chips from trusted metadata;
14. discovery from a result;
15. restart-safe backfill;
16. feature flags/canary;
17. tests, metrics, diagnostics and rollback.

## V1 does not require

- automatic object detection;
- face/person recognition;
- dedicated GPU;
- learned personalized ranking;
- multi-object embeddings;
- generative AI;
- OCR-based embroidery understanding;
- custom vector DB;
- model fine-tuning.

## V2

Planned automatic regions:

```text
person
garment
product / tote / hat
embroidery / text region
background / scene
```

## V3

Planned multi-vector asset:

```text
asset
├── global_embedding
├── garment_embedding
├── product_embedding
├── person_embedding
├── scene_embedding
├── composition_embedding
└── embroidery_embedding
```

---

# 6. Target architecture

```text
                             Browser
                                │
                           CAM Frontend
                                │
                                ▼
                             CAM API
                  ┌─────────────┴──────────────┐
                  │                            │
          existing Search V3           Visual Search API
                  │                            │
                  └─────────────┬──────────────┘
                                │
                                ▼
                         Elasticsearch
                 text + metadata + vectors
                                ▲
                                │
                       indexed derived data
                                │
                    ┌───────────┴───────────┐
                    │                       │
              asset lifecycle          backfill
                    │                       │
                    ▼                       ▼
                 visual encoder / worker
                    │
          ┌─────────┼──────────┐
          │         │          │
       decode    pHash      embedding
          │                    │
          └────────────┬───────┘
                       ▼
                Elasticsearch upsert
```

For uploaded/cropped queries:

```text
Browser
  ↓
CAM API
  ↓
validate temporary image/crop
  ↓
Visual Encoder
  ↓
query vector
  ↓
Elasticsearch tenant-scoped KNN
  ↓
rerank + diversify
  ↓
asset results
```

Encoder transport must remain swappable:

```text
LocalCpuVisualEncoder
RemoteVisualEncoder
```

Future migration should be possible via a configuration such as `VISUAL_ENCODER_URL` without changing public search APIs.

---

# 7. Component boundaries

## CAM API

Responsible for authentication, tenant resolution, authorization, request validation, query orchestration, filters, pagination, feature flags and response formatting.

Not responsible for loading a large ML model or performing long-running backfills.

## Visual Encoder

Responsible for deterministic preprocessing, EXIF normalization, crop, RGB conversion, resize, inference, vector normalization, model/version metadata and concurrency limits.

Conceptual interface:

```python
class VisualEncoder(Protocol):
    @property
    def descriptor(self) -> EncoderDescriptor: ...
    def encode_image(self, image: Image.Image) -> list[float]: ...
    def encode_text(self, text: str) -> list[float]: ...
```

Only implement `encode_text` if the approved model supports a shared text/image embedding space.

## Elasticsearch

Responsible for vector storage, ANN/KNN candidate retrieval, tenant filters and compatible metadata filters.

## Ranking

Keep retrieval and ranking separate:

```text
retrieve candidates
  ↓
remove invalid/unauthorized/suppressed
  ↓
score
  ↓
deduplicate
  ↓
diversify
  ↓
page
```

## Frontend

Responsible for query-image UX, crop rectangle, chips, result grid, Find Similar and discovery navigation.

---

# 8. Repository layout guidance

Paths below are proposals. VS-00 must inspect current repository conventions first.

Possible API module:

```text
apps/api/app/modules/visual_search/
├── __init__.py
├── router.py
├── schemas.py
├── service.py
├── repository.py
├── ranking.py
└── settings.py
```

Possible worker module:

```text
apps/worker/visual_search/
├── encoder.py
├── preprocess.py
├── fingerprint.py
├── indexer.py
└── tasks.py
```

The current repository contains job skeletons under `apps/worker/jobs`; Codex must determine the actual production image-worker implementation before creating new worker infrastructure.

Do not build a new queue/scheduler framework if one already exists.

---

# 9. Visual representations

## SHA-256

Exact content identity. Not semantic similarity.

## pHash

Near-duplicate detection for resize/recompression and limited transformations. Do not use it as the main Pinterest-like signal.

## Semantic image embedding

Main signal for garment, scene, composition, style and related visual concepts.

The exact model is chosen only after VS-00/VS-02 evaluation of:

- licensing;
- CPU performance;
- memory footprint;
- output dimension;
- image/text shared space;
- Python/runtime compatibility;
- real CAM relevance benchmark.

Candidates may include CLIP/SigLIP-family models, but no model is hardcoded before evaluation.

---

# 10. Deterministic preprocessing contract

1. reject invalid/non-image input;
2. safe decode;
3. EXIF orientation normalization;
4. convert to RGB;
5. enforce maximum decoded pixels;
6. apply crop if present;
7. resize according to model preprocessing;
8. normalize exactly as model requires;
9. compute embedding;
10. normalize vector if required by chosen similarity strategy.

Do not feed full 4000–8000 px images directly to a small vision transformer.

---

# 11. Crop contract

Public API uses normalized coordinates:

```json
{
  "x": 0.21,
  "y": 0.18,
  "width": 0.52,
  "height": 0.61
}
```

Validation:

```text
0 <= x < 1
0 <= y < 1
0 < width <= 1
0 < height <= 1
x + width <= 1
y + height <= 1
minimum crop size enforced
```

Server applies EXIF orientation before crop so frontend/backend coordinates refer to the same visual orientation.

---

# 12. Elasticsearch strategy

VS-00 must capture:

```text
exact Elasticsearch version
current index names
aliases
Search V3 mappings
index/reindex scripts
query implementation
tenant filter implementation
refresh/index lifecycle
```

Conceptual visual fields:

```text
asset_id
tenant_id
asset_type
visual_embedding_v1
visual_embedding_model
visual_embedding_model_version
visual_embedding_preprocess_version
visual_embedding_generated_at
sha256
visual_phash
width
height
aspect_ratio
```

VS-00 must choose one strategy:

### A. Extend existing search document

Pros: easy filters and hydration, fewer joins.

### B. Separate derived visual index

Pros: easier model rebuild and isolation from Search V3 mapping.

If B is used, response hydration and authorization must remain safe.

Changing model/dimension/normalization/preprocessing/distance requires `visual_embedding_v2` or a new versioned index.

---

# 13. Asset indexing lifecycle

```text
asset create/import
  ↓
eligible?
  ├── no → skipped
  └── yes
       ↓
     queued
       ↓
   processing
       ↓
decode + fingerprint
       ↓
   embedding
       ↓
ES upsert
       ↓
     ready
```

Failures support retry/backoff or permanent failed/skipped status.

Indexing must be idempotent using an identity equivalent to:

```text
tenant_id + asset_id + embedding_schema_version
```

Content changes require re-embedding. Metadata-only changes should not recompute vectors unnecessarily. Deleted/archived assets must not remain searchable.

---

# 14. Query types

## Existing asset

```text
asset_id
  ↓
authorize
  ↓
reuse stored vector
  ↓
KNN
  ↓
rerank
```

Do not invoke inference when a valid stored vector already exists.

## Uploaded image

```text
multipart upload
  ↓
auth + byte/type validation
  ↓
safe decode
  ↓
temporary representation
  ↓
encoder
  ↓
KNN
  ↓
discard temporary query data
```

## Crop

Existing asset crop and uploaded-image crop both require fresh inference on the cropped pixels. The global asset vector is not a valid crop representation.

## Image + text

Possible strategies:

```text
q = normalize(alpha * image_vector + beta * text_vector)
```

or image KNN followed by text/metadata rerank. Choose by benchmark; do not invent fusion weights without evaluation.

---

# 15. Conceptual API

Final paths must follow current CAM conventions.

Recommended endpoints:

```text
POST /api/search/visual/by-asset
POST /api/search/visual/upload
```

Existing asset request:

```json
{
  "asset_id": "asset_uuid",
  "crop": null,
  "text": null,
  "filters": {},
  "limit": 40,
  "cursor": null
}
```

With crop:

```json
{
  "asset_id": "asset_uuid",
  "crop": {
    "x": 0.20,
    "y": 0.15,
    "width": 0.55,
    "height": 0.65
  },
  "text": "outdoor",
  "filters": {},
  "limit": 40
}
```

Upload endpoint uses multipart form data.

Response should reuse current asset-summary/card DTOs where possible.

---

# 16. Upload security

Required:

- authenticated request;
- tenant context;
- maximum raw bytes;
- safe decode/type verification;
- maximum decoded pixel count;
- timeout;
- decompression-bomb protection;
- no user-controlled filesystem paths;
- generated temporary paths only when necessary;
- guaranteed cleanup;
- no public URL for query upload;
- no raw bytes in logs;
- no signed source URL in logs;
- ignore EXIF metadata except orientation.

Do not claim HEIC/AVIF support until installed decoders are verified.

---

# 17. Ranking and diversity

Stage 1 retrieves approximately 200–500 ANN candidates; exact numbers require benchmark.

Conceptual ranking:

```text
final_score =
    w_visual   * visual_similarity
  + w_text     * semantic_text_score
  + w_metadata * metadata_score
  + w_quality  * asset_quality_score
```

Then apply:

```text
duplicate suppression
diversification
business exclusions
```

Weights belong in one documented/tested configuration, not scattered magic constants.

V1 diversity may suppress over-representation by:

- exact/pHash duplicate cluster;
- identical content variants;
- extremely close vector cluster;
- burst/source grouping where useful.

Future option: MMR or cluster-aware diversification.

---

# 18. Refinement chips

Prefer existing trusted metadata:

```text
Quarter Zip
Nurse
Hospital
Embroidery
Navy
Lifestyle
Outdoor
Female
```

Possible sources:

1. existing tags;
2. product/category metadata;
3. existing trusted AI metadata;
4. common fields among top candidates.

Do not add a large generative captioning dependency only for chips in V1.

---

# 19. Pinterest-style frontend UX

Main search gets a visual-search entry:

```text
┌──────────────────────────────────────┐
│ Search creative assets...       [📷] │
└──────────────────────────────────────┘
```

Options:

```text
Upload reference image
Choose existing asset
```

Eligible assets get **Find visually similar**.

Visual query state shows the reference and refinement chips.

Crop mode requires:

- move;
- resize;
- reset/close;
- minimum size;
- normalized coordinate conversion;
- intentional/debounced request.

Never call the encoder on every pointer-move event.

Results must reuse the existing asset grid/card where practical and preserve selection, preview, download, detail, pagination and permissions.

Discovery loop:

```text
Asset A → Find Similar → Asset B → Find Similar → Asset C
```

Browser Back/history should behave sensibly.

---

# 20. Feature flags

Minimum:

```env
VISUAL_SEARCH_ENABLED=false
VISUAL_SEARCH_UPLOAD_ENABLED=false
```

Recommended:

```env
VISUAL_SEARCH_CROP_ENABLED=false
VISUAL_SEARCH_HYBRID_TEXT_ENABLED=false
VISUAL_SEARCH_BACKFILL_ENABLED=false
```

Reuse existing centralized feature-flag infrastructure when available.

Rollout:

```text
all flags off
  ↓
internal tenant allowlist
  ↓
Find Similar
  ↓
upload
  ↓
crop
  ↓
hybrid text
  ↓
broader rollout
```

---

# 21. Backfill strategy

Backfill must be:

- restart-safe;
- idempotent;
- batched;
- checkpointed;
- version-aware;
- rate limited;
- retryable;
- observable;
- safely stoppable.

Initial production profile:

```text
concurrency = 1
small batches
sleep/throttle between batches
off-hours where practical
```

Do not backfill the full library immediately after deployment.

---

# 22. Observability

Indexing metrics/log dimensions:

```text
visual_index_jobs_total
visual_index_jobs_failed
visual_index_jobs_pending
visual_index_duration
visual_embedding_duration
visual_embedding_skipped
visual_embedding_version
```

Query metrics:

```text
visual_search_requests_total
visual_search_errors_total
visual_search_kind
visual_query_embedding_duration
visual_knn_duration
visual_rerank_duration
visual_search_total_duration
visual_search_result_count
visual_search_empty_result
```

Also monitor encoder RSS/CPU, ES query latency, ES memory/disk impact and backfill queue depth.

Never log raw image content, secrets, signed URLs or cross-tenant asset details.

---

# 23. Search-quality benchmark

Create an internal relevance dataset, initially around 100–300 queries.

Cover:

- garment similarity;
- embroidery-heavy assets;
- same product/different scene;
- same scene/different product;
- same color/different object;
- near duplicates;
- crops;
- small crops;
- difficult lighting;
- lifestyle vs studio;
- no-good-match cases.

Human relevance labels:

```text
3 highly relevant
2 relevant
1 weakly related
0 irrelevant
```

Possible metrics:

```text
Recall@10
Precision@10
NDCG@10
empty-result rate
query latency
```

Model upgrades must be benchmarked against the current production model.

---

# 24. Testing strategy

## Unit tests

- crop validation;
- EXIF transform/crop;
- preprocessing determinism;
- vector normalization;
- fingerprint determinism;
- feature flags;
- ranking;
- duplicate suppression;
- diversity;
- cursor/pagination;
- encoder unavailable.

## Mandatory tenant-isolation tests

```text
tenant A query cannot return tenant B asset
tenant A cannot use tenant B asset as query
tenant A cannot crop tenant B asset
filters cannot bypass tenant isolation
cursor cannot cross tenant boundary
feature allowlist cannot cross tenant
```

## Elasticsearch integration tests

- mapping creation;
- vector insert;
- KNN with tenant filter;
- metadata filters;
- update;
- deletion;
- partially indexed assets;
- version migration behavior.

Mock-only tests are insufficient for ES vector syntax.

## API tests

- auth;
- malformed/oversized images;
- pixel limits;
- crop bounds;
- missing/unauthorized asset;
- unindexed asset;
- encoder unavailable;
- ES unavailable;
- successful by-asset/upload/crop searches.

## Frontend tests

- entry point;
- upload preview;
- crop state;
- API request;
- loading/empty/retry;
- Find Similar;
- discovery loop;
- Back navigation;
- disabled feature.

---

# 25. Failure behavior

Visual search is optional and must fail softly.

If visual infrastructure is unavailable:

```text
CAM core          usable
Search V3         usable
asset browsing    usable
visual search     unavailable/disabled
backfill          paused/retryable
```

Do not make CAM readiness fail only because optional visual search is unavailable unless a future product decision explicitly changes that behavior.

---

# 26. Rollback

`VISUAL_SEARCH_ENABLED=false` must disable user-visible visual behavior without breaking Search V3.

Do not delete the old search index during a visual-search deployment.

Prefer additive/backward-compatible DB changes only when absolutely required.

For model upgrades:

```text
v1 active
  ↓
create v2 mapping/index
  ↓
canary v2
  ↓
benchmark
  ↓
backfill v2
  ↓
switch read version
  ↓
observe
  ↓
retain v1 during rollback window
```

Never overwrite all v1 vectors in place and destroy rollback.

---

# 27. Definition of Done — V1

- [ ] Existing asset supports Find Similar.
- [ ] Authorized user can upload a reference image.
- [ ] Crop search works.
- [ ] Image + optional text refinement works.
- [ ] Existing metadata filters work.
- [ ] Tenant isolation is proven by integration tests.
- [ ] Search V3 regressions remain green.
- [ ] Visual search can be disabled immediately.
- [ ] Indexing is idempotent.
- [ ] Backfill is restart-safe and throttled.
- [ ] Model/schema version is recorded.
- [ ] Near-duplicate flooding is controlled.
- [ ] Query uploads are cleaned up.
- [ ] Metrics/diagnostics exist.
- [ ] VPS benchmark completed.
- [ ] Canary completed before broad rollout.
- [ ] Operations docs updated.
- [ ] No production deployment occurs without explicit authorization.

---

# 28. Task map

| Task | Goal | Depends on |
|---|---|---|
| VS-00 | Current-source audit + ADR | — |
| VS-01 | Foundation/config/contracts | VS-00 |
| VS-02 | Encoder/preprocessing/fingerprint | VS-01 |
| VS-03 | Elasticsearch vectors/retrieval | VS-02 |
| VS-04 | Asset indexing lifecycle | VS-03 |
| VS-05 | Existing-asset Find Similar | VS-04 |
| VS-06 | Upload reference search | VS-05 |
| VS-07 | Crop/region search | VS-06 |
| VS-08 | Pinterest-style frontend | VS-05/06/07 |
| VS-09 | Hybrid text/chips/ranking/diversity | VS-08 |
| VS-10 | Backfill/reindex tooling | VS-04 |
| VS-11 | Observability/diagnostics | VS-04–10 |
| VS-12 | Quality/performance/canary | all V1 |
| VS-20 | Object detection + multi-vector | V1 complete |
| VS-21 | Learned ranking/personalization | sufficient usage data |

---

# 29. CODEX TASK VS-00 — Current-state audit and ADR

## Goal

Audit current `main` before implementation and create the repository-specific ADR.

## Deliverable

Create/update:

```text
docs/plans/VISUAL_SEARCH_IMPLEMENTATION.md
```

Document:

1. actual Search V3 code paths;
2. exact Elasticsearch version;
3. current indices/aliases/mappings;
4. current tenant-filter mechanism;
5. asset indexing lifecycle;
6. real production image-worker code path;
7. current frontend search components;
8. feature flags/config patterns;
9. asset file/thumbnail access;
10. current metrics/tests/deploy conventions;
11. current-index vs separate-visual-index decision;
12. initial encoder candidates + benchmark plan;
13. embedding version strategy;
14. backfill approach;
15. exact proposed files for VS-01 through VS-03.

## Codex prompt

```text
You are implementing VS-00 for Pinterest-style Visual Search in:
BaoNghiaNghia/creative-asset-manager

AUDIT/ADR ONLY. Do not implement visual search yet.

Before doing anything:
1. Fetch latest main and record HEAD.
2. Read root AGENTS.md.
3. Read docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md.
4. Inspect current source; do not assume paths from the guide.

Audit:
- Search V3 entry points/services/ES repositories/mappings;
- exact ES server/client version;
- index/alias/reindex lifecycle;
- tenant filtering;
- asset lifecycle/source access;
- actual production image worker/queue/retry system;
- frontend search routes/components;
- feature flag/config infrastructure;
- observability/testing/deploy conventions.

Decide and justify:
A) extend existing search index, or
B) create a separate versioned visual index.

Evaluate at least two CPU-compatible image/text embedding candidates by license, Python compatibility, output dimension, memory footprint, CPU inference, shared text/image space and operational complexity. If benchmarking cannot safely be done now, define the VS-02 benchmark.

Deliver docs/plans/VISUAL_SEARCH_IMPLEMENTATION.md with concrete source references, architecture diagram, decision table, risks/open questions and exact proposed files for VS-01–VS-03.

Do not deploy. Do not modify production. Do not add migrations. Do not install infrastructure.

Final report: HEAD, changed files, decisions, unknowns, checks and whether VS-01 is unblocked.
```

---

# 30. CODEX TASK VS-01 — Foundation/config/contracts

```text
Implement VS-01 from the approved visual-search ADR.

Start from latest main on a feature branch. Read AGENTS.md and both visual-search documents. Inspect current config/router/service/DI/feature-flag conventions.

Implement only:
- visual-search feature flags/settings;
- typed encoder abstraction;
- embedding descriptor/version contract;
- normalized crop request schemas;
- response contracts reusing current asset DTOs where practical;
- domain exceptions;
- service/provider skeleton;
- unit tests.

All visual flags default OFF.
Do not load a model.
Do not add ES vector mapping yet.
Do not build frontend UI.
Do not change Search V3 behavior.
Do not deploy.

Acceptance: application boots with visual search disabled; current search tests remain green; no FastAPI request path imports a heavy ML framework.
```

---

# 31. CODEX TASK VS-02 — Encoder/preprocessing/fingerprint

```text
Implement VS-02 using the encoder strategy approved by VS-00.

Read current image decoding/thumbnail/source-access code first.

Implement:
- safe image decode;
- EXIF orientation normalization;
- RGB conversion;
- decoded pixel limit;
- normalized crop support;
- deterministic model preprocessing;
- image embedding;
- optional text embedding only if approved;
- vector normalization matching ES similarity;
- model/preprocess/schema descriptor;
- exact hash;
- pHash if approved;
- model load once per process;
- concurrency guard = 1 for current VPS profile;
- tests;
- repeatable benchmark command.

Benchmark at least model/version/dimension, cold-load time, RSS after load, single encode latency, sequential latency and peak RSS.

Do not put the model in FastAPI request process. Do not run production backfill. Do not deploy.
```

---

# 32. CODEX TASK VS-03 — Elasticsearch vector retrieval

```text
Implement VS-03 using the exact Elasticsearch version and index strategy approved in the ADR.

Implement:
- versioned vector mapping;
- deterministic document identity;
- tenant_id + asset_id;
- model/preprocess/version metadata;
- vector upsert/delete;
- KNN/ANN candidate retrieval;
- tenant filter DURING candidate retrieval;
- supported existing metadata filters;
- integration tests against a compatible ES instance.

Mandatory tests:
- cross-tenant retrieval guard;
- dimension mismatch;
- unindexed asset;
- deleted/hidden asset behavior;
- metadata filters;
- deterministic fixture candidates.

Do not destructively switch production aliases, reindex production, build frontend or deploy.
```

---

# 33. CODEX TASK VS-04 — Asset visual-index lifecycle

```text
Implement VS-04 by integrating with the ACTUAL current production job/indexing architecture. Do not create a parallel queue framework.

For eligible image assets:
- obtain bytes/derivative through current secure asset access;
- preprocess;
- fingerprint;
- embed;
- upsert versioned visual record;
- record/log outcome according to approved state strategy.

Handle retries idempotently, deletion/archive, content replacement, unsupported/corrupt images, model/schema upgrade and source failures.

Metadata-only changes must not recompute image vectors unnecessarily.
Visual indexing must remain optional and must not block core ingestion.

Add lifecycle/idempotency tests.
Do not run production backfill. Do not deploy.
```

---

# 34. CODEX TASK VS-05 — Existing asset Find Similar

```text
Implement VS-05 backend Find Similar.

Requirements:
- authenticated endpoint following current CAM conventions;
- current tenant from existing auth context;
- authorize query asset;
- reuse stored vector when ready;
- tenant-scoped KNN;
- exclude query asset;
- existing supported filters;
- hydrate results using current asset DTO/authorization;
- bounded limit/cursor;
- feature flag;
- explicit pending/unavailable behavior;
- latency metrics/log hooks.

Do not expose fake calibrated similarity percentages.
Do not infer tenant from request body.
Do not invoke encoder when stored vector is valid.

Mandatory tests: unauthorized/cross-tenant query asset, zero cross-tenant results, own asset excluded, missing vector, disabled feature, filters, pagination.

No frontend. No deploy.
```

---

# 35. CODEX TASK VS-06 — Upload reference image search

```text
Implement VS-06 authenticated upload-reference visual search.

Security:
- bound upload bytes;
- verify by safe decode, not extension;
- decoded pixel limit;
- EXIF orientation;
- actual decoder allowlist;
- generated temp paths only if needed;
- guaranteed cleanup;
- never create a public asset from query upload;
- never log raw bytes or sensitive source data.

Flow:
upload -> validate/decode -> encoder -> tenant-scoped KNN -> rerank -> current asset DTO.

Use concurrency limits suitable for the VPS. Return a controlled unavailable/capacity response rather than exhausting CAM.

Reuse VS-05 ranking/pagination semantics.
Add unit/API/integration tests.
Do not persist query images without an explicit future requirement. Do not deploy.
```

---

# 36. CODEX TASK VS-07 — Crop/region visual search

```text
Implement VS-07 Pinterest Lens-style crop search.

Support normalized x/y/width/height for:
1) existing authorized asset;
2) uploaded reference image.

Server must normalize EXIF orientation first, validate crop, enforce minimum crop size, crop server-side, encode cropped pixels and perform tenant-scoped retrieval.

Do not reuse global asset embedding for a crop query.

If caching is introduced, key by tenant + immutable content identity + crop + embedding schema + text/filters with bounded TTL/size.

Test edge/out-of-bounds/tiny crops, EXIF orientation, unauthorized asset, tenant isolation and upload cleanup.
No deploy.
```

---

# 37. CODEX TASK VS-08 — Pinterest-style frontend

```text
Implement VS-08 frontend after inspecting current React/Vite search, asset grid/card/detail, API client, state and feature-flag patterns.

Implement:
- visual-search entry in search UI;
- Find Similar on eligible asset card/detail;
- reference preview;
- upload/choose existing asset;
- crop mode with movable/resizable rectangle;
- normalized crop conversion;
- request only after pointer-up/debounce;
- result grid using existing asset cards;
- loading/empty/unavailable/retry states;
- discovery from a result;
- Back/history behavior;
- feature flags;
- accessibility where practical.

Do not display raw cosine as a similarity percentage.
Do not duplicate the entire asset grid/DTO.
Do not bypass existing permissions/download paths.
Add frontend tests. Do not deploy.
```

---

# 38. CODEX TASK VS-09 — Hybrid text/chips/ranking/diversity

```text
Implement VS-09 using the fusion strategy approved by ADR/benchmark.

Implement:
- optional text refinement;
- selected existing metadata filters;
- refinement chips from trusted metadata;
- duplicate suppression with exact/perceptual signals;
- deterministic basic diversity;
- centralized configurable ranking weights;
- tests/benchmark fixtures.

Fixtures should include same garment/different background, same scene/wrong garment, 'outdoor' refinement, supported color refinement and near-duplicate suppression.

Keep user-facing numeric similarity percentages disabled until calibration.
No deploy.
```

---

# 39. CODEX TASK VS-10 — Backfill/reindex tooling

```text
Implement restart-safe visual-search backfill/reindex TOOLING ONLY. Do not execute a production-wide backfill.

Requirements:
- explicit schema/version;
- dry-run;
- tenant scope/allowlist;
- deterministic checkpoint;
- configurable batch size/max assets;
- concurrency default 1;
- delay/throttle;
- skip already-correct vectors;
- retry/backoff;
- resume after restart;
- progress/failure visibility;
- graceful stop.

Provide operator examples, safe first-canary command, rollback/stop behavior and tests.
Do not deploy.
```

---

# 40. CODEX TASK VS-11 — Observability/diagnostics

```text
Implement VS-11 using existing CAM logging/metrics conventions.

Instrument indexing count/success/failure/duration, embedding duration, query embedding duration, KNN duration, rerank duration, total latency, result count/empty result, capacity rejection, backfill backlog/progress and embedding schema/model version.

Add authenticated/admin-safe diagnostics only if consistent with current operations modules. Never expose raw vectors, secrets, query images or another tenant's assets.

Document how to verify flags, mapping/alias, assets by embedding version, indexing failures and encoder resource use.
No deploy.
```

---

# 41. CODEX TASK VS-12 — Quality/performance/canary

```text
Execute VS-12 release-readiness validation. Do not broadly enable production visual search.

Complete:
- curated relevance fixtures;
- evaluation report;
- tenant-isolation release gate;
- Search V3 regression gate;
- performance benchmark/runbook;
- canary checklist;
- rollback checklist.

Measure encoder RSS/CPU, existing-asset p50/p95, upload/crop p50/p95, KNN p50/p95, ES memory impact and backfill resource impact.

Produce PASS / PASS WITH CHANGES / BLOCKED with quality metrics, latency, resource impact, known failures, feature-flag rollout and rollback.

Do not change production configuration without separate explicit authorization.
```

---

# 42. Future tasks

## VS-20 — object detection + multi-vector

After V1 proves value, add automatic clickable regions and vectors for garment/product/person/scene/embroidery.

## VS-21 — learned/personalized ranking

Only after enough interaction data exists. Potential signals include clicks, opens, downloads, saves/collections, refinements and discovery continuation.

Do not accidentally pool private tenant behavior across tenants without an explicit privacy/learning design.

---

# 43. Branch/PR slicing

Recommended:

```text
feature/visual-search-00-adr
feature/visual-search-01-foundation
feature/visual-search-02-encoder
feature/visual-search-03-elasticsearch
feature/visual-search-04-indexing
feature/visual-search-05-find-similar
feature/visual-search-06-upload
feature/visual-search-07-crop
feature/visual-search-08-frontend
feature/visual-search-09-ranking
feature/visual-search-10-backfill
feature/visual-search-11-observability
feature/visual-search-12-readiness
```

Do not mix unrelated Dola/deployment changes into these PRs.

---

# 44. Suggested commits

```text
docs(search): add visual search architecture ADR
feat(search): add visual encoder contracts
feat(search): index visual embeddings in Elasticsearch
feat(search): add tenant-scoped visual KNN retrieval
feat(search): add find-similar endpoint
feat(search): add visual reference upload query
feat(search): add crop-region visual query
feat(client): add visual search discovery UI
feat(search): add hybrid reranking and diversity
feat(search): add visual backfill tooling
obs(search): add visual search metrics
test(search): add visual relevance benchmark
```

---

# 45. Privacy/security checklist

Before canary:

- [ ] query asset authorization uses current CAM authorization;
- [ ] query uploads require auth;
- [ ] tenant filter occurs during retrieval;
- [ ] response hydration reuses existing authorization/sanitization;
- [ ] raw vectors are not exposed to ordinary clients;
- [ ] query bytes are not logged;
- [ ] temp files are deleted;
- [ ] source signed URLs/secrets are not logged;
- [ ] no arbitrary URL fetching is introduced;
- [ ] crop cannot trigger unbounded decode;
- [ ] request sizes are bounded;
- [ ] caches cannot cross tenants;
- [ ] diagnostics are protected;
- [ ] feature flags can immediately disable visual search.

---

# 46. Operations runbook requirements

Final production docs must answer:

```text
How do I verify the visual index exists?
How do I see active embedding schema/model?
How many assets are ready/failed?
How do I pause/resume backfill?
How do I disable visual search?
How do I switch embedding version?
How do I rollback?
How do I detect encoder memory pressure?
How do I verify tenant safety?
How do I remove stale derived indexes after rollback window?
```

---

# 47. Recommended first production rollout

```text
1. deploy code with all visual flags OFF
2. create/verify vector index and encoder runtime
3. index a small internal/canary tenant only
4. enable Find Similar for that tenant
5. observe CAM API, ES, encoder RSS/CPU, errors and relevance
6. enable upload
7. enable crop
8. enable hybrid text/refinements
9. expand gradually
```

Do not broadly enable after the first successful query.

---

# 48. Current-VPS warning

Avoid running all of these together without measurements:

```text
Dola/Chromium render
+
visual embedding backfill
+
large Elasticsearch reindex
```

If visual inference becomes sustained, migrate only the encoder to another VPS/GPU:

```text
CAM VPS
  │ internal authenticated call
  ▼
Visual Encoder VPS/GPU
```

That is why the encoder abstraction is mandatory from V1.

---

# 49. Master Codex execution prompt

Replace `<TASK_ID>` with one task only.

```text
You are working on Pinterest-style Visual Search for:
BaoNghiaNghia/creative-asset-manager

Execute ONLY task <TASK_ID> from:
docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md
and:
docs/plans/VISUAL_SEARCH_IMPLEMENTATION.md
(if VS-00 has created it).

Mandatory startup:
1. Fetch latest main.
2. Record exact HEAD SHA.
3. Read root AGENTS.md.
4. Read the master visual-search guide.
5. Read current visual-search ADR if present.
6. Inspect every module you intend to modify.
7. Compare current source with plan assumptions.
8. Adapt to current source; do not create duplicate infrastructure.

Global constraints:
- preserve multi-tenant authorization;
- tenant filtering must happen during visual candidate retrieval;
- preserve Search V3;
- new user-visible visual behavior remains behind feature flags until rollout;
- PostgreSQL remains authoritative;
- Elasticsearch remains derived search state;
- do not add another vector database;
- do not load a heavy model in FastAPI request process;
- embedding model/schema/preprocessing must be versioned;
- current VPS is resource constrained: encoder/backfill concurrency defaults to 1 unless measured evidence supports more;
- do not expose fake similarity percentages;
- do not run a production-wide backfill;
- do not deploy;
- do not modify production config;
- do not modify unrelated Dola work;
- do not invent code paths;
- add/update tests for every behavioral change.

Security release blockers:
- cross-tenant query asset access;
- cross-tenant result leakage;
- unbounded image decode/upload;
- temp-file leakage;
- feature-flag bypass;
- Search V3 regression.

Implementation rules:
- smallest coherent change for the selected task;
- reuse current application patterns;
- keep business logic out of routers/components;
- keep ranking separately testable;
- indexing/retries idempotent;
- preserve rollback compatibility.

Before finishing:
1. run narrow relevant tests;
2. run required lint/type/format checks;
3. run Search V3 regression checks when search code changed;
4. run tenant-isolation tests;
5. inspect git diff for unrelated changes.

Final report:
# <TASK_ID> Result
## Baseline
- main HEAD:
- branch:
## Changes
- file / reason
## Architecture decisions
## Security
- tenant isolation
- upload safety
- feature flags
## Tests
- command / result
## Performance/resource impact
## Risks/follow-ups
## Acceptance criteria
## Next task readiness
READY / BLOCKED

Do not begin the next task automatically.
```

---

# 50. VS-00 reconnaissance checklist

Codex must answer with exact source references:

```text
[ ] What file defines the FastAPI app?
[ ] Where are routers registered?
[ ] Where is Search V3 implemented?
[ ] What Elasticsearch client/version is used?
[ ] What ES server version is deployed/configured?
[ ] What index/alias names exist?
[ ] Where are mappings created?
[ ] Where is tenant filtering injected?
[ ] How are asset DTOs hydrated?
[ ] How are thumbnails/source files accessed?
[ ] What is the production image worker implementation?
[ ] How are jobs queued?
[ ] How are retries/idempotency handled?
[ ] Where are feature flags implemented?
[ ] Where are frontend search routes/components?
[ ] Where are asset card/detail actions?
[ ] What test fixtures exist for tenant/search/assets?
[ ] How are metrics/logs emitted?
[ ] How does deploy build Python dependencies?
[ ] Is adding ML dependencies to the current worker venv safe?
```

If the existing worker environment is too tightly coupled, consider a small separate visual-encoder venv/service while keeping CAM as orchestrator. Do not create it until VS-00/VS-02 justifies it.

---

# 51. Decision priority

When uncertain, optimize in this order:

1. tenant isolation/security;
2. CAM availability;
3. rollback safety;
4. relevance quality;
5. operational simplicity;
6. query latency;
7. indexing throughput;
8. engineering elegance.

A slow safe backfill is better than a fast backfill that destabilizes CAM.

---

# 52. Example user journeys

## Existing asset

```text
open nurse lifestyle asset
  ↓
Find Similar
  ↓
reuse stored vector
  ↓
same/related quarter zip + nurse/lifestyle results
  ↓
click another asset
  ↓
Find Similar
```

## Upload

```text
upload reference
  ↓
encode
  ↓
tenant-scoped search
  ↓
discovery grid
```

## Crop garment

```text
select/upload image
  ↓
crop garment
  ↓
server crop
  ↓
region embedding
  ↓
garment-focused results
```

## Crop + text

```text
crop = quarter zip
text = outdoor
  ↓
visual retrieval + text/metadata refinement
  ↓
related garment in outdoor creative
```

---

# 53. Domain advantage for CAM

Future CAM visual search can outperform generic discovery for its private creative domain by combining visual similarity with structured dimensions:

```text
GARMENT
EMBROIDERY
PRODUCT TYPE
PERSON ROLE
POSE
ENVIRONMENT
COMPOSITION
COLOR PALETTE
PHOTOGRAPHIC STYLE
ASPECT RATIO
CAMPAIGN
PRODUCT / SKU
SOURCE
```

Example:

```text
reference garment crop
+ outdoor
+ 1:1
+ lifestyle
+ product family X
```

---

# 54. Architectural anti-patterns

Reject unless a future ADR explicitly changes the decision:

```text
FastAPI imports a heavy PyTorch vision model
Qdrant + Elasticsearch for V1
search all tenants then filter
save every uploaded query image permanently
one unversioned embedding field forever
backfill entire library immediately
raw cosine * 100 = similarity percentage
object detection required before Find Similar
second duplicated asset-grid/search frontend
```

---

# 55. Minimal valuable milestone

The first useful internal demo is:

```text
global embeddings
+
tenant-scoped Elasticsearch KNN
+
existing asset Find Similar
```

This delivers real value before upload, crop, text refinement or object detection.

---

# 56. Release sign-off questions

Before requesting production rollout approval, answer:

```text
1. What exact visual model/version is active?
2. What vector dimension?
3. What measured encoder RSS?
4. What is Find Similar p95?
5. What are upload/crop p95?
6. Is tenant filtered during KNN retrieval?
7. What test proves no cross-tenant result?
8. How many assets are indexed?
9. How is backfill paused?
10. How is visual search immediately disabled?
11. How is Search V3 verified?
12. What ES memory/disk increase occurred?
13. What is rollback?
14. Which old model/index remains for rollback?
15. Has relevance been human-reviewed?
```

If these answers are unavailable, broad production rollout is not ready.

---

# 57. Final development order

```text
VS-00  Audit/ADR
  ↓
VS-01  Contracts/config
  ↓
VS-02  Encoder
  ↓
VS-03  Elasticsearch vectors
  ↓
VS-04  Lifecycle indexing
  ↓
VS-05  Find Similar       ← first product demo
  ↓
VS-06  Upload
  ↓
VS-07  Crop               ← Pinterest-like core
  ↓
VS-08  Frontend
  ↓
VS-09  Hybrid/discovery   ← Pinterest-like quality
  ↓
VS-10  Backfill
  ↓
VS-11  Observability
  ↓
VS-12  Release readiness
```

After V1:

```text
usage/relevance data
  ↓
VS-20 object/multi-vector
  ↓
scale/quality data
  ↓
remote GPU encoder if justified
  ↓
VS-21 learned ranking if justified
```

---

# 58. Final architectural statement

The architecture to preserve is:

```text
Pinterest-like UX
        │
        ▼
CAM-owned secure visual-search API
        │
        ├── full image
        ├── existing asset
        ├── crop
        └── optional text
        │
        ▼
versioned visual encoder
        │
        ▼
tenant-filtered Elasticsearch ANN/KNN
        │
        ▼
reranking + duplicate suppression + diversity
        │
        ▼
existing CAM asset grid
        │
        ▼
continuous visual discovery
```

The system is deliberately designed so that:

- CAM remains usable if visual search is disabled;
- Search V3 remains intact;
- the model can change without destroying rollback;
- the encoder can later move to another VPS/GPU;
- no additional vector DB is needed initially;
- crop/discovery can be added incrementally;
- tenant isolation exists from the first candidate retrieval;
- current VPS resource constraints are respected.

This is the architecture to preserve while implementing the Codex tasks above.
