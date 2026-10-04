# POD Artwork Reconstruction Tool

> **Status:** Canonical design document — standalone-tool architecture
> **Role:** Source of truth for POD artwork reconstruction, quality, learning, harness, logging and update decisions.

## 1. Product definition

The product is a **standalone desktop tool** that runs on the user's local machine:

```text
Open POD Artwork Tool
        ↓
Drag/drop or upload one or more reference images
        ↓
Tool analyzes and reconstructs the artwork
        ↓
Tool performs quality checks and targeted repair
        ↓
User reviews the result
        ↓
Export clean 2D artwork
```

The primary user experience is intentionally simple: **Input → Process → Output**.

Internally the tool may use local GPU models, deterministic image processing and remote AI providers, but those implementation details should normally remain hidden from the user.

The primary output is a clean 2D artwork master suitable for POD production. A 4500×5400 RGBA PNG with 300-DPI metadata is the default delivery profile, but print quality is determined by actual detail, text, edges, alpha and fidelity rather than file dimensions alone.

## 2. Product UX

Main screen:

```text
┌────────────────────────────────────────┐
│        POD Artwork Reconstruction      │
├────────────────────────────────────────┤
│                                        │
│   Drag images here                     │
│   or [ Select Images ]                 │
│                                        │
├────────────────────────────────────────┤
│ Mode                                   │
│ ○ Quick 2D                             │
│ ● Print Ready                          │
│ ○ Max Fidelity                         │
├────────────────────────────────────────┤
│ Processing                             │
│ [██████████████░░░░] 74%               │
│ Rebuilding artwork texture             │
├────────────────────────────────────────┤
│ Input                  Output          │
│ [ preview ]            [ preview ]     │
├────────────────────────────────────────┤
│ [Regenerate] [Enhance] [Export]        │
└────────────────────────────────────────┘
```

The main workflow must not expose queue internals, model names, worker topology, harness configuration or learning internals unless the user opens Advanced/Developer views.

### 2.1 Quality modes

- **Quick 2D:** prioritize turnaround time; produce a reviewable clean 2D draft quickly.
- **Print Ready:** default mode; use smart routing, local precision finish and full QC.
- **Max Fidelity:** use stronger analysis, alternate candidates where justified, targeted rescue and stricter QC.

A Quick 2D result can be upgraded to Print Ready or Max Fidelity from cached/checkpointed artifacts without repeating valid earlier stages.

## 3. Canonical system architecture

The desktop application is one product but internally separates UI and processing so heavy GPU work or model failure does not freeze the interface.

```text
                 PODArtworkTool.exe
                         │
              ┌──────────┴──────────┐
              │                     │
         Desktop UI            Engine Process
              │                     │
              │            ┌────────┼─────────┐
              │            ▼        ▼         ▼
              │       Deterministic Local AI Remote AI
              │          stages     models    providers
              │            │        │         │
              │            └────────┼─────────┘
              │                     ▼
              │                Reconstruction
              │                     ▼
              │               Precision Finish
              │                     ▼
              │                    QC
              │                     ▼
              └────────────────── Output
```

If the engine process fails, the UI remains alive, the failure is logged and the job resumes from the latest valid checkpoint when possible.

## 4. Canonical processing workflow

```text
INPUT / REFERENCE SET
        │
        ▼
PRE-FLIGHT
        │
        ├─ hash / deduplicate
        ├─ source quality
        ├─ dimensions / orientation
        ├─ blur / compression
        ├─ artwork detection
        └─ metadata / provenance
        │
        ▼
ARTWORK IDENTITY
        │
        ▼
MULTI-REFERENCE FUSION
(if several images show the same artwork)
        │
        ▼
LIGHT ANALYZER
        │
        ├─ DesignSpec
        ├─ OCR/text
        ├─ composition
        ├─ material/texture
        ├─ warp/occlusion
        └─ confidence
        │
        ▼
TYPED VALIDATION
        │
        ▼
SMART ROUTER
   ┌────┼───────────┐
   ▼    ▼           ▼
DETERMINISTIC    LOCAL AI    REMOTE AI
text/logo/etc.   Qwen/FLUX*  fast/precision*
   │              │           │
   └──────────────┼───────────┘
                  ▼
           SEMANTIC MASTER
                  │
             QC GATE #1
           source fidelity
                  │
                  ▼
          PRECISION FINISH
   vector / raster / texture / SR
         alpha / edge / compositor
                  │
                  ▼
             QC GATE #2
          technical print quality
                  │
          ┌───────┼───────────┐
          ▼       ▼           ▼
        PASS   REGION       REVIEW
               RESCUE
          │       │           │
          └───────┴───────────┘
                  ▼
              FINAL MASTER
                  │
             USER FEEDBACK
                  │
                  ▼
            EXPERIENCE STORE
                  │
             LEARNING LOOP
```

