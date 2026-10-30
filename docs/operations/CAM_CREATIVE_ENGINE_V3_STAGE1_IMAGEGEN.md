# CAM Creative Engine V3 — Native Stage 1 Artwork (2026-10-30)

## Objective and status

Translate an already completed **Creative Brief V1** into **six original embroidery masters** for GatorHats Products Stage 1 without changing the older V1 / Typography V2 workflows. Three meaning-led modes (Typography-First, Creative Twist, Explore) each get independent variants **A** and **B**. **Provider policy (2026-10-30): Codex CLI first, no separate Image API spend.** The direct OpenAI Images API is optional only after explicit operator opt-in; it is OFF in production. Current Codex CLI has `image_generation=true` but the session did not expose `image_gen` on 0.159.3 or isolated 0.166.0, so V3 image generation remains disabled. No fake/flat PIL stitching fallback and no automatic Stage 2 handoff.

| Component | Role |
| --- | --- |
| `creative_v3_generation.py` | Tenant-scoped, immutable V1 fingerprint, exact quote, 6 prompts |
| `creative_v3_openai_runner.py` | Optional paid API path retained for explicit operator opt-in; NOT selected by default or enabled on production |
| `creative_v3_runner.py` | Preferred Codex CLI `$imagegen` generation with ChatGPT sign-in, native SHA-256 provenance, isolated checkpoints, and G5 RGBA QA; currently blocked by tool availability |
| Existing `rrugc_keyword_image_generate` worker | Real generation and existing versioned Managed Storage / Stage 1 Output History |
| `CreativeBriefDialog` | Explicit **Generate 6 embroidery designs** after completing V1, with a gated authenticated status probe |
| `creative-brief-v3` bundled Skill | Preferred isolated one-slot Codex CLI contract, six times per batch; native tool availability is required |
| Existing Stage 2 | **No automatic handoff**; user must inspect and explicitly pick a design |

## Process

```
Picked keyword (exact, unchanged)
   -> Completed Creative Brief V1 (strict 3 modes)
   -> User explicitly clicks "Generate 6 embroidery designs"
   -> Authorize tenant, feature, revision and native Skill
   -> Pin SHA-256 of V1 source + keyword in existing processing job
   -> 1–2 Typography-First, 3–4 Creative Twist, 5–6 Explore
   -> Each slot: compare live brief fingerprint -> Codex CLI built-in $imagegen
     -> verified native image SHA-256 -> transparent RGBA
     -> center-only padding if safe -> 2048x1024 G5 gate
     -> unique digest + source fingerprint revalidation
   -> ALL six technical passes required
   -> Existing Managed Storage + Stage 1 Output History
   -> User visually reviews spelling, colors, stitch scale, originality
   -> User optionally picks and hands off to Stage 2 ThreadTransfer
```

## Security and limits

