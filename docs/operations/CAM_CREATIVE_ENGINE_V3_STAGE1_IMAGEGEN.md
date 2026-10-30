# CAM Creative Engine V3 — Native Stage 1 Artwork (2026-10-30)

## Objective and status

Translate an already completed **Creative Brief V1** into **six original embroidery masters** for GatorHats Products Stage 1 without changing the older V1 / Typography V2 workflows. Three meaning-led modes (Typography-First, Creative Twist, Explore) each get independent variants **A** and **B**. This is **native ImageGen**, not the deterministic Google Fonts/PIL renderer and not a cap mockup.

| Component | Role |
| --- | --- |
| `creative_v3_generation.py` | Tenant-scoped, immutable V1 fingerprint, exact quote, 6 prompts |
| `creative_v3_runner.py` | Sequential bounded native ImageGen, copy-on-write per-processing checkpoints, G5 RGBA alpha QA |
| Existing `rrugc_keyword_image_generate` worker | Real generation and existing versioned Managed Storage / Stage 1 Output History |
| `CreativeBriefDialog` | Explicit **Generate 6 embroidery designs** after completing V1, with a gated authenticated status probe |
| New `creative-brief-v3` bundled Skill | Isolated single-slot ImageGen contract; installed by standard backend deploy |
| Existing Stage 2 | **No automatic handoff**; user must inspect and explicitly pick a design |

## Process

```
Picked keyword (exact, unchanged)
   -> Completed Creative Brief V1 (strict 3 modes)
   -> User explicitly clicks "Generate 6 embroidery designs"
   -> Authorize tenant, feature, revision and native Skill
   -> Pin SHA-256 of V1 source + keyword in existing processing job
   -> 1–2 Typography-First, 3–4 Creative Twist, 5–6 Explore
   -> Each slot: compare live brief fingerprint -> native ImageGen
     -> isolated native SHA-256 provenance -> transparent RGBA
     -> center-only padding if safe -> 2048x1024 G5 gate
     -> unique digest + source fingerprint revalidation
   -> ALL six technical passes required
   -> Existing Managed Storage + Stage 1 Output History
   -> User visually reviews spelling, colors, stitch scale, originality
   -> User optionally picks and hands off to Stage 2 ThreadTransfer
```

## Security and limits

- V3 is disabled by default and requires **all** of: `CREATIVE_ENGINE_V3_ENABLED=true`, `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=true`, `CREATIVE_ENGINE_V3_CANARY_TENANT_IDS` containing the exact authorized tenant, plus existing global `IMAGE_GENERATION_ENABLED=true` and `CODEX_IMAGE_GENERATION_ENABLED=true`.
- No auto-run or cron; only an authenticated user with `realistic_review_ugc.run` can queue.
- One request = six distinct native ImageGen slots; use the existing AI queue/concurrency quotas. The Native runner requires Codex login and actual availability of built-in `$imagegen`, neither is implied by a passing unit test.
- A keyword must be still picked, have its **current** completed Creative Brief V1 and be <=150 characters. An old brief, disabled feature, changed quote, different tenant or changed V3 Skill is denied.
- `expected_brief_updated_at` prevents stale modal submissions. Processing payload pins V1 content fingerprint. Revalidation is required **before and after each paid call**. V1 contents are never run as system commands.
- All six images must pass **technical** checks (correct alpha, canvas, visible footprint, no duplicate). A passing technical test **does not** verify pixel spelling, visual fidelity to the hook, trademark compliance or physical stitch feasibility. These require human inspection; output `font_info_json` records this honestly.
- A previous output pointer and selected assets are retained while a new job runs. New jobs have separate private per-processing staging locations and cannot read partial designs from prior versions. No migration/new worker/new output table.
- No fallback to Cloudflare FLUX, API-key imagegen, PIL stitch overlay, flat font rendering, or synthetic files. Missing native ImageGen is a failed explicit job, **not** a fake success.
- On successful managed-storage upload, V3 temporary checkpoints are removed. Failed or deferred job checkpoints are scoped to the failed processing ID; production disk retention can be cleaned separately after recoveries.
- Existing V1 generation, V2 Style Lab, selected Stage 1 assets and Stage 2 are not regenerated automatically. This version does not launch Stage 2.

## Release and smoke protocol

1. Unit/regression tests: `test_creative_v3_generation.py`, `test_creative_engine_v3.py`, `test_keyword_images.py`, `CreativeBriefDialog.test.tsx`, TS typecheck, frontend build.
2. Deploy from `main`; verify `/ready`, `/version`, frontend `build-info.json` and worker services.
3. Verify feature OFF by default and zero new image-generation jobs.
4. Enable the two V3 flags plus an explicit **one-tenant** canary only after verifying actual Codex/ImageGen runtime and the original source's permission.
5. In an authenticated real Stage 1 keyword with completed brief, click once, monitor six native slots and Managed Storage history. Inspect image legibility before moving it to Stage 2.
6. Disable `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED` immediately for rollback without touching Creative Brief V1 or existing Stage 1 history.

**Do not claim generation completed until the live provider produced six verified images and the six stored outputs are visible in Stage 1.**
