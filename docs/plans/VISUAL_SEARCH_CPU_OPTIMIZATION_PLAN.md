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

**Implementation status: read-only baseline capture tooling implemented on 2026-09-22; the actual production baseline must still be captured on the authorized target host before rollout.**

- `baseline_audit.py` records the explicitly supplied deployed commit, encoder `/ready` contract/runtime state, optional encoder RSS, host CPU/load/RAM/swap/root-disk state, and sanitized Elasticsearch version/health/JVM/filesystem/index-store metrics;
- it enumerates only the dedicated Visual Search alias namespace, target physical indices, document counts, and the bounded `visual_embedding` mapping fields needed for compatibility review;
- query/index p50/p95/max come from an operator-exported authenticated Visual Search diagnostics JSON rather than requiring this script to receive or persist an API credential;
- diagnostics are reduced to feature flags plus the bounded metrics payload; unrelated fields are not copied into the baseline report;
- `baseline.complete=true` requires a deployed commit, ready encoder, Elasticsearch version, at least one visual alias, host-memory evidence, and diagnostics evidence;
- the command is read-only: it does not restart services, mutate flags, create indices, switch aliases, or download models;
- VS-CPU-07 release evidence now requires a complete VS-CPU-00 baseline report.

Operator sequence:

```text
1. Export the authenticated Visual Search diagnostics JSON through the normal authorized API path.
2. Record the exact deployed commit.
3. Run baseline_audit.py on the target host before behavior-changing rollout steps.
4. Archive the JSON unchanged with the release evidence set.
```

### VS-CPU-01 — SigLIP2 v2 migration foundation

**Implementation status: code foundation implemented on 2026-09-22; production activation/backfill remains a separate reviewed operation.**

- pinned `google/siglip2-base-patch16-224` at revision `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`;
- defined `visual_embedding_v2` as the active code descriptor while preserving the explicit v1 descriptor for rollback;
- v1/v2 Elasticsearch namespaces remain distinct because the embedding schema version is part of the visual index prefix;
- isolated encoder runtime uses Transformers 4.51.3, local-files-only loading, and validates 768-dimension / 224×224 preprocess compatibility at startup;
- `encode_image` and `encode_text` continue to return normalized vectors coupled to the v2 descriptor;
- added an operator-run provisioning helper for the exact pinned snapshot; normal service startup still performs no model download;
- no destructive v1 cleanup, production-wide backfill, alias mutation, OpenVINO conversion, queue redesign, or INT8 work is part of this phase.

### VS-CPU-02 — OpenVINO baseline

**Implementation status: code baseline implemented on 2026-09-22; production activation still requires artifact export plus bounded validation on the target host.**

- the isolated encoder runtime now supports an explicit `VISUAL_ENCODER_RUNTIME=openvino` mode while `transformers` remains the safe fallback/default until release review;
- OpenVINO is pinned to `2026.4.0`; the runtime rejects an artifact exported under another baseline version;
- `apps/visual_encoder/export_openvino.py` converts the pinned local SigLIP2 snapshot directly with `openvino.convert_model` and writes FP32 IR only;
- the dual-tower encoder is exported as separate image/text IR graphs. These towers contain non-overlapping SigLIP2 weights, run in one encoder process, and avoid executing the unused modality for every request;
- the artifact manifest binds encoder name/revision, `visual_embedding_v2`, dimension, preprocess version, similarity, precision, OpenVINO version, and SHA-256 hashes of all XML/BIN files;
- normal service startup remains local-only and refuses missing, tampered, mismatched, or wrong-version artifacts;
- initial OpenVINO CPU configuration is `INFERENCE_NUM_THREADS=2`, `NUM_STREAMS=1`, and latency performance mode;
- the systemd encoder CPU quota is 200%, allowing the two-thread baseline while still avoiding multi-process model replication;
- `apps/visual_encoder/validate_openvino.py` performs a bounded synthetic image/text correctness comparison against the pinned PyTorch reference and records latency; promotion requires the configured cosine gate to pass;
- no automatic runtime fallback is performed after an OpenVINO load failure. Rollback is explicit by setting `VISUAL_ENCODER_RUNTIME=transformers`, which avoids silently changing embedding behavior;
- no bounded queue, INT8, ANN tuning, alias mutation, or full-corpus backfill is included in this phase.

Operator sequence before any canary activation:

```text
1. Provision the exact SigLIP2 snapshot with provision_model.py.
2. Export FP32 IR with export_openvino.py into a revision-specific artifact directory.
3. Run validate_openvino.py on the target CPU host.
4. Review correctness and latency evidence.
5. Only after review, set VISUAL_ENCODER_RUNTIME=openvino and restart the isolated encoder.
6. Roll back by restoring VISUAL_ENCODER_RUNTIME=transformers.
```

