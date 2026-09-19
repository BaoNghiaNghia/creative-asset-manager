# Cloudflare R2 Video CDN / Hot Cache Design

Status: **Proposed**  
Scope: **Video playback only**  
Branch target: **main**  
Repository: `BaoNghiaNghia/creative-asset-manager`

## 1. Purpose

This document defines the Cloudflare R2/CDN strategy for Creative Asset Manager with one hard product constraint:

> R2 is used only as a **hot cache for original-quality video**. Images are not cached in R2.

The primary goals are:

- reduce video startup latency in Review/Public Review;
- reduce repeated streaming through the API/VPS;
- reduce repeated reads from Google Drive / OneDrive;
- preserve the **highest available source video quality**;
- keep R2 storage comfortably below the 10 GB free-storage allowance target;
- avoid changing the existing source-of-truth model.

This is intentionally **not** a migration of the asset library into R2.

---

## 2. Current system behavior

The current public review media path is approximately:

```text
Browser
  -> FastAPI public review preview endpoint
  -> public-share/session authorization
  -> SourceAssetContentResolver
  -> Google Drive / OneDrive provider
  -> StreamingResponse
  -> Nginx
  -> Browser
```

Relevant current code:

- `apps/api/app/modules/public_review/public_router.py`
- `apps/api/app/modules/assets/content_resolver.py`
- `apps/api/app/modules/explorer/preview.py`
- `apps/api/app/modules/storage/*`

Important current behavior:

- public review media is streamed through FastAPI;
- source providers are resolved on-demand;
- range headers are forwarded to the provider;
- public media concurrency is currently bounded with `PUBLIC_MEDIA_CONCURRENCY = 3`;
- public preview responses are currently returned with `Cache-Control: no-store, private`;
- image thumbnail/preview logic already has its own bounded in-memory behavior;
- managed storage currently targets Google Drive and has existing staging-capacity / cleanup patterns.

This means the existing implementation is safe as a source path, but repeated video playback can repeatedly consume:

- API worker time;
- DB/provider resolution;
- OAuth/provider calls;
- VPS bandwidth;
- public media concurrency slots.

---

## 3. Design decision

### 3.1 What R2 stores

R2 stores only:

```text
original video bytes
```

Examples:

- MP4 source -> exact MP4 bytes;
- MOV source -> exact MOV bytes;
- other supported video source -> exact original bytes.

No resizing, no bitrate reduction, no frame-rate reduction, and no re-encoding before cache storage.

### 3.2 What R2 does not store

Do not store these in this cache:

- images;
- thumbnails;
- image previews;
- AVIF-to-WebP conversions;
- generated images;
- PDFs;
- audio-only files;
- AI metadata;
- comments/notes;
- search indexes;
- temporary video-search chunks;
- long-lived transcoded review proxies.

Images keep using the current provider/thumbnail/preview path.

### 3.3 Source of truth

Google Drive / OneDrive remain the source of truth.

R2 is disposable.

Deleting an object from R2 must never delete or alter the source object.

---

## 4. Target architecture

```text
                        SOURCE OF TRUTH

                 Google Drive / OneDrive
                           |
                           | original video
                           v
                SourceAssetContentResolver
                           |
                    cache miss only
                           v
                 Video Cache Service
                           |
                           v
                 Cloudflare R2
                 HOT VIDEO CACHE
                 original bytes only
                           |
                           v
                 Cloudflare Edge
                           |
                           v
                 Review / Public Review
```

For a cache hit, video bytes should not pass through FastAPI.

The preferred delivery path is:

```text
Browser
  -> FastAPI authorization / media-ticket endpoint
  -> short-lived signed media URL
  -> Cloudflare edge endpoint
  -> private R2
  -> Browser
```

FastAPI remains responsible for authorization. Cloudflare/R2 handles the video byte path.

---

## 5. Cache key strategy

Use immutable, content-addressed keys.

Recommended format:

```text
video-cache/{tenant_id}/{content_hash}/original
```

Optionally preserve a safe extension:

```text
video-cache/{tenant_id}/{content_hash}/original.mp4
video-cache/{tenant_id}/{content_hash}/original.mov
```

Do not use the user-visible filename as the primary cache identity.

The important identity is:

```text
tenant_id + content_hash
```

Benefits:

- duplicate filenames do not create duplicate cache entries;
- renamed files reuse the same cached object;
- identical source content can be deduplicated within a tenant;
- cache URLs can be immutable;
- CDN caching becomes predictable.

Cross-tenant deduplication is intentionally not required.

---

## 6. Storage budget