`*` Concrete provider/model names are provider mappings selected by benchmark and are not permanent architecture.

## 5. Pre-flight and source quality

Pre-flight runs before expensive AI work.

It should determine:

- file hash and duplicate status;
- dimensions and format;
- source quality;
- blur/compression severity;
- transparency if present;
- orientation;
- likely artwork region;
- whether the input belongs to an existing artwork identity;
- quality mode, priority and optional budget constraints.

If an identical or equivalent artwork already has a valid master, the system may reuse it instead of repeating reconstruction.

## 6. Artwork Identity and multi-reference fusion

One artwork may have multiple references:

```text
artwork_001/
  front.jpg
  closeup.jpg
  model.jpg
  side.jpg
```

All references should map to one `artwork_identity`.

The system should use multiple references when available to reduce hallucination and recover information hidden by perspective, wrinkles, hands, folds or poor framing.

Multi-reference fusion may:

- select the clearest region from each image;
- estimate perspective/dewarp transforms;
- build a shared evidence map;
- identify mutually consistent details;
- mark unresolved regions as low confidence.

Do not force one arbitrarily selected image to be the sole source when better evidence exists across several references.

## 7. DesignSpec and typed control plane

The analyzer creates a structured `DesignSpec` containing, where available:

- artwork bounding region;
- exact recognized text and line order;
- objects and relative positions;
- layout/composition;
- dominant colors;
- style/texture classes;
- gradient/metallic/distress/watercolor/glow indicators;
- perspective/warp severity;
- occlusion;
- confidence by region/element;
- preservation constraints;
- recommended capabilities required.

Use a strict typed/validated control plane such as JEV TypeSafe or an equivalent implementation for:

```text
DesignSpec
RouteDecision
ProviderRequest
ProviderResult
JudgeResult
QCResult
RetryDecision
ArtifactManifest
ExportProfile
UserFeedback
JobExperience
LearningRecommendation
```

All contracts are versioned.

Invalid structured output must not silently continue. The system should attempt bounded deterministic repair, structured retry, fallback or manual review.

AI models provide observations; validated policy code decides routing, retries, fallback and escalation unless a learned policy has explicitly passed promotion gates.

## 8. Smart Router

Routing is capability-based, not hard-coded around model names.

Example capabilities:

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

Typical routes:

### 8.1 Deterministic route

Preferred for text/logo/geometric work when reliable reconstruction is possible:

```text
OCR
→ exact text verification
→ font/layout/style analysis
→ vector/shape rebuild
→ high-resolution render
```

### 8.2 Local AI route

Used for illustration, material separation, semantic reconstruction, SR or other operations where a benchmarked local model performs well.

### 8.3 Remote AI route

Used for:

- Quick 2D;
- difficult semantic reconstruction;
- severe deformation/occlusion;
- local-model failure;
- region rescue;
- fallback when local compute is unavailable.

### 8.4 Hybrid route

Use AI for semantic recovery and deterministic/local precision processing for exact text, vectors, gradients, texture, SR, alpha, edge and final composition.

## 9. Reconstruction quality principles

### 9.1 Text and logo

Final glyphs should not depend on a generative image model when deterministic reconstruction is possible.

Any text mismatch is a QC failure.

### 9.2 Illustration

Preserve intentional visual texture while removing garment/fabric effects.

### 9.3 Mixed artwork

Separate typography/vector-capable elements from illustration/texture and composite them after reconstruction.

### 9.4 Material separation

Observed pixels may contain:

```text
Artwork Base              → keep/reconstruct
Artwork Texture           → keep/reconstruct
Garment/Fabric Texture    → remove
Lighting/Shadow           → remove
Wrinkle/Geometry          → correct
Compression/Noise         → reduce/remove
```

The core challenge is distinguishing intentional artwork texture from product texture.

## 10. Difficult gradients and textures

