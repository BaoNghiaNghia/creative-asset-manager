# R2 original-video cache — Phase 2 through Phase 4D operations

Status: Phases 1–4D are implemented in code. Public Review Phase 4B is authorization-preserving and falls back to the source provider. The persisted delivery gate remains OFF by default. No production R2/Worker rollout is implied by this document.

## Boundaries

Google Drive/OneDrive remain authoritative. The fill worker reads an exact tenant-qualified SourceAsset stream and never writes to a source provider, invokes FFmpeg, or transcodes. Only `video/*` with a valid SHA-256 identity and known positive size at most `R2_VIDEO_CACHE_MAX_OBJECT_BYTES` is eligible. The object key is generated server-side as `video-cache/{tenant_id}/{sha256}/original`. Public Review keeps its existing preview route. When the Phase 4A runtime gate is effective, an already-authorized exact video asset/source pair may receive a short Phase 4B redirect to private R2 delivery; every miss or safe delivery failure falls back to the existing provider stream. R2 remains private.

`R2_VIDEO_CACHE_ENABLED=false` disables enqueue, fill and periodic cleanup. Do not turn it on until migration 0083 is present, private bucket credentials are scoped, and the worker role includes `video_cache_fill`.

## Admission and quota

`ensure_video_cache_fill` creates a `video_cache_fill` processing job with only tenant, asset, source-asset and content-hash IDs. A tenant/hash row is unique. Existing READY or active PREPARING/RETRY work is reused. A FAILED fill needs an explicit `retry_failed=True` request. Jobs have five attempts and existing worker lease/backoff semantics.

A bucket-global PostgreSQL advisory transaction lock serializes every reservation and physical-delete accounting update. SQLite uses a process-local re-entrant lock only for deterministic local tests; multi-process SQLite is not a production quota authority.

Effective tracked bytes = READY `size_bytes` + DELETING `size_bytes` + PREPARING `reserved_bytes`. Admission never exceeds the 9,000,000,000-byte hard threshold. If a new reservation would cross it, READY objects are chosen by `last_accessed_at NULLS FIRST, cached_at, id` and physically deleted until projected usage including the incoming reservation is at most 8,000,000,000 bytes. If enough READY bytes cannot be reclaimed, the fill is bypassed. DELETING and PREPARING are never ordinary LRU candidates. A failed or uncertain remote delete remains counted.

Access-touch is a tenant-qualified repository primitive with a default 300-second debounce. Phase 4B touches LRU only after a signed delivery ticket is successfully minted.

## Fill and recovery

The worker reloads the tenant, canonical asset, exact source link, current video MIME, hash and size before opening the provider stream and again before marking READY. The Phase 1 uploader uses 16 MiB multipart parts, SHA-256 and byte-count checks, and HEAD validation. A multipart upload ID is tracked in the cache row. Retryable source/R2 failures use the existing durable job retry; permanent identity/integrity failures become terminal. If abort/delete cannot be confirmed, the row remains in DELETING with conservative physical-byte accounting. Source bytes are never deleted by cache cleanup.

The operational worker runs bounded DB-driven cleanup at `R2_VIDEO_CACHE_CLEANUP_INTERVAL_SECONDS`. A PREPARING row older than `R2_VIDEO_CACHE_PREPARING_STALE_SECONDS` is only reclaimed when its fill job is absent or terminal, or when an exhausted processing attempt has an expired lease past the stale cutoff; pending and retrying jobs are not reclaimed merely for age. Known multipart IDs are aborted before remote deletion. Stuck DELETING rows are retried after their lease/retry time. No full bucket listing is performed. Explicit `VideoCacheCleanup.reconcile_ready()` HEAD-checks a bounded set of known READY keys, removes missing known objects, and cleans up size mismatches; unknown/out-of-prefix keys are never touched. Full-bucket orphan discovery is not implemented.

## Inspection, smoke and metrics

From `apps/api`, run `python -m app.operations.video_cache_cli` for read-only global counts, READY/reserved/DELETING bytes, soft/hard limits and pressure. It prints no object keys, tenant IDs, signed URLs or credentials.

The optional real connectivity smoke requires both `R2_VIDEO_CACHE_ENABLED=true` and `R2_VIDEO_CACHE_REAL_SMOKE=true` and runs `python -m app.operations.video_cache_smoke`. It PUTs a tiny generated object only under `video-cache-smoke-test/`, HEADs, GETs/verifies, DELETEs, and checks missing. Never use a production asset. Standard tests use mocked R2; the smoke is not run without explicit opt-in.

Fill started/completed/failed, eviction and bypass counters are emitted as safe structured log events and held process-locally. Process restarts reset those counters; the database-backed CLI is authoritative for current-byte gauges. No asset ID or hash is used as a metric label.

## Migration and rollback

Migration `0083_r2_video_cache_fill` adds fill ownership, multipart and cleanup lease fields/indexes and removes asset/source cascade FKs so the physical-byte ledger survives source deletion. The tenant FK remains. No R2 network activity occurs in Alembic. Downgrade to 0082 requires a preflight for any cache rows whose asset/source has been deleted, because 0082 restores the cascade FKs. Do not downgrade while fills or cleanup are running; first disable the feature, drain workers, and retain/export the ledger for any remaining R2 objects. A DB downgrade never deletes R2 bytes. A safe rollback keeps the feature disabled and retains 0083 metadata until all known remote objects are confirmed deleted, then downgrades. Production migration and deployment need separate authorization.

Known limits: cleanup is DB-driven and cannot discover remote objects whose ledger row was lost before 0083; tenant deletion can still cascade ledger rows and needs a separate tenant-offboarding policy. READY HEAD reconciliation is bounded/manual rather than a full periodic bucket scan. The optional smoke uses a dedicated prefix, not user media. Public Review CDN delivery remains disabled until the persisted runtime gate is explicitly enabled after rollout preflight.

## Phase 3A delivery foundation — not deployed

Backend cache fill does not require delivery configuration. Optional settings are
`R2_VIDEO_MEDIA_BASE_URL` (HTTPS origin in production), the high-entropy
`R2_VIDEO_MEDIA_SIGNING_SECRET` (at least 32 UTF-8 bytes, protected), and
`R2_VIDEO_MEDIA_TICKET_TTL_SECONDS` (1–3600, default 600). The Worker uses
the same secret and a private `VIDEO_CACHE_BUCKET` binding. Its optional
`R2_VIDEO_MEDIA_MAX_TTL_SECONDS` cannot exceed 3600. Do not place secrets in
TOML, logs, browser bundles or the repository.

Only a READY video row with the exact server-generated key can be signed.
Phase 4B authorizes the public session/share and exact tenant/asset/source
pair before signing. The signer itself still grants no application authority. The Worker validates
method, exact path, version, expiry and HMAC before R2 access. GET streams
the complete original; HEAD returns metadata only. Invalid tickets return

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
reads or writes the secret value.

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
identity.

Only after those checks are green should the platform-admin runtime toggle be
enabled. Rollback order is: disable the application runtime toggle first, then
roll back the Worker version if needed. Provider source streaming remains
available throughout.