- V3 is disabled by default and requires **all** of: `CREATIVE_ENGINE_V3_ENABLED=true`, `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=true`, `CREATIVE_ENGINE_V3_CANARY_TENANT_IDS` containing the exact tenant, `IMAGE_GENERATION_ENABLED=true`, `PROCESSING_JOBS_ENABLED=true`, `CODEX_IMAGE_GENERATION_ENABLED=true`, and `CREATIVE_ENGINE_V3_IMAGE_PROVIDER=codex`. Codex must have an authenticated ChatGPT session and the built-in image tool must actually be exposed. An `OPENAI_API_KEY` is **not** needed or passed to the Codex subprocess. The paid API alternative has separate explicit configuration and is OFF.
- No auto-run or cron; only an authenticated user with `realistic_review_ugc.run` can queue.
- One request = six distinct Codex built-in imagegen calls, serial within the existing Stage 1 worker. They count toward the logged-in Codex plan's usage limits, but **not** separately billed Image API calls. If built-in `image_gen` is absent, fail explicitly without any synthetic fallback or paid API call. Valid 1–5 PNG checkpoints may be preserved for bounded recovery.
- A keyword must be still picked, have its **current** completed Creative Brief V1 and be <=150 characters. An old brief, disabled feature, changed quote, different tenant or changed V3 Skill is denied.
- `expected_brief_updated_at` prevents stale submissions. The processing payload pins V1's content fingerprint and chosen provider; verify before and after each generation call. V1 contents are data, never shell instructions.
- All six images must pass **technical** checks (correct alpha, canvas, visible footprint, no duplicate). A passing technical test **does not** verify pixel spelling, visual fidelity to the hook, trademark compliance or physical stitch feasibility. These require human inspection; output `font_info_json` records this honestly.
- A previous output pointer and selected assets are retained while a new job runs. New jobs have separate private per-processing staging locations and cannot read partial designs from prior versions. No migration/new worker/new output table.
- The Codex subprocess explicitly strips `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_ORGANIZATION`, `OPENAI_PROJECT`, `CODEX_API_KEY` and `OPENAI_EXECUTOR_API_KEY` before launching. No fallback to Image API, Cloudflare FLUX, PIL stitch overlays, flat fonts or synthetic output. Built-in tool unavailability and invalid alpha are genuine job failures, not false successes.
- On successful managed-storage upload, V3 temporary checkpoints are removed. Failed or deferred job checkpoints are scoped to the failed processing ID; production disk retention can be cleaned separately after recoveries.
- Existing V1 generation, V2 Style Lab, selected Stage 1 assets and Stage 2 are not regenerated automatically. This version does not launch Stage 2.

## Release and smoke protocol

1. Unit/regression tests: `test_creative_v3_generation.py`, `test_creative_engine_v3.py`, `test_keyword_images.py`, `CreativeBriefDialog.test.tsx`, TS typecheck, frontend build.
2. Deploy from `main`; verify `/ready`, `/version`, frontend `build-info.json` and worker services.
3. Verify feature OFF by default and zero new image-generation jobs.
4. Confirm the actual worker-selected ChatGPT-authenticated `CODEX_IMAGE_HOME` can perform **one real native** `$imagegen` call and pass the G5 gate (CLI flag alone is insufficient). Keep `CREATIVE_ENGINE_V3_IMAGE_PROVIDER=codex`. Only then enable `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=true` for the one-tenant canary. Until then, leave it `false`; do not spend paid Image API credits. Do not update the production Codex CLI binary merely because a newer version exists unless its isolated image tool probe succeeds.
5. In an authenticated real Stage 1 keyword with completed brief, click once, monitor six native slots and Managed Storage history. Inspect image legibility before moving it to Stage 2.
6. Disable `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED` immediately for rollback without touching Creative Brief V1 or existing Stage 1 history.

**Do not claim generation completed until the live provider produced six verified images and the six stored outputs are visible in Stage 1.**

## Actual deployment vs. availability

- **Code deploy is separate from canary activation.** The source update can be on `main` while the authenticated `/keyword-creative-briefs/v3/generation-status` endpoint legitimately returns `available:false`.
- Authorized CodeLocal workspaces exist for the source and `/etc/creative-asset-manager` separately. Production V3 is presently `CREATIVE_ENGINE_V3_IMAGE_PROVIDER=codex` with `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=false`. Protected env owner and mode are `root:creative-assets 640`. Use only the authorized environment workspace for changes; restart API/image workers after any change.
- A real end-to-end PASS requires a picked keyword with completed V1 brief, explicit Stage 1 launch, six verified **Codex built-in native images**, successful G5 checks, and six managed Output History records. Mocked provider tests and `codex features list` are insufficient.
- If built-in Codex creates valid transparent RGBA PNG (e.g., 1536 × 1024), the existing transparency-only crop/pad helper may center unchanged native pixels on a 2048 × 1024 canvas. No synthetic thread drawing is allowed. The latest isolated CLI probe (0.166.0) also returned `image_gen unavailable` and wrote no image.