- **Smooth gradients:** use vector/procedural gradients where fit is reliable.
- **Metallic/gold/specular:** use sharp masks plus high-resolution raster/procedural texture and highlight information.
- **Watercolor/distressed/grunge:** preserve as high-resolution raster texture/masks.
- **Glow/semi-transparency:** retain high-precision alpha handling.
- **Texture synthesis:** allowed only when source detail is insufficient; generated detail must be treated as plausible synthesis, not recovered ground truth.

## 11. Artifact DAG, checkpoints and cache

The processing pipeline is a dependency graph rather than a monolithic script.

Each stage should be reproducible from:

```text
input artifact hash
+
configuration hash
+
model/provider version
+
prompt/policy/schema version
```

Each successful stage emits an immutable artifact record.

Benefits:

- resume after crash;
- avoid repeating expensive AI calls;
- upgrade Quick 2D to Print Ready without restarting;
- rerun only SR/alpha/export when upstream artifacts remain valid;
- reuse identical preprocessing across benchmark recipes;
- support reproducible debugging.

A stage must be idempotent where practical.

## 12. Master artifact representation

A design may retain:

```text
master/
  source/
  preflight.json
  design_spec.json
  ocr.json
  confidence_map
  evidence_map
  vector/
    typography
    line_art
    geometry
    gradients
  raster/
    illustration
    watercolor
    distress
    metallic/detail textures
  masks/
    alpha
    texture
    edge
  candidates/
  qc/
  artifact_manifest.json
  final_master
```

Internally use higher precision and a working resolution above delivery size where beneficial.

## 13. Two-stage quality control

Do not collapse all quality checks into one score.

### 13.1 QC Gate #1 — Semantic/source fidelity

Checks:

- exact text;
- layout/composition;
- object presence and shape;
- color;
- missing regions;
- source similarity;
- intentional texture;
- unresolved/occluded areas.

### 13.2 QC Gate #2 — Technical print quality

Checks:

- effective resolution;
- edge sharpness;
- aliasing;
- halo;
- alpha;
- blur;
- line survival;
- transparent-pixel contamination;
- output dimensions/profile;
- color-space/export validity.

A candidate must pass both gates for Print Ready.

AI Judge is only one evaluator. Final QC may combine OCR, CV/perceptual metrics, edge metrics, alpha metrics, technical validation and human review.

## 14. Region rescue and bounded retries

If only part of a design fails, do not regenerate the entire image by default.

```text
candidate
   ↓
failed region detected
   ↓
crop + context + mask
   ↓
local or remote rescue
   ↓
merge into locked good regions
   ↓
re-run targeted QC
```

Retries are bounded.

Failure type determines the action:

```text
TEXT_FIDELITY_FAILED  → deterministic text repair
TEXTURE_FAILED        → texture/local/remote region rescue
ALPHA_FAILED          → alpha stage rerun
PROVIDER_ERROR        → provider fallback
OUT_OF_MEMORY         → lower-memory route / alternate worker/provider
SOURCE_INSUFFICIENT   → manual review / alternate reference
```

## 15. Export profiles

Do not hard-code the entire architecture to one output size.

Default POD profile:

- PNG;
- 4500×5400;
- RGBA;
- transparent background;
- 300-DPI metadata.

An `ExportProfile` may additionally define:

- canvas dimensions;
- aspect behavior;
- color-space/ICC policy;
- transparency rules;
- safe margins;
- scaling;
- output formats.

Possible presets include Default POD, Printify, Printful and Custom.

The internal master is independent from delivery size so future exports do not require full reconstruction.

## 16. Job lifecycle and error taxonomy

Core states may include:

```text
queued
preflight
analyzing
dewarping
separating
reconstructing
judging
precision_finishing
upscaling
alpha_refining
qc
region_rescue
retrying
review_required
completed
failed_retryable
failed_final
cancel_requested
cancelled
resuming
waiting_provider
waiting_compute
blocked_budget
```

Use normalized failure categories such as:

```text
SOURCE_ERROR
PROVIDER_ERROR
COMPUTE_ERROR
QUALITY_FAILURE
POLICY_FAILURE
BUDGET_FAILURE
CANCELLED
```

This allows deterministic retry/fallback policy.

## 17. Logger, telemetry and diagnostics

Logging is mandatory from the first build.

Separate:

- **Operational logs:** app/engine/update/debug/errors.
- **Telemetry:** latency, resource use, retries, provider/model/version, quality metrics and cost where applicable.
- **Experience Store:** normalized long-lived learning evidence.

Suggested local structure:

```text
PODTool/
  logs/
    app.log
    engine.log
    updater.log
    jobs/
      <job_id>.jsonl
```

Each stage records at least:

- timestamp;
- job/trace ID;
- stage;
- provider/model/version;
- prompt/policy/schema version;
- attempt;
- duration;
- input/output artifact IDs;
- retry/failure reason;
- GPU/VRAM/RAM metrics where available;
- cost where applicable.

Logging must not block image processing.

### 17.1 Diagnostic bundle

The tool provides **Export Diagnostic Bundle**:

```text
diagnostic_<timestamp>.zip
  system.json
  gpu.json
  app-version.json
  updater.log
  recent-job-logs/
  health-report.json
  config-redacted.json
```

Never include secrets, credentials or API keys.

## 18. Auto-update and rollback

Auto-update is part of the foundation, not a later add-on.

Startup:

```text
Launch tool
    ↓
Updater bootstrap
    ↓
Check release manifest
    ↓
No update ─────────→ Start installed version
    ↓
Update available
    ↓
Download package
    ↓
Verify checksum/signature
    ↓
Backup current version
    ↓
Install
    ↓
Start + health check
    ├─ PASS → keep new version
    └─ FAIL → rollback automatically
```

If the update service or internet is unavailable, log a warning and start the installed version.

Support at least:

```text
stable
staging
```

The updater should be independent from the main engine so a broken release cannot prevent rollback.

## 19. Historical learning from existing input/final pairs

Existing historical data is a first-class starting asset.

```text
Historical Inputs
       +
Approved Finals
       ↓
Pairing
       ↓
Artwork Identity
       ↓
Normalization
       ↓
Historical Dataset
       ↓
Experience / Retrieval / Benchmark / Training
```

A historical record should link one or more product/reference images to one approved final artwork.

The system should support import from folders, filenames, SKU/design IDs or visual matching.

Do not immediately fine-tune on all historical data. Use it first for:

1. similar-case retrieval;
2. recipe learning;
3. router/provider evaluation;
4. QC/ranker development;
5. material-separation evaluation;
6. only later, justified local-model adaptation/fine-tuning.

## 20. Synthetic paired data

Approved clean masters can produce synthetic degraded references:

```text
clean master
   ↓
mockup simulator
   ├─ perspective
   ├─ wrinkle
   ├─ lighting
   ├─ garment texture
   ├─ blur/compression
   └─ partial occlusion
   ↓
synthetic product reference
```

Because the clean master is known, the pair becomes controlled training/evaluation data.

Synthetic data supplements real historical pairs; it must not replace real validation.

## 21. Dataset Registry and split rules

All datasets are versioned.

Never randomly split individual reference images if they show the same artwork.

Split by `artwork_identity` so one design cannot leak across train/validation/test.

A typical starting split may be:

```text
Train            75%
Validation       10%
Golden Holdout   15%
```

The Golden Holdout is never used for:

- training;
- prompt learning;
- router learning;
- retrieval memory.

It exists only for unbiased regression and promotion decisions.

## 22. Experience Store and continuous learning

Every eligible completed job can add evidence:

```text
input
DesignSpec
route
provider/model
candidate
QC
retry history
user feedback
final selected result
cost/latency/resource metrics
```

Learning occurs in layers:

1. **Experience retrieval:** use similar successful/failed cases.
2. **Recipe learning:** learn the best processing recipe for each artwork family.
3. **Policy learning:** improve route/provider/retry decisions.
4. **Prompt/parameter learning:** compare versioned alternatives.
5. **Model learning:** fine-tune/LoRA only after clean dataset evidence justifies it.

User feedback should support:

```text
Approve
Reject
Regenerate
Corrected
Final selected
```

Structured reasons may include wrong text/layout/object/color, lost texture, fake texture, blur, bad edges, bad alpha or excessive source deviation.

Corrected results create high-value preference pairs:

```text
undesired candidate → approved correction
```

## 23. Reconstruction Recipe memory

The system learns complete recipes rather than only “best model”.

Example:

```text
Artwork family: metallic logo

Best recipe:
vector extraction
→ precise geometry
→ metallic texture mask
→ alpha cleanup
→ high-resolution render
```

