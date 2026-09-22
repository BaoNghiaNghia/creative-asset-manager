# R2 video cache and derived playback — operations

Status: Phases 1–4E are implemented in code. Public Review Phase 4B is authorization-preserving and falls back to the source provider. The persisted delivery gate remains OFF by default. No production R2/Worker rollout is implied by this document.

## Boundaries

Google Drive/OneDrive remain authoritative. The original fill worker reads an exact tenant-qualified SourceAsset stream and never writes to a source provider or mutates source bytes. Only `video/*` with a valid SHA-256 identity and known positive size at most `R2_VIDEO_CACHE_MAX_OBJECT_BYTES` is eligible. The immutable original key is generated server-side as `video-cache/{tenant_id}/{sha256}/original`. When `R2_VIDEO_PLAYBACK_DERIVED_ENABLED=true`, a separate background video-worker job may create `video-cache/{tenant_id}/{sha256}/playback.mp4`; it never replaces the original. Public Review keeps its existing preview route. When the Phase 4A runtime gate is effective, an already-authorized exact video asset/source pair may receive a short redirect to private R2 delivery; every miss or safe delivery failure falls back to the existing provider stream. R2 remains private.

`R2_VIDEO_CACHE_ENABLED=false` disables enqueue, fill and periodic cleanup. Do not turn it on until migration 0083 is present, private bucket credentials are scoped, and the worker role includes `video_cache_fill`.

## Admission and quota

`ensure_video_cache_fill` creates a `video_cache_fill` processing job with only tenant, asset, source-asset and content-hash IDs. A tenant/hash row is unique. Existing READY or active PREPARING/RETRY work is reused. A FAILED fill needs an explicit `retry_failed=True` request. Jobs have five attempts and existing worker lease/backoff semantics.

An authorized Public Review video cache miss invokes this admission service only
after the active share/session, exact asset/source pair, effective runtime gate,
and tenant rollout scope have passed. The API waits only for durable idempotent
job admission, never for the upload. The current request continues through the
provider-backed stream, while a later request may use R2 after the worker marks
the object READY. Admission errors are swallowed at this optional cache boundary
without logging provider details or changing the public response.

A bucket-global PostgreSQL advisory transaction lock serializes every reservation and physical-delete accounting update. SQLite uses a process-local re-entrant lock only for deterministic local tests; multi-process SQLite is not a production quota authority.

Effective tracked bytes include original and derived physical bytes plus both original-fill and derived-playback reservations. Admission never exceeds the configured hard threshold. If a new reservation would cross it, READY objects are chosen by `last_accessed_at NULLS FIRST, cached_at, id` and physically deleted until projected usage including the incoming reservation is at most 8,000,000,000 bytes. If enough READY bytes cannot be reclaimed, the fill is bypassed. DELETING and PREPARING are never ordinary LRU candidates. A failed or uncertain remote delete remains counted.

Access-touch is a tenant-qualified repository primitive with a default 300-second debounce. Public Review touches LRU only after a signed delivery ticket is successfully minted.

## Derived Public Review playback

Migration `0089_r2_video_playback_derivative` adds a fail-safe derived-playback ledger. The feature defaults OFF. When enabled, a new original fill admits one `video_playback_prepare` job only after the immutable original is durably READY. Existing READY originals are backfilled by the cache cleanup runner in bounded `R2_VIDEO_PLAYBACK_BACKFILL_BATCH_SIZE` batches (default 2), never from the Public Review request path. Admission therefore never blocks the current playback request. Until the derivative is READY, signed delivery continues using the immutable original.

The video worker downloads the private R2 original into the configured video temp directory and runs `ffprobe`. Web-friendly H.264/AAC MP4 up to 1080p and below the configured bitrate threshold is remuxed only with `-c copy -movflags +faststart`. Other eligible heavy/non-web sources are converted to a bounded 1080p H.264/AAC `playback.mp4` with `+faststart`. The original object is never rewritten, transcoded in place, or used as a derived upload target.

