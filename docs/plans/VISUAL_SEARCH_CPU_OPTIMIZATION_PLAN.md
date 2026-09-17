# Visual Search — CPU-Only Optimization Plan

> **Repository:** `BaoNghiaNghia/creative-asset-manager`  
> **Document date:** 2026-09-17  
> **Status:** Architecture/operations plan. This document does **not** authorize destructive production mutation, production-wide rollout, unbounded backfill, service restart, or deletion of previous visual indices.  
> **Companion documents:** `docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md`, `docs/plans/VISUAL_SEARCH_FULL_CORPUS_OPERATIONS_PLAN.md`, `docs/architecture/SEARCH.md`

---

## 1. Purpose

This plan defines the next Visual Search optimization track for the current CPU-only VPS. The goal is to reduce image-search latency and improve concurrent Visual Search behavior without adding another heavyweight vision model or requiring a GPU.

The optimization target is:

```text
reference image / crop
        ↓
prepared 224px image
        ↓
SigLIP2 Base 224
        ↓
OpenVINO CPU backend
        ↓
bounded inference queue
        ↓
versioned embedding
        ↓
Elasticsearch ANN/KNN
        ↓
rank / hydrate / page
```

This plan intentionally optimizes the current single-encoder architecture before considering a second embedding model.

---

## 2. Current VPS resource envelope

The 2026-09-17 production inspection reported approximately:

```text
OS               Ubuntu 24.04 LTS
CPU              3 vCPU
CPU model        Intel Xeon Platinum 8272CL
CPU ISA          AVX2, AVX-512, AVX512-VNNI available
GPU              none
RAM              7.8 GiB total
RAM available    ~3.0 GiB at inspection
Swap             2.0 GiB
Root disk         30 GiB
Disk free         ~7.9 GiB at inspection
Virtualization   KVM
```

Do not place the public IP address, production credentials, tokens, or service secrets in this document.

Operational implications:

- CPU cores, not model availability, are the primary inference constraint.
- RAM headroom is too small for casually keeping multiple heavyweight encoders resident.
- Disk headroom is too small for uncontrolled Hugging Face caches, duplicate snapshots, Docker layers, Elasticsearch growth, and exported model artifacts.
- Elasticsearch, API, workers, and the visual encoder share the same small CPU budget.

---

## 3. Non-negotiable optimization rules

1. Keep **one heavyweight visual encoder model resident per production encoder process** on the current VPS.
2. Keep **one encoder process** initially. Do not scale Uvicorn workers by duplicating the model in memory.
3. Do not run SigLIP and SigLIP2 concurrently in memory as a long-lived shadow deployment on this VPS.
4. Embeddings remain versioned derived data. A model/preprocess/runtime change that changes the embedding contract must use a compatible versioned descriptor and index.
5. PostgreSQL remains authoritative; Elasticsearch remains rebuildable.
6. Tenant/access filtering remains part of candidate retrieval.
7. Search V3 must remain usable when Visual Search is disabled or encoder capacity is exhausted.
8. Backfill remains bounded, restart-safe, and subordinate to interactive production traffic.
9. Do not add MobileCLIP, DINO, YOLO, SAM, GroundingDINO, or another heavyweight model merely to improve latency. Additional models require a separate capability/relevance case.
10. Production model startup must use a pinned local snapshot. No floating model download during normal service startup.

---

## 4. Encoder target: SigLIP2 Base 224

The preferred migration target is the Base/224 member of the SigLIP2 family so the system does not simultaneously increase model class and input resolution.

Target logical descriptor:

```text
encoder_name              google/siglip2-base-patch16-224
encoder_revision          <exact pinned revision>
embedding_schema_version  visual_embedding_v2
preprocess_version        <explicit pinned preprocess contract>
similarity                cosine
```

The exact dimension and revision must be read from the pinned model/runtime during implementation and encoded in `EmbeddingDescriptor`; do not copy values by assumption.

Migration requirements:

```text
v1 SigLIP index     = previous / rollback-capable
v2 SigLIP2 index    = building → validating → active
```

Never query a SigLIP v1 index with a SigLIP2 query vector or the reverse.

The previous v1 model files may remain on disk for rollback, but the v1 model does not need to remain resident in RAM after v2 activation.

---

## 5. Runtime target: OpenVINO first on this VPS

Because the current host is an Intel Xeon CPU with AVX2/AVX-512/VNNI and no GPU, the preferred CPU inference path is:

```text
SigLIP2 model
     ↓
verified OpenVINO export / conversion
     ↓
OpenVINO CPU plugin
     ↓
1 compiled model
     ↓
bounded async inference
```

ONNX Runtime remains an acceptable fallback if the approved SigLIP2 export/runtime path proves incompatible or materially worse on this host.

### Initial resource settings

Start conservatively:

```text
encoder processes         1
resident models           1
OpenVINO streams          1
inference threads         2
active inference          1
pending queue             8 (initial candidate)
queue wait timeout        5 seconds (initial candidate)
```

These are starting values, not permanent constants. Measure before increasing them.

Do not begin with `streams=3` merely because the VPS exposes 3 vCPUs. The API and Elasticsearch also require CPU time.

### No multi-process model replication

Avoid:

```text
uvicorn --workers 4
  ├─ model copy 1
  ├─ model copy 2
  ├─ model copy 3
  └─ model copy 4
```

Prefer:

```text
1 process
1 compiled model
bounded inference queue
controlled inference requests
```

---

## 6. Replace immediate busy rejection with bounded queueing

The current isolated encoder uses one global capacity lock and rejects a request with `503 encoder busy` when that lock is already held. This protects the process from uncontrolled concurrency but provides poor multi-user behavior.

Target behavior:

```text
request A ─┐
request B ─┤
request C ─┤→ bounded queue → encoder capacity → inference
request D ─┘
```

Required rules:

- Capacity remains bounded.
- Queue length remains bounded.
- Queue wait has a timeout.
- Cancellation disconnects/cleans up safely.
- Saturation must not cause unbounded memory growth.
- Saturation is observable separately from provider/model failures.
- Backfill work must not monopolize the queue ahead of interactive search.

Suggested error distinction:

```text
visual_encoder_queue_full
visual_encoder_queue_timeout
visual_encoder_unavailable
visual_encoder_contract_violation
```

Do not simply replace `asyncio.Lock()` with a large `Semaphore(N)` while leaving PyTorch/OpenVINO thread pools unconstrained. That can oversubscribe the 3-vCPU host and increase p95 latency.

---

## 7. INT8 optimization track

The CPU exposes VNNI-capable instructions, so INT8 is a high-value candidate after the OpenVINO FP32 baseline is correct.

Sequence:

```text
SigLIP2 + OpenVINO baseline
          ↓
contract + relevance sanity
          ↓
INT8 conversion/calibration
          ↓
50–100 representative CAM queries
          ↓
latency / throughput / relevance comparison
          ↓
promote only if quality gate passes
```

The INT8 artifact must receive its own explicit runtime/preprocess/model metadata if it changes the embedding contract materially. Do not silently replace an embedding implementation under the same descriptor if vectors are not reproducibly compatible.

Minimum relevance sanity set should include:

```text
same product
same design / different photo
crop → full image
near-duplicate / compressed derivative
hard negative with similar color/background
text → image refinement
```

Do not optimize only for requests/second while ignoring retrieval quality.

---

## 8. Query embedding cache

Add a small bounded cache before model inference.

Recommended identity:

```text
prepared_image_sha256
+
embedding_schema_version
+
preprocess_version
```

For text encoding:

```text
normalized_text_hash
+
embedding_schema_version
+
preprocess_version
```

Cache rules:

- Cache derived embeddings, never credentials or signed URLs.
- Do not store raw upload bytes merely for caching.
- Bound entry count and memory usage.
- Clear naturally across incompatible embedding-schema changes.
- Record hit/miss metrics.

An initial in-process LRU is sufficient. Do not introduce Redis solely for this optimization unless a later multi-process architecture justifies it.

---

## 9. SHA and pHash fast paths

Exact SHA-256 remains useful for exact-content identity and duplicate suppression.

Optional pHash may be added for near-duplicate detection such as resized or recompressed derivatives:

```text
image
 ├─ SHA-256 → exact identity
 ├─ pHash   → near-duplicate signal
 └─ SigLIP2 → semantic visual similarity
```

pHash is not a replacement for semantic embedding and should not be described as an AI model.

Do not use pHash as a generic shortcut for arbitrary semantic image search. Its value is duplicate/derivative handling and optional near-duplicate fast paths.

---

## 10. Elasticsearch KNN optimization

The current Visual Search projection uses a dedicated versioned Elasticsearch `dense_vector` index with cosine similarity and tenant-scoped KNN filters. Preserve that architecture.

### Verify deployed Elasticsearch version first

