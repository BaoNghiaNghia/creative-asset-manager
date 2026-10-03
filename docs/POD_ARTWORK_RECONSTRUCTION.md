# POD Artwork Reconstruction System

> **Status:** Canonical design document  
> **Role:** Source of truth for future POD artwork-reconstruction discussions and implementation decisions.

## 1. Product goal

Build a production pipeline that converts product/mockup/reference images (shirts, hoodies, flat-lays, logos, mixed text + illustration, etc.) into print-ready artwork while preserving source fidelity.

The target is **not** merely a PNG whose metadata says 4500×5400 at 300 DPI. The target is artwork whose effective detail, edges, text, alpha, gradients and textures remain usable at native resolution and high zoom.

The system should automate the workflow that currently requires manual AI reconstruction and review, while remaining provider-independent, observable and debuggable.

## 2. Input classes

Initial routing must handle at least:

- Typography-only or typography-dominant artwork.
- Illustration-only artwork.
- Mixed typography + illustration.
- Logos and geometric artwork.
- Flat-lay product images.
- Artwork on worn garments with perspective, wrinkles, lighting and fabric texture.
- Low-resolution/compressed references.
- Artwork containing gradients, metallic effects, watercolor, glow/transparency, distressed/grunge and other difficult textures.

## 3. Core quality principle

**4500×5400 pixels and 300-DPI metadata do not prove print quality.** A low-resolution generation resized to 4500×5400 still contains interpolated detail and can show soft/broken edges when zoomed.

Final QC therefore measures effective quality rather than dimensions alone. It should inspect native-resolution crops and representative 100%, 200% and 400% views, checking:

- exact text fidelity;
- edge sharpness;
- halo and aliasing;
- alpha quality;
- compression and blur;
- source/layout fidelity;
- effective resolution.

## 4. Canonical architecture

```text
INPUT
  ↓
Analyzer / DesignSpec
  ↓
Artwork localization + de-warp
  ↓
Material separation
  ├─ artwork base
  ├─ artwork texture
  ├─ garment/fabric texture (remove)
  ├─ lighting/shadow (remove)
  ├─ wrinkle/geometry distortion (remove)
  └─ compression/noise (remove)
  ↓
Route planner
  ├─ TEXT / LOGO
  ├─ ILLUSTRATION
  └─ MIXED
  ↓
Local/remote reconstruction candidates
  ↓
Element/layer separation
  ├─ vector-capable text, line art, geometry, smooth gradients
  └─ high-resolution raster illustration/complex texture
  ↓
Candidate scoring / fidelity judge
  ↓
Targeted retry when required
  ↓
High-resolution compositor
  ↓
Super-resolution / detail restoration where appropriate
  ↓
Alpha + edge refinement
  ↓
Final QC
  ↓
4500×5400 RGBA PNG + master artifacts
```

The architecture is intentionally **hybrid**. Do not force every design into vector and do not force every design into raster.

## 5. DesignSpec and routing

The analyzer should create a structured DesignSpec describing, where available:

- product/print area and artwork bounding region;
- exact recognized text and line ordering;
- major objects and relative positions;
- layout/composition;
- dominant colors;
- style and texture classes;
- perspective/warp severity;
- occlusion estimate;
- reconstruction confidence;
- recommended route and preservation constraints.

### 5.1 Text / logo route

Text must not rely on a generative image model for final glyphs when it can be reconstructed deterministically.

Use OCR + verification, layout/font/style analysis, vector/shape reconstruction and exact text validation. A character mismatch is a QC failure and triggers correction/retry.

Logos and geometric marks should preferentially use vector geometry/masks when source evidence supports it.

### 5.2 Illustration route

Use reconstruction for semantic and visual recovery, then preserve complex visual information as high-resolution raster layers. Do not destroy watercolor, grunge or intentional print texture merely to obtain cleaner edges.

### 5.3 Mixed route

Process typography/vector-capable elements separately from illustration/texture, then composite. This reduces text drift while preserving complex artwork.

## 6. Material separation

One of the hardest problems is distinguishing artwork texture from product/garment effects.

```text
Observed image
  ├─ Artwork Base              → keep/reconstruct
  ├─ Artwork Texture           → keep/reconstruct
  ├─ Garment/Fabric Texture    → remove
  ├─ Lighting/Shadow           → remove
  ├─ Wrinkle/Geometry          → correct
  └─ Compression/Noise         → remove/reduce
```

For example, intentional distressed print texture should survive while fabric grain and folds should not leak into the reconstructed master.

## 7. Difficult gradients and textures

### Smooth gradients