### VS-CPU-03 — Bounded inference queue

**Implementation status: code baseline implemented on 2026-09-22; production sizing remains subject to target-host load evidence.**

- replaced immediate busy rejection with one bounded single-worker priority queue owned by the isolated encoder process;
- default queue size is 8 waiting requests with 2 slots reserved for interactive work; background jobs cannot consume those reserved slots;
- queue wait timeout defaults to 5 seconds and measures time until inference actually begins, not model execution time;
- interactive work has strict priority over queued background/backfill work while preserving FIFO order inside each priority class;
- cancelled requests that have not started are removed from the queue; already-started thread inference is allowed to finish under queue ownership because Python cannot safely force-cancel the native inference call;
- explicit error classes/codes now distinguish `visual_encoder_queue_full`, `visual_encoder_queue_timeout`, and `visual_encoder_unavailable`;
- the API client no longer performs busy retries, preventing retry amplification on top of server-side queueing;
- interactive upload/crop/text requests send `priority=interactive`; worker-side visual indexing sends `priority=background`;
- queue saturation and lifecycle counters are exposed through the encoder `/ready` payload: accepted, started, completed, failed, queue-full, queue-timeout, cancelled, current queued depth, and current active priority;
- tests cover foreground priority, background reservation, timeout cleanup, cancellation cleanup, client error preservation, and router error mapping;
- OpenVINO/PyTorch inference capacity remains one operation at a time. The queue does not increase model concurrency or create extra model copies.

Default controls:

```text
VISUAL_ENCODER_QUEUE_MAX_SIZE=8
VISUAL_ENCODER_QUEUE_INTERACTIVE_RESERVE=2
VISUAL_ENCODER_QUEUE_WAIT_TIMEOUT_SECONDS=5
```

These values are bounded starting points, not production performance claims. Adjust only from measured p50/p95 queue wait, CPU, RSS, and interactive error-rate evidence.

### VS-CPU-04 — INT8 candidate

**Implementation status: candidate build/validation tooling implemented on 2026-09-22; INT8 is intentionally not loadable by the production runtime and is not promoted.**

- NNCF is isolated to `apps/visual_encoder/requirements-quantization.txt` and pinned to `3.4.0`; the production encoder runtime does not depend on NNCF;
- `apps/visual_encoder/quantize_openvino.py` reads only a hash-verified FP32 OpenVINO artifact and the exact pinned SigLIP2 processor snapshot;
- image and text towers are calibrated independently from operator-supplied representative datasets using `nncf.Dataset` and `nncf.quantize`;
- the candidate uses the Transformer quantization mode, CPU target, `MIXED` preset, accurate bias correction, and a bounded calibration subset (default 300 samples per tower);
- the INT8 manifest has a distinct `cam-siglip2-openvino-int8-candidate-v1` artifact format, explicit NNCF/OpenVINO/Transformers versions, source-FP32 hashes, calibration evidence, output hashes, and `compatibility_status=candidate_unvalidated`;
- `OpenVinoSiglip2Encoder` still accepts only the FP32 artifact format/precision, so an INT8 candidate cannot be activated accidentally under `visual_embedding_v2`;
- `apps/visual_encoder/validate_int8.py` compares INT8 against the approved FP32 OpenVINO baseline on representative ranking cases and query latency;
- the validation set supports `image_triplet` cases (query/positive/negative) and `text_image` cases (text/positive/negative);
- the default release gate requires at least 50 cases, preserves positive-over-negative ordering for every case, and requires per-input INT8↔FP32 embedding cosine of at least 0.995;
- validation produces evidence only. It does not rewrite the candidate manifest, mutate the active descriptor, or make INT8 loadable;
- any future promotion requires an explicit reviewed change deciding whether vectors are sufficiently compatible with `visual_embedding_v2` or require a new embedding schema/version.

Example sanity-set shape:

```json
{
  "cases": [
    {
      "id": "same-product-001",
      "kind": "image_triplet",
      "query": "queries/product-a-crop.jpg",
      "positive": "positives/product-a-full.jpg",
      "negative": "negatives/similar-background.jpg"
    },
    {
      "id": "text-refinement-001",
      "kind": "text_image",
      "text": "blue floral embroidery",
      "positive": "positives/blue-floral.jpg",
      "negative": "negatives/blue-plain.jpg"
    }
  ]
}
```

Operator sequence:

```text
1. Create a separate quantization virtualenv from requirements-quantization.txt.
2. Supply representative image calibration data and text calibration lines.
3. Run quantize_openvino.py against the validated FP32 artifact.
4. Prepare 50–100 representative CAM sanity cases covering same-product,
   same-design/different-photo, crop→full, near-duplicate/compressed derivative,
   hard negative, and text→image refinement behavior.
5. Run validate_int8.py and archive the JSON report with latency and relevance evidence.
6. Do not promote the candidate from this phase; production continues to use FP32.
```

### VS-CPU-05 — Elasticsearch ANN tuning

**Implementation status: candidate index + benchmark tooling implemented on 2026-09-22; no candidate index has been created or activated on production.**

- production compose currently pins Elasticsearch 8.15.3; code still performs an explicit runtime version probe before allowing an `int8_hnsw` candidate build;
- the active/baseline Visual Search mapping remains unchanged unless explicit `VisualVectorIndexOptions` are supplied for a new physical candidate;
- `create_int8_hnsw_candidate()` creates only a new versioned physical index with explicit `int8_hnsw`, `m`, and `ef_construction`; it never mutates the active alias;
- `reindex_active_to_candidate()` copies the current visual projection into an already-created physical candidate with `op_type=create`, bounded optional `max_docs`, and no alias switch;
- candidate target names must stay inside the active Visual Search schema/index-generation namespace; aliases and cross-namespace indices are rejected;
- `search_index()` benchmarks a physical candidate directly, retaining tenant/lifecycle/access filters and defensive cross-tenant hit filtering;
- `switch_aliases()` now validates that its target is a physical index in the same Visual Search namespace before any alias mutation;
- `ann_benchmark.py` compares the physical candidate against the active alias without activation and reports p50/p95/max KNN latency, expected-positive recall, baseline result overlap, and result counts;
- the benchmark matrix is A=`K40/candidates160/page20`, B=`K80/candidates240/page20`, C=`K120/candidates320/page40`, D=`K200/candidates500/page40`;
- benchmark datasets carry already-versioned `visual_embedding_v2` vectors plus tenant scope and expected positive asset IDs; raw images/text do not need to be logged into the benchmark report;
- no profile is selected automatically from latency alone. Promotion still requires representative CAM relevance evidence and a separately reviewed alias switch.

Operator sequence:

```text
1. Record the deployed Elasticsearch version/mapping/aliases and host pressure.
2. Create a new versioned int8_hnsw physical candidate.
3. Reindex the active visual projection into that candidate; start bounded if needed.
4. Run ann_benchmark.py against representative CAM query embeddings and expected positives.
5. Review relevance, hard negatives, latency, CPU, JVM pressure, disk and result counts.
6. Only after review, use the existing alias lifecycle for activation.
7. Preserve the previous physical index for rollback.
```

### VS-CPU-06 — Cache and duplicate fast paths

**Implementation status: bounded embedding cache implemented on 2026-09-22; pHash remains intentionally deferred until measured duplicate-hit evidence justifies it.**

- the isolated encoder process owns separate thread-safe in-process LRUs for image and text embeddings, so API and background workers share one cache without introducing Redis or another resident service;
- cache entries store only derived `VisualEmbedding` values and contract-bound keys; raw upload bytes, images, signed URLs, credentials, and raw cache payloads are not retained;
- image keys hash deterministic RGB pixel bytes plus dimensions after the encoder service has safely decoded the request, then bind encoder name/revision, embedding schema, and preprocess version;
- text keys hash the exact stripped UTF-8 query text and bind the same embedding contract fields; case is intentionally preserved because changing text normalization could change model semantics;
- defaults are 128 image entries and 256 text entries, both configurable to zero for a disabled cache;
- LRU eviction is count-bounded and exposes entries/max/hits/misses/evictions through the encoder `/ready` payload;
- cache lookup occurs before inference queue admission, so true hits bypass queue wait and CPU inference;
- on a miss, the queue worker rechecks the cache before model execution to suppress duplicate queued work that arrived while an earlier identical request was running;
- cache state is process-local and cleared naturally on encoder restart; incompatible schema/preprocess/model changes also generate different cache keys;
- tests cover LRU eviction, contract-bound keys, and repeated-request queue bypass;
- pHash/near-duplicate indexing is not added in this phase. Add it only after real CAM hit-rate evidence shows enough resized/recompressed derivatives to justify the extra storage and tuning surface.

Default controls:

```text
VISUAL_ENCODER_IMAGE_CACHE_MAX_ENTRIES=128
VISUAL_ENCODER_TEXT_CACHE_MAX_ENTRIES=256
```

### VS-CPU-07 — Release evidence

**Implementation status: read-only evidence tooling implemented on 2026-09-22; no production rollout is authorized or performed by these tools.**

