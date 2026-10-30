# CAM Creative Engine V3 — Stage 1 Codex-only ImageGen

**Updated:** 2026-10-30
**Branch:** `main` only
**Production status:** Creative Brief V1 and V3 Preview are available. Image generation **remains OFF** while the built-in Codex CLI `image_gen` tool is absent. No six-image V3 run has succeeded end to end.

## Goal

Turn one completed Creative Brief V1 and its **exact** picked keyword into six original transparent satin-stitched embroidery masters for GatorHats Products Stage 1:

| Slots | V1 creative mode | Distinction |
| --- | --- | --- |
| 01–02 | Typography-First (`safe`) | Clear core direction, layout A/B |
| 03–04 | Creative Twist | Familiar format with a new meaningful hook, A/B |
| 05–06 | Explore | Bolder original idea and hierarchy, A/B |

Existing Stage 1 output versions stay intact while generation is pending. New assets pass technical QA and save to existing Managed Storage and Stage 1 Output History only when all six are ready. No automatic Stage 2 handoff. Image spelling and stitch feasibility still require human QA.

## Provider policy: Codex CLI only

- **Never call OpenAI Images API for V3.** The `creative_v3_openai_runner.py` adapter and its API model/quality controls have been **removed**. V3 only supports `CREATIVE_ENGINE_V3_IMAGE_PROVIDER=codex`; any other value denies generation, including when an API key is present.
- The Codex child process strips `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_ORGANIZATION`, `OPENAI_PROJECT`, `CODEX_API_KEY`, and `OPENAI_EXECUTOR_API_KEY`. No fallback through scripts, Cloudflare FLUX, a vendor API, PIL-rendered embroidery or a stock image.
- CLI sign-in uses existing ChatGPT subscription access and may consume **Codex usage limits or credits depending on the plan**. It must not be misrepresented as unlimited.
- The installed `creative-brief-v3` Skill is an isolated single-slot contract. The existing image worker invokes it up to six times, each using the actual built-in `$imagegen`. A genuine native PNG must match the job's isolated `generated_images` cache SHA-256 before any artwork can be accepted.
- The API's `/keyword-creative-briefs/v3/generation-status` is a config gate, **not a proof that the runtime supports `image_gen`**. Do not enable imagegen solely because `codex features list` reports `image_generation=true`.

## Native one-image capability probe (no paid API)

Run using the **same OS account and `CODEX_HOME` as the production image worker**, without printing authentication files or tokens:

```bash
python3 scripts/cam-creative-v3-codex-probe.py \
  --codex-home /var/lib/creative-asset-manager/codex

# Only if the non-generating probe reports ChatGPT login + image feature:
python3 scripts/cam-creative-v3-codex-probe.py \
  --codex-home /var/lib/creative-asset-manager/codex \
  --native --timeout 120
```

The script always removes API keys from the subprocess, requests **one** original transparent PNG, checks PNG header/alpha and native cache SHA-256, then deletes its temporary workspace. It outputs sanitized JSON only. `status=native_imagegen_verified` and `imagegen_available=true` are required; `ready_for_native_probe_not_verified` only means the CLI appears configured.

If the image tool is not exposed, `status=native_tool_missing_or_no_png`; keep ImageGen OFF. Do not install new Codex binaries into the production path just because they exist: prior isolated probes on CLI 0.159.3 and 0.166.0 did not provide the built-in image tool. Reevaluate a new binary in isolation, then test the **actual worker identity**, and only switch production after success.

## Current execution flow

```text
Stage 0 picked keyword
      ↓
Completed Creative Brief V1: three semantic concept directions
      ↓
Explicit user action: Generate 6 embroidery designs
      ↓
Authorize tenant + capability + brief revision
      ↓
Pin keyword/brief SHA-256 and Codex Skill digest in the existing job queue
      ↓
Run six independent native Codex imagegen operations
      ↓
Verify native pixel provenance and genuine transparent PNG
      ↓
G5 alpha/canvas/uniqueness checks + source fingerprint revalidation
      ↓
Versioned Stage 1 Output History (no overwrite)
      ↓
Human spelling/stitch review → optional Stage 2
```

Technical output: 2048 × 1024 transparent RGBA Stage 1 canvas; only safe transparent margin-padding, no artwork redraw. A new processing job gets its own private temporary directory. Six independent outputs are required, and duplicates or fabricated replacements are rejected. Source edits or tenant revocation abort the job.

If CLI exits normally but produces no native PNG, the V3 handler distinguishes a **missing built-in image tool** from other output errors. Missing tool is a nonretryable `creative_v3_native_tool_unavailable`, not a generic transient failure that repeatedly consumes quota.

## Safe production switch

Default state:

```env
CREATIVE_ENGINE_V3_ENABLED=true
CREATIVE_ENGINE_V3_IMAGE_PROVIDER=codex
CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=false
CREATIVE_ENGINE_V3_CANARY_TENANT_IDS=creative-assets-prod
```

The required existing flags `IMAGE_GENERATION_ENABLED`, `CODEX_IMAGE_GENERATION_ENABLED`, and processing queue must also be enabled. Retain the independent `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=false` until the **worker-authenticated** native probe verifies a real PNG. Only then set the flag to `true` and restart API + image workers. Verify one authorized keyword and six managed output versions before wider rollout.

**Rollback:** set only `CREATIVE_ENGINE_V3_IMAGEGEN_ENABLED=false` and restart API/image workers. The completed briefs, earlier image outputs, picked designs, Stage 2, and other Skills remain unchanged.

## Operational acceptance criteria

1. Unit tests for six concepts, prompt correctness, exact quote and unicode, tenant/revision constraints, Codex-only provider, credential scrubbing and rejected forgery pass.
2. Same-account Codex native one-image probe returns `native_imagegen_verified` with SHA-256 provenance; no API key is used.
3. V3 canary manually queues one brief, worker receives and generates six distinct native images, QA and Managed Storage both pass.
4. Output History shows exactly the new six slot versions and retains earlier images.
5. API and workers remain healthy, disk is stable, and temporary pilot files are cleaned.

**Never report item 3 or 4 as complete when only mocked test images exist.**
