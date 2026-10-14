# RRUGC image loading and Cloudflare CDN rollout

## Deployed private preview acceleration

The RRUGC UI has several distinct image types:
- Stage 0 listing/Pinterest evidence is delivered from the marketplace's existing public image CDN. Only use validated official image hosts; never mirror arbitrary URLs on the backend.
- Stage 1 source-plan previews are managed Drive thumbnails at 128px in tables and 1024px in modal views. They already use a revisioned URL and an in-memory thumbnail cache.
- Stage 1 design outputs, Stage 2 colorways, Stage 3 review previews, Stage 4 generated results and output history use authenticated managed Drive thumbnail routes.
- Stage 5 saved Pinterest before images use the managed thumbnail path at 1024px for comparisons; original images remain accessible through the same route without the thumbnail query.

The server-side preview cache shares bounded singleflight memory (96 MiB). On
versioned output-thumbnail requests the API checks a tenant-scoped, size-specific
ETag **after tenant authorization**, before any Drive network I/O. It returns
304 on exact matches. Cache-Control is always **private** and at most one hour:
access must be revalidated after membership changes. No thumbnail may be
publicly cached by a general-purpose reverse proxy or generic CDN path. The
UI includes the output Drive revision in thumbnail URLs so retries or
regeneration do not reuse stale preview bytes.

## Cloudflare R2 edge image CDN (separate activation required)

Do **not** switch RRUGC private images to the existing public R2 video Worker
path without an independently reviewed image-cache budget and authorization
protocol. The video cache quota tracks video objects only; adding untracked
image keys to its 9 GB bucket risks overrun.

The image CDN rollout should use:
1. A separate private R2 image bucket with enforced byte quota, retention and
   oldest-first eviction; cap image representations to preview sizes.
2. Exact allowed key prefixes for source previews, saved input references,
   image job outputs and revisions, partitioned by tenant.
3. An authorized CAM API lookup that issues short-lived, HMAC-signed GET/HEAD
   tickets only for tenant-visible immutable image revisions.
4. A Cloudflare Worker which verifies the ticket **before** an edge-cache
   lookup; the canonical cache key excludes the signature but includes tenant,
   revision and rendition. Never use a public R2 bucket or raw object URL.
5. Bounded, idempotent background fill, fallback to authenticated Drive on
   cache miss or CDN outage, and counters for hit/miss, occupancy and latency.
6. Negative tests for unsigned/expired tickets, tenant spoofing, revoked
   access, arbitrary path injection, image replacement and full-R2 quota.

Provisioning a bucket, configuring Worker bindings/secrets and activating
Cloudflare traffic are separate infrastructure steps. This code release
improves browser/origin image caching; it **does not** imply the private R2
image CDN is active.