- the bounded inference queue now keeps a rolling 256-sample queue-wait window and exposes p50/p95/max plus sample count through `/ready`;
- `apps/visual_encoder/benchmark_load.py` runs a bounded interactive encoder load probe, records request p50/p95/max, queries/sec, status/error counts, maximum observed queue depth/wait p95, and post-run cache/queue state;
- load probes generate unique text queries or minimally perturbed local image pixels so the benchmark measures model/queue behavior instead of only cache hits; reports never contain the raw image bytes or raw generated query payloads;
- the encoder internal key is read from `VISUAL_ENCODER_INTERNAL_KEY`; it is never accepted as a CLI argument or written into reports;
- `release_evidence.py` is read-only and collects encoder readiness/runtime/queue/cache metrics, host CPU/load/RAM/swap/root-disk state, optional encoder RSS by PID, and a sanitized Elasticsearch version/health/JVM/filesystem/index-store snapshot;
- `regression_evidence.py` runs the bounded release regression groups with the current Python environment: isolated encoder tests, the full Visual Search module tests, the full Search V3 module tests, VPS deployment tests, plus dedicated acceptance checks for cross-tenant isolation, queue priority, pinned startup, and alias lifecycle; it emits only command targets/pytest filters, exit status, timing, and bounded stdout/stderr summaries;
- `rollback_proof.py` verifies the exact previous SigLIP v1 revision directory and hashes its core local model files, then checks the dedicated `visual_embedding_v1` Elasticsearch read alias, vector mapping, and document count without mutating aliases or indices;
- the evidence bundle can attach the complete VS-CPU-00 baseline, approved OpenVINO validation report, ANN benchmark report, optional INT8 candidate report, bounded load report, regression report, and explicit rollback-proof report;
- `release_evidence_complete` remains false unless the baseline is complete, encoder readiness and Elasticsearch health are good, the OpenVINO report passed, ANN relevance evidence exists, load evidence exists, the regression report passed, and `rollback.verified=true`;
- `acceptance_gates.py` evaluates the twelve gates in section 15 from that immutable release bundle and returns `pass`, `fail`, `unknown`, or `not_applicable`; missing runtime evidence never becomes an implicit pass;
- the disk gate uses the documented 12 GiB minimum migration-headroom target; the swap gate only passes when the load probe was given a reviewed maximum swap-growth threshold and stayed within it;
- INT8 evidence is optional because the INT8 candidate is not promoted in this track;
- evidence tooling never changes feature flags, restarts a service, creates/deletes an index, switches aliases, or deletes rollback artifacts.

Required evidence before a reviewed rollout decision:

```text
encoder request p50/p95/max
queue wait p50/p95/max
queries/sec
queue full / timeout behavior
KNN p50/p95
ANN expected-positive recall / hard-negative review
CPU / system load
RAM / encoder RSS
swap
root-disk free space
Elasticsearch health / JVM / disk / index footprint
OpenVINO correctness report
cache hit/miss/eviction evidence
failure/retry behavior
rollback proof
```

Suggested bounded sequence:

```text
1. Validate the FP32 OpenVINO artifact.
2. Export/reindex an ANN candidate without switching aliases.
3. Run the representative ANN benchmark matrix.
4. Run the bounded encoder load probe on the target host; if swap is a release gate,
   provide the reviewed --max-swap-growth-mib threshold so the report can pass/fail explicitly.
5. Capture rollback proof while the previous model/index artifacts still exist.
6. Run regression_evidence.py with the release Python environment.
7. Run release_evidence.py to assemble the immutable JSON evidence bundle.
8. Run acceptance_gates.py against that bundle.
9. Review the bundle and gate report; collection/evaluation itself is not rollout approval.
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

`acceptance_gates.py` maps these twelve gates to explicit evidence states:

- code-contract gates use the dedicated regression subsets for Search V3, tenant isolation, queue priority, pinned startup, deployment bounds, and alias lifecycle;
- v1 rollback requires `rollback_proof.py` to report `verified=true`;
- KNN tuning passes the evidence-presence gate only when the ANN report contains measured expected-recall and baseline-overlap fields; relevance approval remains a release-review decision;
- INT8 is `not_applicable` while INT8 is not part of the release candidate; if an INT8 report is attached, at least 50 cases and `passed=true` are required;
- swap pressure remains `unknown` unless the bounded load probe was run with a reviewed swap-growth threshold; it fails if that threshold is exceeded;
- disk headroom fails below the documented 12 GiB minimum;
- the evaluator cannot return `eligible_for_release_review` unless the VS-CPU-00 baseline is complete and the VS-CPU-07 release-evidence bundle itself is complete.

An `unknown` gate is blocking. `not_applicable` is allowed only for an optimization that is not being promoted, currently INT8.

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
