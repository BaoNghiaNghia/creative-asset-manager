# CAM Creative Brief Lite V3 — 2026-11-02

## Scope

- Preserve Stage 1's 1 Typography-First + 4 Creative Twist + 4 Explore contract.
- Use text-only Creative Brief for semantics, hierarchy, original compositions, and optional meaning-led accents. Its legacy `embroidery_plan` is advisory and excluded from the new native ImageGen prompt.
- Image generation's `stage1_embroidery_material.py` is the authoritative stitch/color/rendering profile for new jobs (`embroidery-first-lite-v4`). Older in-flight V1/V2/V3 snapshots keep their pinned prompts and QA checkpoints.
- Do not change managed `creative-brief-v3` Skill bundle during this release: existing queued jobs have a pinned SHA256 and would be rejected if the bundle changed. The app-provided material prompt supersedes old design guidance for NEW jobs.

## Automatic retry pause

- `RRUGC_CREATIVE_BRIEF_AUTO_RETRY_ENABLED=false` by default; no production env change required. Only Creative Brief Codex quota/auth/network failure is terminal (manual Retry analysis), without repeated Codex calls.
- Existing deferred Creative Brief jobs with a persisted Codex wait error are recognized and terminalized on their next claim without invoking Codex. Their images, unrelated jobs, and completed briefs remain unchanged.
- New briefs may still be explicitly queued; bounded worker-contention deferral (not provider retry) remains available. No recurring full-library backfill is started.
- To resume retry policy later, review and explicitly set the feature flag before restarting worker services; do not silently enable.

## QA boundaries

- Native PNG cache provenance/hash, alpha/canvas, and duplicate checks remain mandatory. For a bad technical output, the adapter may regenerate ONLY the failed slot once; quota, auth, network and source-change failures are never subject to this repair.
- Neither technical QA nor the UI validates spelling, glossy/plastic appearance, or physical stitch density. The Stage 1 full-image viewer explicitly exposes outstanding visual review checks before a design is picked for Stage 2.
- Do not present `completed` as production-certified; finished artwork still requires human verification.

## Release safety

- No migration, new provider, or new external dependency. Preserve Output History and all user assets.
- Use backend release script with `--no-migrate` and verified schema compatibility, frontend deploy script with matching committed `dist` provenance. Release from the committed main branch with independent rollback release retained.
- Note any unrelated pre-existing test failures separately; do not weaken tests or security gates to conceal them.
