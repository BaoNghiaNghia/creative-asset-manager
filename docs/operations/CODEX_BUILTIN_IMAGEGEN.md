# Built-in Codex imagegen for CAM (2026-10-26)

## Source and installation

- Official OpenAI skill: https://github.com/openai/skills/tree/main/skills/.system/imagegen
- Pinned upstream SHA: `49f948faa9258a0c61caceaf225e179651397431`
- CAM vendors the unmodified Apache-2.0 skill in `deploy/codex/skills/imagegen/` (including license, original references and fallback CLI).
- The reviewed backend release pipeline syncs the repo-managed Skill into the service account CODEX_HOME; `scripts/cam-codex-runtime.sh prepare` safely syncs it without changing auth files.
- Codex CLI 0.159.3 reports `image_generation stable true`, and the account is logged in with ChatGPT. Its own `skills/.system/imagegen` is also visible to Codex; this does NOT imply every execution can perform reference editing.
- The official built-in tool requires no `OPENAI_API_KEY`. The explicit CLI/API fallback does, and is NOT authorized or used for this CAM workflow.

## Evidence and gates

- First-party `scripts/cam-codex-runtime.sh smoke` generated one real PNG via built-in imagegen (1254 x 1254, 1,844,780 bytes), as the service account without any API-key fallback.
- `scripts/cam-codex-imagegen-reference-smoke.sh` tests one isolated Concept Short C1 reference edit, not a six-image paid batch. It uses `view_image` on the original code-built reference before the native edit, keeps the exact original stitch prompt, and requires final PNG bytes to match the new native artifact.
- JSONL telemetry can omit built-in `image_generation` events even on success. For `concept-short-v1`, the worker checks the per-attempt private native `generated_images` cache before safe cleanup, comparing content hashes to the published PNGs. Files without genuine native provenance are not accepted solely because Codex exited successfully. The worker also validates PNG structure, dimensions, names, uniqueness, source plan and tenant/job state.
- The per-job generated image cache must be empty at start. Failed source plans and code-generated imitation stitches remain blocked. Existing final checkpoints and published output histories are not deleted.
- Neither smoke test writes to production DB or Managed Storage; it never retries generation automatically.

## Provider selection

- `CONCEPT_SHORT_CLOUDFLARE_EDIT_ENABLED` remains the explicit gate for the existing Cloudflare Concept Short adapter. Installing the Skill alone does not disable Cloudflare or change paid provider routing.
- **Switch to Codex only after a real reference-edit canary succeeds**, with verified image provenance and acceptable text/embroidery fidelity. Do not silently switch to an OpenAI API-key path.
- The existing Cloudflare path may remain enabled while Codex built-in image generation is installed; this avoids breaking running/past jobs or their output versions.
- Concept Short allows 1-6 genuine edited PNGs; skipped ineligible source slots do not force fabricated filler images.
- Built-in tool output dimensions are provider-controlled. Never upscale, re-encode or fabricate thread texture merely to meet a hard-coded pixel count.

## Release, rollback and limits

- Copy a verified commit to `main`; use the standard immutable backend release script to install it. Keep current/previous releases and respect release gates.
- For a reversible provider migration, change only the audited provider flag via the existing protected production configuration flow; restart only affected worker services and verify readiness.
- Verify service-account login, imagegen basic smoke, one real reference edit, test suite, failed/partial job semantics and image output history before announcing that native image-edit fully replaces Cloudflare.
- To rollback the native integration, restore the previous immutable backend release and/or leave the Cloudflare flag enabled. Do not delete original skills, production credentials, completed images or user design histories.