The design target is to remain comfortably below the 10 GB storage allowance.

Use decimal-byte limits:

```env
R2_VIDEO_CACHE_SOFT_LIMIT_BYTES=8000000000
R2_VIDEO_CACHE_HARD_LIMIT_BYTES=9000000000
```

Interpretation:

- **8 GB soft target**: desired steady-state maximum;
- **9 GB hard trigger**: eviction must run before admitting more data;
- **1 GB headroom**: protects against concurrency, cleanup delay, multipart remnants, accounting drift, and operational mistakes.

Do not intentionally fill R2 to exactly 10 GB.

### 6.1 Maximum single object

A single source video should not be allowed to monopolize the cache.

Recommended initial rule:

```text
maximum cacheable object = min(
    configured absolute limit,
    20% of hard quota
)
```

With a 9 GB hard quota:

```text
20% ~= 1.8 GB
```

Suggested configuration:

```env
R2_VIDEO_CACHE_MAX_OBJECT_BYTES=1800000000
```

If a video is larger than this, bypass R2 and use the existing source stream.

This is a cache policy, not an upload restriction on the underlying source provider.

---

## 7. Cache admission policy

Do not cache videos during source sync.

Source sync should continue to ingest metadata only.

A video becomes eligible for R2 only when it is actually requested for playback.

### Eligible

```text
media_type starts with video/
AND size is known or safely bounded
AND size <= R2_VIDEO_CACHE_MAX_OBJECT_BYTES
AND content hash is available
```

### Not eligible

```text
image/*
audio/*
application/pdf
unknown unsafe media
oversized video
missing stable identity
```

The default behavior for an ineligible video is the existing provider-backed stream.

---

## 8. Cache-on-demand flow

### 8.1 Cache hit

```text
User opens video
  -> authorize request
  -> lookup video_cache_objects
  -> object ready in R2
  -> issue signed media URL
  -> edge/R2 serves video
  -> update last_accessed_at asynchronously
```

The API should not proxy the video body on a cache hit.

### 8.2 Cache miss

The first implementation should optimize for responsiveness and safety:

```text
User opens video
  -> authorize request
  -> cache miss
  -> keep current provider-backed playback path working
  -> enqueue background R2 cache fill
  -> later views use R2
```

This avoids making first playback wait for a complete cache-fill operation.

A later optimization may implement a tee/fan-out path, but that should not be the first rollout because it makes retry, disconnect, partial upload, and Range handling more complex.

### 8.3 Concurrent cache misses

If multiple users request the same uncached video at the same time:

- only one cache-fill job should win;
- other requests continue using current playback behavior;
- do not upload the same object multiple times.

Use a DB uniqueness constraint and/or advisory lock keyed by:

```text
tenant_id + content_hash
```

---

## 9. Proposed database model

Create a cache-specific table instead of reusing `asset_storage_objects`.

Reason:

`asset_storage_objects` currently models managed storage semantics such as:

- provider-backed stored assets;
- `staging` / `durable`;
- AI-job lifecycle protection;
- managed-storage cleanup.

R2 video cache has different semantics and should remain disposable.

Recommended table:

```text
video_cache_objects
```

Suggested fields:

```text
id
tenant_id
asset_id
source_asset_id
content_hash

r2_key
mime_type
size_bytes
etag

status
  preparing
  ready
  deleting
  failed

created_at
updated_at
cached_at
last_accessed_at
access_count

last_error_code
last_error_message
```

Recommended uniqueness:

```text
UNIQUE (tenant_id, content_hash)
```

Useful indexes:

```text
(status)
(last_accessed_at)
(tenant_id, last_accessed_at)
```

---

## 10. Quota enforcement

Quota enforcement must happen in the application, not only through R2 lifecycle rules.

### 10.1 High-water / low-water strategy

```text
normal operation <= 8 GB

new object would push usage > 9 GB
  -> stop admission
  -> evict old cache objects
  -> continue until projected usage <= 8 GB
  -> admit new object if enough capacity exists
```

Pseudo-flow:

```text
required = incoming_object_size
usage = tracked_ready_bytes + tracked_preparing_bytes

if required > max_object_bytes:
    bypass_cache

if usage + required > hard_limit:
    evict_lru_until(
        projected_usage + required <= soft_limit
    )

if still insufficient:
    bypass_cache

reserve(required)
upload
mark_ready(actual_size)
```

### 10.2 Atomic reservation

Reuse the same design principle already present in managed storage:

- reserve bytes before upload;
- serialize quota-sensitive reservations;
- use a PostgreSQL advisory lock when running on PostgreSQL;
- include `preparing` objects in reserved usage;
- release reservation on terminal failure.

This prevents two workers from each seeing 8.5 GB and both uploading 1 GB.

---

## 11. Eviction policy

Start with deterministic LRU.

Evict:

```sql
ORDER BY last_accessed_at ASC, cached_at ASC, id ASC
```

Only `ready` cache entries are eligible.

Do not delete:

- objects currently being prepared;
- objects actively being deleted;
- objects pinned by a future explicit policy.

Initial design does not require pinning.

Eviction flow:

```text
lock row
  -> verify still eligible
  -> mark deleting
  -> delete R2 object
  -> delete or tombstone DB row
```

If the R2 object is already absent, treat the operation as successful cleanup.

If delete fails transiently:

- keep the DB record;
- mark/retry safely;
- do not lie about freed bytes.

---

## 12. Last-access tracking

Updating the database on every Range request would create unnecessary write load.

A media playback may generate multiple HTTP requests.

Preferred behavior:

- update access time at ticket creation or logical playback open;
- not on every R2 Range request;
- debounce repeated accesses to the same video.

Example policy:

```text
do not update last_accessed_at more than once per object every 5 minutes
```

This is sufficient for LRU quality.

---

## 13. Range request support

Original-quality video can be large, so Range support is mandatory.

The final delivery endpoint must support:

```http
Range: bytes=...
```

and correct partial responses:

```http
206 Partial Content
Accept-Ranges: bytes
Content-Range: ...
```

Do not require the browser to download the whole object before playback.

This is especially important when:

- source files are hundreds of MB or larger;
- users seek inside the video;
- the review modal automatically plays media;
- users switch between items rapidly.

---

## 14. Autoplay and next-item behavior

The Review modal already supports sequential media navigation.

R2 should not trigger cache-fill for an entire folder.

Default policy:

```text
current video:
  normal cache-on-demand

next video:
  optional warm-up

previous video:
  do not proactively warm

all other folder videos:
  do nothing
```

Start with:

```env
R2_VIDEO_PREFETCH_ENABLED=false
```

Enable next-item prefetch only after observing real usage.

If enabled, use strict controls:

- only one next video;
- only if cache capacity is healthy;
- only if next asset is video;
- only if next object is below max object size;
- low-priority background job;
- cancel or ignore stale work when navigation changes.

---

## 15. Image behavior

Images are explicitly excluded from R2.

Do not modify R2 storage for:

- public thumbnails;
- Explorer thumbnails;
- AVIF preview conversion;
- source images;
- generated image previews.

Existing image behavior should remain independent.

This distinction should be enforced in code, not just documentation.

Recommended boundary:

```python
if not media_type.startswith("video/"):
    return CACHE_NOT_APPLICABLE
```

Do not add a generic "cache every media type" abstraction in the first implementation.

---

## 16. Video quality policy

R2 stores the source bytes unchanged.

Therefore:

```text
source quality == cached quality
```

Do not run the R2 cache path through:

- `video_search/proxy.py`;
- FFmpeg resize;
- 720p conversion;
- FPS reduction;
- bitrate reduction;
- audio recompression.

The existing video-search proxy pipeline remains for its own purpose and should not become the R2 playback cache.

### Browser compatibility

Original quality does not guarantee browser compatibility.

For example, some source codecs/containers may not play on every browser.

The R2 cache should preserve current application behavior:

- if the original source currently plays, the R2 copy should play identically;
- if the original source is not browser-compatible, R2 alone does not fix that.

A future compatibility-derivative design can be introduced separately without changing the "original cache" policy.

---

## 17. Secure delivery

R2 should remain private.

Do not expose a permanent public R2 URL for review assets.

Recommended flow:

```text
Browser
  -> CAM API
  -> authorize tenant/share/session/asset/source
  -> issue short-lived media ticket
  -> Browser requests media domain
  -> edge verifies signature + expiry
  -> edge reads private R2
```

Recommended signed URL lifetime:

```text
5-15 minutes
```

The ticket should bind at least:

- tenant/cache object identity;
- expiry;
- signature version.

Do not put provider OAuth tokens in URLs.

Do not use user-visible filenames as authorization.

### Important cache rule

Authorization and CDN caching must be separated correctly.

Do not simply change the current cookie-protected preview route from:

```http
Cache-Control: no-store, private
```

to public caching.

The current endpoint is authorization-sensitive.

Create a dedicated signed media delivery path.

---

## 18. Cloudflare Worker / edge responsibilities

A minimal edge handler should:

1. parse the signed request;
2. validate expiry;
3. validate HMAC/signature;
4. resolve the R2 object key;
5. process GET/HEAD;
6. support Range;
7. return correct Content-Type;
8. return immutable/cache-friendly headers for content-addressed objects;
9. never expose bucket listing;
10. never permit arbitrary object-key traversal.

The edge should not make authorization calls back to the CAM API on every byte-range request.

Authorization happens when the media ticket is issued.

---

## 19. Suggested configuration

Application:

```env
R2_VIDEO_CACHE_ENABLED=false

R2_ACCOUNT_ID=
R2_BUCKET_NAME=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=

R2_VIDEO_CACHE_SOFT_LIMIT_BYTES=8000000000
R2_VIDEO_CACHE_HARD_LIMIT_BYTES=9000000000
R2_VIDEO_CACHE_MAX_OBJECT_BYTES=1800000000

R2_VIDEO_MEDIA_BASE_URL=https://media.example.com
R2_VIDEO_MEDIA_SIGNING_SECRET=
R2_VIDEO_MEDIA_TICKET_TTL_SECONDS=600

R2_VIDEO_PREFETCH_ENABLED=false
R2_VIDEO_CACHE_ACCESS_TOUCH_SECONDS=300
```

Use the project's secret-management conventions.

Never commit:

- R2 secret access keys;
- signing secrets;
- provider OAuth secrets.

---

## 20. Cache lifecycle safety net

R2 lifecycle rules may be used as a safety net, not as the primary quota controller.

Suggested categories:

```text
video-cache/tmp/
  short retention

video-cache/{tenant}/{content_hash}/...
  application-controlled LRU
```

The application remains responsible for staying below the configured byte budget.

A lifecycle rule alone cannot implement true LRU because lifecycle rules do not understand application `last_accessed_at`.

---

## 21. Reconciliation

The DB is the quota ledger, but R2 is the physical truth.

Add a scheduled reconciliation job.

Responsibilities:

- detect DB `ready` rows whose R2 object is missing;
- detect abandoned `preparing` rows;
- detect old multipart/temp uploads;
- verify tracked size where practical;
- optionally detect R2 cache objects not represented in DB;
- repair usage accounting.

Recommended cadence:

```text
light cleanup: every 5-15 minutes
full reconciliation: daily
```

Do not list the entire bucket on every playback request.

---

## 22. Failure behavior

The cache must be optional.

Any R2 failure should degrade to the existing provider path whenever security allows it.

Examples:

### R2 lookup unavailable

```text
fallback -> current source stream
```

### Cache fill fails

```text
playback continues from source
mark cache row failed/retryable
```

### Quota exhausted

```text
attempt eviction
if insufficient -> bypass cache
```

### Oversized source video

```text
bypass cache
```

### Source changed during cache fill

```text
discard partial cache object
do not mark ready
retry using new content identity
```

R2 problems must not make the source asset unavailable if the existing source path still works.

---

## 23. Observability

Add metrics for:

```text
video_cache_hit_total
video_cache_miss_total
video_cache_bypass_total
video_cache_fill_started_total
video_cache_fill_completed_total
video_cache_fill_failed_total

video_cache_eviction_total
video_cache_bytes_ready
video_cache_bytes_reserved
video_cache_object_count

video_cache_ticket_issued_total
video_cache_ticket_failed_total
```

Useful dimensions:

- tenant;
- source provider;
- bypass reason;
- failure category.

Avoid high-cardinality labels such as asset ID or content hash in metrics.

Structured logs may include redacted IDs when needed.

Key ratios:

```text
cache hit ratio
R2 bytes / quota
cache-fill success rate
provider-stream reduction
median video startup time
p95 video startup time
```

---

## 24. Operational dashboard / alerts

Recommended warning thresholds:

```text
R2 tracked usage >= 8.0 GB -> warning / eviction expected
R2 tracked usage >= 8.5 GB -> elevated warning
R2 tracked usage >= 9.0 GB -> no new admission until cleanup
```

Alert if:

- cleanup is repeatedly failing;
- R2 and DB usage diverge significantly;
- cache fill failure rate spikes;
- signed media validation errors spike unexpectedly;
- cache hit ratio collapses;
- provider fallback traffic unexpectedly grows.

Do not rely only on Cloudflare billing alarms.

---

## 25. Suggested implementation boundaries

Recommended new code areas:

```text
apps/api/app/modules/video_cache/
    model.py
    repository.py
    service.py
    quota.py
    cleanup.py
    scheduler.py
    ticket.py

apps/api/app/providers/cloudflare/
    r2.py
```