Another example:

```text
Artwork family: watercolor + typography

Best recipe:
semantic reconstruction
→ exact OCR/text rebuild
→ preserve raster watercolor
→ local SR
→ alpha refinement
```

Recipe quality is measured by the Harness.

## 24. Benchmark / Evaluation Harness

The Harness is a first-class subsystem, not a temporary script.

It does **not** process user production jobs directly. It measures whether a proposed change is better than the production champion.

```text
Dataset Registry
      ↓
Dataset Snapshot
      ↓
Benchmark Plan
      ↓
Recipe A / B / C
      ↓
Normalized Results
      ↓
Semantic QC
Technical QC
Operational Metrics
      ↓
Scorecard
      ↓
Visual Diff
      ↓
Human Spot Check
      ↓
Champion / Challenger
      ↓
Promotion Gate
```

### 24.1 Benchmark case

Each benchmark case contains:

```text
case_id
artwork_identity
source reference set
approved target/master
metadata
exact text/constraints
difficulty/cohort labels
```

### 24.2 Benchmark recipes

The Harness compares **whole recipes**, not just models.

A recipe versions analyzer, dewarp, reconstruction, OCR, vectorization, SR, alpha, QC, prompt and routing settings.

### 24.3 Metrics

Semantic:

- exact text;
- composition/layout;
- object fidelity;
- color;
- texture fidelity;
- missing detail.

Technical:

- edge;
- alpha;
- halo/aliasing;
- blur;
- effective resolution;
- small-detail survival.

Operational:

- latency;
- GPU time;
- peak VRAM/RAM;
- retries;
- provider calls;
- cost;
- failure/manual-review rate.

### 24.4 Cohort evaluation

Results must be broken down by artwork category, for example:

```text
typography
logo
mixed
watercolor
metallic
distressed
flat illustration
heavy wrinkle
low resolution
heavy occlusion
```

Do not promote a change from average score alone if it causes a serious cohort regression.

### 24.5 Regression tiers

```text
Smoke Set
   ↓
Regression Set
   ↓
Golden Holdout
   ↓
Promotion Gate
```

### 24.6 Visual Diff

Harness UI should provide source/target/result comparison, synchronized 100/200/400% zoom, difference heatmaps and targeted crops for text, fine lines, gradient, texture and alpha edge.

### 24.7 Shadow evaluation

Production continues using the Champion while a Challenger may run on selected jobs without affecting user output.

No router/model/prompt/QC policy may self-promote based solely on AI Judge results.

## 25. Champion / Challenger promotion

Version:

- router;
- prompts;
- provider mapping;
- QC thresholds;
- recipes;
- model settings.

A challenger is promoted only after measured evidence shows acceptable quality and no critical regression, while cost/latency/resource use satisfies policy.

Production behavior must never mutate automatically from a few recent jobs.

## 26. Reproducibility and security

Every final master has an `ArtifactManifest` containing:

- source hashes;
- parent artifact hashes;
- model/provider versions;
- prompt/policy/schema versions;
- parameters and seed where supported;
- final export profile;
- QC report.

Text extracted from images is treated as **data**, never as executable model instructions.

Remote providers receive only required artifacts. Credentials are stored securely and never written to logs, diagnostics or datasets.

## 27. Standalone local deployment

The Mini PC/local machine is the primary runtime.

Suggested product components:

```text
PODArtworkTool.exe
  ├─ Desktop UI
  ├─ Engine Service/Process
  ├─ Local model runtime
  ├─ Logger
  ├─ Auto Updater
  ├─ Checkpoint/cache manager
  └─ Dataset/Harness management
```

The tool should not require users to manually start Python or PowerShell in normal use.

Heavy local work may include:

- local reconstruction models;
- OCR;
- OpenCV;
- segmentation/matting;
- vector tracing/rendering;
- SR;
- texture/material processing;
- alpha/edge refinement;
- 6K–8K working composition.

If hardware is insufficient or the user chooses a remote-capable mode, the Router can use configured remote providers.

## 28. Optional VPS role

A VPS is **optional**, not required for the basic standalone workflow.

Possible VPS responsibilities:

- update/release manifest hosting;
- optional telemetry aggregation;
- optional remote job history;
- optional centralized dataset registry;
- remote provider orchestration;
- multi-machine expansion later.

