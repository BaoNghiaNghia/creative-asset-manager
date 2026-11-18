# Shared private R2 delivery Worker — video + Etsy Spy image derivatives

The **existing** `cam-r2-original-video` Worker and private
`creative-asset-video-cache` bucket are reused for Etsy Spy. **No second
bucket, Worker, public R2 URL or additional Cloudflare secret is needed.**
Video routes and ticket signing remain unchanged. The new route
`/etsy-images/v1/{tenant-hmac}/sp-{pack}/{slot}/{quality}/{sha}.{ext}`
accepts only PNG thumbnails and WebP 720px previews. Original design and
Listing PNGs cannot use this route.

CAM verifies principal, tenant and Picked membership before serving any
image or issuing a signed ticket. The image ticket lasts 45 seconds and uses
a distinct canonical HMAC message under the already configured
`R2_VIDEO_MEDIA_SIGNING_SECRET`:

```text
v1
etsy-image-read
/etsy-images/v1/{tenant-hmac}/sp-{pack}/{slot}/{quality}/{sha}.{ext}
{expiry}
```

The Worker is **read-only**, with no PUT/DELETE/list endpoints. CAM writes
derived images under `etsy-images/v1/` using the existing S3 credentials;
the video prefix `video-cache/` remains untouched. Signed image requests
are authorized before edge cache lookup, and outgoing responses use
`Cache-Control: private, no-store`.

**Two independent feature gates:** `CAM_ETSY_IMAGE_SHARED_R2_ENABLED=true`
with `CAM_ETSY_IMAGE_SHARED_R2_CANARY_TENANTS=boostgame` enables a private
R2 read-through cache in the authenticated CAM API. It works immediately
with the *current* video-only Worker. The separate
`CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED=true` gate enables browser redirects
**only after** the updated Worker returns 404 to a valid signed synthetic
image HEAD. The API re-checks this at runtime and falls back to private R2
if the old Worker still rejects image tickets. Update Nginx CSP `img-src`
to include the exact existing Worker origin before enabling redirects.
Never make the bucket public or turn on cache-everything for private API
paths.

To test the updated Worker locally: `npm ci && npm test && npm run typecheck`.
Release the updated Worker to **the same** Cloudflare service using its
existing R2 binding, only when the account has Worker-edit access and the
video rollback is available. Do not overwrite the Worker remotely with an
untested version. An unchanged Worker cannot serve images at the edge, but
the backend R2 cache continues to work.

### Existing Worker guarded rollout (Etsy image canary)

Run from the CAM repository root:

```sh
python3 -m deploy.tools.etsy_shared_worker_release check
# Only after Cloudflare authentication AND both signed synthetic routes pass:
python3 -m deploy.tools.etsy_shared_worker_release deploy
```

The release tool uses the existing Workers.dev origin, existing Worker name
`cam-r2-original-video`, private R2 binding, and the production video TTL.
It refuses missing/mismatched Cloudflare credentials, wrong Worker or bucket,
unsafe CSP, another tenant in the canary, or an already enabled browser edge
flag. It probes a nonexistent **video** object using the existing HMAC
contract before deploying and expects 404, not 403; a 403 might indicate a
secret mismatch, unsupported deployed Worker, or another denial. Diagnose that
first rather than replacing the live video Worker blindly.

After checking local tests, typecheck and dry-run, the deployment path first
verifies that the existing remote Worker can be listed. It uses Wrangler
`--strict --keep-vars` and does not create a second Worker/bucket.
After upload, the tool requires a signed missing-image HEAD 404 and a
continued signed missing-video HEAD 404. On failure, it leaves browser
redirects OFF and attempts Wrangler rollback. A passing deployment still
**does not set** `CAM_ETSY_IMAGE_SHARED_WORKER_ENABLED`; that production
gate can be switched on for `boostgame` only after the edge checks are
confirmed and the API fallback/health checks remain healthy.

Cloudflare login/Worker edit permission must be supplied via a secure
operator workflow (`wrangler login` or a protected API token). Do not
place secrets in repository files, logs, command-line arguments or chat.

This Worker accepts a short-lived read ticket and streams an authorized immutable original or derived playback object from a private R2 binding. It does not authorize CAM users or shares; Phase 4B
performs application authorization before issuing a ticket. Public Review uses
the Worker only when the persisted runtime delivery gate is effective.

## Ticket contract

Accepted keys are exactly `video-cache/{tenant_id}/{lowercase_sha256}/original` and `video-cache/{tenant_id}/{lowercase_sha256}/playback.mp4`; the pathname is exactly `/{key}`. The tenant ID matches `[A-Za-z0-9][A-Za-z0-9_-]{0,254}`; SHA-256 is 64 lowercase hex characters. Percent encoding, traversal, backslashes, controls, duplicate separators, foreign prefixes and every other suffix are rejected.

The exact UTF-8 HMAC-SHA256 message has **no trailing newline**. The signed pathname is the selected canonical object path, for example:

```text
v1
read
/video-cache/{tenant_id}/{lowercase_sha256}/original
{exp}
```

or the same message with `/playback.mp4` as the final path segment.

The URL is `{base}{pathname}?v=1&exp={unix_seconds}&sig={base64url_no_padding}`.
The logical `read` operation permits both GET and HEAD. Range, IP and
browser headers are not signed. Both languages use `test/vectors.json`,
containing only a fixed test secret.

The Worker requires one each of v/exp/sig, an unexpired canonical integer
expiry within 3600 seconds (or its smaller configured max), an exact path and
a valid HMAC before touching R2. All invalid tickets receive generic 403;
only a valid missing key gets 404.

