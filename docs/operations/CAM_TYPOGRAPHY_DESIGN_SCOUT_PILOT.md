# CAM Typography Design Scout — minimal Pinterest pilot

Updated 2026-10-30. Goal: populate Stage 1 Typography Ideas with discovery candidates for real Stage 0 picked keywords, using the existing Windows Pinterest Scout stack and existing Managed Storage. It must **not** autoapprove copyrighted source artwork, run G3 AI, create an ImageGen output, or queue Stage 2.

## Operation

1. Keep the existing CAM V2 API-only canary tenant. Set the separate `TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED=true` flag in the **existing** protected API canary env only after the release and tests pass. ImageGen, G3 worker, V2 Output publish and Stage 2 handoff stay OFF.
2. Update the Windows `D:\Desify_Agentic\scan_pinterest` repo to the merged `main` (fast-forward only, preserve `scout.local.env`, Chrome profiles and local data). Run `START_SCOUT_TYPOGRAPHY.cmd`.
3. PowerShell uses the current Scout agent pairing/token and Python venv. **The first pass reuses source Pin URLs/images already discovered by Stage 0 for selected keywords**, uploading them as unreviewed candidates without opening Pinterest or requiring a browser login. These are *reused Stage 0 sources*, not newly discovered designs. For fresh design discovery it uses a separate Chrome profile `pinterest-profile-typography`. On first login run `START_SCOUT_TYPOGRAPHY_LOGIN.cmd`, sign into Pinterest in the normal Chrome window, close it, and rerun `START_SCOUT_TYPOGRAPHY.cmd`. If the live browser needs login after importing Stage 0 sources, the script keeps and reports the imported candidates. No cookies are copied from Review/Keyword profiles and no verification is bypassed. It runs a **single** bounded scan, not a service.
4. The Windows client requests up to three most recently picked Stage 0 keywords for **fresh discovery** and, separately, up to three picked source Pins not yet ingested for **Stage 0 seed import**. The server skips previously imported Pin URLs (including rejected/archived) and collapses multiple picked keywords pointing to the same Pin, so successive one-shot runs advance instead of downloading the same three sources. It imports these valid source images as candidates. When the isolated browser has a valid Pinterest session, it also searches two typography/cap-design queries per recent keyword, max 18 candidates per query, at most 24 downloads and eight ingests total per run. `--seed-only` disables the browser and performs the existing-source import only. It accepts only HTTPS Pinterest Pin pages and Pinterest CDN image content up to 8 MiB, without redirects.
5. POST each image via agent-token endpoint `/api/v1/realistic-review-ugc/typography/scout-agents/{agent_id}/references`. Backend reconfirms the agent tenant, feature flag, picked keyword, Pin URL, content type, decoded image and max daily 40 candidates before managed storage. Duplicate image hashes are idempotent.
6. New Typography Ideas rows have `source_type=scout`, `source_platform=pinterest`, original Pin URL, scoped keyword, status `candidate`, rights `unknown`, analysis `pending`. Existing references are not modified. Human must independently verify rights and explicitly approve before G3 Style DNA or ImageGen.
7. In CAM Stage 1 > Typography Ideas, filter Source=Scout, review, reject irrelevant images, verify permissions/attribution where appropriate. Only then run manual G3 analysis, accept Style DNA and use Scout-to-Prompt Style Lab.

## Safe checks

- API `/ready` returns 200; DB already at 0189, no new migration required.
- API tests: token auth; feature flag OFF denies; foreign keyword denied; malicious host denied; upload/dedup; existing candidate cannot approve unknown rights.
- Windows unit tests: strict Pin host and path, bounded keyword queries, blocked oversized/non-Pinterest CDN.
- Windows first-run does not require Pinterest login to reuse existing picked keyword source images. *Fresh design discovery* does require its own manually authenticated Pinterest session. Do not copy other users' browser profiles or bypass CAPTCHA. Distinguish `picked_source_candidates` from `new_search_candidates` in real run metrics.
- Record real created/duplicate/failed counts after a successful run; do not claim candidates have been imported until database shows `source_type=scout`.
- Do not turn on `TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED`, `TYPOGRAPHY_V2_PILOT_WORKER_ENABLED`, `TYPOGRAPHY_V2_OUTPUT_PUBLISH_ENABLED` or `TYPOGRAPHY_V2_STAGE2_HANDOFF_ENABLED` as part of Design Scout rollout.
- Avoid unattended recurring scanning during pilot; no new workers, broker, database table or Cloudflare provider.

## Rollback

Disable only `TYPOGRAPHY_V2_DESIGN_SCOUT_ENABLED`, restart API and verify /ready. Stop or close the one-shot Windows Scout. Existing candidate images remain reviewable; no user media or other Scout jobs are deleted. Backend rollback pointer remains usable. The separate Windows Chrome profile and saved Scout config are preserved.