The standalone app must still process local jobs when the VPS is unavailable, except for features that explicitly require remote providers or update/telemetry services.

## 29. Recommended source structure

```text
pod-artwork-tool/
│
├─ desktop/
│  ├─ ui/
│  └─ updater/
│
├─ engine/
│  ├─ preflight/
│  ├─ identity/
│  ├─ fusion/
│  ├─ analyzer/
│  ├─ pipeline/
│  ├─ router/
│  ├─ providers/
│  ├─ vector/
│  ├─ texture/
│  ├─ sr/
│  ├─ alpha/
│  ├─ compositor/
│  ├─ qc/
│  ├─ artifacts/
│  └─ checkpoints/
│
├─ contracts/
│
├─ learning/
│  ├─ experience_store/
│  ├─ retrieval/
│  ├─ recipe_learning/
│  └─ dataset_registry/
│
├─ harness/
│  ├─ datasets/
│  ├─ recipes/
│  ├─ runner/
│  ├─ evaluators/
│  ├─ visual_diff/
│  └─ scorecards/
│
├─ telemetry/
│  ├─ logger/
│  ├─ metrics/
│  ├─ traces/
│  └─ diagnostics/
│
├─ data/
│  ├─ models/
│  ├─ cache/
│  ├─ jobs/
│  └─ datasets/
│
├─ scripts/
│
└─ docs/
```

The architecture should keep provider adapters replaceable so model changes do not rewrite the core application.

## 30. Implementation roadmap

### Phase 0A — Standalone foundation

Build:

- desktop shell;
- separate engine process;
- job IDs/state machine;
- typed contracts;
- artifact manifest;
- structured logger;
- diagnostic bundle;
- auto-update + rollback;
- checkpoint/cache foundation.

### Phase 0B — Historical data foundation

Build:

- historical input/final importer;
- pairing;
- artwork identity;
- deduplication;
- Dataset Registry;
- Golden Holdout.

### Phase 0C — Harness foundation

Build:

- benchmark case schema;
- recipe schema;
- runner;
- semantic/technical/operational metrics;
- scorecards;
- visual diff;
- smoke/regression/golden tiers.

### Phase 1 — Reconstruction V1

Build:

- pre-flight;
- artwork detection;
- DesignSpec;
- deterministic text/logo route;
- one local/remote reconstruction route;
- alpha;
- final export;
- two-stage QC.

### Phase 2 — Hybrid quality

Build:

- multi-reference fusion;
- material separation;
- vector/raster split;
- difficult texture handling;
- SR comparison;
- region confidence.

### Phase 3 — Reliability

Build:

- smart capability router;
- provider fallback;
- region rescue;
- bounded retry;
- crash resume;
- resource-aware scheduling;
- manual review.

### Phase 4 — Learning

Build:

- Experience Store;
- similar-case retrieval;
- Recipe memory;
- structured user feedback;
- policy/prompt analytics;
- Champion/Challenger and shadow evaluation.

### Phase 5 — Domain optimization

Evaluate:

- local model adaptation;
- fine-tune/LoRA where justified;
- synthetic paired dataset;
- improved ranker/QC models;
- batch workflows.

### Phase 6 — Expansion

Optional:

- VPS synchronization;
- multiple local/GPU workers;
- cloud GPU workers;
- Creative Asset Manager integration;
- external API.

## 31. Non-goals and truthfulness

- Do not claim 4500×5400 or 300-DPI metadata alone proves print quality.
- Do not claim exact recovery of information hidden or destroyed in the source.
- Do not report synthesized/SR-invented detail as recovered ground truth.
- Do not vectorize complex raster texture merely to label the output “vector”.
- Do not lock the architecture to a single AI provider before benchmark evidence.
- Do not let learning automatically modify production without regression and promotion gates.
- Do not require VPS availability for the basic standalone local workflow.

## 32. Open benchmark decisions

Keep these configurable until evidence is available:

- exact local reconstruction model/version;
- exact remote provider mappings;
- local VLM/OCR stack;
- SR/restoration implementation;
- vectorization/tracing engine;
- hardware/VRAM requirements;
- candidate count by quality mode;
- retry/fallback limits;
- QC thresholds;
- provider budget limits;
- per-job latency/cost targets;
- when region rescue is preferable to full retry.

Any validated decision that changes production behavior should update this document so the repository retains one canonical technical history.