When reliably fitted, represent smooth gradients as vector/procedural gradients so they render cleanly at arbitrary resolution.

### Metallic / gold / specular effects

Use sharp vector/shape masks for geometry plus high-resolution raster/procedural surface texture, highlight/specular information and masks. Do not reduce complex metallic appearance to one linear gradient.

### Watercolor, distressed, vintage and grunge

Keep these as high-resolution raster texture/mask layers. The system must distinguish intentional artwork texture from garment texture and compression noise.

### Glow and semi-transparency

Preserve with high-precision raster/alpha processing during the master pipeline. Avoid background-removal methods that clip semi-transparent edges.

### Texture synthesis

When the source contains insufficient spatial resolution, synthesis may create perceptually consistent detail rather than simply enlarging pixels. Synthesized detail is **not** pixel-faithful recovery and must not be represented as such.

## 8. Hybrid master representation

A design may contain:

```text
master/
  source
  design_spec.json
  ocr.json
  confidence_map
  vector layers/
    typography
    line art
    geometry
    vector/procedural gradients
  raster layers/
    illustration
    watercolor/grunge/distress
    metallic/detail textures
  masks/
    alpha
    texture
    edge
  reconstruction candidates/
  judge-qc-reports/
  final master
```

Internally prefer higher precision (for example 16-bit raster where supported) and composite at a working resolution above final export when beneficial. Downsample with a high-quality filter to delivery size.

## 9. Reconstruction models and provider abstraction

Do not hard-code the product around one model.

GPT-6 Astra outputs can remain a quality/reference path. Local reconstruction candidates such as **Qwen Image Edit** and **FLUX-family image/edit models** should be benchmarked rather than assumed to be permanent choices.

Provider selection follows evidence from the POD benchmark corpus.

The provider contract should allow:

```text
primary → fallback → retry → manual review
```

without changing the job/output contract.

## 10. Candidate generation and self-correction

For difficult jobs, generate multiple internal candidates and select by fidelity/QC rather than exposing random variance to the user.

A judge receives **source + candidate + DesignSpec** and scores at least:

- text;
- composition;
- object fidelity;
- color/style;
- edge/alpha quality;
- unresolved or occluded regions.

On failure, produce targeted correction instructions identifying what may change and what must remain locked. Retries must be bounded.

The judge should be logically separated from the generator.

## 11. Super-resolution policy

Super-resolution is a restoration/detail tool, **not proof of true source detail**.

Candidate methods, including Real-ESRGAN-class local SR and alternatives, must be benchmarked on this POD domain.

Recommended raster flow:

```text
best practical native reconstruction
  → cleanup
  → SR/detail restoration
  → high-resolution composition
  → controlled downsample
  → 4500×5400
```

Text/vector geometry should be rendered from vector/shape representation at final or higher resolution instead of being raster-upscaled when possible.

## 12. Alpha and edge pipeline

Maintain RGB artwork, alpha matte and edge matte separately until final composition.

QC must detect:

- clipped thin lines;
- white/black halos;
- jagged edges;
- excessive feathering;
- loss of semi-transparent effects.

## 13. Reconstruction confidence

Maintain per-region or per-element confidence, e.g.:

```text
text       99%
outline    96%
gradient   94%
texture    88%
occluded   63%
```

Low-confidence regions should not be blindly sharpened. They may trigger alternate candidates, targeted reconstruction or manual review.

Occluded/missing source information cannot be guaranteed to be restored exactly. Reconstruction in such regions is informed synthesis, not recovery of unavailable pixels.

## 14. Output contract

Primary delivery target:

- 4500×5400 RGBA PNG;
- transparent background where required;
- 300-DPI metadata for POD compatibility;
- print-quality QC pass independent of metadata.

Retain master artifacts so future exports do not require regenerating the design. Where appropriate, retain SVG/vector layers in addition to raster masters.

## 15. Job lifecycle and observability

Suggested states:

```text
queued
analyzing
dewarping
separating
reconstructing
judging
retrying
compositing
upscaling
alpha_refining
qc
completed
failed_retryable
failed_final
manual_review
```

Every stage records:

- provider/model/version;
- duration;
- attempt number;
- input/output artifact IDs;
- scores;
- retry/failure reason;
- estimated/actual cost where applicable.

Logs must make it possible to answer why a job passed, failed, retried or became expensive.

## 16. Benchmark strategy

Start with the current real source examples and existing Astra-created outputs as the **V0 benchmark corpus**. Astra outputs are visual references, not ground truth for native sharpness/effective resolution.

Expand coverage to:

- typography-only;
- mixed text/illustration;
- complex illustration;
- flat-lay;
- worn garment;
- severe wrinkles/perspective;
- light/dark garments;
- low-resolution/compressed source;
- gradients;
- metallic;
- watercolor;
- distressed/grunge;
- glow;
- semi-transparent edges.

Compare candidates using native-resolution crops and 100/200/400% inspection.

Track at least:

- exact OCR/text fidelity;
- composition/layout similarity;
- object/detail fidelity;
- color/gradient fidelity;
- texture preservation vs garment-texture leakage;
- edge sharpness/aliasing;
- alpha quality;
- artifact rate;
- effective resolution;
- pass/retry/manual-review rate;
- latency;
- cost per accepted result.

**Do not select the production model, GPU, SR engine or provider until benchmark evidence supports the decision.**

## 17. Staged roadmap

### Phase 0 — Benchmark harness

Create the corpus, metric/QC pipeline, crop inspection and repeatable comparison harness.

### Phase 1 — Reconstruction Engine V1

DesignSpec, de-warp, material separation, text/illustration/mixed routing, one local reconstruction candidate, alpha pipeline and final export.

### Phase 2 — Hybrid quality pipeline

Vector-capable reconstruction, high-precision raster layers, texture maps/masks, SR comparison and effective-resolution QC.

### Phase 3 — Reliability

Multi-candidate generation, independent judge, targeted retry, provider fallback, confidence maps, job observability and manual-review state.

### Phase 4 — Domain optimization

Use accepted/rejected/corrected production examples as a POD-domain dataset for prompt/router optimization and, when justified, local fine-tuning/LoRA experiments.

### Phase 5 — Productization

Batch jobs, user-facing review/approve/reject, exports, quotas/credits if required, and integration with the wider Creative Asset Manager workflow.

Pattern generation, mockup generation and video generation remain outside the initial reconstruction-engine scope unless promoted by a later product decision.

## 18. Explicit non-goals / truthfulness

- Do not claim file dimensions or DPI metadata alone mean print-ready quality.
- Do not claim exact recovery of information hidden, cropped out or destroyed by compression.
- Do not vectorize complex raster texture merely to call the output vector.
- Do not let SR invent detail and then report it as recovered source detail.
- Do not lock the architecture to one AI provider before domain benchmarks.

## 19. Rights and usage

The reconstruction workflow should be used for artwork the user owns, is licensed to use, or otherwise has rights to reproduce. Technical extraction/reconstruction does not itself create reproduction rights.

## 20. Open benchmark decisions

These remain intentionally unresolved until measured:

- production reconstruction model/version;
- Qwen vs FLUX-family vs other local candidates;
- local VLM/OCR stack;
- SR/restoration engine;
- vectorization/tracing implementation;
- GPU class and VRAM target;
- candidates/retries by quality mode;
- exact QC thresholds;
- per-job cost and throughput targets;
- when remote Astra/reference paths should be invoked.

Future POD implementation discussions should update this document whenever a decision becomes validated so the repository retains one canonical technical history.

## 21. GPT-6 hybrid acceleration layer

GPT-6-class reasoning/vision models are a control and semantic-recovery capability, not a substitute for the print-quality pipeline. Keep reasoning/vision and image-generation providers separate so latency, quality and cost can be benchmarked independently.

Supported roles:

- **Analyzer / planner:** understand the source, produce DesignSpec, identify exact text, objects, composition, difficult texture, occlusion and uncertainty.
- **Fast 2D path:** when the user needs an initial 2D result quickly, use a remote image-generation provider to produce a semantic draft, then run basic cleanup/QC.
- **Precision assist:** invoke a higher-fidelity remote reconstruction path for difficult artwork, severe deformation, ambiguity, occlusion or local-model failure.
- **Region rescue:** repair only failed/low-confidence regions rather than regenerating an already-good full design.
- **Judge:** compare source, candidate and DesignSpec, while deterministic OCR/CV/edge/alpha metrics remain independent checks.
- **Fallback:** preserve service when local workers/models are unavailable, overloaded or repeatedly fail.

The remote image result is an intermediate semantic reconstruction, not automatically the print master. Exact text, vector-capable geometry, gradients, complex textures, alpha, edges and effective resolution still pass through the precision pipeline.

### 21.1 User-facing quality modes

Use three understandable modes while keeping provider choice internal:

- **Quick 2D:** prioritize latency; return a reviewable transparent 2D draft quickly.
- **Print Ready:** default smart routing plus local precision finish and full QC.
- **Max Fidelity:** stronger analysis, multiple/alternate candidates where justified, targeted rescue and stricter QC.

A Quick 2D result can continue into Print Ready/Max Fidelity without repeating source analysis when cached DesignSpec/artifacts remain valid.

