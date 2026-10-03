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
