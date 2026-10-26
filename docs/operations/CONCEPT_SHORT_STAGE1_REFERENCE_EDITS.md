# Concept Short v1: Stage 1 image-edit execution and partial results (2026-10-26)

## Why the ChatGPT Skill behaves differently in CAM

In a ChatGPT conversation, the Skill can call the ChatGPT image-edit tool *only when that tool is available in that ChatGPT runtime*. Installing its `SKILL.md` on the VPS does not grant Codex CLI access to the same session-specific image tool. The installed production Codex CLI had no configured MCP image generation server, and a real run produced 0 image-edit events. Its instruction `$imagegen` is not a working tool bridge by itself.

The Cloudflare FLUX.2 Klein reference edit is an **optional CAM-specific provider**, not a dependency of the original Skill. It uses existing production Cloudflare credentials. The OpenAI image-generation API, Gemini with image-edit capability, or another supported provider can be integrated separately; paid ChatGPT access alone does not authorize unattended API calls from CAM. Never imply ChatGPT image-tool calls were made through a standalone Codex job.

## Stage 1 contract

- The installed `concept-short-v1` provides fonts, quote, layout, C4/C7 and C5/C8 substitutions, prompt, and corrected C6 wraps. Do not replace its original composition rules with hardcoded faux embroidery.
- Its `make_concepts.py` may produce up to six **candidate source slots**. Source PNGs are flat references, not embroidered final images.
- A job can publish **one through six distinct actual edited PNGs**. Failed/skipped source slots and genuine provider refusals must not turn already successful edits into a failed job. Keep original slot numbering (e.g. `design_01.png`, `design_04.png`) even with gaps.
- Each published image must be a valid, nonduplicated, inside-workspace, correctly named full-size PNG. Reject symlink paths, previews, flat sources, invalid images, and code-generated imitation stitching.
- Only `concept-short-v1` receives this partial-outcome allowance. Other Stage 1 Skills keep their existing exact-count contracts until separately reviewed.
- For an in-progress Cloudflare job, skip a slot which returns `concept_short_flux_rejected` without rewording prompts, evading provider safeguards or replacing it with fake art. If a retryable failure occurs after one or more successful edits, preserve the valid subset.
- `status=completed` with `error_code=stage1_partial_output` indicates an explicitly partial result; the UI says `Partial` and shows the N/6 count. Historical versions are retained.
- Previous approved images and published Drive versions are protected. Incomplete replacement sets never trigger deletion of the previous versions.

## Recovery and rollout

- Previously failed `Las Vegas` can use `Publish N images` if the original failure was `concept_short_flux_rejected`, the matching failed worker attempt still has 1–5 independently validated actual edits, and no prior output history exists. This is an explicit authenticated RUN action and queues **upload-only** work. It does not repeat provider calls or re-render the original skill.
- The server validates tenant, job/processing state, original error category, output filename/PNG/digest, and absence of previously published assets before issuing upload-only work. Another tenant cannot trigger it.
- The old `Cowboys AND Tequila` error is historical. A newly invoked job uses the corrected C6 code, but old status is not altered silently.
- The remaining gateway uses `CONCEPT_SHORT_CLOUDFLARE_EDIT_ENABLED` with Cloudflare credential fields in root-owned `production.env`. Production use of the Cloudflare provider is **optional**, bills by actual calls, and should be controllable without editing the skill.
- Managed Storage, Processing and skill-registry gates are retained. No DB migration or new Python package is needed.
- The original image may be downscaled to 504px before submitting to Cloudflare (input limit <512px), so a correctly formatted PNG is not proof the text is visually perfect. Human review of glyphs and stitching is still appropriate.
- Verified single edit on the production VPS: Cloudflare HTTP 200, base64 image result, 1536×1024. Verify full job and read-only Output History after deployment rather than treating the one-image canary as full acceptance.

## Failure and rollback

- Zero genuine image edits: job fails; never publish a code-only source instead.
- Credential/auth errors fail closed; rate limits and provider failures preserve successful local checkpoints. No covert fallback to Codex or text-to-image.
- Disabling the provider flag turns off new Cloudflare requests. Do not send future Concept Short jobs to legacy Codex on that VPS unless an actual image-edit capability has been installed and tested.
- Rollback the normal backend and frontend releases with their reviewed scripts and health checks; no schema downgrade is required. Do not delete DB history, published Drive versions, source skills or remaining successful staging edits.