Derived preparation is bounded by source size, output size, R2 quota reservation, local free-space preflight and a wall-clock FFmpeg timeout. Any preparation failure leaves the READY original usable. A READY derivative is preferred by the signer only when its exact canonical key, kind and positive size are valid; otherwise delivery falls back to the original. Cache eviction deletes both known canonical objects. The Cloudflare Worker accepts only the exact immutable `original` or `playback.mp4` key shapes and preserves the same signed-ticket and Range rules for both.

Rollout order: deploy migration/API/video-worker code and the updated Cloudflare Worker path rules first; verify FFmpeg/ffprobe exist on the video worker; then explicitly set `R2_VIDEO_PLAYBACK_DERIVED_ENABLED=true`. Do not enable the flag before migration 0089 and Worker support are live. Disabling the flag immediately makes the signer prefer originals again; it does not delete derivatives.

## Fill and recovery

The worker reloads the tenant, canonical asset, exact source link, current video MIME, hash and size before opening the provider stream and again before marking READY. The Phase 1 uploader uses 16 MiB multipart parts, SHA-256 and byte-count checks, and HEAD validation. A multipart upload ID is tracked in the cache row. Retryable source/R2 failures use the existing durable job retry; permanent identity/integrity failures become terminal. If abort/delete cannot be confirmed, the row remains in DELETING with conservative physical-byte accounting. Source bytes are never deleted by cache cleanup.

The operational worker runs bounded DB-driven cleanup at `R2_VIDEO_CACHE_CLEANUP_INTERVAL_SECONDS`. A PREPARING row older than `R2_VIDEO_CACHE_PREPARING_STALE_SECONDS` is only reclaimed when its fill job is absent or terminal, or when an exhausted processing attempt has an expired lease past the stale cutoff; pending and retrying jobs are not reclaimed merely for age. Known multipart IDs are aborted before remote deletion. Stuck DELETING rows are retried after their lease/retry time. No full bucket listing is performed. Explicit `VideoCacheCleanup.reconcile_ready()` HEAD-checks a bounded set of known READY keys, removes missing known objects, and cleans up size mismatches; unknown/out-of-prefix keys are never touched. Full-bucket orphan discovery is not implemented.

## Inspection, smoke and metrics

From `apps/api`, run `python -m app.operations.video_cache_cli` for read-only global counts, READY/reserved/DELETING bytes, soft/hard limits and pressure. It prints no object keys, tenant IDs, signed URLs or credentials.

The optional real connectivity smoke requires both `R2_VIDEO_CACHE_ENABLED=true` and `R2_VIDEO_CACHE_REAL_SMOKE=true` and runs `python -m app.operations.video_cache_smoke`. It PUTs a tiny generated object only under `video-cache-smoke-test/`, HEADs, GETs/verifies, DELETEs, and checks missing. Never use a production asset. Standard tests use mocked R2; the smoke is not run without explicit opt-in.

Fill started/completed/failed, eviction and bypass counters are emitted as safe structured log events and held process-locally. Process restarts reset those counters; the database-backed CLI is authoritative for current-byte gauges. No asset ID or hash is used as a metric label.

## Migration and rollback

Migration `0083_r2_video_cache_fill` adds fill ownership, multipart and cleanup lease fields/indexes and removes asset/source cascade FKs so the physical-byte ledger survives source deletion. Migration `0089_r2_video_playback_derivative` adds derived playback state, size/reservation accounting and job ownership; it performs no R2 or FFmpeg work. No R2 network activity occurs in Alembic. Before downgrading 0089, disable derived playback and drain `video_playback_prepare` jobs; the DB downgrade does not delete `playback.mp4` objects, so remove known derivatives through normal cache eviction/cleanup before dropping their ledger fields. Downgrade to 0082 still requires a preflight for cache rows whose asset/source has been deleted because 0082 restores cascade FKs. Production migration and deployment need separate authorization.

Known limits: cleanup is DB-driven and cannot discover remote objects whose ledger row was lost before 0083; tenant deletion can still cascade ledger rows and needs a separate tenant-offboarding policy. READY HEAD reconciliation is bounded/manual rather than a full periodic bucket scan. The optional smoke uses a dedicated prefix, not user media. Public Review CDN delivery remains disabled until the persisted runtime gate is explicitly enabled after rollout preflight.