## Local tests and development

```sh
npm ci
npm test
npm run typecheck
```

To run local Wrangler, copy `wrangler.toml.example` into ignored
`wrangler.toml`, use a **development** bucket name, place a test-only
signing secret in ignored `.dev.vars`, then run `npx wrangler dev`.
The default binding uses local R2 simulation. Do not set `remote = true`
or use `wrangler dev --remote` for this workflow. Mocked tests need no
Cloudflare account or credentials.

## Separately approved rollout (documentation only)

1. Keep the bucket private; do not enable a public URL or `r2.dev`.
2. Review the actual `VIDEO_CACHE_BUCKET` binding and selected Worker endpoint mode.
3. Set the same high-entropy, at-least-32-byte secret on backend and Worker
   through protected configuration. The Worker command is
   `wrangler secret put R2_VIDEO_MEDIA_SIGNING_SECRET`; do not commit it.
4. Set the backend media base URL to the approved HTTPS Worker origin and
   align its 1–3600-second TTL with the Worker maximum.
5. Review and test in staging; authorize any Worker deployment, DNS, secret
   or bucket-setting change separately.

### Zero-custom-domain workers.dev mode

Custom Domain remains the preferred production topology. An operator that
intentionally does not use a custom domain may instead render the Worker with
`workers_dev=true`:

```sh
python -m deploy.tools.r2_video_worker_rollout render \
  --worker-name cam-r2-original-video \
  --bucket-name creative-asset-video-cache \
  --workers-dev \
  --max-ttl-seconds 600 \
  --output infrastructure/cloudflare/r2-video-worker/wrangler.production.json
```

The backend must separately opt in with
`R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV=true` and set
`R2_VIDEO_MEDIA_BASE_URL` to the exact HTTPS Worker URL, for example
`https://cam-r2-original-video.<account-subdomain>.workers.dev`.
Without that flag, production preflight and runtime readiness remain fail-closed.

This exception does **not** make R2 public. The bucket remains private behind
the `VIDEO_CACHE_BUCKET` binding; `r2.dev` and raw
`r2.cloudflarestorage.com` delivery remain forbidden.

GET streams the R2 body directly; HEAD reads metadata only. Responses use
`private, no-store`, `nosniff`, and `no-referrer`. There are no
write/delete/list routes and no redirect to public R2 or S3 URLs.

Phase 3B now provides Range/206 and authenticated edge caching locally.
Phase 4A provides the persisted runtime CDN toggle, Phase 4B provides Public Review authorization plus provider fallback, and Phase 4C adds a fail-closed rollout preflight. None of those phases deploy this Worker automatically.

## Phase 3B: Range and authenticated edge caching

After a ticket is fully verified, a full GET may use caches.default under a
fixed trusted internal origin plus the exact immutable pathname. The signed
query never forms part of the cache key, and authentication is always performed
before a cache lookup. Only successful full GET representations are cached.

Supported Range forms are bytes=start-end, bytes=start-, and bytes=-suffix.
Valid ranges return 206; malformed, multi-range, and unsatisfiable requests
return 416. A cold Range makes R2 HEAD then native ranged GET and deliberately
does not populate Cache API. HEAD has no body and reports full metadata. The
bucket remains private; no r2.dev, Worker endpoint, DNS, secret, or deployment
was configured by this phase. A later approved rollout may use either a Custom
Domain or the explicit workers.dev opt-in mode, always with the private
VIDEO_CACHE_BUCKET binding and a Worker secret.


## Phase 4C production preflight contract

The repository CI runs `npm test` and `npm run typecheck` for this Worker on
every pull request and push to main.

For an approved production rollout, keep the application runtime delivery
toggle OFF while configuring the Worker. The API-side Phase 4C preflight can
optionally send one signed HEAD request for a random, absent preflight key. The
expected result is 404: this proves the route accepted the HMAC ticket and then
looked up the private R2 binding. The probe performs no PUT/DELETE/list and does
not use a user asset. A 403 or other response fails rollout readiness. For
temporary production troubleshooting, `R2_VIDEO_MEDIA_AUTH_DIAGNOSTICS=true`
enables Worker-side structured rejection reasons without logging the pathname,
ticket, signature, secret, or exception message; keep it false normally.


## Phase 4D production configuration and canary scope

Use `deploy.tools.r2_video_worker_rollout` from the repository root to render
`wrangler.production.json`. The generated file is ignored by Git and contains
no secret value. The default mode uses a Custom Domain with
`workers_dev=false`; the explicit `--workers-dev` mode instead emits
`workers_dev=true` with no custom-domain route. Both modes bind the private R2
bucket as `VIDEO_CACHE_BUCKET` and declare the signing secret as required.

The rollout helper's `plan` subcommand does not execute Wrangler. It labels
which suggested commands mutate Cloudflare and keeps the application runtime
gate OFF as a rollout invariant. A workers.dev plan additionally requires the
explicit `--allow-workers-dev` operator flag; this is separate from the
backend's `R2_VIDEO_MEDIA_ALLOW_WORKERS_DEV=true` runtime opt-in.

Application traffic is additionally scoped by
`VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS`. Empty canary scope with global rollout
false is deny-all. Promotion to all tenants requires the separate
`VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=true` setting and an empty canary
list.

For the production canary, use the application Phase 4D preflight with both
`--probe-worker` and `--probe-ready-object` before enabling the persisted
runtime gate.