Names may be adjusted to existing project conventions.

Keep R2 cache logic separate from:

- generic source provider access;
- managed Google Drive storage;
- video-search analysis proxies;
- image preview logic.

---

## 26. Reuse patterns already present in the repository

The codebase already contains useful patterns that should be reused conceptually.

### Capacity reservation

`apps/api/app/modules/storage/repository.py`

Existing managed storage already demonstrates:

- byte accounting;
- capacity checks;
- PostgreSQL advisory locking;
- reservation before upload.

Use the same concurrency discipline for R2 cache capacity.

### Cleanup

`apps/api/app/modules/storage/managed_cleanup.py`

Existing cleanup demonstrates:

- bounded batches;
- lock before delete;
- dry-run support;
- remote delete before DB cleanup;
- idempotent missing-object handling.

Use similar safety properties for cache eviction.

### Source streaming

`apps/api/app/modules/assets/content_resolver.py`

Reuse this as the source read path on cache miss/fill.

Do not create a second OAuth/provider implementation.

### Existing video-search proxy

`apps/api/app/modules/video_search/proxy.py`

Do **not** use its FFmpeg output as the R2 original-quality cache.

Its lifecycle and temporary-workspace patterns may still be useful references.

---

## 27. Rollout plan

### Phase 0 - Observability

Before changing delivery:

- measure current video startup time;
- measure source provider latency;
- measure public media concurrency saturation;
- measure average and p95 video size;
- calculate how many currently active videos fit inside 8 GB.

### Phase 1 - R2 cache backend, no serving

Feature flag:

```text
R2_VIDEO_CACHE_ENABLED=false
```

Implement:

- table;
- provider;
- quota accounting;
- cache fill;
- cleanup;
- reconciliation;
- metrics.

Test in staging.

### Phase 2 - Shadow/cache fill

Enable cache fill for selected tenants/users while playback still uses current source stream.

Validate:

- original byte integrity;
- object size;
- content hash;
- cleanup;
- quota enforcement.

### Phase 3 - Cache-hit delivery

Enable signed edge URLs for ready R2 objects.

Cache misses still use current stream.

### Phase 4 - Quota validation

Run long enough to verify:

```text
steady state around <= 8 GB
hard admission never crosses configured 9 GB
cleanup works under concurrency
```

### Phase 5 - Optional next-video warmup

Only after measuring benefit.

Do not enable whole-folder prefetch.

---

## 28. Test plan

Minimum high-value tests:

1. video cache hit returns a signed edge URL;
2. image request never creates an R2 cache object;
3. uncached video still plays through current fallback;
4. cache fill preserves exact source size/hash;
5. duplicate concurrent misses create one cache entry;
6. object larger than max-object policy bypasses cache;
7. projected quota > 9 GB triggers eviction before admission;
8. eviction runs until projected usage <= 8 GB;
9. insufficient reclaimable space causes safe bypass;
10. LRU selects oldest `last_accessed_at`;
11. failed R2 delete does not falsely free quota;
12. missing R2 object cleanup is idempotent;
13. source change during fill does not publish stale content;
14. signed URL expires correctly;
15. forged signature is rejected;
16. Range request returns correct partial data;
17. HEAD works without sending the body;
18. tenant A cannot receive tenant B cache object;
19. public review share scope is still enforced before ticket issuance;
20. R2 outage falls back to source playback;
21. cleanup/reconciliation can run repeatedly without corruption;
22. feature flag off restores current behavior.

---

## 29. Non-goals

This design does not attempt to:

- replace Google Drive / OneDrive as source storage;
- cache images in R2;
- introduce HLS/DASH;
- create adaptive bitrate renditions;
- downgrade video quality;
- transcode all assets;
- guarantee every original codec plays in every browser;
- cache every video in a folder;
- use R2 as permanent archival storage.

---

## 30. Final policy summary

```text
SOURCE
Google Drive / OneDrive

R2
original video only
no image
no quality reduction
hot cache only

IDENTITY
tenant_id + content_hash

ADMISSION
on playback demand only

QUOTA
8 GB soft target
9 GB hard admission trigger
~1 GB operational headroom below 10 GB target

EVICTION
LRU

DELIVERY
short-lived signed media URL
Cloudflare edge
private R2
Range support

MISS
fallback to current source stream
background cache fill

IMAGE
unchanged
no R2
```

The most important invariant is:

> **R2 is an optional, disposable acceleration layer. It must never become the only copy of an asset, and it must never be required for the source asset to remain usable.**
