# Etsy Spy Video UGC private CDN & poster delivery (2026-11-22)

## Architecture and scope

Existing canonical Google Drive uploads are preserved. A successful verified Muse upload may create up to three **disposable** objects in the already-existing private R2 bucket `creative-asset-video-cache`:

- `video-cache/{tenant}/{source_sha256}/playback.mp4` — H.264 MP4, remuxed with `-movflags +faststart` when needed.
- `video-cache/{tenant}/{source_sha256}/poster-320.webp` — small video selector preview.
- `video-cache/{tenant}/{source_sha256}/poster-640.webp` — modal poster.

The browser initially requests only WebP. MP4 is mounted **after clicking Play**. One player exists at a time. All requests go through the authenticated Etsy Spy listing/Picked/tenant gate before deciding between the signed Worker, R2 API fallback, or verified Google Drive stream.

R2 objects are staged only on verified ingest or an explicitly reviewed operator backfill, not on GET or view. R2 PUT uses `If-None-Match: *` to prevent overwriting and extension of the **fixed 30-day lifecycle** on repeated requests. No new bucket, database, R2 credentials, provider account, public R2 URL, background queue, or persistent VPS media directory is introduced.

## Activation and compatibility

The new `CAM_MUSE_UGC_R2_ENABLED` flag overrides the existing image-derivative R2 canary when present. If unset, the existing `CAM_ETSY_IMAGE_SHARED_R2_ENABLED` flag and `CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS` are reused (currently production tenant `boostgame` only). `CAM_MUSE_UGC_R2_CANARY_TENANTS` can override the tenant list. There is no wildcard tenant fallback.

`CAM_MUSE_UGC_EDGE_ENABLED=false` is the default. **Do not turn edge on until the current existing Worker has the WebP posterior route changes deployed and separate valid synthetic signed HEAD probes return 404 for missing `playback.mp4`, `poster-320.webp`, and `poster-640.webp`.** When edge is off or not ready, the application uses private R2 proxy. If R2 fails or lacks data, video falls back to the original private Drive route; WebP poster falls back to the browser icon (never fetch 40 MiB MP4 just to show a thumbnail).

Original Listing Image PNG, Native Master, original Drive MP4, per-tenant jobs, content QA states, queue and review evidence are not modified. Existing video jobs created **before** enabling the R2 cache have no posters until a separately reviewed, bounded backfill (not implied by enabling the feature).

An explicit **read-only dry-run** can inspect up to 25 recent, verified files without modifying Drive, database or R2:

```sh
python -m scripts.backfill_muse_ugc_media_r2 --tenant boostgame --limit 10
```

Only after reviewing the dry-run results and confirming the R2 quota, an operator can authorize the same command with `--apply` using the existing protected runtime. The backfill is intentionally not an automated page-view side effect and never handles rows older than 29 days.

## Operational safeguards

- Prevent anonymous cross-tenant access: all video and poster requests require `etsy_spy.read`, active tenant and a current Picked record.
- Worker signed URLs are bearer capabilities valid for ~540 seconds (subject to current Worker TTL). Never log their token/signature; editing Picked permissions stops future URL issuance. Previously issued URLs can remain usable for the short ticket duration.
- Cloudflare R2 bucket remains private. Worker key path and payload content type are strict.
- The existing `video-cache/` R2 lifecycle deletes objects after 30 days; Worker rejects expired objects even before physical deletion.
- Do not deploy the Worker without Cloudflare credentials or inspect secrets through browser or logs. Do not enable the image Worker gate until its synthetic signed image HEAD preflight passes.
- On newly uploaded videos, derivative preparation may add up to 45 seconds to the ingestion request, with a best-effort fallback; this requires monitoring and may be moved into the existing job scheduler if load demands.
- Known remaining risk: authenticated production playback, Cloudflare Worker edge delivery and media timing metrics require an operator-enabled signed-in canary.

## Verification and rollback

```sh
PYTHONPATH=apps/api python3 -m unittest discover -s apps/api/tests/modules/muse_bridge -p test_ugc_media_cache_stdlib.py -v
cd infrastructure/cloudflare/r2-video-worker && npm test && npm run typecheck
cd apps/client && npx vitest run app/etsy-spy/VideoUGCCell.test.tsx app/etsy-spy/EtsySpyPage.test.tsx
cd apps/client && npm run typecheck && node scripts/etsy-video-ugc-modal-smoke.mjs
```

In a production release, verify `/live`, `/ready`, static JS/CSS, video `206 Content-Range` under authenticated tenant scope, poster `200 image/webp`, `X-CAM-UGC-Delivery`, and compare 5-min `ugc_r2_playback`/`ugc_r2_poster` HIT/MISS.

Rollback without deleting source assets:
1. Set `CAM_MUSE_UGC_R2_ENABLED=false` to disable the new video cache; `CAM_MUSE_UGC_EDGE_ENABLED=false` disables Worker redirects separately.
2. Roll back API using official `scripts/cam-rebuild-backend.sh --rollback`; roll back frontend with `scripts/deploy-cam-frontend.sh --rollback` only if needed.
3. Do **not** delete original Drive files or disable unrelated Public Review video caching. Existing disposable R2 objects age out from the original upload date.

No migration is required. Production IAM/Cloudflare account changes are not authorized by this feature alone.