Before changing vector index options, record:

```text
Elasticsearch version
current visual physical index
current visual read alias
current vector mapping
current vector dimension
current index size
current JVM heap / memory pressure
```

Do not assume an `index_options` type is supported by the deployed cluster.

### Quantized HNSW candidate

If the deployed Elasticsearch version supports it and the relevance/rollback gate passes, create a **new versioned visual index** using `int8_hnsw` rather than modifying the active physical index in place.

Conceptual mapping:

```json
{
  "visual_embedding": {
    "type": "dense_vector",
    "dims": "<descriptor dimension>",
    "index": true,
    "similarity": "cosine",
    "index_options": {
      "type": "int8_hnsw"
    }
  }
}
```

Activation remains alias-based and rollback preserves the previous index.

### Retrieval breadth is not UI page size

Preserve the full-corpus plan requirement to separate:

```text
ANN num_candidates
retrieval K
UI page size
```

Do not globally lower retrieval breadth just to improve one latency sample. Benchmark a bounded matrix on real CAM data.

Suggested benchmark matrix:

```text
A  K=40   num_candidates=160   page=20/40
B  K=80   num_candidates=240   page=20/40
C  K=120  num_candidates=320   page=20/40
D  K=200  num_candidates=500   page=40      # full-corpus quality candidate
```

For every candidate capture:

```text
KNN p50/p95
request p50/p95
CPU load
Elasticsearch memory pressure
result count
relevance / hard-negative review
```

Use the smallest retrieval breadth that satisfies the agreed relevance gate.

---

## 11. Interactive search must outrank backfill

On the current VPS, backfill concurrency remains 1 initially.

Priority model:

```text
interactive upload/crop/text search
          ↓ higher priority
encoder capacity
          ↑ lower priority
visual-index backfill
```

A full-corpus backfill must pause/throttle when any of these are sustained:

```text
encoder queue saturation
interactive p95 above threshold
high system load
low available RAM
meaningful swap growth
Elasticsearch memory pressure
low disk headroom
```

Do not execute an unbounded full-corpus re-embedding job immediately after a model migration.

Before a large v2 backfill, report:

```text
eligible assets
current compatible v1/v2 coverage
jobs required
measured encoder images/sec
available RAM
swap usage
root disk free
Elasticsearch disk usage
estimated new index/model artifact footprint
```

---

## 12. Disk and RAM operational gates

Current measured disk headroom (~7.9 GiB free) is marginal for retaining old/new model snapshots plus Docker/Elasticsearch growth.

Before keeping multiple model/index artifacts during migration:

- inspect Docker disk usage;
- inspect model/cache directories;
- inspect Elasticsearch storage;
- remove only verified disposable caches/layers;
- prefer expanding the root disk rather than deleting rollback artifacts.

Operational target:

```text
preferred free disk before migration/backfill: 12–15 GiB or more
```

If practical, expand the VPS root disk from 30 GiB to roughly 50–60 GiB before large visual-corpus growth.

RAM rules:

- Avoid model process replication.
- Avoid running v1 and v2 models resident together for long periods.
- Treat sustained swap growth during interactive search as a performance failure signal.
- Measure encoder RSS separately from API and Elasticsearch memory.

---

## 13. Observability additions

The existing Visual Search timing stages remain authoritative. Extend operations evidence with CPU-specific capacity metrics:

```text
encoder_queue_depth
encoder_queue_wait_ms p50/p95/max
encoder_queue_full count
encoder_queue_timeout count
encoder_active_inference
encoder_requests/sec
encoder RSS
system load
available RAM
swap used/delta
OpenVINO stream count
OpenVINO inference thread count
embedding cache hit/miss
```

Retain existing stages:

```text
request_total_ms
prepare_image_ms
encode_image_ms
encode_text_ms
knn_ms
rank_ms
hydrate_ms
```

Do not log raw images, vectors, tenant identifiers, signed URLs, authorization headers, secrets, or raw sensitive text solely for performance diagnostics.

---

## 14. Implementation phases

### VS-CPU-00 — Baseline and compatibility audit

- record deployed commit and Elasticsearch version;
- record current visual descriptor/index aliases;
- capture encoder/API/ES RSS and CPU;
- capture current image/text encode p50/p95;
- capture KNN and total request p50/p95;
- verify disk/RAM/swap headroom;
- no behavior change.

### VS-CPU-01 — SigLIP2 v2 migration foundation