## Phase 3A delivery foundation — not deployed

Backend cache fill does not require delivery configuration. Optional settings are
`R2_VIDEO_MEDIA_BASE_URL` (HTTPS origin in production), the high-entropy
`R2_VIDEO_MEDIA_SIGNING_SECRET` (at least 32 UTF-8 bytes, protected), and
`R2_VIDEO_MEDIA_TICKET_TTL_SECONDS` (1–3600, default 600). The Worker uses
the same secret and a private `VIDEO_CACHE_BUCKET` binding. Its optional
`R2_VIDEO_MEDIA_MAX_TTL_SECONDS` cannot exceed 3600. Do not place secrets in
TOML, logs, browser bundles or the repository.

Only a READY video row with an exact server-generated canonical key can be signed. When the derived feature is enabled and its ledger is READY, the signer prefers `playback.mp4`; otherwise it signs the immutable `original`. Phase 4B authorizes the public session/share and exact tenant/asset/source pair before signing. The signer itself still grants no application authority. The Worker validates method, exact path, version, expiry and HMAC before R2 access. GET streams the selected object; HEAD returns metadata only. Invalid tickets return

## Phase 3B Range and edge-cache operations - not deployed

The Worker supports one byte range only: bounded, open-ended, and suffix. It
rejects multiple or malformed ranges with 416 and never builds multipart
responses. A valid range uses R2 HEAD then native R2 ranged GET; it does not
load an original video into Worker memory. Cold ranges deliberately bypass edge
cache to avoid caching an unbounded collection of range variants.

