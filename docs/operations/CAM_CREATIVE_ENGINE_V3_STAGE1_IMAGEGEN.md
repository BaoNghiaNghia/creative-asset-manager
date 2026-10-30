# CAM Creative Engine V3 — Native Stage 1 Artwork (2026-10-30)

## Objective and status

Translate an already completed **Creative Brief V1** into **six original embroidery masters** for GatorHats Products Stage 1 without changing the older V1 / Typography V2 workflows. Three meaning-led modes (Typography-First, Creative Twist, Explore) each get independent variants **A** and **B**. This is **OpenAI GPT Image API generation** (default model `gpt-image-2.5-flare`), not the deterministic Google Fonts/PIL renderer, Codex CLI tool, or a cap mockup. The previous Codex CLI experiment failed: its runtime had no `image_gen` tool despite showing `image_generation=true`.

| Component | Role |
| --- | --- |
| `creative_v3_generation.py` | Tenant-scoped, immutable V1 fingerprint, exact quote, 6 prompts |
| `creative_v3_openai_runner.py` | Direct OpenAI Images API, six separate API calls, bounded bytes, per-processing checkpoints and G5 RGBA alpha QA |
| `creative_v3_runner.py` | Shared checkpoint/QA helper and optional legacy Codex-only path (disabled by default for V3) |
| Existing `rrugc_keyword_image_generate` worker | Real generation and existing versioned Managed Storage / Stage 1 Output History |
| `CreativeBriefDialog` | Explicit **Generate 6 embroidery designs** after completing V1, with a gated authenticated status probe |
| New `creative-brief-v3` bundled Skill | Legacy Codex single-slot contract retained for rollback; **not** required for direct API generation |
| Existing Stage 2 | **No automatic handoff**; user must inspect and explicitly pick a design |

## Process

```
Picked keyword (exact, unchanged)
   -> Completed Creative Brief V1 (strict 3 modes)
   -> User explicitly clicks "Generate 6 embroidery designs"
   -> Authorize tenant, feature, revision and native Skill
   -> Pin SHA-256 of V1 source + keyword in existing processing job
   -> 1–2 Typography-First, 3–4 Creative Twist, 5–6 Explore
   -> Each slot: compare live brief fingerprint -> OpenAI Images API
     -> real base64 PNG response + SHA-256 -> transparent RGBA
     -> center-only padding if safe -> 2048x1024 G5 gate
     -> unique digest + source fingerprint revalidation
   -> ALL six technical passes required
   -> Existing Managed Storage + Stage 1 Output History
   -> User visually reviews spelling, colors, stitch scale, originality
   -> User optionally picks and hands off to Stage 2 ThreadTransfer
```

## Security and limits

- V3 is disabled by default and requires **all** of: `CREATIVE_ENGINE_V3_ENABLED=true`, `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=true`, `CREATIVE_ENGINE_V3_CANARY_TENANT_IDS` containing the exact authorized tenant, plus `IMAGE_GENERATION_ENABLED=true`, `PROCESSING_JOBS_ENABLED=true`, and a valid `OPENAI_API_KEY`. **The new API path does not require `CODEX_IMAGE_GENERATION_ENABLED`.**
- No auto-run or cron; only an authenticated user with `realistic_review_ugc.run` can queue.
- One request = six distinct paid image API calls, serialized within the current Stage 1 worker and existing tenant AI queue/concurrency quotas. Each call has `max_retries=0` to avoid unbounded spending. A provider-side rate limit with 1-5 valid local checkpoints can resume using the existing bounded worker recovery; failures before producing an image do not automatically rerun paid calls.
- A keyword must be still picked, have its **current** completed Creative Brief V1 and be <=150 characters. An old brief, disabled feature, changed quote, different tenant or changed V3 Skill is denied.
- `expected_brief_updated_at` prevents stale modal submissions. Processing payload pins V1 content fingerprint. Revalidation is required **before and after each paid call**. V1 contents are never run as system commands.
- All six images must pass **technical** checks (correct alpha, canvas, visible footprint, no duplicate). A passing technical test **does not** verify pixel spelling, visual fidelity to the hook, trademark compliance or physical stitch feasibility. These require human inspection; output `font_info_json` records this honestly.
- A previous output pointer and selected assets are retained while a new job runs. New jobs have separate private per-processing staging locations and cannot read partial designs from prior versions. No migration/new worker/new output table.
- The **OpenAI Image API explicitly uses a server-side API key**, unlike the old Codex CLI path. No fallback to Cloudflare FLUX, PIL stitch overlays, flat font rendering, third-party API or synthetic output. API rejection, missing credentials or unverified alpha is a genuine failed job, never a fake success.
- On successful managed-storage upload, V3 temporary checkpoints are removed. Failed or deferred job checkpoints are scoped to the failed processing ID; production disk retention can be cleaned separately after recoveries.
- Existing V1 generation, V2 Style Lab, selected Stage 1 assets and Stage 2 are not regenerated automatically. This version does not launch Stage 2.

## Release and smoke protocol

1. Unit/regression tests: `test_creative_v3_generation.py`, `test_creative_engine_v3.py`, `test_keyword_images.py`, `CreativeBriefDialog.test.tsx`, TS typecheck, frontend build.
2. Deploy from `main`; verify `/ready`, `/version`, frontend `build-info.json` and worker services.
3. Verify feature OFF by default and zero new image-generation jobs.
4. Provision a valid OpenAI API key in production's secret environment; ensure the OpenAI project has image-model access and available billing. Set `CREATIVE_ENGINE_V3_IMAGE_PROVIDER=openai`, `CREATIVE_ENGINE_V3_OPENAI_MODEL=gpt-image-2.5-flare`, `CREATIVE_ENGINE_V3_OPENAI_QUALITY=medium`, and `CREATIVE_ENGINE_V3_OPENAI_TIMEOUT_SECONDS=300`. Enable the two V3 flags plus an explicit **one-tenant** allowlist only after validation. Rollback is `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=false`. Never put the key in code, chat, logs or a committed file.
5. In an authenticated real Stage 1 keyword with completed brief, click once, monitor six native slots and Managed Storage history. Inspect image legibility before moving it to Stage 2.
6. Disable `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED` immediately for rollback without touching Creative Brief V1 or existing Stage 1 history.

**Do not claim generation completed until the live provider produced six verified images and the six stored outputs are visible in Stage 1.**

## Actual deployment vs. availability

- **Code deploy is separate from canary activation.** The source update can be on `main` while the authenticated `/keyword-creative-briefs/v3/generation-status` endpoint legitimately returns `available:false`.
- The CodeLocal authorized source checkout does not grant write access to `/etc/creative-asset-manager/production.env`. Do not work around that boundary. An authorized production operator must edit the protected env and restart API/image workers. Be sure the key has project access and billing; **Codex ChatGPT login does not authenticate API usage**.
- A real end-to-end PASS requires a user-authorized picked keyword with a completed V1 brief, a direct click on the Stage 1 button, six real responses from the Images API, successful G5 checks, and six Output History records verified read-only. Backend unit tests with mocked API responses are not enough.
- For this implementation, the GPT Image API produces transparent `1536x1024` PNG with `background=transparent`, then the existing transparent-only padding helper centers its unchanged native pixels onto the `2048x1024` Stage 1 canvas. No synthetic stitched image is substituted.