### 21.2 Capability-based routing

Do not route by hard-coded model names. Route by capabilities such as:

```text
need_fast_draft
need_semantic_reconstruction
need_exact_text
need_complex_texture
need_region_rescue
need_vector
need_high_resolution
need_fallback
```

Provider adapters map those capabilities to the currently benchmarked GPT/local implementations. Replacing a provider must not change downstream contracts.

## 22. Typed control plane

Introduce a strict typed/validated control plane (JEV TypeSafe or an equivalent implementation) at metadata boundaries. It does **not** process image pixels and does not directly improve visual quality; it prevents malformed AI/provider data from propagating through the job.

Version and validate at least:

```text
DesignSpec
RouteDecision
ProviderRequest
ProviderResult
JudgeResult
QCResult
RetryDecision
ArtifactManifest
UserFeedback
JobExperience
LearningRecommendation
```

Validation includes schema version, required fields, enums/ranges and cross-field constraints. Invalid output follows bounded deterministic repair, structured-model repair/retry, fallback or manual review instead of silently entering later stages.

Policy decisions should consume validated observations. Models may estimate complexity/confidence; deterministic policy decides provider, retry, rescue and escalation unless an explicitly benchmarked learned policy is promoted.

## 23. Observability and logger architecture

Logging is a core production subsystem from the first benchmark job. A stable `job_id` and trace/correlation IDs must follow the job across VPS, remote providers and compute workers.

Separate three concerns:

- **Operational logs:** errors, debug events, worker/API/GPU failures and infrastructure diagnostics.
- **Telemetry:** stage latency, queue time, provider/model/version, cost, retries, QC scores, GPU/VRAM where available and throughput.
- **Experience Store:** normalized long-lived evidence used for retrieval and learning: source fingerprint, DesignSpec, route, candidate/result, QC, corrections and user outcome.

Every important stage records its input/output artifact IDs, model/provider and version, prompt/policy/schema versions, attempt, timing, failure/retry reason and applicable cost. Secrets and credentials must never be stored in logs.

Images/binaries belong in artifact/object storage; the database stores references, hashes and metadata. Temporary candidates and debug logs have bounded retention, while selected approved/rejected/corrected examples may be retained according to dataset/rights policy.

## 24. Experience memory and continuous learning

The system should improve from completed work, but production behavior must not self-modify immediately from individual jobs.

### 24.1 Learning levels

1. **Experience retrieval:** find similar past jobs and expose their routes, failures, accepted results and costs as evidence for the current router.
2. **Policy learning:** learn which provider/route/retry strategy performs best by artwork class, complexity, worker state, quality mode and cost/latency target.
3. **Prompt/parameter learning:** version prompts and parameters; compare approval, QC, retry, cost and latency before promotion.
4. **Model learning:** only after a sufficiently clean dataset exists, evaluate fine-tuning/LoRA or domain-specific local models.

Do not begin with fine-tuning. First accumulate reproducible high-quality evidence.

### 24.2 Feedback signals

Review supports at least approve, reject, regenerate and corrected/final-selected outcomes. Rejection/correction reasons should be structured, for example wrong text/layout/object/color, lost or fake texture, blur, bad edge/alpha or excessive source deviation.

A corrected result is especially valuable because it creates a preference pair:

```text
failed/undesired candidate → approved corrected result
```

Retry history is also training evidence. If repeated full regeneration does not improve a failure class, the learned policy can propose region rescue, a different provider or manual review.

### 24.3 Safe optimization: champion/challenger

Router, prompt, QC thresholds and model/provider policies use versioned **champion/challenger** evaluation. Challengers run in benchmark/shadow mode or controlled traffic first.

Promotion requires measured evidence across relevant cohorts: quality/approval must not regress beyond defined bounds, failure/retry behavior must remain acceptable, and cost/latency trade-offs must satisfy policy. Self-evaluation by an AI judge alone is never sufficient to promote production behavior.

## 25. Dataset registry

Production history is not automatically training data. A Dataset Builder filters Experience Store records for:

- rights/usage eligibility;
- user feedback/approval signal;
- deduplication;
- corruption/incomplete artifacts;
- schema compatibility;
- minimum source/result quality;
- useful positive, negative and corrected examples.

Datasets are immutable/versioned releases (for example POD Dataset v1/v2/v3) so every benchmark, learned policy and future fine-tune can identify its exact source dataset.

## 26. Deployment topology: VPS control plane + disposable compute workers

The preferred deployment is hybrid.

### VPS / always-on control plane

Keep online coordination on the existing VPS:

```text
Web/API + auth
job table/state
queue/orchestrator
typed control plane
smart router/policy
logger + telemetry
Experience Store metadata
Dataset Registry metadata
learning/analytics
remote GPT/image-provider orchestration
artifact metadata
```

Remote GPT/image inference does not require the VPS to own a GPU.

### Mini PC / compute worker

Treat the Mini PC as a replaceable capability-advertising worker, especially when it has useful GPU resources:

```text
local Qwen/FLUX-class reconstruction
local VLM where benchmarked
OCR / OpenCV
segmentation/matting
vector tracing/rendering
SR/detail restoration
texture processing
alpha/edge refinement
6K–8K working compositor
temporary model/artifact cache
```

The exact local model and GPU/VRAM target remain benchmark decisions.

### Worker protocol

Workers connect outward to the control plane, register capabilities, heartbeat, claim leased jobs, process them, upload artifacts and report telemetry. Do not make one Mini PC a single point of failure.

```text
Central Queue
   ├─ pod-mini-01
   ├─ future local GPU worker
   └─ optional cloud GPU worker
```

On heartbeat/lease loss, unfinished work returns to a recoverable state and can wait, move to another compatible worker or use an allowed remote fallback.

The router considers worker availability/load as well as artwork complexity. Quick 2D may use the remote fast path without waiting for a busy/offline local GPU; Max Fidelity may wait for stronger local compute or combine local/remote candidates according to policy.

### Storage placement

Mini PC storage is for models, caches, current jobs and temporary intermediates. Durable job state, artifact references, approved masters and learning metadata remain centralized. Large final/source artifacts should use durable artifact/object storage rather than depending on the worker disk.

## 27. Updated end-to-end architecture

```text
                         INPUT
                           │
                           ▼
                  Analyzer / DesignSpec
                           │
                     Typed validation
                           │
                           ▼
                     SMART ROUTER
              ┌────────────┼─────────────┐
              │            │             │
              ▼            ▼             ▼
         LOCAL WORKER   FAST REMOTE   PRECISION REMOTE
         Qwen/FLUX*      Quick 2D*      difficult/rescue*
              │            │             │
              └────────────┼─────────────┘
                           ▼
                  Reconstruction Result
                           │
                     Typed validation
                           │
                           ▼
                   LOCAL PRECISION
              vector / raster / texture
                 SR / alpha / edge
                           │
                           ▼
                     QC + JUDGE
                           │
                     Typed result
                           │
                 ┌─────────┼─────────┐
                 ▼         ▼         ▼
               PASS    REGION     RETRY /
                       RESCUE     FALLBACK
                 │         │         │
                 └─────────┴─────────┘
                           ▼
                       FINAL MASTER
                           │
                    USER FEEDBACK
                           │
                           ▼
                    EXPERIENCE STORE
                           │
                    DATASET REGISTRY
                           │
                           ▼
                    LEARNING ENGINE
                           │
             router/prompt/QC challengers
                           │
                    SHADOW/BENCHMARK
                           │
                    controlled promote
```

`*` Concrete provider/model names are implementation mappings selected by benchmark, not permanent architecture.

The long-term optimization objective is not simply to reconstruct an artwork once. Every eligible completed job should add evidence that can make future jobs more accurate, faster, cheaper or easier to recover, without allowing uncontrolled self-modification.

## 28. Updated implementation priorities

1. **Phase 0A — Contracts + telemetry first:** typed schemas, job/trace IDs, artifact manifest, structured stage events, provider/policy/prompt versioning and benchmark harness.
2. **Phase 0B — Baseline providers:** benchmark fast remote 2D, precision remote and at least one local reconstruction route on the same POD corpus.
3. **Phase 1 — Smart reconstruction V1:** DesignSpec, capability router, Quick 2D, Print Ready path, local precision finish, QC and bounded fallback.
4. **Phase 2 — Worker architecture:** Mini PC agent, heartbeat/capability registry, job leases, retry/requeue and worker telemetry.
5. **Phase 3 — Rescue + reliability:** region rescue, independent judge, confidence maps, multi-candidate only where justified and manual review.
6. **Phase 4 — Experience learning:** retrieval of similar cases, structured feedback, policy/prompt analytics, champion/challenger shadow evaluation.
7. **Phase 5 — Domain learning/productization:** Dataset Registry, justified fine-tune/LoRA experiments, batch processing and wider Creative Asset Manager integration.

Before locking local inference choices, record the Mini PC CPU, RAM, GPU and VRAM and benchmark representative jobs. Before promoting any GPT/local provider mapping, measure it against the same versioned corpus and QC contract.
