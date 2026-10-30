# CAM Typography Design Scout — minimal Pinterest pilot

Updated 2026-10-30. Goal: populate Stage 1 Typography Ideas with discovery candidates for real Stage 0 picked keywords, using the existing Windows Pinterest Scout stack and existing Managed Storage. It must **not** autoapprove copyrighted source artwork, run G3 AI, create an ImageGen output, or queue Stage 2.

## Operation

1. Keep the existing CAM V2 API-only canary tenant. Set the separate `TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED=true` flag in the **existing** protected API canary env only after the release and tests pass. ImageGen, G3 worker, V2 Output publish and Stage 2 handoff stay OFF.
2. Update the Windows `D:\Desify_Agentic\scan_pinterest` repo to the merged `main` (fast-forward only, preserve `scout.local.env`, Chrome profiles and local data). Run `START_SCOUT_TYPOGRAPHY.cmd`.
3. PowerShell uses the current Scout agent pairing/token and Python venv, and starts a **separate** Chrome profile `pinterest-profile-typography`. **First login:** run `START_SCOUT_TYPOGRAPHY_LOGIN.cmd`, sign into Pinterest in the normal Chrome window, then close Chrome and rerun `START_SCOUT_TYPOGRAPHY.cmd`. The Scout prints `PINTEREST_LOGIN_REQUIRED` on a failed unauthenticated scan; it does not copy cookies from Review/Keyword profiles or bypass verification. It runs a **single** bounded scan, not a service.
4. The Windows client requests up to three most recently picked Stage 0 keywords from CAM (agent-scoped API), then searches Pinterest for two typography/cap-design queries per keyword. It collects max 18 candidates per query, 24 attempted downloads per run and 8 successful ingests. It accepts only HTTPS Pinterest Pin pages and Pinterest CDN image content up to 8 MiB. No redirects.
5. POST each image via agent-token endpoint `/api/v1/realistic-review-ugc/typography/scout-agents/{agent_id}/references`. Backend reconfirms the agent tenant, feature flag, picked keyword, Pin URL, content type, decoded image and max daily 40 candidates before managed storage. Duplicate image hashes are idempotent.
6. New Typography Ideas rows have `source_type=scout`, `source_platform=pinterest`, original Pin URL, scoped keyword, status `candidate`, rights `unknown`, analysis `pending`. Existing references are not modified. Human must independently verify rights and explicitly approve before G3 Style DNA or ImageGen.
7. In CAM Stage 1 > Typography Ideas, filter Source=Scout, review, reject irrelevant images, verify permissions/attribution where appropriate. Only then run manual G3 analysis, accept Style DNA and use Scout-to-Prompt Style Lab.

## Safe checks

- API `/ready` returns 200; DB already at 0189, no new migration required.
- API tests: token auth; feature flag OFF denies; foreign keyword denied; malicious host denied; upload/dedup; existing candidate cannot approve unknown rights.
- Windows unit tests: strict Pin host and path, bounded keyword queries, blocked oversized/non-Pinterest CDN.
- Windows first-run may require Pinterest login; if not authenticated, do not silently capture other users' browser profiles or use CAPTCHA bypass.
- Record real created/duplicate/failed counts after a successful run; do not claim candidates have been imported until database shows `source_type=scout`.
- Do not turn on `TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED`, `TYPOGRAPHY_V2_PILOT_WORKER_ENABLED`, `TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED` or `TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED` as part of Design Scout rollout.
- Avoid unattended recurring scanning during pilot; no new workers, broker, database table or Cloudflare provider.

## Rollback

Disable only `TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED`, restart API and verify /ready. Stop or close the one-shot Windows Scout. Existing candidate images remain reviewable; no user media or other Scout jobs are deleted. Backend rollback pointer remains usable. The separate Windows Chrome profile and saved Scout config are preserved.