Full authenticated GETs may use caches.default with an internal fixed-origin
canonical key derived only from the validated immutable pathname. v, exp, and
sig are never in this key. Authentication happens before every cache lookup.
Cache errors and all non-success responses are never stored. The bucket remains
private; do not enable r2.dev. A future Worker rollout must bind
VIDEO_CACHE_BUCKET, set R2_VIDEO_MEDIA_SIGNING_SECRET as a Worker secret, and
configure an approved HTTPS media.<domain>/* route separately. No route, DNS,
secret, deployment, or production setting was changed here.


## Phase 4A–4C rollout gate — not deployed automatically

Phase 4A stores `VIDEO_CDN_DELIVERY_ENABLED` in PostgreSQL. Migration 0084
seeds it OFF. It is not an environment variable and must remain OFF while the
API, Worker route, DNS/custom domain and secrets are being staged.

Phase 4B preserves the existing Public Review preview URL. After share/session
and exact `(asset_id, source_asset_id)` authorization, an eligible READY cache
row must match tenant, content hash, canonical asset ID and exact source-asset
ID before a short signed Worker redirect is returned. Any runtime/config/cache/
signing miss uses the source provider path.

Phase 4C adds a read-only preflight and CI coverage for the Worker package.
Before a production canary:

1. deploy the API/worker code and run Alembic to the single current head;
2. keep `VIDEO_CDN_DELIVERY_ENABLED=false`;
3. configure a private R2 bucket and scoped credentials;
4. deploy the R2 video Worker with `VIDEO_CACHE_BUCKET` bound to that private
   bucket; keep `r2.dev` and `workers.dev` disabled for the production media
   origin;
5. set the same signing secret in backend protected configuration and Worker
   secret storage; do not place it in Git/TOML/browser output;
6. enable cache fill and obtain at least one verified READY video while quota is
   at or below the configured soft limit;
7. run the fail-closed preflight:

```bash
cd apps/api
python -m app.operations.video_delivery_preflight \
  --require-production \
  --probe-worker
```

The optional Worker probe creates no R2 object. It signs one HEAD request for a
random `video-cache/cam-preflight/<sha256>/original` key. A correct route,
Worker secret and private R2 binding authenticate the ticket and return 404 for
the absent key. 403, network failure, an unexpected success response, stale DB
schema, runtime already enabled, unapproved public Cloudflare origin, quota
pressure, active deletion, or no READY canary object fail the preflight.

Only after the preflight is green should a platform administrator enable the
persisted delivery toggle with an audited reason. Rollback is to disable that
toggle. Disabling delivery does not delete R2 objects or mutate source assets.


## Phase 4D tenant canary and Worker rollout plan

Phase 4D adds a second rollout boundary beneath the persisted master toggle.
`VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS` is a comma-separated deployment setting
with a maximum of 100 explicit tenant IDs. With the default empty value and
`VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=false`, CDN delivery is deny-all
even if the persisted runtime toggle is accidentally enabled.

For a canary, set only the approved tenant IDs and keep global rollout false.
The Public Review resolver checks this tenant scope after share/session/exact
asset-source authorization but before cache lookup or signing. Non-canary
tenants continue through the existing provider stream.

Global rollout is a separate explicit state:
`VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=true` and the canary list must be
empty. The application rejects ambiguous configuration where both are set.

The production Worker config can be rendered without accepting any secret value:

```bash
python -m deploy.tools.r2_video_worker_rollout render \
  --worker-name cam-r2-original-video \
  --bucket-name YOUR_PRIVATE_R2_BUCKET \
  --media-host media.example.com \
  --max-ttl-seconds 600 \
  --output infrastructure/cloudflare/r2-video-worker/wrangler.production.json
```

The rendered config disables workers.dev, uses one Custom Domain, binds only
`VIDEO_CACHE_BUCKET`, and declares
`R2_VIDEO_MEDIA_SIGNING_SECRET` as a required secret name. The renderer never
reads or writes the secret value. The rollout plan first inspects the expected
remote Worker project and stops if it does not exist; first-time Worker account
bootstrap remains a separately approved operator action.

Inspect the non-executing remote rollout plan with:

```bash
python -m deploy.tools.r2_video_worker_rollout plan \
  --config infrastructure/cloudflare/r2-video-worker/wrangler.production.json \
  --release-tag phase4d-RELEASE
```

Keep the application runtime toggle OFF while Worker routing/secrets are staged.
Before enabling the tenant canary, run both signed probes:

```bash
cd apps/api
python -m app.operations.video_delivery_preflight \
  --require-production \
  --probe-worker \
  --probe-ready-object
```

The missing-key probe expects 404. The READY-object probe issues HEAD only and
requires 200 with the durable byte size, video content type and byte-range
support. Neither probe downloads a video body or prints tenant/asset/object
identity. Redirects are not followed; any 3xx response fails the probe.

Only after those checks are green should the platform-admin runtime toggle be
enabled. Rollback order is: disable the application runtime toggle first, then
roll back the Worker version if needed. Provider source streaming remains
available throughout.


## Phase 4E observability and automatic provider fallback guard

Phase 4E adds process-local rollout evidence and a circuit breaker around the
signed Worker handoff. It does not replace the persisted Phase 4A master toggle
and does not write rollback state to PostgreSQL.

The guard is explicitly enabled with:

```env
VIDEO_CDN_DELIVERY_GUARD_ENABLED=true
VIDEO_CDN_DELIVERY_GUARD_FAILURE_THRESHOLD=3
VIDEO_CDN_DELIVERY_GUARD_PROBE_INTERVAL_SECONDS=30
VIDEO_CDN_DELIVERY_GUARD_COOLDOWN_SECONDS=120
VIDEO_CDN_DELIVERY_GUARD_TIMEOUT_SECONDS=2.0
```

Production canary preflight fails while the guard remains disabled.

For an eligible Public Review video, the API continues to authorize the exact
share/asset/source pair and create the short signed ticket. Runtime playback
decisions use the process-local last-known health sample and never wait for a
Worker health request. When a sample is due, the resolver schedules one
background HEAD against that exact signed Worker URL with redirects and
proxy-environment inheritance disabled. At most one probe is in flight per API
process. A healthy sample requires status 200, the durable Content-Length, a
video Content-Type and Accept-Ranges: bytes. No media body is downloaded.

An unverified, degraded or open guard falls back to the existing provider
stream immediately while an eligible due probe is scheduled in the background.
A previously healthy sample may continue to redirect the current request while
its background refresh is in flight. Failed refreshes affect subsequent
requests; after the configured consecutive-failure threshold the process-local
circuit opens and continues provider fallback until cooldown expires. The next
eligible request after cooldown schedules the recovery probe without blocking
that request. A successful probe closes the circuit.

The breaker is intentionally process-local. In a multi-process or multi-host
deployment each API process protects itself independently. This avoids an
automatic database mutation or distributed kill switch based on a transient
network observation. The persisted runtime toggle remains the operator-owned
global rollback control.

Platform administrators can inspect only aggregate safe data at:

```
GET /api/v1/admin/video-delivery/observability
```

The response contains process-local counters, a bounded 512-sample decision
latency summary, and circuit state/thresholds. It never contains tenant IDs,
asset/source IDs, R2 keys, media URLs, signed tickets, secrets or signing
material.

Counters include redirect, runtime/scope/cache/identity/guard/internal fallback,
probe success/failure and circuit-open events. Structured logs use the same
bounded metric names. PostgreSQL remains authoritative for durable cache state;
these process-local counters are rollout evidence only.

For canary rollout, inspect this endpoint per API process/instance where
possible. If the circuit opens or provider fallback rises unexpectedly, keep or
set the persisted runtime toggle OFF before investigating Worker/R2. Do not
increase thresholds to mask a failing canary.


## Phase 5A activation gate

Phase 4A–4E are code-complete. Phase 5A does not deploy or enable production;
it hardens the boundary immediately before operator activation.

The persisted master runtime toggle can now be enabled only when all server
prerequisites are true:

- R2 video cache enabled;
- signed delivery configured;
- canary/global rollout scope configured;
- Phase 4E delivery guard enabled.

If any prerequisite disappears while the persisted row is ON,
`effective_enabled` becomes false and Public Review falls back to the source
provider path. Disabling the persisted toggle remains allowed regardless of
prerequisite health.

Before production activation, use the single read-only gate:

```bash
cd apps/api
python -m app.operations.video_delivery_activation
```

The command requires production by default and performs the full static
preflight plus both signed HEAD checks:

1. current single Alembic head;
2. production environment;
3. R2 cache and signed delivery configuration;
4. approved private Worker/custom-domain origin;
5. explicit tenant canary scope by default;
6. persisted runtime row present and still OFF;
7. guard enabled;
8. cache quota below the soft limit and no active deletion;
9. at least one READY video;
10. authenticated missing-key Worker probe returning 404;
11. READY-object Worker probe returning matching HEAD metadata.

The command is read-only. It does not enable the runtime gate, update the
database, upload/delete/list R2 objects, deploy Cloudflare, change DNS, or print
signed URLs, credentials, tenant IDs, asset/source IDs or object keys.

A staging dry run must be explicit:

```bash
python -m app.operations.video_delivery_activation --allow-non-production
```

A future global rollout also requires an explicit
`--allow-global-rollout`; the default production path requires tenant canary
scope.

Only when the JSON result reports `"ready_to_enable": true` should a platform
administrator use the audited runtime endpoint/UI to enable delivery. This
command intentionally cannot perform that mutation.


## Phase 5B Platform Admin activation console

The Configuration tab now reflects the full Phase 4D/4E/5A activation state
instead of the old Phase 4A-only card. Platform administrators can inspect the
persisted runtime gate, effective delivery, rollout mode, prerequisite readiness,
process-local redirect/fallback/probe counters, decision p95 and circuit state.

The console intentionally does not expose canary tenant IDs, Worker origin,
signed capability URLs, R2 object keys or credentials.

Observability is advisory and process-local. Its request is independent from
the persisted runtime-status request so an observability outage cannot hide the
global Disable control. Operator rollback remains
`VIDEO_CDN_DELIVERY_ENABLED=false`.

Phase 5B is an operator UI only. It does not perform Worker deployment, DNS or
secret configuration, preflight probes, R2 mutation, or automatic production
activation.