- pin exact local SigLIP2 Base 224 snapshot;
- define `visual_embedding_v2` descriptor;
- preserve v1 index/rollback;
- verify `encode_image` and `encode_text` contract;
- no destructive v1 cleanup.

### VS-CPU-02 — OpenVINO baseline

- export/convert the pinned SigLIP2 model using a reproducible build/setup step;
- run one encoder process and one resident model;
- start with 2 inference threads and 1 stream;
- compare correctness and latency against the reference implementation;
- preserve fallback path until release review.

### VS-CPU-03 — Bounded inference queue

- replace immediate busy rejection with bounded queueing;
- add timeout/full error classes;
- prioritize interactive requests over backfill;
- add saturation metrics and tests.

### VS-CPU-04 — INT8 candidate

- build INT8 artifact appropriate to the approved OpenVINO path;
- run relevance sanity set plus latency/throughput comparison;
- promote only after contract and relevance review.

### VS-CPU-05 — Elasticsearch ANN tuning

- verify supported vector index options;
- create new versioned candidate index if using quantized HNSW;
- benchmark K / `num_candidates` matrix;
- activate only through the existing alias lifecycle;
- preserve previous index rollback.

### VS-CPU-06 — Cache and duplicate fast paths

- add bounded image/text embedding cache;
- optionally add pHash near-duplicate support;
- measure actual hit rate before expanding complexity.

### VS-CPU-07 — Release evidence

Capture:

```text
encoder p50/p95
queue p50/p95/max
queries/sec
KNN p50/p95
request p50/p95
CPU
RAM / RSS
swap
Elasticsearch memory/disk
relevance sanity results
failure/retry behavior
rollback proof
```

Do not declare the CPU optimization complete from a single successful request.

---

## 15. Acceptance gates

Minimum release gates:

```text
[ ] Search V3 regression suite remains green.
[ ] No cross-tenant result is possible.
[ ] v1 rollback remains available during v2 rollout.
[ ] Encoder has one bounded production capacity model, not unbounded tasks.
[ ] Queue saturation is observable and fails closed/bounded.
[ ] No model is downloaded from a floating revision during service startup.
[ ] Interactive search is not starved by backfill.
[ ] KNN tuning has measured relevance evidence, not latency-only evidence.
[ ] INT8 promotion has a representative CAM sanity set.
[ ] RAM does not enter sustained swap pressure under accepted concurrency.
[ ] Disk has safe migration/backfill headroom.
[ ] Elasticsearch index activation is reversible by alias/lifecycle rollback.
```

---

## 16. Explicit non-goals for this optimization

Do not combine this track with:

```text
object detection
SAM/segmentation
DINO multi-embedding
face recognition
OCR architecture
new vector database
personalized ranking
GPU deployment
large/giant SigLIP2 variants
automatic multi-region embeddings
```

Those capabilities may be useful later, but they increase CPU cost or system complexity and are not required to solve the current latency/concurrency bottleneck.

---

## 17. Capacity upgrade trigger

Software optimization cannot remove the physical 3-vCPU limit.

Consider upgrading the VPS before increasing model complexity when production evidence shows any of:

```text
interactive queue frequently non-zero during normal use
accepted relevance requires retrieval breadth that pushes KNN p95 too high
encoder CPU is saturated for sustained periods
backfill cannot run without materially degrading interactive search
swap grows during normal concurrent Visual Search
```

A practical next hardware tier for materially better CPU-only concurrency is approximately:

```text
6–8 vCPU
16 GiB RAM
larger root disk
```

At that point benchmark multiple OpenVINO streams again rather than carrying the 3-vCPU settings forward unchanged.

---

## 18. Codex implementation rule

Before any VS-CPU phase, Codex must read:

```text
AGENTS.md
docs/security/SECURITY_GOVERNANCE.md
docs/architecture/AGENT.md
docs/architecture/SEARCH.md
docs/plans/CAM_VISUAL_SEARCH_PINTEREST_IMPLEMENTATION_GUIDE.md
docs/plans/VISUAL_SEARCH_FULL_CORPUS_OPERATIONS_PLAN.md
docs/plans/VISUAL_SEARCH_CPU_OPTIMIZATION_PLAN.md
```

Then inspect the actual current implementation. Current source code and deployed measurements win over stale assumptions in planning documents.

Implement one bounded VS-CPU phase at a time. Do not silently combine model migration, runtime conversion, queue redesign, index quantization, and full-corpus backfill in one change.
