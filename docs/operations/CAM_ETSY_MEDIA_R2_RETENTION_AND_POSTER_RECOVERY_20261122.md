# Etsy Spy private media cache: 30-day guard and poster recovery

## What this release changes

- The original, private Google Drive PNG and MP4 remain canonical and are never exposed to a public bucket.
- Etsy Listing Image R2 derivative PUT is create-once `If-None-Match: *`. Retrying a thumbnail/720px WebP never renews its R2 age. A conflicting existing object is only accepted after content-type, length, and checksum metadata agree.
- The image API refuses R2 bytes that lack a trustworthy S3 `LastModified` or are outside the fixed 0..30-day age window, then preserves the existing authorized Drive fallback.
- The *source code* of the existing R2 Video Worker now rejects image objects without a trustworthy `uploaded` timestamp, rejects objects older than 30 days, and caps internal edge cache TTL to the remaining absolute lifetime. Old cache entries without the new expiry marker are bypassed.
- The existing bounded Muse Video UGC R2 backfill no longer skips a video when `playback.mp4` already exists but one or both WebP posters are absent. It repairs only missing derivative identities; the upload implementation uses conditional PUT and cannot overwrite the existing playback object.

## Cloudflare rollout gate

The production browser image/video Worker flag must **remain OFF** until its deployed version returns signed synthetic `HEAD 404` for a missing private image path, and for all required UGC MP4/WebP paths. This canary is cryptographically signed, with tenant/Picked permission checks occurring first in CAM.

The existing `creative-asset-video-cache` bucket must have two non-overlapping 30-day deletion rules:

- `video-cache/` (already tracked by the existing Video lifecycle tool)
- `etsy-images/v1/` (new rule, ID `cam-etsy-images-v1-expire-30d`)

The new lifecycle tool uses the **existing S3 R2 credentials**, no new bucket or database. It reads and validates the entire existing rule list and preserves unrelated/video/multipart rules. A conflicting or unknown overlapping expiration blocks the write. Check first:

```sh
python3 deploy/tools/r2_etsy_image_lifecycle_30d.py plan
python3 deploy/tools/r2_etsy_image_lifecycle_30d.py check
```

Only after the read succeeds and existing rules are reviewed:

```sh
python3 deploy/tools/r2_etsy_image_lifecycle_30d.py apply --confirm-prefix etsy-images/v1/
python3 deploy/tools/r2_etsy_image_lifecycle_30d.py check
python3 deploy/tools/etsy_shared_r2_preflight.py --probe
```

As checked on 2026-11-22, the production S3 credentials were rejected for bucket lifecycle **read** (`lifecycle_read_rejected`), and Wrangler was not logged in. Do not claim lifecycle applied, launch an image backfill, or enable the image Worker gate until an operator grants the narrow Cloudflare bucket lifecycle/Worker deployment permissions and the preflight passes. Never paste tokens into chat, source, logs, or shell arguments.

## Controlled video-poster backfill

The CLI reads at most 25 of the newest boostgame Muse jobs, accepts only verified Google Drive receipts and jobs less than 29 days old, and never writes to DB or local persistent storage:

```sh
cd apps/api
python -m scripts.backfill_muse_ugc_media_r2 --tenant boostgame --limit 10
```

With the exact dry-run scope reviewed, run `--apply` manually only when current R2 quota and service load allow. This is never triggered on media view, and is not a queue-wide rerun. The JSON output includes `missing_playback` and `missing_posters` as aggregate counts; no listing IDs or media paths are exposed.

## Verification and rollback

- `python3 -m unittest deploy.tests.test_r2_etsy_image_lifecycle_30d -v`
- `PYTHONPATH=apps/api python3 -m unittest discover -s apps/api/tests/modules/etsy_spy -p test_listing_shared_r2_stdlib.py -v`
- `cd infrastructure/cloudflare/r2-video-worker && npm test && npm run typecheck`
- Check signed access, cache HIT/MISS, production `/version` and `/ready`.
- Roll back the backend through the existing official release script. Keep CDN Worker toggles OFF until signed preflight passes. Neither rollback nor release cleanup deletes original Drive media.

No schema migration, new persistent queue, new database, or local image/video archive is required.
